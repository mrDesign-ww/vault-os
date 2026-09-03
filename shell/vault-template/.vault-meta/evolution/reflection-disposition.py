#!/usr/bin/env python3
"""Publish and reconstruct a content-free reflection disposition receipt."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
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


POLICY = load_module("reflection_policy", HERE / "reflection_policy.py")
SELECTOR = load_module("trace_manifest", HERE / "trace-manifest.py")
SCANNER = load_module("trace_scan", HERE / "trace-scan.py")
MEMORY = load_module("vault_memory_model", VAULT / "scripts" / "memory-model.py")
EVIDENCE = load_module("reflection_evidence", HERE / "reflection-evidence.py")


def git(*args):
    result = subprocess.run(
        ["git", *args], cwd=VAULT, capture_output=True, text=False,
    )
    if result.returncode:
        raise ValueError("checkpoint-invalid")
    return result.stdout


def read_private_json(path, label):
    raw, _ = POLICY.read_regular_bytes(Path(path), label)
    return POLICY.strict_json_loads(raw, label)


def work_page(path):
    relative = Path(str(path))
    if (
        relative.is_absolute() or relative.suffix != ".md" or ".." in relative.parts
        or not relative.parts or relative.parts[0] != "wiki"
        or relative.parts[:2] in (("wiki", "personal"), ("wiki", "client"))
        or "sources" in relative.parts
        or relative.as_posix()
        == "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md"
    ):
        raise ValueError("disposition-page-zone")
    candidate = Path(os.path.abspath(str(VAULT / relative)))
    if candidate.resolve(strict=True) != candidate:
        raise ValueError("disposition-page-path")
    return relative.as_posix(), candidate


def validate_signal_plan(
    plan, scan_receipt, policy, policy_sha, platform, manifest_sha, scan_sha,
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
        if not isinstance(item, dict):
            raise ValueError("disposition-signal")
        action = item.get("action")
        expected = {"signal_id", "action"} if action == "rejected" else {
            "signal_id", "action", "page_path",
        }
        signal_id = item.get("signal_id")
        if (
            set(item) != expected or action not in ACTIONS
            or not SHA256_RE.fullmatch(str(signal_id or "")) or signal_id in seen
        ):
            raise ValueError("disposition-signal")
        seen.add(signal_id)
        normalized.append(dict(item))
    normalized.sort(key=lambda item: item["signal_id"])
    if normalized != plan["signals"]:
        raise ValueError("disposition-signal-order")
    ids = [item["signal_id"] for item in normalized]
    if (
        len(ids) != scan_receipt["signal_count"]
        or hashlib.sha256(POLICY.canonical_json_bytes(ids)).hexdigest()
        != scan_receipt["signal_set_sha256"]
    ):
        raise ValueError("disposition-signal-set")
    return normalized, hashlib.sha256(POLICY.canonical_json_bytes(plan)).hexdigest()


def page_outcomes(signals, manifest_sha, scan_sha):
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
        relative, path = work_page(signal["page_path"])
        page_signals.setdefault(relative, []).append(signal["signal_id"])
        frontmatter, _, page_sha = MEMORY.read_page(path)
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
        is_provisional = (
            frontmatter.get("memory_approval_mode", "").strip().lower() == "provisional"
            and frontmatter.get("promotion_eligible", "").strip().lower() == "false"
        )
        if is_provisional != provisional:
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
        frontmatter, _, _ = MEMORY.read_page(VAULT / path)
        if json.loads(frontmatter["reflection_signal_ids"]) != sorted(signal_ids):
            raise ValueError("disposition-page-provenance")
    return [outcomes[key] for key in sorted(outcomes)]


def checkpoint_path_allowed(path, outcome_paths):
    return path in outcome_paths


def assert_checkpoint_preserves_l3(parent, changed_paths):
    approvals_raw = git("show", parent + ":.vault-meta/memory-approvals.json")
    if hashlib.sha256(approvals_raw).hexdigest() != MEMORY.APPROVALS_MANIFEST_SHA256:
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
            parent_raw = git("show", parent + ":" + path)
            parent_text = parent_raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("checkpoint-parent-page") from error
        frontmatter, _ = MEMORY.parse_text(parent_text)
        if frontmatter.get("memory_level", "").strip().lower() == "l3":
            raise ValueError("checkpoint-l3-mutation")


def checkpoint_evidence(expected, outcomes, require_head=True):
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
        if (
            parent != commit or expected_paths != []
        ):
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
            if status not in ("A", "M") or not checkpoint_path_allowed(path, outcome_paths):
                raise ValueError("checkpoint-path-scope")
            changed.append((status, path))
            committed = git("show", commit + ":" + path)
            paths.append({
                "path": path, "sha256": hashlib.sha256(committed).hexdigest(),
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
        "committed_at": SELECTOR.iso_utc(
            SELECTOR.parse_timestamp(committed_at, "checkpoint time")
        ),
        "changed_paths": paths,
        "changed_paths_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes(paths)
        ).hexdigest(),
    }


def acceptance_evidence(result, outcomes, lock_token=None):
    return EVIDENCE.acceptance_evidence(
        result, outcomes, lock_token=lock_token,
    )


def published_artifacts(policy, policy_sha, platform, manifest_sha, scan_sha):
    completion_root, scan_root, manifest_root = SCANNER.catalog_roots(
        policy, policy_sha, platform,
    )
    _, observed_manifest_sha, manifest = SELECTOR.load_manifest_artifact(
        manifest_root / (manifest_sha + ".json"), manifest_root, policy,
        policy_sha, platform, completion_root,
    )
    if observed_manifest_sha != manifest_sha:
        raise ValueError("disposition-manifest")
    _, scan = SCANNER.load_scan_receipt(
        scan_root, scan_sha, manifest_sha, manifest, policy, policy_sha, platform,
    )
    return manifest, scan


def build_receipt(
    policy, policy_sha, platform, manifest_sha, scan_sha, plan,
    acceptance, checkpoint=None, lock_token=None,
):
    manifest, scan = published_artifacts(
        policy, policy_sha, platform, manifest_sha, scan_sha,
    )
    signals, plan_sha = validate_signal_plan(
        plan, scan, policy, policy_sha, platform, manifest_sha, scan_sha,
    )
    outcomes = page_outcomes(signals, manifest_sha, scan_sha)
    if checkpoint is not None and checkpoint != plan["checkpoint"]["commit_sha"]:
        raise ValueError("checkpoint-plan-mismatch")
    checkpoint = checkpoint_evidence(plan["checkpoint"], outcomes)
    acceptance = acceptance_evidence(
        acceptance, outcomes, lock_token=lock_token,
    )
    validated_index = (
        MEMORY.validate_live_index("work", lock_token=lock_token)
        if lock_token is not None else MEMORY.validate_live_index("work")
    )
    if (
        acceptance["index_snapshot_sha256"] != validated_index["snapshot_sha256"]
        or acceptance["loadouts_sha256"] != validated_index["loadouts_sha256"]
    ):
        raise ValueError("acceptance-index-drift")
    verifier_field = "verifier_%s_sha256" % platform
    return {
        "schema_version": 2,
        "receipt_type": "reflection-disposition",
        "status": "completed",
        "platform": platform,
        "zone": "work",
        "approval_scope": "standing-autonomous-l0-l2",
        "delegated_by": POLICY.OWNER,
        "delegation_id": policy["delegation_id"],
        "policy_sha256": policy_sha,
        "manifest_sha256": manifest_sha,
        "scan_receipt_sha256": scan_sha,
        "signal_count": scan["signal_count"],
        "signal_set_sha256": scan["signal_set_sha256"],
        "plan_sha256": plan_sha,
        "signals": signals,
        "outcome_pages": outcomes,
        "verifier": {
            "name": "evolution-verifier",
            "verdict": "PASS",
            "skill_sha256": policy["code"][verifier_field],
            "decision_sha256": plan_sha,
            "manifest_sha256": manifest_sha,
            "scan_receipt_sha256": scan_sha,
            "reviewed_checkpoint_sha256": hashlib.sha256(
                POLICY.canonical_json_bytes(plan["checkpoint"])
            ).hexdigest(),
        },
        "checkpoint": checkpoint,
        "retrieval_acceptance": acceptance,
        "completed_at": checkpoint["committed_at"],
    }


def validate_receipt(value, digest, policy, policy_sha, platform):
    expected = {
        "schema_version", "receipt_type", "status", "platform", "zone",
        "approval_scope", "delegated_by", "delegation_id", "policy_sha256",
        "manifest_sha256", "scan_receipt_sha256", "signal_count",
        "signal_set_sha256", "plan_sha256", "signals", "outcome_pages", "verifier",
        "checkpoint", "retrieval_acceptance", "completed_at",
    }
    if (
        not isinstance(value, dict) or set(value) != expected
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
        or value.get("receipt_type") != "reflection-disposition"
        or value.get("status") != "completed"
        or value.get("platform") != platform or value.get("zone") != "work"
        or value.get("approval_scope") != "standing-autonomous-l0-l2"
        or value.get("delegated_by") != POLICY.OWNER
        or value.get("delegation_id") != policy["delegation_id"]
        or value.get("policy_sha256") != policy_sha
        or not SHA256_RE.fullmatch(str(value.get("manifest_sha256", "")))
        or not SHA256_RE.fullmatch(str(value.get("scan_receipt_sha256", "")))
        or not SHA256_RE.fullmatch(str(value.get("signal_set_sha256", "")))
        or not SHA256_RE.fullmatch(str(value.get("plan_sha256", "")))
        or digest != hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest()
    ):
        raise ValueError("disposition-authority")
    manifest, scan = published_artifacts(
        policy, policy_sha, platform, value["manifest_sha256"],
        value["scan_receipt_sha256"],
    )
    SELECTOR.validate_disposition_projection(
        value, digest, scan, policy, policy_sha, platform,
    )
    return EVIDENCE.validate_live_disposition(
        value, scan, policy, policy_sha, platform,
    )


def _load_published_body(digest, platform, policy=None, policy_sha=None):
    if not SHA256_RE.fullmatch(str(digest or "")):
        raise ValueError("disposition-path")
    if policy is None:
        policy, policy_sha = POLICY.load_policy()
    root = POLICY.generation_root(
        VAULT / policy["roots"]["disposition_work"], policy_sha, platform,
        create=False,
    )
    raw, _ = POLICY.read_regular_bytes(
        root / (digest + ".json"), "reflection disposition", owner_private=True,
    )
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("disposition-content-address")
    value = POLICY.strict_json_loads(raw, "reflection disposition")
    return validate_receipt(value, digest, policy, policy_sha, platform)


def load_published(digest, platform, policy=None, policy_sha=None):
    if policy is None:
        policy, policy_sha = POLICY.load_policy()
    value = _load_published_body(
        digest, platform, policy=policy, policy_sha=policy_sha,
    )
    root = POLICY.generation_root(
        VAULT / policy["roots"]["disposition_work"], policy_sha, platform,
        create=False,
    )
    SELECTOR.require_finalization(
        root, value, digest, policy, policy_sha, platform,
    )
    return value


def publish_receipt(
    root, receipt, policy=None, policy_sha=None, platform=None, lock_token=None,
):
    raw = POLICY.canonical_json_bytes(receipt)
    digest = hashlib.sha256(raw).hexdigest()
    if policy is None:
        policy, policy_sha = POLICY.load_policy()
    platform = platform or receipt.get("platform")
    validate_receipt(receipt, digest, policy, policy_sha, platform)
    descriptor = SCANNER.lock_catalog(root)
    try:
        if lock_token is not None:
            SELECTOR.check_lock(lock_token)
        checkpoint = receipt.get("checkpoint")
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get("commit_sha")
            != git("rev-parse", "HEAD").decode().strip()
        ):
            raise ValueError("checkpoint-not-head")
        published = None
        for path in sorted(Path(root).iterdir()):
            if path.name.startswith("."):
                continue
            existing_raw, _ = POLICY.read_regular_bytes(
                path, "reflection disposition", owner_private=True,
            )
            if hashlib.sha256(existing_raw).hexdigest() != path.stem:
                raise ValueError("disposition-content-address")
            existing = POLICY.strict_json_loads(
                existing_raw, "reflection disposition",
            )
            if existing.get("scan_receipt_sha256") != receipt["scan_receipt_sha256"]:
                continue
            if existing_raw != raw:
                raise ValueError("disposition-conflict")
            published = (path, path.stem, False)
            break
        if published is None:
            published = POLICY.publish_immutable(root, raw)
        path, digest, created = published
        if lock_token is not None:
            SELECTOR.check_lock(lock_token)
        if checkpoint["commit_sha"] != git("rev-parse", "HEAD").decode().strip():
            raise ValueError("checkpoint-not-head")
        finalization = SELECTOR.build_finalization_marker(receipt, digest)
        if lock_token is not None:
            SELECTOR.check_lock(lock_token)
        _, finalization_sha, _ = POLICY.publish_immutable(
            Path(root) / ".finalized",
            POLICY.canonical_json_bytes(finalization),
        )
        if lock_token is not None:
            SELECTOR.check_lock(lock_token)
        manifest, scan = published_artifacts(
            policy, policy_sha, platform, receipt["manifest_sha256"],
            receipt["scan_receipt_sha256"],
        )
        marker = SELECTOR.PROCESSED.build_marker(
            policy, policy_sha, platform, manifest, scan,
            receipt["scan_receipt_sha256"], finalization, finalization_sha,
        )
        if lock_token is not None:
            SELECTOR.check_lock(lock_token)
        SELECTOR.PROCESSED.publish_marker(
            policy, policy_sha, marker,
        )
        if lock_token is not None:
            SELECTOR.check_lock(lock_token)
        return path, digest, created
    finally:
        SCANNER.unlock_catalog(descriptor)


def self_test():
    ids = ["a" * 64, "b" * 64]
    scan = {
        "manifest_sha256": "d" * 64,
        "policy_sha256": "f" * 64, "platform": "codex",
        "completion_receipt_sha256s": ["9" * 64],
        "signal_count": 2,
        "signal_set_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes(ids)
        ).hexdigest(),
    }
    policy = {
        "delegation_id": "test",
        "code": {
            "verifier_codex_sha256": "c" * 64,
            "processed_sha256": "8" * 64,
        },
    }
    head = git("rev-parse", "HEAD").decode().strip()
    plan = {
        "schema_version": 2, "verdict": "PASS", "platform": "codex",
        "policy_sha256": "f" * 64,
        "manifest_sha256": "d" * 64, "scan_receipt_sha256": "e" * 64,
        "verifier": {
            "name": "evolution-verifier", "verdict": "PASS",
            "skill_sha256": "c" * 64,
        },
        "checkpoint": {
            "parent_sha": head, "commit_sha": head, "changed_paths": [],
        },
        "signals": [
            {"signal_id": "a" * 64, "action": "rejected"},
            {"signal_id": "b" * 64, "action": "rejected"},
        ],
    }
    signals, plan_sha = validate_signal_plan(
        plan, scan, policy, "f" * 64, "codex", "d" * 64, "e" * 64,
    )
    assert [item["signal_id"] for item in signals] == ids
    assert SHA256_RE.fullmatch(plan_sha)
    assert checkpoint_evidence(plan["checkpoint"], [])["commit_sha"] == head
    assert checkpoint_path_allowed("wiki/example.md", {"wiki/example.md"})
    assert not checkpoint_path_allowed("wiki/log.md", {"wiki/example.md"})
    assert not checkpoint_path_allowed("wiki/workspace/projects/demo/_index.md", {"wiki/example.md"})
    original_git = globals()["git"]
    original_approvals_sha = MEMORY.APPROVALS_MANIFEST_SHA256
    approved_path = "wiki/resources/concepts/approved.md"
    approvals_raw = POLICY.canonical_json_bytes({
        "schema_version": 1,
        "l3": {approved_path: {
            "sha256": "1" * 64, "approved_by": "{{OWNER_NAME}}",
            "approved_on": "2026-08-21",
        }},
    })
    parent_pages = {
        approved_path: b"---\nmemory_level: l2\n---\n",
        "wiki/explicit-l3.md": b"---\nmemory_level: l3\n---\n",
        "wiki/existing-l2.md": b"---\nmemory_level: l2\n---\n",
    }

    def fake_git(*args):
        if args == ("show", "p:.vault-meta/memory-approvals.json"):
            return approvals_raw
        if len(args) == 2 and args[0] == "show" and args[1].startswith("p:"):
            return parent_pages[args[1][2:]]
        raise AssertionError(args)

    globals()["git"] = fake_git
    MEMORY.APPROVALS_MANIFEST_SHA256 = hashlib.sha256(approvals_raw).hexdigest()
    try:
        for path in (approved_path, "wiki/explicit-l3.md"):
            try:
                assert_checkpoint_preserves_l3("p", [("M", path)])
            except ValueError as error:
                assert str(error) == "checkpoint-l3-mutation"
            else:
                raise AssertionError("L3 checkpoint mutation was accepted")
        assert_checkpoint_preserves_l3("p", [("M", "wiki/existing-l2.md")])
        assert_checkpoint_preserves_l3("p", [("A", "wiki/new-l2.md")])
    finally:
        globals()["git"] = original_git
        MEMORY.APPROVALS_MANIFEST_SHA256 = original_approvals_sha
    try:
        validate_signal_plan(
            {**plan, "verdict": "REVISE"}, scan, policy, "f" * 64,
            "codex", "d" * 64, "e" * 64,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("non-PASS disposition was accepted")
    for schema in (2.0, True):
        try:
            validate_signal_plan(
                {**plan, "schema_version": schema}, scan, policy, "f" * 64,
                "codex", "d" * 64, "e" * 64,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("non-integer disposition plan schema was accepted")
    originals = {
        "published_artifacts": globals()["published_artifacts"],
        "page_outcomes": globals()["page_outcomes"],
        "checkpoint_evidence": globals()["checkpoint_evidence"],
        "acceptance_evidence": globals()["acceptance_evidence"],
        "validate_live_index": MEMORY.validate_live_index,
        "evidence_validate": EVIDENCE.validate_live_disposition,
        "selector_evidence_validate": SELECTOR.EVIDENCE.validate_live_disposition,
    }
    acceptance = {
        "context_receipt_sha256": "1" * 64,
        "resolver": MEMORY.resolver_identity(),
        "query_sha256": "2" * 64, "zone": "work",
        "loadout": "general", "project": None,
        "include_l0": False, "include_stale": False, "explain": False,
        "effective_item_limit": 8,
        "index_snapshot_sha256": "3" * 64, "loadouts_sha256": "4" * 64,
        "selected_chunks": [], "l3_pages": [],
    }
    globals()["published_artifacts"] = lambda *args: ({
        "policy_sha256": "f" * 64,
        "completion_receipts": [{"receipt_sha256": "9" * 64}],
    }, scan)
    globals()["page_outcomes"] = lambda *args: []
    globals()["checkpoint_evidence"] = lambda *args: {
        **plan["checkpoint"], "committed_at": "2026-08-21T12:00:00Z",
        "changed_paths_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes([])
        ).hexdigest(),
    }
    globals()["acceptance_evidence"] = lambda *args, **kwargs: acceptance
    MEMORY.validate_live_index = lambda zone: {
        "snapshot_sha256": "3" * 64, "loadouts_sha256": "4" * 64,
    }
    EVIDENCE.validate_live_disposition = lambda value, *args: value
    SELECTOR.EVIDENCE.validate_live_disposition = lambda value, *args: value
    try:
        receipt = build_receipt(
            policy, "f" * 64, "codex", "d" * 64, "e" * 64,
            plan, {}, head,
        )
        assert receipt["schema_version"] == 2
        assert receipt["plan_sha256"] == plan_sha
        import tempfile
        with tempfile.TemporaryDirectory(prefix="disposition-test-") as directory:
            root = Path(os.path.realpath(directory))
            test_policy = {
                **policy,
                "roots": {"processed_work": str(root / "processed")},
            }
            for schema in (2.0, True):
                try:
                    build_receipt(
                        policy, "f" * 64, "codex", "d" * 64, "e" * 64,
                        {**plan, "schema_version": schema}, {}, head,
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError("non-integer external plan was accepted")
                assert not list(root.iterdir())
            path, digest, created = publish_receipt(
                root, receipt, test_policy, "f" * 64, "codex",
            )
            replay_path, replay_digest, replay_created = publish_receipt(
                root, receipt, test_policy, "f" * 64, "codex",
            )
            assert created and not replay_created
            assert path == replay_path and digest == replay_digest
            finalizations = list((root / ".finalized").glob("*.json"))
            assert len(finalizations) == 1
            SELECTOR.require_finalization(
                root, receipt, digest, policy, "f" * 64, "codex",
            )
    finally:
        globals()["published_artifacts"] = originals["published_artifacts"]
        globals()["page_outcomes"] = originals["page_outcomes"]
        globals()["checkpoint_evidence"] = originals["checkpoint_evidence"]
        globals()["acceptance_evidence"] = originals["acceptance_evidence"]
        MEMORY.validate_live_index = originals["validate_live_index"]
        EVIDENCE.validate_live_disposition = originals["evidence_validate"]
        SELECTOR.EVIDENCE.validate_live_disposition = originals[
            "selector_evidence_validate"
        ]
    print("reflection-disposition self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", choices=("publish", "verify"))
    parser.add_argument("--platform", choices=("claude", "codex"))
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--scan-receipt-sha256")
    parser.add_argument("--plan")
    parser.add_argument("--acceptance-result")
    parser.add_argument("--checkpoint")
    parser.add_argument("--disposition-sha256")
    parser.add_argument("--lock-token")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.command or not args.platform:
        parser.error("command and --platform are required")
    try:
        policy, policy_sha = POLICY.load_policy()
        if args.command == "verify":
            value = load_published(
                args.disposition_sha256, args.platform, policy, policy_sha,
            )
            print(json.dumps({
                "schema_version": 1, "status": "verified",
                "platform": args.platform,
                "disposition_sha256": args.disposition_sha256,
                "manifest_sha256": value["manifest_sha256"],
                "scan_receipt_sha256": value["scan_receipt_sha256"],
                "signal_count": value["signal_count"],
            }, sort_keys=True))
            return 0
        required = (
            args.manifest_sha256, args.scan_receipt_sha256, args.plan,
            args.acceptance_result, args.checkpoint, args.lock_token,
        )
        if any(value is None for value in required):
            parser.error("publish requires manifest, scan, plan, acceptance and checkpoint")
        plan = read_private_json(args.plan, "reflection disposition plan")
        acceptance = read_private_json(
            args.acceptance_result, "reflection acceptance result",
        )
        receipt = build_receipt(
            policy, policy_sha, args.platform, args.manifest_sha256,
            args.scan_receipt_sha256, plan, acceptance, args.checkpoint,
            lock_token=args.lock_token,
        )
        root = POLICY.generation_root(
            VAULT / policy["roots"]["disposition_work"], policy_sha, args.platform,
        )
        SELECTOR.check_lock(args.lock_token)
        path, digest, created = publish_receipt(
            root, receipt, policy, policy_sha, args.platform,
            lock_token=args.lock_token,
        )
        if load_published(
            digest, args.platform, policy, policy_sha,
        ) != receipt:
            raise ValueError("published disposition verification drifted")
        print(json.dumps({
            "schema_version": 1, "status": "completed",
            "platform": args.platform, "disposition": path.name,
            "disposition_sha256": digest, "created": created,
            "manifest_sha256": receipt["manifest_sha256"],
            "scan_receipt_sha256": receipt["scan_receipt_sha256"],
            "signal_count": receipt["signal_count"],
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
