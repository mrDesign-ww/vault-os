#!/usr/bin/env python3
"""Create and inspect approval-gated L0 memory candidates."""

import argparse
import importlib.util
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from datetime import date
from pathlib import Path


VAULT = Path(__file__).resolve().parents[1]
MODEL_PATH = VAULT / "scripts" / "memory-model.py"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_SEPARATOR = " | "


def load_model():
    specification = importlib.util.spec_from_file_location("vault_memory_model", MODEL_PATH)
    if specification is None or specification.loader is None:
        raise ValueError("memory model is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


MM = load_model()


def slugify(value):
    return MM.candidate_slug(value)


def yaml_scalar(value):
    return json.dumps(str(value), ensure_ascii=False)


def canonical_source(value, zone):
    path = Path(value)
    if not path.is_absolute():
        path = VAULT / path
    absolute = Path(os.path.abspath(str(path)))
    try:
        relative = MM.relative_path(absolute)
    except ValueError as error:
        raise ValueError("candidate source is missing or outside the vault") from error
    if not MM.in_zone(absolute, zone):
        raise ValueError("candidate source is outside the active zone")
    return MM.candidate_source_record(relative, zone)


def evidence_digest(sources):
    return MM.candidate_evidence_digest(sources)


def verify_sources(sources, zone):
    current = [canonical_source(source["path"], zone) for source in sources]
    if current != sources:
        raise ValueError("candidate source bytes changed before commit")


def candidate_fingerprint(args, sources):
    return MM.candidate_fingerprint(
        args.zone, args.project or "", args.target_level,
        args.title, args.statement, sources,
    )


def candidate_identifier(title, fingerprint):
    return MM.candidate_identifier(title, fingerprint)


def candidate_markdown(args, candidate_id, fingerprint, sources, created_on=None):
    today = (created_on or date.today()).isoformat()
    project = args.project or ""
    source_scalar = SOURCE_SEPARATOR.join(source["path"] for source in sources)
    source_hashes = SOURCE_SEPARATOR.join(source["sha256"] for source in sources)
    source_list = "\n".join(
        "- `%s` (SHA-256 `%s`)" % (source["path"], source["sha256"])
        for source in sources
    )
    recorded_evidence = evidence_digest(sources)
    return """---
type: memory-candidate
title: %s
status: candidate
created: %s
updated: %s
memory_level: l0
memory_provenance: "candidate only; canonical sources listed below"
candidate_id: %s
candidate_fingerprint: %s
candidate_status: pending
candidate_target_level: %s
candidate_project: %s
candidate_confidence: %s
candidate_proposed_by: %s
candidate_sources: %s
candidate_source_hashes: %s
candidate_evidence_sha256: %s
tags:
  - memory
  - candidate
  - approval-required
---

# %s

> Candidate only. It is excluded from default retrieval until evidence is
> verified and a separate L1 or L2 canonical page is approved.

## Proposed memory

%s

## Evidence

%s

## Approval checklist

- [ ] Source and exact wording verified.
- [ ] Recorded source SHA-256 still matches the source bytes.
- [ ] Current project and zone confirmed.
- [ ] Conflicting or superseded memory checked.
- [ ] Owner approved promotion to %s.
""" % (
        yaml_scalar(args.title), today, today, yaml_scalar(candidate_id),
        fingerprint,
        args.target_level, yaml_scalar(project), args.confidence,
        args.proposed_by, yaml_scalar(source_scalar), yaml_scalar(source_hashes),
        recorded_evidence, args.title,
        args.statement.strip(), source_list, args.target_level.upper(),
    )


def existing_candidate(
    root, zone, fingerprint, verify_sources=True, verify_project=True,
):
    directory = MM.open_real_directory(root, "memory candidate directory")
    root_identity = MM.directory_identity(os.fstat(directory))
    match = None
    try:
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".md") or name == "_index.md":
                continue
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("candidate inbox contains a non-regular entry")
            descriptor = os.open(
                name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory
            )
            opened = os.fstat(descriptor)
            if MM.file_identity(opened) != MM.file_identity(metadata):
                os.close(descriptor)
                raise ValueError("candidate page changed before open")
            frontmatter, body, _ = MM.read_page_descriptor(descriptor, root / name)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if MM.file_identity(current) != MM.file_identity(opened):
                raise ValueError("candidate page changed after read")
            identity = MM.validate_candidate_identity(
                zone, frontmatter, body,
                verify_sources=verify_sources, verify_project=verify_project,
            )
            if identity["fingerprint"] == fingerprint:
                match = identity["candidate_id"]
                break
    finally:
        os.close(directory)
    current_root = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current_root.st_mode)
        or MM.directory_identity(current_root) != root_identity
    ):
        raise ValueError("candidate inbox changed during duplicate check")
    return match


