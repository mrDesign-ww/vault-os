"""Adopting an upstream install that predates the stamp keeps every folder its setup owns (audit M7).

An upstream setup (gstack) creates folders next to its own: `<name>-*` skills, links into its checkout, a
`_<name>-command` folder and folders that carry its ownership marker `.<name>-owned`. A machine installed before the
stamp existed (the add-only route or an older release) must keep all of them when the pinned install is adopted.
Runs on a temporary HOME with a local fixture repository; no network.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upgrade_fixtures as F  # noqa: E402

SETUP = r'''#!/bin/sh
set -e
skills="$HOME/.claude/skills"
ln -s "$PWD/alpha" "$skills/suite-alpha"
mkdir -p "$skills/_suite-command" "$skills/helper"
printf -- '---\nname: suite\n---\n' > "$skills/_suite-command/SKILL.md"
: > "$skills/_suite-command/.suite-owned"
printf -- '---\nname: helper\n---\n' > "$skills/helper/SKILL.md"
: > "$skills/helper/.suite-owned"
'''


@unittest.skipUnless(not sys.platform.startswith("win"), "the setup fixture is a shell script")
class AdoptPreStampUpstreamTest(unittest.TestCase):
    def setUp(self):
        self.m = F.Machine()
        src = self.m.tmp / "src" / "suite"
        (src / "alpha").mkdir(parents=True)
        (src / "alpha" / "SKILL.md").write_text("---\nname: suite-alpha\n---\n", encoding="utf-8")
        (src / "setup").write_text(SETUP, encoding="utf-8")
        (src / "setup").chmod(0o755)
        F.git("init", "-q", cwd=src)
        F.git("add", "-A", cwd=src)
        F.git("commit", "-q", "-m", "one", cwd=src)
        commit = F.git("rev-parse", "HEAD", cwd=src).stdout.strip()
        self.manifest = self.m.tmp / "upstream.json"
        self.manifest.write_text(json.dumps({"skills": [
            {"name": "suite", "kind": "setup", "repo": str(src), "commit": commit, "requires": ["git"],
             "platforms": ["darwin", "linux"], "setup": ["./setup"]}]}), encoding="utf-8")
        # The pre-stamp install: the checkout at the pin, its setup run by hand, no stamp.
        skills = self.m.claude / "skills"
        skills.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(src), str(skills / "suite")], check=True, capture_output=True)
        subprocess.run(["./setup"], cwd=str(skills / "suite"), env=self.m.env(), check=True, capture_output=True)
        self.args = ["--owner", "Test Owner", "--title", "Test Vault", "--language", "English", "--client-label", "Client",
                     "--vault", str(self.m.vault), "--no-codex", "--upstream-skills",
                     "--upstream-manifest", str(self.manifest)]

    def tearDown(self):
        self.m.cleanup()

    def test_adopt_keeps_command_and_marked_folders(self):
        plan, _ = self.m.plan(*self.args)
        removes = {a["path"] for a in plan["actions"] if a["op"] == "remove"}
        for child in ("skills/_suite-command", "skills/helper", "skills/suite-alpha", "skills/suite"):
            self.assertNotIn(child, removes, child)
        self.assertEqual([a for a in plan["actions"] if a["op"] == "upstream"], [], "the pinned install is adopted")
        _, out = self.m.upgrade(*self.args)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        skills = self.m.claude / "skills"
        for child in ("_suite-command/SKILL.md", "_suite-command/.suite-owned", "helper/SKILL.md", "suite/setup"):
            self.assertTrue((skills / child).is_file(), child)
        self.assertTrue((skills / "suite-alpha").is_symlink())
        self.assertEqual(self.m.engine("parity", "--upstream-manifest", str(self.manifest)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
