#!/usr/bin/env python3
"""Fail-closed, zone-scoped L0-L3 retrieval over canonical Markdown."""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import subprocess
import sys
import tempfile
import stat
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


VAULT = Path(__file__).resolve().parents[1]
WIKI = VAULT / "wiki"
MODE_PATH = VAULT / ".vault-meta" / "mode.json"
APPROVALS_PATH = VAULT / ".vault-meta" / "memory-approvals.json"
LOADOUTS_PATH = VAULT / ".vault-meta" / "memory-loadouts.json"
EVALS_PATH = VAULT / ".vault-meta" / "memory-evals.json"
INDEX_DIR = VAULT / ".vault-meta" / "memory-index"
LOCK_SCRIPT = VAULT / "scripts" / "wiki-lock.sh"
L3_OWNER = "{{OWNER_NAME}}"
APPROVALS_MANIFEST_SHA256 = "0000000000000000000000000000000000000000000000000000000000000000"
EVALS_MANIFEST_SHA256 = "0000000000000000000000000000000000000000000000000000000000000000"
CONTEXT_RECEIPT_SCHEMA_VERSION = 2
CONTEXT_RESOLVER_VERSION = 1
CONTEXT_RESOLVER_PATH = "scripts/memory-model.py"
MACOS_UF_DATALESS = 0x40000000
ZONE_LAYOUT = {
    "work": {
        "root": "wiki", "index": "wiki/index.md", "log": "wiki/log.md",
        "hot": "wiki/hot.md", "projects_folder": "wiki/workspace/projects/",
        "inbox_folder": "wiki/workspace/projects/inbox/",
    },
    "personal": {
        "root": "wiki/personal", "index": "wiki/personal/_index.md",
        "log": "wiki/personal/log.md", "hot": "wiki/personal/hot.md",
        "projects_folder": "wiki/personal/projects/",
        "inbox_folder": "wiki/personal/inbox/",
    },
    "client": {
        "root": "wiki/client", "index": "wiki/client/_index.md",
        "log": "wiki/client/log.md", "hot": "wiki/client/hot.md",
        "projects_folder": "wiki/client/projects/",
        "inbox_folder": "wiki/client/inbox/",
    },
}

LEVELS = ("l0", "l1", "l2", "l3")
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
FM_SCALAR_RE = re.compile(r"(?m)^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$")
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+", re.UNICODE)
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
LOADOUT_RE = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
CANDIDATE_SLUG_RE = re.compile(r"[^a-z0-9]+")
CANDIDATE_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
CANDIDATE_SOURCE_SEPARATOR = " | "
CANDIDATE_STATEMENT_START = "## Proposed memory\n\n"
CANDIDATE_STATEMENT_END = "\n\n## Evidence\n\n"
CANDIDATE_HEADING_RE = re.compile(
    r"(?m)(?:^[ \t]{0,3}#{1,2}(?:[ \t]+|$)|^[^\n]+\n[ \t]{0,3}(?:=+|-+)[ \t]*$)"
)
STALE_MARKERS = (
    "superseded", "retired", "archived", "historical", "do-not-implement",
    "removed-route", "историч", "устарел", "не текущ", "архив",
)
L0_TYPES = {
    "agent-memory", "source", "raw-source", "transcript", "session", "journal",
    "production-log", "implementation-log", "design-log", "take-log",
    "memory-candidate",
}
L0_PATH_PARTS = {
    "sources", "incoming", "inbox", "agent-memory", "raw", "assets", "qa",
    "evidence", "prototype-v2", "prototype-dev", "versions",
}
L0_FILENAMES = {
    "readme.md", "retired.md", "version.md", "asset_manifest.md",
    "approved_asset_manifest.md", "asset_provenance.md",
}
L2_TYPE_MARKERS = (
    "index", "architecture", "playbook", "workflow", "synthesis", "handoff",
    "checkpoint", "state", "doctrine", "bible", "contract", "spec", "plan",
    "blueprint", "system", "strategy", "manifest", "registry", "roadmap",
    "brief", "pipeline",
)
EXCLUDED_PATH_PARTS = {
    "node_modules", ".git", ".next", "dist", "build", "coverage",
}
CANONICAL_HOT_PATHS = {layout["hot"] for layout in ZONE_LAYOUT.values()}

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_BUILT = 10


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def schema_version_is(value, expected):
    return (
        isinstance(value, dict)
        and type(value.get("schema_version")) is int
        and value["schema_version"] == expected
    )


def validate_mode(mode):
    if (
        not schema_version_is(mode, 1)
        or mode.get("mode") != "para"
        or mode.get("zones", {}).get("default") != "work"
        or mode.get("zones", {}).get("isolation") != "strict"
        or mode.get("memory_model", {}).get("owner") != L3_OWNER
        or mode.get("memory_model", {}).get("approval_manifest")
        != ".vault-meta/memory-approvals.json"
        or mode.get("memory_model", {}).get("loadout_manifest")
        != ".vault-meta/memory-loadouts.json"
        or mode.get("memory_model", {}).get("context_budgets") is not True
        or mode.get("memory_model", {}).get("candidate_inbox") is not True
    ):
        raise ValueError("canonical mode or memory trust configuration changed")
    for zone, expected in ZONE_LAYOUT.items():
        configured = mode.get("zones", {}).get(zone, {})
        for key in ("index", "log", "hot", "projects_folder", "inbox_folder"):
            if configured.get(key) != expected[key]:
                raise ValueError("canonical %s %s path changed" % (zone, key))
        if zone != "work" and str(configured.get("root", "")).rstrip("/") != expected["root"]:
                raise ValueError("canonical %s root changed" % zone)
    return mode


def load_mode():
    return validate_mode(load_control_json(MODE_PATH, "mode manifest"))


def load_approvals():
    raw = read_control_bytes(APPROVALS_PATH, "L3 approval manifest")
    if hashlib.sha256(raw).hexdigest() != APPROVALS_MANIFEST_SHA256:
        raise ValueError("L3 approval manifest differs from the code trust anchor")
    value = json.loads(raw)
    if not schema_version_is(value, 1):
        raise ValueError("unsupported L3 approval manifest")
    return value


def strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
        return decoded if isinstance(decoded, str) else value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def parse_text(text):
    match = FM_RE.match(text)
    if not match:
        return {}, text
    frontmatter = {
        key: strip_quotes(value)
        for key, value in FM_SCALAR_RE.findall(match.group(1))
    }
    return frontmatter, text[match.end():]


def candidate_slug(value):
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = CANDIDATE_SLUG_RE.sub("-", ascii_value.lower()).strip("-")[:60]
    return slug or "candidate-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def candidate_identifier(title, fingerprint):
    return "%s-%s" % (candidate_slug(title), fingerprint)


def validate_candidate_title(value):
    title = value.strip()
    if (
        title != value or not title or len(title) > 200
        or any(
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in title
        )
    ):
        raise ValueError("candidate title must be one safe line of at most 200 characters")
    return title


class CandidateHTMLBoundaryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.has_heading = False

    def handle_starttag(self, tag, attrs):
        self.has_heading |= tag.lower() in {"h1", "h2"}

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.has_heading |= tag.lower() in {"h1", "h2"}


def has_candidate_html_heading(value):
    parser = CandidateHTMLBoundaryParser()
    parser.feed(value)
    parser.close()
    return parser.has_heading


def validate_candidate_statement(value):
    statement = value.strip()
    if (
        statement != value or not statement or len(statement) > 8000
        or any(
            (
                unicodedata.category(character).startswith("C")
                and character not in {"\n", "\t"}
            )
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in statement
        )
        or CANDIDATE_HEADING_RE.search(statement)
        or has_candidate_html_heading(statement)
    ):
        raise ValueError("candidate statement must be plain content without H1 or H2 headings")
    return statement


