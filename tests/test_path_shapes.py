"""Path-shape matrix (UPGRADE.md 5.1 step 0, 2.2): every vault system path the plan touches, times every shape.

The paths come from the plan's own list: a dry run of a baseline foreign vault gives every vault path it writes,
splits, seeds or moves to, every folder above one, and the folders the vault tools write into. One representative
file path is taken per (folder, operation). Each path then gets each shape (absent, folder, file, link to a file,
link to a folder, dangling link, link chain) in a fresh foreign vault with Git, on a temporary HOME. For every case:

- the dry run exits 0 or 3 (a question), never 8 or anything else, and writes nothing;
- a question is shown in the dry run, refuses --confirm and runs nothing; outside the zones (below wiki/, no dot
  name on the way) a shape never needs a question, and inside one a wrong shape is always a question naming it;
  a move destination expects nothing there, and anything there makes the move pick a free name (" (n)");
- exit 0 implies --confirm exits 0 with "upgrade complete: parity 0";
- every original file and link is byte-identical somewhere in the vault, except what a rule of 2.1 replaces by
  design (a file at a config path, anything inside an owned folder: then its archive copy is byte-identical; a
  mixed file keeps the person's key or line);
- link targets outside the vault are untouched, parity is 0, a second plan is a no-op;
- a forced rollback of the archive returns the exact tree.

HousekeepingTest runs a sample of the matrix while the person's Git starts housekeeping after every commit (CI run
39506112345: a detached repack wrote .git/info/refs inside a dry run and after a rollback).
"""
import collections
import concurrent.futures
import hashlib
import json
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upgrade_fixtures as F  # noqa: E402
from vaultos import engine, fsx, journal, manifest, migrate  # noqa: E402

ANSWERS = ["--owner", "Test Owner", "--title", "Test Vault", "--language", "English", "--client-label", "Client",
           "--no-codex", "--no-upstream-skills"]
SHAPES = ("absent", "folder", "file", "link to file", "link to folder", "dangling link", "link chain")
BASE = {  # Home.md sorts before wiki (SPOT-CHECK-2 MAJOR-1); the others land below the inbox, wiki/areas, _attachments
    "Home.md": "# home\n",
    "Notes/idea.md": "# idea\n",
    "Areas/Health.md": "# health\n",
    "attachments/pic.png": b"\x89PNG pic",
}
OUTSIDE = {"target-file.txt": "outside file\n", "target-dir/inside.txt": "outside folder\n"}
POSIX = not sys.platform.startswith("win")
TOOL_DIRS = [p for p in engine.VAULT_TOOL_PATHS if "." not in posixpath.basename(p)]  # folders the vault tools write
# The person's Git config in the temporary HOME: automatic housekeeping after every commit, in the foreground. Git
# 2.50 to 2.53 run the gc task (gc.auto=1: two loose objects in objects/17 pass); 2.54 and later run the geometric
# repack, and 2.56 raised its threshold to 6700, so a negative auto value forces it on every run.
HOUSEKEEPING = ("[gc]\n\tauto = 1\n\tautoDetach = false\n[maintenance]\n\tauto = true\n\tautoDetach = false\n"
                "[maintenance \"geometric-repack\"]\n\tauto = -1\n")


