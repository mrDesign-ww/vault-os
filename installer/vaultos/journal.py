"""Backup archive with a write-ahead journal. Every live change is recorded (and fsynced) before it happens, and
every rollback step is idempotent, so a killed upgrade can always be returned to the exact previous state.

Record types (one JSON object per line in journal.jsonl):
  begin      {plan_id, version, roots}
  backup     {layer, path, state, copy}       the original state of a path, copied into files/<layer>/<path>
  write      {layer, path, written}           path was (or is about to be) replaced or created; written = identity
  remove     {layer, path}                    path was (or is about to be) removed
  mkdir      {layer, path}                    folder created by the upgrade
  rmdir      {layer, path, mode}              empty folder removed by the upgrade
  move       {layer, src, dst}                personal file moved by the vault migration
  external   {layer, path, before}            a tool (upstream setup, git, index build) changes children of path
  settle     {layer, path, state[, regenerable]} or {layer, path, children}
                                              what a successful upgrade left there, written just before commit
  commit     {}                               the upgrade finished
  rolled_back{}                               a rollback finished

Rollback of a committed upgrade (with settle records) keeps the person's newer work: what equals the settled state is
the upgrade's own and is replaced by the backup; anything else goes to <archive>/displaced/<layer>/<path> first;
children and files created after the settle stay in place; the vault's Git metadata stays as it is when the person
committed after the upgrade. An interrupted upgrade (no commit) is returned to the exact previous tree.
"""
import datetime as dt
import json
import os
import signal
import uuid

from . import fsx

FAULT_ENV = "VAULT_OS_FAULT_AFTER"  # test hook: SIGKILL the process after N live changes
GIT_DIR = ".git"  # the vault's Git metadata, vault layer
GIT_HISTORY = ("HEAD", "refs", "logs", "packed-refs")  # these move when the person commits after the upgrade


