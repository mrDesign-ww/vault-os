#!/usr/bin/env python3
"""Cycle-free live evidence validation for autonomous reflection outcomes."""

import datetime
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
VAULT = HERE.parents[1]
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40,64}\Z")
ACTIONS = {"rejected", "materialized-l1", "materialized-l2", "provisional-l2"}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = load_module("reflection_evidence_policy", HERE / "reflection_policy.py")
MEMORY = load_module("reflection_evidence_memory", VAULT / "scripts" / "memory-model.py")


def parse_timestamp(value, label):
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("%s is not an ISO timestamp" % label) from error
    if parsed.tzinfo is None:
        raise ValueError("%s lacks a timezone" % label)
    return parsed.astimezone(datetime.timezone.utc)


def iso_utc(value):
    return value.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def git(*args):
    result = subprocess.run(
        ["git", *args], cwd=VAULT, capture_output=True, text=False,
    )
    if result.returncode:
        raise ValueError("checkpoint-invalid")
    return result.stdout


def work_page(path, writable=True, require_live=True):
    relative = Path(str(path))
    if (
        relative.is_absolute() or relative.suffix != ".md" or ".." in relative.parts
        or not relative.parts or relative.parts[0] != "wiki"
        or relative.parts[:2] in (("wiki", "personal"), ("wiki", "client"))
        or relative.as_posix() != str(path)
        or (
            writable and (
                "sources" in relative.parts
                or relative.as_posix()
                == "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md"
            )
        )
    ):
        raise ValueError("disposition-page-zone")
    candidate = Path(os.path.abspath(str(VAULT / relative)))
    if require_live and candidate.resolve(strict=True) != candidate:
        raise ValueError("disposition-page-path")
    return relative.as_posix(), candidate


def git_blob(commit, path, allowed_modes=(b"100644",)):
    if not COMMIT_RE.fullmatch(str(commit or "")):
        raise ValueError("checkpoint-invalid")
    raw_entry = git("ls-tree", "-z", commit, "--", path)
    entries = [item for item in raw_entry.split(b"\0") if item]
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise ValueError("checkpoint-page")
    header, encoded_path = entries[0].split(b"\t", 1)
    fields = header.split()
    if (
        len(fields) < 2 or fields[0] not in allowed_modes or fields[1] != b"blob"
        or encoded_path.decode("utf-8", errors="strict") != path
    ):
        raise ValueError("checkpoint-page")
    return git("show", commit + ":" + path)


def checkpoint_memory(commit, expected_resolver=None):
    path = "scripts/memory-model.py"
    raw = git_blob(commit, path, allowed_modes=(b"100644", b"100755"))
    digest = hashlib.sha256(raw).hexdigest()
    with tempfile.TemporaryDirectory(prefix="checkpoint-memory-") as directory:
        target = Path(directory) / "scripts" / "memory-model.py"
        target.parent.mkdir(mode=0o700)
        target.write_bytes(raw)
        target.chmod(0o600)
        memory = load_module("checkpoint_memory_%s" % digest[:12], target)
    try:
        mode = memory.validate_mode(json.loads(
            git_blob(commit, ".vault-meta/mode.json").decode("utf-8")
        ))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("checkpoint-mode-drift") from error
    identity = {
        "version": memory.CONTEXT_RESOLVER_VERSION,
        "path": memory.CONTEXT_RESOLVER_PATH,
        "sha256": digest,
    }
    if expected_resolver is not None and expected_resolver != identity:
        raise ValueError("checkpoint-resolver-drift")
    memory.resolver_identity = lambda: dict(identity)
    memory.load_mode = lambda: mode
    return memory


def validate_signal_plan(
    plan, scan, policy, policy_sha, platform, manifest_sha, scan_sha,
):
    verifier = plan.get("verifier") if isinstance(plan, dict) else None
    checkpoint = plan.get("checkpoint") if isinstance(plan, dict) else None
    verifier_field = "verifier_%s_sha256" % platform
    if (
        not isinstance(plan, dict)
        or set(plan) != {
            "schema_version", "verdict", "platform", "manifest_sha256",
            "scan_receipt_sha256", "policy_sha256", "verifier", "checkpoint",
            "signals",
        }
        or type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != 2
        or plan.get("verdict") != "PASS"
        or plan.get("platform") != platform
        or plan.get("policy_sha256") != policy_sha
        or plan.get("manifest_sha256") != manifest_sha
        or plan.get("scan_receipt_sha256") != scan_sha
        or verifier != {
            "name": "evolution-verifier", "verdict": "PASS",
            "skill_sha256": policy["code"][verifier_field],
        }
        or not isinstance(checkpoint, dict)
        or set(checkpoint) != {"parent_sha", "commit_sha", "changed_paths"}
        or not isinstance(plan.get("signals"), list)
    ):
        raise ValueError("disposition-plan")
    for field in ("parent_sha", "commit_sha"):
        if not COMMIT_RE.fullmatch(str(checkpoint.get(field, ""))):
            raise ValueError("disposition-plan-checkpoint")
    paths = checkpoint.get("changed_paths")
    if not isinstance(paths, list):
        raise ValueError("disposition-plan-checkpoint")
    for item in paths:
        if (
            not isinstance(item, dict) or set(item) != {"path", "sha256"}
            or not isinstance(item.get("path"), str)
            or not item["path"] or "\x00" in item["path"]
            or not SHA256_RE.fullmatch(str(item.get("sha256", "")))
        ):
            raise ValueError("disposition-plan-checkpoint")
    if paths != sorted(paths, key=lambda item: item["path"]) or len(
        {item["path"] for item in paths}
    ) != len(paths):
        raise ValueError("disposition-plan-checkpoint")
    normalized = []
    seen = set()
    for item in plan["signals"]:
        action = item.get("action") if isinstance(item, dict) else None
        expected = {"signal_id", "action"} if action == "rejected" else {
            "signal_id", "action", "page_path",
        }
        signal_id = item.get("signal_id") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict) or set(item) != expected
            or action not in ACTIONS
            or not SHA256_RE.fullmatch(str(signal_id or "")) or signal_id in seen
        ):
            raise ValueError("disposition-signal")
        if action != "rejected":
            work_page(item["page_path"])
        seen.add(signal_id)
        normalized.append(dict(item))
    if normalized != sorted(normalized, key=lambda item: item["signal_id"]):
        raise ValueError("disposition-signal-order")
    ids = [item["signal_id"] for item in normalized]
    if (
        len(ids) != scan["signal_count"]
        or hashlib.sha256(POLICY.canonical_json_bytes(ids)).hexdigest()
        != scan["signal_set_sha256"]
    ):
        raise ValueError("disposition-signal-set")
    return normalized, hashlib.sha256(POLICY.canonical_json_bytes(plan)).hexdigest()