def changed_paths(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def jobs():
    value = os.environ.get("VAULT_OS_MATRIX_JOBS", "")
    return int(value) if value.isdigit() and int(value) > 0 else max(1, min(8, (os.cpu_count() or 2) - 1))


def write_tree(root, files):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def personal_bytes(rel):
    if rel.endswith(".json"):
        return b'{"mine": true}\n'
    if rel.endswith(".md"):
        return b"# mine\n"
    return b"mine\n"


def make_shape(m, rel, shape, kind):
    path = m.vault / rel
    outside = m.home / "outside"
    if shape == "absent":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if shape == "folder":
        path.mkdir()
        (path / "mine.txt").write_bytes(b"mine inside\n")
    elif shape == "file":
        path.write_bytes(personal_bytes(rel))
    elif shape == "link to file":
        os.symlink(str(outside / "target-file.txt"), str(path))
    elif shape == "link to folder":
        os.symlink(str(outside / "target-dir"), str(path))
    elif shape == "dangling link":
        os.symlink(str(outside / "missing"), str(path))
    else:  # a link to a link: the hop names the kind the system expects there
        os.symlink(str(outside / ("hop-dir" if kind == "folder" else "hop-file")), str(path))


def force_housekeeping(m):
    """Every later commit under m's HOME runs Git housekeeping: the config above, and two loose objects in objects/17,
    the shard Git samples to estimate the loose objects (gc.auto=1 passes at two). The objects are unreachable blobs;
    the snapshots exclude the object store. housekeeping_runs(m) proves it for this Git and this vault."""
    (m.home / ".gitconfig").write_text(HOUSEKEEPING, encoding="utf-8")
    planted, i = 0, 0
    while planted < 2:
        body = b"housekeeping trigger %d\n" % i
        i += 1
        if hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest().startswith("17"):
            out = subprocess.run(["git", *F.GIT_QUIET, "hash-object", "-w", "--stdin"], input=body, cwd=str(m.vault),
                                 check=True, capture_output=True)
            if not out.stdout.startswith(b"17"):
                raise AssertionError("the fixture vault does not use SHA-1 object names")
            planted += 1


def housekeeping_runs(m):
    """The person commits in m's vault under m's HOME: True when that commit's housekeeping wrote .git/info/refs with
    the new HEAD (a repack runs update-server-info)."""
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
                          "--allow-empty", "-m", "mine"], cwd=str(m.vault), env=m.env(), capture_output=True,
                         text=True)
    if out.returncode:
        raise AssertionError("the person's commit failed: " + out.stderr)
    head = F.git("rev-parse", "HEAD", cwd=m.vault).stdout.strip()
    refs = m.vault / ".git" / "info" / "refs"
    return refs.is_file() and head in refs.read_text(encoding="utf-8")


def machine(rel=None, shape="absent", kind="file", housekeeping=False):
    m = F.Machine()
    write_tree(m.home / "outside", OUTSIDE)
    os.symlink(str(m.home / "outside" / "target-dir"), str(m.home / "outside" / "hop-dir"))
    os.symlink(str(m.home / "outside" / "target-file.txt"), str(m.home / "outside" / "hop-file"))
    write_tree(m.vault, BASE)
    if rel:
        make_shape(m, rel, shape, kind)
    F.git("init", "-q", cwd=m.vault)
    F.git("add", "-A", cwd=m.vault)
    F.git("commit", "-qm", "my vault", cwd=m.vault)
    if housekeeping:
        force_housekeeping(m)
    return m


def args_of(m):
    return ANSWERS + ["--vault", str(m.vault)]


def identity(path):
    if os.path.islink(path):
        return ("link", os.readlink(path))
    return ("file", fsx.sha256_file(path))


def vault_entries(vault):
    """{rel: identity} of every file and link in the vault (links not followed, .git excluded)."""
    out = {}
    for current, dirs, files in os.walk(str(vault), followlinks=False):
        rel_dir = os.path.relpath(current, str(vault)).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        if rel_dir == "":
            dirs[:] = [d for d in dirs if d != ".git"]
        for d in list(dirs):
            if os.path.islink(os.path.join(current, d)):
                out[rel_dir + d] = identity(os.path.join(current, d))
                dirs.remove(d)
        for name in files:
            out[rel_dir + name] = identity(os.path.join(current, name))
    return out


def archived(m, rel):
    """Identity of rel's copy in the newest archive (a backup of rel or of a folder above it), or None."""
    archive = F.archives(m)[-1]
    for record in journal.read_journal(str(archive / "journal.jsonl")):
        if record["op"] != "backup" or record["layer"] != "vault" or not record.get("copy"):
            continue
        base = record["path"]
        if rel == base or rel.startswith(base + "/"):
            copy = os.path.join(record["copy"], *rel[len(base):].strip("/").split("/")) if rel != base else \
                record["copy"]
            if os.path.lexists(copy):
                return identity(copy)
    return None


def inside(rel, folder):
    return rel == folder or rel.startswith(folder + "/")


