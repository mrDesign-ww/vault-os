#!/usr/bin/env python3
"""Token-owned, renewable locks for multi-step vault mutations."""

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath


VAULT = Path(__file__).resolve().parents[1]
LOCK_DIR = VAULT / ".vault-meta" / "locks-v2"
DEFAULT_TTL = 21600


def normalize_path(value):
    if not value or "\n" in value or "\r" in value:
        raise ValueError("path must be a non-empty single line")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("path must stay inside the vault")
    resolved = (VAULT / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(VAULT)
    except ValueError as error:
        raise ValueError("path resolves outside the vault") from error
    return pure.as_posix()


def lock_path(path):
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return LOCK_DIR / (digest + ".json")


def generation_path(path):
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return LOCK_DIR / (digest + ".epoch")


def directory_identity(metadata):
    return metadata.st_dev, metadata.st_ino


def open_real_directory(path, label):
    absolute = Path(os.path.abspath(str(path)))
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open("/", flags)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            opened = os.fstat(child)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(child)
                raise ValueError("%s contains a non-directory component" % label)
            os.close(descriptor)
            descriptor = child
        opened = os.fstat(descriptor)
        current = os.stat(absolute, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or directory_identity(opened) != directory_identity(current)
        ):
            raise ValueError("%s changed during open" % label)
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise ValueError("%s is not a canonical real directory" % label) from error
    except Exception:
        os.close(descriptor)
        raise


def verify_lock_directory(descriptor):
    opened = os.fstat(descriptor)
    current = os.stat(LOCK_DIR, follow_symlinks=False)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or directory_identity(opened) != directory_identity(current)
    ):
        raise ValueError("lock directory changed")


