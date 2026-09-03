#!/usr/bin/env python3
"""fm-fix.py — дозаполнение недостающих полей frontmatter (Vault Evolution, Фаза 2 fix).

Только страницы с СУЩЕСТВУЮЩИМ frontmatter-блоком и частичными пробелами.
L0 evidence folders, generated output и страницы без шапки не трогаются.
Не трогает body и существующие поля.

Значения: created/updated из git (fallback mtime); type/status/tags из пути-эвристики.
Dry-run по умолчанию; --apply чтобы записать. Пишет напрямую (bash), PostToolUse-хук не триггерит.
"""
import argparse
import contextlib
import datetime
import fcntl
import importlib.util
import os
import re
import secrets
import stat
import subprocess
import tempfile

VAULT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
WIKI = os.path.join(VAULT, "wiki")
FM = ("type", "status", "created", "updated", "tags")
ZONES = {
    "work": (WIKI, ("personal", "client")),
    "personal": (os.path.join(WIKI, "personal"), ()),
    "client": (os.path.join(WIKI, "client"), ()),
}
EXCLUDED_DIRS = {
    "sources", "incoming", "inbox", "agent-memory", "raw", "assets", "qa",
    "evidence", "prototype-v2", "prototype-dev", "versions",
    "node_modules", ".git", ".next", "dist", "build", "coverage",
}


def directory_identity(metadata):
    return metadata.st_dev, metadata.st_ino


def open_real_directory(path):
    absolute = os.path.abspath(path)
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open("/", flags)
    try:
        for part in (part for part in absolute.split(os.sep) if part):
            child = os.open(part, flags, dir_fd=descriptor)
            opened = os.fstat(child)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(child)
                raise ValueError("directory path contains a non-directory component")
            os.close(descriptor)
            descriptor = child
        opened = os.fstat(descriptor)
        current = os.stat(absolute, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or directory_identity(opened) != directory_identity(current)
        ):
            raise ValueError("directory changed during open: %s" % absolute)
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise ValueError("directory path is not canonical: %s" % absolute) from error
    except Exception:
        os.close(descriptor)
        raise


def verify_directory(path, descriptor):
    opened = os.fstat(descriptor)
    current = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or directory_identity(opened) != directory_identity(current)
    ):
        raise ValueError("directory changed during traversal: %s" % path)


def files(root, excl):
    out = []
    root = os.path.abspath(root)
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    def visit(descriptor, directory, root_level=False):
        for name in sorted(os.listdir(descriptor)):
            if (
                name.startswith(".") or name in EXCLUDED_DIRS
                or (root_level and name in excl)
            ):
                continue
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            path = os.path.join(directory, name)
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("link is not allowed in the active zone: %s" % path)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, flags, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if directory_identity(opened) != directory_identity(metadata):
                        raise ValueError("directory changed before open: %s" % path)
                    visit(child, path)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode) and name.endswith(".md"):
                out.append(path)
        verify_directory(directory, descriptor)

    root_descriptor = open_real_directory(root)
    try:
        visit(root_descriptor, root, root_level=True)
    finally:
        os.close(root_descriptor)
    return sorted(out)


def self_test():
    with tempfile.TemporaryDirectory(prefix="fm-zone-test-") as directory:
        directory = os.path.realpath(directory)
        os.makedirs(os.path.join(directory, "visible"))
        os.makedirs(os.path.join(directory, "personal"))
        open(os.path.join(directory, "visible", "ok.md"), "w").close()
        open(os.path.join(directory, "personal", "secret.md"), "w").close()
        found = files(directory, ("personal",))
        assert len(found) == 1 and found[0].endswith("ok.md")
        _, identity = read_regular_text(found[0])
        atomic_write(found[0], "verified\n", identity)
        verified, stale_identity = read_regular_text(found[0])
        assert verified == "verified\n"
        with open(found[0], "w", encoding="utf-8") as handle:
            handle.write("concurrent\n")
        try:
            atomic_write(found[0], "lost-update\n", stale_identity)
        except RuntimeError:
            pass
        else:
            raise AssertionError("concurrent target change was overwritten")
        _, current_identity = read_regular_text(found[0])

        def reject_owner():
            raise PermissionError("expired owner")

        try:
            atomic_write(
                found[0], "expired-owner\n", current_identity,
                before_replace=reject_owner,
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("frontmatter committed after owner rejection")
        assert read_regular_text(found[0])[0] == "concurrent\n"
        root_link = os.path.join(directory, "root-link")
        os.symlink(os.path.join(directory, "visible"), root_link)
        ancestor_link = os.path.join(directory, "ancestor-link")
        os.symlink(directory, ancestor_link)
        for candidate in (root_link, os.path.join(ancestor_link, "visible")):
            try:
                files(candidate, ())
            except ValueError:
                pass
            else:
                raise AssertionError("symlinked scan path accepted")
    print("fm-fix self-test: PASS")


def file_identity(metadata):
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns,
    )