class Matrix:
    def __init__(self):
        m = machine()
        try:
            plan, rc = m.plan(*args_of(m))
        finally:
            m.cleanup()
        if rc != 0:
            raise AssertionError("the baseline dry run has questions: %s" % plan["questions"])
        vault_acts = sorted((a["path"], a["op"]) for a in plan["actions"] if a["layer"] == "vault")
        targets = vault_acts + sorted((dst, "move") for _, dst, _ in plan["migration"]["moves"])
        self.ops = dict(vault_acts)
        self.dests = {dst for _, dst, _ in plan["migration"]["moves"]} - set(self.ops)
        self.owned = sorted({posixpath.dirname(p) for p, op in vault_acts if op == "tree"})
        folders, files = set(TOOL_DIRS), {}
        for rel, op in targets:
            parts = rel.split("/")
            folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
            files.setdefault((posixpath.dirname(rel), op), rel)
        self.paths = [(p, "folder") for p in sorted(folders)] + \
            [(p, "folder" if self.ops.get(p) == "tree" else "file") for p in sorted(set(files.values()))]
        self.cases = [(None, "absent", "file")]  # every path absent: the baseline vault itself
        for rel, kind in self.paths:
            self.cases += [(rel, shape, kind) for shape in SHAPES[1:]]

    def interior(self, rel):
        return any(rel.startswith(d + "/") for d in self.owned)

    def mismatch(self, rel, kind, shape):
        if rel in self.dests:  # a move destination expects nothing: anything there makes the move pick a free name
            return False
        return shape not in ("absent", kind)

    def run(self, rel, shape, kind, housekeeping=False):
        """[problems] of one case and its outcome ("question" or "upgraded")."""
        m = machine(rel, shape, kind, housekeeping)
        problems = []
        try:
            outcome = self._run(m, rel, shape, kind, problems)
            if housekeeping and not housekeeping_runs(m):  # the same trigger fires in this vault, after the checks
                problems.append("the forced housekeeping does not run in this vault")
            return outcome, problems
        except Exception as error:  # noqa: BLE001 - one case's crash is that case's finding
            problems.append("crashed: %r" % error)
            return "crashed", problems
        finally:
            m.cleanup()

    def _run(self, m, rel, shape, kind, problems):
        args = args_of(m)
        zone = rel is not None and engine.in_zone(rel)
        wrong = rel is not None and self.mismatch(rel, kind, shape) and not self.interior(rel)
        before = m.snapshot()
        outside_before = fsx.snapshot(str(m.home / "outside"))
        originals = vault_entries(m.vault)
        dry = m.engine("upgrade", *args, "--json")
        if m.snapshot() != before:
            problems.append("the dry run wrote")
        if dry.returncode not in (0, 3):
            problems.append("dry run exit %d: %s" % (dry.returncode, (dry.stdout + dry.stderr)[-400:]))
            return "failed"
        plan = json.loads(dry.stdout)["plan"] if dry.stdout.strip().startswith("{") else None
        if dry.returncode == 3:
            questions = plan["questions"] if plan else [dry.stderr.strip()]
            if not [q for q in questions if q]:
                problems.append("exit 3 without a question")
            if not zone:
                problems.append("a question outside the zones: %s" % questions)
            if wrong and not [q for q in questions if rel in q]:
                problems.append("no question names %s: %s" % (rel, questions))
            if plan:
                out = m.engine("upgrade", *args, "--confirm", plan["id"], "--approve-memory-policy")
                if out.returncode != 3:
                    problems.append("--confirm of a plan with questions exit %d" % out.returncode)
            if m.snapshot() != before:
                problems.append("a plan with questions changed the machine")
            return "question"
        if wrong and zone:
            problems.append("a wrong shape inside a zone gave no question")
        if rel in self.dests and shape != "absent" and [m for m in plan["migration"]["moves"] if m[1] == rel]:
            problems.append("a move lands on the existing %s at %s" % (shape, rel))
        extra = ["--approve-memory-policy"] if plan["approval_needed"] else []
        out = m.engine("upgrade", *args, "--confirm", plan["id"], *extra)
        if out.returncode != 0 or "upgrade complete: parity 0" not in out.stdout:
            problems.append("dry run exit 0 but --confirm exit %d: %s" % (out.returncode, (out.stdout + out.stderr)
                                                                       [-600:]))
            if m.snapshot() != before:
                problems.append("the failed upgrade did not roll back exactly: %s"
                                % changed_paths(before, m.snapshot())[:6])
            return "failed"
        self.check_originals(m, plan, rel, shape, kind, originals, problems)
        if fsx.snapshot(str(m.home / "outside")) != outside_before:
            problems.append("a link target outside the vault changed")
        parity = m.engine("parity", "--json")
        if parity.returncode != 0 or json.loads(parity.stdout)["differences"] != 0:
            problems.append("parity: %s" % parity.stdout[-300:])
        installed = m.snapshot()
        again, rc = m.plan(*args)
        if (rc, again["actions"], again["migration"]["moves"], again["migration"]["rewrites"]) != (0, [], [], {}):
            problems.append("second plan not a no-op: rc %d, %s, %s" % (rc, again["actions"][:3],
                                                                       again["migration"]["moves"][:3]))
        if m.snapshot() != installed:
            problems.append("the second plan wrote")
        rolled = m.rollback(F.archives(m)[-1])
        if rolled.returncode != 0:
            problems.append("rollback exit %d: %s" % (rolled.returncode, (rolled.stdout + rolled.stderr)[-300:]))
        elif m.snapshot() != before:
            problems.append("rollback not exact: %s" % changed_paths(before, m.snapshot())[:6])
        return "upgraded"

    def check_originals(self, m, plan, rel, shape, kind, originals, problems):
        after = collections.Counter(vault_entries(m.vault).values())
        for path, ident in sorted(originals.items()):
            mine = rel is not None and inside(path, rel)
            if mine and (self.interior(rel) or (shape == "folder" and rel in self.owned)):
                if archived(m, path) != ident:  # an owned folder holds exactly the system's children (2.1)
                    problems.append("%s inside an owned folder is not in the archive" % path)
                continue
            if mine and shape == kind == "file" and path == rel:
                cls = manifest.vault_path_class(rel) if self.ops.get(rel) in ("write", "split") else "note"
                if cls == "config":  # a config path is archived and replaced (2.1)
                    if archived(m, path) != ident:
                        problems.append("%s at a config path is not in the archive" % path)
                    continue
                if cls == "mixed":  # merged: the person's key or line stays
                    text = (m.vault / rel).read_text(encoding="utf-8")
                    if ('"mine": true' if rel.endswith(".json") else "mine") not in text:
                        problems.append("%s lost the person's part in the merge" % path)
                    continue
            if after[ident] < 1:
                problems.append("original %s is not byte-identical anywhere in the vault" % path)
            else:
                after[ident] -= 1