def open_lock_directory(create=False):
    parent = LOCK_DIR.parent
    parent_descriptor = open_real_directory(parent, "lock metadata directory")
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        try:
            descriptor = os.open(LOCK_DIR.name, flags, dir_fd=parent_descriptor)
        except FileNotFoundError:
            if not create:
                raise
            os.mkdir(LOCK_DIR.name, 0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            descriptor = os.open(LOCK_DIR.name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ValueError("lock directory must be a real directory") from error
    finally:
        os.close(parent_descriptor)
    try:
        verify_lock_directory(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def read_record(path, directory=None):
    if path.parent != LOCK_DIR:
        raise ValueError("lock record is outside the canonical directory")
    owned_directory = directory is None
    directory = directory if directory is not None else open_lock_directory()
    try:
        verify_lock_directory(directory)
        descriptor = os.open(
            path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("lock record is not a regular file: %s" % path)
            record = json.load(handle)
        verify_lock_directory(directory)
    except FileNotFoundError:
        return None
    finally:
        if owned_directory:
            os.close(directory)
    if not isinstance(record, dict):
        raise ValueError("invalid lock record: %s" % path)
    return record


def read_generation(path, directory=None):
    record = read_record(generation_path(path), directory)
    if record is None:
        return 0
    generation = record.get("generation")
    if not isinstance(generation, int) or generation < 0:
        raise ValueError("invalid lock generation: %s" % generation_path(path))
    return generation


def atomic_json(path, payload, directory=None):
    if path.parent != LOCK_DIR:
        raise ValueError("lock record is outside the canonical directory")
    owned_directory = directory is None
    directory = directory if directory is not None else open_lock_directory(create=True)
    temporary = ".lock-%s.tmp" % secrets.token_hex(12)
    try:
        verify_lock_directory(directory)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        verify_lock_directory(directory)
        os.replace(
            temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory
        )
        os.fsync(directory)
        verify_lock_directory(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        if owned_directory:
            os.close(directory)


def active(record, now=None):
    return float(record.get("expires_at", 0)) > (time.time() if now is None else now)


def bump_generation(path, directory=None):
    generation = read_generation(path, directory) + 1
    atomic_json(
        generation_path(path), {"path": path, "generation": generation}, directory
    )
    return generation


def meta_lock():
    directory = open_lock_directory(create=True)
    try:
        descriptor = os.open(
            ".meta.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        handle = os.fdopen(descriptor, "a+")
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            handle.close()
            raise ValueError("meta lock must be a regular file")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        verify_lock_directory(directory)
        return handle, directory
    except Exception:
        os.close(directory)
        raise


def acquire(path, ttl, directory=None):
    now = time.time()
    target = lock_path(path)
    record = read_record(target, directory)
    if record:
        return None
    generation = bump_generation(path, directory)
    token = secrets.token_hex(32)
    atomic_json(target, {
        "schema_version": 2,
        "path": path,
        "token": token,
        "created_at": now,
        "renewed_at": now,
        "expires_at": now + ttl,
        "generation": generation,
        "pid": os.getpid(),
    }, directory)
    return token


def clear_stale(path, directory=None):
    owned_directory = directory is None
    directory = directory if directory is not None else open_lock_directory()
    target = lock_path(path)
    try:
        record = read_record(target, directory)
        if not record or active(record):
            return False
        bump_generation(path, directory)
        os.unlink(target.name, dir_fd=directory)
        os.fsync(directory)
        verify_lock_directory(directory)
        return True
    finally:
        if owned_directory:
            os.close(directory)


def require_owner(path, token, allow_expired=False, directory=None):
    record = read_record(lock_path(path), directory)
    if not record or not secrets.compare_digest(str(record.get("token", "")), token):
        raise PermissionError("lock is not owned by this token")
    if not allow_expired and not active(record):
        raise PermissionError("lock expired; acquire a new lock")
    return record


def main():
    parser = argparse.ArgumentParser(description="Token-owned vault write lock")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "peek"):
        command = sub.add_parser(name)
        command.add_argument("path")
        if name == "acquire":
            command.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    for name in ("check", "release", "renew"):
        command = sub.add_parser(name)
        command.add_argument("path")
        command.add_argument("token")
        if name == "renew":
            command.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    sub.add_parser("list")
    clear_parser = sub.add_parser("clear-stale")
    clear_parser.add_argument("path")
    sub.add_parser("self-test")
    args = parser.parse_args()

    if args.command == "self-test":
        assert VAULT == Path(__file__).resolve().parents[1]
        assert normalize_path("wiki/project/note.md") == "wiki/project/note.md"
        for invalid in ("", "/tmp/x", "../x", "wiki/../../x"):
            try:
                normalize_path(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError("unsafe path accepted: %r" % invalid)
        assert active({"expires_at": 2}, 1)
        assert not active({"expires_at": 1}, 2)
        original_lock_dir = LOCK_DIR
        try:
            with tempfile.TemporaryDirectory(prefix="wiki-lock-parent-test-") as directory:
                base = Path(os.path.realpath(directory))
                vault = base / "vault"
                outside = base / "outside"
                vault.mkdir()
                outside.mkdir()
                (vault / ".vault-meta").symlink_to(outside, target_is_directory=True)
                globals()["LOCK_DIR"] = vault / ".vault-meta" / "locks-v2"
                try:
                    meta_lock()
                except ValueError:
                    pass
                else:
                    raise AssertionError("symlinked metadata parent accepted")
                assert not (outside / "locks-v2").exists()
            with tempfile.TemporaryDirectory(prefix="wiki-lock-dir-test-") as directory:
                base = Path(os.path.realpath(directory))
                metadata = base / "metadata"
                outside = base / "outside"
                metadata.mkdir()
                outside.mkdir()
                alias = metadata / "locks-v2"
                alias.symlink_to(outside, target_is_directory=True)
                globals()["LOCK_DIR"] = alias
                try:
                    meta_lock()
                except ValueError:
                    pass
                else:
                    raise AssertionError("symlinked lock directory accepted")
                assert not (outside / ".meta.lock").exists()
            with tempfile.TemporaryDirectory(prefix="wiki-lock-test-") as directory:
                globals()["LOCK_DIR"] = Path(os.path.realpath(directory))
                token = acquire(".vault-meta/write/work", 60)
                assert len(token) == 64 and all(c in "0123456789abcdef" for c in token)
                assert token and read_generation(".vault-meta/write/work") == 1
                require_owner(".vault-meta/write/work", token)
                record = read_record(lock_path(".vault-meta/write/work"))
                generation = read_generation(".vault-meta/write/work")
                record["expires_at"] = time.time() + 120
                atomic_json(lock_path(".vault-meta/write/work"), record)
                require_owner(".vault-meta/write/work", token)
                assert read_generation(".vault-meta/write/work") == generation
                record = read_record(lock_path(".vault-meta/write/work"))
                record["expires_at"] = 0
                atomic_json(lock_path(".vault-meta/write/work"), record)
                assert acquire(".vault-meta/write/work", 60) is None
                try:
                    require_owner(".vault-meta/write/work", token)
                except PermissionError:
                    pass
                else:
                    raise AssertionError("expired owner remained writable")
                require_owner(".vault-meta/write/work", token, allow_expired=True)
                bump_generation(".vault-meta/write/work")
                lock_path(".vault-meta/write/work").unlink()
                directory_fd = open_lock_directory()
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                assert read_generation(".vault-meta/write/work") == 2
                stale_token = acquire(".vault-meta/write/work", 60)
                stale_record = read_record(lock_path(".vault-meta/write/work"))
                stale_record["expires_at"] = 0
                atomic_json(lock_path(".vault-meta/write/work"), stale_record)
                assert clear_stale(".vault-meta/write/work")
                assert read_generation(".vault-meta/write/work") == 4
                try:
                    require_owner(".vault-meta/write/work", stale_token, allow_expired=True)
                except PermissionError:
                    pass
                else:
                    raise AssertionError("cleared stale token remained valid")
        finally:
            globals()["LOCK_DIR"] = original_lock_dir
        print("wiki-lock self-test: PASS")
        return 0

    handle, directory = meta_lock()
    try:
        if args.command in {"acquire", "peek", "check", "release", "renew", "clear-stale"}:
            path = normalize_path(args.path)
        if args.command in {"acquire", "renew"} and args.ttl < 60:
            parser.error("--ttl must be at least 60 seconds")

        if args.command == "acquire":
            token = acquire(path, args.ttl, directory)
            if token is None:
                record = read_record(lock_path(path), directory)
                if record and not active(record):
                    print(
                        "ERR: stale lock blocks acquisition; release with its token or run "
                        "clear-stale %s after confirming the writer died" % path,
                        file=sys.stderr,
                    )
                else:
                    print("ERR: lock is held: %s" % path, file=sys.stderr)
                return 75
            print(token)
        elif args.command == "check":
            require_owner(path, args.token, directory=directory)
            print("owned")
        elif args.command == "renew":
            record = require_owner(path, args.token, directory=directory)
            now = time.time()
            record.update({"renewed_at": now, "expires_at": now + args.ttl, "pid": os.getpid()})
            atomic_json(lock_path(path), record, directory)
            print("renewed")
        elif args.command == "release":
            require_owner(path, args.token, allow_expired=True, directory=directory)
            bump_generation(path, directory)
            os.unlink(lock_path(path).name, dir_fd=directory)
            os.fsync(directory)
            verify_lock_directory(directory)
            print("released")
        elif args.command == "peek":
            record = read_record(lock_path(path), directory)
            print(json.dumps({
                "path": path,
                "active": bool(record),
                "expired": bool(record and not active(record)),
                "expires_at": record.get("expires_at") if record else None,
                "generation": max(
                    read_generation(path, directory),
                    int(record.get("generation", 0)) if record else 0,
                ),
            }, sort_keys=True))
        elif args.command == "list":
            for name in sorted(
                name for name in os.listdir(directory) if name.endswith(".json")
            ):
                record = read_record(LOCK_DIR / name, directory)
                print(json.dumps({
                    "path": record.get("path"),
                    "active": True,
                    "expired": not active(record),
                    "expires_at": record.get("expires_at"),
                }, sort_keys=True))
        elif args.command == "clear-stale":
            print(1 if clear_stale(path, directory) else 0)
    except (ValueError, PermissionError) as error:
        print("ERR: %s" % error, file=sys.stderr)
        return 4
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        os.close(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