def page_outcomes(signals, manifest_sha, scan_sha, commit=None):
    outcomes = {}
    page_signals = {}
    expected_levels = {
        "materialized-l1": ("l1", False),
        "materialized-l2": ("l2", False),
        "provisional-l2": ("l2", True),
    }
    for signal in signals:
        if signal["action"] == "rejected":
            continue
        relative, path = work_page(
            signal["page_path"], require_live=commit is None,
        )
        page_signals.setdefault(relative, []).append(signal["signal_id"])
        if commit is None:
            frontmatter, _, page_sha = MEMORY.read_page(path)
        else:
            raw = git_blob(commit, relative)
            try:
                frontmatter, _ = MEMORY.parse_text(raw.decode("utf-8"))
            except UnicodeDecodeError as error:
                raise ValueError("checkpoint-page") from error
            page_sha = hashlib.sha256(raw).hexdigest()
        classification = MEMORY.classify(path, frontmatter)
        expected_level, provisional = expected_levels[signal["action"]]
        if (
            frontmatter.get("memory_level", "").strip().lower() != expected_level
            or classification.get("level") != expected_level
            or classification.get("issues")
            or not frontmatter.get("memory_provenance")
            or frontmatter.get("reflection_manifest_sha256") != manifest_sha
            or frontmatter.get("reflection_scan_receipt_sha256") != scan_sha
        ):
            raise ValueError("disposition-page-trust")
        try:
            provenance_signals = json.loads(
                frontmatter.get("reflection_signal_ids", "")
            )
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("disposition-page-provenance") from error
        if (
            not isinstance(provenance_signals, list)
            or any(not SHA256_RE.fullmatch(str(item)) for item in provenance_signals)
            or provenance_signals != sorted(set(provenance_signals))
        ):
            raise ValueError("disposition-page-provenance")
        provisional_page = (
            frontmatter.get("memory_approval_mode", "").strip().lower() == "provisional"
            and frontmatter.get("promotion_eligible", "").strip().lower() == "false"
        )
        if provisional_page != provisional:
            raise ValueError("disposition-page-trust")
        outcome = {
            "page_path": relative,
            "page_sha256": page_sha,
            "memory_level": expected_level,
            "memory_trust": classification.get("trust"),
            "provisional": provisional,
        }
        prior = outcomes.get(relative)
        if prior is not None and prior != outcome:
            raise ValueError("disposition-page-conflict")
        outcomes[relative] = outcome
    for path, signal_ids in page_signals.items():
        if commit is None:
            frontmatter, _, _ = MEMORY.read_page(VAULT / path)
        else:
            frontmatter, _ = MEMORY.parse_text(
                git_blob(commit, path).decode("utf-8")
            )
        if json.loads(frontmatter["reflection_signal_ids"]) != sorted(signal_ids):
            raise ValueError("disposition-page-provenance")
    return [outcomes[key] for key in sorted(outcomes)]


def assert_checkpoint_preserves_l3(parent, changed_paths):
    approvals_raw = git("show", parent + ":.vault-meta/memory-approvals.json")
    parent_memory = checkpoint_memory(parent)
    if (
        hashlib.sha256(approvals_raw).hexdigest()
        != parent_memory.APPROVALS_MANIFEST_SHA256
    ):
        raise ValueError("checkpoint-l3-approval-drift")
    approvals = POLICY.strict_json_loads(
        approvals_raw, "parent L3 approval manifest",
    )
    approved = approvals.get("l3") if isinstance(approvals, dict) else None
    if not isinstance(approved, dict):
        raise ValueError("checkpoint-l3-approval-drift")
    for status, path in changed_paths:
        if path in approved:
            raise ValueError("checkpoint-l3-mutation")
        if status != "M":
            continue
        try:
            frontmatter, _ = MEMORY.parse_text(
                git("show", parent + ":" + path).decode("utf-8", errors="strict")
            )
        except UnicodeDecodeError as error:
            raise ValueError("checkpoint-parent-page") from error
        if frontmatter.get("memory_level", "").strip().lower() == "l3":
            raise ValueError("checkpoint-l3-mutation")


