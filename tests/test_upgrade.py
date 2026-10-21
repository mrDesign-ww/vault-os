"""Clean install and full upgrade (installer/vault-os.py): parity 0, the personal boundary, rollback, recovery.

Every test runs on a temporary HOME. The legacy tests install the previous public release (28777ca) by its own
procedure, add a person's own system items and personal files, and give the vault a foreign folder structure.
"""
import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upgrade_fixtures as F  # noqa: E402
from vaultos import boundary, engine, fsx, journal, manifest, migrate  # noqa: E402

FRESH = ["--owner", "Test Owner", "--title", "Test Vault", "--language", "English", "--client-label", "Client",
         "--codex", "--no-upstream-skills"]
POSIX = not sys.platform.startswith("win")


def changed(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


class FreshInstallTest(unittest.TestCase):
    def setUp(self):
        self.m = F.Machine()
        self.args = FRESH + ["--vault", str(self.m.vault)]

    def tearDown(self):
        self.m.cleanup()

    def test_fresh_install_is_exact_and_idempotent(self):
        m = self.m
        self.assertEqual(json.loads(m.engine("detect", "--json").stdout)["result"], "fresh")
        empty = m.snapshot()
        plan, rc = m.plan(*self.args)
        self.assertEqual(rc, 0)
        self.assertTrue(plan["approval_needed"])
        self.assertTrue(plan["actions"])
        self.assertEqual(m.snapshot(), empty, "the dry run must not write")
        refused = m.engine("upgrade", *self.args, "--confirm", plan["id"])
        self.assertEqual(refused.returncode, 3, refused.stderr)
        self.assertIn("--approve-memory-policy", refused.stderr)
        wrong = m.engine("upgrade", *self.args, "--confirm", "0" * 16, "--approve-memory-policy")
        self.assertEqual(wrong.returncode, 3)
        self.assertEqual(changed(empty, m.snapshot()), [])
        _, out = m.upgrade(*self.args)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("parity 0", out.stdout)
        parity = m.engine("parity", "--json")
        self.assertEqual(parity.returncode, 0, parity.stdout + parity.stderr)
        self.assertEqual(json.loads(parity.stdout)["differences"], 0)
        state = json.loads(m.engine("detect", "--json").stdout)
        self.assertEqual(state["result"], "installed")
        self.assertFalse(state["outdated"])
        stamp = json.loads((m.claude / "vault-os" / "installed.json").read_text(encoding="utf-8"))
        self.assertEqual(stamp["state"], "installed")
        self.assertEqual(stamp["version"], (F.ROOT / "VERSION").read_text().strip())
        retrieval = subprocess.run([sys.executable, "-B", "scripts/memory-model.py", "retrieve", "memory source of truth",
                                    "--zone", "work", "--loadout", "general"], cwd=str(m.vault), capture_output=True,
                                   text=True, env=m.env())
        self.assertEqual(retrieval.returncode, 0, retrieval.stderr)
        self.assertGreaterEqual(retrieval.stdout.count('"memory_trust": "approved"'), 2)
        # A second run plans nothing and changes nothing: no new archive either.
        installed = m.snapshot()
        archives = F.archives(m)
        again, rc = m.plan()
        self.assertEqual(rc, 0)
        self.assertEqual((again["actions"], again["migration"]["moves"], again["stamp"]), ([], [], False))
        out = m.engine("upgrade", "--confirm", again["id"])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("nothing to change", out.stdout)
        self.assertEqual(changed(installed, m.snapshot()), [])
        self.assertEqual(F.archives(m), archives)
        # Rolling the install back leaves the empty home.
        out = m.rollback(archives[-1])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(changed(empty, m.snapshot()), [])
        self.assertEqual(json.loads(m.engine("detect", "--json").stdout)["result"], "fresh")

    def test_windows_refuses_every_write_and_leaves_nothing(self):
        m = self.m
        win = ["--platform", "win32"]
        empty = m.snapshot()
        plan, rc = m.plan(*self.args, *win)
        self.assertIn(rc, (0, 3))
        for args in (["upgrade", *self.args, *win, "--confirm", plan["id"], "--approve-memory-policy"],
                     ["rollback", str(m.backups / "x"), *win], ["recover", *win]):
            out = m.engine(*args)
            self.assertEqual(out.returncode, 5, args)
            self.assertIn("not yet on Windows", out.stderr)
        self.assertEqual(m.engine("detect", *win).returncode, 0)
        self.assertEqual(changed(empty, m.snapshot()), [])
        self.assertFalse(m.backups.exists())

    def test_installer_flags_delegate_to_the_engine(self):
        m = self.m
        script = F.ROOT / "installer" / "install-claude-layer.py"
        out = subprocess.run([sys.executable, str(script), "--upgrade", *self.args, "--home", str(m.home), "--json"],
                             capture_output=True, text=True, env=m.env(), timeout=900)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("plan", json.loads(out.stdout))
        self.assertFalse(m.claude.exists())
        bad = subprocess.run([sys.executable, str(script), "--upgrade", "--mcp", "default", "--home", str(m.home)],
                             capture_output=True, text=True, env=m.env())
        self.assertEqual(bad.returncode, 2)


class AddOnlyInstallTest(unittest.TestCase):
    """The step-by-step installers (INSTALL.md) give the same machine: parity 0, and the upgrade only stamps it."""

    def setUp(self):
        self.m = F.Machine()

    def tearDown(self):
        self.m.cleanup()

    def test_step_installers_match_the_engine(self):
        m = self.m
        env = m.env()
        inst = F.ROOT / "installer"
        subprocess.run([sys.executable, str(inst / "render-vault.py"), "--target", str(m.vault), "--owner",
                        "Test Owner", "--title", "Test Vault", "--language", "English", "--client-label", "Client",
                        "--approve-memory-policy"], check=True, capture_output=True, env=env)
        build_baseline(m)
        subprocess.run([sys.executable, str(inst / "install-claude-layer.py"), "--owner", "Test Owner", "--language",
                        "English", "--claude-home", str(m.claude), "--python", sys.executable,
                        "--extra-skills-dir", str(m.codex / "skills"), "--extra-skills-dir", str(m.agents / "skills")],
                       check=True, capture_output=True, env=env)
        subprocess.run([sys.executable, str(inst / "install-codex-layer.py"), "--vault", str(m.vault),
                        "--codex-home", str(m.codex)], check=True, capture_output=True, env=env)
        self.assertEqual(json.loads(m.engine("detect", "--json").stdout)["result"], "pre-stamp")
        args = ["--vault", str(m.vault), "--codex", "--no-upstream-skills"]
        parity = m.engine("parity", *args)
        self.assertEqual(parity.returncode, 0, parity.stdout + parity.stderr)
        before = m.snapshot()
        plan, rc = m.plan(*args)
        self.assertEqual((rc, plan["actions"], plan["migration"]["moves"], plan["stamp"]), (0, [], [], True))
        self.assertFalse(plan["approval_needed"])
        out = m.engine("upgrade", *args, "--confirm", plan["id"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(changed(before, m.snapshot()), [".claude/vault-os/", ".claude/vault-os/installed.json"])


def build_baseline(m):
    """INSTALL.md step 4: the first commit and the three zone indexes."""
    env = m.env()
    F.git("init", "-q", cwd=m.vault)
    F.git("add", ".", cwd=m.vault)
    F.git("commit", "-q", "-m", "Initialize Vault OS", cwd=m.vault)
    for zone in ("work", "personal", "client"):
        token = subprocess.run(["bash", "scripts/wiki-lock.sh", "acquire", ".vault-meta/write/" + zone, "--ttl", "3600"],
                               cwd=str(m.vault), capture_output=True, text=True, env=env, check=True).stdout.strip()
        for step in ("build", "validate"):
            subprocess.run([sys.executable, "-B", "scripts/memory-model.py", step, "--zone", zone, "--lock-token",
                            token], cwd=str(m.vault), capture_output=True, env=env, check=True)
        subprocess.run(["bash", "scripts/wiki-lock.sh", "release", ".vault-meta/write/" + zone, token],
                       cwd=str(m.vault), capture_output=True, env=env, check=True)
    receipts = sorted(str(p.relative_to(m.vault)) for p in (m.vault / ".vault-meta" / "memory-index").glob(
        "*.authority.json"))
    F.git("add", "-f", *receipts, cwd=m.vault)
    F.git("commit", "-q", "-m", "Bind memory index authority", cwd=m.vault)


def legacy_machine():
    m = F.Machine()
    F.legacy_install(m)
    F.add_personal_extras(m)
    F.add_foreign_vault(m.vault)
    return m


@unittest.skipUnless(POSIX and F.legacy_available(), "needs the previous release in the Git history and a POSIX shell")
class LegacyUpgradeTest(unittest.TestCase):
    """Upgrade from the previous public release with a person's own items and a foreign vault."""

    def setUp(self):
        self.m = legacy_machine()
        self.args = ["--vault", str(self.m.vault), "--no-upstream-skills"]
        self.mapped = self.args + ["--map", "Secret=wiki/workspace/projects/Secret"]

    def tearDown(self):
        self.m.cleanup()

    def test_upgrade_replaces_the_system_and_keeps_every_personal_file(self):
        m = self.m
        self.assertEqual(json.loads(m.engine("detect", "--json").stdout)["result"], "pre-stamp")
        before = m.snapshot()
        settings_before = (m.claude / "settings.json").read_bytes()
        system = set()
        old_files = migrate.scan(str(m.vault), system)
        old_links = migrate.link_targets(str(m.vault), [f for f in old_files if f in F.FOREIGN_NOTES])
        # A folder that looks like another zone of the old system is a question, not a guess.
        blocked = m.engine("upgrade", *self.args)
        self.assertEqual(blocked.returncode, 3, blocked.stderr)
        self.assertIn('--map "Secret=', blocked.stdout)
        self.assertNotIn("--confirm", blocked.stdout)
        plan, rc = m.plan(*self.mapped)
        self.assertEqual(rc, 0)
        self.assertFalse(plan["approval_needed"], "an unchanged doctrine keeps its approval")
        removes = {a["path"] for a in plan["actions"] if a["op"] == "remove"}
        for rel in ("skills/my-own-skill", "hooks/my-hook.py", "agents/my-agent.md", "commands/my-command.md",
                    "output-styles/terse.md", "AGENTS.md", "prompts/p.md"):
            self.assertTrue(any(r == rel or rel.startswith(r + "/") for r in removes), rel)
        self.assertEqual(changed(before, m.snapshot()), [])
        _, out = m.upgrade(*self.mapped)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        parity = m.engine("parity")
        self.assertEqual(parity.returncode, 0, parity.stdout + parity.stderr)
        state = json.loads(m.engine("detect", "--json").stdout)
        self.assertEqual((state["result"], state["outdated"]), ("installed", False))
        archive = F.archives(m)[-1]

        # Personal files of the homes are untouched, byte for byte.
        for rel, text in F.PERSONAL_FILES.items():
            self.assertEqual((m.home / rel).read_text(encoding="utf-8"), text, rel)
        # The person's own system items are gone from the live system and kept in the archive.
        for rel, text in F.SYSTEM_EXTRAS.items():
            self.assertFalse((m.home / rel).exists(), rel)
            layer, inner = rel.split("/", 1)
            copy = archive / "files" / layer.lstrip(".") / inner
            self.assertEqual(copy.read_text(encoding="utf-8"), text, rel)
        self.assertEqual((archive / "files" / "claude" / "settings.json").read_bytes(), settings_before)
        # Settings: system keys replaced, personal keys kept.
        s = json.loads((m.claude / "settings.json").read_text(encoding="utf-8"))
        template = json.loads((F.ROOT / "shell" / "claude" / "settings.template.json").read_text(encoding="utf-8"))
        self.assertEqual((s["theme"], s["apiKeyHelper"], s["env"]["MY_PRIVATE_DIR"]),
                         ("dark", "/opt/me/key-helper.sh", "/opt/me"))
        self.assertIn("Bash(ls:*)", s["permissions"]["allow"])
        self.assertNotIn("myOwnSystemThing", s)
        self.assertNotIn("my-plugin@my-market", s["enabledPlugins"])
        self.assertNotIn("my-hook.py", json.dumps(s["hooks"]))
        self.assertEqual(s["skillOverrides"], template["skillOverrides"])
        self.assertEqual(s["permissions"]["defaultMode"], "default")
        # Codex: personal keys and tables byte for byte, system keys and tables from the template.
        config = (m.codex / "config.toml").read_text(encoding="utf-8")
        for line in ('approval_policy = "never"', "[profiles.fast]", 'model = "my-fast-model"',
                     "[mcp_servers.private]", "[hooks.state]", '"hook-a" = "0123abcd"'):
            self.assertIn(line, config)
        self.assertIn('model = "gpt-6.1-sol"', config)
        tpl = manifest.load_installers(str(F.ROOT))[1].config_template()  # with the plugins shipped disabled
        self.assertEqual(boundary.codex_system_view(config, tpl), boundary.codex_system_view(tpl, tpl))
        self.assertIn('[plugins."i-have-adhd@i-have-adhd"]\nenabled = false\n', config)

        # The vault: every personal file kept, moved by the plan, content equal up to the logged link rewrites.
        mapping = json.loads((archive / "migration" / "mapping.json").read_text(encoding="utf-8"))
        final = {src: dst for src, dst, _ in mapping["moves"]}
        for rel, content in F.FOREIGN_NOTES.items():
            dest = m.vault / final.get(rel, rel)
            self.assertTrue(dest.is_file(), rel)
            original = content if isinstance(content, bytes) else content.encode("utf-8")
            if rel in mapping["rewrites"]:
                restored = migrate.revert_edits(migrate.read_text(str(dest)), mapping["rewrites"][rel])
                self.assertEqual(migrate.encode(restored), original, rel)
            else:
                self.assertEqual(dest.read_bytes(), original, rel)
        self.assertEqual(final.get("wiki/workspace/projects/Kept.md"), None, "wiki pages in place stay")
        self.assertEqual(final["Secret/project/Deal.md"], "wiki/workspace/projects/Secret/project/Deal.md")
        self.assertEqual(final["Journal/2026-10-01.md"], "wiki/personal/inbox/Journal/2026-10-01.md")
        self.assertEqual(final["Journal/mood.png"], "wiki/personal/inbox/Journal/mood.png")
        self.assertEqual(final["Archive/Old.md"], "wiki/archives/Old.md")
        self.assertEqual(final["people/Bob.md"], "wiki/resources/people/Bob.md")
        self.assertIn(["Projects/Alpha/Plan.md", "wiki/workspace/projects/Alpha/Plan (2).md"], mapping["renamed"])
        self.assertFalse((m.vault / "Projects").exists(), "emptied old folders are removed")
        # Links: every link reaches the same note as before; code is never rewritten.
        after_files = [final.get(rel, rel) for rel in old_files if rel in F.FOREIGN_NOTES]
        layout = migrate.Layout(after_files)
        for rel, targets in old_links.items():
            dest = final.get(rel, rel)
            now = {}
            for _, _, kind, parts in migrate.links_of(dest, migrate.read_text(str(m.vault / dest))):
                target = migrate.resolve(kind, parts, dest, layout)
                now[target] = now.get(target, 0) + 1
            for target, count in targets.items():
                self.assertGreaterEqual(now.get(final.get(target, target), 0), count, (rel, target))
        spec = (m.vault / final["Projects/Alpha/Spec.md"]).read_text(encoding="utf-8")
        self.assertIn("```\n[[Plan]] stays as code\n```", spec)
        health = (m.vault / final["1. Areas/Health.md"]).read_text(encoding="utf-8")
        self.assertIn("`[[Spec]]`", health)
        canvas = json.loads((m.vault / final["Notes/board.canvas"]).read_text(encoding="utf-8"))
        self.assertEqual(canvas["nodes"][0]["file"], final["Notes/Plan.md"])
        # The archive is complete.
        for name in ("journal.jsonl", "plan.json", "stamp-before.json", "README.txt", "migration/undo.py"):
            self.assertTrue((archive / name).is_file(), name)
        ops = [json.loads(line)["op"] for line in (archive / "journal.jsonl").read_text().splitlines()]
        self.assertEqual((ops[0], ops[-1]), ("begin", "commit"))

        # Rollback restores the exact previous tree.
        out = m.rollback(archive)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(changed(before, m.snapshot()), [])
        self.assertEqual(json.loads(m.engine("detect", "--json").stdout)["result"], "pre-stamp")
        again = m.engine("rollback", str(archive))
        self.assertEqual(again.returncode, 2, "a second rollback of the same archive is refused")

        # Upgrade again, then a second upgrade is a no-op.
        _, out = m.upgrade(*self.mapped)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        installed = m.snapshot()
        count = len(F.archives(m))
        plan, rc = m.plan()
        self.assertEqual((rc, plan["actions"], plan["migration"]["moves"], plan["stamp"]), (0, [], [], False))
        out = m.engine("upgrade", "--confirm", plan["id"])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(changed(installed, m.snapshot()), [])
        self.assertEqual(len(F.archives(m)), count)


def live_ticks(archive):
    """The journal records that count as live changes, in order (what the fault hook counts)."""
    out = []
    for line in (archive / "journal.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["op"] in ("remove", "mkdir", "rmdir", "move") or (r["op"] == "write" and r["written"]["type"] != "external"):
            out.append(r)
    return out


@unittest.skipUnless(POSIX and F.legacy_available(), "needs the previous release in the Git history and a POSIX shell")
class InterruptedUpgradeTest(unittest.TestCase):
    """A killed upgrade is detected, refused until recovered, and recover restores the exact previous tree."""

    def setUp(self):
        self.m = legacy_machine()
        self.args = ["--vault", str(self.m.vault), "--no-upstream-skills", "--map",
                     "Secret=wiki/workspace/projects/Secret"]

    def tearDown(self):
        self.m.cleanup()

    def test_kill_at_every_phase_then_recover(self):
        m = self.m
        before = m.snapshot()
        plan, out = m.upgrade(*self.args)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        ticks = live_ticks(F.archives(m)[-1])
        self.assertEqual(m.rollback(F.archives(m)[-1]).returncode, 0)
        self.assertEqual(changed(before, m.snapshot()), [])
        layers = [r["layer"] for r in ticks]
        moves = [i for i, r in enumerate(ticks) if r["op"] == "move"]
        points = sorted({1, 2, min(40, len(ticks)), layers.index("codex") + 1, layers.index("vault") + 1,
                         moves[0] + 1, moves[-1] + 1, len(ticks)})
        landed = set()
        for n in points:
            with self.subTest(kill_after=n):
                _, out = m.upgrade(*self.args, env=m.env(VAULT_OS_FAULT_AFTER=str(n)))
                self.assertEqual(out.returncode, -9, out.stdout + out.stderr)
                killed = F.archives(m)[-1]
                done = live_ticks(killed)
                self.assertEqual(len(done), n)
                landed.add(done[-1]["layer"])
                self.assertEqual(json.loads(m.engine("detect", "--json").stdout)["result"], "interrupted")
                self.assertEqual(m.engine("upgrade", *self.args).returncode, 4)
                self.assertEqual(m.engine("rollback", str(killed)).returncode, 4)
                out = m.engine("recover")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertEqual(changed(before, m.snapshot()), [])
                self.assertEqual(json.loads(m.engine("detect", "--json").stdout)["result"], "pre-stamp")
        self.assertTrue({"claude", "codex", "vault"} <= landed, landed)
        _, out = m.upgrade(*self.args)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(m.engine("parity").returncode, 0)


SETUP_SCRIPT = r'''#!/bin/sh
set -e
echo "$HOME" > installed-for
ln -s "$PWD/alpha" "$HOME/.claude/skills/suite-alpha"
"$VAULT_OS_TEST_PYTHON" - <<'PY'
import json, os
path = os.path.join(os.environ["HOME"], ".claude", "settings.json")
with open(path) as f:
    settings = json.load(f)
hook = {"type": "command", "command": "sh " + os.path.join(os.environ["HOME"], ".claude/skills/suite/bin/hook.sh")}
settings.setdefault("hooks", {}).setdefault("SessionStart", []).append({"hooks": [hook]})
with open(path, "w") as f:
    json.dump(settings, f, indent=2)
PY
'''


@unittest.skipUnless(POSIX, "the setup fixture is a shell script")
class UpstreamUpgradeTest(unittest.TestCase):
    """Pinned upstream skills: installed by their setup, adopted on the next run, reinstalled at a new pin."""

    def setUp(self):
        self.m = F.Machine()
        self.src = self.m.tmp / "src"
        folder = self.src / "folder"
        (folder / "skills" / "demo").mkdir(parents=True)
        (folder / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        suite = self.src / "suite"
        (suite / "alpha").mkdir(parents=True)
        (suite / "alpha" / "SKILL.md").write_text("---\nname: suite-alpha\n---\n", encoding="utf-8")
        (suite / "setup").write_text(SETUP_SCRIPT, encoding="utf-8")
        (suite / "setup").chmod(0o755)
        self.commits = {}
        for repo in (folder, suite):
            F.git("init", "-q", cwd=repo)
            F.git("add", "-A", cwd=repo)
            F.git("commit", "-q", "-m", "one", cwd=repo)
            self.commits[repo.name] = F.git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        self.manifest = self.m.tmp / "upstream.json"
        self.write_manifest()
        self.args = FRESH[:-1] + ["--upstream-skills", "--vault", str(self.m.vault), "--upstream-manifest",
                                  str(self.manifest)]
        self.env = self.m.env(VAULT_OS_TEST_PYTHON=sys.executable)

    def write_manifest(self):
        self.manifest.write_text(json.dumps({"skills": [
            {"name": "demo", "kind": "folder", "repo": str(self.src / "folder"), "commit": self.commits["folder"],
             "path": "skills/demo", "requires": ["git"], "platforms": ["darwin", "linux", "win32"]},
            {"name": "suite", "kind": "setup", "repo": str(self.src / "suite"), "commit": self.commits["suite"],
             "requires": ["git"], "platforms": ["darwin", "linux"], "setup": ["./setup"]},
        ]}), encoding="utf-8")

    def tearDown(self):
        self.m.cleanup()

    def engine_plan(self):
        out = self.m.engine("upgrade", *self.args, "--json", env=self.env)
        self.assertIn(out.returncode, (0, 3), out.stderr)
        return json.loads(out.stdout)["plan"]

    def apply(self, plan):
        out = self.m.engine("upgrade", *self.args, "--confirm", plan["id"], "--approve-memory-policy", env=self.env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_install_adopt_rollback_and_new_pin(self):
        m = self.m
        empty = m.snapshot()
        plan = self.engine_plan()
        self.assertEqual(sorted(a["path"] for a in plan["actions"] if a["op"] == "upstream"),
                         ["skills/demo", "skills/suite"])
        self.apply(plan)
        skills = m.claude / "skills"
        self.assertTrue(os.path.islink(str(skills / "suite-alpha")))
        self.assertEqual((skills / "suite" / "installed-for").read_text().strip(), str(m.home))
        self.assertIn("/skills/suite/bin/hook.sh", (m.claude / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(m.engine("parity", "--upstream-manifest", str(self.manifest), env=self.env).returncode, 0)
        # The next run adopts both and changes nothing.
        installed = m.snapshot()
        plan = self.engine_plan()
        self.assertEqual((plan["actions"], plan["stamp"]), ([], False))
        self.apply(plan)
        self.assertEqual(changed(installed, m.snapshot()), [])
        # Rollback removes the upstream installs, their links and their hooks.
        out = m.rollback(F.archives(m)[-1], env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(changed(empty, m.snapshot()), [])
        # A new pin of the setup kind is a reinstall: old children and hooks go, the new setup adds them again.
        self.apply(self.engine_plan())
        suite = self.src / "suite"
        (suite / "NEW").write_text("two\n", encoding="utf-8")
        F.git("add", "-A", cwd=suite)
        F.git("commit", "-q", "-m", "two", cwd=suite)
        self.commits["suite"] = F.git("rev-parse", "HEAD", cwd=suite).stdout.strip()
        self.write_manifest()
        plan = self.engine_plan()
        ups = [a for a in plan["actions"] if a["op"] == "upstream"]
        self.assertEqual([a["path"] for a in ups], ["skills/suite"])
        self.assertIn("installed at", ups[0]["reason"])
        self.assertIn("skills/suite-alpha", {a["path"] for a in plan["actions"] if a["op"] == "remove"})
        self.apply(plan)
        self.assertTrue((skills / "suite" / "NEW").is_file())
        settings = (m.claude / "settings.json").read_text(encoding="utf-8")
        self.assertEqual(settings.count("/skills/suite/bin/hook.sh"), 1)
        self.assertEqual(m.engine("parity", "--upstream-manifest", str(self.manifest), env=self.env).returncode, 0)


class BoundaryTest(unittest.TestCase):
    """The split rules are idempotent: applying them to their own output changes nothing."""

    def test_claude_settings(self):
        template = json.loads((F.ROOT / "shell" / "claude" / "settings.template.json").read_text(encoding="utf-8"))
        template.pop("_comment", None)
        current = {"theme": "dark", "apiKeyHelper": "/x", "myThing": 1, "env": {"MINE": "1", "OLD_SYS": "2"},
                   "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Read(.env)"], "defaultMode": "bypassPermissions"},
                   "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}
        once, notes = boundary.desired_settings(current, template, ["OLD_SYS"])
        self.assertEqual((once["theme"], once["apiKeyHelper"], once["env"]["MINE"]), ("dark", "/x", "1"))
        self.assertNotIn("OLD_SYS", once["env"])
        self.assertNotIn("myThing", once)
        self.assertEqual(once["permissions"]["deny"], ["Read(.env)"])
        self.assertIn("Bash(ls:*)", once["permissions"]["allow"])
        self.assertEqual(once["permissions"].get("defaultMode"), template["permissions"].get("defaultMode"))
        self.assertEqual(once.get("hooks"), template.get("hooks"))
        self.assertIn(("remove", "myThing"), notes)
        twice, notes = boundary.desired_settings(once, template, ["OLD_SYS"])
        self.assertEqual((twice, notes), (once, []))

    def test_codex_config(self):
        template = (F.ROOT / "shell" / "codex" / "config.template.toml").read_text(encoding="utf-8")
        current = F.PERSONAL_CODEX + '\n[marketplaces.someone-else]\nsource = "x"\n\n[features]\nmine = true\n'
        once = boundary.desired_codex_config(current, template)
        self.assertEqual(boundary.desired_codex_config(once, template), once)
        self.assertEqual(boundary.codex_system_view(once, template), boundary.codex_system_view(template, template))
        for line in ('approval_policy = "never"', "[profiles.fast]", "[mcp_servers.private]", "[hooks.state]"):
            self.assertIn(line, once)
        self.assertNotIn("someone-else", once)
        self.assertNotIn("mine = true", once)


# ── fix round 1 after the final audit: the vault side (FINAL-AUDIT B1, M1-M4, O1, O2, MINOR 3, 4) ────────────

def write_tree(root, files):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def engine_opts(m, **extra):
    opts = {"owner": "Test Owner", "title": "Test Vault", "language": "English", "client_label": "Client",
            "vault": str(m.vault), "codex": True, "upstream_skills": False, "claude_home": str(m.claude),
            "codex_home": str(m.codex), "agents_home": str(m.agents), "backup_root": str(m.backups), "maps": []}
    opts.update(extra)
    return opts


def survived(vault, plan, originals):
    """The originals that are not live byte for byte (or equal up to the logged link rewrites) after a plan ran."""
    moves = {src: dst for src, dst, _ in plan["migration"]["moves"]}
    rewrites = plan["migration"]["rewrites"]
    lost = []
    for rel, data in originals.items():
        path = vault / moves.get(rel, rel)
        if not path.is_file() or path.is_symlink():
            lost.append(rel)
            continue
        now = path.read_bytes()
        if rel in rewrites:
            now = migrate.encode(migrate.revert_edits(now.decode("utf-8", "surrogateescape"), rewrites[rel]) or "")
        if now != data:
            lost.append(rel)
    return lost


def live_layout(vault):
    paths = []
    for current, dirs, files in os.walk(str(vault)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel = os.path.relpath(current, str(vault)).replace(os.sep, "/")
        paths += [(n if rel == "." else rel + "/" + n) for n in files if not n.startswith(".")]
    return migrate.Layout(paths)


def link_targets_of(vault, rel):
    text = (vault / rel).read_text(encoding="utf-8")
    layout = live_layout(vault)
    return [migrate.resolve(kind, parts, rel, layout) for _, _, kind, parts in migrate.links_of(rel, text)]


class VaultRulesTest(unittest.TestCase):
    """Unit rules of the vault boundary: path classes, shipped versions, merges, origin, plan text, Git hygiene."""

    def setUp(self):
        self.m = F.Machine()

    def tearDown(self):
        self.m.cleanup()

    def test_every_vault_system_path_of_a_real_plan_has_exactly_one_class(self):
        m = self.m
        m.vault.mkdir()
        write_tree(m.vault, {"Reference/_index.md": "# mine\n", "Notes/a.md": "# a\n"})
        opts = engine_opts(m)
        homes = engine.homes_from(opts)
        answers, _, questions = engine.resolve_answers(F.ROOT, opts, homes, None)
        self.assertEqual(questions, [])
        plan, ctx = engine.build(F.ROOT, answers, homes, None, opts)
        try:
            layer = ctx.layers["vault"]
            paths = set(layer.entries) | set(layer.splits) | set(layer.owned_dirs) | set(layer.absent) | \
                set(layer.seeds) | {a["path"] for a in plan["actions"] if a["layer"] == "vault"}
            classes = {rel: manifest.vault_path_class(rel) for rel in paths}  # raises unless exactly one class
            self.assertGreater(len(paths), 20)
            expect = {".gitignore": "mixed", ".obsidian/core-plugins.json": "mixed",
                      ".obsidian/community-plugins.json": "mixed", "Reference/_index.md": "note",
                      manifest.DOCTRINE[0]: "note", "CLAUDE.md": "config", "AGENTS.md": "config",
                      "scripts/memory-model.py": "config", ".vault-meta/memory-approvals.json": "config",
                      ".claude/settings.json": "config"}
            self.assertEqual({k: classes.get(k) for k in expect}, expect)
            self.assertEqual(plan["questions"], [])
        finally:
            ctx.cleanup()

    def test_vault_leftovers_go_only_when_unchanged(self):
        m = self.m
        old = {"Reference/Old same.md": "# shipped\n", "Reference/Old mine.md": "# shipped\n",
               "scripts/old-same.py": "x = 1\n", "scripts/old-mine.py": "x = 1\n"}
        write_tree(m.tmp / "old", old)
        stamped = {rel: fsx.entry_state(str(m.tmp / "old" / rel)) for rel in old}
        write_tree(m.vault, {"Reference/Old same.md": "# shipped\n", "Reference/Old mine.md": "# edited by me\n",
                             "scripts/old-same.py": "x = 1\n", "scripts/old-mine.py": "x = 2  # mine\n"})
        opts = engine_opts(m)
        homes = engine.homes_from(opts)
        answers, _, _ = engine.resolve_answers(F.ROOT, opts, homes, None)
        stamp = {"state": "installed", "answers": dict(answers), "manifest": {"vault": {"entries": stamped}}}
        plan, ctx = engine.build(F.ROOT, answers, homes, stamp, opts)
        ctx.cleanup()
        removed = {a["path"] for a in plan["actions"] if a["layer"] == "vault" and a["op"] == "remove"}
        self.assertEqual(removed & set(old), {"Reference/Old same.md", "scripts/old-same.py"})
        self.assertFalse([mv for mv in plan["migration"]["moves"] if mv[0] == "Reference/Old mine.md"])

    @unittest.skipUnless(F.legacy_available(), "needs the previous release in the Git history")
    def test_shipped_note_versions_list_28777ca_and_the_current_templates(self):
        shipped = manifest.shipped_versions()
        tpl = "shell/vault-template/"
        hint = "installer/vaultos/shipped-versions.json is stale: add normalized_note_sha %s to notes[%r]"
        names = F.git("ls-tree", "-r", "--name-only", F.LEGACY, tpl, cwd=F.ROOT).stdout.splitlines()
        seen = set()
        for path in names:
            rel = path[len(tpl):]
            if rel.startswith("Reference/") or rel in manifest.DOCTRINE:
                text = F.git("show", "%s:%s" % (F.LEGACY, path), cwd=F.ROOT).stdout
                sha = manifest.normalized_note_sha(text)
                self.assertIn(sha, shipped["notes"].get(rel, []), hint % (sha, rel))
                seen.add(rel)
        for rel in sorted(seen | {r for r in manifest.DOCTRINE}):
            path = F.ROOT / tpl / rel
            if path.is_file():
                sha = manifest.normalized_note_sha(path.read_text(encoding="utf-8"))
                self.assertIn(sha, shipped["notes"].get(rel, []), hint % (sha, rel))
        for path in sorted((F.ROOT / tpl / "Reference").iterdir()):
            rel = "Reference/" + path.name
            sha = manifest.normalized_note_sha(path.read_text(encoding="utf-8"))
            self.assertIn(sha, shipped["notes"].get(rel, []), hint % (sha, rel))
        old = json.loads(F.git("show", "%s:%s.obsidian/community-plugins.json" % (F.LEGACY, tpl), cwd=F.ROOT).stdout)
        now = json.loads((F.ROOT / tpl / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(set(old) - set(now)), sorted(set(shipped["community_plugins_old"]) - set(now)))

    def test_rendered_reference_pages_count_as_shipped(self):
        m = self.m
        stage = m.tmp / "render"
        answers = {"vault": str(m.vault), "owner": "Ann Example", "title": "T", "language": "English",
                   "client_label": "Client", "date": "2026-01-02"}
        root = Path(manifest.render_vault(F.ROOT, answers, str(stage)))
        for rel in ("Reference/_index.md", "Reference/Obsidian Plugins.md", "Reference/Claude Commands.md"):
            self.assertTrue(manifest.is_shipped_note(rel, str(root / rel), "Ann Example"), rel)
        mine = m.tmp / "mine.md"
        mine.write_text("# my own index\n", encoding="utf-8")
        self.assertFalse(manifest.is_shipped_note("Reference/_index.md", str(mine), "Ann Example"))

    def test_gitignore_merge(self):
        tpl = "# system\n.vault-meta/memory-index/\n!.vault-meta/memory-index/*.authority.json\n.DS_Store\n"
        self.assertEqual(boundary.desired_gitignore(None, tpl), tpl)
        self.assertEqual(boundary.desired_gitignore(".DS_Store\n\n.vault-meta/memory-index/\n", tpl), tpl)
        mine = "Private/\n# my comment\n*.secret.md\n.DS_Store\n"
        once = boundary.desired_gitignore(mine, tpl, ["/wiki/personal/inbox/secret.md"])
        lines = once.splitlines()
        self.assertEqual(lines[:4], ["Private/", "# my comment", "*.secret.md", "/wiki/personal/inbox/secret.md"])
        self.assertTrue(lines[5].startswith(boundary.MARK_OPEN) and lines[-1].startswith(boundary.MARK_CLOSE))
        self.assertEqual(lines[6:-1], tpl.splitlines(), "the system block comes last: the last matching rule wins")
        self.assertEqual(boundary.desired_gitignore(once, tpl), once)
        self.assertEqual(boundary.desired_gitignore(once, tpl, ["/wiki/personal/inbox/secret.md"]), once)
        self.assertEqual(boundary.gitignore_rule("a/b*[x]?.md"), "/a/b\\*\\[x]\\?.md")
        self.assertEqual(boundary.gitignore_rule("Old/Private", folder=True), "/Old/Private/")

    def test_obsidian_plugin_lists(self):
        tpl = {"file-explorer": True, "graph": True, "sync": False, "publish": False}
        self.assertEqual(boundary.desired_core_plugins(None, tpl), tpl)
        mine = {"file-explorer": False, "sync": True, "publish": True, "daily-notes": True}
        once = boundary.desired_core_plugins(mine, tpl)
        self.assertEqual(once, {"file-explorer": True, "graph": True, "sync": True, "publish": True,
                                "daily-notes": True})
        self.assertEqual(boundary.desired_core_plugins(once, tpl), once)
        ctpl = ["dataview", "obsidian-git"]
        self.assertEqual(boundary.desired_community_plugins(None, ctpl), ctpl)
        mine = ["templater-obsidian", "obsidian-kanban", "excalidraw"]
        foreign = boundary.desired_community_plugins(mine, ctpl, ["obsidian-kanban"], drop_old=False)
        self.assertEqual(foreign, ["templater-obsidian", "obsidian-kanban", "excalidraw", "dataview", "obsidian-git"])
        ours = boundary.desired_community_plugins(mine, ctpl, ["obsidian-kanban"], drop_old=True)
        self.assertEqual(ours, ["templater-obsidian", "excalidraw", "dataview", "obsidian-git"])
        self.assertEqual(boundary.desired_community_plugins(ours, ctpl, ["obsidian-kanban"], drop_old=True), ours)

    def test_vault_origin(self):
        vault = str(self.m.vault)
        self.m.vault.mkdir()
        self.assertEqual(engine.vault_origin(vault, None), "foreign")
        write_tree(self.m.vault, {".vault-meta/mode.json": json.dumps({"memory_model": {"model": "l0-l3"}})})
        self.assertEqual(engine.vault_origin(vault, None), "vault-os")
        stamp = {"state": "installed", "answers": {"vault": vault}, "vault_origin": "foreign"}
        self.assertEqual(engine.vault_origin(vault, stamp), "foreign", "the first answer is kept")
        self.assertEqual(engine.vault_origin(vault, {"state": "installed", "answers": {"vault": "/elsewhere"}}),
                         "vault-os")

    def test_plan_text_names_upstream_writes_kept_items_and_mcp(self):
        plan = {"version": "1", "id": "x", "stamp": True, "approval_needed": True,
                "approval_reasons": ["your approved L3 page wiki/a.md gets link rewrites"],
                "actions": [{"layer": "claude", "op": "upstream", "path": "skills/gstack", "reason": "missing",
                             "commit": "a" * 40, "kind": "setup"}],
                "migration": {"moves": [], "rewrites": {}, "kept": [["Code/p", "Git repository"]]},
                "warnings": [], "questions": []}
        text = engine.summary(plan)
        self.assertIn("rollback does not undo", text)
        self.assertIn("installs no MCP servers", text)
        self.assertIn("install-claude-layer.py --mcp", text)
        self.assertIn("Code/p (Git repository)", text)
        self.assertIn("wiki/a.md gets link rewrites", text)

    def test_upstream_choice_comes_only_from_a_pinned_install(self):
        m = self.m
        m.vault.mkdir()
        write_tree(m.claude, {"skills/remotion/SKILL.md": "---\nname: remotion\n---\n"})
        opts = engine_opts(m, upstream_skills=None)
        homes = engine.homes_from(opts)
        self.assertFalse(engine.resolve_answers(F.ROOT, opts, homes, None)[0]["upstream_skills"],
                         "the remotion folder 28777ca bundled is not a pinned install")
        (m.claude / "skills" / "gstack" / ".git").mkdir(parents=True)
        self.assertTrue(engine.resolve_answers(F.ROOT, opts, homes, None)[0]["upstream_skills"])

    def test_a_failed_checkpoint_commit_leaves_nothing_staged(self):
        vault = self.m.vault
        write_tree(vault, {"Note.md": "# n\n"})
        F.git("init", "-q", cwd=vault)
        F.git("add", "-A", cwd=vault)
        F.git("commit", "-qm", "mine", cwd=vault)
        write_tree(vault, {"scripts/tool.py": "x = 1\n", ".git/hooks/pre-commit": "#!/bin/sh\nexit 1\n"})
        os.chmod(str(vault / ".git" / "hooks" / "pre-commit"), 0o755)
        with self.assertRaises(RuntimeError) as caught:  # a failed commit fails the upgrade (SPOT-CHECK MINOR 5)
            engine.commit_paths(str(vault), ["scripts/tool.py"], "Vault OS: system files", True, [])
        self.assertIn("commit failed", str(caught.exception))
        self.assertEqual(F.git("diff", "--cached", "--name-only", cwd=vault).stdout, "")

    def test_checkpoint_commit_uses_the_on_disk_case(self):
        vault = self.m.vault
        write_tree(vault, {"Scripts/Screenplay draft.md": "# my screenplay\n"})
        if not migrate.case_insensitive(str(vault)):
            self.skipTest("the temporary folder is on a case-sensitive volume")
        F.git("init", "-q", cwd=vault)
        F.git("add", "-A", cwd=vault)
        F.git("commit", "-qm", "mine", cwd=vault)
        write_tree(vault, {"scripts/memory-model.py": "x = 1\n"})
        warnings = []
        engine.commit_paths(str(vault), ["scripts/memory-model.py"], "Vault OS: system files", True, warnings)
        self.assertEqual(warnings, [])
        self.assertEqual(F.git("diff", "--cached", "--name-only", cwd=vault).stdout, "")
        self.assertIn("Scripts/memory-model.py", F.git("ls-files", cwd=vault).stdout)

    def test_a_failed_index_build_raises_and_names_the_zone(self):
        vault = self.m.vault
        write_tree(vault, {"scripts/wiki-lock.sh": 'if [ "$1" = acquire ]; then echo token; fi\n',
                           "scripts/memory-model.py": "import sys\nif sys.argv[1] == 'build':\n"
                                                      "    print('1 issue: wiki/x/hot.md cache-must-use-zone-hot-path')"
                                                      "\n    sys.exit(1)\n"})
        F.git("init", "-q", cwd=vault)
        archive = types.SimpleNamespace(expect_tool_change=lambda *a: None, external=lambda *a: None)
        ctx = types.SimpleNamespace(layers={"vault": manifest.Layer("vault", str(vault))})
        with self.assertRaises(RuntimeError) as caught:
            engine.vault_checkpoint(archive, ctx, {"actions": [], "version": "t"}, str(vault), lambda *_: None)
        self.assertIn("zone work", str(caught.exception))
        self.assertIn("cache-must-use-zone-hot-path", str(caught.exception))


@unittest.skipUnless(POSIX, "the upgrade runs on POSIX")
class PersonalAtSystemPathTest(unittest.TestCase):
    """B1: a person's own file at a vault system path moves below the inbox before the template is written."""

    MINE = {"Reference/_index.md": "# My reference index (personal)\n- [[Books]]\n",
            "Reference/Obsidian Plugins.md": "# my own plugin notes (personal)\n",
            "Reference/Books.md": "# books\n",
            "Home.md": "# home\n[[Obsidian Plugins]] and [index](Reference/_index.md)\n"}

    def test_personal_reference_files_survive_and_links_follow(self):
        m = F.Machine()
        self.addCleanup(m.cleanup)
        write_tree(m.vault, self.MINE)
        plan, out = m.upgrade(*FRESH, "--vault", str(m.vault))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        originals = {rel: text.encode("utf-8") for rel, text in self.MINE.items()}
        self.assertEqual(survived(m.vault, plan, originals), [])
        moves = {src: dst for src, dst, _ in plan["migration"]["moves"]}
        for rel in ("Reference/_index.md", "Reference/Obsidian Plugins.md"):
            self.assertIn(migrate.SYSTEM_PATH_REASON, [why for src, _, why in plan["migration"]["moves"] if src == rel])
            self.assertTrue(manifest.is_shipped_note(rel, str(m.vault / rel), "Test Owner"), "template in place")
        home = moves.get("Home.md", "Home.md")
        targets = link_targets_of(m.vault, home)
        self.assertEqual(targets, [moves["Reference/Obsidian Plugins.md"], moves["Reference/_index.md"]])
        parity = m.engine("parity", "--json")
        self.assertEqual(json.loads(parity.stdout)["differences"], 0, parity.stdout)


@unittest.skipUnless(POSIX, "the upgrade runs on POSIX")
class ForeignVaultUpgradeTest(unittest.TestCase):
    """Audit scenario B and S in one foreign vault: meta names, symlinks, own .gitignore, Obsidian plugins, a nested
    repository, a Scripts/ folder, unsorted notes. One upgrade, then one check per finding."""

    VAULT = {
        ".obsidian/core-plugins.json": json.dumps({"file-explorer": True, "sync": True, "publish": True,
                                                   "daily-notes": True}, indent=2),
        ".obsidian/community-plugins.json": json.dumps(["obsidian-git", "templater-obsidian", "excalidraw"]),
        ".obsidian/plugins/templater-obsidian/data.json": json.dumps({"templates_folder": "Templates"}),
        ".gitignore": "Private/\n*.secret.md\n",
        "Reference/Books.md": "# books\n",
        "Scripts/Screenplay draft.md": "# my screenplay\n",
        "Scripts/scene.fdx": "<FinalDraft/>",
        "Code/myproj/main.py": "print('hi')\n",
        "Code/myproj/README.md": "# my project readme\n",
        "Private/secret.md": "# secret, git-ignored by my own .gitignore\n",
        "Notes/x.secret.md": "# also ignored\n",
        "Daily/2026-10-01.md": "# daily\n",
        "Media/pic.png": b"\x89PNG media",
        "Home.md": "# home\n[[Books]] [[secret]] [[2026-10-01]] ![[pic.png]] [[index]]\n",
        "Notes/hot.md": "# my hot topics\n",
        "Notes/index.md": "# index of notes\n",
        "Notes/real.md": "# real note behind a link\n",
        "Health/Bloodwork 2026.md": "# private medical note\n",
    }
    LINKS = {"Notes/alias.md": "real.md", "Notes/broken.md": "missing.md"}

    @classmethod
    def setUpClass(cls):
        m = cls.m = F.Machine()
        write_tree(m.claude, {"CLAUDE.md": "# my rules\n", "settings.json": json.dumps({"model": "sonnet"}) + "\n"})
        write_tree(m.vault, cls.VAULT)
        for rel, target in cls.LINKS.items():
            os.symlink(target, str(m.vault / rel))
        for repo, message in ((m.vault / "Code" / "myproj", "proj"), (m.vault, "my vault")):
            F.git("init", "-q", cwd=repo)
            F.git("add", "-A", cwd=repo)
            F.git("commit", "-qm", message, cwd=repo)
        F.git("remote", "add", "origin", "https://example.invalid/me/vault.git", cwd=m.vault)
        cls.repo_head = F.git("rev-parse", "HEAD", cwd=m.vault / "Code" / "myproj").stdout
        cls.originals = {rel: (c if isinstance(c, bytes) else c.encode("utf-8")) for rel, c in cls.VAULT.items()
                         if not rel.startswith(".")}
        cls.args = FRESH + ["--vault", str(m.vault)]
        cls.dry = m.engine("upgrade", *cls.args)
        cls.plan, cls.out = m.upgrade(*cls.args)
        cls.moves = {src: dst for src, dst, _ in cls.plan["migration"]["moves"]}

    @classmethod
    def tearDownClass(cls):
        cls.m.cleanup()

    def test_dry_run_and_upgrade_succeed_with_parity_0(self):
        self.assertEqual(self.dry.returncode, 0, self.dry.stdout + self.dry.stderr)
        self.assertEqual(self.out.returncode, 0, self.out.stdout + self.out.stderr)
        self.assertIn("parity 0", self.out.stdout)
        parity = self.m.engine("parity", "--json")
        self.assertEqual(json.loads(parity.stdout)["differences"], 0, parity.stdout)

    def test_every_personal_file_and_link_survives(self):
        self.assertEqual(survived(self.m.vault, self.plan, self.originals), [])
        for rel, target in self.LINKS.items():
            self.assertEqual(os.readlink(str(self.m.vault / rel)), target)
        self.assertIn(["Notes/alias.md", "symbolic link"], self.plan["migration"]["kept"])
        home = self.moves.get("Home.md", "Home.md")
        self.assertNotIn(None, link_targets_of(self.m.vault, home))

    def test_meta_names_and_symlinks_keep_the_index_building(self):
        self.assertTrue(self.moves["Notes/index.md"].endswith("index (2).md"), self.moves)
        self.assertTrue(self.moves["Notes/hot.md"].endswith("hot (2).md"), self.moves)
        env = self.m.env()
        for zone in ("work", "personal", "client"):
            out = subprocess.run([sys.executable, "-B", "scripts/memory-model.py", "validate", "--zone", zone],
                                 cwd=str(self.m.vault), capture_output=True, text=True, env=env)
            self.assertEqual(out.returncode, 0, zone + out.stdout + out.stderr)
        for zone, query in (("work", "memory source of truth"), ("personal", "my hot topics")):
            out = subprocess.run([sys.executable, "-B", "scripts/memory-model.py", "retrieve", query, "--zone", zone,
                                  "--loadout", "general"], cwd=str(self.m.vault), capture_output=True, text=True,
                                 env=env)
            self.assertEqual(out.returncode, 0, zone + out.stderr)

    def test_own_gitignore_is_merged_and_moved_secrets_stay_ignored(self):
        text = (self.m.vault / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[:2], ["Private/", "*.secret.md"])
        self.assertIn(boundary.MARK_OPEN, text)
        for rel in ("Private/secret.md", "Notes/x.secret.md"):
            dest = self.moves.get(rel, rel)
            out = subprocess.run(["git", "check-ignore", "-q", dest], cwd=str(self.m.vault))
            self.assertEqual(out.returncode, 0, "%s (now %s) is no longer ignored" % (rel, dest))

    def test_obsidian_plugin_lists_keep_the_persons_choices(self):
        core = json.loads((self.m.vault / ".obsidian" / "core-plugins.json").read_text(encoding="utf-8"))
        self.assertEqual((core["sync"], core["publish"], core["daily-notes"]), (True, True, True))
        tpl = json.loads((F.ROOT / "shell" / "vault-template" / ".obsidian" / "core-plugins.json").read_text())
        self.assertTrue(all(core[k] for k, v in tpl.items() if v is True))
        community = json.loads((self.m.vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(community[:3], ["obsidian-git", "templater-obsidian", "excalidraw"])
        ctpl = json.loads((F.ROOT / "shell" / "vault-template" / ".obsidian" / "community-plugins.json").read_text())
        self.assertEqual([i for i in community if i in ctpl and i != "obsidian-git"],
                         [i for i in ctpl if i != "obsidian-git"])
        data = self.m.vault / ".obsidian" / "plugins" / "templater-obsidian" / "data.json"
        self.assertEqual(json.loads(data.read_text()), {"templates_folder": "Templates"})

    def test_nested_git_repository_stays_whole(self):
        repo = self.m.vault / "Code" / "myproj"
        self.assertIn(["Code/myproj", "Git repository"], self.plan["migration"]["kept"])
        self.assertFalse([s for s in self.moves if s.startswith("Code/myproj/")])
        self.assertEqual(F.git("status", "--porcelain", cwd=repo).stdout, "")
        self.assertEqual(F.git("rev-parse", "HEAD", cwd=repo).stdout, self.repo_head)

    def test_unsorted_notes_of_a_foreign_vault_go_to_the_personal_inbox(self):
        self.assertEqual(self.plan["vault_origin"], "foreign")
        self.assertTrue(self.moves["Home.md"].startswith("wiki/personal/inbox/"), self.moves)
        self.assertTrue(self.moves["Health/Bloodwork 2026.md"].startswith("wiki/personal/inbox/"), self.moves)

    def test_scripts_folder_commit_succeeds_and_nothing_stays_staged(self):
        vault = self.m.vault
        self.assertEqual(F.git("diff", "--cached", "--name-only", cwd=vault).stdout, "")
        log = F.git("log", "--format=%s", cwd=vault).stdout
        self.assertIn("system files", log)
        self.assertIn("memory index authority", log)
        tracked = F.git("ls-files", cwd=vault).stdout.lower()
        self.assertIn("scripts/memory-model.py", tracked)


# ── fix round 2 after the spot-check: symbolic links at vault system paths (SPOT-CHECK MAJOR-1, MINOR 5) ──────

def kept_and_changed(plan):
    """Paths the plan lists as kept in place while it also writes, removes or moves them (or below them)."""
    kept = {rel.lower() for rel, _ in plan["migration"]["kept"]}
    changed_paths = {a["path"].lower() for a in plan["actions"] if a["layer"] == "vault"}
    for src, dst, _ in plan["migration"]["moves"]:
        changed_paths |= {src.lower(), dst.lower()}
    return sorted(k for k in kept if any(c == k or c.startswith(k + "/") for c in changed_paths))


@unittest.skipUnless(POSIX and F.legacy_available(), "needs the previous release in the Git history and a POSIX shell")
class LinkAtSystemPathTest(unittest.TestCase):
    """A symbolic link at a vault system path, or a linked folder above one, is the person's: the link itself is
    renamed beside the system path (" (n)", same folder, same target bytes, target untouched) before the system file
    or folder is written there as a real one. Upgrade from 28777ca with a foreign vault, then a no-op second plan and
    an exact rollback."""

    def run_case(self, links, outside, check):
        m = legacy_machine()
        self.addCleanup(m.cleanup)
        write_tree(m.home / "outside", outside)
        links = {rel: t.replace("<outside>", str(m.home / "outside")) for rel, t in links.items()}
        os.symlink(str(m.home / "outside" / "final.md"), str(m.vault / "Notes" / "hop.md"))  # a kept link
        for rel, target in links.items():
            path = m.vault / rel
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, str(path))
        F.git("add", "-A", cwd=m.vault)
        F.git("commit", "-qm", "my links", cwd=m.vault)
        args = ["--vault", str(m.vault), "--no-upstream-skills", "--map", "Secret=wiki/workspace/projects/Secret"]
        outside_before = fsx.snapshot(str(m.home / "outside"))
        before = m.snapshot()
        plan, out = m.upgrade(*args)
        self.assertEqual(kept_and_changed(plan), [], "a path is both kept and changed")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("upgrade complete: parity 0", out.stdout)
        self.assertNotIn("commit failed", out.stdout + out.stderr)
        originals = {rel: c if isinstance(c, bytes) else c.encode("utf-8") for rel, c in F.FOREIGN_NOTES.items()}
        self.assertEqual(survived(m.vault, plan, originals), [], "a personal file is not live byte for byte")
        moves = {src: (dst, why) for src, dst, why in plan["migration"]["moves"]}
        for rel, target in links.items():
            self.assertIn(rel, moves, "the link at %s is not moved aside" % rel)
            dst, why = moves[rel]
            self.assertEqual(why, migrate.LINK_REASON)
            self.assertEqual(posixpath.dirname(dst), posixpath.dirname(rel), "the link stays in its folder")
            self.assertTrue(os.path.islink(str(m.vault / dst)), dst)
            self.assertEqual(os.readlink(str(m.vault / dst)), target, "the link bytes are unchanged")
            self.assertFalse(os.path.islink(str(m.vault / rel)), "the system path is a real file or folder")
            self.assertTrue((m.vault / rel).exists(), rel)
        removed = [a["path"] for a in plan["actions"] if a["layer"] == "vault" and a["op"] == "remove"]
        self.assertFalse([p for p in removed if p in links], removed)
        self.assertEqual(fsx.snapshot(str(m.home / "outside")), outside_before, "a link target changed")
        self.assertEqual(os.readlink(str(m.vault / "Notes" / "hop.md")), str(m.home / "outside" / "final.md"))
        # The checkpoint committed the system paths cleanly: nothing staged, nothing left over at them.
        self.assertEqual(F.git("diff", "--cached", "--name-only", cwd=m.vault).stdout, "")
        self.assertEqual(F.git("status", "--porcelain", "--", *links, cwd=m.vault).stdout, "")
        parity = m.engine("parity", "--json")
        self.assertEqual(json.loads(parity.stdout)["differences"], 0, parity.stdout)
        check(m, moves)
        installed = m.snapshot()
        again, rc = m.plan(*args)
        self.assertEqual((rc, again["actions"], again["migration"]["moves"]), (0, [], []))
        self.assertEqual(changed(installed, m.snapshot()), [])
        rolled = m.rollback(F.archives(m)[-1])
        self.assertEqual(rolled.returncode, 0, rolled.stdout + rolled.stderr)
        self.assertEqual(changed(before, m.snapshot()), [])

    def test_file_link_dangling_link_link_chain_and_a_linked_folder_outside_the_vault(self):
        outside = {"myindex.md": "# my own reference index, outside the vault\n", "final.md": "# chain end\n",
                   "vault-skills/mine/SKILL.md": "---\nname: mine\n---\n"}
        links = {"Reference/_index.md": "<outside>/myindex.md",          # a file link
                 "Reference/Obsidian Plugins.md": "gone.md",             # a dangling link
                 "Reference/Claude Commands.md": "../Notes/hop.md",      # a chain: hop.md links outside
                 ".claude/skills": "<outside>/vault-skills"}             # a folder outside the vault

        def check(m, moves):
            chain = m.vault / moves["Reference/Claude Commands.md"][0]
            self.assertEqual(os.path.realpath(str(chain)), os.path.realpath(str(m.home / "outside" / "final.md")))
            self.assertTrue((m.vault / ".claude" / "skills" / "save" / "SKILL.md").is_file())
        self.run_case(links, outside, check)

    def test_a_linked_reference_folder(self):
        outside = {"final.md": "# chain end\n", "shared-ref/_index.md": "# my shared reference index\n",
                   "shared-ref/Books.md": "# books\n"}

        def check(m, moves):
            self.assertEqual(moves["Reference"][0], "Reference (2)")
            self.assertEqual((m.vault / "Reference (2)" / "Books.md").read_text(encoding="utf-8"), "# books\n")
            self.assertTrue(manifest.is_shipped_note("Reference/_index.md", str(m.vault / "Reference" / "_index.md"),
                                                     "Test Owner"))
        self.run_case({"Reference": "../outside/shared-ref"}, outside, check)


@unittest.skipUnless(POSIX, "the upgrade runs on POSIX")
class CheckpointAndPlanCheckTest(unittest.TestCase):
    """A failed vault checkpoint commit fails the upgrade (exit 7, rolled back, never "complete"); a folder that
    replaced a link or a file commits; a plan that keeps and changes one path is an engine error."""

    def test_a_failed_checkpoint_commit_fails_the_upgrade_and_rolls_back(self):
        m = F.Machine()
        self.addCleanup(m.cleanup)
        write_tree(m.vault, {"Home.md": "# home\n"})
        F.git("init", "-q", cwd=m.vault)
        F.git("add", "-A", cwd=m.vault)
        F.git("commit", "-qm", "mine", cwd=m.vault)
        # Only the system-files commit fails; the index receipts still commit, so the zones validate.
        write_tree(m.vault, {".git/hooks/commit-msg": "#!/bin/sh\ngrep -q 'system files' \"$1\" && exit 1\nexit 0\n"})
        os.chmod(str(m.vault / ".git" / "hooks" / "commit-msg"), 0o755)
        before = m.snapshot()
        _, out = m.upgrade(*FRESH, "--vault", str(m.vault))
        self.assertEqual(out.returncode, 7, out.stdout + out.stderr)
        self.assertIn("commit failed", out.stdout + out.stderr)
        self.assertNotIn("upgrade complete", out.stdout)
        self.assertEqual(changed(before, m.snapshot()), [])

    def test_a_folder_that_replaced_a_link_or_a_file_commits(self):
        m = F.Machine()
        self.addCleanup(m.cleanup)
        vault = m.vault
        vault.mkdir(parents=True)
        os.symlink(str(m.home), str(vault / "Reference"))
        write_tree(vault, {"scripts": "a file where the system keeps a folder\n", "Mine.md": "# mine\n"})
        F.git("init", "-q", cwd=vault)
        F.git("add", "-A", cwd=vault)
        F.git("commit", "-qm", "mine", cwd=vault)
        os.rename(str(vault / "Reference"), str(vault / "Reference (2)"))
        os.rename(str(vault / "scripts"), str(vault / "scripts.txt"))
        write_tree(vault, {"Reference/_index.md": "# index\n", "scripts/tool.py": "x = 1\n", "Mine.md": "# edit\n"})
        F.git("add", "Mine.md", cwd=vault)  # the person's own staged change stays staged and out of the commit
        warnings = []
        engine.commit_paths(str(vault), ["Reference", "Reference/_index.md", "scripts", "scripts/tool.py"],
                            "Vault OS: system files", True, warnings)
        self.assertEqual(warnings, [])
        self.assertEqual(F.git("status", "--porcelain", "--", "Reference", "scripts", cwd=vault).stdout, "")
        self.assertEqual(F.git("diff", "--cached", "--name-only", cwd=vault).stdout, "Mine.md\n")

    def test_nothing_is_removed_through_a_linked_folder(self):
        m = F.Machine()
        self.addCleanup(m.cleanup)
        write_tree(m.home / "outside", {"old/SKILL.md": "mine\n"})
        m.claude.mkdir(parents=True)
        os.symlink(str(m.home / "outside"), str(m.claude / "skills"))
        layer = manifest.Layer("claude", str(m.claude))
        layer.absent.add("skills/old")  # a leftover path that resolves into the link's target
        self.assertEqual(engine.layer_actions(layer, types.SimpleNamespace(data={}), {}), [])
        archive = journal.Archive.create(str(m.backups), {"claude": str(m.claude)})
        with self.assertRaises(OSError):
            archive.remove("claude", "skills/old")
        self.assertTrue((m.home / "outside" / "old" / "SKILL.md").is_file())

    def test_a_path_kept_and_changed_is_an_engine_error(self):
        plan = {"migration": {"kept": [["Reference", "symbolic link"], ["Notes/a.md", "symbolic link"]],
                              "moves": [["Notes/b.md", "wiki/personal/inbox/Notes/b.md", "unsorted"]]},
                "actions": [{"layer": "vault", "op": "remove", "path": "Reference"},
                            {"layer": "claude", "op": "remove", "path": "Notes/a.md"}]}
        self.assertEqual(engine.plan_conflicts(plan), ["Reference"])
        plan["actions"] = [{"layer": "vault", "op": "write", "path": "Reference/_index.md"}]
        self.assertEqual(engine.plan_conflicts(plan), ["Reference"])
        plan["actions"] = []
        plan["migration"]["moves"].append(["Notes/a.md", "Notes/a (2).md", "symbolic link at a system path"])
        self.assertEqual(engine.plan_conflicts(plan), ["Notes/a.md"])
        plan["migration"]["moves"].pop()
        self.assertEqual(engine.plan_conflicts(plan), [])


@unittest.skipUnless(POSIX, "the upgrade runs on POSIX")
class IndexPredictionTest(unittest.TestCase):
    """M1: a note that would break the memory index where it stays is a plan question; --confirm is refused."""

    def test_a_hot_page_outside_the_zone_hot_path_blocks_the_plan(self):
        m = F.Machine()
        self.addCleanup(m.cleanup)
        subprocess.run([sys.executable, str(F.ROOT / "installer" / "render-vault.py"), "--target", str(m.vault),
                        "--owner", "Test Owner", "--title", "Test Vault", "--language", "English", "--client-label",
                        "Client", "--approve-memory-policy"], check=True, capture_output=True, env=m.env())
        write_tree(m.vault, {"wiki/resources/Notes/hot.md": "# a stray hot page\n"})
        args = FRESH + ["--vault", str(m.vault)]
        plan, rc = m.plan(*args)
        self.assertEqual(rc, 3)
        self.assertTrue([q for q in plan["questions"] if "wiki/resources/Notes/hot.md" in q
                         and "cache-must-use-zone-hot-path" in q], plan["questions"])
        before = m.snapshot()
        out = m.engine("upgrade", *args, "--confirm", plan["id"], "--approve-memory-policy")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("open questions", out.stderr)
        self.assertEqual(changed(before, m.snapshot()), [])


if __name__ == "__main__":
    unittest.main()
