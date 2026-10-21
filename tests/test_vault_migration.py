"""Vault migration rules (installer/vaultos/migrate.py) and its journal: moves only, links kept, exact undo."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))

from vaultos import engine, fsx, journal, migrate  # noqa: E402


def make_vault(root, files):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="vault-os-migration-")))
        self.vault = self.tmp / "vault"
        self.vault.mkdir()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def plan(self, files, reserved=(), maps=(), system=()):
        make_vault(self.vault, files)
        return migrate.plan(str(self.vault), set(system), set(reserved), maps)

    @staticmethod
    def moves(plan):
        return {src: dst for src, dst, _ in plan["moves"]}


class RulesTest(Base):
    def test_folder_buckets_numbering_and_unsorted(self):
        p = self.plan({
            "1. Areas/Health.md": "x", "📥 Inbox/todo.md": "x", "02 - Projects/Alpha/Plan.md": "x",
            "Archive/Old.md": "x", "people/Bob.md": "x", "attachments/a.png": b"png", "Random/Note.md": "x",
            "Top.md": "x", "Ресурсы/Книга.md": "x",
        })
        self.assertEqual(self.moves(p), {
            "1. Areas/Health.md": "wiki/areas/Health.md",
            "📥 Inbox/todo.md": "wiki/workspace/projects/inbox/todo.md",
            "02 - Projects/Alpha/Plan.md": "wiki/workspace/projects/Alpha/Plan.md",
            "Archive/Old.md": "wiki/archives/Old.md",
            "people/Bob.md": "wiki/resources/people/Bob.md",
            "attachments/a.png": "_attachments/a.png",
            "Random/Note.md": "wiki/workspace/projects/inbox/Random/Note.md",
            "Top.md": "wiki/workspace/projects/inbox/Top.md",
            "Ресурсы/Книга.md": "wiki/resources/Книга.md",
        })
        self.assertEqual(p["files"], 9)

    def test_frontmatter_decides_over_the_folder(self):
        p = self.plan({
            "Notes/day.md": "---\nzone: personal\n---\n# day\n",
            "Notes/old.md": "---\nstatus: archived\n---\n",
            "Notes/area.md": "---\npara: areas\n---\n",
            "Notes/tagged.md": "---\ntags:\n  - para/resources\n---\n",
            "Clients/Acme/Deal.md": "x",
        })
        self.assertEqual(self.moves(p), {
            "Notes/day.md": "wiki/personal/inbox/Notes/day.md",
            "Notes/old.md": "wiki/archives/Notes/old.md",
            "Notes/area.md": "wiki/areas/Notes/area.md",
            "Notes/tagged.md": "wiki/resources/Notes/tagged.md",
            "Clients/Acme/Deal.md": "wiki/client/inbox/Acme/Deal.md",
        })

    def test_system_layout_stays_and_system_paths_are_not_personal(self):
        p = self.plan({"wiki/workspace/projects/X/a.md": "x", "wiki/areas/b.md": "x", "wiki/index.md": "x",
                       "Reference/c.md": "x", "_attachments/d.png": b"d", "scripts/tool.py": "x",
                       ".obsidian/app.json": "{}", "CLAUDE.md": "system"}, system={"CLAUDE.md"})
        self.assertEqual(p["moves"], [])
        self.assertEqual(p["files"], 5)

    def test_collisions_get_a_suffix_and_links_follow(self):
        p = self.plan({
            "Notes/Plan.md": "# notes plan\n",
            "Projects/Alpha/Plan.md": "# alpha plan\n",
            "Projects/Alpha/Spec.md": "[[Plan]] and [[Plan|the plan]]\n",
            "Inbox/a.md": "inbox a\n", "a.md": "root a\n",
        }, reserved={"wiki/areas/Reserved.md"})
        moves = self.moves(p)
        self.assertEqual(moves["Notes/Plan.md"], "wiki/workspace/projects/inbox/Notes/Plan.md")
        self.assertEqual(moves["Projects/Alpha/Plan.md"], "wiki/workspace/projects/Alpha/Plan (2).md")
        self.assertEqual(sorted([moves["Inbox/a.md"], moves["a.md"]]),
                         ["wiki/workspace/projects/inbox/a (2).md", "wiki/workspace/projects/inbox/a.md"])
        edits = p["rewrites"]["Projects/Alpha/Spec.md"]
        text = migrate.apply_edits("[[Plan]] and [[Plan|the plan]]\n", edits)
        self.assertEqual(text, "[[Plan (2)|Plan]] and [[Plan (2)|the plan]]\n")
        self.assertIn(["Projects/Alpha/Plan.md", "wiki/workspace/projects/Alpha/Plan (2).md"], p["renamed"])

    def test_reserved_system_paths_are_never_a_target(self):
        p = self.plan({"Areas/index.md": "mine\n"}, reserved={"wiki/areas/index.md"})
        self.assertEqual(self.moves(p), {"Areas/index.md": "wiki/areas/index (2).md"})

    def test_code_is_not_a_link_and_md_links_and_canvas_follow(self):
        note = ("See [[Other]] and [md](../Notes/Other.md#Top) and ![[pic.png]].\n"
                "```\n[[Other]] in a fence\n```\nand `[[Other]]` inline\n")
        p = self.plan({
            "Areas/Health.md": note,
            "Notes/Other.md": "# other\n",
            "Notes/pic.png": b"png",
            "Notes/board.canvas": json.dumps({"nodes": [{"id": "1", "type": "file", "file": "Notes/Other.md"}]}),
        })
        moves = self.moves(p)
        text = migrate.apply_edits(note, p["rewrites"]["Areas/Health.md"])
        self.assertIn("[[Other]]", text.split("```")[0])  # a unique name keeps its short link
        self.assertIn("[md](../workspace/projects/inbox/Notes/Other.md#Top)", text)
        self.assertIn("```\n[[Other]] in a fence\n```", text)
        self.assertIn("`[[Other]]` inline", text)
        canvas = json.loads(migrate.apply_edits(
            (self.vault / "Notes/board.canvas").read_text(encoding="utf-8"), p["rewrites"]["Notes/board.canvas"]))
        self.assertEqual(canvas["nodes"][0]["file"], moves["Notes/Other.md"])

    def test_attachments_follow_a_note_moved_by_frontmatter(self):
        p = self.plan({"Journal/day.md": "---\nzone: personal\n---\n![[mood.png]]\n", "Journal/mood.png": b"m",
                       "Journal/shared.png": b"s", "Journal/other.md": "![[shared.png]]\n",
                       "Journal/day2.md": "---\nzone: personal\n---\n![[shared.png]]\n"})
        moves = self.moves(p)
        self.assertEqual(moves["Journal/mood.png"], "wiki/personal/inbox/Journal/mood.png")
        self.assertEqual(moves["Journal/shared.png"], "wiki/workspace/projects/inbox/Journal/shared.png",
                         "an attachment with two users stays with its folder")

    def test_zone_like_folder_is_blocked_until_mapped(self):
        files = {"Secret/_index.md": "# zone\n", "Secret/hot.md": "h\n", "Secret/p/Deal.md": "d\n"}
        p = self.plan(files)
        self.assertEqual(p["blocked"], {"Secret": 3})
        self.assertFalse(any(src.startswith("Secret/") for src, _, _ in p["moves"]))
        mapped = migrate.plan(str(self.vault), set(), set(), [("Secret", "wiki/workspace/projects/Secret")])
        self.assertEqual(mapped["blocked"], {})
        self.assertEqual(self.moves(mapped)["Secret/p/Deal.md"], "wiki/workspace/projects/Secret/p/Deal.md")

    def test_old_zone_roots_from_mode_json_are_blocked(self):
        files = {".vault-meta/mode.json": json.dumps({"zones": {"family": {"root": "wiki/family"}}}),
                 "wiki/family/note.md": "x\n"}
        p = self.plan(files)
        self.assertEqual(p["blocked"], {"wiki/family": 1})

    def test_plan_is_read_only(self):
        make_vault(self.vault, {"Projects/A/a.md": "[[b]]\n", "Notes/b.md": "b\n"})
        before = fsx.snapshot(str(self.vault))
        migrate.plan(str(self.vault), set(), set(), ())
        self.assertEqual(fsx.snapshot(str(self.vault)), before)


class ApplyUndoTest(Base):
    FILES = {
        "Projects/Alpha/Plan.md": "# Alpha\n[[Spec]] ![[d.png]] [[Plan#Goals|goals]]\n",
        "Projects/Alpha/Spec.md": "[[Projects/Alpha/Plan|plan]]\n```\n[[Plan]]\n```\n",
        "Projects/Alpha/d.png": b"\x89PNG",
        "Notes/Plan.md": "# other plan\n[[Spec]]\n",
        "1. Areas/Health.md": "[[Plan]] [md](../Notes/Plan.md)\n",
        "Journal/day.md": "---\nzone: personal\n---\n![[mood.png]]\n",
        "Journal/mood.png": b"mood",
        "wiki/workspace/projects/Kept.md": "[[Health]]\n",
        "Bad/latin1.md": b"caf\xe9 [[Health]]\n",
    }

    def apply(self):
        make_vault(self.vault, self.FILES)
        os.chmod(str(self.vault / "Projects"), 0o750)
        self.before = fsx.snapshot(str(self.vault))
        plan = migrate.plan(str(self.vault), set(), set(), ())
        files = migrate.scan(str(self.vault), set())
        ctx = types.SimpleNamespace(migration=plan, migration_before=migrate.fingerprint(str(self.vault), files),
                                    migration_links=migrate.link_targets(str(self.vault), files))
        self.links = ctx.migration_links
        archive = journal.Archive.create(str(self.tmp / "backups"), {"vault": str(self.vault)})
        archive.record("begin", plan_id="t", version="t", roots=archive.roots)
        mapping = engine.apply_migration(archive, ctx, str(self.vault), lambda *_: None)
        archive.record("commit")
        return plan, mapping, archive

    def test_apply_keeps_counts_hashes_and_links(self):
        plan, mapping, archive = self.apply()
        self.assertEqual(sum(sum(t.values()) for t in self.links.values()), 9)
        self.assertEqual(migrate.verify(str(self.vault), mapping["before"], plan, self.links), [])
        spec = (self.vault / self.moves(plan)["Projects/Alpha/Spec.md"]).read_text(encoding="utf-8")
        self.assertIn("```\n[[Plan]]\n```", spec)
        after = migrate.scan(str(self.vault), set())
        self.assertEqual(len(after), len(self.FILES))
        self.assertEqual(sorted(after), sorted(mapping["after"]))
        self.assertFalse((self.vault / "Projects").exists())
        self.assertIn("Projects/Alpha", mapping["removed_dirs"])
        latin = (self.vault / self.moves(plan)["Bad/latin1.md"]).read_bytes()
        self.assertTrue(latin.startswith(b"caf\xe9 [["), "undecodable bytes survive a link rewrite")
        self.assertTrue((Path(archive.path) / "migration" / "undo.py").is_file())

    def test_journal_rollback_restores_the_exact_tree(self):
        _, _, archive = self.apply()
        journal.rollback(archive.path, lambda *_: None)
        self.assertEqual(fsx.snapshot(str(self.vault)), self.before)

    def test_rollback_survives_a_torn_journal_line(self):
        _, _, archive = self.apply()
        with open(os.path.join(archive.path, "journal.jsonl"), "a", encoding="utf-8") as handle:
            handle.write('{"op": "wri')
        journal.rollback(archive.path, lambda *_: None)
        self.assertEqual(fsx.snapshot(str(self.vault)), self.before)

    def test_undo_script_moves_everything_back(self):
        _, _, archive = self.apply()
        undo = os.path.join(archive.path, "migration", "undo.py")
        moved = fsx.snapshot(str(self.vault))
        dry = subprocess.run([sys.executable, "-I", undo, "--dry-run"], capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(fsx.snapshot(str(self.vault)), moved)
        out = subprocess.run([sys.executable, "-I", undo], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        now = fsx.snapshot(str(self.vault))
        files = {k: v for k, v in now.items() if not k.endswith("/")}
        self.assertEqual(files, {k: v for k, v in self.before.items() if not k.endswith("/")})
        self.assertEqual(sorted(k for k in now if k.endswith("/")), sorted(k for k in self.before if k.endswith("/")))

    def test_a_note_changed_after_the_plan_stops_the_migration(self):
        make_vault(self.vault, self.FILES)
        plan = migrate.plan(str(self.vault), set(), set(), ())
        files = migrate.scan(str(self.vault), set())
        ctx = types.SimpleNamespace(migration=plan, migration_before=migrate.fingerprint(str(self.vault), files),
                                    migration_links={})
        (self.vault / "Notes" / "Plan.md").write_text("edited\n", encoding="utf-8")
        archive = journal.Archive.create(str(self.tmp / "backups"), {"vault": str(self.vault)})
        with self.assertRaises(RuntimeError):
            engine.apply_migration(archive, ctx, str(self.vault), lambda *_: None)
        self.assertTrue((self.vault / "Notes" / "Plan.md").is_file())


class MigrationSafetyTest(Base):
    """Fix round 1 (FINAL-AUDIT B1, M1, M4, O2, MINOR 2, 3): meta names, symlinks, nested repositories, inboxes."""

    RESERVED = {"wiki/index.md", "wiki/hot.md", "wiki/overview.md", "wiki/dashboard.md", "wiki/log.md",
                "wiki/personal/hot.md", "wiki/personal/_index.md", "Reference/_index.md"}

    def test_meta_names_get_a_suffix_except_repeatable_ones(self):
        plan = self.plan({"Notes/index.md": "# i\n", "Notes/hot.md": "# h\n", "Notes/overview.md": "# o\n",
                          "Notes/log.md": "# l\n", "Notes/readme.md": "# r\n", "Topics/_index.md": "# x\n",
                          "Home.md": "[[index]] [[hot]]\n"}, reserved=self.RESERVED)
        moves = self.moves(plan)
        for name in ("index", "hot", "overview"):
            self.assertTrue(moves["Notes/%s.md" % name].endswith("/Notes/%s (2).md" % name), moves)
        self.assertTrue(moves["Topics/_index.md"].endswith("/Topics/_index.md"), moves)
        for name in ("log", "readme"):
            self.assertTrue(moves["Notes/%s.md" % name].endswith("/Notes/%s.md" % name), moves)
        self.assertIn("Home.md", plan["rewrites"])

    def test_symlinks_are_kept_in_place_and_listed(self):
        make_vault(self.vault, {"Notes/real.md": "# real\n", "Home.md": "[[alias]]\n"})
        os.symlink("real.md", str(self.vault / "Notes" / "alias.md"))
        os.symlink("missing.md", str(self.vault / "Notes" / "broken.md"))
        os.symlink("Notes", str(self.vault / "Linked"))
        plan = migrate.plan(str(self.vault), set(), set(), ())
        kept = dict(plan["kept"])
        self.assertEqual(kept, {"Notes/alias.md": "symbolic link", "Notes/broken.md": "symbolic link",
                                "Linked": "symbolic link"})
        moved = {src for src, _, _ in plan["moves"]}
        self.assertFalse(moved & set(kept))
        self.assertIn("Notes/real.md", moved)
        self.assertTrue(any("Notes/alias.md" in w for w in plan["warnings"]), plan["warnings"])

    def test_a_folder_with_its_own_git_repository_is_one_unit(self):
        make_vault(self.vault, {"Code/proj/README.md": "# proj\nsee [plan](../../Notes/Plan.md)\n", "Code/proj/main.py": "x\n",
                                "Notes/Plan.md": "# plan\n[[README]]\n"})
        subprocess.run(["git", "init", "-q"], cwd=str(self.vault / "Code" / "proj"), check=True)
        detail = {}
        plan = migrate.plan(str(self.vault), set(), set(), (), detail=detail)
        self.assertIn(["Code/proj", "Git repository"], plan["kept"])
        self.assertFalse([m for m in plan["moves"] if m[0].startswith("Code/proj/")])
        self.assertNotIn("Code/proj/README.md", plan["rewrites"])
        self.assertIn("Code/proj/README.md", detail["kept_paths"])
        self.assertTrue(any("Code/proj/README.md" in w and "Git repository" in w for w in plan["warnings"]),
                        plan["warnings"])
        self.assertNotIn("Notes/Plan.md", plan["rewrites"], "a link into the kept repository still resolves")

    def test_personal_file_at_a_system_path_moves_below_the_inbox(self):
        files = {"Reference/_index.md": "# mine\n[[Obsidian Plugins]]\n", "Reference/Obsidian Plugins.md": "# my\n",
                 "Home.md": "[[Obsidian Plugins]] [[Reference/_index]]\n"}
        make_vault(self.vault, files)
        system = {"Reference/_index.md", "Reference/Obsidian Plugins.md"}
        displaced = sorted(system)
        plan = migrate.plan(str(self.vault), set(), system, (), displaced, migrate.PERSONAL_INBOX)
        moves = {src: (dst, why) for src, dst, why in plan["moves"]}
        self.assertEqual(moves["Reference/_index.md"],
                         ("wiki/personal/inbox/Reference/_index.md", migrate.SYSTEM_PATH_REASON))
        self.assertEqual(moves["Reference/Obsidian Plugins.md"],
                         ("wiki/personal/inbox/Reference/Obsidian Plugins (2).md", migrate.SYSTEM_PATH_REASON))
        self.assertIn("Home.md", plan["rewrites"])

    def test_unsorted_notes_use_the_given_inbox_and_frontmatter_never_widens_a_zone(self):
        files = {"Loose.md": "# loose\n", "Personal/Day.md": "---\nzone: work\n---\n# day\n",
                 "Topic/Work note.md": "---\nzone: work\n---\n# w\n"}
        plan = self.plan(files)
        make_vault(self.vault, files)
        moves = self.moves(migrate.plan(str(self.vault), set(), set(), (), (), migrate.PERSONAL_INBOX))
        self.assertEqual(moves["Loose.md"], "wiki/personal/inbox/Loose.md")
        self.assertTrue(moves["Personal/Day.md"].startswith("wiki/personal/"), moves)
        self.assertFalse(moves["Topic/Work note.md"].startswith("wiki/personal/"), moves)
        self.assertEqual(self.moves(plan)["Loose.md"], migrate.INBOX + "/Loose.md")

    def test_any_case_of_scripts_is_the_system_folder_on_a_case_insensitive_volume(self):
        make_vault(self.vault, {"Scripts/Screenplay.md": "# s\n", "Other.md": "# o\n"})
        if not migrate.case_insensitive(str(self.vault)):
            self.skipTest("the temporary folder is on a case-sensitive volume")
        self.assertEqual(migrate.scan(str(self.vault), set(), fold_case=True), ["Other.md"])
        self.assertIn("Scripts/Screenplay.md", migrate.scan(str(self.vault), set()))


if __name__ == "__main__":
    unittest.main()