def atomic_new_page(directory_path, filename, raw, before_commit=lambda: None):
    directory = MM.open_real_directory(directory_path, "memory candidate directory")
    temporary = ".%s-%s.tmp" % (filename, secrets.token_hex(8))
    committed = False
    committed_identity = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            before_commit()
            os.link(
                temporary, filename,
                src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False,
            )
            committed = True
            committed_identity = MM.file_identity(
                os.stat(filename, dir_fd=directory, follow_symlinks=False)
            )
            os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
            before_commit()
            opened = os.fstat(directory)
            current = os.stat(directory_path, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or MM.directory_identity(opened) != MM.directory_identity(current):
                raise ValueError("memory candidate directory changed during commit")
            destination = os.stat(filename, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(destination.st_mode)
                or destination.st_nlink != 1
                or MM.file_identity(destination) != committed_identity
            ):
                raise ValueError("memory candidate changed after commit")
        except Exception:
            if committed:
                try:
                    current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    current = None
                if current is not None and MM.file_identity(current) == committed_identity:
                    os.unlink(filename, dir_fd=directory)
                    os.fsync(directory)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            raise
    finally:
        os.close(directory)


def propose(args):
    args.title = MM.validate_candidate_title(args.title)
    args.statement = MM.validate_candidate_statement(args.statement)
    MM.require_zone_lock(args.zone, args.lock_token)
    MM.validate_candidate_project(args.zone, args.project)
    source_pairs = {
        (record["path"], record["sha256"])
        for record in (canonical_source(source, args.zone) for source in args.source)
    }
    sources = [
        {"path": path, "sha256": digest}
        for path, digest in sorted(source_pairs)
    ]
    fingerprint = candidate_fingerprint(args, sources)
    candidate_id = candidate_identifier(args.title, fingerprint)
    filename = candidate_id + ".md"
    raw = candidate_markdown(args, candidate_id, fingerprint, sources).encode("utf-8")
    rendered_frontmatter, rendered_body = MM.parse_text(raw.decode("utf-8"))
    rendered_identity = MM.validate_candidate_identity(
        args.zone, rendered_frontmatter, rendered_body
    )
    if (
        rendered_identity["candidate_id"] != candidate_id
        or rendered_identity["fingerprint"] != fingerprint
    ):
        raise ValueError("rendered candidate identity differs before commit")
    root = MM.candidate_root(args.zone)
    with MM.zone_commit_guard(args.zone, args.lock_token) as check_owner:
        duplicate = existing_candidate(root, args.zone, fingerprint)
        if duplicate:
            raise ValueError("exact candidate already exists: %s" % duplicate)

        def check_owner_and_sources():
            check_owner()
            MM.validate_candidate_project(args.zone, args.project)
            verify_sources(sources, args.zone)

        try:
            atomic_new_page(root, filename, raw, before_commit=check_owner_and_sources)
        except FileExistsError as error:
            raise ValueError("exact candidate already exists: %s" % candidate_id) from error
    return {
        "candidate_id": candidate_id,
        "candidate_fingerprint": fingerprint,
        "page_path": MM.relative_path(root / filename),
        "status": "pending",
        "target_level": args.target_level,
        "project": args.project,
        "sources": [source["path"] for source in sources],
        "source_sha256": {source["path"]: source["sha256"] for source in sources},
        "evidence_sha256": evidence_digest(sources),
        "requires_catalog_and_index_refresh": True,
        "required_next_steps": [
            "update candidate catalog, work log, and hot cache",
            "build and validate the work index under the same owner token",
            "create a scoped local Git checkpoint before releasing the token",
            "release the token and run a post-release candidate/retrieval check",
        ],
    }


def candidate_evidence_status(frontmatter, zone):
    paths = [
        value for value in frontmatter.get("candidate_sources", "").split(SOURCE_SEPARATOR)
        if value
    ]
    hashes = [
        value for value in frontmatter.get("candidate_source_hashes", "").split(SOURCE_SEPARATOR)
        if value
    ]
    recorded = frontmatter.get("candidate_evidence_sha256", "")
    if (
        not paths or len(paths) != len(hashes)
        or any(not SHA256_RE.fullmatch(value) for value in hashes)
        or not SHA256_RE.fullmatch(recorded)
    ):
        return {
            "valid": False, "recorded_sha256": recorded or None,
            "current_sha256": None, "error": "candidate evidence metadata is invalid",
        }
    sources = [
        {"path": path, "sha256": digest}
        for path, digest in zip(paths, hashes)
    ]
    if evidence_digest(sources) != recorded:
        return {
            "valid": False, "recorded_sha256": recorded,
            "current_sha256": None, "error": "candidate evidence digest is inconsistent",
        }
    try:
        current = [canonical_source(path, zone) for path in paths]
    except ValueError as error:
        return {
            "valid": False, "recorded_sha256": recorded,
            "current_sha256": None, "error": str(error),
        }
    current_digest = evidence_digest(current)
    return {
        "valid": current == sources and current_digest == recorded,
        "recorded_sha256": recorded,
        "current_sha256": current_digest,
        "sources": [
            {
                "path": source["path"],
                "recorded_sha256": source["sha256"],
                "current_sha256": current_item["sha256"],
                "matches": source == current_item,
            }
            for source, current_item in zip(sources, current)
        ],
    }


def list_candidates(zone):
    active, generation = MM.zone_write_state(zone)
    if active:
        raise ValueError("zone write in progress; retry candidate listing after release")
    root = MM.candidate_root(zone)
    directory = MM.open_real_directory(root, "memory candidate directory")
    root_identity = MM.directory_identity(os.fstat(directory))
    items = []
    try:
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".md") or name == "_index.md":
                continue
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("candidate inbox contains a non-regular entry")
            descriptor = os.open(
                name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory
            )
            opened = os.fstat(descriptor)
            if MM.file_identity(opened) != MM.file_identity(metadata):
                os.close(descriptor)
                raise ValueError("candidate page changed before open")
            frontmatter, body, _ = MM.read_page_descriptor(descriptor, root / name)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if MM.file_identity(current) != MM.file_identity(opened):
                raise ValueError("candidate page changed after read")
            identity = MM.validate_candidate_identity(
                zone, frontmatter, body, verify_sources=False
            )
            items.append({
                "candidate_id": identity["candidate_id"],
                "candidate_fingerprint": identity["fingerprint"],
                "identity_schema": identity["schema"],
                "title": frontmatter.get("title", Path(name).stem),
                "status": frontmatter.get("candidate_status", frontmatter.get("status", "")),
                "target_level": frontmatter.get("candidate_target_level", ""),
                "project": frontmatter.get("candidate_project", "") or None,
                "page_path": MM.relative_path(root / name),
                "evidence": candidate_evidence_status(frontmatter, zone),
            })
    finally:
        os.close(directory)
    current_root = os.stat(root, follow_symlinks=False)
    final_active, final_generation = MM.zone_write_state(zone)
    if (
        not stat.S_ISDIR(current_root.st_mode)
        or MM.directory_identity(current_root) != root_identity
        or final_active or final_generation != generation
    ):
        raise ValueError("candidate inbox changed during listing; retry")
    return {"zone": zone, "count": len(items), "items": items}