def checkpoint_evidence(expected, outcomes, require_head=False):
    if not isinstance(expected, dict):
        raise ValueError("checkpoint-invalid")
    commit = expected.get("commit_sha")
    parent = expected.get("parent_sha")
    expected_paths = expected.get("changed_paths")
    if not COMMIT_RE.fullmatch(str(commit or "")) or not COMMIT_RE.fullmatch(str(parent or "")):
        raise ValueError("checkpoint-invalid")
    resolved = git("rev-parse", "--verify", commit + "^{commit}").decode().strip()
    if resolved != commit:
        raise ValueError("checkpoint-invalid")
    if require_head and commit != git("rev-parse", "HEAD").decode().strip():
        raise ValueError("checkpoint-not-head")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=VAULT, capture_output=True,
    ).returncode:
        raise ValueError("checkpoint-not-reachable")
    if not outcomes:
        if parent != commit or expected_paths != []:
            raise ValueError("checkpoint-empty-disposition-must-not-mutate")
        paths = []
    else:
        lineage = git("rev-list", "--parents", "-n", "1", commit).decode().split()
        if len(lineage) != 2 or lineage != [commit, parent]:
            raise ValueError("checkpoint-parent")
        raw = git(
            "diff-tree", "--no-commit-id", "--name-status", "-r",
            "--no-renames", "-z", parent, commit,
        )
        parts = raw.split(b"\0")
        if parts and parts[-1] == b"":
            parts.pop()
        if len(parts) % 2:
            raise ValueError("checkpoint-diff")
        outcome_paths = {item["page_path"] for item in outcomes}
        paths = []
        changed = []
        for status_raw, path_raw in zip(parts[0::2], parts[1::2]):
            try:
                status = status_raw.decode("ascii")
                path = path_raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("checkpoint-diff") from error
            if status not in ("A", "M") or path not in outcome_paths:
                raise ValueError("checkpoint-path-scope")
            changed.append((status, path))
            paths.append({
                "path": path,
                "sha256": hashlib.sha256(git("show", commit + ":" + path)).hexdigest(),
            })
        assert_checkpoint_preserves_l3(parent, changed)
        paths.sort(key=lambda item: item["path"])
        if paths != expected_paths:
            raise ValueError("checkpoint-reviewed-diff-drift")
        by_path = {item["path"]: item["sha256"] for item in paths}
        for outcome in outcomes:
            if by_path.get(outcome["page_path"]) != outcome["page_sha256"]:
                raise ValueError("checkpoint-page-drift")
    committed_at = git("show", "-s", "--format=%cI", commit).decode().strip()
    return {
        "parent_sha": parent, "commit_sha": commit,
        "committed_at": iso_utc(parse_timestamp(committed_at, "checkpoint time")),
        "changed_paths": paths,
        "changed_paths_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes(paths)
        ).hexdigest(),
    }


def snippet_evidence(snippet, text):
    if not isinstance(snippet, str):
        raise ValueError("acceptance-snippet")
    source = text.strip()
    prefix = snippet.startswith("… ")
    suffix = snippet.endswith(" …")
    body = snippet[2:] if prefix else snippet
    body = body[:-2] if suffix else body
    candidates = []
    start = source.find(body)
    while start >= 0:
        end = start + len(body)
        if prefix == (start > 0) and suffix == (end < len(source)):
            candidates.append((start, end))
        start = source.find(body, start + 1)
    if not candidates:
        raise ValueError("acceptance-snippet")
    start, end = candidates[0]
    return {
        "snippet_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
        "snippet_start": start, "snippet_end": end,
        "snippet_prefix": prefix, "snippet_suffix": suffix,
    }


def acceptance_evidence(result, outcomes, lock_token=None):
    receipt = MEMORY.validate_result_receipt(result, lock_token=lock_token)
    if (
        result.get("zone") != "work" or result.get("include_l0") is not False
        or result.get("include_stale") is not False
    ):
        raise ValueError("acceptance-scope")
    if lock_token is not None:
        MEMORY.require_zone_lock("work", lock_token)
    docs = MEMORY.load_index(
        "work", require_checkpoint=lock_token is None,
    )["docs"]
    if lock_token is not None:
        MEMORY.require_zone_lock("work", lock_token)
    by_identity = {
        (doc["page_path"], doc["chunk_index"]): doc for doc in docs.values()
    }
    selected = []
    for item in receipt["selected_chunks"]:
        doc = by_identity.get((item["page_path"], item["chunk_index"]))
        if (
            doc is None or doc["page_sha256"] != item["page_sha256"]
            or doc["chunk_sha256"] != item["chunk_sha256"]
        ):
            raise ValueError("acceptance-index-drift")
        selected.append({
            "page_path": item["page_path"],
            "page_sha256": item["page_sha256"],
            "chunk_index": item["chunk_index"],
            "chunk_sha256": item["chunk_sha256"],
            **snippet_evidence(item["snippet"], doc["text"]),
        })
    if not {item["page_path"] for item in outcomes}.issubset(
        {item["page_path"] for item in selected}
    ):
        raise ValueError("acceptance-missing-outcome")
    return {
        "context_receipt_sha256": receipt["receipt_sha256"],
        "resolver": receipt["resolver"],
        "query_sha256": receipt["query_sha256"],
        "zone": receipt["zone"],
        "loadout": receipt["loadout"], "project": receipt["project"],
        "include_l0": receipt["include_l0"],
        "include_stale": receipt["include_stale"],
        "explain": receipt["explain"],
        "effective_item_limit": receipt["effective_item_limit"],
        "index_snapshot_sha256": receipt["index_snapshot_sha256"],
        "loadouts_sha256": receipt["loadouts_sha256"],
        "selected_chunks": selected,
        "l3_pages": receipt["l3_pages"],
    }


