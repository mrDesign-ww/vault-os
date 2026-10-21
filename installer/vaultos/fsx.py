"""Filesystem primitives: hashing, tree identity, atomic writes, link-safe copy and removal."""
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import tempfile

JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__"}
JUNK_SUFFIXES = (".pyc",)
TMP_PREFIX = ".vault-os-tmp-"
OLD_PREFIX = ".vault-os-old-"


def is_junk(name):
    return name in JUNK_NAMES or name.endswith(JUNK_SUFFIXES)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))


def sha256_value(value):
    return sha256_text(canonical_json(value))


def is_regular(path):
    """True for a regular file (a link to one counts). A FIFO, socket or device is never opened: reading it blocks."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def sha256_file(path):
    if not is_regular(path):
        raise OSError("%s is not a regular file (FIFO, socket or device): it is not read" % path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_exec(path):
    return bool(os.lstat(path).st_mode & stat.S_IXUSR)


def lexists(path):
    return os.path.lexists(path)


LINK_SHAPES = ("link to file", "link to folder", "dangling link", "link chain")


def shape(path):
    """What lstat finds at path (the path-shape preflight, UPGRADE.md 5.1): absent, folder, file, special (a FIFO,
    socket or device), or one of LINK_SHAPES. A link is looked at only to name its kind; nothing is read through it."""
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return "absent"
    if stat.S_ISLNK(info.st_mode):
        if os.path.islink(os.path.join(os.path.dirname(path), os.readlink(path))):
            return "link chain"
        if not os.path.exists(path):
            return "dangling link"
        return "link to folder" if os.path.isdir(path) else "link to file"
    if stat.S_ISDIR(info.st_mode):
        return "folder"
    return "file" if stat.S_ISREG(info.st_mode) else "special"


def walk_entries(root):
    """Yield (relative posix path, absolute path) of files and links under root, junk excluded, links not followed."""
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not is_junk(d))
        rel_dir = os.path.relpath(current, root)
        names = list(files) + [d for d in dirs if os.path.islink(os.path.join(current, d))]
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(current, d))]
        for name in sorted(names):
            if is_junk(name):
                continue
            rel = name if rel_dir == "." else os.path.join(rel_dir, name)
            yield rel.replace(os.sep, "/"), os.path.join(current, name)


def tree_lines(root):
    lines = []
    for rel, path in walk_entries(root):
        if os.path.islink(path):
            lines.append("%s\0link\0%s" % (rel, os.readlink(path)))
        elif not is_regular(path):
            lines.append("%s\0special\0" % rel)
        else:
            lines.append("%s\0%s\0%s" % (rel, "x" if is_exec(path) else "-", sha256_file(path)))
    return lines


def tree_sha(root):
    lines = tree_lines(root)
    return sha256_text("\n".join(lines)), len(lines)


def entry_state(path):
    """Identity of whatever is at path: absent, file, link or tree."""
    if not lexists(path):
        return {"type": "absent"}
    if os.path.islink(path):
        return {"type": "link", "target": os.readlink(path)}
    if os.path.isdir(path):
        digest, count = tree_sha(path)
        return {"type": "tree", "sha256": digest, "files": count}
    if not is_regular(path):
        return {"type": "special"}
    return {"type": "file", "sha256": sha256_file(path), "exec": is_exec(path)}


def same_state(a, b):
    if a is None or b is None or a.get("type") != b.get("type"):
        return False
    if a["type"] == "absent":
        return True
    if a["type"] == "link":
        return a["target"] == b["target"]
    if a["type"] == "file" and os.name == "posix" and a.get("exec") != b.get("exec"):
        return False
    return a.get("sha256") == b.get("sha256")


def fsync_dir(path):
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path, data, exec_bit=False):
    """Write bytes through a temporary sibling and an atomic rename; never writes through a symlink."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        mode = 0o755 if exec_bit else 0o644
        os.chmod(tmp, mode)
        if os.path.islink(path) or os.path.isdir(path):
            raise OSError("refusing to replace a link or folder with a file: %s" % path)
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    fsync_dir(parent)


def copy_entry(src, dst):
    """Copy a file, link or tree, keeping modes and links (never following them)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    elif os.path.isdir(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)


def place_tree(src, dst):
    """Install a copy of the tree src at dst (dst must be absent) through a temporary sibling and a rename."""
    parent = os.path.dirname(dst)
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, TMP_PREFIX + os.path.basename(dst))
    if lexists(tmp):
        remove_entry(tmp)
    shutil.copytree(src, tmp, symlinks=True)
    os.rename(tmp, dst)
    fsync_dir(parent)


def remove_entry(path):
    """Remove a file, link or tree at path without following links. Callers archive first."""
    if not lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def move_aside(path, into):
    """Move path into the folder into (never deletes); returns the new location."""
    os.makedirs(into, exist_ok=True)
    base = os.path.basename(path.rstrip("/")) or "item"
    dest = os.path.join(into, base)
    n = 2
    while lexists(dest):
        dest = os.path.join(into, "%s.%d" % (base, n))
        n += 1
    try:
        os.rename(path, dest)
    except OSError:
        copy_entry(path, dest)
        remove_entry(path)
    return dest


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root, exclude=()):
    """{relative path: identity} of every file and link below root, for exact before/after comparisons."""
    out = {}
    root = str(root)
    skip = tuple(e.rstrip("/") + "/" for e in exclude)
    for current, dirs, files in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(current, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        keep = []
        for d in sorted(dirs):
            rel = rel_dir + d
            if (rel + "/").startswith(skip) or rel in exclude:
                continue
            if os.path.islink(os.path.join(current, d)):
                out[rel] = ("link", os.readlink(os.path.join(current, d)))
            else:
                keep.append(d)
                out[rel + "/"] = ("dir", stat.S_IMODE(os.lstat(os.path.join(current, d)).st_mode))
        dirs[:] = keep
        for name in sorted(files):
            rel = rel_dir + name
            if rel in exclude or (rel + "/").startswith(skip):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path):
                out[rel] = ("link", os.readlink(path))
            elif not is_regular(path):
                out[rel] = ("special", stat.S_IMODE(os.lstat(path).st_mode))
            else:
                out[rel] = ("file", stat.S_IMODE(os.lstat(path).st_mode), sha256_file(path))
    return out
