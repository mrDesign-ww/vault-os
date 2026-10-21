import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer" / "install-claude-layer.py"
SHELL = ROOT / "shell" / "claude"
MAC_ONLY = ("image-size-gate.py", "keep-awake.sh", "afplay")


def run(home, *extra):
    cmd = [sys.executable, str(INSTALLER), "--owner", "Test Owner", "--language", "English",
           "--claude-home", str(home), *extra]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stdout + out.stderr)
    return json.loads(out.stdout)


def hook_commands(settings):
    return [h["command"] for groups in settings.get("hooks", {}).values() for g in groups for h in g["hooks"]]


class InstallClaudeLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-os-claude-layer-"))
        self.home = self.tmp / "claude"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def settings(self):
        return json.loads((self.home / "settings.json").read_text(encoding="utf-8"))

    def test_fresh_install_renders_every_layer(self):
        report = run(self.home, "--platform", "linux")
        self.assertFalse(report["skipped"] and [s for s in report["skipped"] if "POSIX" not in s])
        for layer in ("agents", "hooks", "docs", "templates", "statusline", "scripts", "skills"):
            self.assertTrue((self.home / layer).is_dir(), layer)
        self.assertEqual(len(list((self.home / "agents").glob("*.md"))), len(list((SHELL / "agents").glob("*.md"))))
        skills = [p for p in (SHELL / "skills").iterdir() if p.is_dir()]
        for skill in skills:
            self.assertTrue((self.home / "skills" / skill.name / "SKILL.md").is_file(), skill.name)
        claude_md = (self.home / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Test Owner", claude_md)
        self.assertIn("English", claude_md)
        settings_text = (self.home / "settings.json").read_text(encoding="utf-8")
        for text in (claude_md, settings_text):
            self.assertIsNone(re.search(r"\{\{[A-Z_]+\}\}", text))
        settings = self.settings()
        self.assertEqual(settings["language"], "English")
        self.assertNotEqual((settings.get("permissions") or {}).get("defaultMode"), "bypassPermissions")
        self.assertIn(str(self.home).replace("\\", "/"), settings["statusLine"]["command"])

    def test_hooks_are_filtered_per_platform(self):
        manifest = json.loads((SHELL / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        for entry in manifest["hooks"]:
            script = re.search(r"\{\{CLAUDE_HOME\}\}/(\S+?)\"", entry["command"])
            if script:
                self.assertTrue((SHELL / script.group(1)).is_file(), script.group(1))
        for platform, expect_mac in (("darwin", True), ("linux", False), ("win32", False)):
            home = self.tmp / platform
            run(home, "--platform", platform)
            commands = " ".join(hook_commands(json.loads((home / "settings.json").read_text(encoding="utf-8"))))
            self.assertIn("guard-dangerous-bash.py", commands)
            self.assertIn("weekly-limit-guard.py", commands)
            self.assertIn("orchestration-doc.py", commands)
            for marker in MAC_ONLY:
                self.assertEqual(marker in commands, expect_mac, (platform, marker))
            self.assertEqual((home / "scripts" / "keep-awake.sh").exists(), platform != "win32")

    def test_user_files_are_skipped_or_backed_up(self):
        (self.home / "agents").mkdir(parents=True)
        mine = "my own builder\n"
        (self.home / "agents" / "builder.md").write_text(mine, encoding="utf-8")
        (self.home / "CLAUDE.md").write_text("my rules\n", encoding="utf-8")
        report = run(self.home, "--platform", "linux")
        self.assertIn("agents/builder.md", report["skipped"])
        self.assertIn("CLAUDE.md", report["skipped"])
        self.assertEqual((self.home / "agents" / "builder.md").read_text(encoding="utf-8"), mine)
        self.assertEqual((self.home / "CLAUDE.md").read_text(encoding="utf-8"), "my rules\n")

        report = run(self.home, "--platform", "linux", "--backup")
        backups = list((self.home / "agents").glob("builder.md.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), mine)
        self.assertEqual((self.home / "agents" / "builder.md").read_bytes(), (SHELL / "agents" / "builder.md").read_bytes())
        self.assertEqual(len(list(self.home.glob("CLAUDE.md.backup-*"))), 1)

    def test_skill_folder_changed_by_user_is_kept(self):
        skill = next(p for p in sorted((SHELL / "skills").iterdir()) if p.is_dir())
        target = self.home / "skills" / skill.name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("edited by the user\n", encoding="utf-8")
        report = run(self.home, "--platform", "linux")
        self.assertIn("skills/%s/" % skill.name, report["skipped"])
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "edited by the user\n")

    def test_settings_merge_keeps_user_values(self):
        self.home.mkdir(parents=True)
        user = {
            "language": "French",
            "enabledPlugins": {"figma@claude-plugins-official": False},
            "statusLine": {"type": "command", "command": "my-status"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-stop-hook"}]}]},
            "custom": {"keep": True},
        }
        (self.home / "settings.json").write_text(json.dumps(user), encoding="utf-8")
        run(self.home, "--platform", "linux")
        settings = self.settings()
        self.assertEqual(settings["language"], "French")
        self.assertFalse(settings["enabledPlugins"]["figma@claude-plugins-official"])
        self.assertTrue(settings["enabledPlugins"]["superpowers@claude-plugins-official"])
        self.assertEqual(settings["statusLine"]["command"], "my-status")
        self.assertEqual(settings["custom"], {"keep": True})
        self.assertIn("my-stop-hook", hook_commands(settings))
        backups = list(self.home.glob("settings.json.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), user)

    def test_second_run_changes_nothing(self):
        run(self.home, "--platform", "darwin")
        first = self.settings()
        report = run(self.home, "--platform", "darwin")
        self.assertEqual(report["installed"], [])
        self.assertEqual(report["settings"], [])
        self.assertEqual(self.settings(), first)
        commands = hook_commands(first)
        self.assertEqual(len(commands), len(set(commands)))

    def test_dry_run_writes_nothing(self):
        run(self.home, "--platform", "linux", "--dry-run")
        self.assertFalse(self.home.exists())

    def test_extra_skills_dir_for_codex(self):
        extra = self.tmp / "agents-skills"
        run(self.home, "--platform", "linux", "--extra-skills-dir", str(extra))
        self.assertTrue((extra / "impeccable" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
