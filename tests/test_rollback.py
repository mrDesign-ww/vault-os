"""Rollback after continued use (preview, the person's yes, --confirm) and the upgrade lock (installer/vault-os.py).

A rollback of an upgrade the person kept using shows a preview and changes nothing without --confirm. Then it keeps
the person's newer work: new notes in folders the upgrade made stay in place, commits made after the upgrade stay
reachable, and files the upgrade wrote that the person changed go to the holding folder <archive>/displaced/.
Every test runs on a temporary HOME.
"""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upgrade_fixtures as F  # noqa: E402
from test_upgrade import POSIX, changed, legacy_machine  # noqa: E402
from vaultos import engine, fsx, journal  # noqa: E402

MAPPED = ["--no-upstream-skills", "--map", "Secret=wiki/workspace/projects/Secret"]


def file_hashes(root):
    """{absolute path: sha256} of every regular file below root, links not followed."""
    out = {}
    for current, _, files in os.walk(str(root)):
        for name in files:
            path = os.path.join(current, name)
            if os.path.isfile(path) and not os.path.islink(path):
                out[path] = fsx.sha256_file(path)
    return out


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def git_out(vault, *args):
    return F.git(*args, cwd=vault).stdout.strip()


@unittest.skipUnless(POSIX and F.legacy_available(), "needs the previous release in the Git history and a POSIX shell")
class RollbackAfterUseTest(unittest.TestCase):
    def setUp(self):
        self.m = legacy_machine()
        self.args = ["--vault", str(self.m.vault)] + MAPPED
        self.head_before = git_out(self.m.vault, "rev-parse", "HEAD")
        self.before = self.m.snapshot()
        _, out = self.m.upgrade(*self.args)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.archive = F.archives(self.m)[-1]
        self.roots = {"claude": self.m.claude, "codex": self.m.codex, "agents": self.m.agents, "vault": self.m.vault}

    def tearDown(self):
        self.m.cleanup()

    def preview(self):
        out = self.m.engine("rollback", str(self.archive), "--json")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return json.loads(out.stdout)["rollback"]

    def everything(self):
        """The home and the archive, so a check can prove nothing was written."""
        return fsx.snapshot(str(self.m.home), exclude=("Vault/.git/objects",))

    def test_preview_then_confirm_keeps_the_persons_newer_work(self):
        m, vault = self.m, self.m.vault
        mapping = json.loads((self.archive / "migration" / "mapping.json").read_text(encoding="utf-8"))
        final = {src: dst for src, dst, _ in mapping["moves"]}
        made = os.path.dirname(final["Projects/Alpha/Spec.md"])
        self.assertIn(made, mapping["created_dirs"], "the upgrade made this folder")
        # Continued use: a new note in a folder the upgrade made, an edited rewritten note, a commit.
        new_note = vault / made / "new-after.md"
        new_note.write_text("# written after the upgrade\n", encoding="utf-8")
        rewritten = sorted(r for r in mapping["rewrites"] if r in F.FOREIGN_NOTES)[0]
        edited = vault / final.get(rewritten, rewritten)
        edited.write_text(edited.read_text(encoding="utf-8") + "\nmy edit after the upgrade\n", encoding="utf-8")
        edited_bytes = edited.read_bytes()
        F.git("add", "--", str(new_note.relative_to(vault)), str(edited.relative_to(vault)), cwd=vault)
        F.git("commit", "-q", "-m", "my work after the upgrade", cwd=vault)
        mine = git_out(vault, "rev-parse", "HEAD")

        # The preview lists what stays and what moves aside, and writes nothing.
        quiet = self.everything()
        text = m.engine("rollback", str(self.archive))
        self.assertEqual(text.returncode, 0, text.stdout + text.stderr)
        holding = str(self.archive / "displaced")
        self.assertIn(holding, text.stdout)
        p = self.preview()
        self.assertIn("--confirm %s" % p["id"], text.stdout)
        self.assertEqual(self.everything(), quiet, "the preview must not write")
        stays = {(s["layer"], s["path"]): s["reason"] for s in p["stays"]}
        moved = {(d["layer"], d["path"]): d["reason"] for d in p["displaced"]}
        self.assertIn(("vault", made), stays)
        self.assertIn("created after", stays[("vault", made)])
        self.assertIn(("vault", final.get(rewritten, rewritten)), moved)
        self.assertIn("changed after the upgrade", moved[("vault", final.get(rewritten, rewritten))])
        self.assertIn("history kept", stays[("vault", ".git")])
        self.assertEqual(p["git"], "kept")
        self.assertFalse([k for k in moved if k[1].startswith(".git")], "no Git metadata moves aside")
        self.assertIn(rewritten.split("/")[-1], text.stdout)

        # A wrong id is refused and changes nothing.
        wrong = m.engine("rollback", str(self.archive), "--confirm", "0" * 16)
        self.assertEqual(wrong.returncode, 3, wrong.stdout + wrong.stderr)
        self.assertEqual(self.everything(), quiet)

        live = file_hashes(m.home)
        live = {k: v for k, v in live.items() if not k.startswith(str(m.backups))}
        out = m.engine("rollback", str(self.archive), "--confirm", p["id"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

        # The new note and the commit are still in the vault; the edited note is in the holding folder.
        self.assertEqual(new_note.read_text(encoding="utf-8"), "# written after the upgrade\n")
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", mine, "HEAD"], cwd=str(vault)).returncode,
                         0, "the commit made after the upgrade is reachable from HEAD")
        held = [path for path, sha in file_hashes(self.archive / "displaced").items()
                if sha == fsx.sha256_bytes(edited_bytes)]
        self.assertEqual(len(held), 1, "the edited note is in the holding folder byte for byte")
        original = F.FOREIGN_NOTES[rewritten]
        original = original if isinstance(original, bytes) else original.encode("utf-8")
        self.assertEqual((vault / rewritten).read_bytes(), original, "the note is back at its old place")
        # Nothing was deleted: every file of the live tree is still somewhere, unless it was the upgrade's own.
        after = set(file_hashes(m.home).values())
        ours = [os.path.join(str(self.roots[a["layer"]]), *a["path"].split("/")) for a in p["actions"]
                if a["do"] == "restore" and a.get("live") == "upgrade"]
        lost = [path for path, sha in live.items() if sha not in after
                and not any(path == o or path.startswith(o + os.sep) for o in ours)]
        self.assertEqual(lost, [])
        self.assertEqual(m.engine("rollback", str(self.archive)).returncode, 2, "rolled back once only")

    def test_preview_id_follows_the_live_tree(self):
        m, vault = self.m, self.m.vault
        mapping = json.loads((self.archive / "migration" / "mapping.json").read_text(encoding="utf-8"))
        made = os.path.dirname({s: d for s, d, _ in mapping["moves"]}["Projects/Alpha/Spec.md"])
        first = self.preview()["id"]
        self.assertEqual(self.preview()["id"], first, "the same live tree gives the same id")
        (vault / made / "later.md").write_text("# later\n", encoding="utf-8")
        second = self.preview()["id"]
        self.assertNotEqual(first, second)
        quiet = self.everything()
        stale = m.engine("rollback", str(self.archive), "--confirm", first)
        self.assertEqual(stale.returncode, 3, stale.stdout + stale.stderr)
        self.assertEqual(self.everything(), quiet, "a stale id changes nothing")
        out = m.engine("rollback", str(self.archive), "--confirm", second)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual((vault / made / "later.md").read_text(encoding="utf-8"), "# later\n")

    def test_git_status_alone_restores_the_refs_and_displaces_only_the_index(self):
        m, vault = self.m, self.m.vault
        index = vault / ".git" / "index"
        settled = index.read_bytes()
        tracked = vault / git_out(vault, "ls-files").splitlines()[0]
        stamp = time.time() + 30
        os.utime(str(tracked), (stamp, stamp))
        subprocess.run(["git", "status", "--porcelain"], cwd=str(vault), capture_output=True, check=True)
        refreshed = index.read_bytes()
        self.assertNotEqual(refreshed, settled, "precondition: git status refreshed the index")
        p = self.preview()
        self.assertEqual(p["git"], "restored")
        self.assertEqual([(d["layer"], d["path"]) for d in p["displaced"]], [("vault", ".git/index")])
        self.assertIn("regenerable", p["displaced"][0]["reason"])
        out = m.engine("rollback", str(self.archive), "--confirm", p["id"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(git_out(vault, "rev-parse", "HEAD"), self.head_before)
        self.assertEqual(changed(self.before, m.snapshot()), [])
        held = sorted(str(p.relative_to(self.archive / "displaced")) for p in (self.archive / "displaced").rglob("*")
                      if p.is_file())
        self.assertEqual(held, ["vault/.git/index"])
        self.assertEqual((self.archive / "displaced" / "vault" / ".git" / "index").read_bytes(), refreshed)


class NestedKeyTest(unittest.TestCase):
    """A tool path inside a folder the upgrade placed (a host render inside an upstream checkout): the backup copies
    stay apart and the preview takes the same decisions as the rollback."""

    def test_rollback_right_after_is_exact_and_matches_the_preview(self):
        m = F.Machine()
        try:
            root = m.claude
            (root / "skills" / "suite").mkdir(parents=True)
            (root / "skills" / "suite" / "old.txt").write_text("the pre-upgrade copy\n", encoding="utf-8")
            src = m.tmp / "src"
            (src / ".agents").mkdir(parents=True)
            (src / "SKILL.md").write_text("---\nname: suite\n---\n", encoding="utf-8")
            (src / ".agents" / "render.txt").write_text("one\n", encoding="utf-8")
            before = fsx.snapshot(str(root))
            archive = journal.Archive.create(str(m.backups), {"claude": str(root)})
            archive.record("begin", plan_id="t", version="t", roots=archive.roots)
            archive.put_tree("claude", "skills/suite", str(src))
            archive.external("claude", "skills")
            archive.expect_tool_change("claude", "skills/suite/.agents")
            (root / "skills" / "suite" / ".agents" / "render.txt").write_text("two\n", encoding="utf-8")  # the tool
            archive.settle()
            archive.record("commit")
            outer_copy = Path(archive.path) / "files" / "claude" / "skills" / "suite"
            self.assertEqual(sorted(p.name for p in outer_copy.iterdir()), ["old.txt"], "copies stay apart")
            p = journal.preview(archive.path)
            self.assertEqual((p["displaced"], p["stays"]), ([], []))
            journal.rollback(archive.path, lambda *_: None)
            self.assertEqual(fsx.snapshot(str(root)), before)
            self.assertFalse((Path(archive.path) / "displaced").exists(), "the rollback did what the preview said")
        finally:
            m.cleanup()


class LockTest(unittest.TestCase):
    """recover and the upgrade respect a lock held by a live process; a dead or bad pid is a stale lock."""

    def setUp(self):
        self.m = F.Machine()
        self.m.backups.mkdir()
        self.lock = self.m.backups / engine.LOCK_NAME

    def tearDown(self):
        self.m.cleanup()

    def interrupted_archive(self):
        """An upgrade that made one folder and was killed: no commit record."""
        archive = journal.Archive.create(str(self.m.backups), {"vault": str(self.m.vault)})
        archive.record("begin", plan_id="t", version="t", roots=archive.roots)
        archive.mkdir("vault", "made-by-the-upgrade")
        return Path(archive.path)

    def hold(self, pid, archive=""):
        self.lock.write_text(json.dumps({"pid": pid, "archive": str(archive)}), encoding="utf-8")

    def test_recover_refuses_while_the_lock_holder_lives(self):
        archive = self.interrupted_archive()
        self.hold(os.getpid(), archive)
        held = self.lock.read_bytes()
        out = self.m.engine("recover")
        self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
        self.assertEqual(journal.status(str(archive)), "incomplete", "the live upgrade is not rolled back")
        self.assertTrue((self.m.vault / "made-by-the-upgrade").is_dir())
        self.assertEqual(self.lock.read_bytes(), held)

    def test_recover_clears_a_dead_or_bad_lock(self):
        for pid in (dead_pid(), "not-a-pid", None):
            with self.subTest(pid=pid):
                archive = self.interrupted_archive()
                self.hold(pid, archive)
                out = self.m.engine("recover")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertEqual(journal.status(str(archive)), "rolled_back")
                self.assertFalse((self.m.vault / "made-by-the-upgrade").exists())
                self.assertFalse(self.lock.exists())

    def test_only_the_holder_releases_the_lock(self):
        mine = engine.Lock(str(self.m.backups))
        mine.acquire("a")
        other = engine.Lock(str(self.m.backups))
        with self.assertRaises(engine.Stop) as refused:
            other.acquire("b")
        self.assertEqual(refused.exception.code, 4)
        other.release()
        self.assertTrue(self.lock.exists(), "a failed acquire must not release someone else's lock")
        mine.release()
        self.assertFalse(self.lock.exists())

    def test_upgrade_that_loses_the_race_keeps_the_other_lock(self):
        self.hold(os.getpid(), "the other upgrade")
        held = self.lock.read_bytes()
        homes = {"claude": str(self.m.claude), "codex": str(self.m.codex), "agents": str(self.m.agents),
                 "backups": str(self.m.backups)}
        plan = {"id": "t", "questions": [], "approval_needed": False, "actions": [{"layer": "claude"}],
                "migration": {"moves": [], "rewrites": {}}, "stamp": True}
        saved = engine.preflight, engine.prefetch
        engine.preflight = lambda *a, **k: None  # the other process took the lock after this one's preflight
        engine.prefetch = lambda *a, **k: {}
        try:
            with self.assertRaises(engine.Stop) as refused:
                engine.upgrade(str(F.ROOT), {"platform": "linux", "vault": str(self.m.vault)}, homes, None, {}, plan,
                               None, log=lambda *_: None)
        finally:
            engine.preflight, engine.prefetch = saved
        self.assertEqual(refused.exception.code, 4)
        self.assertEqual(self.lock.read_bytes(), held, "the other process's lock is untouched")
        self.assertEqual(sorted(p.name for p in self.m.backups.iterdir()), [engine.LOCK_NAME], "no empty archive left")


if __name__ == "__main__":
    unittest.main()