class Archive:
    def __init__(self, path, roots):
        self.path = path
        self.roots = roots  # layer -> absolute root
        self.journal_path = os.path.join(path, "journal.jsonl")
        self.backed = {}
        self.copies = {}  # (layer, path) -> its copy in the archive, for keys that have one
        self.live_ops = 0
        fault = os.environ.get(FAULT_ENV, "")
        self.fault_after = int(fault) if fault.isdigit() else None

    # ── creation and loading ────────────────────────────────────────────────
    @classmethod
    def create(cls, backup_root, roots):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(backup_root, "%s-%s" % (stamp, uuid.uuid4().hex[:4]))
        os.makedirs(os.path.join(path, "files"))
        return cls(path, roots)

    @classmethod
    def load(cls, path):
        records = read_journal(os.path.join(path, "journal.jsonl"))
        begin = next((r for r in records if r["op"] == "begin"), None)
        if begin is None:
            raise ValueError("not an upgrade archive: %s" % path)
        archive = cls(path, begin["roots"])
        archive.records = records
        return archive

    def abs(self, layer, rel):
        return os.path.join(self.roots[layer], *rel.split("/")) if rel else self.roots[layer]

    def copy_path(self, layer, rel):
        return os.path.join(self.path, "files", layer, *rel.split("/"))

    def record(self, op, **fields):
        fields["op"] = op
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(fields, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def tick(self):
        """Count one live change; the fault hook kills the process here, between two recorded steps."""
        self.live_ops += 1
        if self.fault_after is not None and self.live_ops >= self.fault_after:
            os.kill(os.getpid(), signal.SIGKILL)

    # ── forward operations ──────────────────────────────────────────────────
    def backup(self, layer, rel):
        key = (layer, rel)
        if key in self.backed:
            return self.backed[key]
        path = self.abs(layer, rel)
        state = fsx.entry_state(path)
        copy = None
        if state["type"] != "absent":
            copy = self.copy_path(layer, rel)
            if any(l == layer and (not r or not rel or rel.startswith(r + "/") or r.startswith(rel + "/"))
                   for l, r in self.copies):
                # a path inside (or around) one already copied: its copy must not land inside the other copy
                copy = os.path.join(self.path, "files", ".nested", str(len(self.copies)), layer, *rel.split("/"))
            fsx.copy_entry(path, copy)
            self.copies[key] = copy
        self.record("backup", layer=layer, path=rel, state=state, copy=copy)
        self.backed[key] = state
        return state

    def refuse_link_parent(self, layer, rel):
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            if os.path.islink(self.abs(layer, "/".join(parts[:i]))):
                raise OSError("refusing to change a path below a symbolic link: %s" % "/".join(parts[:i]))

    def ensure_parent(self, layer, rel):
        """Create the missing parent folders of rel through journaled mkdirs; refuse a parent that is a link."""
        self.refuse_link_parent(layer, rel)
        self.mkdir(layer, "/".join(rel.split("/")[:-1]))

    def write_file(self, layer, rel, data, exec_bit=False):
        self.ensure_parent(layer, rel)
        self.backup(layer, rel)
        path = self.abs(layer, rel)
        written = {"type": "file", "sha256": fsx.sha256_bytes(data), "exec": bool(exec_bit)}
        self.record("write", layer=layer, path=rel, written=written)
        if fsx.lexists(path) and (os.path.islink(path) or os.path.isdir(path)):
            fsx.remove_entry(path)
        fsx.atomic_write(path, data, exec_bit)
        self.tick()

    def copy_file(self, layer, rel, src):
        with open(src, "rb") as handle:
            data = handle.read()
        self.write_file(layer, rel, data, fsx.is_exec(src))

    def put_tree(self, layer, rel, src):
        self.ensure_parent(layer, rel)
        self.backup(layer, rel)
        path = self.abs(layer, rel)
        digest, count = fsx.tree_sha(src)
        self.record("write", layer=layer, path=rel, written={"type": "tree", "sha256": digest, "files": count})
        fsx.remove_entry(path)
        fsx.place_tree(src, path)
        self.tick()

    def remove(self, layer, rel):
        self.refuse_link_parent(layer, rel)  # never removes through a link (its target is not the system's)
        self.backup(layer, rel)
        self.record("remove", layer=layer, path=rel)
        fsx.remove_entry(self.abs(layer, rel))
        self.tick()

    def mkdir(self, layer, rel):
        """Create rel (and its missing parents, the layer root included), one journaled record per folder."""
        rel = rel.strip("/")
        path = self.abs(layer, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            return
        if rel:
            self.mkdir(layer, os.path.dirname(rel))
        self.record("mkdir", layer=layer, path=rel)
        if rel:
            os.mkdir(path)
        else:
            os.makedirs(path)
        self.tick()

    def expect_tool_change(self, layer, rel):
        """Before a tool (git, the index build, an upstream setup) changes rel: keep its state for the rollback."""
        self.backup(layer, rel)
        self.record("write", layer=layer, path=rel, written={"type": "external"})

    def rmdir(self, layer, rel):
        path = self.abs(layer, rel)
        mode = os.stat(path).st_mode & 0o7777
        self.record("rmdir", layer=layer, path=rel, mode=mode)
        os.rmdir(path)
        self.tick()

    def move(self, layer, src, dst):
        self.refuse_link_parent(layer, src)
        self.ensure_parent(layer, dst)
        self.record("move", layer=layer, src=src, dst=dst)
        if fsx.lexists(self.abs(layer, dst)):
            raise OSError("migration target exists: %s" % dst)
        os.rename(self.abs(layer, src), self.abs(layer, dst))
        self.tick()

    def external(self, layer, rel):
        """Before a tool writes below rel: remember its current children so a rollback can displace new ones."""
        path = self.abs(layer, rel)
        before = sorted(os.listdir(path)) if os.path.isdir(path) else None
        self.record("external", layer=layer, path=rel, before=before)

    def settle(self, regenerable=()):
        """Just before the commit record: what the upgrade left at every path a tool changed (and at every path it
        wrote inside a tool's folder), the vault's Git history entries, and the children of every tool folder. A
        later rollback treats exactly this as the upgrade's own; anything newer is the person's."""
        records = read_journal(self.journal_path)
        written, folders = {}, []
        for r in records:
            if r["op"] == "write":
                written[(r["layer"], r["path"])] = r["written"]
            elif r["op"] == "external" and (r["layer"], r["path"]) not in folders:
                folders.append((r["layer"], r["path"]))

        def inside(key, folder):
            return key[0] == folder[0] and (not folder[1] or key[1].startswith(folder[1] + "/"))

        keys = []
        for key, identity in written.items():
            if identity.get("type") == "external":
                keys.append(key)
            elif any(inside(key, f) for f in folders) and not fsx.same_state(fsx.entry_state(self.abs(*key)),
                                                                            identity):
                keys.append(key)  # e.g. an upstream skill folder its own setup wrote into
        if "vault" in self.roots and os.path.isdir(self.abs("vault", GIT_DIR)):
            for name in GIT_HISTORY + ("index",):
                key = ("vault", GIT_DIR + "/" + name)
                if key not in keys:
                    keys.append(key)
        regen = {tuple(k) for k in regenerable}
        for layer, rel in keys:
            fields = {"state": fsx.entry_state(self.abs(layer, rel))}
            if (layer, rel) in regen:
                fields["regenerable"] = True
            self.record("settle", layer=layer, path=rel, **fields)
        for layer, rel in folders:
            path = self.abs(layer, rel)
            self.record("settle", layer=layer, path=rel,
                        children=sorted(os.listdir(path)) if os.path.isdir(path) else None)


def read_journal(path):
    records = []
    if not os.path.exists(path):
        return records
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                break  # a torn last line from a crash: everything before it is valid
    return records


def status(path):
    ops = [r["op"] for r in read_journal(os.path.join(path, "journal.jsonl"))]
    if "rolled_back" in ops:
        return "rolled_back"
    if "commit" in ops:
        return "committed"
    if "begin" in ops:
        return "incomplete"
    return "empty"


def list_archives(backup_root):
    if not os.path.isdir(backup_root):
        return []
    out = []
    for name in sorted(os.listdir(backup_root)):
        path = os.path.join(backup_root, name)
        if os.path.isfile(os.path.join(path, "journal.jsonl")):
            out.append(path)
    return out


# ── rollback ────────────────────────────────────────────────────────────────

class _FS:
    """The live filesystem, or (dry) an overlay that answers as if the rollback's earlier steps had already run, so
    the preview takes exactly the decisions the rollback will take."""

    def __init__(self, dry):
        self.dry = dry
        self.over = {}  # absolute path -> None (gone), ("dir",) (empty folder made here), ("real", path on disk)

    def _resolve(self, path):
        if not self.dry:
            return ("real", path)
        p = path
        while True:
            if p in self.over:
                v = self.over[p]
                if v is None or (v[0] == "dir" and p != path):
                    return None
                if v[0] == "dir" or p == path:
                    return v
                return ("real", os.path.join(v[1], os.path.relpath(path, p)))
            parent = os.path.dirname(p)
            if parent == p:
                return ("real", path)
            p = parent

    def exists(self, path):
        r = self._resolve(path)
        return r is not None and (r[0] == "dir" or fsx.lexists(r[1]))

    def isdir(self, path):
        r = self._resolve(path)
        return r is not None and (r[0] == "dir" or (os.path.isdir(r[1]) and not os.path.islink(r[1])))

    def state(self, path):
        r = self._resolve(path)
        if r is None:
            return {"type": "absent"}
        if r[0] == "dir":
            return {"type": "tree", "sha256": fsx.sha256_text(""), "files": 0}
        return fsx.entry_state(r[1])

    def listdir(self, path):
        r = self._resolve(path)
        names = set()
        if r is not None and r[0] == "real" and os.path.isdir(r[1]) and not os.path.islink(r[1]):
            names = set(os.listdir(r[1]))
        if self.dry:
            for k, v in self.over.items():
                if os.path.dirname(k) == path:
                    (names.discard if v is None else names.add)(os.path.basename(k))
        return sorted(names)

    def remove(self, path):
        if self.dry:
            self.over[path] = None
        else:
            fsx.remove_entry(path)

    def copy_in(self, src, dst):
        if self.dry:
            self.over[dst] = ("real", src)
        else:
            fsx.copy_entry(src, dst)

    def rename(self, src, dst):
        if self.dry:
            self.over[dst] = self._resolve(src)
            self.over[src] = None
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)

    def makedirs(self, path, mode):
        if self.dry:
            self.over[path] = ("dir",)
        else:
            os.makedirs(path)
            os.chmod(path, mode)

    def rmdir(self, path):
        if self.dry:
            self.over[path] = None
        else:
            os.rmdir(path)

    def move_out(self, path, dest):
        """Move path to dest in the holding folder (never deletes)."""
        if self.dry:
            self.over[path] = None
            return
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            os.rename(path, dest)
        except OSError:
            fsx.copy_entry(path, dest)
            fsx.remove_entry(path)


def _digest(value):
    return fsx.sha256_value(value)[:16]


def _replay(archive, fs, log):
    """Replay the journal backwards through fs. Returns {"actions": [...], "git": none|restored|kept}."""
    records = archive.records
    ops = {r["op"] for r in records}
    backups, written, settled, children_after, regen = {}, {}, {}, {}, set()
    for r in records:
        op = r["op"]
        if op == "backup":
            backups.setdefault((r["layer"], r["path"]), r)
        elif op == "write":
            written[(r["layer"], r["path"])] = r["written"]
        elif op == "settle":
            key = (r["layer"], r["path"])
            if "children" in r:
                children_after[key] = r["children"]
            else:
                settled[key] = r["state"]
                if r.get("regenerable"):
                    regen.add(key)
    after_use = "commit" in ops and bool(settled or children_after)  # else: exact return (crash recovery)
    holding = os.path.join(archive.path, "displaced")
    actions, reserved = [], set()

    def act(do, layer, rel, reason=None, **extra):
        a = {"do": do, "layer": layer, "path": rel}
        if reason:
            a["reason"] = reason
        a.update(extra)
        actions.append(a)

    def displace(layer, rel, reason, state=None):
        target = archive.abs(layer, rel)
        dest = os.path.join(holding, layer, *rel.split("/")) if rel else os.path.join(holding, layer)
        base, n = dest, 2
        while dest in reserved or fsx.lexists(dest):
            dest, n = "%s.%d" % (base, n), n + 1
        reserved.add(dest)
        state = fs.state(target) if state is None else state
        fs.move_out(target, dest)
        act("displace", layer, rel, reason, state=_digest(state), to=dest)
        if log:
            log("displaced %s (%s) to %s" % (target, reason, dest))

    def in_git(key):
        return key[0] == "vault" and (key[1] == GIT_DIR or key[1].startswith(GIT_DIR + "/"))

    # A key with other keys inside it (an upstream folder whose setup a tool changed) is judged by its state before
    # the replay restores the inner keys, which is what its settle record describes.
    keys = set(backups)
    outer = {k: fs.state(archive.abs(*k)) for k in keys
             if any(o != k and o[0] == k[0] and (not k[1] or o[1].startswith(k[1] + "/")) for o in keys)}

    git = "none"
    history = [k for k in (("vault", GIT_DIR + "/" + n) for n in GIT_HISTORY) if k in settled]
    if after_use and history:
        now = [fs.state(archive.abs(*k)) for k in history]
        moved = [k for k, s in zip(history, now) if not fsx.same_state(s, settled[k])]
        git = "kept" if moved else "restored"
        if moved:
            act("stay", "vault", GIT_DIR, "history kept: commits were made after the upgrade, so all Git metadata "
                "stays as it is; the restored files will show as changes in git status", state=_digest(now))

    def why(key):
        if not after_use:
            if (written.get(key) or {}).get("type") == "external":
                return "changed by a tool step of the upgrade"
            return "changed after the upgrade"
        if key == ("vault", GIT_DIR + "/index"):
            return "Git index refreshed after the upgrade (for example by git status); regenerable"
        if key == ("vault", GIT_DIR):
            return "Git repository made by the upgrade, no commits since; only regenerable state changed"
        if key in regen:
            return "index or lock state changed after the upgrade; regenerable"
        return "changed after the upgrade"

    def restore(key):
        """Put the backed-up state of key back in place; what is there and is not the upgrade's goes aside first."""
        b = backups.get(key)
        if b is None or (git == "kept" and in_git(key)):
            return
        layer, rel = key
        target = archive.abs(layer, rel)
        live = fs.state(target)
        if fsx.same_state(live, b["state"]):
            return
        ours = settled.get(key, written.get(key)) if after_use else written.get(key)
        was = "absent"
        if live["type"] != "absent":
            if ours is not None and fsx.same_state(outer.get(key, live), ours):
                fs.remove(target)
                was = "upgrade"
            else:
                displace(layer, rel, why(key), live)
                was = "displaced"
        if b["state"]["type"] != "absent":
            fs.copy_in(b["copy"], target)
        act("restore", layer, rel, live=was, state=_digest(live))

    done = set()
    for r in reversed(records):
        op = r["op"]
        if op in ("write", "remove"):
            key = (r["layer"], r["path"])
            if key not in done:
                restore(key)
                done.add(key)
        elif op == "mkdir":
            layer, rel = r["layer"], r["path"]
            target = archive.abs(layer, rel)
            if not fs.isdir(target):
                continue
            for name in fs.listdir(target):
                if name.startswith((fsx.TMP_PREFIX, fsx.OLD_PREFIX)):
                    displace(layer, (rel + "/" + name).lstrip("/"), "temporary file of an interrupted write")
            left = fs.listdir(target)
            if left and all(fsx.is_junk(n) for n in left):
                for name in left:
                    fs.remove(os.path.join(target, name))
                left = []
            if not left:
                fs.rmdir(target)
                act("remove_dir", layer, rel)
            elif after_use:
                act("stay", layer, rel, "folder made by the upgrade holds files created after it: kept in place "
                    "with them", state=_digest(fs.state(target)))
            else:
                displace(layer, rel, "folder created by the upgrade is not empty")
        elif op == "rmdir":
            target = archive.abs(r["layer"], r["path"])
            if not fs.exists(target):
                fs.makedirs(target, r["mode"])
                act("recreate_dir", r["layer"], r["path"])
        elif op == "move":
            layer = r["layer"]
            src, dst = archive.abs(layer, r["src"]), archive.abs(layer, r["dst"])
            if fs.exists(dst) and not fs.exists(src):
                fs.rename(dst, src)
                act("move_back", layer, r["dst"], to=r["src"], state=_digest(fs.state(src)))
            elif after_use and fs.exists(dst):
                act("stay", layer, r["dst"], "its old place %s is taken again: kept at the new place" % r["src"],
                    state=_digest(fs.state(dst)))
            elif after_use and not fs.exists(src):
                act("stay", layer, r["dst"], "not at its new place any more (moved or removed after the upgrade); "
                    "its old place was %s" % r["src"])
        elif op == "external":
            layer, rel, before = r["layer"], r["path"], r["before"]
            target = archive.abs(layer, rel)
            if before is None or not fs.isdir(target) or (git == "kept" and in_git((layer, rel))):
                continue
            later = children_after.get((layer, rel))
            for name in fs.listdir(target):
                if name in before:
                    continue
                child = (layer, (rel + "/" + name).lstrip("/"))
                if child in backups or child in done:
                    continue
                if after_use and (later is None or name not in later):
                    act("stay", layer, child[1], "created after the upgrade")
                    continue
                displace(layer, child[1], "made by a tool step of the upgrade (git, the index build or an upstream "
                         "setup)")
    # Leftover temporary siblings from a write interrupted between its two renames.
    for layer, root in archive.roots.items():
        parents = sorted({os.path.dirname(r["path"]) for r in records
                          if r["op"] in ("write", "remove") and r["layer"] == layer})
        for parent in parents:
            if not fs.isdir(archive.abs(layer, parent)):
                continue
            for name in fs.listdir(archive.abs(layer, parent)):
                if name.startswith((fsx.TMP_PREFIX, fsx.OLD_PREFIX)):
                    displace(layer, (parent + "/" + name).lstrip("/"), "temporary file of an interrupted write")
    return {"actions": actions, "git": git}


def preview(path):
    """What rollback(path) would do now, changing nothing: counts, what stays, what moves aside, and its id."""
    archive = Archive.load(path)
    result = _replay(archive, _FS(dry=True), None)
    actions = result["actions"]
    return {
        "archive": archive.path,
        "id": _digest({"archive": archive.path, "actions": actions}),
        "restores": sum(1 for a in actions if a["do"] in ("restore", "move_back", "remove_dir", "recreate_dir")),
        "stays": [{k: a[k] for k in ("layer", "path", "reason")} for a in actions if a["do"] == "stay"],
        "displaced": [{k: a[k] for k in ("layer", "path", "reason", "to")} for a in actions if a["do"] == "displace"],
        "holding_folder": os.path.join(archive.path, "displaced"),
        "git": result["git"],
        "actions": actions,
    }


def rollback(path, log=print):
    """Replay the journal backwards. Never deletes data the upgrade did not write: it displaces it or leaves it."""
    archive = Archive.load(path)
    _replay(archive, _FS(dry=False), log)
    archive.record("rolled_back", at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return archive
