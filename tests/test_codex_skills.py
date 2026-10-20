"""Codex and ~/.agents skill sets: the bundled host skills (shell/codex/skills, shell/codex/skill-targets.json) and
the upstream host copies (gstack's own Codex-host setup, Remotion in ~/.agents/skills).

Every test runs on a temporary HOME; the upstream fixtures are local Git repositories (no network).
"""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upgrade_fixtures as F  # noqa: E402

ROOT = F.ROOT
CLAUDE_SKILLS = ROOT / "shell" / "claude" / "skills"
HOST_SKILLS = ROOT / "shell" / "codex" / "skills"
TARGETS = json.loads((ROOT / "shell" / "codex" / "skill-targets.json").read_text(encoding="utf-8"))
ANSWERS = ["--owner", "Test Owner", "--title", "Test Vault", "--language", "English", "--client-label", "Client",
           "--codex"]
POSIX = not sys.platform.startswith("win")


def claude_entries():
    """What every skills target gets from shell/claude/skills: the folders and the root .md and .txt files."""
    return {n for n in os.listdir(str(CLAUDE_SKILLS))
            if (CLAUDE_SKILLS / n).is_dir() or n.endswith((".md", ".txt"))}


def host_base(host):
    """What Codex or ~/.agents gets from shell/claude/skills: the root files, vault-os-update and the folders the
    owner's live folder holds (claude_mirror); not every bundled skill."""
    keep = set(TARGETS["claude_mirror"][host]) | {"vault-os-update"}
    return {n for n in claude_entries() if not (CLAUDE_SKILLS / n).is_dir() or n in keep}