def read_regular_text(path):
    parent = os.path.dirname(path)
    parent_fd = open_real_directory(parent)
    try:
        descriptor = os.open(
            os.path.basename(path),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError("frontmatter target is not a regular file")
            text = handle.read()
            after = os.fstat(handle.fileno())
            if file_identity(before) != file_identity(after):
                raise RuntimeError("frontmatter target changed while being read")
        verify_directory(parent, parent_fd)
        return text, file_identity(before)
    finally:
        os.close(parent_fd)


def atomic_write(path, text, expected_identity, before_replace=None):
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    parent_fd = open_real_directory(parent)
    temporary = ".fm-fix-%s.tmp" % secrets.token_hex(12)
    try:
        opened = os.fstat(parent_fd)
        current = os.stat(parent, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError("frontmatter parent directory changed")
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        current = os.stat(parent, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError("frontmatter parent directory changed before replace")
        target = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(target.st_mode) or file_identity(target) != expected_identity:
            raise RuntimeError("frontmatter target changed before replace")
        if before_replace is not None:
            before_replace()
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        current = os.stat(parent, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError("frontmatter parent directory changed after replace")
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def require_lock(path, token):
    result = subprocess.run(
        ["bash", os.path.join(VAULT, "scripts", "wiki-lock.sh"), "check", path, token],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        raise SystemExit("ERR: --apply requires an active owner token for %s" % path)


def load_lock_api():
    path = os.path.join(VAULT, "scripts", "wiki-lock.py")
    specification = importlib.util.spec_from_file_location("vault_wiki_lock", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("wiki lock API is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@contextlib.contextmanager
def owner_write_guard(path, token):
    api = load_lock_api()
    handle, directory = api.meta_lock()

    def check():
        return api.require_owner(path, token, directory=directory)

    try:
        check()
        yield check
        check()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        os.close(directory)


def git_date(path, first):
    rel = os.path.relpath(path, VAULT)
    try:
        if first:
            args = ["git", "-C", VAULT, "log", "--follow", "--diff-filter=A",
                    "--format=%ad", "--date=short", "--", rel]
        else:
            args = ["git", "-C", VAULT, "log", "-1", "--format=%ad", "--date=short", "--", rel]
        out = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        if out:
            return out[-1] if first else out[0]
    except Exception:
        pass
    return datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()


def infer(path):
    rel = os.path.relpath(path, VAULT).lower()
    typ = ("board" if "/boards/" in rel else "spec" if "/specs/" in rel
           else "session" if "/inbox/" in rel else "concept" if "/concepts/" in rel
           else "entity" if "/people/" in rel else "note")
    st = "archived" if "/archives/" in rel else "draft" if "/inbox/" in rel else "active"
    tags = []
    for proj in ("project-alpha", "project-beta", "design-system"):
        if proj in rel:
            tags = [proj]
            break
    return typ, st, tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--zone", choices=tuple(ZONES), default="work")
    ap.add_argument("--lock-token")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return 0
    lock_path = ".vault-meta/write/%s" % args.zone
    if args.apply:
        if not args.lock_token:
            ap.error("--apply requires --lock-token")
        require_lock(lock_path, args.lock_token)
    zones = [args.zone]

    changed, samples = 0, []
    for z in zones:
        root, excl = ZONES[z]
        for p in files(root, excl):
            t, identity = read_regular_text(p)
            if not t.startswith("---"):
                continue  # no-FM: sources/outputs/README — не трогаем
            end = t.find("\n---", 3)
            if end == -1:
                continue
            block = t[3:end]
            name = os.path.splitext(os.path.basename(p))[0]
            if name in ("index", "_index", "hot", "log", "overview", "dashboard"):
                continue  # служебные каталоги/кэши/логи — особый случай, не трогаем
            if "kanban-plugin:" in block:
                continue  # доска Obsidian Kanban-плагина — frontmatter за плагином
            miss = [f for f in FM if not re.search(r"(?m)^\s*%s:" % f, block)]
            if not miss:
                continue
            typ, st, tags = infer(p)
            add = []
            for f in miss:
                if f == "created":
                    add.append("created: %s" % git_date(p, True))
                elif f == "updated":
                    add.append("updated: %s" % git_date(p, False))
                elif f == "type":
                    add.append("type: %s" % typ)
                elif f == "status":
                    add.append("status: %s" % st)
                elif f == "tags":
                    add.append("tags: [%s]" % ", ".join(tags))
            new = t[:end].rstrip("\n") + "\n" + "\n".join(add) + t[end:]
            changed += 1
            if len(samples) < 10:
                samples.append((os.path.relpath(p, WIKI), miss))
            if args.apply:
                with owner_write_guard(lock_path, args.lock_token) as check_owner:
                    atomic_write(p, new, identity, before_replace=check_owner)

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print("=== fm-fix %s ===" % mode)
    print("pages to update: %d" % changed)
    for rel, miss in samples:
        print("  %-60s += %s" % (rel[:60], ", ".join(miss)))
    if not args.apply:
        print("\n(dry-run; re-run with --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
