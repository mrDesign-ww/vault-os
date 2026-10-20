"""The SessionStart check (shell/claude/hooks/vault-os-check.py): outdated, current, offline, no stamp, interrupted."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "shell" / "claude" / "hooks" / "vault-os-check.py"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="vault-os-check-test-")))
        self.home = self.tmp / ".claude"
        (self.home / "hooks").mkdir(parents=True)
        shutil.copy2(str(HOOK), str(self.home / "hooks" / HOOK.name))
        self.release = self.tmp / "release.json"
        self.url = "file://" + str(self.release)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def run_hook(self, payload=None, url=None, **env):
        e = dict(os.environ, VAULT_OS_CHECK_URL=url or self.url)
        e.pop("VAULT_OS_CHECK", None)
        e.update(env)
        out = subprocess.run([sys.executable, str(self.home / "hooks" / HOOK.name)],
                             input=json.dumps(payload if payload is not None else {"source": "startup"}),
                             capture_output=True, text=True, timeout=30, env=e)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr, "")
        if not out.stdout.strip():
            return ""
        data = json.loads(out.stdout)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
        return data["hookSpecificOutput"]["additionalContext"]

    def stamp(self, state="installed", version=VERSION):
        (self.home / "vault-os").mkdir(exist_ok=True)
        (self.home / "vault-os" / "installed.json").write_text(json.dumps({"state": state, "version": version}),
                                                               encoding="utf-8")

    def latest(self, tag):
        self.release.write_text(json.dumps({"tag_name": tag}), encoding="utf-8")
        cache = self.home / "vault-os" / "check-cache.json"
        if cache.exists():
            cache.unlink()

    def test_no_stamp_offers_the_update_skill(self):
        text = self.run_hook()
        self.assertIn("no install stamp", text)
        self.assertIn("vault-os-update", text)

    def test_interrupted_upgrade(self):
        self.stamp("upgrading")
        self.assertIn("interrupted", self.run_hook())

    def interrupted_with_lock(self, pid):
        backups = self.tmp / "vault-os-backups"
        archive = backups / "20261020-120000-ab12"
        archive.mkdir(parents=True)
        (self.home / "vault-os").mkdir(exist_ok=True)
        (self.home / "vault-os" / "installed.json").write_text(json.dumps(
            {"state": "upgrading", "version": VERSION, "archive": str(archive)}), encoding="utf-8")
        (backups / ".vault-os.lock").write_text(json.dumps({"pid": pid, "archive": str(archive)}), encoding="utf-8")

    def test_running_upgrade_is_not_reported_as_interrupted(self):
        self.interrupted_with_lock(os.getpid())  # this test process stands in for the live upgrade
        self.assertEqual(self.run_hook(), "")

    def test_dead_or_bad_lock_is_reported_as_interrupted(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        for pid in (proc.pid, "x"):
            with self.subTest(pid=pid):
                shutil.rmtree(str(self.tmp / "vault-os-backups"), ignore_errors=True)
                self.interrupted_with_lock(pid)
                self.assertIn("interrupted", self.run_hook())

    def test_offline_budget_is_about_two_seconds_in_total(self):
        import importlib.util
        import socket
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(8)  # accepts connections and never answers
        url = "http://127.0.0.1:%d/" % server.getsockname()[1]
        spec = importlib.util.spec_from_file_location("vault_os_check", str(HOOK))
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        try:
            start = time.time()
            self.assertIsNone(hook.fetch_latest((url, url + "VERSION")))
            self.assertLess(time.time() - start, 2.6)
        finally:
            server.close()

    def test_current_is_silent(self):
        self.stamp()
        self.latest("v" + VERSION)
        self.assertEqual(self.run_hook(), "")

    def test_outdated_names_both_versions_and_is_cached(self):
        self.stamp(version="2026.01.01")
        self.latest("v" + VERSION)
        text = self.run_hook()
        self.assertIn("2026.01.01 is installed and %s is out" % VERSION, text)
        self.release.write_text("not json and no version", encoding="utf-8")
        self.assertIn("is out", self.run_hook(), "the answer is cached for a day")
        cache = json.loads((self.home / "vault-os" / "check-cache.json").read_text(encoding="utf-8"))
        self.assertEqual(cache["latest"], VERSION)

    def test_plain_text_version_file(self):
        self.stamp(version="2026.01.01")
        self.release.write_text(VERSION + "\n", encoding="utf-8")
        self.assertIn("is out", self.run_hook())

    def test_offline_is_silent_and_fast(self):
        self.stamp(version="2026.01.01")
        start = time.time()
        self.assertEqual(self.run_hook(url="http://127.0.0.1:9/"), "")
        self.assertLess(time.time() - start, 10)
        cache = json.loads((self.home / "vault-os" / "check-cache.json").read_text(encoding="utf-8"))
        self.assertIsNone(cache["latest"])

    def test_subagents_and_off_are_silent(self):
        self.assertEqual(self.run_hook({"agent_id": "a1"}), "")
        self.assertEqual(self.run_hook({"transcript_path": "/x/subagents/agent-1.jsonl"}), "")
        self.assertEqual(self.run_hook(VAULT_OS_CHECK="off"), "")

    def test_bad_input_never_fails(self):
        self.stamp(state="installed", version="garbage")
        (self.home / "vault-os" / "check-cache.json").write_text("{broken", encoding="utf-8")
        out = subprocess.run([sys.executable, str(self.home / "hooks" / HOOK.name)], input="not json",
                             capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, VAULT_OS_CHECK_URL="http://127.0.0.1:9/"))
        self.assertEqual((out.returncode, out.stderr), (0, ""))

    def test_writes_only_its_cache(self):
        self.stamp()
        self.latest("v" + VERSION)
        before = sorted(p.relative_to(self.tmp).as_posix() for p in self.tmp.rglob("*"))
        self.run_hook()
        after = sorted(p.relative_to(self.tmp).as_posix() for p in self.tmp.rglob("*"))
        self.assertEqual(sorted(set(after) - set(before)), [".claude/vault-os/check-cache.json"])


class WiringTest(unittest.TestCase):
    def test_hooks_json_runs_it_at_startup(self):
        hooks = json.loads((ROOT / "shell" / "claude" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        found = []

        def walk(value):
            if isinstance(value, dict):
                if "vault-os-check.py" in str(value.get("command", "")):
                    found.append(value)
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)
        walk(hooks)
        self.assertEqual(len(found), 1)
        self.assertLessEqual(found[0].get("timeout", 60), 5)

    def test_installer_writes_the_hook_and_its_settings_entry(self):
        tmp = Path(tempfile.mkdtemp(prefix="vault-os-check-install-"))
        try:
            home = tmp / ".claude"
            out = subprocess.run([sys.executable, str(ROOT / "installer" / "install-claude-layer.py"), "--owner",
                                  "Test Owner", "--language", "English", "--claude-home", str(home), "--platform",
                                  "linux"], capture_output=True, text=True, timeout=300)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue((home / "hooks" / "vault-os-check.py").is_file())
            settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
            starts = [g for g in settings["hooks"]["SessionStart"]
                      if any("vault-os-check.py" in h.get("command", "") for h in g.get("hooks", []))]
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0].get("matcher"), "startup")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
