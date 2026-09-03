import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RENDERER = REPO / "installer" / "render-vault.py"


def run(command, cwd=None):
    return subprocess.run(
        command, cwd=cwd, check=True, text=True, capture_output=True,
    ).stdout


@unittest.skipUnless(os.name == "posix", "hardened memory runtime is POSIX-only")
class RenderVaultTest(unittest.TestCase):
    def test_fresh_vault_trust_chain_and_retrieval(self):
        with tempfile.TemporaryDirectory(prefix="vault-os-test-") as directory:
            target = Path(directory) / "Example Vault"
            today = dt.date.today().isoformat()
            rendered = json.loads(run([
                sys.executable, str(RENDERER), "--target", str(target),
                "--owner", "Example O'Connor", "--title", "Example Owner's Vault",
                "--language", "English", "--client-label", "Client",
                "--date", today, "--approve-memory-policy",
            ]))
            self.assertEqual(rendered["zones"], ["work", "personal", "client"])
            self.assertEqual(len(rendered["reflection_policy_sha256"]), 64)
            self.assertFalse((target / "CLAUDE.template.md").exists())
            self.assertTrue((target / "CLAUDE.md").exists())

            for path in target.rglob("*"):
                if path.is_file():
                    try:
                        text = path.read_text()
                    except UnicodeDecodeError:
                        continue
                    self.assertNotIn("{{OWNER_NAME}}", text)
                    self.assertNotIn("{{VAULT_ID}}", text)

            run([sys.executable, "scripts/memory-model.py", "self-test"], target)
            run([sys.executable, ".vault-meta/evolution/reflection_policy.py"], target)
            run([
                sys.executable, "-c",
                "import importlib.util; p='.vault-meta/evolution/reflection_policy.py'; "
                "s=importlib.util.spec_from_file_location('rp',p); m=importlib.util.module_from_spec(s); "
                "s.loader.exec_module(m); m.load_policy()",
            ], target)

            run(["git", "init", "-q"], target)
            run(["git", "config", "user.name", "Vault OS Test"], target)
            run(["git", "config", "user.email", "test@example.invalid"], target)
            run(["git", "add", "."], target)
            run(["git", "commit", "-qm", "Initialize Vault OS"], target)
            self_tests = [
                ["scripts/memory-candidates.py", "self-test"],
                ["scripts/wiki-lock.py", "self-test"],
                [".vault-meta/evolution/claude-trace-hook.py", "--self-test"],
                [".vault-meta/evolution/fm-fix.py", "--self-test"],
                [".vault-meta/evolution/health-check.py", "--self-test"],
                [".vault-meta/evolution/lint-scan.py", "--self-test"],
                [".vault-meta/evolution/reflection-disposition.py", "--self-test"],
                [".vault-meta/evolution/reflection-evidence.py"],
                [".vault-meta/evolution/reflection-processed.py"],
                [".vault-meta/evolution/trace-manifest.py", "--self-test"],
                [".vault-meta/evolution/trace-scan.py", "--self-test"],
                [".vault-meta/evolution/trace-session.py", "--self-test"],
                [".vault-meta/evolution/update-last-run.py", "--self-test"],
            ]
            for command in self_tests:
                run([sys.executable] + command, target)
            for zone in ("work", "personal", "client"):
                lock_path = ".vault-meta/write/%s" % zone
                token = run(["bash", "scripts/wiki-lock.sh", "acquire", lock_path, "--ttl", "3600"], target).strip()
                try:
                    run([sys.executable, "scripts/memory-model.py", "build", "--zone", zone, "--lock-token", token], target)
                    run([sys.executable, "scripts/memory-model.py", "validate", "--zone", zone, "--lock-token", token], target)
                finally:
                    run(["bash", "scripts/wiki-lock.sh", "release", lock_path, token], target)
            run(["git", "add", ".vault-meta/memory-index"], target)
            run(["git", "commit", "-qm", "Bind memory index authority"], target)
            result = json.loads(run([
                sys.executable, "scripts/memory-model.py", "retrieve",
                "memory source of truth", "--zone", "work", "--loadout", "general",
            ], target))
            self.assertEqual(len(result["l3_loadout"]), 2)
            self.assertTrue(all(item["memory_trust"] == "approved" for item in result["l3_loadout"]))
            self.assertIn("Obsidian is the source of truth", json.dumps(result))

    def test_refuses_nonempty_target_and_implicit_approval(self):
        with tempfile.TemporaryDirectory(prefix="vault-os-test-") as directory:
            target = Path(directory) / "existing"
            target.mkdir()
            (target / "keep.txt").write_text("keep")
            base = [
                sys.executable, str(RENDERER), "--target", str(target),
                "--owner", "Example Owner", "--title", "Example Vault",
            ]
            refused = subprocess.run(base + ["--approve-memory-policy"], text=True, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual((target / "keep.txt").read_text(), "keep")
            empty = Path(directory) / "empty"
            implicit = subprocess.run(base[:3] + [str(empty)] + base[4:], text=True, capture_output=True)
            self.assertNotEqual(implicit.returncode, 0)
            self.assertFalse(empty.exists())


if __name__ == "__main__":
    unittest.main()