def loadouts_at_commit(commit, memory=MEMORY):
    raw = git_blob(commit, ".vault-meta/memory-loadouts.json")
    value = POLICY.strict_json_loads(raw, "checkpoint memory loadouts")
    try:
        value = memory.validate_loadouts(value)
    except (AttributeError, ValueError) as error:
        raise ValueError("disposition-retrieval-evidence") from error
    roles = value["roles"]
    if not isinstance(roles, dict):
        raise ValueError("disposition-retrieval-evidence")
    identity = hashlib.sha256()
    identity.update(b"memory-loadout-v2\0")
    identity.update(raw)
    for role_name in sorted(roles):
        skills = roles[role_name].get("pinned_skills")
        if not isinstance(skills, list):
            raise ValueError("disposition-retrieval-evidence")
        for path in sorted(skills):
            identity.update(b"\0")
            identity.update(role_name.encode("utf-8"))
            identity.update(b"\0")
            identity.update(path.encode("utf-8"))
            identity.update(b"\0")
            identity.update(hashlib.sha256(git_blob(commit, path)).hexdigest().encode("ascii"))
    return value, identity.hexdigest()


def loadouts_identity_at_commit(commit):
    return loadouts_at_commit(commit)[1]


def commit_documents(commit, paths, memory=MEMORY):
    docs = {}
    for page_path in sorted(set(paths)):
        relative, _ = work_page(
            page_path, writable=False, require_live=False,
        )
        raw = git_blob(commit, relative)
        try:
            frontmatter, body = memory.parse_text(raw.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise ValueError("checkpoint-page") from error
        classified = memory.classify(memory.VAULT / relative, frontmatter)
        page_sha = hashlib.sha256(raw).hexdigest()
        status = frontmatter.get("status", "").strip()
        if not status and memory.status_is_stale(relative):
            status = "inferred-stale-path"
        for chunk_index, (text, section_stale) in enumerate(memory.chunk_body(body)):
            docs[(relative, chunk_index)] = {
                "page_path": relative, "page_sha256": page_sha,
                "chunk_index": chunk_index,
                "chunk_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "page_type": frontmatter.get("type", "").strip().lower(),
                "memory_level": classified["level"],
                "chunk_stale": memory.status_is_stale(status) or section_stale,
                "candidate_project": frontmatter.get(
                    "candidate_project", ""
                ).strip(),
            }
    return docs


def validate_acceptance(value, outcomes, commit=None, lock_token=None):
    memory = MEMORY
    checkpoint_loadouts = None
    if commit is not None:
        resolver = value.get("resolver") if isinstance(value, dict) else None
        try:
            memory = checkpoint_memory(commit, resolver)
            checkpoint_loadouts, checkpoint_loadouts_sha = loadouts_at_commit(
                commit, memory,
            )
        except ValueError as error:
            raise ValueError("disposition-retrieval-evidence") from error
    if commit is None:
        validated = (
            MEMORY.validate_live_index("work", lock_token=lock_token)
            if lock_token is not None else MEMORY.validate_live_index("work")
        )
    else:
        validated = {
            "snapshot_sha256": value.get("index_snapshot_sha256"),
            "loadouts_sha256": checkpoint_loadouts_sha,
        }
    if (
        not isinstance(value, dict)
        or set(value) != {
            "context_receipt_sha256", "resolver", "query_sha256", "zone",
            "loadout", "project", "include_l0", "include_stale", "explain",
            "effective_item_limit", "index_snapshot_sha256", "loadouts_sha256",
            "selected_chunks", "l3_pages",
        }
        or any(
            not SHA256_RE.fullmatch(str(value.get(field, "")))
            for field in (
                "context_receipt_sha256", "query_sha256",
                "index_snapshot_sha256", "loadouts_sha256",
            )
        )
        or value.get("zone") != "work"
        or value.get("include_l0") is not False
        or value.get("include_stale") is not False
        or type(value.get("explain")) is not bool
        or type(value.get("effective_item_limit")) is not int
        or not 1 <= value["effective_item_limit"] <= 20
        or not isinstance(value.get("loadout"), str)
        or not value["loadout"]
        or (
            value.get("project") is not None
            and not isinstance(value.get("project"), str)
        )
        or value.get("index_snapshot_sha256") != validated["snapshot_sha256"]
        or value.get("loadouts_sha256") != validated["loadouts_sha256"]
    ):
        raise ValueError("disposition-retrieval-drift")
    selected = value.get("selected_chunks")
    if (
        not isinstance(selected, list)
        or len(selected) > value["effective_item_limit"]
        or not isinstance(value.get("l3_pages"), list)
    ):
        raise ValueError("disposition-retrieval-evidence")
    if commit is None:
        docs = (
            MEMORY.load_index("work")
            if lock_token is None else
            MEMORY.load_index("work", require_checkpoint=False)
        )["docs"]
        if lock_token is not None:
            MEMORY.require_zone_lock("work", lock_token)
        by_identity = {
            (doc["page_path"], doc["chunk_index"]): doc for doc in docs.values()
        }
    else:
        by_identity = commit_documents(
            commit,
            [item.get("page_path") for item in selected if isinstance(item, dict)],
            memory,
        )
    seen = set()
    receipt_chunks = []
    for item in selected:
        identity = (
            item.get("page_path"), item.get("chunk_index")
        ) if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or set(item) != {
                "page_path", "page_sha256", "chunk_index", "chunk_sha256",
                "snippet_sha256", "snippet_start", "snippet_end",
                "snippet_prefix", "snippet_suffix",
            }
            or type(item.get("chunk_index")) is not int
            or item["chunk_index"] < 0
            or type(item.get("snippet_start")) is not int
            or type(item.get("snippet_end")) is not int
            or type(item.get("snippet_prefix")) is not bool
            or type(item.get("snippet_suffix")) is not bool
            or any(
                not SHA256_RE.fullmatch(str(item.get(field, "")))
                for field in ("page_sha256", "chunk_sha256", "snippet_sha256")
            )
            or identity in seen
        ):
            raise ValueError("disposition-retrieval-evidence")
        doc = by_identity.get(identity)
        if (
            doc is None or doc["page_sha256"] != item["page_sha256"]
            or doc["chunk_sha256"] != item["chunk_sha256"]
        ):
            raise ValueError("disposition-retrieval-evidence")
        source = doc["text"].strip()
        start, end = item["snippet_start"], item["snippet_end"]
        if not 0 <= start <= end <= len(source):
            raise ValueError("disposition-retrieval-evidence")
        snippet = source[start:end]
        if item["snippet_prefix"]:
            snippet = "… " + snippet
        if item["snippet_suffix"]:
            snippet += " …"
        if (
            item["snippet_prefix"] != (start > 0)
            or item["snippet_suffix"] != (end < len(source))
            or hashlib.sha256(snippet.encode("utf-8")).hexdigest()
            != item["snippet_sha256"]
        ):
            raise ValueError("disposition-retrieval-evidence")
        receipt_chunks.append({
            "page_path": item["page_path"],
            "page_sha256": item["page_sha256"],
            "chunk_index": item["chunk_index"],
            "chunk_sha256": item["chunk_sha256"],
            "snippet": snippet, "snippet_chars": len(snippet),
            "snippet_sha256": item["snippet_sha256"],
        })
        seen.add(identity)
    if commit is not None:
        role = checkpoint_loadouts["roles"].get(value["loadout"])
        if not isinstance(role, dict):
            raise ValueError("disposition-retrieval-evidence")
        project = value["project"]
        if role["project_required"] and not project:
            raise ValueError("disposition-retrieval-evidence")
        if value["effective_item_limit"] > role["max_items"]:
            raise ValueError("disposition-retrieval-evidence")
        prefix = memory.project_prefix("work", project)
        if prefix is not None:
            try:
                frontmatter, _ = memory.parse_text(
                    git_blob(commit, prefix + "_index.md").decode("utf-8")
                )
            except (UnicodeDecodeError, ValueError) as error:
                raise ValueError("disposition-retrieval-evidence") from error
            if frontmatter.get("type", "").strip().lower() != "project":
                raise ValueError("disposition-retrieval-evidence")
        used_levels = {"l0": 0, "l1": 0, "l2": 0}
        used_chars = 0
        seen_pages = set()
        for item in receipt_chunks:
            doc = by_identity[(item["page_path"], item["chunk_index"])]
            level = doc["memory_level"]
            if (
                level not in role["levels"]
                or doc["chunk_stale"]
                or item["page_path"] in seen_pages
                or (prefix is not None and not memory.project_scoped(
                    doc, prefix, project,
                ))
                or item["snippet_chars"] > role["snippet_chars"]
            ):
                raise ValueError("disposition-retrieval-evidence")
            seen_pages.add(item["page_path"])
            used_levels[level] += 1
            used_chars += item["snippet_chars"]
        if (
            used_chars > role["max_chars"]
            or any(
                used_levels[level] > role["per_level"][level]
                for level in used_levels
            )
            or len(value["l3_pages"]) > role["l3_max_items"]
        ):
            raise ValueError("disposition-retrieval-evidence")
    if not {item["page_path"] for item in outcomes}.issubset(
        {item["page_path"] for item in selected}
    ):
        raise ValueError("disposition-retrieval-evidence")
    receipt = {
        "schema_version": memory.CONTEXT_RECEIPT_SCHEMA_VERSION,
        "resolver": value["resolver"],
        "query_sha256": value["query_sha256"], "zone": value["zone"],
        "project": value["project"], "loadout": value["loadout"],
        "include_l0": value["include_l0"],
        "include_stale": value["include_stale"], "explain": value["explain"],
        "index_snapshot_sha256": value["index_snapshot_sha256"],
        "loadouts_sha256": value["loadouts_sha256"],
        "effective_item_limit": value["effective_item_limit"],
        "selected_chunks": receipt_chunks, "l3_pages": value["l3_pages"],
        "receipt_sha256": value["context_receipt_sha256"],
    }
    try:
        memory.validate_context_receipt(receipt)
    except ValueError as error:
        raise ValueError("disposition-retrieval-evidence") from error
    if commit is not None:
        approvals = POLICY.strict_json_loads(
            git_blob(commit, ".vault-meta/memory-approvals.json"),
            "checkpoint L3 approvals",
        )
        approved = approvals.get("l3") if isinstance(approvals, dict) else None
        if not isinstance(approved, dict):
            raise ValueError("disposition-retrieval-evidence")
        for item in value["l3_pages"]:
            path = item.get("page_path") if isinstance(item, dict) else None
            relative, _ = work_page(path, writable=False, require_live=False)
            raw = git_blob(commit, relative)
            if (
                hashlib.sha256(raw).hexdigest() != item.get("page_sha256")
                or approved.get(relative, {}).get("sha256") != item.get("page_sha256")
            ):
                raise ValueError("disposition-retrieval-evidence")
    return value