def changed(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def rollback(m, archive):
    """The engine's rollback: the preview, then the confirmed run (or the direct run of an engine without a preview)."""
    if hasattr(m, "rollback"):
        return m.rollback(archive)
    return m.engine("rollback", str(archive))


class BundledHostSkillsTest(unittest.TestCase):
    """The folders, the target lists, the licences and the third-party table agree."""

    def test_targets_name_existing_folders_only(self):
        folders = {p.name for p in HOST_SKILLS.iterdir() if p.is_dir()}
        listed = set(TARGETS["codex"]) | set(TARGETS["agents"])
        self.assertEqual(listed, folders)
        self.assertEqual(sorted(TARGETS["codex"]), TARGETS["codex"])
        self.assertEqual(sorted(TARGETS["agents"]), TARGETS["agents"])
        self.assertFalse(folders & claude_entries(), "a host skill may not shadow a Claude skill")
        self.assertEqual([p.name for p in HOST_SKILLS.iterdir() if not p.is_dir()], [])

    def test_every_bundled_folder_has_its_licence(self):
        for folder in sorted(p for p in HOST_SKILLS.iterdir() if p.is_dir()):
            licences = [f for f in os.listdir(str(folder)) if re.match(r"(?i)^licen[cs]e", f)]
            self.assertTrue(licences, "%s has no LICENSE file" % folder.name)
            text = (folder / licences[0]).read_text(encoding="utf-8", errors="replace")
            self.assertRegex(text, r"(?i)(MIT License|Apache License)", folder.name)
            self.assertTrue((folder / "SKILL.md").is_file(), folder.name)

    def test_third_party_table_lists_every_host_skill(self):
        table = (CLAUDE_SKILLS / "THIRD-PARTY-SKILLS.md").read_text(encoding="utf-8")
        for name in sorted(p.name for p in HOST_SKILLS.iterdir() if p.is_dir()):
            self.assertRegex(table, r"(?<![\w-])%s(?![\w-])" % re.escape(name), name)

    def test_no_junk_or_links_in_the_bundle(self):
        for root, dirs, files in os.walk(str(HOST_SKILLS)):
            for name in dirs + files:
                path = os.path.join(root, name)
                self.assertFalse(os.path.islink(path), path)
                self.assertNotIn(name, (".DS_Store", "__pycache__", "node_modules", ".git"), path)


class InstalledSetsTest(unittest.TestCase):
    """--codex installs exactly the planned sets, and the step installers give the same folders."""

    def setUp(self):
        self.m = F.Machine()

    def tearDown(self):
        self.m.cleanup()

    def test_engine_install_gives_the_planned_codex_and_agents_sets(self):
        m = self.m
        args = ANSWERS + ["--no-upstream-skills", "--vault", str(m.vault)]
        plan, out = m.upgrade(*args)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        base = claude_entries()
        planned = {}
        for layer in ("claude", "codex", "agents"):
            planned[layer] = {a["path"].split("/")[1] for a in plan["actions"]
                              if a["layer"] == layer and a["path"].startswith("skills/")}
        self.assertEqual(planned["claude"], base)
        self.assertEqual(planned["codex"], host_base("codex") | set(TARGETS["codex"]))
        self.assertEqual(planned["agents"], host_base("agents") | set(TARGETS["agents"]))
        for layer, root in (("claude", m.claude), ("codex", m.codex), ("agents", m.agents)):
            live = {n for n in os.listdir(str(root / "skills")) if not n.startswith(".")}
            self.assertEqual(live, planned[layer], layer)
        for name in TARGETS["codex"]:
            self.assertTrue((m.codex / "skills" / name / "SKILL.md").is_file(), name)
        self.assertEqual(m.engine("parity").returncode, 0)

    def test_step_installer_gives_the_same_sets(self):
        m = self.m
        inst = ROOT / "installer" / "install-claude-layer.py"
        out = subprocess.run([sys.executable, str(inst), "--owner", "Test Owner", "--language", "English",
                              "--claude-home", str(m.claude), "--python", sys.executable, "--platform", "linux",
                              "--extra-skills-dir", str(m.codex / "skills"),
                              "--extra-skills-dir", str(m.agents / "skills")],
                             capture_output=True, text=True, env=m.env())
        self.assertEqual(out.returncode, 0, out.stderr)
        base = claude_entries()
        sets = {layer: {n for n in os.listdir(str(root / "skills")) if not n.startswith(".")}
                for layer, root in (("claude", m.claude), ("codex", m.codex), ("agents", m.agents))}
        self.assertEqual(sets["claude"], base)
        self.assertEqual(sets["codex"], host_base("codex") | set(TARGETS["codex"]))
        self.assertEqual(sets["agents"], host_base("agents") | set(TARGETS["agents"]))
        # Any other extra folder gets only the Claude skills.
        other = m.tmp / "elsewhere" / "skills"
        subprocess.run([sys.executable, str(inst), "--owner", "Test Owner", "--language", "English",
                        "--claude-home", str(m.claude), "--python", sys.executable, "--platform", "linux",
                        "--extra-skills-dir", str(other)], check=True, capture_output=True, env=m.env())
        self.assertEqual({n for n in os.listdir(str(other))}, base)


class LiveSetsTest(unittest.TestCase):
    """Codex and ~/.agents get exactly the owner's live sets, not a mirror of every bundled skill (owner rule:
    the system is the same as his, with the same settings)."""

    def setUp(self):
        self.m = F.Machine()

    def tearDown(self):
        self.m.cleanup()

    def folders(self, root):
        return {n for n in os.listdir(str(root / "skills")) if (root / "skills" / n).is_dir()}

    def test_mirror_lists_name_bundled_claude_skills_only(self):
        bundled = {n for n in claude_entries() if (CLAUDE_SKILLS / n).is_dir()}
        for host in ("codex", "agents"):
            names = TARGETS["claude_mirror"][host]
            self.assertEqual(names, sorted(names))
            self.assertLessEqual(set(names), bundled, host)
            self.assertFalse(set(names) & set(TARGETS[host]), host)

    def test_fresh_install_gives_exactly_the_live_sets(self):
        m = self.m
        plan, out = m.upgrade(*ANSWERS, "--no-upstream-skills", "--vault", str(m.vault))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        for host, root in (("codex", m.codex), ("agents", m.agents)):
            want = set(TARGETS["claude_mirror"][host]) | set(TARGETS[host]) | {"vault-os-update"}
            self.assertEqual(self.folders(root), want, host)
            self.assertFalse((root / "skills" / "figma-craft").exists(), host)
            self.assertTrue((root / "skills" / "THIRD-PARTY-SKILLS.md").is_file(), host)
        self.assertTrue((m.claude / "skills" / "ui-ux-pro-max" / "SKILL.md").is_file())
        self.assertTrue((m.claude / "skills" / "skill-router" / "SKILL.md").is_file())
        self.assertEqual(m.engine("parity").returncode, 0)

    @unittest.skipUnless(F.legacy_available(), "the previous release is not in this clone")
    def test_upgrade_from_the_old_mirror_removes_unchanged_extras_and_keeps_edits(self):
        m = self.m
        F.legacy_install(m)
        edited = m.codex / "skills" / "ui-ux-pro-max" / "SKILL.md"
        edited.write_text(edited.read_text(encoding="utf-8") + "\nmy own line\n", encoding="utf-8")
        added = m.agents / "skills" / "domain-modeling"
        (added / "notes.md").write_text("mine\n", encoding="utf-8")
        untouched = m.codex / "skills" / "scrum-master"
        self.assertTrue(untouched.is_dir())
        plan, out = m.upgrade(*ANSWERS, "--no-upstream-skills", "--vault", str(m.vault))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("my own line", edited.read_text(encoding="utf-8"))
        self.assertEqual((added / "notes.md").read_text(encoding="utf-8"), "mine\n")
        self.assertFalse(untouched.exists())
        self.assertFalse((m.agents / "skills" / "scrum-master").exists())
        removed = {(a["layer"], a["path"]) for a in plan["actions"] if a["op"] == "remove"}
        self.assertIn(("codex", "skills/scrum-master"), removed)
        self.assertNotIn(("codex", "skills/ui-ux-pro-max"), removed)
        self.assertNotIn(("agents", "skills/domain-modeling"), removed)
        # The kept copies are the person's now: a second run plans nothing, and parity stays clean.
        self.assertEqual(m.engine("parity").returncode, 0)
        again, _ = m.plan(*ANSWERS, "--no-upstream-skills", "--vault", str(m.vault))
        self.assertEqual(again["actions"], [])

    @unittest.skipUnless(F.legacy_available(), "the previous release is not in this clone")
    def test_legacy_mirror_hashes_match_the_release(self):
        sys.path.insert(0, str(ROOT / "installer"))
        from vaultos import fsx
        old = F.legacy_tree(self.m.tmp / "rel")
        listed = json.loads((ROOT / "installer" / "vaultos" / "legacy-mirror.json").read_text(encoding="utf-8"))
        base = old / "shell" / "claude" / "skills"
        want = {n: fsx.tree_sha(str(base / n))[0] for n in sorted(os.listdir(str(base))) if (base / n).is_dir()}
        self.assertEqual(listed["skills"], want)


SUITE_SETUP = r'''#!/bin/sh
set -e
if [ "$1" = "--host" ] && [ "$2" = "codex" ]; then
  mkdir -p .agents/skills/suite-alpha "$CODEX_HOME/skills/suite"
  cp alpha/SKILL.md .agents/skills/suite-alpha/SKILL.md
  cp VERSION "$CODEX_HOME/skills/suite/VERSION"
  echo "$HOME" > "$CODEX_HOME/skills/suite/runtime-for"
  ln -s "$PWD/.agents/skills/suite-alpha" "$CODEX_HOME/skills/suite-alpha"
  exit 0
fi
echo "$HOME" > installed-for
ln -s "$PWD/alpha" "$HOME/.claude/skills/suite-alpha"
'''


@unittest.skipUnless(POSIX, "the setup fixture is a shell script")
class UpstreamHostTest(unittest.TestCase):
    """A setup kind also runs its Codex-host setup at the same pin; a folder kind gets its ~/.agents copy."""

    def setUp(self):
        self.m = F.Machine()
        self.src = self.m.tmp / "src"
        folder = self.src / "folder"
        (folder / "skills" / "demo").mkdir(parents=True)
        (folder / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        suite = self.src / "suite"
        (suite / "alpha").mkdir(parents=True)
        (suite / "alpha" / "SKILL.md").write_text("---\nname: suite-alpha\n---\n", encoding="utf-8")
        (suite / "VERSION").write_text("1\n", encoding="utf-8")
        (suite / "setup").write_text(SUITE_SETUP, encoding="utf-8")
        (suite / "setup").chmod(0o755)
        self.commits = {}
        for repo in (folder, suite):
            F.git("init", "-q", cwd=repo)
            F.git("add", "-A", cwd=repo)
            F.git("commit", "-q", "-m", "one", cwd=repo)
            self.commits[repo.name] = F.git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        self.manifest = self.m.tmp / "upstream.json"
        self.write_manifest(hosts=True)
        self.args = ANSWERS + ["--upstream-skills", "--vault", str(self.m.vault), "--upstream-manifest",
                               str(self.manifest)]

    def write_manifest(self, hosts):
        demo = {"name": "demo", "kind": "folder", "repo": str(self.src / "folder"), "commit": self.commits["folder"],
                "path": "skills/demo", "requires": ["git"], "platforms": ["darwin", "linux", "win32"]}
        suite = {"name": "suite", "kind": "setup", "repo": str(self.src / "suite"), "commit": self.commits["suite"],
                 "requires": ["git"], "platforms": ["darwin", "linux"], "setup": ["./setup"]}
        if hosts:
            demo["hosts"] = {"agents": {"name": "demo-agents"}}
            suite["hosts"] = {"codex": {"setup": ["./setup", "--host", "codex"]}}
        self.manifest.write_text(json.dumps({"skills": [demo, suite]}), encoding="utf-8")

    def tearDown(self):
        self.m.cleanup()

    def plan(self):
        out = self.m.engine("upgrade", *self.args, "--json")
        self.assertIn(out.returncode, (0, 3), out.stderr)
        return json.loads(out.stdout)["plan"]

    def apply(self, plan):
        out = self.m.engine("upgrade", *self.args, "--confirm", plan["id"], "--approve-memory-policy")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def parity(self):
        out = self.m.engine("parity", "--upstream-manifest", str(self.manifest))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def upstream_ops(self, plan):
        return sorted((a["layer"], a["path"]) for a in plan["actions"] if a["op"] == "upstream")

    def test_install_adopt_rollback_and_new_pin(self):
        m = self.m
        empty = m.snapshot()
        plan = self.plan()
        self.assertEqual(self.upstream_ops(plan), [("agents", "skills/demo-agents"), ("claude", "skills/demo"),
                                                   ("claude", "skills/suite"), ("codex", "skills/suite")])
        self.apply(plan)
        codex = m.codex / "skills"
        self.assertEqual((codex / "suite" / "runtime-for").read_text().strip(), str(m.home))
        self.assertTrue(os.path.islink(str(codex / "suite-alpha")))
        self.assertEqual(os.path.realpath(str(codex / "suite-alpha")),
                         os.path.realpath(str(m.claude / "skills" / "suite" / ".agents" / "skills" / "suite-alpha")))
        self.assertTrue((m.agents / "skills" / "demo-agents" / "SKILL.md").is_file())
        self.assertFalse((m.agents / "skills" / "demo").exists())
        stamp = json.loads((m.claude / "vault-os" / "installed.json").read_text(encoding="utf-8"))
        self.assertEqual(stamp["upstream"]["suite"]["hosts"]["codex"]["children"], ["suite-alpha"])
        self.assertEqual(stamp["upstream"]["demo"]["hosts"]["agents"]["name"], "demo-agents")
        self.parity()
        # The next run adopts the Claude install and both host copies: nothing to change.
        installed = m.snapshot()
        plan = self.plan()
        self.assertEqual((plan["actions"], plan["stamp"]), ([], False))
        self.apply(plan)
        self.assertEqual(changed(installed, m.snapshot()), [])
        # Rollback removes the host copies with everything else.
        out = rollback(m, F.archives(m)[-1])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(changed(empty, m.snapshot()), [])
        # A new pin reinstalls the setup kind: the old Codex runtime and its links go first, the host setup
        # then runs again from the new checkout.
        self.apply(self.plan())
        suite = self.src / "suite"
        (suite / "VERSION").write_text("2\n", encoding="utf-8")
        F.git("commit", "-q", "-am", "two", cwd=suite)
        self.commits["suite"] = F.git("rev-parse", "HEAD", cwd=suite).stdout.strip()
        self.write_manifest(hosts=True)
        plan = self.plan()
        self.assertEqual(self.upstream_ops(plan), [("claude", "skills/suite"), ("codex", "skills/suite")])
        removed = {(a["layer"], a["path"]) for a in plan["actions"] if a["op"] == "remove"}
        self.assertTrue({("codex", "skills/suite"), ("codex", "skills/suite-alpha"),
                         ("claude", "skills/suite-alpha")} <= removed, removed)
        self.apply(plan)
        self.assertEqual((codex / "suite" / "VERSION").read_text().strip(), "2")
        self.assertTrue(os.path.islink(str(codex / "suite-alpha")))
        self.parity()

    def test_codex_host_added_to_an_adopted_claude_install(self):
        m = self.m
        self.write_manifest(hosts=False)
        self.apply(self.plan())
        before = m.snapshot()
        checkout_head = F.git("rev-parse", "HEAD", cwd=m.claude / "skills" / "suite").stdout
        self.write_manifest(hosts=True)
        plan = self.plan()
        # Claude keeps its install; only the host copies are planned, with no new fetch of the setup kind.
        self.assertEqual(self.upstream_ops(plan), [("agents", "skills/demo-agents"), ("codex", "skills/suite")])
        self.apply(plan)
        self.assertEqual(F.git("rev-parse", "HEAD", cwd=m.claude / "skills" / "suite").stdout, checkout_head)
        self.assertTrue((m.codex / "skills" / "suite" / "runtime-for").is_file())
        self.parity()
        # Rolling this upgrade back leaves the earlier install exactly as it was, the host render included.
        out = rollback(m, F.archives(m)[-1])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([k for k in changed(before, m.snapshot()) if not k.startswith(".claude/vault-os/")], [])

    def test_step_installer_sets_up_the_hosts(self):
        m = self.m
        inst = ROOT / "installer" / "install-claude-layer.py"
        cmd = [sys.executable, str(inst), "--owner", "Test Owner", "--language", "English", "--claude-home",
               str(m.claude), "--python", sys.executable, "--extra-skills-dir", str(m.codex / "skills"),
               "--extra-skills-dir", str(m.agents / "skills"), "--upstream-skills", "--upstream-manifest",
               str(self.manifest)]
        report = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True, env=m.env()).stdout)
        self.assertEqual(len([u for u in report["upstream"] if u.startswith("installed")]), 4, report)
        self.assertTrue((m.codex / "skills" / "suite" / "runtime-for").is_file())
        self.assertTrue((m.agents / "skills" / "demo-agents" / "SKILL.md").is_file())
        again = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True, env=m.env()).stdout)
        self.assertEqual(again["upstream"], [])
        # The engine adopts the Codex host of that install as it is.
        plan = self.plan()
        self.assertEqual([a for a in plan["actions"] if a["layer"] == "codex" and a["path"].startswith("skills/")], [])


if __name__ == "__main__":
    unittest.main()