@unittest.skipUnless(POSIX, "symbolic links")
class PhaseOrderTest(unittest.TestCase):
    """SPOT-CHECK-2 MAJOR-1 and MINOR 1 at the unit level: phase 0 renames come first in the plan, the shape check
    refuses a move below a link that is renamed only later, and a link whose folder target moves gets a warning."""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="vault-os-shapes-")))
        self.vault = self.tmp / "vault"
        write_tree(self.tmp / "outside", {"wiki/x.md": "# x\n"})
        write_tree(self.vault, {"Home.md": "# home\n", "Notes/shared-ref/Books.md": "# books\n"})
        os.symlink(str(self.tmp / "outside" / "wiki"), str(self.vault / "wiki"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def test_phase_0_renames_come_first_and_the_shape_check_holds_the_order(self):
        plan = migrate.plan(str(self.vault), set(), set(), (), (), migrate.PERSONAL_INBOX,
                            links={"wiki": migrate.LINK_REASON})
        self.assertEqual(plan["moves"][0], ["wiki", "wiki (2)", migrate.LINK_REASON])
        self.assertIn(["Home.md", "wiki/personal/inbox/Home.md", "unsorted"], plan["moves"])
        ctx = types.SimpleNamespace(layers={"vault": manifest.Layer("vault", str(self.vault))})
        full = {"actions": [], "migration": plan}
        self.assertEqual(engine.plan_shape_errors(full, ctx, {"vault": str(self.vault)}, False), [])
        wrong = dict(plan, moves=sorted(plan["moves"]))  # by source path: Home.md before wiki (the round 2 order)
        problems = engine.plan_shape_errors({"actions": [], "migration": wrong}, ctx, {"vault": str(self.vault)},
                                            False)
        self.assertTrue([p for p in problems if "Home.md" in p and "wiki (link to folder)" in p], problems)
        below = {"actions": [{"layer": "vault", "op": "write", "path": "wiki (2)/x.md"}], "migration": plan}
        self.assertTrue(engine.plan_shape_errors(below, ctx, {"vault": str(self.vault)}, False))

    def test_a_link_whose_folder_target_moves_gets_a_warning(self):
        os.symlink("Notes/shared-ref", str(self.vault / "Reference"))
        os.symlink("Notes/shared-ref", str(self.vault / "Shelf"))  # a personal link: kept, same warning
        plan = migrate.plan(str(self.vault), set(), set(), (), (), migrate.PERSONAL_INBOX,
                            links={"Reference": migrate.LINK_REASON, "wiki": migrate.LINK_REASON})
        moves = {src: dst for src, dst, _ in plan["moves"]}
        self.assertEqual(moves["Reference"], "Reference (2)")
        self.assertIn("Notes/shared-ref/Books.md", moves)
        for name in ("Reference (now Reference (2))", "Shelf"):
            self.assertTrue([w for w in plan["warnings"] if name in w and "folder Notes/shared-ref" in w],
                            (name, plan["warnings"]))


@unittest.skipUnless(POSIX, "the upgrade runs on POSIX")
class PathShapeMatrixTest(unittest.TestCase):
    def test_every_system_path_times_every_shape(self):
        matrix = Matrix()
        self.assertGreater(len(matrix.paths), 40, "the plan lists fewer system paths than expected")
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs()) as pool:
            futures = {pool.submit(matrix.run, *case): case for case in matrix.cases}
            for future in concurrent.futures.as_completed(futures):
                results[futures[future]] = future.result()
        outcomes = collections.Counter(outcome for outcome, _ in results.values())
        failed = sorted(("%s as %s" % (rel, shape), problems) for (rel, shape, _), (_, problems) in results.items()
                        if problems)
        sys.stderr.write("\npath-shape matrix: %d cases (%d paths x %d shapes + the baseline): %s\n"
                         % (len(matrix.cases), len(matrix.paths), len(SHAPES) - 1, dict(outcomes)))
        self.assertEqual(failed, [], "%d of %d cases failed" % (len(failed), len(matrix.cases)))


@unittest.skipUnless(POSIX, "the upgrade runs on POSIX")
class HousekeepingTest(unittest.TestCase):
    """The installer starts no Git housekeeping (UPGRADE.md 5.3): with the person's Git set to run it after every
    commit, a sample of the matrix keeps every check, the exact rollback included. The sample holds the two cases of
    CI run 39506112345, the baseline and every 80th case. A control vault proves the trigger first, and every sampled
    vault proves it again after its checks."""

    def test_the_matrix_holds_while_git_would_run_housekeeping(self):
        control = machine(housekeeping=True)
        try:
            self.assertTrue(housekeeping_runs(control),
                            "the forced housekeeping no longer runs on this Git; the trigger needs an update")
        finally:
            control.cleanup()
        matrix = Matrix()
        named = [c for c in matrix.cases if c[:2] in ((".agents/skills", "link to folder"),
                                                       (".obsidian/app.json", "link to file"))]
        self.assertEqual(len(named), 2, "the two cases of CI run 39506112345 left the matrix")
        sample = named + [c for c in matrix.cases[::80] if c not in named]
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs()) as pool:
            results = dict(zip(sample, pool.map(lambda case: matrix.run(*case, housekeeping=True), sample)))
        failed = sorted(("%s as %s" % (rel, shape), problems) for (rel, shape, _), (_, problems) in results.items()
                        if problems)
        self.assertEqual(failed, [], "%d of %d sampled cases failed" % (len(failed), len(sample)))


if __name__ == "__main__":
    unittest.main()
