"""Tests for the parity steps of the installers: settings defaults, bypass opt-in, upstream skills, MCP, Codex."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / "installer" / "install-claude-layer.py"
CODEX = ROOT / "installer" / "install-codex-layer.py"
SHELL = ROOT / "shell" / "claude"


def run(script, *args, env=None):
    out = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=300, env=env)
    if out.returncode != 0:
        raise AssertionError(out.stdout + out.stderr)
    return json.loads(out.stdout)


def claude(home, *extra, language="English"):
    return run(CLAUDE, "--owner", "Test Owner", "--language", language, "--claude-home", str(home),
               "--platform", "linux", *extra)


def git(*args, cwd):
    subprocess.run(["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "user.name=t", "-c",
                    "user.email=t@example.invalid", *args], cwd=cwd, check=True, capture_output=True, text=True)


class SettingsParityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-os-parity-"))
        self.home = self.tmp / ".claude"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def settings(self):
        return json.loads((self.home / "settings.json").read_text(encoding="utf-8"))

    def test_owner_defaults_are_written(self):
        claude(self.home)
        s = self.settings()
        template = json.loads((SHELL / "settings.template.json").read_text(encoding="utf-8"))
        self.assertEqual(s["model"], "opus[1m]")
        self.assertEqual(s["modelSettings"], template["modelSettings"])
        self.assertEqual(s["env"], {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"})
        self.assertEqual(s["skillOverrides"], template["skillOverrides"])
        self.assertEqual(s["skillOverrides"]["gstack-review"], "user-invocable-only")
        self.assertEqual(s["skillOverrides"]["superpowers:using-git-worktrees"], "off")
        self.assertEqual(s["permissions"], {"defaultMode": "default"})
        self.assertTrue(s["statusLine"]["command"].endswith("--lang en"))
        self.assertIs(s["enabledPlugins"]["i-have-adhd@i-have-adhd"], False)  # ships off (owner 2026-10-20)
        self.assertEqual(s["extraKnownMarketplaces"]["i-have-adhd"],
                         {"source": {"source": "github", "repo": "ayghri/i-have-adhd"}})

    def test_russian_language_gets_russian_status_line(self):
        claude(self.home, language="русский")
        self.assertTrue(self.settings()["statusLine"]["command"].endswith("--lang ru"))

    def test_maps_merge_entry_by_entry(self):
        self.home.mkdir(parents=True)
        user = {"skillOverrides": {"humanizer": "on", "mine": "off"}, "env": {"MY_FLAG": "1"},
                "permissions": {"defaultMode": "acceptEdits"}}
        (self.home / "settings.json").write_text(json.dumps(user), encoding="utf-8")
        claude(self.home)
        s = self.settings()
        self.assertEqual(s["skillOverrides"]["humanizer"], "on")
        self.assertEqual(s["skillOverrides"]["mine"], "off")
        self.assertEqual(s["skillOverrides"]["gpt-taste"], "name-only")
        self.assertEqual(s["env"], {"MY_FLAG": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"})
        self.assertEqual(s["permissions"], {"defaultMode": "acceptEdits"})

    def test_bypass_only_on_request_and_never_over_a_user_mode(self):
        claude(self.home, "--bypass-permissions")
        perms = self.settings()["permissions"]
        self.assertEqual(perms["defaultMode"], "bypassPermissions")
        self.assertIn("Bash(*)", perms["allow"])
        self.assertEqual(len(perms["allow"]), 10)
        other = self.tmp / "other" / ".claude"
        other.mkdir(parents=True)
        (other / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "plan"}}), encoding="utf-8")
        claude(other, "--bypass-permissions")
        self.assertEqual(json.loads((other / "settings.json").read_text())["permissions"]["defaultMode"], "plan")

    def test_unchanged_settings_are_not_backed_up_again(self):
        claude(self.home)
        claude(self.home)
        report = claude(self.home)
        self.assertEqual(report["backed_up"], [])
        self.assertEqual(len(list(self.home.glob("settings.json.backup-*"))), 0)


class UpstreamSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-os-upstream-"))
        self.home = self.tmp / "home" / ".claude"
        folder = self.tmp / "folder-src"
        (folder / "skills" / "demo").mkdir(parents=True)
        (folder / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        setup = self.tmp / "setup-src"
        (setup / "bin").mkdir(parents=True)
        (setup / "setup").write_text('#!/bin/sh\necho "$HOME" > installed-for\n', encoding="utf-8")
        (setup / "bin" / "gstack-config").write_text('#!/bin/sh\necho "$2=$3" >> config-set\n', encoding="utf-8")
        for f in (setup / "setup", setup / "bin" / "gstack-config"):
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        commits = {}
        for repo in (folder, setup):
            git("init", "-q", cwd=repo)
            git("add", "-A", cwd=repo)
            git("commit", "-q", "-m", "x", cwd=repo)
            commits[repo] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                           text=True).stdout.strip()
        self.manifest = self.tmp / "upstream.json"
        self.manifest.write_text(json.dumps({"skills": [
            {"name": "demo", "kind": "folder", "repo": str(folder), "commit": commits[folder], "path": "skills/demo",
             "requires": ["git"], "platforms": ["darwin", "linux", "win32"]},
            {"name": "suite", "kind": "setup", "repo": str(setup), "commit": commits[setup], "requires": ["git"],
             "platforms": ["darwin", "linux"], "setup": ["./setup"], "config": {"skill_prefix": "true"}},
            {"name": "needs-tool", "kind": "folder", "repo": str(folder), "commit": commits[folder], "path": "skills/demo",
             "requires": ["no-such-tool-xyz"], "platforms": ["linux"]},
        ]}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def install(self, *extra):
        return claude(self.home, "--upstream-skills", "--upstream-manifest", str(self.manifest), *extra)

    def test_dry_run_plans_only(self):
        report = self.install("--dry-run")
        self.assertIn("would install", " ".join(report["upstream"]))
        self.assertFalse(self.home.exists())

    @unittest.skipIf(sys.platform.startswith("win"), "the setup fixture is a shell script")
    def test_pinned_install_then_second_run_is_same(self):
        report = self.install()
        self.assertTrue((self.home / "skills" / "demo" / "SKILL.md").is_file())
        suite = self.home / "skills" / "suite"
        self.assertEqual((suite / "installed-for").read_text().strip(), str(self.home.parent))
        self.assertEqual((suite / "config-set").read_text().strip(), "skill_prefix=true")
        self.assertTrue(any("needs no-such-tool-xyz" in s for s in report["skipped"]))
        again = self.install()
        self.assertEqual(again["upstream"], [])
        self.assertTrue(any(s.startswith("skills/suite/") for s in again["same"]))

    def test_shipped_manifest_is_pinned(self):
        manifest = json.loads((SHELL / "upstream-skills.json").read_text(encoding="utf-8"))
        names = {s["name"] for s in manifest["skills"]}
        self.assertEqual(names, {"gstack", "remotion"})
        for s in manifest["skills"]:
            self.assertRegex(s["commit"], r"^[0-9a-f]{40}$")
            self.assertTrue(s["repo"].startswith("https://github.com/"))


@unittest.skipIf(sys.platform.startswith("win"), "the fake claude CLI is a shell script")
class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-os-mcp-"))
        self.home = self.tmp / ".claude"
        self.log = self.tmp / "calls.log"
        self.fake = self.tmp / "claude"
        self.fake.write_text('#!/bin/sh\necho "$@" >> "%s"\n[ "$2" = get ] && grep -q "add .* $3 " "%s" && exit 0\n'
                             '[ "$2" = get ] && exit 1\nexit 0\n' % (self.log, self.log), encoding="utf-8")
        self.fake.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_default_servers_url_only_and_idempotent(self):
        report = claude(self.home, "--mcp", "default", "--claude-bin", str(self.fake))
        self.assertEqual(sorted(report["mcp"]), ["added magnific", "added replicate"])
        calls = self.log.read_text()
        self.assertIn("mcp add --scope user --transport http magnific https://mcp.magnific.com", calls)
        self.assertNotIn("higgsfield", calls)
        self.assertNotRegex(calls, r"(?i)token|header|--env")
        again = claude(self.home, "--mcp", "default", "--claude-bin", str(self.fake))
        self.assertEqual(again["mcp"], [])
        self.assertIn("mcp magnific", again["same"])

    def test_dry_run_prints_commands(self):
        report = claude(self.home, "--mcp", "all", "--dry-run")
        self.assertEqual(len(report["mcp"]), 3)
        self.assertTrue(all(c.startswith("would run: claude mcp add --scope user") for c in report["mcp"]))


class CodexLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-os-codex-"))
        self.home = self.tmp / "codex"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def codex(self, platform, *extra):
        return run(CODEX, "--vault", str(self.tmp / "My Vault"), "--codex-home", str(self.home), "--platform", platform,
                   *extra)

    def test_merge_keeps_user_values_and_adds_parity_blocks(self):
        self.home.mkdir()
        (self.home / "config.toml").write_text('model = "my-model"\n\n[plugins."figma@claude-plugins-official"]\n'
                                               'enabled = false\n', encoding="utf-8")
        report = self.codex("darwin", "--mcp", "higgsfield")
        text = (self.home / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "my-model"', text)
        self.assertNotIn('model = "gpt-6.1-sol"', text)
        self.assertIn('model_reasoning_effort = "xhigh"', text)
        self.assertIn("[features]", text)
        self.assertIn('[plugins."superpowers@claude-plugins-official"]', text)
        self.assertEqual(text.count('[plugins."figma@claude-plugins-official"]'), 1)
        self.assertIn('[mcp_servers.higgsfield]\nurl = "https://mcp.higgsfield.ai/mcp"', text)
        self.assertIn('[plugins."i-have-adhd@i-have-adhd"]\nenabled = false\n', text)  # ships off
        self.assertIn('[marketplaces.i-have-adhd]\nsource_type = "git"\n'
                      'source = "https://github.com/ayghri/i-have-adhd.git"\n', text)
        self.assertNotIn("hooks.state", text)
        self.assertEqual(len(report["backed_up"]), 1)
        hooks = json.loads((self.home / "hooks.json").read_text(encoding="utf-8"))
        commands = " ".join(h["command"] for g in hooks["hooks"].values() for e in g for h in e["hooks"])
        self.assertIn(str(self.tmp / "My Vault"), commands)
        self.assertIn("keep-awake.sh", commands)
        self.assertIn("afplay", commands)
        post = [h["command"] for e in hooks["hooks"]["PostToolUse"] for h in e["hooks"]]
        self.assertTrue(any("impeccable" in c for c in post), "the same command on two events must land on both")
        again = self.codex("darwin", "--mcp", "higgsfield")
        self.assertEqual((again["config"], again["hooks"], again["backed_up"]), ([], [], []))

    def test_linux_gets_no_macos_hooks(self):
        self.codex("linux")
        hooks = json.loads((self.home / "hooks.json").read_text(encoding="utf-8"))
        commands = " ".join(h["command"] for g in hooks["hooks"].values() for e in g for h in e["hooks"])
        self.assertNotIn("afplay", commands)
        self.assertNotIn("keep-awake", commands)
        self.assertIn("impeccable", commands)

    def test_dry_run_writes_nothing(self):
        report = self.codex("linux", "--dry-run")
        self.assertTrue(report["config"])
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