def self_test():
    assert slugify("Candidate Name") == "candidate-name"
    assert slugify("Решение").startswith("candidate-")
    sources = [{"path": "wiki/source.md", "sha256": "a" * 64}]
    assert SHA256_RE.fullmatch(evidence_digest(sources))
    args = argparse.Namespace(
        zone="work", project="demo", title="Candidate", target_level="l2",
        confidence="medium", proposed_by="codex", statement="Claim",
    )
    fingerprint = candidate_fingerprint(args, sources)
    candidate_id = candidate_identifier(args.title, fingerprint)
    metadata_variant = argparse.Namespace(
        zone="work", project="demo", title="Candidate", target_level="l2",
        confidence="high", proposed_by="owner", statement="Claim",
    )
    assert candidate_fingerprint(metadata_variant, sources) == fingerprint
    first_day = candidate_markdown(
        args, candidate_id, fingerprint, sources, date(2026, 8, 20)
    )
    next_day = candidate_markdown(
        args, candidate_id, fingerprint, sources, date(2026, 8, 21)
    )
    assert candidate_id in first_day and candidate_id in next_day
    assert "candidate_fingerprint: %s" % fingerprint in first_day
    assert "candidate_source_hashes" in first_day and evidence_digest(sources) in first_day
    quoted_title = 'Candidate "quoted" \\ path'
    assert MM.strip_quotes(yaml_scalar(quoted_title)) == quoted_title
    with tempfile.TemporaryDirectory(prefix="memory-candidate-test-") as directory:
        root = Path(os.path.realpath(directory))
        atomic_new_page(root, "candidate.md", b"first")
        assert (root / "candidate.md").read_bytes() == b"first"
        try:
            atomic_new_page(root, "candidate.md", b"second")
        except FileExistsError:
            pass
        else:
            raise AssertionError("candidate writer overwrote an existing page")
        (root / "candidate.md").unlink()
        callbacks = 0

        def replace_destination():
            nonlocal callbacks
            callbacks += 1
            if callbacks == 2:
                (root / "race.md").write_bytes(b"replacement")

        try:
            atomic_new_page(root, "race.md", b"expected", replace_destination)
        except ValueError:
            pass
        else:
            raise AssertionError("post-link candidate replacement was accepted")
        assert (root / "race.md").read_bytes() == b"replacement"
        (root / "race.md").unlink()
        callbacks = 0

        def link_destination():
            nonlocal callbacks
            callbacks += 1
            if callbacks == 2:
                os.link(root / "linked.md", root / "linked-alias.bin")

        try:
            atomic_new_page(root, "linked.md", b"expected", link_destination)
        except ValueError:
            pass
        else:
            raise AssertionError("hard-linked candidate destination was accepted")
        assert not (root / "linked.md").exists()
        assert (root / "linked-alias.bin").read_bytes() == b"expected"
        (root / "linked-alias.bin").unlink()
        legacy_id = "2026-08-20-%s-%s" % (slugify(args.title), fingerprint[:10])
        duplicate_page = candidate_markdown(
            args, legacy_id, fingerprint, sources, date(2026, 8, 20)
        ).replace("candidate_fingerprint: %s\n" % fingerprint, "")
        atomic_new_page(root, "renamed-historical.md", duplicate_page.encode("utf-8"))
        assert existing_candidate(
            root, "work", fingerprint,
            verify_sources=False, verify_project=False,
        ) == legacy_id
    print("memory-candidates self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description="Approval-gated L0 memory candidates")
    subparsers = parser.add_subparsers(dest="command")
    propose_parser = subparsers.add_parser("propose", help="Create a new L0 candidate")
    propose_parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    propose_parser.add_argument("--project")
    propose_parser.add_argument("--title", required=True)
    propose_parser.add_argument("--statement", required=True)
    propose_parser.add_argument("--target-level", choices=("l1", "l2"), required=True)
    propose_parser.add_argument("--confidence", choices=("low", "medium", "high"), default="medium")
    propose_parser.add_argument("--proposed-by", choices=("codex", "claude", "owner"), required=True)
    propose_parser.add_argument("--source", action="append", required=True)
    propose_parser.add_argument("--lock-token", required=True)
    list_parser = subparsers.add_parser("list", help="List candidates in one active zone")
    list_parser.add_argument("--zone", choices=("work", "personal", "client"), default="work")
    subparsers.add_parser("self-test", help="Run deterministic checks")
    args = parser.parse_args()
    try:
        if args.command == "propose":
            print(json.dumps(propose(args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "list":
            print(json.dumps(list_candidates(args.zone), ensure_ascii=False, indent=2))
            return 0
        if args.command == "self-test":
            self_test()
            return 0
    except (ValueError, PermissionError, FileExistsError, json.JSONDecodeError) as error:
        print("ERR: %s" % error, file=sys.stderr)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
