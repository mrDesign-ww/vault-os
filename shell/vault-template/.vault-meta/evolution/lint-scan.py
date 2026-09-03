#!/usr/bin/env python3
"""lint-scan.py — zone-aware детерминированный вики-lint (Vault Evolution, Фаза 2).

Классифицирует wikilinks честно, чтобы «dead» не раздувалось ложными срабатываниями:
  - ok           резолвится внутри своей зоны
  - reference-ref цель = top-level Reference page — легитимная ссылка вне wiki
  - immutable-ref unresolved ссылка из L0 log/source — отчёт, но не auto-fix
  - cross-zone   цель в ДРУГОЙ зоне (обычно легитимная nav-ссылка между индексами;
                 всё, что НЕ между индексами, помечается как возможное нарушение изоляции)
  - dead         не резолвится нигде — вот это настоящие битые/заготовки

Также: orphans (страница без входящих ссылок, кроме служебных) и frontmatter-пробелы.
L0 evidence и implementation artifacts выводятся отдельными категориями и не
предлагаются для массового auto-fix.
Резолвит путевые wikilinks ([[folder/page]]) и экранированные пайпы в таблицах ([[page\\|alias]]).
Только отчёт, ничего не меняет.
"""
import argparse
import json
import os
import re
import stat
import tempfile
from collections import defaultdict

VAULT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
WIKI = os.path.join(VAULT, "wiki")
REFERENCE = os.path.join(VAULT, "Reference")

ZONES = {
    "work": {"root": WIKI, "exclude": ["personal", "client"]},
    "personal": {"root": os.path.join(WIKI, "personal"), "exclude": []},
    "client": {"root": os.path.join(WIKI, "client"), "exclude": []},
}
SERVICE = {"index", "_index", "hot", "log", "overview", "dashboard"}
WIKILINK = re.compile(r"!?\[\[([^\]|#]+)")
FM_FIELDS = ("type", "status", "created", "updated", "tags")
ATTACH = re.compile(r"\.(png|jpe?g|gif|svg|pdf|canvas|excalidraw|webp|mp4|mov)$", re.I)
NUMERIC_VECTOR = re.compile(r"^-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)+$")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
TITLE = re.compile(r"(?m)^title:\s*[\"']?([^\"'\n]+)")
EXCLUDED_DIRS = {"node_modules", ".git", ".next", "dist", "build", "coverage"}
IMMUTABLE_PARTS = {"sources", "incoming", "inbox", "agent-memory", "raw"}
ARTIFACT_PARTS = {"assets", "prototype-v2", "prototype-dev", "versions"}
ARTIFACT_NAMES = re.compile(
    r"^(README|RETIRED|VERSION|(?:APPROVED_)?ASSET_|IMPLEMENTATION_BRIEF_|out_)",
    re.I,
)


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


def markdown_files(root, excluded_roots=()):
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
                or (root_level and name in excluded_roots)
            ):
                continue
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            path = os.path.join(directory, name)
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("link is not allowed in the scanned tree: %s" % path)
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


def zone_files(z):
    return markdown_files(ZONES[z]["root"], set(ZONES[z]["exclude"]))


def self_test():
    original_work = ZONES["work"]
    try:
        with tempfile.TemporaryDirectory(prefix="lint-zone-test-") as directory:
            directory = os.path.realpath(directory)
            os.makedirs(os.path.join(directory, "visible"))
            os.makedirs(os.path.join(directory, "personal"))
            open(os.path.join(directory, "visible", "ok.md"), "w").close()
            open(os.path.join(directory, "personal", "secret.md"), "w").close()
            ZONES["work"] = {"root": directory, "exclude": ["personal"]}
            found = zone_files("work")
            assert len(found) == 1 and found[0].endswith("ok.md")
            link = os.path.join(directory, "root-link")
            os.symlink(os.path.join(directory, "visible"), link)
            ZONES["work"] = {"root": link, "exclude": []}
            try:
                zone_files("work")
            except ValueError:
                pass
            else:
                raise AssertionError("zone-root symlink accepted")
            ancestor_link = os.path.join(directory, "ancestor-link")
            os.symlink(directory, ancestor_link)
            ZONES["work"] = {
                "root": os.path.join(ancestor_link, "visible"), "exclude": [],
            }
            try:
                zone_files("work")
            except ValueError:
                pass
            else:
                raise AssertionError("symlinked zone ancestor accepted")
    finally:
        ZONES["work"] = original_work
    print("lint-scan self-test: PASS")


