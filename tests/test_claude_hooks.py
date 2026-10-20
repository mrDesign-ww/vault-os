import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "shell" / "claude" / "hooks"
SELFTESTED = [
    HOOKS / "guard-dangerous-bash.py",
    HOOKS / "weekly-limit-guard.py",
    HOOKS / "image-size-gate.py",
    HOOKS / "orchestration-doc.py",
    HOOKS / "vault-os-check.py",
    ROOT / "shell" / "claude" / "statusline" / "statusline.py",
]


def py(script, *args, stdin="", env=None):
    return subprocess.run([sys.executable, str(script), *args], input=stdin, capture_output=True, text=True,
                          timeout=60, env=env)


class HookSelfTests(unittest.TestCase):
    def test_selftests_pass(self):
        for script in SELFTESTED:
            with self.subTest(script=script.name):
                out = py(script, "--selftest")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_guard_blocks_pattern_kill(self):
        out = py(HOOKS / "guard-dangerous-bash.py",
                 stdin=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pkill -f node"}}))
        self.assertEqual(out.returncode, 2)
        out = py(HOOKS / "guard-dangerous-bash.py",
                 stdin=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}))
        self.assertEqual(out.returncode, 0)

    def test_hooks_fail_open_on_bad_input(self):
        for name in ("guard-dangerous-bash.py", "weekly-limit-guard.py", "image-size-gate.py", "orchestration-doc.py"):
            with self.subTest(hook=name):
                self.assertEqual(py(HOOKS / name, stdin="not json").returncode, 0)

    @unittest.skipUnless(sys.platform == "darwin", "keep-awake uses caffeinate (macOS only)")
    def test_keep_awake_self_test(self):
        out = subprocess.run(["bash", str(ROOT / "shell" / "claude" / "scripts" / "keep-awake.sh"), "self-test"],
                             capture_output=True, text=True, timeout=120)
        self.assertIn("PASS", out.stdout, out.stdout + out.stderr)


class CompactSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-os-compact-"))
        self.env = dict(os.environ, COMPACT_SNAP_DIR=str(self.tmp / "snaps"))
        self.env.pop("CLAUDE_AGENT_ID", None)
        transcript = self.tmp / "session.jsonl"
        lines = [
            {"type": "user", "message": {"content": "Please keep the footer calm and finish the review."}},
            {"type": "user", "toolUseResult": {"status": "async_launched", "agentId": "a1b2c3",
                                               "description": "[Sonnet] Footer build"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/work/project/STATE.md"}}]}},
        ]
        transcript.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        self.event = {"session_id": "s1", "transcript_path": str(transcript), "trigger": "auto"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_pre_then_post_injects_snapshot(self):
        script = HOOKS / "compact-snapshot.py"
        self.assertEqual(py(script, "pre", stdin=json.dumps(self.event), env=self.env).returncode, 0)
        snap = (self.tmp / "snaps" / "s1.md").read_text(encoding="utf-8")
        self.assertIn("keep the footer calm", snap)
        self.assertIn("a1b2c3", snap)
        self.assertIn("STATE.md", snap)
        out = py(script, "post", stdin=json.dumps({"session_id": "s1", "source": "compact"}), env=self.env)
        context = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("If you are a subagent", context)
        self.assertIn("a1b2c3", context)

    def test_subagents_stay_silent(self):
        script = HOOKS / "compact-snapshot.py"
        sub = dict(self.event, agent_id="agent-9")
        self.assertEqual(py(script, "pre", stdin=json.dumps(sub), env=self.env).returncode, 0)
        self.assertFalse((self.tmp / "snaps").exists())
        out = py(script, "post", stdin=json.dumps({"session_id": "s1", "agent_type": "builder"}), env=self.env)
        self.assertEqual(out.stdout, "")

    def test_orchestration_doc_skips_subagents(self):
        out = py(HOOKS / "orchestration-doc.py", stdin=json.dumps({"agent_id": "x"}))
        self.assertEqual(out.stdout, "")


if __name__ == "__main__":
    unittest.main()