def validate_live_disposition(value, scan, policy, policy_sha, platform):
    checkpoint = value.get("checkpoint")
    checkpoint_plan = {
        key: checkpoint.get(key) if isinstance(checkpoint, dict) else None
        for key in ("parent_sha", "commit_sha", "changed_paths")
    }
    verifier_field = "verifier_%s_sha256" % platform
    plan = {
        "schema_version": 2, "verdict": "PASS", "platform": platform,
        "policy_sha256": policy_sha,
        "manifest_sha256": value.get("manifest_sha256"),
        "scan_receipt_sha256": value.get("scan_receipt_sha256"),
        "verifier": {
            "name": "evolution-verifier", "verdict": "PASS",
            "skill_sha256": policy["code"][verifier_field],
        },
        "checkpoint": checkpoint_plan,
        "signals": value.get("signals"),
    }
    signals, plan_sha = validate_signal_plan(
        plan, scan, policy, policy_sha, platform,
        value.get("manifest_sha256"), value.get("scan_receipt_sha256"),
    )
    if plan_sha != value.get("plan_sha256") or signals != value.get("signals"):
        raise ValueError("disposition-plan-drift")
    outcomes = page_outcomes(
        signals, value["manifest_sha256"], value["scan_receipt_sha256"],
        commit=checkpoint_plan["commit_sha"],
    )
    if outcomes != value.get("outcome_pages"):
        raise ValueError("disposition-page-drift")
    observed_checkpoint = checkpoint_evidence(
        checkpoint_plan, outcomes, require_head=False,
    )
    if observed_checkpoint != checkpoint:
        raise ValueError("disposition-checkpoint-drift")
    if value.get("verifier") != {
        "name": "evolution-verifier", "verdict": "PASS",
        "skill_sha256": policy["code"][verifier_field],
        "decision_sha256": plan_sha,
        "manifest_sha256": value["manifest_sha256"],
        "scan_receipt_sha256": value["scan_receipt_sha256"],
        "reviewed_checkpoint_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes(checkpoint_plan)
        ).hexdigest(),
    }:
        raise ValueError("disposition-verifier")
    validate_acceptance(
        value.get("retrieval_acceptance"), outcomes,
        commit=checkpoint_plan["commit_sha"],
    )
    if value.get("completed_at") != observed_checkpoint["committed_at"]:
        raise ValueError("disposition-retrieval-drift")
    parse_timestamp(value.get("completed_at"), "completed_at")
    return value