def candidate_evidence_digest(sources):
    payload = json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_fingerprint(zone, project, target_level, title, statement, sources):
    payload = {
        "zone": zone,
        "project": project or "",
        "target_level": target_level,
        "title": title.strip(),
        "statement": statement.strip(),
        "sources": sorted(sources, key=lambda item: (item["path"], item["sha256"])),
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def is_candidate_shaped(frontmatter):
    return (
        frontmatter.get("type", "").strip().lower() == "memory-candidate"
        or any(key.startswith("candidate_") for key in frontmatter)
    )


def validate_candidate_project(zone, project, verify_exists=True):
    project = (project or "").strip()
    if not project:
        return None
    prefix = project_prefix(zone, project)
    if not verify_exists:
        return prefix
    project_path = VAULT / prefix
    descriptor = open_real_directory(project_path, "candidate project")
    try:
        metadata = os.stat("_index.md", dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("candidate project index must be a regular file")
        page = os.open(
            "_index.md", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        opened = os.fstat(page)
        if file_identity(opened) != file_identity(metadata):
            os.close(page)
            raise ValueError("candidate project index changed before open")
        frontmatter, _, _ = read_page_descriptor(page, project_path / "_index.md")
        current = os.stat("_index.md", dir_fd=descriptor, follow_symlinks=False)
        if file_identity(current) != file_identity(opened):
            raise ValueError("candidate project index changed after read")
        if frontmatter.get("type", "").strip().lower() != "project":
            raise ValueError("candidate project is not canonical")
    except OSError as error:
        raise ValueError("candidate project is missing or not canonical") from error
    finally:
        os.close(descriptor)
    return prefix


def validate_candidate_identity(
    zone, frontmatter, body, verify_sources=True, verify_project=True,
):
    if frontmatter.get("type", "").strip().lower() != "memory-candidate":
        raise ValueError("candidate fields require type memory-candidate")
    title = validate_candidate_title(frontmatter.get("title", ""))
    target = frontmatter.get("candidate_target_level", "").strip().lower()
    project = frontmatter.get("candidate_project", "").strip()
    candidate_id = frontmatter.get("candidate_id", "").strip()
    if target not in {"l1", "l2"} or not candidate_id:
        raise ValueError("candidate identity metadata is incomplete")
    validate_candidate_project(zone, project, verify_exists=verify_project)

    if (
        body.count(CANDIDATE_STATEMENT_START) != 1
        or body.count(CANDIDATE_STATEMENT_END) != 1
    ):
        raise ValueError("candidate Proposed memory section is missing")
    start = body.find(CANDIDATE_STATEMENT_START)
    end = body.find(CANDIDATE_STATEMENT_END, start + len(CANDIDATE_STATEMENT_START))
    statement = validate_candidate_statement(
        body[start + len(CANDIDATE_STATEMENT_START):end]
    )

    paths = [
        value for value in frontmatter.get("candidate_sources", "").split(
            CANDIDATE_SOURCE_SEPARATOR
        ) if value
    ]
    hashes = [
        value for value in frontmatter.get("candidate_source_hashes", "").split(
            CANDIDATE_SOURCE_SEPARATOR
        ) if value
    ]
    if (
        not paths or len(paths) != len(hashes)
        or any(not CANDIDATE_SHA256_RE.fullmatch(value) for value in hashes)
        or len(set(paths)) != len(paths)
        or len(set(zip(paths, hashes))) != len(paths)
    ):
        raise ValueError("candidate source identity is invalid")
    sources = sorted(
        ({"path": path, "sha256": digest} for path, digest in zip(paths, hashes)),
        key=lambda item: (item["path"], item["sha256"]),
    )
    evidence = frontmatter.get("candidate_evidence_sha256", "").strip()
    if evidence != candidate_evidence_digest(sources):
        raise ValueError("candidate evidence identity is inconsistent")
    for source in sources:
        candidate_source_path(source["path"], zone)
        if verify_sources and candidate_source_record(source["path"], zone) != source:
            raise ValueError("candidate source bytes differ from recorded evidence")

    fingerprint = candidate_fingerprint(
        zone, project, target, title, statement, sources
    )
    stored = frontmatter.get("candidate_fingerprint", "").strip()
    if stored and (
        not CANDIDATE_SHA256_RE.fullmatch(stored) or stored != fingerprint
    ):
        raise ValueError("candidate fingerprint differs from the claim payload")
    current_id = candidate_identifier(title, fingerprint)
    legacy_id = None
    if len(candidate_id) > 11 and ISO_DATE_RE.fullmatch(candidate_id[:10]):
        try:
            date.fromisoformat(candidate_id[:10])
        except ValueError:
            pass
        else:
            legacy_id = "%s-%s-%s" % (
                candidate_id[:10], candidate_slug(title), fingerprint[:10]
            )
    if candidate_id == current_id:
        if not stored:
            raise ValueError("current candidate identity is missing its fingerprint")
        schema = "current"
    elif candidate_id == legacy_id:
        schema = "predecessor"
    else:
        raise ValueError("candidate ID differs from the claim payload")
    return {
        "candidate_id": candidate_id,
        "fingerprint": fingerprint,
        "schema": schema,
        "statement": statement,
        "sources": sources,
    }


def read_page(path):
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    return read_page_descriptor(descriptor, path)


def file_identity(metadata):
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns,
    )


def content_file_identity(metadata):
    return file_identity(metadata) + (metadata.st_ctime_ns,)


def logical_file_state(metadata):
    birthtime = int(getattr(metadata, "st_birthtime", 0) * 1_000_000_000)
    return [
        metadata.st_dev, metadata.st_ino, birthtime,
        metadata.st_size, metadata.st_mtime_ns,
    ]


def content_is_dataless(metadata):
    return bool(getattr(metadata, "st_flags", 0) & MACOS_UF_DATALESS)


class PageReadDrift(ValueError):
    def __init__(self, path, raw, metadata):
        super().__init__("wiki page changed while being read: %s" % path)
        self.content_sha256 = hashlib.sha256(raw).hexdigest()
        self.anchor = (metadata.st_dev, metadata.st_ino, metadata.st_size)


def read_page_descriptor(descriptor, path):
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("wiki page is not a unique regular file: %s" % path)
        raw = handle.read()
        after = os.fstat(handle.fileno())
        if after.st_nlink != 1:
            raise ValueError("wiki page acquired a hardlink while reading: %s" % path)
        if content_file_identity(before) != content_file_identity(after):
            raise PageReadDrift(path, raw, before)
    frontmatter, body = parse_text(raw.decode("utf-8", errors="replace"))
    return frontmatter, body, hashlib.sha256(raw).hexdigest()


def relative_path(path):
    return Path(os.path.abspath(str(path))).relative_to(VAULT).as_posix()


def in_zone(path, zone, mode=None):
    mode = mode or load_mode()
    rel = relative_path(path)
    if zone == "work":
        return (
            rel.startswith("wiki/")
            and not rel.startswith("wiki/personal/")
            and not rel.startswith("wiki/client/")
        )
    root = ZONE_LAYOUT.get(zone, {}).get("root", "")
    return bool(root) and (rel == root or rel.startswith(root + "/"))


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


def candidate_source_path(value, zone):
    if (
        not value or Path(value).is_absolute()
        or CANDIDATE_SOURCE_SEPARATOR in value or '"' in value or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("candidate source path contains unsupported characters")
    absolute = Path(os.path.abspath(str(VAULT / value)))
    try:
        relative = relative_path(absolute)
    except ValueError as error:
        raise ValueError("candidate source is outside the vault") from error
    if relative != value or not in_zone(absolute, zone):
        raise ValueError("candidate source is outside the active zone")
    return absolute, relative


def candidate_source_record(value, zone):
    _, relative = candidate_source_path(value, zone)
    parts = Path(relative).parts
    directory = open_real_directory(VAULT, "vault root")
    try:
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for part in parts[:-1]:
            if part not in os.listdir(directory):
                raise ValueError("candidate source path casing is not canonical")
            child = os.open(part, flags, dir_fd=directory)
            opened_directory = os.fstat(child)
            if not stat.S_ISDIR(opened_directory.st_mode):
                os.close(child)
                raise ValueError("candidate source path contains a non-directory")
            os.close(directory)
            directory = child
        filename = parts[-1]
        if filename not in os.listdir(directory):
            raise ValueError("candidate source filename casing is not canonical")
        before = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("candidate source must be a canonical regular file")
        descriptor = os.open(
            filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if file_identity(opened) != file_identity(before) or opened.st_nlink != 1:
                raise ValueError("candidate source changed before open")
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
            if file_identity(after) != file_identity(opened) or after.st_nlink != 1:
                raise ValueError("candidate source changed while being read")
        current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        if file_identity(current) != file_identity(opened) or current.st_nlink != 1:
            raise ValueError("candidate source changed after read")
    except (FileNotFoundError, OSError) as error:
        raise ValueError("candidate source is missing or not canonical") from error
    finally:
        os.close(directory)
    return {"path": relative, "sha256": digest.hexdigest()}


def read_control_bytes(path, label):
    directory = open_real_directory(path.parent, label + " directory")
    try:
        first_raw = None
        anchor = None
        for attempt in range(2):
            before = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("%s must be a regular file" % label)
            descriptor = os.open(
                path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                raw = handle.read()
                after = os.fstat(handle.fileno())
            anchors = {
                (item.st_dev, item.st_ino, item.st_size)
                for item in (before, opened, after)
            }
            identities = {
                file_identity(item) for item in (before, opened, after)
            }
            if len(anchors) != 1:
                raise ValueError("%s changed while being read" % label)
            current_anchor = anchors.pop()
            if len(identities) != 1:
                if attempt:
                    raise ValueError("%s changed while being read" % label)
                first_raw = raw
                anchor = current_anchor
                continue
            if first_raw is not None and (
                raw != first_raw or current_anchor != anchor
            ):
                raise ValueError("%s changed during retry" % label)
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if file_identity(current) != file_identity(after):
                raise ValueError("%s changed after read" % label)
            return raw
        raise ValueError("%s did not stabilize" % label)
    finally:
        os.close(directory)


def load_control_json(path, label):
    value = json.loads(read_control_bytes(path, label))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object: %s" % path)
    return value


def validate_skill_path(relative):
    if not isinstance(relative, str) or not relative.endswith("/SKILL.md"):
        raise ValueError("pinned skill must name a vault-local SKILL.md")
    if not (
        relative.startswith(".agents/skills/")
        or relative.startswith(".claude/skills/")
    ):
        raise ValueError("pinned skill is outside the approved skill roots")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("pinned skill path is unsafe")
    return path


def skill_metadata(relative):
    path = validate_skill_path(relative)
    raw = read_control_bytes(VAULT / path, "pinned skill")
    return {
        "name": path.parent.name,
        "path": path.as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def validate_loadouts(value):
    if (
        not schema_version_is(value, 1)
        or not isinstance(value.get("roles"), dict)
    ):
        raise ValueError("unsupported memory loadout manifest")
    default = value.get("default")
    roles = value["roles"]
    if default not in roles:
        raise ValueError("default memory loadout is missing")
    required = {
        "description", "project_required", "levels", "allow_l0", "max_items",
        "max_chars", "snippet_chars", "per_level", "l3_max_items",
        "type_boosts", "pinned_skills",
    }
    for name, role in roles.items():
        if not isinstance(name, str) or not LOADOUT_RE.fullmatch(name):
            raise ValueError("invalid memory loadout name")
        if not isinstance(role, dict) or not required.issubset(role):
            raise ValueError("incomplete memory loadout: %s" % name)
        if not isinstance(role["description"], str) or not role["description"].strip():
            raise ValueError("memory loadout description is required: %s" % name)
        if not isinstance(role["project_required"], bool) or not isinstance(role["allow_l0"], bool):
            raise ValueError("memory loadout boolean is invalid: %s" % name)
        levels = role["levels"]
        if (
            not isinstance(levels, list) or not levels
            or any(level not in {"l1", "l2"} for level in levels)
            or len(levels) != len(set(levels))
        ):
            raise ValueError("memory loadout levels are invalid: %s" % name)
        for field, minimum, maximum in (
            ("max_items", 1, 20), ("max_chars", 500, 20000),
            ("snippet_chars", 60, 2000), ("l3_max_items", 0, 8),
        ):
            number = role[field]
            if isinstance(number, bool) or not isinstance(number, int) or not minimum <= number <= maximum:
                raise ValueError("memory loadout %s is invalid: %s" % (field, name))
        if role["snippet_chars"] > role["max_chars"]:
            raise ValueError("snippet budget exceeds context budget: %s" % name)
        quotas = role["per_level"]
        if not isinstance(quotas, dict) or set(quotas) != {"l0", "l1", "l2"}:
            raise ValueError("memory loadout level quotas are invalid: %s" % name)
        if any(
            isinstance(limit, bool) or not isinstance(limit, int)
            or not 0 <= limit <= role["max_items"]
            for limit in quotas.values()
        ):
            raise ValueError("memory loadout quota is invalid: %s" % name)
        boosts = role["type_boosts"]
        if not isinstance(boosts, dict) or any(
            not isinstance(page_type, str) or not page_type
            or isinstance(boost, bool) or not isinstance(boost, (int, float))
            or not 0.5 <= boost <= 2.0
            for page_type, boost in boosts.items()
        ):
            raise ValueError("memory loadout type boosts are invalid: %s" % name)
        skills = role["pinned_skills"]
        if not isinstance(skills, list) or len(skills) > 8 or len(skills) != len(set(skills)):
            raise ValueError("memory loadout pinned skills are invalid: %s" % name)
        for skill in skills:
            validate_skill_path(skill)
    return value


def load_loadouts():
    raw = read_control_bytes(LOADOUTS_PATH, "memory loadout manifest")
    value = validate_loadouts(json.loads(raw))
    identity = hashlib.sha256()
    identity.update(b"memory-loadout-v2\0")
    identity.update(raw)
    for role_name in sorted(value["roles"]):
        for path in sorted(value["roles"][role_name]["pinned_skills"]):
            metadata = skill_metadata(path)
            identity.update(b"\0")
            identity.update(role_name.encode("utf-8"))
            identity.update(b"\0")
            identity.update(path.encode("utf-8"))
            identity.update(b"\0")
            identity.update(metadata["sha256"].encode("ascii"))
    return value, identity.hexdigest()


def load_evals():
    raw = read_control_bytes(EVALS_PATH, "memory evaluation manifest")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EVALS_MANIFEST_SHA256:
        raise ValueError("memory evaluation manifest differs from the code trust anchor")
    value = json.loads(raw)
    cases = value.get("cases") if isinstance(value, dict) else None
    if (
        not schema_version_is(value, 1)
        or value.get("zone") != "work" or not isinstance(cases, list)
    ):
        raise ValueError("unsupported memory evaluation manifest")
    seen = set()
    for case in cases:
        required = {
            "id", "query", "loadout", "top", "must_paths", "must_contain",
            "must_not_contain", "path_assertions", "expected_public_sha256",
        }
        optional = {
            "project", "include_l0", "include_stale", "required_levels",
            "require_stale", "forbidden_path_prefixes",
        }
        if (
            not isinstance(case, dict) or not required.issubset(case)
            or not set(case).issubset(required | optional)
        ):
            raise ValueError("incomplete memory evaluation case")
        case_id = case["id"]
        if (
            not isinstance(case_id, str) or not LOADOUT_RE.fullmatch(case_id)
            or case_id in seen
        ):
            raise ValueError("invalid or duplicate memory evaluation id")
        seen.add(case_id)
        if not isinstance(case["query"], str) or not case["query"].strip():
            raise ValueError("memory evaluation query is required")
        if not isinstance(case["loadout"], str) or not LOADOUT_RE.fullmatch(case["loadout"]):
            raise ValueError("memory evaluation loadout is invalid")
        if not isinstance(case["top"], int) or isinstance(case["top"], bool) or not 1 <= case["top"] <= 20:
            raise ValueError("memory evaluation top is invalid")
        if (
            type(case["expected_public_sha256"]) is not str
            or not CANDIDATE_SHA256_RE.fullmatch(case["expected_public_sha256"])
        ):
            raise ValueError("memory evaluation public-result hash is invalid")
        project = case.get("project")
        if project is not None and (not isinstance(project, str) or not PROJECT_RE.fullmatch(project)):
            raise ValueError("memory evaluation project is invalid")
        if any(type(case.get(field, False)) is not bool for field in (
            "include_l0", "include_stale", "require_stale",
        )) or (case.get("require_stale", False) and not case.get("include_stale", False)):
            raise ValueError("memory evaluation retrieval mode is invalid")
        required_levels = case.get("required_levels", [])
        if (
            not isinstance(required_levels, list)
            or len(required_levels) != len(set(required_levels))
            or any(level not in {"l0", "l1", "l2"} for level in required_levels)
            or ("l0" in required_levels and not case.get("include_l0", False))
        ):
            raise ValueError("memory evaluation required levels are invalid")
        forbidden_prefixes = case.get("forbidden_path_prefixes", [])
        if (
            not isinstance(forbidden_prefixes, list)
            or len(forbidden_prefixes) != len(set(forbidden_prefixes))
            or any(
                not isinstance(prefix, str) or not prefix.startswith("wiki/")
                or prefix.startswith(("wiki/personal/", "wiki/client/"))
                for prefix in forbidden_prefixes
            )
        ):
            raise ValueError("memory evaluation forbidden paths are invalid")
        for field in ("must_paths", "must_contain", "must_not_contain"):
            items = case[field]
            if (
                not isinstance(items, list)
                or (field != "must_not_contain" and not items)
                or len(items) != len(set(items))
                or any(not isinstance(item, str) or not item for item in items)
            ):
                raise ValueError("memory evaluation %s is invalid" % field)
        if any(
            not path.startswith("wiki/")
            or path.startswith(("wiki/personal/", "wiki/client/"))
            for path in case["must_paths"]
        ):
            raise ValueError("memory evaluation path leaves the work zone")
        assertions = case["path_assertions"]
        if not isinstance(assertions, list) or not assertions:
            raise ValueError("memory evaluation path assertions are invalid")
        asserted_paths = set()
        for assertion in assertions:
            if not isinstance(assertion, dict) or set(assertion) != {
                "path", "must_contain", "must_not_contain",
            }:
                raise ValueError("memory evaluation path assertion is invalid")
            path = assertion["path"]
            if path in asserted_paths or path not in case["must_paths"]:
                raise ValueError("memory evaluation asserted path is invalid")
            asserted_paths.add(path)
            if (
                not isinstance(assertion["must_contain"], list)
                or not assertion["must_contain"]
                or not isinstance(assertion["must_not_contain"], list)
                or any(
                    not isinstance(item, str) or not item
                    for field in ("must_contain", "must_not_contain")
                    for item in assertion[field]
                )
            ):
                raise ValueError("memory evaluation path terms are invalid")
    return value, digest


def candidate_root(zone):
    load_mode()
    if zone not in ZONE_LAYOUT:
        raise ValueError("unknown zone: %s" % zone)
    return VAULT / ZONE_LAYOUT[zone]["inbox_folder"] / "memory-candidates"


def canonical_zone_root(zone):
    if zone not in ZONE_LAYOUT:
        raise ValueError("unknown zone: %s" % zone)
    root = VAULT / ZONE_LAYOUT[zone]["root"]
    descriptor = open_real_directory(root, "zone root")
    os.close(descriptor)
    return root


def zone_records(zone, read_content=True):
    load_mode()
    root = canonical_zone_root(zone)
    excluded_roots = {"personal", "client"} if zone == "work" else set()
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    records = []

    def verify_directory(path, descriptor):
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("zone directory changed during traversal: %s" % path)

    def visit(descriptor, directory, root_level=False):
        try:
            for name in sorted(os.listdir(descriptor)):
                if (
                    name.startswith(".") or name in EXCLUDED_PATH_PARTS
                    or (root_level and name in excluded_roots)
                ):
                    continue
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                path = directory / name
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError("symlink is not allowed in the active zone: %s" % path)
                if stat.S_ISDIR(metadata.st_mode):
                    child = os.open(name, directory_flags, dir_fd=descriptor)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (
                            metadata.st_dev, metadata.st_ino
                        ):
                            raise ValueError("zone directory changed before open: %s" % path)
                        visit(child, path)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(metadata.st_mode) and name.endswith(".md"):
                    if metadata.st_nlink != 1:
                        raise ValueError("wiki page is not a unique regular file: %s" % path)
                    if not read_content:
                        records.append((
                            path, None, None, None,
                            logical_file_state(metadata),
                        ))
                        continue
                    if content_is_dataless(metadata):
                        raise ValueError(
                            "wiki page is not local; choose Keep Downloaded before full scan: %s"
                            % path
                        )
                    page = os.open(
                        name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=descriptor,
                    )
                    opened = os.fstat(page)
                    if content_file_identity(opened) != content_file_identity(metadata):
                        os.close(page)
                        raise ValueError("wiki page changed before open: %s" % path)
                    try:
                        record = read_page_descriptor(page, path)
                    except PageReadDrift as error:
                        retry_metadata = os.stat(
                            name, dir_fd=descriptor, follow_symlinks=False
                        )
                        anchor = (opened.st_dev, opened.st_ino, opened.st_size)
                        if (
                            error.anchor != anchor
                            or
                            not stat.S_ISREG(retry_metadata.st_mode)
                            or retry_metadata.st_nlink != 1
                            or (
                                retry_metadata.st_dev,
                                retry_metadata.st_ino,
                                retry_metadata.st_size,
                            ) != anchor
                        ):
                            raise
                        retry = os.open(
                            name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=descriptor,
                        )
                        retry_opened = os.fstat(retry)
                        if (
                            retry_opened.st_dev,
                            retry_opened.st_ino,
                            retry_opened.st_size,
                        ) != anchor or retry_opened.st_nlink != 1:
                            os.close(retry)
                            raise ValueError("wiki page changed before retry: %s" % path)
                        record = read_page_descriptor(retry, path)
                        if record[2] != error.content_sha256:
                            raise ValueError(
                                "wiki page content changed during retry: %s" % path
                            )
                        opened = retry_opened
                    current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if (
                        current.st_nlink != 1
                        or content_file_identity(current)
                        != content_file_identity(opened)
                    ):
                        raise ValueError("wiki page changed after read: %s" % path)
                    records.append((
                        path, *record,
                        logical_file_state(current),
                    ))
            verify_directory(directory, descriptor)
        except OSError as error:
            raise ValueError("zone changed during traversal: %s" % directory) from error

    root_descriptor = open_real_directory(root, "zone root")
    try:
        visit(root_descriptor, root, root_level=True)
    finally:
        os.close(root_descriptor)
    return sorted(records, key=lambda item: relative_path(item[0]))


def records_file_state(records):
    return {
        relative_path(path): state
        for path, _frontmatter, _body, _page_hash, state in records
    }


def zone_file_state(zone):
    return records_file_state(zone_records(zone, read_content=False))


def classify(path, frontmatter=None):
    frontmatter = frontmatter or {}
    rel = relative_path(path)
    name = path.name.lower()
    parts = {part.lower() for part in Path(rel).parts}
    explicit = frontmatter.get("memory_level", "").strip().lower()
    issues = []
    approval_mode = frontmatter.get("memory_approval_mode", "").strip().lower()
    promotion_eligible = frontmatter.get("promotion_eligible", "").strip().lower()
    provisional = approval_mode == "provisional" and promotion_eligible == "false"
    if approval_mode or promotion_eligible:
        if explicit != "l2" or not provisional:
            issues.append("invalid-provisional-memory-contract")

    cache_role = frontmatter.get("memory_role", "").strip().lower() == "cache"
    if name == "hot.md" or cache_role:
        if rel not in CANONICAL_HOT_PATHS:
            issues.append("cache-must-use-zone-hot-path")
        if explicit:
            issues.append("hot-cache-cannot-have-memory-level")
        return {
            "level": "cache", "source": "inferred", "reason": "hot-cache",
            "trust": "cache", "invalid": explicit or None, "issues": issues,
            "path": rel,
        }

    page_type = frontmatter.get("type", "").strip().lower()
    candidate_prefixes = tuple(
        layout["inbox_folder"] + "memory-candidates/"
        for layout in ZONE_LAYOUT.values()
    )
    has_candidate_fields = is_candidate_shaped(frontmatter)
    if has_candidate_fields and not rel.startswith(candidate_prefixes):
        issues.append("memory-candidate-must-use-zone-inbox")
    raw_reason = None
    if (
        name == "log.md" or name in L0_FILENAMES or name.startswith("out_")
        or parts.intersection(L0_PATH_PARTS)
    ):
        raw_reason = "raw-path"
    elif page_type in L0_TYPES:
        raw_reason = "raw-type"

    if raw_reason:
        if explicit and explicit != "l0":
            issues.append("evidence-cannot-self-promote")
        if explicit and explicit not in LEVELS:
            issues.append("invalid-memory-level")
        return {
            "level": "l0", "source": "explicit" if explicit == "l0" else "inferred",
            "reason": raw_reason, "trust": "evidence",
            "invalid": explicit if explicit and explicit != "l0" else None,
            "issues": issues, "path": rel,
        }

    if explicit:
        if explicit in LEVELS:
            return {
                "level": explicit, "source": "explicit", "reason": "frontmatter",
                "trust": (
                    "approval-required" if explicit == "l3"
                    else "provisional-synthesis" if provisional
                    else "explicit"
                ),
                "invalid": None, "issues": issues, "path": rel,
            }
        issues.append("invalid-memory-level")

    if name in {"index.md", "_index.md", "overview.md"}:
        level, reason = "l2", "navigation-state"
    elif any(marker in (page_type + " " + path.stem.lower()) for marker in L2_TYPE_MARKERS):
        level, reason = "l2", "scenario-type"
    else:
        level, reason = "l1", "durable-default"
    return {
        "level": level, "source": "inferred", "reason": reason,
        "trust": "legacy-unverified", "invalid": explicit or None,
        "issues": issues, "path": rel,
    }


def l3_approval(path, frontmatter, approvals=None, mode=None, page_hash=None):
    approvals = approvals or load_approvals()
    if mode is None:
        load_mode()
    owner = L3_OWNER
    approved_by = frontmatter.get("memory_approved_by", "")
    approved_on = frontmatter.get("memory_approved", "")
    if not isinstance(approved_by, str) or not isinstance(approved_on, str):
        return False, "invalid-owner-or-date"
    approved_by = approved_by.strip()
    approved_on = approved_on.strip()
    if not owner or approved_by != owner or not ISO_DATE_RE.fullmatch(approved_on):
        return False, "invalid-owner-or-date"
    try:
        approval_date = date.fromisoformat(approved_on)
    except ValueError:
        return False, "invalid-approval-date"
    if approval_date > date.today():
        return False, "invalid-approval-date"
    record = approvals.get("l3", {}).get(relative_path(path))
    if not isinstance(record, dict):
        return False, "missing-approval-manifest"
    actual_hash = page_hash
    if actual_hash is None:
        _, _, actual_hash = read_page(path)
    if (
        record.get("sha256") != actual_hash
        or record.get("approved_by") != owner
        or record.get("approved_on") != approved_on
    ):
        return False, "approval-manifest-mismatch"
    return True, "owner-approved-manifest"


def status_is_stale(status):
    normalized = (status or "").strip().lower()
    return any(marker in normalized for marker in STALE_MARKERS)


def duplicate_linkable_filenames(page_levels):
    grouped = defaultdict(list)
    for path, level in page_levels:
        grouped[path.name.casefold()].append({
            "path": relative_path(path), "level": level,
        })
    return [
        {"filename": records[0]["path"].rsplit("/", 1)[-1], "pages": records}
        for filename, records in sorted(grouped.items())
        if len(records) > 1
        and filename != "_index.md"
        and any(record["level"] != "l0" for record in records)
    ]


def canonical_cache_issue(zone, cache_paths):
    expected = ZONE_LAYOUT[zone]["hot"]
    if cache_paths.count(expected) == 1:
        return None
    return {
        "path": expected, "value": None, "fallback": "cache",
        "issues": ["exactly-one-canonical-hot-required"],
    }


def scan_zone(zone, records=None):
    counts = Counter()
    explicit = Counter()
    invalid = []
    l3_without_approval = []
    provenance_issues = []
    l2_without_provenance = []
    legacy_unverified = 0
    pages = zone_records(zone) if records is None else records
    page_levels = []
    approvals = load_approvals()
    mode = load_mode()
    cache_paths = []

    for path, frontmatter, body, page_hash, _state in pages:
        result = classify(path, frontmatter)
        if is_candidate_shaped(frontmatter):
            try:
                validate_candidate_identity(zone, frontmatter, body)
            except ValueError:
                result["issues"].append("invalid-memory-candidate-identity")
        if result["trust"] == "legacy-unverified":
            legacy_unverified += 1
        if result["level"] == "cache":
            cache_paths.append(relative_path(path))
        page_levels.append((path, result["level"]))
        counts[result["level"]] += 1
        if result["source"] == "explicit" and result["level"] in LEVELS:
            explicit[result["level"]] += 1
        if result.get("invalid") or result["issues"]:
            invalid.append({
                "path": relative_path(path), "value": result.get("invalid"),
                "fallback": result["level"], "issues": result["issues"],
            })
        if result["level"] == "l1" and result["source"] == "explicit":
            if not frontmatter.get("memory_provenance", "").strip():
                provenance_issues.append(relative_path(path))
        if result["level"] == "l2" and result["source"] == "explicit":
            if not frontmatter.get("memory_provenance", "").strip():
                l2_without_provenance.append(relative_path(path))
        if result["level"] == "l3":
            approved, reason = l3_approval(
                path, frontmatter, approvals, mode, page_hash
            )
            if not approved:
                l3_without_approval.append({"path": relative_path(path), "reason": reason})

    cache_issue = canonical_cache_issue(zone, cache_paths)
    if cache_issue:
        invalid.append(cache_issue)

    duplicate_filenames = duplicate_linkable_filenames(page_levels)
    issue_count = (
        len(invalid) + len(l3_without_approval) + len(provenance_issues)
        + len(duplicate_filenames)
    )
    return {
        "schema_version": 2,
        "zone": zone,
        "pages": len(pages),
        "counts": {key: counts.get(key, 0) for key in LEVELS + ("cache",)},
        "explicit": {key: explicit.get(key, 0) for key in LEVELS},
        "legacy_inferred": sum(counts.values()) - sum(explicit.values()),
        "legacy_unverified": legacy_unverified,
        "invalid_levels": invalid,
        "l1_without_provenance": provenance_issues,
        "l2_without_provenance": l2_without_provenance,
        "l3_without_approval": l3_without_approval,
        "duplicate_linkable_filenames": duplicate_filenames,
        "issue_count": issue_count,
    }


def tokenize(text):
    return [token.lower() for token in TOKEN_RE.findall(text)]


def split_block(block, max_chars):
    pieces = []
    remaining = block.strip()
    while len(remaining) > max_chars:
        boundary = max(
            remaining.rfind("\n", 0, max_chars + 1),
            remaining.rfind(" ", 0, max_chars + 1),
            remaining.rfind("\t", 0, max_chars + 1),
        )
        if boundary <= 0:
            crossing = next(
                (
                    match for match in TOKEN_RE.finditer(remaining)
                    if match.start() < max_chars < match.end()
                ),
                None,
            )
            boundary = (
                crossing.start() if crossing and crossing.start() > 0
                else crossing.end() if crossing
                else max_chars
            )
        piece = remaining[:boundary].strip()
        if not piece:
            boundary = max_chars
            piece = remaining[:boundary]
        pieces.append(piece)
        remaining = remaining[boundary:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def chunk_body(body, max_chars=1600):
    blocks = [part.strip() for part in re.split(r"\n(?=#{1,6}\s)|\n\s*\n", body) if part.strip()]
    chunks = []
    current = ""
    current_stale = False
    heading_state = {}
    for block in blocks:
        heading = re.match(r"^(#{1,6})\s+(.*)", block)
        if heading:
            level = len(heading.group(1))
            heading_state = {key: value for key, value in heading_state.items() if key < level}
            heading_state[level] = status_is_stale(heading.group(2)) or any(heading_state.values())
        block_stale = any(heading_state.values())
        if heading and current:
            chunks.append((current, current_stale))
            current = ""
        pieces = split_block(block, max_chars) or [""]
        for piece in pieces:
            candidate = (current + "\n\n" + piece).strip() if current else piece
            if current and (len(candidate) > max_chars or block_stale != current_stale):
                chunks.append((current, current_stale))
                current = piece
                current_stale = block_stale
            else:
                current = candidate
                current_stale = block_stale
    if current:
        chunks.append((current, current_stale))
    return chunks


def query_snippet(text, query_terms, max_chars=500):
    text = text.strip()
    if max_chars < 1:
        raise ValueError("snippet size must be positive")
    if len(text) <= max_chars:
        return text
    decorate = max_chars > 4
    content_chars = max_chars - 4 if decorate else max_chars
    terms = {term.casefold() for term in query_terms if term}
    tokens = [
        (match.start(), match.end(), match.group(0).casefold())
        for match in TOKEN_RE.finditer(text)
    ]
    matches = [item for item in tokens if item[2] in terms]
    windows = {0: min(len(text), content_chars)}

    def aligned_start(token_start, token_end):
        span = token_end - token_start
        if span >= content_chars:
            return token_start
        context = min(80, (content_chars - span) // 2)
        start = max(0, token_start - context)
        floor = max(0, start - 160)
        paragraph = text.rfind("\n\n", floor, start)
        if paragraph >= 0:
            start = paragraph + 2
        else:
            line = text.rfind("\n", floor, start)
            if line >= 0:
                start = line + 1
            else:
                word = text.rfind(" ", max(0, start - 30), start)
                if word >= 0:
                    start = word + 1
        start = max(start, token_end - content_chars)
        crossing = next(
            (
                (token_start, token_end) for token_start, token_end, _ in tokens
                if token_start < start < token_end
            ),
            None,
        )
        if crossing is None:
            return start
        return crossing[1] if crossing[1] <= token_start else crossing[0]

    for token_start, token_end, _ in matches:
        start = aligned_start(token_start, token_end)
        windows[start] = min(len(text), start + content_chars)

    def score(start):
        window_end = windows[start]
        full_terms = [
            term for token_start, token_end, term in matches
            if token_start >= start and token_end <= window_end
        ]
        overlapping_terms = [
            term for token_start, token_end, term in matches
            if token_start >= start and token_start < window_end
        ]
        return (
            len(set(full_terms)),
            len(full_terms),
            len(set(overlapping_terms)),
            len(overlapping_terms),
            -start,
        )

    start = max(windows, key=score)
    end = windows[start]
    required_end = max(
        (
            token_end for token_start, token_end, _ in matches
            if token_start >= start and token_start < end
        ),
        default=start,
    )
    required_end = min(required_end, end)
    clipped_right = end < len(text)
    if clipped_right:
        floor = start + content_chars // 2
        boundary = max(
            text.rfind("\n\n", floor, end),
            text.rfind("\n", floor, end),
            text.rfind(" ", floor, end),
        )
        if boundary > start and boundary >= required_end:
            end = boundary
        else:
            crossing = next(
                (
                    (token_start, token_end)
                    for token_start, token_end, _ in tokens
                    if token_start < end < token_end
                ),
                None,
            )
            if crossing:
                if crossing[0] >= required_end and crossing[0] > start:
                    end = crossing[0]
    clipped_right = end < len(text)
    snippet = text[start:end].strip()
    if decorate and start:
        snippet = "… " + snippet
    if decorate and clipped_right:
        snippet += " …"
    if len(snippet) > max_chars:
        raise AssertionError("snippet exceeded its hard character budget")
    return snippet


def snapshot_digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolver_identity():
    raw = read_control_bytes(Path(__file__).resolve(), "memory resolver")
    return {
        "version": CONTEXT_RESOLVER_VERSION,
        "path": CONTEXT_RESOLVER_PATH,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def compose_snapshot(zone, records=None):
    docs = {}
    postings = defaultdict(list)
    page_hashes = {}
    pages_indexed = 0
    approvals = load_approvals()
    mode = load_mode()

    pages = zone_records(zone) if records is None else records
    for page, frontmatter, body, page_hash, _state in pages:
        memory = classify(page, frontmatter)
        if memory["level"] == "cache":
            continue
        page_path = relative_path(page)
        page_hashes[page_path] = page_hash
        if not body.strip():
            continue
        approved = False
        if memory["level"] == "l3":
            approved, _ = l3_approval(
                page, frontmatter, approvals, mode, page_hash
            )
        if approved:
            trust = "approved"
        elif memory["level"] == "l2" and memory["source"] == "explicit":
            trust = (
                "provisional-synthesis"
                if memory["trust"] == "provisional-synthesis"
                else "verified-synthesis"
                if frontmatter.get("memory_provenance", "").strip()
                else "explicit-unverified"
            )
        else:
            trust = memory["trust"]
        title = frontmatter.get("title", "").strip() or page.stem
        page_type = frontmatter.get("type", "").strip().lower()
        candidate_identity = (
            validate_candidate_identity(zone, frontmatter, body)
            if is_candidate_shaped(frontmatter) else None
        )
        status = frontmatter.get("status", "").strip()
        if not status and status_is_stale(page_path):
            status = "inferred-stale-path"
        first_paragraph = next(
            (part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()), ""
        )[:300]
        prefix = (
            "Memory level %s. Trust: %s. Wiki page \"%s\". Status: %s. The page opens: %s"
            % (memory["level"].upper(), trust, title, status or "unspecified", first_paragraph)
        )
        chunks = chunk_body(body)
        if not chunks:
            continue
        pages_indexed += 1
        for chunk_index, (raw, section_stale) in enumerate(chunks):
            chunk_stale = status_is_stale(status) or section_stale
            chunk_id = hashlib.sha256(
                (page_path + ":" + str(chunk_index)).encode("utf-8")
            ).hexdigest()[:20]
            tokens = tokenize(prefix + "\n\n" + raw)
            docs[chunk_id] = {
                "page_path": page_path,
                "page_sha256": page_hash,
                "chunk_index": chunk_index,
                "chunk_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "title": title,
                "page_type": page_type,
                "status": status,
                "memory_level": memory["level"],
                "memory_source": memory["source"],
                "memory_reason": memory["reason"],
                "memory_trust": trust,
                "l3_approved": approved,
                "chunk_stale": chunk_stale,
                "candidate_status": frontmatter.get("candidate_status", "").strip().lower(),
                "candidate_target_level": frontmatter.get("candidate_target_level", "").strip().lower(),
                "candidate_project": frontmatter.get("candidate_project", "").strip(),
                "candidate_evidence_sha256": frontmatter.get("candidate_evidence_sha256", "").strip(),
                "candidate_fingerprint": (
                    candidate_identity["fingerprint"] if candidate_identity else ""
                ),
                "dl": len(tokens),
                "text": raw,
            }
            for term, count in Counter(tokens).items():
                postings[term].append([chunk_id, count])
    return {
        "page_count": pages_indexed,
        "doc_count": len(docs),
        "page_hashes": page_hashes,
        "docs": docs,
        "vocab": {term: postings[term] for term in sorted(postings)},
    }


def index_path(zone):
    return INDEX_DIR / (zone + ".json")


def index_authority_path(zone):
    return INDEX_DIR / (zone + ".authority.json")


def canonical_index_bytes(payload):
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def index_authority(index):
    return {
        "schema_version": 1,
        "zone": index["zone"],
        "index_sha256": hashlib.sha256(canonical_index_bytes(index)).hexdigest(),
        "snapshot_sha256": index["snapshot_sha256"],
        "file_state_sha256": index["file_state_sha256"],
        "loadouts_sha256": index["loadouts_sha256"],
        "approvals_sha256": index["approvals_sha256"],
        "resolver": index["resolver"],
    }


def checkpoint_index_authority(zone):
    relative = relative_path(index_authority_path(zone))
    result = subprocess.run(
        ["git", "show", "HEAD:" + relative], cwd=VAULT,
        capture_output=True, timeout=15,
    )
    if result.returncode:
        raise ValueError("memory index authority is not checkpointed")
    return result.stdout


def load_index_authority(zone, require_checkpoint=True):
    path = index_authority_path(zone)
    raw = read_control_bytes(path, "memory index authority")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("memory index authority is invalid") from error
    if require_checkpoint and raw != checkpoint_index_authority(zone):
        raise ValueError("memory index authority differs from the local checkpoint")
    return value


def verify_index_directory(descriptor):
    opened = os.fstat(descriptor)
    current = os.stat(INDEX_DIR, follow_symlinks=False)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or directory_identity(opened) != directory_identity(current)
    ):
        raise ValueError("memory index directory changed")


def cleanup_index_artifacts(
    directory, stem, suffixes, remove=True, exclude=(),
):
    pattern = re.compile(
        r"\.%s-[0-9a-f]{24}\.(?:%s)\Z"
        % (re.escape(stem), "|".join(map(re.escape, suffixes)))
    )
    removed = False
    for name in os.listdir(directory):
        if not pattern.fullmatch(name) or name in exclude:
            continue
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("memory index transaction artifact is unsafe")
        if remove:
            os.unlink(name, dir_fd=directory)
            removed = True
    if removed:
        os.fsync(directory)


def index_artifact_matches(directory, name, expected_identity, expected_sha256):
    try:
        descriptor = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory,
        )
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read()
            after = os.fstat(handle.fileno())
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return (
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == after.st_nlink == current.st_nlink == 1
        and file_identity(before) == file_identity(after)
        == file_identity(current) == expected_identity
        and hashlib.sha256(raw).hexdigest() == expected_sha256
    )


def open_index_directory(create=False):
    parent = INDEX_DIR.parent
    parent_descriptor = open_real_directory(parent, "memory metadata directory")
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        try:
            descriptor = os.open(INDEX_DIR.name, flags, dir_fd=parent_descriptor)
        except FileNotFoundError:
            if not create:
                raise
            os.mkdir(INDEX_DIR.name, 0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            descriptor = os.open(INDEX_DIR.name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ValueError("memory index directory must be a real directory") from error
    finally:
        os.close(parent_descriptor)
    try:
        verify_index_directory(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def index_lock(zone):
    directory = open_index_directory(create=True)
    try:
        descriptor = os.open(
            zone + ".lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        handle = os.fdopen(descriptor, "a+")
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            handle.close()
            raise ValueError("memory index lock must be a regular file")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        verify_index_directory(directory)
        return handle, directory
    except Exception:
        os.close(directory)
        raise


def zone_lock_path(zone):
    return ".vault-meta/write/%s" % zone


def lock_command(*arguments):
    return subprocess.run(
        ["bash", str(LOCK_SCRIPT), *arguments],
        capture_output=True, text=True, timeout=15,
    )


def require_zone_lock(zone, token):
    result = lock_command("check", zone_lock_path(zone), token)
    if result.returncode:
        raise ValueError("active owner token required for zone build")


def load_lock_api():
    specification = importlib.util.spec_from_file_location(
        "vault_wiki_lock", VAULT / "scripts" / "wiki-lock.py"
    )
    if specification is None or specification.loader is None:
        raise ValueError("wiki lock API is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@contextlib.contextmanager
def zone_commit_guard(zone, token):
    api = load_lock_api()
    handle, directory = api.meta_lock()

    def check():
        return api.require_owner(zone_lock_path(zone), token, directory=directory)

    try:
        check()
        yield check
        check()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        os.close(directory)


def acquire_zone_lock(zone):
    result = lock_command("acquire", zone_lock_path(zone), "--ttl", "21600")
    if result.returncode:
        raise ValueError("zone write lock is held; build must wait or use its owner token")
    token = result.stdout.strip()
    if not token:
        raise ValueError("zone write lock did not return an owner token")
    return token


def release_zone_lock(zone, token):
    result = lock_command("release", zone_lock_path(zone), token)
    if result.returncode:
        raise ValueError("failed to release automatic zone write lock")


def zone_write_state(zone):
    result = lock_command("peek", zone_lock_path(zone))
    if result.returncode:
        raise ValueError("zone write lock state is unavailable")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        if result.stdout.strip() == "unheld":
            return False, 0
        raise ValueError("invalid zone write lock state")
    generation = payload.get("generation", 0)
    if not isinstance(generation, int) or generation < 0:
        raise ValueError("invalid zone write generation")
    return bool(payload.get("active")), generation


def index_identity(zone):
    directory = open_index_directory()
    try:
        metadata = os.stat(
            zone + ".json", dir_fd=directory, follow_symlinks=False
        )
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("memory index must be a unique regular file")
        return file_identity(metadata)
    finally:
        os.close(directory)


def atomic_index(
    path, payload, directory=None, before_replace=None, after_replace=None,
):
    if path.parent != INDEX_DIR:
        raise ValueError("memory index target is outside the canonical directory")
    owned_directory = directory is None
    directory = directory if directory is not None else open_index_directory(create=True)
    temporary = ".%s-%s.tmp" % (path.stem, secrets.token_hex(12))
    backup = None
    backup_identity = None
    backup_sha256 = None
    predecessor_bytes = None
    try:
        cleanup_index_artifacts(directory, path.stem, ("tmp",))
        cleanup_index_artifacts(
            directory, path.stem, ("backup",), remove=False,
        )
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_index_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        verify_index_directory(directory)
        if before_replace is not None:
            before_replace()
        try:
            predecessor = os.stat(
                path.name, dir_fd=directory, follow_symlinks=False
            )
        except FileNotFoundError:
            predecessor = None
        if predecessor is not None:
            if not stat.S_ISREG(predecessor.st_mode) or predecessor.st_nlink != 1:
                raise ValueError("existing memory index is not a unique regular file")
            backup = ".%s-%s.backup" % (path.stem, secrets.token_hex(12))
            source = os.open(
                path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory,
            )
            destination = os.open(
                backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                dir_fd=directory,
            )
            with os.fdopen(source, "rb") as source_handle, os.fdopen(
                destination, "wb"
            ) as backup_handle:
                source_before = os.fstat(source_handle.fileno())
                if (
                    source_before.st_nlink != 1
                    or file_identity(source_before) != file_identity(predecessor)
                ):
                    raise ValueError("existing memory index changed before preservation")
                predecessor_bytes = source_handle.read()
                backup_handle.write(predecessor_bytes)
                backup_handle.flush()
                os.fsync(backup_handle.fileno())
                source_after = os.fstat(source_handle.fileno())
                if (
                    source_after.st_nlink != 1
                    or file_identity(source_after) != file_identity(source_before)
                ):
                    raise ValueError("existing memory index changed during preservation")
            preserved = os.stat(backup, dir_fd=directory, follow_symlinks=False)
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(preserved.st_mode) or preserved.st_nlink != 1
                or preserved.st_size != predecessor.st_size
                or current.st_nlink != 1
                or file_identity(current) != file_identity(predecessor)
            ):
                raise ValueError("existing memory index could not be preserved")
            backup_identity = file_identity(preserved)
            backup_sha256 = hashlib.sha256(predecessor_bytes).hexdigest()
        pending = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(pending.st_mode) or pending.st_nlink != 1:
            raise ValueError("temporary memory index is not a unique regular file")
        committed_identity = file_identity(pending)
        published = False
        try:
            os.replace(
                temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory
            )
            published = True
            committed = os.stat(
                path.name, dir_fd=directory, follow_symlinks=False
            )
            if committed.st_nlink != 1 or file_identity(committed) != committed_identity:
                raise ValueError("memory index changed during atomic replace")
            os.fsync(directory)
            verify_index_directory(directory)
            if after_replace is not None:
                after_replace()
            cleanup_index_artifacts(
                directory, path.stem, ("backup",),
                exclude=(backup,) if backup is not None else (),
            )
            if backup is not None:
                if not index_artifact_matches(
                    directory, backup, backup_identity, backup_sha256,
                ):
                    raise ValueError(
                        "memory index predecessor changed during transaction"
                    )
            current = os.stat(
                path.name, dir_fd=directory, follow_symlinks=False
            )
            if current.st_nlink != 1 or file_identity(current) != committed_identity:
                raise ValueError("memory index changed during transaction cleanup")
        except Exception as error:
            if not published:
                raise
            recovery = None
            retained_backup = backup
            backup = None
            if retained_backup is not None and index_artifact_matches(
                directory, retained_backup, backup_identity, backup_sha256,
            ):
                recovery = retained_backup
            elif predecessor_bytes is not None:
                recovery = ".%s-%s.backup" % (
                    path.stem, secrets.token_hex(12),
                )
                descriptor = os.open(
                    recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(predecessor_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                recovered = os.stat(
                    recovery, dir_fd=directory, follow_symlinks=False,
                )
                if not index_artifact_matches(
                    directory, recovery, file_identity(recovered), backup_sha256,
                ):
                    raise ValueError("memory index recovery copy could not be rebuilt")
            try:
                current = os.stat(
                    path.name, dir_fd=directory, follow_symlinks=False
                )
            except FileNotFoundError:
                current = None
            except OSError as rollback_error:
                raise ValueError(
                    "memory index state unavailable before rollback; predecessor retained"
                ) from rollback_error
            if current is not None and (
                current.st_nlink != 1
                or file_identity(current) != committed_identity
            ):
                raise ValueError("memory index changed before rollback") from error
            try:
                if recovery is not None:
                    os.replace(
                        recovery, path.name,
                        src_dir_fd=directory, dst_dir_fd=directory,
                    )
                elif current is not None:
                    os.unlink(path.name, dir_fd=directory)
            except OSError as rollback_error:
                raise ValueError(
                    "memory index rollback failed; predecessor retained"
                ) from rollback_error
            os.fsync(directory)
            verify_index_directory(directory)
            raise
        if backup is not None:
            # Keep one predecessor until the next successful transaction.
            # This avoids an irreversible final unlink window.
            backup = None
    finally:
        for leftover in (temporary, backup):
            if leftover is None:
                continue
            try:
                os.unlink(leftover, dir_fd=directory)
            except FileNotFoundError:
                pass
        if owned_directory:
            os.close(directory)


def build_index(zone, lock_token=None):
    automatic_token = None
    handle = None
    index_directory = None
    if lock_token:
        require_zone_lock(zone, lock_token)
    else:
        automatic_token = acquire_zone_lock(zone)
    try:
        handle, index_directory = index_lock(zone)
        records = zone_records(zone)
        policy = scan_zone(zone, records)
        if policy["issue_count"]:
            raise ValueError(
                "memory policy has %d issue(s); run scan --zone %s"
                % (policy["issue_count"], zone)
            )
        _, loadouts_sha256 = load_loadouts()
        load_approvals()
        resolver = resolver_identity()
        snapshot = compose_snapshot(zone, records)
        digest = snapshot_digest(snapshot)
        file_state = records_file_state(records)
        if zone_file_state(zone) != file_state:
            raise ValueError("canonical Markdown changed during index build; retry")
        if load_loadouts()[1] != loadouts_sha256:
            raise ValueError("memory loadout policy changed during index build; retry")
        index = {
            "schema_version": 3,
            "model": "l0-l3-canonical",
            "zone": zone,
            "built_at": utc_now(),
            "snapshot_sha256": digest,
            "loadouts_sha256": loadouts_sha256,
            "approvals_sha256": APPROVALS_MANIFEST_SHA256,
            "resolver": resolver,
            "policy_issue_count": policy["issue_count"],
            "file_state_sha256": snapshot_digest(file_state),
            "file_state": file_state,
            **snapshot,
        }
        with zone_commit_guard(zone, lock_token or automatic_token) as check_owner:
            def check_owner_and_policy():
                check_owner()
                if load_loadouts()[1] != loadouts_sha256:
                    raise ValueError("memory loadout policy changed during index commit")
                load_approvals()
                if resolver_identity() != resolver:
                    raise ValueError("memory resolver changed during index commit")
                if zone_file_state(zone) != file_state:
                    raise ValueError("canonical Markdown changed during index commit")

            atomic_index(
                index_path(zone), index, index_directory,
                before_replace=check_owner_and_policy,
                after_replace=check_owner_and_policy,
            )
            atomic_index(
                index_authority_path(zone), index_authority(index), index_directory,
                before_replace=check_owner_and_policy,
                after_replace=check_owner_and_policy,
            )
        return index
    finally:
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        if index_directory is not None:
            os.close(index_directory)
        if automatic_token:
            release_zone_lock(zone, automatic_token)


def validate_index(
    index, zone, verify_canonical=True, index_raw=None,
    require_checkpoint=True,
):
    if not isinstance(index, dict):
        raise ValueError("invalid memory index payload")
    if not schema_version_is(index, 3) or index.get("model") != "l0-l3-canonical":
        raise ValueError("unsupported memory index schema")
    if index.get("zone") != zone:
        raise ValueError("memory index zone mismatch: expected %s" % zone)
    _, loadouts_sha256 = load_loadouts()
    if index.get("loadouts_sha256") != loadouts_sha256:
        raise ValueError("memory loadout policy changed; rebuild the active zone index")
    load_approvals()
    if index.get("approvals_sha256") != APPROVALS_MANIFEST_SHA256:
        raise ValueError("L3 approvals changed; rebuild the active zone index")
    if index.get("resolver") != resolver_identity():
        raise ValueError("memory resolver changed; rebuild the active zone index")
    load_mode()
    if type(index.get("policy_issue_count")) is not int or index["policy_issue_count"] != 0:
        raise ValueError("memory index was not built from a clean policy scan")
    file_state = index.get("file_state")
    if (
        not isinstance(file_state, dict)
        or any(
            type(path) is not str or not path
            or not isinstance(value, list) or len(value) != 5
            or any(type(item) is not int or item < 0 for item in value)
            for path, value in file_state.items()
        )
        or index.get("file_state_sha256") != snapshot_digest(file_state)
    ):
        raise ValueError("memory index file-state manifest is invalid")
    snapshot = {
        key: index.get(key)
        for key in ("page_count", "doc_count", "page_hashes", "docs", "vocab")
    }
    if not all(isinstance(snapshot[key], dict) for key in ("page_hashes", "docs", "vocab")):
        raise ValueError("invalid memory index structure")
    if snapshot_digest(snapshot) != index.get("snapshot_sha256"):
        raise ValueError("memory index payload digest mismatch")
    canonical_raw = canonical_index_bytes(index)
    if index_raw is not None and index_raw != canonical_raw:
        raise ValueError("memory index encoding is not canonical")
    authority = load_index_authority(zone, require_checkpoint=require_checkpoint)
    if authority != index_authority(index):
        raise ValueError("memory index differs from its checkpointed authority")
    if verify_canonical:
        if zone_file_state(zone) != file_state:
            raise ValueError("memory index is stale or differs from canonical Markdown")
    return index


def load_index(zone, require_checkpoint=True):
    path = index_path(zone)
    directory = open_index_directory()
    try:
        descriptor = os.open(
            path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
    except FileNotFoundError:
        os.close(directory)
        raise FileNotFoundError(str(path))
    except Exception:
        os.close(directory)
        raise
    try:
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("memory index must be a unique regular file")
            raw = handle.read()
            index = json.loads(raw)
            after = os.fstat(handle.fileno())
            if after.st_nlink != 1 or file_identity(after) != file_identity(before):
                raise ValueError("memory index changed while reading")
        current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if current.st_nlink != 1 or file_identity(current) != file_identity(before):
            raise ValueError("memory index changed after reading")
        verify_index_directory(directory)
    finally:
        os.close(directory)
    return validate_index(
        index, zone, index_raw=raw, require_checkpoint=require_checkpoint,
    )


def validate_live_index(zone, lock_token=None):
    if lock_token:
        require_zone_lock(zone, lock_token)
        generation = None
    else:
        active, generation = zone_write_state(zone)
        if active:
            raise ValueError("zone write in progress; retry validation after release")
    identity = index_identity(zone)
    index = load_index(zone, require_checkpoint=lock_token is None)
    if lock_token:
        require_zone_lock(zone, lock_token)
    else:
        final_active, final_generation = zone_write_state(zone)
        if final_active or final_generation != generation:
            raise ValueError("zone changed during validation; retry")
    if index_identity(zone) != identity:
        raise ValueError("memory index changed during validation; retry")
    return index


def project_prefix(zone, project):
    if not project:
        return None
    if not PROJECT_RE.fullmatch(project):
        raise ValueError("invalid project slug")
    load_mode()
    folder = ZONE_LAYOUT[zone]["projects_folder"]
    if not folder:
        raise ValueError("zone has no projects folder")
    prefix = folder.rstrip("/") + "/" + project + "/"
    if prefix == ZONE_LAYOUT[zone]["inbox_folder"]:
        raise ValueError("reserved project slug: %s" % project)
    return prefix


def project_scoped(doc, prefix, project):
    if prefix is None:
        return True
    if doc.get("page_type") == "memory-candidate":
        return doc.get("candidate_project") == project
    return doc["page_path"].startswith(prefix)


def resolve_loadout(name, project, include_l0, top):
    manifest, manifest_sha256 = load_loadouts()
    name = name or manifest["default"]
    if not LOADOUT_RE.fullmatch(name) or name not in manifest["roles"]:
        raise ValueError("unknown memory loadout: %s" % name)
    role = manifest["roles"][name]
    if role["project_required"] and not project:
        raise ValueError("memory loadout %s requires --project" % name)
    if include_l0 and not role["allow_l0"]:
        raise ValueError("memory loadout %s does not allow L0 evidence" % name)
    item_limit = role["max_items"] if top is None else top
    if item_limit > role["max_items"]:
        raise ValueError(
            "--top exceeds the %s loadout budget of %d"
            % (name, role["max_items"])
        )
    skills = [skill_metadata(path) for path in role["pinned_skills"]]
    return name, role, manifest_sha256, skills, item_limit


def candidate_inventory(docs, project=None):
    pages = {}
    for doc in docs.values():
        if doc.get("page_type") != "memory-candidate":
            continue
        if project is not None and doc.get("candidate_project") != project:
            continue
        pages[doc["page_path"]] = {
            "status": doc.get("candidate_status") or doc.get("status") or "unknown",
            "target_level": doc.get("candidate_target_level") or "unknown",
            "project": doc.get("candidate_project") or None,
            "evidence_sha256": doc.get("candidate_evidence_sha256") or None,
            "fingerprint": doc.get("candidate_fingerprint") or None,
        }
    statuses = Counter(item["status"] for item in pages.values())
    return {
        "pages": len(pages),
        "by_status": dict(sorted(statuses.items())),
        "items": [
            {"page_path": path, **pages[path]} for path in sorted(pages)
        ],
    }


def budget_candidates(ranked, docs, query_terms, role, item_limit):
    candidates = []
    seen_pages = set()
    used_by_level = Counter()
    used_chars = 0
    dropped = Counter()
    for chunk_id, raw_score, final_score in ranked:
        doc = docs[chunk_id]
        page_path = doc["page_path"]
        if page_path in seen_pages:
            dropped["duplicate_page"] += 1
            continue
        if len(candidates) >= item_limit:
            seen_pages.add(page_path)
            dropped["item_budget"] += 1
            continue
        level = doc["memory_level"]
        if used_by_level[level] >= role["per_level"][level]:
            dropped["level_quota"] += 1
            continue
        snippet = query_snippet(doc["text"], query_terms, role["snippet_chars"])
        snippet_chars = len(snippet)
        if used_chars + snippet_chars > role["max_chars"]:
            dropped["character_budget"] += 1
            continue
        seen_pages.add(page_path)
        used_by_level[level] += 1
        used_chars += snippet_chars
        candidates.append({
            "page_path": page_path,
            "page_sha256": doc["page_sha256"],
            "chunk_index": doc["chunk_index"],
            "chunk_sha256": doc["chunk_sha256"],
            "snippet_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
            "chunk_stale": bool(doc.get("chunk_stale", False)),
            "title": doc["title"],
            "page_type": doc.get("page_type", ""),
            "status": doc["status"],
            "memory_level": doc["memory_level"],
            "memory_source": doc["memory_source"],
            "memory_reason": doc["memory_reason"],
            "memory_trust": doc["memory_trust"],
            "canonical": doc.get("memory_trust") == "verified-synthesis",
            "score": round(final_score, 6),
            "bm25_score": round(raw_score, 6),
            "role_type_boost": role["type_boosts"].get(doc.get("page_type", ""), 1.0),
            "snippet_chars": snippet_chars,
            "snippet": snippet,
        })
    return candidates, used_by_level, used_chars, dropped


def make_context_receipt(
    query_text, zone, project, loadout, include_l0, include_stale,
    item_limit, index_sha256, loadouts_sha256, candidates, l3_loadout, explain,
):
    selected_chunks = []
    for item in candidates:
        snippet = item["snippet"]
        if not isinstance(snippet, str):
            raise ValueError("context snippet must be text")
        selected_chunks.append({
            "page_path": item["page_path"],
            "page_sha256": item["page_sha256"],
            "chunk_index": item["chunk_index"],
            "chunk_sha256": item["chunk_sha256"],
            "snippet": snippet,
            "snippet_chars": len(snippet),
            "snippet_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
        })
    payload = {
        "schema_version": CONTEXT_RECEIPT_SCHEMA_VERSION,
        "resolver": resolver_identity(),
        "query_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
        "zone": zone,
        "project": project,
        "loadout": loadout,
        "include_l0": include_l0,
        "include_stale": include_stale,
        "explain": explain,
        "index_snapshot_sha256": index_sha256,
        "loadouts_sha256": loadouts_sha256,
        "effective_item_limit": item_limit,
        "selected_chunks": selected_chunks,
        "l3_pages": [
            {key: item[key] for key in ("page_path", "page_sha256")}
            for item in l3_loadout
        ],
    }
    return {**payload, "receipt_sha256": snapshot_digest(payload)}


def receipt_page_path_is(value, zone):
    if (
        type(value) is not str or not value.endswith(".md")
        or "\\" in value
        or any(
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in value
        )
    ):
        return False
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        return False
    if zone == "work":
        return (
            value.startswith("wiki/")
            and not value.startswith(("wiki/personal/", "wiki/client/"))
        )
    root = ZONE_LAYOUT[zone]["root"]
    return value.startswith(root + "/")


def validate_context_receipt(receipt):
    required = {
        "schema_version", "resolver", "query_sha256", "zone", "project",
        "loadout", "include_l0", "include_stale", "explain",
        "index_snapshot_sha256", "loadouts_sha256", "effective_item_limit",
        "selected_chunks", "l3_pages", "receipt_sha256",
    }
    if type(receipt) is not dict or set(receipt) != required:
        raise ValueError("context receipt fields are invalid")
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != CONTEXT_RECEIPT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported context receipt")
    digest = receipt["receipt_sha256"]
    if type(digest) is not str or not CANDIDATE_SHA256_RE.fullmatch(digest):
        raise ValueError("invalid context receipt digest")
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if snapshot_digest(payload) != digest:
        raise ValueError("context receipt digest mismatch")
    resolver = receipt["resolver"]
    expected_resolver = resolver_identity()
    if (
        type(resolver) is not dict
        or set(resolver) != {"version", "path", "sha256"}
        or type(resolver["version"]) is not int
        or type(resolver["path"]) is not str
        or type(resolver["sha256"]) is not str
        or not CANDIDATE_SHA256_RE.fullmatch(resolver["sha256"])
        or snapshot_digest(resolver) != snapshot_digest(expected_resolver)
    ):
        raise ValueError("context receipt resolver differs from current code")
    zone = receipt["zone"]
    if type(zone) is not str or zone not in ZONE_LAYOUT:
        raise ValueError("context receipt zone is invalid")
    project = receipt["project"]
    if project is not None and (
        type(project) is not str or not PROJECT_RE.fullmatch(project)
    ):
        raise ValueError("context receipt project is invalid")
    if project is not None:
        try:
            project_prefix(zone, project)
        except ValueError as error:
            raise ValueError("context receipt project is invalid") from error
    if (
        type(receipt["loadout"]) is not str
        or not LOADOUT_RE.fullmatch(receipt["loadout"])
    ):
        raise ValueError("context receipt loadout is invalid")
    if any(
        type(receipt[field]) is not bool
        for field in ("include_l0", "include_stale", "explain")
    ):
        raise ValueError("context receipt mode is invalid")
    for field in ("query_sha256", "index_snapshot_sha256", "loadouts_sha256"):
        if (
            type(receipt[field]) is not str
            or not CANDIDATE_SHA256_RE.fullmatch(receipt[field])
        ):
            raise ValueError("invalid context receipt hash: %s" % field)
    item_limit = receipt["effective_item_limit"]
    if type(item_limit) is not int or not 1 <= item_limit <= 20:
        raise ValueError("context receipt item limit is invalid")
    selected = receipt["selected_chunks"]
    if type(selected) is not list or len(selected) > item_limit:
        raise ValueError("context receipt selection is invalid")
    selected_keys = {
        "page_path", "page_sha256", "chunk_index", "chunk_sha256",
        "snippet", "snippet_chars", "snippet_sha256",
    }
    seen_chunks = set()
    for item in selected:
        if type(item) is not dict or set(item) != selected_keys:
            raise ValueError("context receipt selected item is invalid")
        snippet = item["snippet"]
        chunk_key = (item["page_path"], item["chunk_index"])
        if (
            type(snippet) is not str
            or type(item["snippet_chars"]) is not int
            or item["snippet_chars"] != len(snippet)
            or type(item["snippet_sha256"]) is not str
            or item["snippet_sha256"]
            != hashlib.sha256(snippet.encode("utf-8")).hexdigest()
            or not receipt_page_path_is(item["page_path"], zone)
            or type(item["page_sha256"]) is not str
            or not CANDIDATE_SHA256_RE.fullmatch(item["page_sha256"])
            or type(item["chunk_index"]) is not int
            or item["chunk_index"] < 0
            or type(item["chunk_sha256"]) is not str
            or not CANDIDATE_SHA256_RE.fullmatch(item["chunk_sha256"])
            or chunk_key in seen_chunks
        ):
            raise ValueError("context receipt snippet is invalid")
        seen_chunks.add(chunk_key)
    l3_pages = receipt["l3_pages"]
    if type(l3_pages) is not list or len(l3_pages) > 8:
        raise ValueError("context receipt L3 selection is invalid")
    seen_l3 = set()
    for item in l3_pages:
        if (
            type(item) is not dict
            or set(item) != {"page_path", "page_sha256"}
            or not receipt_page_path_is(item["page_path"], zone)
            or type(item["page_sha256"]) is not str
            or not CANDIDATE_SHA256_RE.fullmatch(item["page_sha256"])
            or item["page_path"] in seen_l3
        ):
            raise ValueError("context receipt L3 page is invalid")
        seen_l3.add(item["page_path"])
    return receipt


def public_result_projection(result):
    projection = json.loads(json.dumps(result, ensure_ascii=False))
    projection.pop("context_receipt", None)
    projection.pop("index_built_at", None)
    return projection


def golden_result_projection(result):
    projection = public_result_projection(result)
    projection.pop("index_snapshot_sha256", None)
    return projection


def validate_result_receipt(result, lock_token=None):
    explain = result.get("explain")
    if type(explain) is not bool:
        raise ValueError("returned loadout mode is invalid")
    receipt = validate_context_receipt(result.get("context_receipt"))
    returned = make_context_receipt(
        result["query"], result["zone"], result.get("project"),
        result["loadout"]["name"], result["include_l0"], result["include_stale"],
        result["context_budget"]["limits"]["items"],
        result["index_snapshot_sha256"], result["loadouts_sha256"],
        result["candidates"], result["l3_loadout"], explain,
    )
    if receipt != returned:
        raise ValueError("context receipt differs from the returned loadout")
    canonical = retrieve(
        result["query"], result["zone"],
        top=result["context_budget"]["limits"]["items"],
        include_l0=result["include_l0"], include_stale=result["include_stale"],
        project=result.get("project"), loadout_name=result["loadout"]["name"],
        explain=explain, lock_token=lock_token,
    )
    if receipt != validate_context_receipt(canonical.get("context_receipt")):
        raise ValueError("context receipt differs from canonical retrieval")
    if snapshot_digest(public_result_projection(result)) != snapshot_digest(
        public_result_projection(canonical)
    ):
        raise ValueError("returned loadout differs from canonical retrieval")
    return receipt


def retrieve(
    query_text, zone, top=None, include_l0=False, include_stale=False,
    project=None, loadout_name=None, explain=False, lock_token=None,
):
    if type(query_text) is not str or type(zone) is not str or zone not in ZONE_LAYOUT:
        raise ValueError("retrieval query or zone is invalid")
    if any(type(value) is not bool for value in (include_l0, include_stale, explain)):
        raise ValueError("retrieval mode is invalid")
    if top is not None and (type(top) is not int or top < 1):
        raise ValueError("retrieval item limit is invalid")
    if project is not None and (
        type(project) is not str or not PROJECT_RE.fullmatch(project)
    ):
        raise ValueError("retrieval project is invalid")
    if loadout_name is not None and (
        type(loadout_name) is not str or not LOADOUT_RE.fullmatch(loadout_name)
    ):
        raise ValueError("retrieval loadout is invalid")
    if lock_token is not None:
        require_zone_lock(zone, lock_token)
        write_generation = None
    else:
        write_active, write_generation = zone_write_state(zone)
        if write_active:
            raise ValueError("zone write in progress; retry retrieval after the writer releases its lock")
    identity = index_identity(zone)
    index = load_index(zone, require_checkpoint=lock_token is None)
    if index_identity(zone) != identity:
        raise ValueError("memory index changed during validation; retry retrieval")
    docs = index["docs"]
    vocab = index["vocab"]
    query_terms = tokenize(query_text)
    prefix = project_prefix(zone, project)
    if prefix is not None and not any(
        doc["page_path"] == prefix + "_index.md" and doc.get("page_type") == "project"
        for doc in docs.values()
    ):
        raise ValueError("unknown canonical project: %s" % project)
    loadout, role, loadouts_sha256, skills, item_limit = resolve_loadout(
        loadout_name, project, include_l0, top
    )
    if index.get("loadouts_sha256") != loadouts_sha256:
        raise ValueError("memory loadout policy differs from the active index")
    allowed_levels = set(role["levels"])
    if include_l0:
        allowed_levels.add("l0")
    exclusions = Counter()
    allowed = set()
    for chunk_id, doc in docs.items():
        if doc["memory_level"] not in allowed_levels:
            exclusions["memory_level"] += 1
        elif not include_stale and doc.get("chunk_stale", False):
            exclusions["stale"] += 1
        elif not project_scoped(doc, prefix, project):
            exclusions["project_scope"] += 1
        else:
            allowed.add(chunk_id)
    scores = defaultdict(float)
    if allowed and query_terms:
        average_length = sum(docs[cid]["dl"] for cid in allowed) / float(len(allowed)) or 1.0
        for term in query_terms:
            postings = [item for item in vocab.get(term, []) if item[0] in allowed]
            if not postings:
                continue
            document_frequency = len(postings)
            inverse_frequency = math.log(
                1 + (len(allowed) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            for chunk_id, count in postings:
                document_length = docs[chunk_id]["dl"]
                denominator = count + 1.5 * (
                    1 - 0.75 + 0.75 * document_length / average_length
                )
                scores[chunk_id] += inverse_frequency * (count * 2.5) / denominator

    level_boost = {"l0": 0.7, "l1": 1.0, "l2": 1.2}
    trust_boost = {
        "approved": 1.3, "verified-synthesis": 1.15,
        "explicit": 1.08, "explicit-unverified": 0.95, "legacy-unverified": 0.82,
        "provisional-synthesis": 0.88,
        "evidence": 0.7, "approval-required": 0.0,
    }
    ranked = sorted(
        (
            (chunk_id, score, score * level_boost[docs[chunk_id]["memory_level"]]
             * trust_boost.get(docs[chunk_id].get("memory_trust"), 0.75)
             * role["type_boosts"].get(docs[chunk_id].get("page_type", ""), 1.0))
            for chunk_id, score in scores.items()
        ),
        key=lambda item: item[2], reverse=True,
    )

    candidates, used_by_level, used_chars, selection_dropped = budget_candidates(
        ranked, docs, query_terms, role, item_limit
    )

    l3_pages = {}
    for doc in docs.values():
        if (
            doc["memory_level"] == "l3" and doc.get("l3_approved")
            and (include_stale or not doc.get("chunk_stale", False))
        ):
            l3_pages[doc["page_path"]] = {
                "page_path": doc["page_path"],
                "page_sha256": doc["page_sha256"],
                "title": doc["title"],
                "memory_level": "l3",
                "memory_trust": "approved",
            }
    l3_loadout = [l3_pages[path] for path in sorted(l3_pages)]
    l3_dropped = max(0, len(l3_loadout) - role["l3_max_items"])
    l3_loadout = l3_loadout[:role["l3_max_items"]]
    result = {
        "schema_version": 2,
        "query": query_text,
        "zone": zone,
        "project": project,
        "strategy": "canonical-zone-bm25+budgeted-role-loadout",
        "index_built_at": index.get("built_at"),
        "index_snapshot_sha256": index["snapshot_sha256"],
        "loadouts_sha256": loadouts_sha256,
        "include_l0": include_l0,
        "include_stale": include_stale,
        "explain": explain,
        "loadout": {
            "name": loadout,
            "description": role["description"],
            "project_required": role["project_required"],
            "pinned_skills": skills,
        },
        "context_budget": {
            "limits": {
                "items": item_limit,
                "snippet_chars": role["max_chars"],
                "snippet_chars_per_item": role["snippet_chars"],
                "per_level": role["per_level"],
                "l3_items": role["l3_max_items"],
            },
            "used": {
                "items": len(candidates),
                "snippet_chars": used_chars,
                "per_level": {
                    level: used_by_level.get(level, 0) for level in ("l0", "l1", "l2")
                },
                "l3_items": len(l3_loadout),
            },
            "dropped": {
                **dict(sorted(selection_dropped.items())),
                "l3_item_budget": l3_dropped,
            },
        },
        "l3_loadout": l3_loadout,
        "candidates": candidates,
    }
    result["context_receipt"] = make_context_receipt(
        query_text, zone, project, loadout, include_l0, include_stale,
        item_limit, index["snapshot_sha256"], loadouts_sha256,
        candidates, l3_loadout, explain,
    )
    if explain:
        result["inspector"] = {
            "indexed_chunks": len(docs),
            "eligible_chunks": len(allowed),
            "matched_chunks": len(scores),
            "excluded_chunks": dict(sorted(exclusions.items())),
            "candidate_inbox": candidate_inventory(docs, project),
        }
    if lock_token is not None:
        require_zone_lock(zone, lock_token)
        zone_changed = False
    else:
        final_active, final_generation = zone_write_state(zone)
        zone_changed = final_active or final_generation != write_generation
    if (
        zone_changed or index_identity(zone) != identity
        or load_loadouts()[1] != loadouts_sha256
        or [skill_metadata(path) for path in role["pinned_skills"]] != skills
    ):
        raise ValueError("zone changed during retrieval; retry against the new canonical state")
    return result


def evaluate_retrieval(zone):
    manifest, manifest_sha256 = load_evals()
    if zone != manifest["zone"]:
        raise ValueError("no fixed retrieval evaluations for zone: %s" % zone)
    roles = load_loadouts()[0]["roles"]
    results = []
    for case in manifest["cases"]:
        result = retrieve(
            case["query"], zone, top=case["top"],
            project=case.get("project"), loadout_name=case["loadout"],
            include_l0=case.get("include_l0", False),
            include_stale=case.get("include_stale", False),
        )
        receipt = validate_result_receipt(result)
        public_sha256 = snapshot_digest(golden_result_projection(result))
        if public_sha256 != case["expected_public_sha256"]:
            raise ValueError("evaluation %s public result differs from its golden result" % case["id"])
        candidates = result["candidates"]
        limits = result["context_budget"]["limits"]
        used = result["context_budget"]["used"]
        role = roles[case["loadout"]]
        if (
            used["items"] != len(candidates) or used["items"] > limits["items"]
            or used["snippet_chars"] > limits["snippet_chars"]
            or used["l3_items"] > limits["l3_items"]
            or any(
                used["per_level"][level] > limits["per_level"][level]
                for level in ("l0", "l1", "l2")
            )
        ):
            raise ValueError("evaluation %s exceeded its context budget" % case["id"])
        if any(
            (item["chunk_stale"] and not case.get("include_stale", False))
            or (item["memory_level"] == "l0" and not case.get("include_l0", False))
            or (
                item["memory_level"] != "l0"
                and item["memory_level"] not in role["levels"]
            )
            for item in candidates
        ):
            raise ValueError("evaluation %s returned excluded context" % case["id"])
        levels = {item["memory_level"] for item in candidates}
        if not set(case.get("required_levels", [])).issubset(levels):
            raise ValueError("evaluation %s missed a required memory level" % case["id"])
        if case.get("require_stale", False) and not any(
            item["chunk_stale"] for item in candidates
        ):
            raise ValueError("evaluation %s missed required historical context" % case["id"])
        if any(
            item["page_path"].startswith(prefix)
            for item in candidates
            for prefix in case.get("forbidden_path_prefixes", [])
        ):
            raise ValueError("evaluation %s crossed a forbidden project path" % case["id"])
        paths = {item["page_path"] for item in candidates}
        missing_paths = [path for path in case["must_paths"] if path not in paths]
        searchable = "\n".join(
            item["title"] + "\n" + item["snippet"] for item in candidates
        ).casefold()
        missing_terms = [term for term in case["must_contain"] if term.casefold() not in searchable]
        forbidden_terms = [
            term for term in case["must_not_contain"]
            if term.casefold() in searchable
        ]
        path_failures = []
        for assertion in case["path_assertions"]:
            path_text = "\n".join(
                item["title"] + "\n" + item["snippet"]
                for item in candidates if item["page_path"] == assertion["path"]
            ).casefold()
            path_missing = [
                term for term in assertion["must_contain"]
                if term.casefold() not in path_text
            ]
            path_forbidden = [
                term for term in assertion["must_not_contain"]
                if term.casefold() in path_text
            ]
            if path_missing or path_forbidden:
                path_failures.append({
                    "path": assertion["path"],
                    "missing": path_missing,
                    "forbidden": path_forbidden,
                })
        if missing_paths or missing_terms or forbidden_terms or path_failures:
            raise ValueError(
                "evaluation %s failed paths=%s missing=%s forbidden=%s path_assertions=%s"
                % (
                    case["id"], missing_paths, missing_terms,
                    forbidden_terms, path_failures,
                )
            )
        results.append({
            "id": case["id"], "passed": True,
            "receipt_sha256": receipt["receipt_sha256"],
            "public_sha256": public_sha256,
            "selected_paths": [item["page_path"] for item in candidates],
        })
    return {
        "schema_version": 1,
        "zone": zone,
        "evals_sha256": manifest_sha256,
        "passed": len(results),
        "cases": results,
    }


def self_test():
    samples = [
        (VAULT / "wiki/workspace/projects/demo/sources/raw.md", {"memory_level": "l3"}, "l0"),
        (VAULT / "wiki/workspace/projects/demo/analysis/raw/notes.md", {}, "l0"),
        (VAULT / "wiki/workspace/projects/demo/assets/ASSET_MANIFEST.md", {}, "l0"),
        (VAULT / "wiki/workspace/projects/demo/Decision.md", {"type": "decision"}, "l1"),
        (VAULT / "wiki/workspace/projects/demo/_index.md", {"type": "index"}, "l2"),
        (VAULT / "wiki/workspace/projects/demo/Candidate.md", {
            "type": "memory-candidate", "memory_level": "l2",
            "candidate_status": "pending",
        }, "l0"),
        (VAULT / "wiki/resources/Rules.md", {"memory_level": "l3"}, "l3"),
        (VAULT / "wiki/hot.md", {"type": "meta"}, "cache"),
        (VAULT / "wiki/hot 2.md", {"memory_role": "cache"}, "cache"),
    ]
    for path, frontmatter, expected in samples:
        assert classify(path, frontmatter)["level"] == expected
    assert content_is_dataless(type("Metadata", (), {"st_flags": MACOS_UF_DATALESS})())
    assert not content_is_dataless(type("Metadata", (), {"st_flags": 0})())
    base_state = {
        "st_dev": 1, "st_ino": 2, "st_birthtime": 3.0,
        "st_size": 4, "st_mtime_ns": 5, "st_ctime_ns": 6, "st_flags": 0,
    }
    hydrated = type("Metadata", (), {
        **base_state, "st_ctime_ns": 7, "st_flags": MACOS_UF_DATALESS,
    })()
    replaced = type("Metadata", (), {
        **base_state, "st_ino": 8, "st_birthtime": 9.0,
    })()
    assert logical_file_state(type("Metadata", (), base_state)()) == logical_file_state(hydrated)
    assert logical_file_state(type("Metadata", (), base_state)()) != logical_file_state(replaced)
    assert "evidence-cannot-self-promote" in classify(samples[0][0], samples[0][1])["issues"]
    assert "memory-candidate-must-use-zone-inbox" in classify(samples[5][0], samples[5][1])["issues"]
    assert "cache-must-use-zone-hot-path" in classify(samples[-1][0], samples[-1][1])["issues"]
    provisional = classify(
        VAULT / "wiki/resources/Provisional.md",
        {
            "memory_level": "l2", "memory_approval_mode": "provisional",
            "promotion_eligible": "false",
        },
    )
    assert provisional["trust"] == "provisional-synthesis" and not provisional["issues"]
    invalid_provisional = classify(
        VAULT / "wiki/resources/Invalid provisional.md",
        {"memory_level": "l2", "memory_approval_mode": "provisional"},
    )
    assert "invalid-provisional-memory-contract" in invalid_provisional["issues"]
    assert canonical_cache_issue("work", [])
    assert not canonical_cache_issue("work", ["wiki/hot.md"])
    candidate_sources = [{"path": "wiki/index.md", "sha256": "a" * 64}]
    candidate_statement = "Stable claim"
    candidate_fp = candidate_fingerprint(
        "work", "demo", "l1", "Candidate", candidate_statement, candidate_sources
    )
    candidate_body = (
        "# Candidate\n\n## Proposed memory\n\n%s\n\n## Evidence\n\n- source\n"
        % candidate_statement
    )
    candidate_frontmatter = {
        "type": "memory-candidate", "title": "Candidate",
        "candidate_id": candidate_identifier("Candidate", candidate_fp),
        "candidate_fingerprint": candidate_fp,
        "candidate_target_level": "l1", "candidate_project": "demo",
        "candidate_sources": "wiki/index.md",
        "candidate_source_hashes": "a" * 64,
        "candidate_evidence_sha256": candidate_evidence_digest(candidate_sources),
    }
    assert validate_candidate_identity(
        "work", candidate_frontmatter, candidate_body,
        verify_sources=False, verify_project=False,
    )["schema"] == "current"
    predecessor = dict(candidate_frontmatter)
    predecessor.pop("candidate_fingerprint")
    predecessor["candidate_id"] = "2026-08-20-candidate-%s" % candidate_fp[:10]
    assert validate_candidate_identity(
        "work", predecessor, candidate_body,
        verify_sources=False, verify_project=False,
    )["fingerprint"] == candidate_fp
    forged = dict(candidate_frontmatter)
    forged["candidate_id"] = "forged-id"
    forged["candidate_fingerprint"] = "b" * 64
    try:
        validate_candidate_identity(
            "work", forged, candidate_body,
            verify_sources=False, verify_project=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("forged candidate identity accepted")
    duplicate_source = dict(candidate_frontmatter)
    duplicate_source["candidate_sources"] = "wiki/index.md | wiki/index.md"
    duplicate_source["candidate_source_hashes"] = "%s | %s" % ("a" * 64, "a" * 64)
    try:
        validate_candidate_identity(
            "work", duplicate_source, candidate_body,
            verify_sources=False, verify_project=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate candidate source identity accepted")
    wrong_type = dict(candidate_frontmatter)
    wrong_type["type"] = "note"
    try:
        validate_candidate_identity(
            "work", wrong_type, candidate_body,
            verify_sources=False, verify_project=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("candidate fields with a different type were accepted")
    outside_source = dict(candidate_frontmatter)
    outside_source["candidate_sources"] = "../outside.md"
    outside_sources = [{"path": "../outside.md", "sha256": "a" * 64}]
    outside_source["candidate_evidence_sha256"] = candidate_evidence_digest(outside_sources)
    outside_fp = candidate_fingerprint(
        "work", "demo", "l1", "Candidate", candidate_statement, outside_sources
    )
    outside_source["candidate_fingerprint"] = outside_fp
    outside_source["candidate_id"] = candidate_identifier("Candidate", outside_fp)
    try:
        validate_candidate_identity(
            "work", outside_source, candidate_body,
            verify_sources=False, verify_project=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("outside-zone candidate source was accepted")
    assert strip_quotes(json.dumps('Quote " and slash \\')) == 'Quote " and slash \\'
    assert validate_candidate_title('Quote " and slash \\') == 'Quote " and slash \\'
    for invalid_title in (" leading", "trailing ", "line\u0085break", "line\u2028break"):
        try:
            validate_candidate_title(invalid_title)
        except ValueError:
            pass
        else:
            raise AssertionError("Unicode newline/control title accepted")
    for invalid_statement in (
        " leading", "trailing ", "Claim\r## Injected", "Claim\u0085Injected",
        "Claim\u2028Injected", "Claim\n---", "Claim\n===",
        'Visible claim\n<h2 class="evidence">Evidence</h2>',
        'Visible claim\n<h2 title="<">Evidence impostor',
        "Claim\n\n## Evidence\n\nnonce",
    ):
        try:
            validate_candidate_statement(invalid_statement)
        except ValueError:
            pass
        else:
            raise AssertionError("candidate statement structure injection accepted")
    try:
        validate_candidate_project("work", "definitely-missing-candidate-project")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown candidate project accepted")
    try:
        candidate_source_record("wiki/INDEX.md", "work")
    except ValueError:
        pass
    else:
        raise AssertionError("noncanonical candidate source casing accepted")
    with tempfile.TemporaryDirectory(prefix="memory-source-hardlink-") as directory:
        root = Path(directory)
        source = root / "wiki" / "source.md"
        source.parent.mkdir()
        source.write_text("source", encoding="utf-8")
        os.link(source, root / "wiki" / "alias.md")
        original_vault = globals()["VAULT"]
        try:
            globals()["VAULT"] = root
            try:
                candidate_source_record("wiki/source.md", "work")
            except ValueError:
                pass
            else:
                raise AssertionError("hard-linked candidate source accepted")
        finally:
            globals()["VAULT"] = original_vault
    with tempfile.TemporaryDirectory(prefix="memory-candidate-drift-") as directory:
        root = Path(os.path.realpath(directory))
        project = root / "wiki/workspace/projects/demo"
        source = project / "sources/evidence.md"
        source.parent.mkdir(parents=True)
        (project / "_index.md").write_text(
            "---\ntype: project\n---\n# Demo\n", encoding="utf-8",
        )
        source.write_text("original evidence", encoding="utf-8")
        source_record = {
            "path": "wiki/workspace/projects/demo/sources/evidence.md",
            "sha256": hashlib.sha256(b"original evidence").hexdigest(),
        }
        statement = "Verified candidate statement"
        fingerprint = candidate_fingerprint(
            "work", "demo", "l1", "Candidate drift", statement,
            [source_record],
        )
        frontmatter = {
            "type": "memory-candidate", "title": "Candidate drift",
            "candidate_target_level": "l1", "candidate_project": "demo",
            "candidate_id": candidate_identifier("Candidate drift", fingerprint),
            "candidate_sources": source_record["path"],
            "candidate_source_hashes": source_record["sha256"],
            "candidate_evidence_sha256": candidate_evidence_digest([source_record]),
            "candidate_fingerprint": fingerprint,
        }
        body = (
            "# Candidate drift\n\n## Proposed memory\n\n%s"
            "\n\n## Evidence\n\n- exact source\n" % statement
        )
        original_vault = globals()["VAULT"]
        try:
            globals()["VAULT"] = root
            assert validate_candidate_identity(
                "work", frontmatter, body, verify_sources=True,
            )["fingerprint"] == fingerprint
            source.write_text("changed evidence", encoding="utf-8")
            try:
                validate_candidate_identity(
                    "work", frontmatter, body, verify_sources=True,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("candidate source drift was accepted")
        finally:
            globals()["VAULT"] = original_vault
    original_vault = globals()["VAULT"]
    try:
        globals()["VAULT"] = Path("/private/tmp/sources/relocated-vault")
        relocated = classify(
            globals()["VAULT"] / "wiki/resources/Rule.md",
            {"memory_level": "l2"},
        )
        assert relocated["level"] == "l2"
    finally:
        globals()["VAULT"] = original_vault
    current_mode = load_control_json(MODE_PATH, "mode manifest")
    assert schema_version_is({"schema_version": 1}, 1)
    assert not schema_version_is({"schema_version": True}, 1)
    assert not schema_version_is({"schema_version": 1.0}, 1)
    validate_mode(current_mode)
    remapped_mode = json.loads(json.dumps(current_mode))
    remapped_mode["zones"]["personal"]["root"] = "wiki/workspace/"
    try:
        validate_mode(remapped_mode)
    except ValueError:
        pass
    else:
        raise AssertionError("remapped zone root accepted")
    loadout_manifest, loadout_sha256 = load_loadouts()
    assert len(loadout_sha256) == 64 and loadout_manifest["default"] == "general"
    eval_manifest, eval_sha256 = load_evals()
    assert len(eval_sha256) == 64 and eval_manifest["zone"] == "work"
    assert resolve_loadout("general", None, False, None)[4] == 8
    try:
        resolve_loadout("reviewer", None, False, None)
    except ValueError:
        pass
    else:
        raise AssertionError("project-scoped reviewer loadout accepted without project")
    try:
        resolve_loadout("general", None, False, 9)
    except ValueError:
        pass
    else:
        raise AssertionError("loadout item budget could be bypassed")
    broken_loadouts = json.loads(json.dumps(loadout_manifest))
    broken_loadouts["roles"]["general"]["max_chars"] = 1
    try:
        validate_loadouts(broken_loadouts)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid loadout budget accepted")
    for invalid_schema in (True, 1.0):
        broken_loadouts = json.loads(json.dumps(loadout_manifest))
        broken_loadouts["schema_version"] = invalid_schema
        try:
            validate_loadouts(broken_loadouts)
        except ValueError:
            pass
        else:
            raise AssertionError("non-integer loadout schema accepted")
    assert canonical_zone_root("work") == WIKI
    with tempfile.TemporaryDirectory(prefix="memory-ancestor-link-") as directory:
        base = Path(os.path.realpath(directory))
        (base / "real" / "leaf").mkdir(parents=True)
        (base / "alias").symlink_to(base / "real", target_is_directory=True)
        try:
            descriptor = open_real_directory(base / "alias" / "leaf", "test directory")
        except ValueError:
            pass
        else:
            os.close(descriptor)
            raise AssertionError("symlinked ancestor accepted")
    original_vault, original_load_mode = VAULT, load_mode
    original_stat = os.stat
    try:
        with tempfile.TemporaryDirectory(prefix="memory-zone-race-") as directory:
            base = Path(os.path.realpath(directory))
            vault = base / "vault"
            wiki = vault / "wiki"
            safe = wiki / "safe"
            outside = base / "outside"
            safe.mkdir(parents=True)
            outside.mkdir()
            (safe / "same.md").write_text("inside", encoding="utf-8")
            (outside / "same.md").write_text("outside", encoding="utf-8")
            globals()["VAULT"] = vault
            globals()["load_mode"] = lambda: {}
            swapped = [False]

            def swapping_stat(path, *args, **kwargs):
                metadata = original_stat(path, *args, **kwargs)
                if path == "safe" and kwargs.get("dir_fd") is not None and not swapped[0]:
                    safe.rename(wiki / "safe-original")
                    safe.symlink_to(outside, target_is_directory=True)
                    swapped[0] = True
                return metadata

            os.stat = swapping_stat
            try:
                zone_records("work")
            except ValueError:
                pass
            else:
                raise AssertionError("parent-directory swap reached outside content")
    finally:
        os.stat = original_stat
        globals()["VAULT"] = original_vault
        globals()["load_mode"] = original_load_mode

    original_reader = read_page_descriptor
    try:
        with tempfile.TemporaryDirectory(prefix="memory-zone-read-retry-") as directory:
            vault = Path(os.path.realpath(directory)) / "vault"
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            page = wiki / "stable.md"
            page.write_text("stable bytes", encoding="utf-8")
            globals()["VAULT"] = vault
            globals()["load_mode"] = lambda: {}
            attempts = [0]

            def one_time_metadata_drift(descriptor, path):
                if path == page and attempts[0] == 0:
                    attempts[0] += 1
                    with os.fdopen(descriptor, "rb") as handle:
                        metadata = os.fstat(handle.fileno())
                        raw = handle.read()
                    raise PageReadDrift(path, raw, metadata)
                return original_reader(descriptor, path)

            globals()["read_page_descriptor"] = one_time_metadata_drift
            records = zone_records("work")
            assert len(records) == 1 and records[0][0] == page and attempts[0] == 1

        with tempfile.TemporaryDirectory(prefix="memory-zone-read-change-") as directory:
            vault = Path(os.path.realpath(directory)) / "vault"
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            page = wiki / "changed.md"
            page.write_bytes(b"AAAA")
            globals()["VAULT"] = vault
            globals()["load_mode"] = lambda: {}
            changed = [False]

            def same_size_content_change(descriptor, path):
                if path == page and not changed[0]:
                    changed[0] = True
                    with os.fdopen(descriptor, "rb") as handle:
                        metadata = os.fstat(handle.fileno())
                        raw = handle.read()
                    page.write_bytes(b"BBBB")
                    raise PageReadDrift(path, raw, metadata)
                return original_reader(descriptor, path)

            globals()["read_page_descriptor"] = same_size_content_change
            try:
                zone_records("work")
            except ValueError as error:
                assert "content changed during retry" in str(error)
            else:
                raise AssertionError("same-size page content change accepted")

        with tempfile.TemporaryDirectory(prefix="memory-zone-hardlink-") as directory:
            vault = Path(os.path.realpath(directory)) / "vault"
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            page = wiki / "linked.md"
            page.write_text("linked bytes", encoding="utf-8")
            os.link(page, vault / "outside-link.md")
            globals()["VAULT"] = vault
            globals()["load_mode"] = lambda: {}
            globals()["read_page_descriptor"] = original_reader
            try:
                zone_records("work")
            except ValueError as error:
                assert "unique regular file" in str(error)
            else:
                raise AssertionError("hard-linked canonical Markdown accepted")
    finally:
        globals()["read_page_descriptor"] = original_reader
        globals()["VAULT"] = original_vault
        globals()["load_mode"] = original_load_mode

    original_index_dir = INDEX_DIR
    try:
        with tempfile.TemporaryDirectory(prefix="memory-index-dir-") as directory:
            base = Path(os.path.realpath(directory))
            metadata = base / "meta"
            outside = base / "outside"
            metadata.mkdir()
            outside.mkdir()
            alias = metadata / "memory-index"
            alias.symlink_to(outside, target_is_directory=True)
            globals()["INDEX_DIR"] = alias
            try:
                atomic_index(index_path("work"), {"synthetic": "only"})
            except ValueError:
                pass
            else:
                raise AssertionError("symlinked index directory accepted")
            assert not (outside / "work.json").exists()
            globals()["INDEX_DIR"] = metadata / "real-index"
            atomic_index(index_path("work"), {"temporary": True})
            assert json.loads(index_path("work").read_text()) == {"temporary": True}

            def reject_owner():
                raise PermissionError("expired owner")

            try:
                atomic_index(
                    index_path("work"), {"rejected": True},
                    before_replace=reject_owner,
                )
            except PermissionError:
                pass
            else:
                raise AssertionError("index committed after owner rejection")
            assert json.loads(index_path("work").read_text()) == {"temporary": True}
            try:
                atomic_index(
                    index_path("work"), {"rejected-after": True},
                    after_replace=reject_owner,
                )
            except PermissionError:
                pass
            else:
                raise AssertionError("index survived failed post-commit validation")
            assert json.loads(index_path("work").read_text()) == {"temporary": True}
            assert index_path("work").stat().st_nlink == 1
            stale_tmp = INDEX_DIR / (".work-%s.tmp" % ("a" * 24))
            stale_backup = INDEX_DIR / (".work-%s.backup" % ("b" * 24))
            stale_tmp.write_text("interrupted temporary", encoding="utf-8")
            stale_backup.write_text("interrupted backup", encoding="utf-8")
            prior_bytes = index_path("work").read_bytes()
            atomic_index(
                index_path("work"), {"recovered": True},
                after_replace=lambda: None,
            )
            assert json.loads(index_path("work").read_text()) == {"recovered": True}
            assert index_path("work").stat().st_nlink == 1
            assert not stale_tmp.exists() and not stale_backup.exists()
            recovery_copies = list(INDEX_DIR.glob(".work-*.backup"))
            assert len(recovery_copies) == 1
            assert recovery_copies[0].read_bytes() == prior_bytes
            unsafe_backup = INDEX_DIR / (".work-%s.backup" % ("c" * 24))
            unsafe_backup.write_text("unsafe backup", encoding="utf-8")
            os.link(unsafe_backup, base / "unsafe-backup-link")
            predecessor_bytes = index_path("work").read_bytes()
            try:
                atomic_index(
                    index_path("work"), {"must-not-commit": True},
                    after_replace=lambda: None,
                )
            except ValueError as error:
                assert "transaction artifact is unsafe" in str(error)
            else:
                raise AssertionError("unsafe orphan backup accepted")
            assert index_path("work").read_bytes() == predecessor_bytes
            assert index_path("work").stat().st_nlink == 1
            (base / "unsafe-backup-link").unlink()
            unsafe_backup.unlink()

            late_backup = INDEX_DIR / (".work-%s.backup" % ("d" * 24))
            late_alias = base / "late-backup-link"

            def inject_unsafe_backup():
                late_backup.write_text("late unsafe backup", encoding="utf-8")
                os.link(late_backup, late_alias)

            try:
                atomic_index(
                    index_path("work"), {"must-roll-back": True},
                    after_replace=inject_unsafe_backup,
                )
            except ValueError as error:
                assert "transaction artifact is unsafe" in str(error)
            else:
                raise AssertionError("late unsafe orphan backup accepted")
            assert index_path("work").read_bytes() == predecessor_bytes
            late_alias.unlink()
            late_backup.unlink()

            known_backups = set(INDEX_DIR.glob(".work-*.backup"))

            def remove_fresh_backup():
                fresh = set(INDEX_DIR.glob(".work-*.backup")) - known_backups
                assert len(fresh) == 1
                fresh.pop().unlink()

            try:
                atomic_index(
                    index_path("work"), {"missing-recovery": True},
                    after_replace=remove_fresh_backup,
                )
            except ValueError as error:
                assert "predecessor changed during transaction" in str(error)
            else:
                raise AssertionError("missing retained predecessor accepted")
            assert index_path("work").read_bytes() == predecessor_bytes

            changed_backup = []

            def change_fresh_backup():
                fresh = list(INDEX_DIR.glob(".work-*.backup"))
                assert len(fresh) == 1
                fresh[0].write_bytes(b"changed recovery bytes")
                changed_backup.append(fresh[0])

            try:
                atomic_index(
                    index_path("work"), {"changed-recovery": True},
                    after_replace=change_fresh_backup,
                )
            except ValueError as error:
                assert "predecessor changed during transaction" in str(error)
            else:
                raise AssertionError("changed retained predecessor accepted")
            assert index_path("work").read_bytes() == predecessor_bytes
            changed_backup[0].unlink()

            index_directory = open_index_directory()
            original_fsync = os.fsync
            failed_directory_fsync = []

            def fail_first_directory_fsync(descriptor):
                if descriptor == index_directory and not failed_directory_fsync:
                    failed_directory_fsync.append(True)
                    raise OSError("synthetic directory fsync failure")
                return original_fsync(descriptor)

            os.fsync = fail_first_directory_fsync
            try:
                try:
                    atomic_index(
                        index_path("work"), {"fsync-must-roll-back": True},
                        directory=index_directory,
                    )
                except OSError as error:
                    assert "synthetic directory fsync failure" in str(error)
                else:
                    raise AssertionError("post-publish fsync failure accepted")
            finally:
                os.fsync = original_fsync
                os.close(index_directory)
            assert index_path("work").read_bytes() == predecessor_bytes

            def restore_retained_index(expected):
                copies = list(INDEX_DIR.glob(".work-*.backup"))
                assert len(copies) == 1 and copies[0].read_bytes() == expected
                os.replace(copies[0], index_path("work"))
                assert index_path("work").read_bytes() == expected

            original_match = index_artifact_matches
            injected_success_target = []

            def replace_target_during_success_check(*arguments):
                result = original_match(*arguments)
                if not injected_success_target:
                    injected_success_target.append(True)
                    foreign = base / "success-check-foreign.json"
                    foreign.write_text("{\"foreign-success\":true}\n", encoding="utf-8")
                    os.replace(foreign, index_path("work"))
                return result

            predecessor_bytes = index_path("work").read_bytes()
            globals()["index_artifact_matches"] = replace_target_during_success_check
            try:
                atomic_index(index_path("work"), {"must-not-return-success": True})
            except ValueError as error:
                assert "changed before rollback" in str(error)
            else:
                raise AssertionError("late target replacement returned success")
            finally:
                globals()["index_artifact_matches"] = original_match
            assert json.loads(index_path("work").read_text()) == {
                "foreign-success": True,
            }
            restore_retained_index(predecessor_bytes)

            rollback_check_armed = []
            injected_rollback_target = []

            def reject_and_arm_rollback_check():
                rollback_check_armed.append(True)
                raise PermissionError("reject before rollback check")

            def replace_target_during_rollback_check(*arguments):
                result = original_match(*arguments)
                if rollback_check_armed and not injected_rollback_target:
                    injected_rollback_target.append(True)
                    foreign = base / "rollback-check-foreign.json"
                    foreign.write_text("{\"foreign-rollback\":true}\n", encoding="utf-8")
                    os.replace(foreign, index_path("work"))
                return result

            predecessor_bytes = index_path("work").read_bytes()
            globals()["index_artifact_matches"] = replace_target_during_rollback_check
            try:
                atomic_index(
                    index_path("work"), {"must-not-overwrite-foreign": True},
                    after_replace=reject_and_arm_rollback_check,
                )
            except ValueError as error:
                assert "changed before rollback" in str(error)
            else:
                raise AssertionError("rollback overwrote late foreign target")
            finally:
                globals()["index_artifact_matches"] = original_match
            assert json.loads(index_path("work").read_text()) == {
                "foreign-rollback": True,
            }
            restore_retained_index(predecessor_bytes)

            index_directory = open_index_directory()
            original_stat = os.stat
            rollback_stat_armed = []
            failed_rollback_stat = []

            def reject_and_arm_rollback_stat():
                rollback_stat_armed.append(True)
                raise PermissionError("reject before rollback stat")

            def fail_rollback_target_stat(name, *arguments, **keywords):
                if (
                    rollback_stat_armed and not failed_rollback_stat
                    and name == index_path("work").name
                    and keywords.get("dir_fd") == index_directory
                ):
                    failed_rollback_stat.append(True)
                    raise OSError("synthetic rollback stat failure")
                return original_stat(name, *arguments, **keywords)

            predecessor_bytes = index_path("work").read_bytes()
            os.stat = fail_rollback_target_stat
            try:
                try:
                    atomic_index(
                        index_path("work"), {"rollback-stat-fails": True},
                        directory=index_directory,
                        after_replace=reject_and_arm_rollback_stat,
                    )
                except ValueError as error:
                    assert "state unavailable before rollback" in str(error)
                else:
                    raise AssertionError("rollback stat failure was accepted")
            finally:
                os.stat = original_stat
                os.close(index_directory)
            assert failed_rollback_stat
            restore_retained_index(predecessor_bytes)

            index_directory = open_index_directory()
            original_replace = os.replace
            rollback_replace_armed = []
            failed_rollback_replace = []

            def reject_and_arm_rollback_replace():
                rollback_replace_armed.append(True)
                raise PermissionError("reject before rollback replace")

            def fail_rollback_replace(source, target, *arguments, **keywords):
                if (
                    rollback_replace_armed and not failed_rollback_replace
                    and str(source).endswith(".backup")
                    and target == index_path("work").name
                ):
                    failed_rollback_replace.append(True)
                    raise OSError("synthetic rollback replace failure")
                return original_replace(source, target, *arguments, **keywords)

            predecessor_bytes = index_path("work").read_bytes()
            os.replace = fail_rollback_replace
            try:
                try:
                    atomic_index(
                        index_path("work"), {"rollback-replace-fails": True},
                        directory=index_directory,
                        after_replace=reject_and_arm_rollback_replace,
                    )
                except ValueError as error:
                    assert "rollback failed; predecessor retained" in str(error)
                else:
                    raise AssertionError("rollback replace failure was accepted")
            finally:
                os.replace = original_replace
                os.close(index_directory)
            assert failed_rollback_replace
            restore_retained_index(predecessor_bytes)

            original_cleanup = cleanup_index_artifacts

            def replace_target_during_cleanup(
                descriptor, stem, suffixes, remove=True, exclude=(),
            ):
                result = original_cleanup(
                    descriptor, stem, suffixes, remove=remove, exclude=exclude,
                )
                if remove and suffixes == ("backup",) and exclude:
                    foreign = INDEX_DIR / "foreign.json"
                    foreign.write_text("{\"foreign\":true}\n", encoding="utf-8")
                    os.replace(foreign, index_path("work"))
                return result

            globals()["cleanup_index_artifacts"] = replace_target_during_cleanup
            try:
                atomic_index(
                    index_path("work"), {"must-detect-foreign": True},
                    after_replace=lambda: None,
                )
            except ValueError as error:
                assert "changed before rollback" in str(error)
            else:
                raise AssertionError("foreign index replacement accepted")
            finally:
                globals()["cleanup_index_artifacts"] = original_cleanup
            assert json.loads(index_path("work").read_text()) == {"foreign": True}
            retained = list(INDEX_DIR.glob(".work-*.backup"))
            assert len(retained) == 1 and retained[0].read_bytes() == predecessor_bytes
    finally:
        globals()["INDEX_DIR"] = original_index_dir
    assert tokenize("Hello, Память 42") == ["hello", "память", "42"]
    assert chunk_body("# A\n\nOne\n\n# B\n\nTwo")
    excerpt = query_snippet(
        ("intro " * 120) + "274 из 274 " + ("middle " * 20) + "848 обложек",
        ["274", "848"], 300,
    )
    assert "274" in excerpt and "848" in excerpt
    exact = query_snippet(
        ("inactive 1274 filler " * 30) + "active canonical 274 target",
        ["active", "274"], 120,
    )
    assert "active" in tokenize(exact) and "274" in tokenize(exact)
    clipped = query_snippet("active " + ("word " * 100), ["active"], 80)
    assert clipped.endswith(" …")
    boundary_chunks = chunk_body(("a " * 799) + "target", 1600)
    assert sum("target" in tokenize(piece) for piece, _ in boundary_chunks) == 1
    huge_prefix = ("x" * 600) + " target canonical"
    huge_excerpt = query_snippet(huge_prefix, ["target"], 80)
    assert "target" in tokenize(huge_excerpt)
    for offset in range(1, 81):
        short_excerpt = query_snippet(
            ("prefix " * offset) + "target " + ("tail " * 30),
            ["target"], 80,
        )
        assert "target" in tokenize(short_excerpt)
    long_target = "z" * 450
    long_excerpt = query_snippet(
        ("intro " * 30) + long_target + (" tail" * 30),
        [long_target], 500,
    )
    assert long_target in tokenize(long_excerpt)
    oversized_target = "y" * 600
    oversized_excerpt = query_snippet(
        "intro " + oversized_target + " tail", [oversized_target], 500,
    )
    assert len(oversized_excerpt) <= 500 and oversized_excerpt.count("y") >= 490
    for limit in (1, 2, 4, 5, 80, 500):
        assert len(query_snippet("prefix " + ("word " * 200), ["word"], limit)) <= limit
    section_chunks = chunk_body(
        "## Superseded review manifest\n\n" + ("old " * 30)
        + "\n\n### Details\n\nold-value\n\n## Current\n\nnew-value",
        max_chars=40,
    )
    assert all(stale for raw, stale in section_chunks if "old" in raw or "Details" in raw)
    assert any(not stale for raw, stale in section_chunks if "new-value" in raw)
    assert status_is_stale("superseded-by-v2") and status_is_stale("Исторический snapshot")
    assert not status_is_stale("active")
    assert project_prefix("work", "demo") == "wiki/workspace/projects/demo/"
    try:
        project_prefix("work", "inbox")
    except ValueError:
        pass
    else:
        raise AssertionError("reserved inbox accepted as a project")
    assert duplicate_linkable_filenames([
        (VAULT / "wiki/a/Same.md", "l2"),
        (VAULT / "wiki/b/Same.md", "l1"),
    ])
    assert not duplicate_linkable_filenames([
        (VAULT / "wiki/a/README.md", "l0"),
        (VAULT / "wiki/b/README.md", "l0"),
    ])
    synthetic_docs = {
        "a": {
            "page_path": "wiki/a.md", "title": "A", "page_type": "audit",
            "status": "current", "memory_level": "l2", "memory_source": "explicit",
            "memory_reason": "frontmatter", "memory_trust": "verified-synthesis",
            "page_sha256": "a" * 64, "chunk_index": 0,
            "chunk_sha256": "1" * 64, "chunk_stale": False,
            "text": "target one",
        },
        "b": {
            "page_path": "wiki/b.md", "title": "B", "page_type": "audit",
            "status": "current", "memory_level": "l2", "memory_source": "explicit",
            "memory_reason": "frontmatter", "memory_trust": "verified-synthesis",
            "page_sha256": "b" * 64, "chunk_index": 0,
            "chunk_sha256": "2" * 64, "chunk_stale": False,
            "text": "target two",
        },
        "c": {
            "page_path": "wiki/c.md", "title": "C", "page_type": "decision",
            "status": "current", "memory_level": "l1", "memory_source": "explicit",
            "memory_reason": "frontmatter", "memory_trust": "explicit",
            "page_sha256": "c" * 64, "chunk_index": 0,
            "chunk_sha256": "3" * 64, "chunk_stale": False,
            "text": "target three",
        },
    }
    budget_role = json.loads(json.dumps(loadout_manifest["roles"]["general"]))
    budget_role["max_chars"] = 15
    budget_role["snippet_chars"] = 60
    budget_role["per_level"] = {"l0": 0, "l1": 1, "l2": 1}
    selected, selected_levels, selected_chars, dropped = budget_candidates(
        [("a", 3.0, 3.0), ("b", 2.0, 2.0), ("c", 1.0, 1.0)],
        synthetic_docs, ["target"], budget_role, 3,
    )
    assert [item["page_path"] for item in selected] == ["wiki/a.md"]
    assert selected_levels["l2"] == 1 and selected_chars <= 15
    assert dropped["level_quota"] == 1 and dropped["character_budget"] == 1
    receipt = make_context_receipt(
        "target", "work", None, "general", False, False, 3,
        "d" * 64, "e" * 64, selected,
        [{"page_path": "wiki/rules.md", "page_sha256": "f" * 64}], False,
    )
    validated_receipt = validate_context_receipt(receipt)
    assert validated_receipt["selected_chunks"][0]["page_path"] == "wiki/a.md"
    assert validated_receipt["selected_chunks"][0]["snippet"] == selected[0]["snippet"]
    delete = object()
    malformed_receipts = (
        (("extra",), True),
        (("l3_pages",), delete),
        (("schema_version",), 2.0),
        (("resolver", "version"), True),
        (("query_sha256",), 7),
        (("zone",), 7),
        (("project",), 7),
        (("loadout",), 7),
        (("include_l0",), 0),
        (("effective_item_limit",), 3.0),
        (("selected_chunks",), {}),
        (("selected_chunks", 0, "extra"), True),
        (("selected_chunks", 0, "page_sha256"), 7),
        (("selected_chunks", 0, "page_path"), "../wiki/a.md"),
        (("selected_chunks", 0, "page_path"), "wiki/..\\personal\\secret.md"),
        (("selected_chunks", 0, "page_path"), "wiki/" + chr(0x202E) + "hidden.md"),
        (("selected_chunks", 0, "chunk_index"), True),
        (("selected_chunks", 0, "chunk_sha256"), 7),
        (("selected_chunks", 0, "snippet_chars"), 10.0),
        (("l3_pages",), {}),
        (("l3_pages", 0, "extra"), True),
        (("l3_pages", 0, "page_path"), 7),
        (("l3_pages", 0, "page_path"), "wiki/folder\\secret.md"),
        (("l3_pages", 0, "page_path"), "wiki/" + chr(0x200B) + "hidden.md"),
    )
    for path, value in malformed_receipts:
        malformed = json.loads(json.dumps(receipt))
        target = malformed
        for key in path[:-1]:
            target = target[key]
        if value is delete:
            target.pop(path[-1])
        else:
            target[path[-1]] = value
        payload = {
            key: item for key, item in malformed.items()
            if key != "receipt_sha256"
        }
        malformed["receipt_sha256"] = snapshot_digest(payload)
        try:
            validate_context_receipt(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError("schema-invalid context receipt accepted: %s" % (path,))
    corrupted_receipt = json.loads(json.dumps(receipt))
    corrupted_receipt["selected_chunks"][0]["chunk_index"] = 1
    try:
        validate_context_receipt(corrupted_receipt)
    except ValueError:
        pass
    else:
        raise AssertionError("modified context receipt accepted")
    receipt_result = {
        "schema_version": 2,
        "explain": False,
        "context_receipt": receipt,
        "query": "target", "zone": "work", "project": None,
        "loadout": {
            "name": "general", "description": "test role",
            "project_required": False, "pinned_skills": [],
        },
        "include_l0": False, "include_stale": False,
        "context_budget": {
            "limits": {"items": 3},
            "used": {"snippet_chars": selected_chars},
        },
        "index_snapshot_sha256": "d" * 64,
        "loadouts_sha256": "e" * 64,
        "candidates": selected,
        "l3_loadout": [
            {"page_path": "wiki/rules.md", "page_sha256": "f" * 64}
        ],
    }
    original_retrieve = retrieve
    try:
        globals()["retrieve"] = lambda *args, **kwargs: receipt_result
        assert validate_result_receipt(receipt_result) == receipt
        observed_lock = []
        globals()["retrieve"] = lambda *args, **kwargs: (
            observed_lock.append(kwargs.get("lock_token")) or receipt_result
        )
        assert validate_result_receipt(
            receipt_result, lock_token="a" * 64,
        ) == receipt
        assert observed_lock == ["a" * 64]
        globals()["retrieve"] = lambda *args, **kwargs: receipt_result
        tampered_result = json.loads(json.dumps(receipt_result))
        tampered_result["candidates"][0]["snippet"] += " forged"
        try:
            validate_result_receipt(tampered_result)
        except ValueError:
            pass
        else:
            raise AssertionError("tampered model-visible snippet accepted")
        coherent_forgery = json.loads(json.dumps(tampered_result))
        coherent_forgery["context_receipt"] = make_context_receipt(
            coherent_forgery["query"], coherent_forgery["zone"],
            coherent_forgery.get("project"), coherent_forgery["loadout"]["name"],
            coherent_forgery["include_l0"], coherent_forgery["include_stale"],
            coherent_forgery["context_budget"]["limits"]["items"],
            coherent_forgery["index_snapshot_sha256"],
            coherent_forgery["loadouts_sha256"], coherent_forgery["candidates"],
            coherent_forgery["l3_loadout"], coherent_forgery["explain"],
        )
        try:
            validate_result_receipt(coherent_forgery)
        except ValueError:
            pass
        else:
            raise AssertionError("self-consistent context forgery accepted")
        metadata_forgeries = []
        for field, value in (
            ("schema_version", 999),
            ("schema_version", 2.0),
            ("explain", True),
            ("loadout.project_required", True),
            ("loadout.pinned_skills", [{"name": "forged"}]),
            ("candidate.title", "Forged title"),
            ("candidate.memory_trust", "forged"),
            ("budget.snippet_chars", 0),
            ("budget.snippet_chars", float(selected_chars)),
        ):
            forged = json.loads(json.dumps(receipt_result))
            if field == "schema_version":
                forged["schema_version"] = value
            elif field == "explain":
                forged["explain"] = value
            elif field == "loadout.project_required":
                forged["loadout"]["project_required"] = value
            elif field == "loadout.pinned_skills":
                forged["loadout"]["pinned_skills"] = value
            elif field == "candidate.title":
                forged["candidates"][0]["title"] = value
            elif field == "candidate.memory_trust":
                forged["candidates"][0]["memory_trust"] = value
            else:
                forged["context_budget"]["used"]["snippet_chars"] = value
            metadata_forgeries.append(forged)
        for forged in metadata_forgeries:
            try:
                validate_result_receipt(forged)
            except ValueError:
                pass
            else:
                raise AssertionError("forged returned metadata accepted")
        assert all(
            "absolute_path" not in item
            for field in ("candidates", "l3_loadout")
            for item in receipt_result[field]
        )
        path_injection = json.loads(json.dumps(receipt_result))
        path_injection["candidates"][0]["absolute_path"] = "/outside/unrelated.md"
        try:
            validate_result_receipt(path_injection)
        except ValueError:
            pass
        else:
            raise AssertionError("unauthenticated absolute path accepted")
        baseline_public = snapshot_digest(golden_result_projection(receipt_result))
        persistent_forgery = json.loads(json.dumps(receipt_result))
        persistent_forgery["candidates"][0]["title"] = "Persistent forged title"
        globals()["retrieve"] = lambda *args, **kwargs: persistent_forgery
        assert validate_result_receipt(persistent_forgery) == receipt
        assert snapshot_digest(golden_result_projection(persistent_forgery)) != baseline_public
        inspector_result = json.loads(json.dumps(receipt_result))
        inspector_result["explain"] = True
        inspector_result["inspector"] = {"indexed_chunks": 3}
        inspector_result["context_receipt"] = make_context_receipt(
            inspector_result["query"], inspector_result["zone"],
            inspector_result.get("project"), inspector_result["loadout"]["name"],
            inspector_result["include_l0"], inspector_result["include_stale"],
            inspector_result["context_budget"]["limits"]["items"],
            inspector_result["index_snapshot_sha256"],
            inspector_result["loadouts_sha256"], inspector_result["candidates"],
            inspector_result["l3_loadout"], True,
        )
        globals()["retrieve"] = lambda *args, **kwargs: inspector_result
        assert validate_result_receipt(inspector_result) == inspector_result["context_receipt"]
        missing_inspector = json.loads(json.dumps(inspector_result))
        missing_inspector.pop("inspector")
        try:
            validate_result_receipt(missing_inspector)
        except ValueError:
            pass
        else:
            raise AssertionError("missing inspector metadata accepted")
    finally:
        globals()["retrieve"] = original_retrieve
    invalid_requests = (
        ("target", "work", {"top": True}),
        ("target", "work", {"top": 1.0}),
        ("target", "work", {"top": 0}),
        ("target", "work", {"include_l0": 0}),
        ("target", "work", {"include_stale": 0}),
        ("target", "work", {"explain": 0}),
        ("target", "work", {"project": 0}),
        ("target", "work", {"loadout_name": 0}),
        (0, "work", {}),
        ("target", 0, {}),
    )
    for query, zone, kwargs in invalid_requests:
        try:
            retrieve(query, zone, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("schema-invalid retrieval request accepted")
    item_role = json.loads(json.dumps(loadout_manifest["roles"]["general"]))
    item_role["max_chars"] = 100
    item_role["snippet_chars"] = 60
    item_role["per_level"] = {"l0": 0, "l1": 3, "l2": 3}
    _, _, _, item_dropped = budget_candidates(
        [("a", 3.0, 3.0), ("b", 2.0, 2.0), ("c", 1.0, 1.0)],
        synthetic_docs, ["target"], item_role, 1,
    )
    assert item_dropped["item_budget"] == 2
    inventory = candidate_inventory({
        "x": {"page_path": "wiki/inbox/x.md", "page_type": "memory-candidate",
              "candidate_status": "pending", "candidate_target_level": "l1",
              "candidate_project": "demo"},
        "y": {"page_path": "wiki/inbox/x.md", "page_type": "memory-candidate",
              "candidate_status": "pending", "candidate_target_level": "l1",
              "candidate_project": "demo"},
    })
    assert inventory["pages"] == 1 and inventory["by_status"] == {"pending": 1}
    candidate_doc = {
        "page_path": "wiki/workspace/projects/inbox/memory-candidates/x.md",
        "page_type": "memory-candidate", "candidate_project": "demo",
    }
    assert project_scoped(candidate_doc, "wiki/workspace/projects/demo/", "demo")
    assert not project_scoped(candidate_doc, "wiki/workspace/projects/other/", "other")
    assert not project_scoped(candidate_doc, "wiki/workspace/projects/inbox/", "inbox")
    original_zone_records = zone_records
    original_load_approvals, original_load_mode = load_approvals, load_mode
    try:
        empty_page = VAULT / "wiki" / "Empty.md"
        records = [
            (empty_page, {}, "", "a" * 64, [1, 2, 3, 0, 1]),
            (VAULT / "wiki" / "hot.md", {"type": "meta"}, "", "b" * 64,
             [1, 3, 4, 0, 1]),
        ]
        def unexpected_zone_records(zone):
            raise AssertionError("provided records triggered a second zone read")
        globals()["zone_records"] = unexpected_zone_records
        globals()["load_approvals"] = lambda: {"l3": {}}
        globals()["load_mode"] = lambda: {}
        empty_snapshot = compose_snapshot("work", records)
        assert empty_snapshot["page_hashes"] == {"wiki/Empty.md": "a" * 64}
        assert empty_snapshot["page_count"] == 0
        assert scan_zone("work", records)["issue_count"] == 0
    finally:
        globals()["zone_records"] = original_zone_records
        globals()["load_approvals"] = original_load_approvals
        globals()["load_mode"] = original_load_mode
    original_zone_file_state = zone_file_state
    original_load_loadouts = load_loadouts
    original_load_index_authority = load_index_authority
    original_load_approvals, original_load_mode = load_approvals, load_mode
    try:
        file_state = {"wiki/Empty.md": [1, 2, 3, 0, 1]}
        snapshot = {
            "page_count": 0, "doc_count": 0,
            "page_hashes": {}, "docs": {}, "vocab": {},
        }
        index = {
            "schema_version": 3, "model": "l0-l3-canonical", "zone": "work",
            "loadouts_sha256": "c" * 64, "policy_issue_count": 0,
            "approvals_sha256": APPROVALS_MANIFEST_SHA256,
            "resolver": resolver_identity(),
            "file_state_sha256": snapshot_digest(file_state),
            "file_state": file_state,
            "snapshot_sha256": snapshot_digest(snapshot),
            **snapshot,
        }
        globals()["load_loadouts"] = lambda: ({}, "c" * 64)
        globals()["load_approvals"] = lambda: {"l3": {}}
        globals()["load_mode"] = lambda: {}
        globals()["zone_file_state"] = lambda zone: file_state
        globals()["load_index_authority"] = (
            lambda zone, require_checkpoint=True: index_authority(index)
        )
        assert validate_index(index, "work") is index
        for invalid_state in (
            {"wiki/Empty.md": [True, 2, 3, 0, 1]},
            {"wiki/Empty.md": [1, 2, 3, 0, 1.0]},
        ):
            forged = {**index, "file_state": invalid_state,
                      "file_state_sha256": snapshot_digest(invalid_state)}
            try:
                validate_index(forged, "work")
            except ValueError:
                pass
            else:
                raise AssertionError("invalid file-state manifest accepted")
        forged_snapshot = {
            **snapshot,
            "docs": {"forged": {"text": "forgedacceptancebeacon"}},
            "doc_count": 1,
        }
        forged_index = {
            **index, **forged_snapshot,
            "snapshot_sha256": snapshot_digest(forged_snapshot),
        }
        try:
            validate_index(forged_index, "work")
        except ValueError:
            pass
        else:
            raise AssertionError("forged memory index bypassed its authority")
        globals()["zone_file_state"] = lambda zone: {
            "wiki/Other.md": [1, 4, 5, 0, 1]
        }
        try:
            validate_index(index, "work")
        except ValueError:
            pass
        else:
            raise AssertionError("stale file-state manifest accepted")
    finally:
        globals()["zone_file_state"] = original_zone_file_state
        globals()["load_loadouts"] = original_load_loadouts
        globals()["load_index_authority"] = original_load_index_authority
        globals()["load_approvals"] = original_load_approvals
        globals()["load_mode"] = original_load_mode
    try:
        project_prefix("work", "../personal")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe project slug accepted")

    l3_path = VAULT / "wiki" / "resources" / "concepts" / "Synthetic L3.md"
    l3_relative = relative_path(l3_path)
    l3_hash = "a" * 64
    l3_frontmatter = {
        "memory_approved_by": L3_OWNER,
        "memory_approved": "2026-08-20",
    }
    l3_manifest = {"l3": {l3_relative: {
        "sha256": l3_hash,
        "approved_by": L3_OWNER,
        "approved_on": "2026-08-20",
    }}}
    assert l3_approval(
        l3_path, l3_frontmatter, l3_manifest, {}, l3_hash,
    ) == (True, "owner-approved-manifest")
    l3_negative_cases = (
        ({}, l3_manifest, l3_path, l3_hash),
        ({"memory_approved_by": "Other", "memory_approved": "2026-08-20"}, l3_manifest, l3_path, l3_hash),
        ({"memory_approved_by": L3_OWNER, "memory_approved": "not-a-date"}, l3_manifest, l3_path, l3_hash),
        ({"memory_approved_by": L3_OWNER, "memory_approved": "2999-01-01"}, l3_manifest, l3_path, l3_hash),
        (l3_frontmatter, {"l3": {}}, l3_path, l3_hash),
        (l3_frontmatter, l3_manifest, l3_path.with_name("Renamed L3.md"), l3_hash),
        (l3_frontmatter, l3_manifest, l3_path, "b" * 64),
        (l3_frontmatter, {"l3": {l3_relative: {
            "sha256": l3_hash, "approved_by": "Other", "approved_on": "2026-08-20",
        }}}, l3_path, l3_hash),
    )
    for frontmatter, approvals, path, page_hash in l3_negative_cases:
        approved, _ = l3_approval(path, frontmatter, approvals, {}, page_hash)
        if approved:
            raise AssertionError("invalid L3 approval tuple accepted")

    original_read_control_bytes = read_control_bytes
    try:
        globals()["read_control_bytes"] = lambda path, label: b'{"schema_version":1,"l3":{}}\n'
        try:
            load_approvals()
        except ValueError:
            pass
        else:
            raise AssertionError("unanchored L3 approval manifest bytes accepted")
    finally:
        globals()["read_control_bytes"] = original_read_control_bytes
    print("memory-model self-test: PASS")


def add_query_arguments(parser):
    parser.add_argument("query")
    parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    parser.add_argument("--project")
    parser.add_argument("--loadout")
    parser.add_argument("--top", type=positive_int)
    parser.add_argument("--include-l0", action="store_true")
    parser.add_argument("--include-stale", action="store_true")
    parser.add_argument("--lock-token")


def main():
    parser = argparse.ArgumentParser(description="Zone-scoped L0-L3 memory model")
    subparsers = parser.add_subparsers(dest="command")
    scan_parser = subparsers.add_parser("scan", help="Classify canonical pages")
    scan_parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    scan_parser.add_argument("--json", action="store_true")
    build_parser = subparsers.add_parser("build", help="Build a verified local index")
    build_parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    build_parser.add_argument("--lock-token")
    validate_parser = subparsers.add_parser("validate", help="Verify index against canonical Markdown")
    validate_parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    validate_parser.add_argument("--lock-token")
    retrieve_parser = subparsers.add_parser("retrieve", help="Retrieve an L3, L2, L1 loadout")
    add_query_arguments(retrieve_parser)
    inspect_parser = subparsers.add_parser("inspect", help="Explain a budgeted context loadout")
    add_query_arguments(inspect_parser)
    evaluate_parser = subparsers.add_parser("evaluate", help="Run fixed retrieval quality checks")
    evaluate_parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    subparsers.add_parser("list-loadouts", help="List named context loadouts and budgets")
    resolve_parser = subparsers.add_parser("resolve", help="Resolve one page's memory level")
    resolve_parser.add_argument("path")
    resolve_parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    subparsers.add_parser("self-test", help="Run deterministic checks")
    args = parser.parse_args()

    try:
        if args.command == "scan":
            result = scan_zone(args.zone)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("zone: %s | pages: %d | issues: %d" % (
                    result["zone"], result["pages"], result["issue_count"]
                ))
                print("levels: " + " | ".join(
                    "%s=%d" % (level.upper(), result["counts"][level]) for level in LEVELS
                ) + " | cache=%d" % result["counts"]["cache"])
                print("explicit: %d | legacy unverified: %d" % (
                    sum(result["explicit"].values()), result["legacy_unverified"]
                ))
            return EXIT_OK if result["issue_count"] == 0 else 1
        if args.command == "build":
            result = build_index(args.zone, args.lock_token)
            print(json.dumps({
                "zone": args.zone, "built_at": result["built_at"],
                "pages": result["page_count"], "chunks": result["doc_count"],
                "snapshot_sha256": result["snapshot_sha256"],
                "loadouts_sha256": result["loadouts_sha256"],
                "path": str(index_path(args.zone)),
            }, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "validate":
            result = validate_live_index(args.zone, args.lock_token)
            print(json.dumps({
                "zone": args.zone, "valid": True, "built_at": result["built_at"],
                "pages": result["page_count"], "chunks": result["doc_count"],
                "snapshot_sha256": result["snapshot_sha256"],
                "loadouts_sha256": result["loadouts_sha256"],
            }, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command in {"retrieve", "inspect"}:
            print(json.dumps(retrieve(
                args.query, args.zone, top=args.top, include_l0=args.include_l0,
                include_stale=args.include_stale, project=args.project,
                loadout_name=args.loadout, explain=args.command == "inspect",
                lock_token=args.lock_token,
            ), ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "evaluate":
            print(json.dumps(evaluate_retrieval(args.zone), ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "list-loadouts":
            manifest, manifest_sha256 = load_loadouts()
            print(json.dumps({
                "schema_version": manifest["schema_version"],
                "default": manifest["default"],
                "loadouts_sha256": manifest_sha256,
                "roles": {
                    name: {
                        **role,
                        "pinned_skills": [
                            skill_metadata(path) for path in role["pinned_skills"]
                        ],
                    }
                    for name, role in sorted(manifest["roles"].items())
                },
            }, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "resolve":
            canonical_zone_root(args.zone)
            path = Path(args.path)
            if not path.is_absolute():
                path = VAULT / path
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(VAULT)
            except ValueError:
                raise ValueError("page is outside the vault")
            except FileNotFoundError:
                raise ValueError("page not found")
            if resolved != path.absolute():
                raise ValueError("page path must not contain symlinks")
            if not in_zone(path, args.zone):
                raise ValueError("page is outside the active zone")
            if not path.is_file() or path.is_symlink():
                raise ValueError("page not found or is a symlink")
            frontmatter, _, _ = read_page(path)
            print(json.dumps(classify(path, frontmatter), ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "self-test":
            self_test()
            return EXIT_OK
    except FileNotFoundError:
        print(
            "ERR: no valid L0-L3 index or approval manifest. Build the active zone index.",
            file=sys.stderr,
        )
        return EXIT_NOT_BUILT
    except (ValueError, PermissionError, json.JSONDecodeError) as error:
        print("ERR: %s" % error, file=sys.stderr)
        return 1
    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