def base(p):
    return os.path.splitext(os.path.basename(p))[0]


def read_regular_text(path, limit=None):
    parent = os.path.dirname(path)
    parent_fd = open_real_directory(parent)
    try:
        first_text = None
        anchor = None
        final_metadata = None
        for attempt in range(2):
            descriptor = os.open(
                os.path.basename(path),
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("wiki page is not a regular file: %s" % path)
                text = handle.read() if limit is None else handle.read(limit)
                after = os.fstat(handle.fileno())
            before_identity = (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
            )
            after_identity = (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            )
            if before_identity != after_identity:
                current_anchor = (before.st_dev, before.st_ino, before.st_size)
                if attempt or current_anchor != (
                    after.st_dev, after.st_ino, after.st_size
                ):
                    raise ValueError("wiki page changed while being read: %s" % path)
                first_text = text
                anchor = current_anchor
                continue
            if first_text is not None and (
                text != first_text
                or anchor != (before.st_dev, before.st_ino, before.st_size)
            ):
                raise ValueError("wiki page changed during retry: %s" % path)
            final_metadata = after
            break
        if final_metadata is None:
            raise ValueError("wiki page did not stabilize: %s" % path)
        current = os.stat(
            os.path.basename(path), dir_fd=parent_fd, follow_symlinks=False
        )
        if (
            current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns
        ) != (
            final_metadata.st_dev, final_metadata.st_ino,
            final_metadata.st_size, final_metadata.st_mtime_ns,
        ):
            raise ValueError("wiki page changed after read: %s" % path)
        verify_directory(parent, parent_fd)
        return text
    finally:
        os.close(parent_fd)


def page_title(p):
    text = read_regular_text(p, 8192)
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    match = TITLE.search(text[3:end] if end != -1 else "")
    return match.group(1).strip() if match else None


def immutable_page(p):
    parts = {part.lower() for part in p.split(os.sep)}
    return os.path.basename(p).lower() == "log.md" or bool(parts & IMMUTABLE_PARTS)


def artifact_page(p):
    parts = {part.lower() for part in p.split(os.sep)}
    return bool(parts & ARTIFACT_PARTS) or bool(ARTIFACT_NAMES.match(base(p)))


def load_names(zone):
    paths = zone_files(zone)
    names = {base(p) for p in paths}
    aliases = {
        title: base(p)
        for p in paths
        for title in [page_title(p)]
        if title and title != base(p)
    }
    references = set()
    if os.path.exists(REFERENCE):
        references = {
            base(path) for path in markdown_files(REFERENCE)
        }
    return names, aliases, references


def explicit_cross_zone(target, active_zone):
    normalized = target.replace("\\", "/").lstrip("./").lower()
    prefixes = {
        "work": ("personal/", "client/", "wiki/personal/", "wiki/client/"),
        "personal": (
            "client/", "wiki/client/", "workspace/", "areas/", "resources/",
            "archives/", "wiki/workspace/", "wiki/areas/", "wiki/resources/",
            "wiki/archives/",
        ),
        "client": (
            "personal/", "wiki/personal/", "workspace/", "areas/", "resources/",
            "archives/", "wiki/workspace/", "wiki/areas/", "wiki/resources/",
            "wiki/archives/",
        ),
    }
    return any(normalized.startswith(prefix) for prefix in prefixes[active_zone])


def scan_zone(z, names, aliases, references):
    files = zone_files(z)
    inbound = {n: 0 for n in names}
    dead, cross, memrefs, refrefs, immutable_refs = [], [], [], [], []
    fm_gaps, immutable_fm_gaps, artifact_fm_gaps = [], [], []
    for p in files:
        rel_page = os.path.relpath(p, VAULT).replace(os.sep, "/")
        text = read_regular_text(p)
        if text.startswith("---"):
            end = text.find("\n---", 3)
            block = text[3:end] if end != -1 else ""
            miss = [f for f in FM_FIELDS if not re.search(r"(?m)^\s*%s:" % f, block)]
        else:
            miss = ["no-frontmatter"]
        if miss:
            record = {"page": base(p), "path": rel_page, "missing": miss}
            if immutable_page(p):
                immutable_fm_gaps.append(record)
            elif artifact_page(p):
                artifact_fm_gaps.append(record)
            else:
                fm_gaps.append(record)
        for m in WIKILINK.finditer(HTML_COMMENT.sub("", text)):
            target = m.group(1).split("\\")[0].strip()
            if not target or ATTACH.search(target) or NUMERIC_VECTOR.fullmatch(target):
                continue
            tb = target.split("/")[-1].strip()
            if explicit_cross_zone(target, z):
                cross.append({
                    "page": base(p), "path": rel_page, "target": target,
                    "allowed_navigation": base(p) in {"index", "_index"}
                    and tb in {"index", "_index"},
                })
            elif tb in names:
                if tb != base(p):
                    inbound[tb] = inbound.get(tb, 0) + 1
            elif tb in aliases:
                canonical = aliases[tb]
                if canonical != base(p):
                    inbound[canonical] = inbound.get(canonical, 0) + 1
            elif tb in references:
                refrefs.append({"page": base(p), "path": rel_page, "target": tb})
            elif immutable_page(p):
                immutable_refs.append({"page": base(p), "path": rel_page, "target": target})
            else:
                dead.append({"page": base(p), "path": rel_page, "target": target})
    path_by_name = defaultdict(list)
    for p in files:
        path_by_name[base(p)].append(p)
    duplicate_filenames = [
        {
            "filename": name + ".md",
            "paths": sorted(
                os.path.relpath(p, VAULT).replace(os.sep, "/") for p in paths
            ),
        }
        for name, paths in sorted(path_by_name.items())
        if len(paths) > 1
        and name != "_index"
        and not all(immutable_page(p) or artifact_page(p) for p in paths)
    ]
    orphan_paths, immutable_orphans, artifact_orphans = [], [], []
    for name in sorted(n for n in names
                       if inbound.get(n, 0) == 0 and n not in SERVICE and not n.startswith("_")):
        paths = path_by_name[name]
        records = [os.path.relpath(p, VAULT).replace(os.sep, "/") for p in paths]
        if paths and all(immutable_page(p) for p in paths):
            immutable_orphans.extend(records)
        elif paths and all(artifact_page(p) for p in paths):
            artifact_orphans.extend(records)
        else:
            orphan_paths.extend(records)
    return {"files": len(files), "dead": dead, "cross_zone": cross,
            "memory_refs": memrefs, "reference_refs": refrefs,
            "immutable_refs": immutable_refs, "orphans": orphan_paths,
            "immutable_orphans": immutable_orphans,
            "artifact_orphans": artifact_orphans,
            "duplicate_filenames": duplicate_filenames,
            "fm_gaps": fm_gaps,
            "immutable_fm_gaps": immutable_fm_gaps,
            "artifact_fm_gaps": artifact_fm_gaps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", choices=list(ZONES), default="work")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return 0
    zones = [args.zone]
    names, aliases, references = load_names(args.zone)
    result = {args.zone: scan_zone(args.zone, names, aliases, references)}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    for z in zones:
        r = result[z]
        print("\n===== ZONE %s (%d pages) =====" % (z, r["files"]))
        print("DEAD (genuinely unresolved): %d" % len(r["dead"]))
        for d in r["dead"][:30]:
            print("  %-36s -> [[%s]]" % (d["page"][:36], d["target"][:40]))
        if len(r["dead"]) > 30:
            print("  ... +%d more" % (len(r["dead"]) - 30))
        print("memory-refs: %d | reference-refs: %d | immutable-refs: %d | cross-zone: %d"
              % (len(r["memory_refs"]), len(r["reference_refs"]), len(r["immutable_refs"]), len(r["cross_zone"])))
        print("actionable orphans: %d | actionable fm-gaps: %d | L0 orphans/fm: %d/%d | artifact orphans/fm: %d/%d"
              % (len(r["orphans"]), len(r["fm_gaps"]),
                 len(r["immutable_orphans"]), len(r["immutable_fm_gaps"]),
                 len(r["artifact_orphans"]), len(r["artifact_fm_gaps"])))
        print("duplicate linkable filenames: %d" % len(r["duplicate_filenames"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