def self_test():
    def rejected(call, label):
        try:
            call()
        except (FileNotFoundError, ValueError):
            return
        raise AssertionError(label)

    for schema in (2.0, True):
        rejected(
            lambda schema=schema: validate_signal_plan(
                {"schema_version": schema}, {}, {}, "a" * 64, "codex",
                "b" * 64, "c" * 64,
            ),
            "non-integer disposition schema was accepted",
        )
    rejected(
        lambda: work_page("wiki/definitely-missing-reflection-page.md"),
        "absent disposition page was accepted",
    )
    rejected(
        lambda: checkpoint_evidence({
            "parent_sha": "0" * 40, "commit_sha": "0" * 40,
            "changed_paths": [],
        }, []),
        "nonexistent checkpoint was accepted",
    )

    originals = {
        "validate_live_index": MEMORY.validate_live_index,
        "load_index": MEMORY.load_index,
        "require_zone_lock": MEMORY.require_zone_lock,
        "read_page": MEMORY.read_page,
        "classify": MEMORY.classify,
        "work_page": globals()["work_page"],
    }
    index_sha, loadouts_sha = "3" * 64, "4" * 64
    document = {
        "page_path": "wiki/evidence-test.md", "page_sha256": "5" * 64,
        "chunk_index": 0, "chunk_sha256": "6" * 64,
        "text": "verified context",
    }
    MEMORY.validate_live_index = lambda zone, lock_token=None: {
        "snapshot_sha256": index_sha, "loadouts_sha256": loadouts_sha,
    }
    checkpoint_reads = []
    MEMORY.load_index = lambda zone, require_checkpoint=True: (
        checkpoint_reads.append(require_checkpoint)
        or {"docs": {"test": document}}
    )
    MEMORY.require_zone_lock = lambda zone, token: None
    try:
        snippet = "verified context"
        receipt_payload = {
            "schema_version": MEMORY.CONTEXT_RECEIPT_SCHEMA_VERSION,
            "resolver": MEMORY.resolver_identity(), "query_sha256": "2" * 64,
            "zone": "work", "project": None, "loadout": "general",
            "include_l0": False, "include_stale": False, "explain": False,
            "index_snapshot_sha256": index_sha,
            "loadouts_sha256": loadouts_sha, "effective_item_limit": 1,
            "selected_chunks": [{
                "page_path": document["page_path"],
                "page_sha256": document["page_sha256"],
                "chunk_index": 0, "chunk_sha256": document["chunk_sha256"],
                "snippet": snippet, "snippet_chars": len(snippet),
                "snippet_sha256": hashlib.sha256(
                    snippet.encode("utf-8")
                ).hexdigest(),
            }],
            "l3_pages": [],
        }
        receipt_sha = MEMORY.snapshot_digest(receipt_payload)
        acceptance = {
            "context_receipt_sha256": receipt_sha,
            "resolver": receipt_payload["resolver"],
            "query_sha256": receipt_payload["query_sha256"], "zone": "work",
            "loadout": "general", "project": None,
            "include_l0": False, "include_stale": False, "explain": False,
            "effective_item_limit": 1, "index_snapshot_sha256": index_sha,
            "loadouts_sha256": loadouts_sha,
            "selected_chunks": [{
                "page_path": document["page_path"],
                "page_sha256": document["page_sha256"],
                "chunk_index": 0, "chunk_sha256": document["chunk_sha256"],
                **snippet_evidence(snippet, document["text"]),
            }],
            "l3_pages": [],
        }
        assert validate_acceptance(acceptance, []) == acceptance
        assert validate_acceptance(
            acceptance, [], lock_token="exact-test-token",
        ) == acceptance
        assert checkpoint_reads[-1] is False
        rejected(
            lambda: validate_acceptance({
                **acceptance, "context_receipt_sha256": "7" * 64,
            }, []),
            "forged context receipt hash was accepted",
        )
        forged = json.loads(json.dumps(acceptance))
        forged["selected_chunks"][0]["snippet_end"] -= 1
        rejected(
            lambda: validate_acceptance(forged, []),
            "forged retrieval snippet was accepted",
        )

        head = git("rev-parse", "HEAD").decode().strip()
        checkpoint_plan = {
            "parent_sha": head, "commit_sha": head, "changed_paths": [],
        }
        checkpoint = checkpoint_evidence(checkpoint_plan, [])
        signal_id = "a" * 64
        scan = {
            "signal_count": 1,
            "signal_set_sha256": hashlib.sha256(
                POLICY.canonical_json_bytes([signal_id])
            ).hexdigest(),
        }
        policy_sha, manifest_sha, scan_sha = "b" * 64, "c" * 64, "d" * 64
        policy = {"code": {"verifier_codex_sha256": "e" * 64}}
        signals = [{"signal_id": signal_id, "action": "rejected"}]
        plan = {
            "schema_version": 2, "verdict": "PASS", "platform": "codex",
            "policy_sha256": policy_sha, "manifest_sha256": manifest_sha,
            "scan_receipt_sha256": scan_sha,
            "verifier": {
                "name": "evolution-verifier", "verdict": "PASS",
                "skill_sha256": "e" * 64,
            },
            "checkpoint": checkpoint_plan, "signals": signals,
        }
        plan_sha = hashlib.sha256(POLICY.canonical_json_bytes(plan)).hexdigest()
        head_memory = checkpoint_memory(head)
        historical_payload = {
            "schema_version": head_memory.CONTEXT_RECEIPT_SCHEMA_VERSION,
            "resolver": head_memory.resolver_identity(), "query_sha256": "f" * 64,
            "zone": "work", "project": None, "loadout": "general",
            "include_l0": False, "include_stale": False, "explain": False,
            "index_snapshot_sha256": "1" * 64,
            "loadouts_sha256": loadouts_identity_at_commit(head),
            "effective_item_limit": 8, "selected_chunks": [], "l3_pages": [],
        }
        def replay_acceptance(payload, selected=None):
            return {
                "context_receipt_sha256": head_memory.snapshot_digest(payload),
                **{
                    key: item for key, item in payload.items()
                    if key not in {"schema_version", "selected_chunks"}
                },
                "selected_chunks": (
                    payload["selected_chunks"] if selected is None else selected
                ),
            }

        historical_acceptance = replay_acceptance(historical_payload)
        disposition = {
            "manifest_sha256": manifest_sha,
            "scan_receipt_sha256": scan_sha, "plan_sha256": plan_sha,
            "signals": signals, "outcome_pages": [],
            "checkpoint": checkpoint,
            "verifier": {
                **plan["verifier"], "decision_sha256": plan_sha,
                "manifest_sha256": manifest_sha,
                "scan_receipt_sha256": scan_sha,
                "reviewed_checkpoint_sha256": hashlib.sha256(
                    POLICY.canonical_json_bytes(checkpoint_plan)
                ).hexdigest(),
            },
            "retrieval_acceptance": historical_acceptance,
            "completed_at": checkpoint["committed_at"],
        }
        assert validate_live_disposition(
            disposition, scan, policy, policy_sha, "codex",
        ) == disposition
        invalid_payloads = (
            {**historical_payload, "loadout": "reviewer", "project": None},
            {**historical_payload, "loadout": "invented"},
            {**historical_payload, "effective_item_limit": 9},
        )
        for invalid_payload in invalid_payloads:
            forged_disposition = {
                **disposition,
                "retrieval_acceptance": replay_acceptance(invalid_payload),
            }
            rejected(
                lambda forged=forged_disposition: validate_live_disposition(
                    forged, scan, policy, policy_sha, "codex",
                ),
                "impossible historical loadout contract was accepted",
            )

        current_resolver_sha = MEMORY.resolver_identity()["sha256"]
        historical_commit = None
        for candidate in git("rev-list", "--max-count=50", "HEAD").decode().splitlines():
            try:
                candidate_sha = hashlib.sha256(git_blob(
                    candidate, "scripts/memory-model.py",
                    allowed_modes=(b"100644", b"100755"),
                )).hexdigest()
            except ValueError:
                continue
            if candidate_sha != current_resolver_sha:
                historical_commit = candidate
                break
        if historical_commit is None:
            # A fresh template has no earlier resolver revision to replay yet.
            globals()["work_page"] = lambda value, **kwargs: (
                value, Path("/synthetic")
            )
            MEMORY.read_page = lambda path: ({
                "memory_level": "l3", "memory_provenance": "test",
                "reflection_manifest_sha256": "8" * 64,
                "reflection_scan_receipt_sha256": "9" * 64,
                "reflection_signal_ids": json.dumps(["a" * 64]),
            }, "", "b" * 64)
            MEMORY.classify = lambda path, frontmatter: {
                "level": "l3", "issues": [], "trust": "explicit",
            }
            rejected(
                lambda: page_outcomes([{
                    "signal_id": "a" * 64, "action": "materialized-l1",
                    "page_path": "wiki/evidence-test.md",
                }], "8" * 64, "9" * 64),
                "L3 page was accepted as an autonomous L1 outcome",
            )
            print("reflection-evidence self-test: PASS (historical replay unavailable)")
            return
        historical_memory = checkpoint_memory(historical_commit)
        historical_payload = {
            "schema_version": historical_memory.CONTEXT_RECEIPT_SCHEMA_VERSION,
            "resolver": historical_memory.resolver_identity(),
            "query_sha256": "7" * 64, "zone": "work", "project": None,
            "loadout": "general", "include_l0": False,
            "include_stale": False, "explain": False,
            "index_snapshot_sha256": "8" * 64,
            "loadouts_sha256": loadouts_identity_at_commit(historical_commit),
            "effective_item_limit": 1, "selected_chunks": [], "l3_pages": [],
        }
        historical_replay = {
            "context_receipt_sha256": historical_memory.snapshot_digest(
                historical_payload
            ),
            **{
                key: value for key, value in historical_payload.items()
                if key != "schema_version"
            },
        }
        assert validate_acceptance(
            historical_replay, [], commit=historical_commit,
        ) == historical_replay
        project_payload = {
            **historical_payload,
            "project": "demo-project", "loadout": "reviewer",
        }
        project_replay = {
            "context_receipt_sha256": historical_memory.snapshot_digest(
                project_payload
            ),
            **{
                key: value for key, value in project_payload.items()
                if key != "schema_version"
            },
        }
        assert validate_acceptance(
            project_replay, [], commit=historical_commit,
        ) == project_replay
        for invalid_payload in (
            {**historical_payload, "loadout": "reviewer", "project": None},
            {**historical_payload, "loadout": "invented"},
            {
                **historical_payload,
                "project": "missing-project", "loadout": "reviewer",
            },
            {**historical_payload, "effective_item_limit": 9},
        ):
            invalid_replay = {
                "context_receipt_sha256": historical_memory.snapshot_digest(
                    invalid_payload
                ),
                **{
                    key: item for key, item in invalid_payload.items()
                    if key != "schema_version"
                },
            }
            rejected(
                lambda replay=invalid_replay: validate_acceptance(
                    replay, [], commit=historical_commit,
                ),
                "invalid historical role or project boundary was accepted",
            )

        cross_path = "wiki/workspace/projects/design-system/_index.md"
        cross_docs = commit_documents(
            historical_commit, [cross_path], historical_memory,
        )
        cross_doc = cross_docs[sorted(cross_docs)[0]]
        cross_source = cross_doc["text"].strip()
        cross_end = min(100, len(cross_source))
        cross_snippet = cross_source[:cross_end]
        if cross_end < len(cross_source):
            cross_snippet += " …"
        cross_selected = [{
            "page_path": cross_doc["page_path"],
            "page_sha256": cross_doc["page_sha256"],
            "chunk_index": cross_doc["chunk_index"],
            "chunk_sha256": cross_doc["chunk_sha256"],
            "snippet": cross_snippet,
            "snippet_chars": len(cross_snippet),
            "snippet_sha256": hashlib.sha256(
                cross_snippet.encode("utf-8")
            ).hexdigest(),
        }]
        cross_payload = {
            **project_payload, "selected_chunks": cross_selected,
        }
        cross_replay = {
            "context_receipt_sha256": historical_memory.snapshot_digest(
                cross_payload
            ),
            **{
                key: item for key, item in cross_payload.items()
                if key not in {"schema_version", "selected_chunks"}
            },
            "selected_chunks": [{
                "page_path": cross_doc["page_path"],
                "page_sha256": cross_doc["page_sha256"],
                "chunk_index": cross_doc["chunk_index"],
                "chunk_sha256": cross_doc["chunk_sha256"],
                **snippet_evidence(cross_snippet, cross_doc["text"]),
            }],
        }
        rejected(
            lambda: validate_acceptance(
                cross_replay, [], commit=historical_commit,
            ),
            "cross-project historical context was accepted",
        )
        rejected(
            lambda: validate_acceptance({
                **historical_replay,
                "resolver": {
                    **historical_replay["resolver"], "sha256": "9" * 64,
                },
            }, [], commit=historical_commit),
            "forged historical resolver was accepted",
        )

        globals()["work_page"] = lambda value, **kwargs: (
            value, Path("/synthetic")
        )
        MEMORY.read_page = lambda path: ({
            "memory_level": "l3", "memory_provenance": "test",
            "reflection_manifest_sha256": "8" * 64,
            "reflection_scan_receipt_sha256": "9" * 64,
            "reflection_signal_ids": json.dumps(["a" * 64]),
        }, "", "b" * 64)
        MEMORY.classify = lambda path, frontmatter: {
            "level": "l3", "issues": [], "trust": "explicit",
        }
        rejected(
            lambda: page_outcomes([{
                "signal_id": "a" * 64, "action": "materialized-l1",
                "page_path": "wiki/evidence-test.md",
            }], "8" * 64, "9" * 64),
            "L3 page was accepted as an autonomous L1 outcome",
        )
    finally:
        MEMORY.validate_live_index = originals["validate_live_index"]
        MEMORY.load_index = originals["load_index"]
        MEMORY.require_zone_lock = originals["require_zone_lock"]
        MEMORY.read_page = originals["read_page"]
        MEMORY.classify = originals["classify"]
        globals()["work_page"] = originals["work_page"]
    print("reflection-evidence self-test: PASS")


if __name__ == "__main__":
    self_test()
