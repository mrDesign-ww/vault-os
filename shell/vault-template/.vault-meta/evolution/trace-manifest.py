#!/usr/bin/env python3
"""Select trusted completed work turns into an immutable delegated manifest."""

import argparse
import collections
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
SPEC = importlib.util.spec_from_file_location(
    "reflection_policy", HERE / "reflection_policy.py",
)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
EVIDENCE_SPEC = importlib.util.spec_from_file_location(
    "reflection_evidence", HERE / "reflection-evidence.py",
)
EVIDENCE = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(EVIDENCE)
PROCESSED_SPEC = importlib.util.spec_from_file_location(
    "reflection_processed", HERE / "reflection-processed.py",
)
PROCESSED = importlib.util.module_from_spec(PROCESSED_SPEC)
PROCESSED_SPEC.loader.exec_module(PROCESSED)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
LOCK_PATH = ".vault-meta/write/work"
ID_RE = re.compile(
    r"(?:[0-9a-f]{64}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\Z",
    re.I,
)


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


def code_sha256(path):
    raw, _ = POLICY.read_regular_bytes(path, "reflection code")
    return hashlib.sha256(raw).hexdigest()


def check_lock(token):
    if not isinstance(token, str) or not SHA256_RE.fullmatch(token):
        raise ValueError("writer-lock")
    result = subprocess.run(
        ["bash", "scripts/wiki-lock.sh", "check", LOCK_PATH, token],
        cwd=VAULT, capture_output=True, text=True,
    )
    if result.returncode or result.stdout.strip() != "owned":
        raise ValueError("writer-lock")


def selection_parameters(policy):
    return {
        "ordering": policy["selection"]["ordering"],
        "max_age_days": policy["selection"]["max_age_days"],
        "max_files": policy["selection"]["max_files"],
        "max_file_bytes": policy["selection"]["max_file_bytes"],
        "max_total_bytes": policy["selection"]["max_total_bytes"],
    }


def completion_catalogs(root, policy, policy_sha, platform):
    """Resolve current and policy-pinned predecessor completion generations."""
    current = Path(root)
    authorities = POLICY.completion_authorities(policy, policy_sha, platform)
    base = None
    if current.name == platform and current.parent.name == policy_sha:
        base = current.parent.parent
    else:
        configured_value = policy.get("roots", {}).get("completion_work")
        if isinstance(configured_value, str) and configured_value:
            configured = Path(configured_value)
            if not configured.is_absolute():
                configured = VAULT / configured
            expected = configured / policy_sha / platform
            if os.path.abspath(str(current)) == os.path.abspath(str(expected)):
                base = configured
    result = []
    for authority in authorities:
        target = current
        if authority["policy_sha256"] != policy_sha:
            if base is None:
                continue
            target = base / authority["policy_sha256"] / platform
        result.append((authority, target))
    return result


def completion_catalog_identity(root, policy, policy_sha, platform):
    return [
        {
            "policy_sha256": authority["policy_sha256"],
            "catalog": catalog_identity(path, "completion"),
        }
        for authority, path in completion_catalogs(
            root, policy, policy_sha, platform,
        )
    ]


def pending_predecessor_scans(
    policy, policy_sha, platform, current_scan_root, processed,
):
    """Fail closed when a prior policy still owns an unfinished outcome cycle."""
    current_scan_root = Path(current_scan_root)
    if (
        current_scan_root.name == platform
        and current_scan_root.parent.name == policy_sha
    ):
        base = current_scan_root.parent.parent
    else:
        configured_value = policy.get("roots", {}).get("scan_receipt_work")
        if not isinstance(configured_value, str) or not configured_value:
            return []
        configured = Path(configured_value)
        base = configured if configured.is_absolute() else VAULT / configured
    expected = {
        "schema_version", "receipt_type", "status", "platform", "zone",
        "manifest_sha256", "policy_sha256", "delegation_id", "scanner_sha256",
        "selector_sha256", "parameters", "completion_receipt_sha256s",
        "segments", "skipped_segments", "signal_count", "signal_set_sha256",
        "verified_at",
    }
    pending = []
    hook_field = (
        "claude_hook_sha256" if platform == "claude"
        else "codex_session_hook_sha256"
    )
    for predecessor in policy.get("completion_predecessors", []):
        if predecessor.get(hook_field) is None:
            continue
        generation = predecessor["policy_sha256"]
        root = POLICY.generation_root(base, generation, platform, create=False)
        if not root.exists():
            continue
        root = POLICY.validate_private_directory(root)
        for path in sorted(root.iterdir()):
            if path.name.startswith("."):
                continue
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                raise ValueError("unexpected predecessor scan artifact")
            raw, _ = POLICY.read_regular_bytes(
                path, "predecessor scan receipt", owner_private=True,
            )
            if hashlib.sha256(raw).hexdigest() != path.stem:
                raise ValueError("predecessor scan receipt content address")
            value = POLICY.strict_json_loads(raw, "predecessor scan receipt")
            receipts = (
                value.get("completion_receipt_sha256s")
                if isinstance(value, dict) else None
            )
            if (
                not isinstance(value, dict) or set(value) != expected
                or type(value.get("schema_version")) is not int
                or value.get("schema_version") != 1
                or value.get("receipt_type") != "reflection-scan"
                or value.get("status") != "completed"
                or value.get("platform") != platform
                or value.get("zone") != "work"
                or value.get("policy_sha256") != generation
                or value.get("delegation_id") != predecessor["delegation_id"]
                or not SHA256_RE.fullmatch(str(value.get("manifest_sha256", "")))
                or type(value.get("signal_count")) is not int
                or value["signal_count"] < 0
                or not isinstance(receipts, list) or not receipts
                or len(receipts) != len(set(receipts))
                or any(not SHA256_RE.fullmatch(str(item)) for item in receipts)
            ):
                raise ValueError("predecessor scan receipt authority")
            coverage = [processed.get(receipt) for receipt in receipts]
            if all(coverage):
                if any(
                    item.get("scan_receipt_sha256") != path.stem
                    for item in coverage
                ):
                    raise ValueError("predecessor processed marker overlap")
                continue
            if any(coverage):
                raise ValueError("predecessor processed marker partial coverage")
            pending.append({
                "policy_sha256": generation,
                "manifest_sha256": value["manifest_sha256"],
                "scan_receipt_sha256": path.stem,
                "signal_count": value["signal_count"],
                "completion_receipt_sha256s": receipts,
            })
    return pending


def require_no_pending_predecessor_scans(
    policy, policy_sha, platform, scan_root,
):
    processed = PROCESSED.read_processed(policy, policy_sha, platform)
    pending = pending_predecessor_scans(
        policy, policy_sha, platform, scan_root, processed,
    )
    if pending:
        first = min(
            pending,
            key=lambda item: (
                item["policy_sha256"], item["scan_receipt_sha256"],
            ),
        )
        raise ValueError(
            "unfinished reflection cycle belongs to predecessor policy %s: %s; "
            "finalize it before policy rollover"
            % (first["policy_sha256"], first["scan_receipt_sha256"])
        )
    return processed


def manifest_record_fields(platform):
    fields = {
        "receipt_sha256", "platform", "session_id", "turn_id", "trace_relative",
        "trace_identity", "segment_start", "segment_end", "segment_size",
        "segment_sha256", "session_meta_sha256", "task_started_at", "completed_at",
    }
    if platform == "claude":
        fields.update(("prompt_sha256", "prompt_sequence"))
    return fields


def opaque_id(name):
    stem = Path(name).stem
    return stem if SHA256_RE.fullmatch(stem) else hashlib.sha256(name.encode("utf-8")).hexdigest()


def validate_completion_receipt(
    value, receipt_sha, policy, policy_sha, hook_sha=None, requested_platform=None,
    authority=None,
):
    expected = {
        "schema_version", "receipt_type", "platform", "session_id", "turn_id",
        "trace_relative", "trace_identity", "segment_start", "segment_end",
        "segment_size", "segment_sha256", "session_meta_sha256",
        "task_started_at", "completed_at", "terminal_reason", "vault_id",
        "vault_path", "initial_zone", "zone_transitions", "eligible",
        "eligibility_reason", "delegated_by", "recorded_by", "delegation_id",
        "policy_sha256", "session_hook_sha256", "registration_policy_sha256",
        "registration_hook_sha256",
    }
    platform = value.get("platform") if isinstance(value, dict) else None
    if platform == "claude":
        expected.update(("prompt_sha256", "prompt_sequence"))
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("receipt-schema")
    expected_receipt = {
        "claude": (
            "claude-stop-turn-segment",
            (("stop-hook", "claude:stop-hook"),
             ("next-prompt-hook", "claude:prompt-hook")),
            "claude_hook_sha256",
        ),
        "codex": (
            "codex-completed-turn-segment",
            (("task_complete", "codex:trace-session-hook"),),
            "codex_session_hook_sha256",
        ),
    }.get(platform)
    if expected_receipt is None:
        raise ValueError("receipt-platform")
    expected_type, terminal_recorders, hook_field = expected_receipt
    authority = authority or {
        "policy_sha256": policy_sha,
        "delegation_id": policy["delegation_id"],
        "not_before": policy["not_before"],
        "session_hook_sha256": policy["code"][hook_field],
    }
    if authority.get("policy_sha256") != policy_sha:
        raise ValueError("receipt-authority")
    authoritative_hook_sha = authority["session_hook_sha256"]
    if hook_sha is not None and hook_sha != authoritative_hook_sha:
        raise ValueError("receipt-authority")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
        or value.get("receipt_type") != expected_type
        or (requested_platform is not None and platform != requested_platform)
        or [platform, "work"] not in policy["allowed_platform_zones"]
        or (value.get("terminal_reason"), value.get("recorded_by")) not in terminal_recorders
        or value.get("vault_id") != policy["vault"]["id"]
        or value.get("vault_path") != policy["vault"]["path"]
        or value.get("initial_zone") != "work"
        or value.get("delegated_by") != POLICY.OWNER
        or value.get("delegation_id") != authority["delegation_id"]
        or value.get("policy_sha256") != policy_sha
        or value.get("session_hook_sha256") != authoritative_hook_sha
        or not SHA256_RE.fullmatch(str(value.get("registration_policy_sha256", "")))
        or not SHA256_RE.fullmatch(str(value.get("registration_hook_sha256", "")))
    ):
        raise ValueError("receipt-authority")
    if not ID_RE.fullmatch(str(value.get("session_id", ""))) or not ID_RE.fullmatch(str(value.get("turn_id", ""))):
        raise ValueError("receipt-identity")
    relative = Path(str(value.get("trace_relative", "")))
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".jsonl":
        raise ValueError("receipt-path")
    identity = value.get("trace_identity")
    if (
        not isinstance(identity, dict) or set(identity) != {"device", "inode"}
        or any(isinstance(identity[key], bool) or not isinstance(identity[key], int) or identity[key] < 0 for key in identity)
    ):
        raise ValueError("receipt-trace-identity")
    start, end, size = value.get("segment_start"), value.get("segment_end"), value.get("segment_size")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (start, end, size)):
        raise ValueError("receipt-range")
    if start < 0 or end <= start or size != end - start:
        raise ValueError("receipt-range")
    for field in ("segment_sha256", "session_meta_sha256"):
        if not isinstance(value.get(field), str) or not SHA256_RE.fullmatch(value[field]):
            raise ValueError("receipt-hash")
    if platform == "claude" and (
        not SHA256_RE.fullmatch(str(value.get("prompt_sha256", "")))
        or isinstance(value.get("prompt_sequence"), bool)
        or not isinstance(value.get("prompt_sequence"), int)
        or value["prompt_sequence"] < 1
    ):
        raise ValueError("receipt-prompt-identity")
    started = parse_timestamp(value.get("task_started_at"), "task_started_at")
    completed = parse_timestamp(value.get("completed_at"), "completed_at")
    if completed < started:
        raise ValueError("receipt-time-order")
    transitions = value.get("zone_transitions")
    if not isinstance(transitions, list):
        raise ValueError("receipt-zone-ledger")
    for transition in transitions:
        expected_transition = {"at", "from", "to", "recorded_by", "scope"}
        if not isinstance(transition, dict) or set(transition) != expected_transition:
            raise ValueError("receipt-zone-ledger")
        parse_timestamp(transition["at"], "zone transition")
        if transition["recorded_by"] not in (
            "codex:trace-session-hook", "claude:zone-switch", "claude:prompt-hook",
        ):
            raise ValueError("receipt-zone-ledger")
        if transition.get("scope") not in ("session", "turn"):
            raise ValueError("receipt-zone-ledger")
    if value.get("eligible") is not True or value.get("eligibility_reason") != "eligible" or transitions:
        raise ValueError("receipt-ineligible")
    if hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest() != receipt_sha:
        raise ValueError("receipt-content-address")
    return value, started, completed


def read_completion_receipts(root, policy, policy_sha, cutoff, platform):
    max_age = datetime.timedelta(days=policy["selection"]["max_age_days"])
    eligible = []
    skipped = []
    for authority, catalog in completion_catalogs(
        root, policy, policy_sha, platform,
    ):
        catalog = Path(catalog)
        if not catalog.exists():
            continue
        catalog = POLICY.validate_private_directory(catalog)
        not_before = parse_timestamp(authority["not_before"], "not_before")
        for path in sorted(catalog.iterdir()):
            artifact = opaque_id(path.name)
            if path.name.startswith("."):
                continue
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                skipped.append({"receipt_id": artifact, "reason_code": "artifact-name"})
                continue
            try:
                raw, _ = POLICY.read_regular_bytes(path, "completion receipt", owner_private=True)
                receipt_sha = hashlib.sha256(raw).hexdigest()
                if receipt_sha != path.stem:
                    raise ValueError("receipt-content-address")
                value = POLICY.strict_json_loads(raw, "completion receipt")
                if isinstance(value, dict) and value.get("platform") in ("claude", "codex") and value["platform"] != platform:
                    continue
                value, started, completed = validate_completion_receipt(
                    value, receipt_sha, policy, authority["policy_sha256"],
                    requested_platform=platform, authority=authority,
                )
                if started < not_before:
                    raise ValueError("before-policy")
                if completed > cutoff + datetime.timedelta(minutes=5):
                    raise ValueError("future-completion")
                if cutoff - completed > max_age:
                    raise ValueError("age-limit")
                if value["segment_size"] > policy["selection"]["max_file_bytes"]:
                    raise ValueError("file-size-limit")
                record = {
                    "receipt_sha256": receipt_sha,
                    "platform": value["platform"],
                    "session_id": value["session_id"],
                    "turn_id": value["turn_id"],
                    "trace_relative": value["trace_relative"],
                    "trace_identity": value["trace_identity"],
                    "segment_start": value["segment_start"],
                    "segment_end": value["segment_end"],
                    "segment_size": value["segment_size"],
                    "segment_sha256": value["segment_sha256"],
                    "session_meta_sha256": value["session_meta_sha256"],
                    "task_started_at": value["task_started_at"],
                    "completed_at": value["completed_at"],
                }
                if platform == "claude":
                    record.update({
                        "prompt_sha256": value["prompt_sha256"],
                        "prompt_sequence": value["prompt_sequence"],
                    })
                eligible.append(record)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                reason = str(error)
                if reason not in {
                    "artifact-name", "receipt-schema", "receipt-authority",
                    "receipt-identity", "receipt-path", "receipt-trace-identity",
                    "receipt-platform",
                    "receipt-range", "receipt-hash", "receipt-time-order",
                    "receipt-prompt-identity",
                    "receipt-zone-ledger", "receipt-ineligible",
                    "receipt-content-address", "before-policy", "future-completion",
                    "age-limit", "file-size-limit",
                }:
                    reason = "receipt-unreadable"
                skipped.append({"receipt_id": artifact, "reason_code": reason})
    return eligible, skipped


def validate_scan_receipt(
    value, receipt_sha, manifest_sha, manifest, policy, policy_sha, platform,
    now=None,
):
    expected = {
        "schema_version", "receipt_type", "status", "platform", "zone",
        "manifest_sha256", "policy_sha256", "delegation_id", "scanner_sha256",
        "selector_sha256", "parameters", "completion_receipt_sha256s",
        "segments", "skipped_segments", "signal_count", "signal_set_sha256",
        "verified_at",
    }
    expected_receipts = [
        record["receipt_sha256"] for record in manifest["completion_receipts"]
    ]
    if (
        not isinstance(value, dict) or set(value) != expected
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("receipt_type") != "reflection-scan"
        or value.get("status") != "completed"
        or value.get("platform") != platform
        or value.get("zone") != "work"
        or value.get("manifest_sha256") != manifest_sha
        or value.get("policy_sha256") != policy_sha
        or value.get("delegation_id") != policy["delegation_id"]
        or value.get("scanner_sha256") != policy["code"]["scanner_sha256"]
        or value.get("selector_sha256") != policy["code"]["selector_sha256"]
        or value.get("parameters") != selection_parameters(policy)
        or value.get("completion_receipt_sha256s") != expected_receipts
        or value.get("skipped_segments") != []
        or not SHA256_RE.fullmatch(str(value.get("signal_set_sha256", "")))
        or isinstance(value.get("signal_count"), bool)
        or not isinstance(value.get("signal_count"), int)
        or value["signal_count"] < 0
    ):
        raise ValueError("scan-receipt-authority")
    segments = value.get("segments")
    if not isinstance(segments, list) or len(segments) != len(expected_receipts):
        raise ValueError("scan-receipt-segments")
    signal_count = 0
    for segment, record in zip(segments, manifest["completion_receipts"]):
        if (
            not isinstance(segment, dict)
            or set(segment) != {"receipt_sha256", "segment_sha256", "signal_count"}
            or segment.get("receipt_sha256") != record["receipt_sha256"]
            or segment.get("segment_sha256") != record["segment_sha256"]
            or isinstance(segment.get("signal_count"), bool)
            or not isinstance(segment.get("signal_count"), int)
            or segment["signal_count"] < 0
        ):
            raise ValueError("scan-receipt-segments")
        signal_count += segment["signal_count"]
    if signal_count != value["signal_count"]:
        raise ValueError("scan-receipt-signal-count")
    verified = parse_timestamp(value.get("verified_at"), "verified_at")
    if verified > (now or datetime.datetime.now(datetime.timezone.utc)) + datetime.timedelta(minutes=5):
        raise ValueError("scan-receipt-time")
    if receipt_sha != hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest():
        raise ValueError("scan-receipt-content-address")
    return value


def disposition_catalog(policy, policy_sha, platform):
    root = POLICY.generation_root(
        VAULT / policy["roots"]["disposition_work"],
        policy_sha, platform, create=False,
    )
    return root, root / ".finalized"


def disposition_page_path(value, writable=False):
    if not isinstance(value, str):
        raise ValueError("disposition-projection-path")
    relative = Path(value)
    if (
        relative.is_absolute() or relative.suffix != ".md"
        or ".." in relative.parts or not relative.parts
        or relative.parts[0] != "wiki"
        or relative.parts[:2] in (("wiki", "personal"), ("wiki", "client"))
        or relative.as_posix() != value
        or (
            writable and (
                "sources" in relative.parts
                or value == "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md"
            )
        )
    ):
        raise ValueError("disposition-projection-path")
    return value


def validate_disposition_projection(
    value, digest, scan, policy, policy_sha, platform,
):
    expected = {
        "schema_version", "receipt_type", "status", "platform", "zone",
        "approval_scope", "delegated_by", "delegation_id", "policy_sha256",
        "manifest_sha256", "scan_receipt_sha256", "signal_count",
        "signal_set_sha256", "plan_sha256", "signals", "outcome_pages",
        "verifier", "checkpoint", "retrieval_acceptance", "completed_at",
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
        or value.get("manifest_sha256") != scan["manifest_sha256"]
        or value.get("signal_count") != scan["signal_count"]
        or value.get("signal_set_sha256") != scan["signal_set_sha256"]
        or isinstance(value.get("signal_count"), bool)
        or not isinstance(value.get("signal_count"), int)
        or value["signal_count"] < 1
        or not SHA256_RE.fullmatch(str(value.get("scan_receipt_sha256", "")))
        or not SHA256_RE.fullmatch(str(value.get("plan_sha256", "")))
        or digest != hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest()
    ):
        raise ValueError("disposition-projection-authority")
    signals = value.get("signals")
    if not isinstance(signals, list):
        raise ValueError("disposition-projection-signals")
    normalized = []
    materialized_paths = set()
    expected_outcomes = {}
    for signal in signals:
        action = signal.get("action") if isinstance(signal, dict) else None
        fields = {"signal_id", "action"} if action == "rejected" else {
            "signal_id", "action", "page_path",
        }
        if (
            not isinstance(signal, dict) or set(signal) != fields
            or action not in {
                "rejected", "materialized-l1", "materialized-l2", "provisional-l2",
            }
            or not SHA256_RE.fullmatch(str(signal.get("signal_id", "")))
        ):
            raise ValueError("disposition-projection-signals")
        if action != "rejected":
            try:
                path = disposition_page_path(signal.get("page_path"), writable=True)
            except ValueError:
                raise ValueError("disposition-projection-signals")
            if path in materialized_paths:
                raise ValueError("disposition-projection-signals")
            materialized_paths.add(path)
            expected_outcomes[path] = {
                "materialized-l1": ("l1", False),
                "materialized-l2": ("l2", False),
                "provisional-l2": ("l2", True),
            }[action]
        normalized.append(signal)
    if (
        normalized != sorted(normalized, key=lambda item: item["signal_id"])
        or len({item["signal_id"] for item in normalized}) != len(normalized)
        or len(normalized) != value["signal_count"]
        or hashlib.sha256(POLICY.canonical_json_bytes(
            [item["signal_id"] for item in normalized]
        )).hexdigest() != value["signal_set_sha256"]
    ):
        raise ValueError("disposition-projection-signals")
    outcomes = value.get("outcome_pages")
    if not isinstance(outcomes, list):
        raise ValueError("disposition-projection-outcomes")
    outcome_paths = set()
    for outcome in outcomes:
        if (
            not isinstance(outcome, dict)
            or set(outcome) != {
                "page_path", "page_sha256", "memory_level", "memory_trust",
                "provisional",
            }
            or not isinstance(outcome.get("page_path"), str)
            or outcome.get("page_path") in outcome_paths
            or not SHA256_RE.fullmatch(str(outcome.get("page_sha256", "")))
            or outcome.get("memory_level") not in ("l1", "l2")
            or not isinstance(outcome.get("memory_trust"), str)
            or not isinstance(outcome.get("provisional"), bool)
        ):
            raise ValueError("disposition-projection-outcomes")
        try:
            path = disposition_page_path(outcome["page_path"], writable=True)
        except ValueError:
            raise ValueError("disposition-projection-outcomes")
        if (
            expected_outcomes.get(path)
            != (outcome["memory_level"], outcome["provisional"])
            or outcome["memory_trust"] != (
                "provisional-synthesis" if outcome["provisional"] else "explicit"
            )
        ):
            raise ValueError("disposition-projection-outcomes")
        outcome_paths.add(path)
    if (
        outcomes != sorted(outcomes, key=lambda item: item["page_path"])
        or outcome_paths != materialized_paths
    ):
        raise ValueError("disposition-projection-outcomes")
    checkpoint = value.get("checkpoint")
    if not isinstance(checkpoint, dict) or set(checkpoint) != {
        "parent_sha", "commit_sha", "committed_at", "changed_paths",
        "changed_paths_sha256",
    }:
        raise ValueError("disposition-projection-checkpoint")
    if any(
        not re.fullmatch(r"[0-9a-f]{40,64}", str(checkpoint.get(field, "")))
        for field in ("parent_sha", "commit_sha")
    ):
        raise ValueError("disposition-projection-checkpoint")
    changed = checkpoint.get("changed_paths")
    if not isinstance(changed, list):
        raise ValueError("disposition-projection-checkpoint")
    for item in changed:
        if (
            not isinstance(item, dict) or set(item) != {"path", "sha256"}
            or not isinstance(item.get("path"), str)
            or not SHA256_RE.fullmatch(str(item.get("sha256", "")))
        ):
            raise ValueError("disposition-projection-checkpoint")
        try:
            disposition_page_path(item["path"], writable=True)
        except ValueError:
            raise ValueError("disposition-projection-checkpoint")
    if (
        changed != sorted(changed, key=lambda item: item["path"])
        or len({item["path"] for item in changed}) != len(changed)
        or {item["path"] for item in changed} != outcome_paths
        or {item["path"]: item["sha256"] for item in changed}
        != {item["page_path"]: item["page_sha256"] for item in outcomes}
        or checkpoint.get("changed_paths_sha256") != hashlib.sha256(
            POLICY.canonical_json_bytes(changed)
        ).hexdigest()
        or value.get("completed_at") != checkpoint.get("committed_at")
    ):
        raise ValueError("disposition-projection-checkpoint")
    parse_timestamp(value["completed_at"], "completed_at")
    checkpoint_plan = {
        key: checkpoint[key] for key in ("parent_sha", "commit_sha", "changed_paths")
    }
    verifier_field = "verifier_%s_sha256" % platform
    plan = {
        "schema_version": 2, "verdict": "PASS", "platform": platform,
        "policy_sha256": policy_sha,
        "manifest_sha256": value["manifest_sha256"],
        "scan_receipt_sha256": value["scan_receipt_sha256"],
        "verifier": {
            "name": "evolution-verifier", "verdict": "PASS",
            "skill_sha256": policy["code"][verifier_field],
        },
        "checkpoint": checkpoint_plan, "signals": signals,
    }
    plan_sha = hashlib.sha256(POLICY.canonical_json_bytes(plan)).hexdigest()
    if value.get("plan_sha256") != plan_sha or value.get("verifier") != {
        "name": "evolution-verifier", "verdict": "PASS",
        "skill_sha256": policy["code"][verifier_field],
        "decision_sha256": plan_sha,
        "manifest_sha256": value["manifest_sha256"],
        "scan_receipt_sha256": value["scan_receipt_sha256"],
        "reviewed_checkpoint_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes(checkpoint_plan)
        ).hexdigest(),
    }:
        raise ValueError("disposition-projection-verifier")
    acceptance = value.get("retrieval_acceptance")
    if not isinstance(acceptance, dict) or set(acceptance) != {
        "context_receipt_sha256", "resolver", "query_sha256", "zone",
        "loadout", "project", "include_l0", "include_stale", "explain",
        "effective_item_limit", "index_snapshot_sha256", "loadouts_sha256",
        "selected_chunks", "l3_pages",
    }:
        raise ValueError("disposition-projection-acceptance")
    if any(
        not SHA256_RE.fullmatch(str(acceptance.get(field, "")))
        for field in (
            "context_receipt_sha256", "query_sha256",
            "index_snapshot_sha256", "loadouts_sha256",
        )
    ) or (
        acceptance.get("zone") != "work"
        or acceptance.get("include_l0") is not False
        or acceptance.get("include_stale") is not False
        or type(acceptance.get("explain")) is not bool
        or type(acceptance.get("effective_item_limit")) is not int
        or not 1 <= acceptance["effective_item_limit"] <= 20
        or not isinstance(acceptance.get("loadout"), str)
        or not acceptance["loadout"]
        or (
            acceptance.get("project") is not None
            and (
                not isinstance(acceptance.get("project"), str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", acceptance["project"])
            )
        )
        or not isinstance(acceptance.get("selected_chunks"), list)
        or len(acceptance["selected_chunks"]) > acceptance["effective_item_limit"]
        or not isinstance(acceptance.get("resolver"), dict)
        or not isinstance(acceptance.get("l3_pages"), list)
    ):
        raise ValueError("disposition-projection-acceptance")
    selected_paths = set()
    selected_identities = set()
    for item in acceptance["selected_chunks"]:
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
            or isinstance(item.get("chunk_index"), bool)
            or not isinstance(item.get("chunk_index"), int)
            or item["chunk_index"] < 0
            or type(item.get("snippet_start")) is not int
            or type(item.get("snippet_end")) is not int
            or item["snippet_start"] < 0
            or item["snippet_end"] < item["snippet_start"]
            or type(item.get("snippet_prefix")) is not bool
            or type(item.get("snippet_suffix")) is not bool
            or not isinstance(item.get("page_path"), str)
            or any(
                not SHA256_RE.fullmatch(str(item.get(field, "")))
                for field in ("page_sha256", "chunk_sha256", "snippet_sha256")
            )
            or identity in selected_identities
        ):
            raise ValueError("disposition-projection-acceptance")
        try:
            disposition_page_path(item["page_path"])
        except ValueError:
            raise ValueError("disposition-projection-acceptance")
        selected_identities.add(identity)
        selected_paths.add(item["page_path"])
    if not outcome_paths.issubset(selected_paths):
        raise ValueError("disposition-projection-acceptance")
    return value


def build_finalization_marker(receipt, disposition_sha):
    checkpoint = receipt["checkpoint"]
    return {
        "schema_version": 1,
        "receipt_type": "reflection-disposition-finalization",
        "status": "completed", "platform": receipt["platform"], "zone": "work",
        "policy_sha256": receipt["policy_sha256"],
        "delegation_id": receipt["delegation_id"],
        "manifest_sha256": receipt["manifest_sha256"],
        "scan_receipt_sha256": receipt["scan_receipt_sha256"],
        "disposition_sha256": disposition_sha,
        "signal_count": receipt["signal_count"],
        "signal_set_sha256": receipt["signal_set_sha256"],
        "checkpoint_commit_sha": checkpoint["commit_sha"],
        "finalized_at": receipt["completed_at"],
    }


def validate_finalization_marker(
    value, digest, disposition_root, scan, policy, policy_sha, platform,
):
    expected = {
        "schema_version", "receipt_type", "status", "platform", "zone",
        "policy_sha256", "delegation_id", "manifest_sha256",
        "scan_receipt_sha256", "disposition_sha256", "signal_count",
        "signal_set_sha256", "checkpoint_commit_sha", "finalized_at",
    }
    if (
        not isinstance(value, dict) or set(value) != expected
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("receipt_type") != "reflection-disposition-finalization"
        or value.get("status") != "completed"
        or value.get("platform") != platform or value.get("zone") != "work"
        or value.get("policy_sha256") != policy_sha
        or value.get("delegation_id") != policy["delegation_id"]
        or value.get("manifest_sha256") != scan["manifest_sha256"]
        or value.get("signal_count") != scan["signal_count"]
        or value.get("signal_set_sha256") != scan["signal_set_sha256"]
        or scan["signal_count"] < 1
        or not SHA256_RE.fullmatch(str(value.get("scan_receipt_sha256", "")))
        or not SHA256_RE.fullmatch(str(value.get("disposition_sha256", "")))
        or not re.fullmatch(r"[0-9a-f]{40,64}", str(value.get("checkpoint_commit_sha", "")))
        or digest != hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest()
    ):
        raise ValueError("disposition-finalization-authority")
    disposition_root = POLICY.validate_private_directory(disposition_root)
    disposition_raw, _ = POLICY.read_regular_bytes(
        disposition_root / (value["disposition_sha256"] + ".json"),
        "reflection disposition", owner_private=True,
    )
    if hashlib.sha256(disposition_raw).hexdigest() != value["disposition_sha256"]:
        raise ValueError("disposition-finalization-content-address")
    disposition = POLICY.strict_json_loads(
        disposition_raw, "reflection disposition",
    )
    if disposition_raw != POLICY.canonical_json_bytes(disposition):
        raise ValueError("disposition-finalization-content-address")
    validate_disposition_projection(
        disposition, value["disposition_sha256"], scan,
        policy, policy_sha, platform,
    )
    EVIDENCE.validate_live_disposition(
        disposition, scan, policy, policy_sha, platform,
    )
    if value != build_finalization_marker(
        disposition, value["disposition_sha256"],
    ):
        raise ValueError("disposition-finalization-drift")
    parse_timestamp(value["finalized_at"], "finalized_at")
    return value


def require_finalization(
    disposition_root, disposition, disposition_sha,
    policy, policy_sha, platform,
):
    finalization_root = POLICY.validate_private_directory(
        Path(disposition_root) / ".finalized",
    )
    scan = {
        "manifest_sha256": disposition["manifest_sha256"],
        "signal_count": disposition["signal_count"],
        "signal_set_sha256": disposition["signal_set_sha256"],
    }
    matches = []
    for path in sorted(finalization_root.iterdir()):
        if path.name.startswith("."):
            continue
        if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            raise ValueError("unexpected disposition finalization artifact")
        raw, _ = POLICY.read_regular_bytes(
            path, "disposition finalization", owner_private=True,
        )
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise ValueError("disposition-finalization-content-address")
        marker = POLICY.strict_json_loads(raw, "disposition finalization")
        if not isinstance(marker, dict):
            raise ValueError("disposition-finalization-authority")
        if marker.get("disposition_sha256") != disposition_sha:
            continue
        validate_finalization_marker(
            marker, path.stem, disposition_root, scan,
            policy, policy_sha, platform,
        )
        matches.append(marker)
    if len(matches) != 1:
        raise ValueError("disposition-finalization-missing")
    return matches[0]


def read_finalized_scans(
    scan_root, manifest_root, completion_root, policy, policy_sha, platform,
):
    disposition_root, finalization_root = disposition_catalog(
        policy, policy_sha, platform,
    )
    if not finalization_root.exists():
        return {}
    finalization_root = POLICY.validate_private_directory(finalization_root)
    finalized = {}
    for path in sorted(finalization_root.iterdir()):
        if path.name.startswith("."):
            continue
        if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            raise ValueError("unexpected disposition finalization artifact")
        raw, _ = POLICY.read_regular_bytes(
            path, "disposition finalization", owner_private=True,
        )
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise ValueError("disposition-finalization-content-address")
        value = POLICY.strict_json_loads(raw, "disposition finalization")
        scan_sha = value.get("scan_receipt_sha256") if isinstance(value, dict) else None
        if not SHA256_RE.fullmatch(str(scan_sha or "")):
            raise ValueError("disposition-finalization-authority")
        scan_raw, _ = POLICY.read_regular_bytes(
            Path(scan_root) / (scan_sha + ".json"),
            "scan receipt", owner_private=True,
        )
        if hashlib.sha256(scan_raw).hexdigest() != scan_sha:
            raise ValueError("scan receipt content address is invalid")
        scan = POLICY.strict_json_loads(scan_raw, "scan receipt")
        manifest_sha = scan.get("manifest_sha256") if isinstance(scan, dict) else None
        _, observed_manifest_sha, manifest = load_manifest_artifact(
            Path(manifest_root) / (str(manifest_sha) + ".json"),
            manifest_root, policy, policy_sha, platform, completion_root,
        )
        validate_scan_receipt(
            scan, scan_sha, observed_manifest_sha, manifest,
            policy, policy_sha, platform,
        )
        validate_finalization_marker(
            value, path.stem, disposition_root, scan,
            policy, policy_sha, platform,
        )
        if scan_sha in finalized:
            raise ValueError("duplicate disposition finalization")
        finalized[scan_sha] = value
    return finalized


def read_scan_manifests(
    root, manifest_root, completion_root, policy, policy_sha, platform,
):
    root = Path(root)
    if not root.exists():
        return {}
    root = POLICY.validate_private_directory(root)
    scans = {}
    finalized = read_finalized_scans(
        root, manifest_root, completion_root, policy, policy_sha, platform,
    )
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            raise ValueError("unexpected scan receipt artifact")
        raw, _ = POLICY.read_regular_bytes(path, "scan receipt", owner_private=True)
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise ValueError("scan receipt content address is invalid")
        value = POLICY.strict_json_loads(raw, "scan receipt")
        if isinstance(value, dict) and value.get("platform") in ("claude", "codex") and value["platform"] != platform:
            continue
        if not isinstance(value, dict) or not SHA256_RE.fullmatch(
            str(value.get("manifest_sha256", ""))
        ):
            raise ValueError("scan-receipt-authority")
        _, manifest_sha, manifest = load_manifest_artifact(
            Path(manifest_root) / (value["manifest_sha256"] + ".json"),
            manifest_root, policy, policy_sha, platform, completion_root,
        )
        validate_scan_receipt(
            value, path.stem, manifest_sha, manifest,
            policy, policy_sha, platform,
        )
        if manifest_sha in scans:
            raise ValueError("duplicate scan manifest")
        scans[manifest_sha] = {
            "scan": value,
            "scan_sha256": path.stem,
            "manifest": manifest,
            "finalized": path.stem in finalized,
        }
    return scans


def read_processed_manifests(
    root, manifest_root, completion_root, policy, policy_sha, platform,
):
    return {
        manifest_sha: state["scan"]
        for manifest_sha, state in read_scan_manifests(
            root, manifest_root, completion_root, policy, policy_sha, platform,
        ).items()
        if state["scan"]["signal_count"] == 0 or state["finalized"]
    }


def processed_marker_plan(
    scan_root, manifest_root, completion_root, policy, policy_sha, platform,
):
    processed = read_processed_manifests(
        scan_root, manifest_root, completion_root,
        policy, policy_sha, platform,
    )
    observed = PROCESSED.read_processed(policy, policy_sha, platform)
    finalized = read_finalized_scans(
        scan_root, manifest_root, completion_root,
        policy, policy_sha, platform,
    )
    expected_receipts = {}
    plans = []
    missing = []
    for manifest_sha, scan in sorted(processed.items()):
        _, _, manifest = load_manifest_artifact(
            Path(manifest_root) / (manifest_sha + ".json"), manifest_root,
            policy, policy_sha, platform, completion_root,
        )
        scan_sha = hashlib.sha256(
            POLICY.canonical_json_bytes(scan)
        ).hexdigest()
        finalization = finalized.get(scan_sha)
        finalization_sha = (
            hashlib.sha256(POLICY.canonical_json_bytes(finalization)).hexdigest()
            if finalization is not None else None
        )
        marker = PROCESSED.build_marker(
            policy, policy_sha, platform, manifest, scan, scan_sha,
            finalization=finalization, finalization_sha=finalization_sha,
        )
        marker_sha = hashlib.sha256(
            POLICY.canonical_json_bytes(marker)
        ).hexdigest()
        evidence = {
            "marker_sha256": marker_sha,
            "manifest_sha256": manifest_sha,
            "scan_receipt_sha256": scan_sha,
            "finalization_sha256": finalization_sha,
        }
        receipts = marker["completion_receipt_sha256s"]
        for receipt_sha in receipts:
            if receipt_sha in expected_receipts:
                raise ValueError("processed evidence overlap")
            expected_receipts[receipt_sha] = evidence
        present = [observed.get(receipt_sha) for receipt_sha in receipts]
        if all(item is None for item in present):
            missing.append((manifest_sha, marker, marker_sha))
        elif any(item != evidence for item in present):
            raise ValueError("processed marker evidence drift")
        plans.append((manifest_sha, marker_sha))
    return plans, missing


def validate_processed_coverage(
    scan_root, manifest_root, completion_root, policy, policy_sha, platform,
):
    plans, missing = processed_marker_plan(
        scan_root, manifest_root, completion_root,
        policy, policy_sha, platform,
    )
    if missing:
        raise ValueError("processed marker is missing")
    return dict(plans)


def repair_processed_markers(
    scan_root, manifest_root, completion_root, policy, policy_sha, platform,
    lock_token,
):
    _, missing = processed_marker_plan(
        scan_root, manifest_root, completion_root,
        policy, policy_sha, platform,
    )
    published = {}
    for manifest_sha, marker, expected_sha in missing:
        check_lock(lock_token)
        _, observed_sha, _ = PROCESSED.publish_marker(
            policy, policy_sha, marker,
        )
        if observed_sha != expected_sha:
            raise ValueError("processed marker publication drift")
        published[manifest_sha] = observed_sha
    validate_processed_coverage(
        scan_root, manifest_root, completion_root,
        policy, policy_sha, platform,
    )
    require_no_pending_predecessor_scans(
        policy, policy_sha, platform, scan_root,
    )
    return published


def catalog_identity(root, label):
    root = Path(root)
    if not root.exists():
        items = []
    else:
        root = POLICY.validate_private_directory(root)
        items = []
        for path in sorted(root.iterdir()):
            if path.name.startswith("."):
                continue
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                raise ValueError("%s-catalog-artifact" % label)
            raw, _ = POLICY.read_regular_bytes(
                path, "%s catalog artifact" % label, owner_private=True,
            )
            if hashlib.sha256(raw).hexdigest() != path.stem:
                raise ValueError("%s-catalog-content-address" % label)
            items.append(path.stem)
    return {
        "count": len(items),
        "sha256": hashlib.sha256(POLICY.canonical_json_bytes(items)).hexdigest(),
    }


def reject_duplicates(records):
    duplicate_indexes = set()
    by_identity = collections.defaultdict(list)
    by_range = collections.defaultdict(list)
    intervals = collections.defaultdict(list)
    for index, record in enumerate(records):
        by_identity[(record["session_id"], record["turn_id"])].append(index)
        by_range[(record["trace_relative"], record["segment_start"], record["segment_end"])].append(index)
        intervals[record["trace_relative"]].append((record["segment_start"], record["segment_end"], index))
    for group in list(by_identity.values()) + list(by_range.values()):
        if len(group) > 1:
            duplicate_indexes.update(group)
    for group in intervals.values():
        ordered = sorted(group)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1]:
                duplicate_indexes.update((previous[2], current[2]))
    accepted = []
    skipped = []
    for index, record in enumerate(records):
        if index in duplicate_indexes:
            skipped.append({"receipt_id": record["receipt_sha256"], "reason_code": "duplicate-or-overlapping-segment"})
        else:
            accepted.append(record)
    return accepted, skipped


def build_manifest(policy, policy_sha, records, skipped, cutoff, platform):
    selector_sha = code_sha256(Path(__file__))
    scanner_sha = code_sha256(HERE / "trace-scan.py")
    ordered = sorted(records, key=lambda item: (item["completed_at"], item["session_id"], item["turn_id"]))
    selected = []
    total_bytes = 0
    cap_skips = []
    for record in ordered:
        if len(selected) >= policy["selection"]["max_files"]:
            cap_skips.append({"receipt_id": record["receipt_sha256"], "reason_code": "count-limit"})
            continue
        if total_bytes + record["segment_size"] > policy["selection"]["max_total_bytes"]:
            cap_skips.append({"receipt_id": record["receipt_sha256"], "reason_code": "total-byte-limit"})
            continue
        selected.append(record)
        total_bytes += record["segment_size"]
    skipped = sorted(skipped + cap_skips, key=lambda item: (item["receipt_id"], item["reason_code"]))
    if not selected:
        return None, {
            "schema_version": 2,
            "receipt_type": "reflection-selection-noop",
            "status": "no-eligible-traces",
            "platform": platform, "zone": "work", "policy_sha256": policy_sha,
            "delegation_id": policy["delegation_id"], "selection_cutoff": iso_utc(cutoff),
            "selector_sha256": selector_sha,
            "parameters": selection_parameters(policy),
            "eligible_count": 0, "skipped": skipped,
        }
    manifest = {
        "schema_version": 3, "manifest_type": "autonomous-reflection-selection",
        "approval_mode": "standing-autonomous-delegation", "delegated_by": POLICY.OWNER,
        "selected_by": "local:trace-manifest-selector", "delegation_id": policy["delegation_id"],
        "policy_sha256": policy_sha, "platform": platform, "zone": "work",
        "selection_cutoff": iso_utc(cutoff), "selector_sha256": selector_sha,
        "scanner_sha256": scanner_sha,
        "parameters": selection_parameters(policy),
        "total_segment_bytes": total_bytes, "completion_receipts": selected,
        "skipped": skipped,
    }
    return manifest, None


def load_completion_receipt(
    receipt_sha, completion_root, policy, policy_sha, platform,
):
    matches = []
    for authority, catalog in completion_catalogs(
        completion_root, policy, policy_sha, platform,
    ):
        path = Path(catalog) / (receipt_sha + ".json")
        try:
            raw, _ = POLICY.read_regular_bytes(
                path, "completion receipt", owner_private=True,
            )
        except FileNotFoundError:
            continue
        if hashlib.sha256(raw).hexdigest() != receipt_sha:
            raise ValueError("manifest-receipt-drift")
        receipt = POLICY.strict_json_loads(raw, "completion receipt")
        receipt, _, _ = validate_completion_receipt(
            receipt, receipt_sha, policy, authority["policy_sha256"],
            requested_platform=platform, authority=authority,
        )
        if raw != POLICY.canonical_json_bytes(receipt):
            raise ValueError("manifest-receipt-drift")
        matches.append((receipt, authority))
    if len(matches) != 1:
        raise ValueError("manifest-receipt-drift")
    return matches[0]


def validate_manifest_value(
    value, manifest_sha, policy, policy_sha, platform, completion_root,
):
    expected = {
        "schema_version", "manifest_type", "approval_mode", "delegated_by",
        "selected_by", "delegation_id", "policy_sha256", "platform", "zone",
        "selection_cutoff", "selector_sha256", "scanner_sha256", "parameters",
        "total_segment_bytes", "completion_receipts", "skipped",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("manifest-schema")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 3
        or value.get("manifest_type") != "autonomous-reflection-selection"
        or value.get("approval_mode") != "standing-autonomous-delegation"
        or value.get("delegated_by") != POLICY.OWNER
        or value.get("selected_by") != "local:trace-manifest-selector"
        or value.get("delegation_id") != policy["delegation_id"]
        or value.get("policy_sha256") != policy_sha
        or value.get("platform") != platform
        or value.get("zone") != "work"
        or [platform, "work"] not in policy["allowed_platform_zones"]
        or value.get("selector_sha256") != policy["code"]["selector_sha256"]
        or value.get("scanner_sha256") != policy["code"]["scanner_sha256"]
        or value.get("parameters") != selection_parameters(policy)
    ):
        raise ValueError("manifest-authority")
    cutoff = parse_timestamp(value.get("selection_cutoff"), "selection_cutoff")
    if cutoff > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
        raise ValueError("manifest-cutoff-future")
    records = value.get("completion_receipts")
    if not isinstance(records, list) or not records or len(records) > policy["selection"]["max_files"]:
        raise ValueError("manifest-record-count")
    expected_record_fields = manifest_record_fields(platform)
    projected_records = []
    total = 0
    for record in records:
        if not isinstance(record, dict) or set(record) != expected_record_fields:
            raise ValueError("manifest-record-schema")
        receipt_sha = record.get("receipt_sha256")
        if not isinstance(receipt_sha, str) or not SHA256_RE.fullmatch(receipt_sha):
            raise ValueError("manifest-receipt-hash")
        receipt, _ = load_completion_receipt(
            receipt_sha, completion_root, policy, policy_sha, platform,
        )
        projected = {
            key: receipt[key] for key in expected_record_fields
            if key != "receipt_sha256"
        }
        projected["receipt_sha256"] = receipt_sha
        if projected != record:
            raise ValueError("manifest-receipt-projection")
        projected_records.append(projected)
        total += record["segment_size"]
    ordered = sorted(
        projected_records,
        key=lambda item: (item["completed_at"], item["session_id"], item["turn_id"]),
    )
    if records != ordered or len({item["receipt_sha256"] for item in records}) != len(records):
        raise ValueError("manifest-record-order")
    if total != value.get("total_segment_bytes") or total > policy["selection"]["max_total_bytes"]:
        raise ValueError("manifest-total-bytes")
    skipped = value.get("skipped")
    if not isinstance(skipped, list):
        raise ValueError("manifest-skipped")
    for item in skipped:
        if (
            not isinstance(item, dict) or set(item) != {"receipt_id", "reason_code"}
            or not SHA256_RE.fullmatch(str(item.get("receipt_id", "")))
            or not re.fullmatch(r"[a-z0-9-]+", str(item.get("reason_code", "")))
        ):
            raise ValueError("manifest-skipped")
    if skipped != sorted(skipped, key=lambda item: (item["receipt_id"], item["reason_code"])):
        raise ValueError("manifest-skipped-order")
    if manifest_sha != hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest():
        raise ValueError("manifest-content-address")
    return records


def load_manifest_artifact(
    path, manifest_root, policy, policy_sha, platform, completion_root=None,
):
    root = POLICY.validate_private_directory(manifest_root)
    candidate = Path(os.path.abspath(str(path)))
    try:
        candidate.relative_to(root)
        if candidate.resolve(strict=True) != candidate:
            raise ValueError("manifest-path")
    except (OSError, ValueError) as error:
        raise ValueError("manifest-path") from error
    if not re.fullmatch(r"[0-9a-f]{64}\.json", candidate.name):
        raise ValueError("manifest-path")
    raw, _ = POLICY.read_regular_bytes(
        candidate, "delegated manifest", owner_private=True,
    )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != candidate.stem:
        raise ValueError("manifest-content-address")
    value = POLICY.strict_json_loads(raw, "delegated manifest")
    if completion_root is None:
        completion_root = POLICY.generation_root(
            VAULT / policy["roots"]["completion_work"], policy_sha, platform,
            create=False,
        )
    validate_manifest_value(
        value, digest, policy, policy_sha, platform, completion_root,
    )
    return candidate, digest, value


def compute_selection(
    policy, policy_sha, cutoff, completion_root, scan_root, manifest_root, platform,
):
    eligible, skipped = read_completion_receipts(
        completion_root, policy, policy_sha, cutoff, platform,
    )
    eligible, duplicate_skips = reject_duplicates(eligible)
    skipped.extend(duplicate_skips)
    validate_processed_coverage(
        scan_root, manifest_root, completion_root,
        policy, policy_sha, platform,
    )
    processed_records = require_no_pending_predecessor_scans(
        policy, policy_sha, platform, scan_root,
    )
    unfinished = [
        state for state in read_scan_manifests(
            scan_root, manifest_root, completion_root,
            policy, policy_sha, platform,
        ).values()
        if state["scan"]["signal_count"] > 0 and not state["finalized"]
    ]
    if unfinished:
        state = min(
            unfinished,
            key=lambda item: (
                item["manifest"]["selection_cutoff"],
                item["scan"]["manifest_sha256"],
            ),
        )
        return state["manifest"], None
    processed_receipts = set(processed_records)
    pending = []
    for record in eligible:
        if record["receipt_sha256"] in processed_receipts:
            skipped.append({"receipt_id": record["receipt_sha256"], "reason_code": "already-processed"})
        else:
            pending.append(record)
    manifest, no_op = build_manifest(
        policy, policy_sha, pending, skipped, cutoff, platform,
    )
    if no_op is not None:
        _, finalization_root = disposition_catalog(
            policy, policy_sha, platform,
        )
        no_op["catalogs"] = {
            "completion": completion_catalog_identity(
                completion_root, policy, policy_sha, platform,
            ),
            "manifest": catalog_identity(manifest_root, "manifest"),
            "scan": catalog_identity(scan_root, "scan"),
            "finalization": catalog_identity(finalization_root, "finalization"),
            "processed": catalog_identity(
                PROCESSED.catalog_root(policy, platform), "processed",
            ),
        }
    return manifest, no_op


def validate_noop_receipt(
    value, digest, policy, policy_sha, platform,
    completion_root, scan_root, manifest_root, replay=True,
):
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version", "receipt_type", "status", "platform", "zone",
            "policy_sha256", "delegation_id", "selection_cutoff",
            "selector_sha256", "parameters", "eligible_count", "skipped",
            "catalogs",
        }
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
        or value.get("receipt_type") != "reflection-selection-noop"
        or value.get("status") != "no-eligible-traces"
        or value.get("platform") != platform or value.get("zone") != "work"
        or value.get("policy_sha256") != policy_sha
        or value.get("delegation_id") != policy["delegation_id"]
        or value.get("selector_sha256") != policy["code"]["selector_sha256"]
        or value.get("parameters") != selection_parameters(policy)
        or value.get("eligible_count") != 0
        or value.get("catalogs") != {
            "completion": completion_catalog_identity(
                completion_root, policy, policy_sha, platform,
            ),
            "manifest": catalog_identity(manifest_root, "manifest"),
            "scan": catalog_identity(scan_root, "scan"),
            "finalization": catalog_identity(
                disposition_catalog(policy, policy_sha, platform)[1],
                "finalization",
            ),
            "processed": catalog_identity(
                PROCESSED.catalog_root(policy, platform), "processed",
            ),
        }
        or digest != hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest()
    ):
        raise ValueError("selection-noop-authority")
    cutoff = parse_timestamp(value.get("selection_cutoff"), "selection_cutoff")
    if cutoff > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
        raise ValueError("selection-noop-cutoff")
    skipped = value.get("skipped")
    if not isinstance(skipped, list):
        raise ValueError("selection-noop-skipped")
    for item in skipped:
        if (
            not isinstance(item, dict) or set(item) != {"receipt_id", "reason_code"}
            or not SHA256_RE.fullmatch(str(item.get("receipt_id", "")))
            or not re.fullmatch(r"[a-z0-9-]+", str(item.get("reason_code", "")))
        ):
            raise ValueError("selection-noop-skipped")
    if skipped != sorted(skipped, key=lambda item: (item["receipt_id"], item["reason_code"])):
        raise ValueError("selection-noop-skipped")
    if replay:
        manifest, expected = compute_selection(
            policy, policy_sha, cutoff, completion_root, scan_root,
            manifest_root, platform,
        )
        if manifest is not None or expected != value:
            raise ValueError("selection-noop-replay-drift")
    return value


def load_noop_receipt(
    root, digest, policy, policy_sha, platform,
    completion_root, scan_root, manifest_root, replay=True,
):
    if not SHA256_RE.fullmatch(str(digest or "")):
        raise ValueError("selection-noop-path")
    root = POLICY.validate_private_directory(root)
    raw, _ = POLICY.read_regular_bytes(
        root / (digest + ".json"), "selection no-op receipt", owner_private=True,
    )
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("selection-noop-content-address")
    value = POLICY.strict_json_loads(raw, "selection no-op receipt")
    return validate_noop_receipt(
        value, digest, policy, policy_sha, platform,
        completion_root, scan_root, manifest_root, replay=replay,
    )


def select(
    policy, policy_sha, cutoff, completion_root, scan_root, manifest_root,
    platform, lock_token=None, noop_root=None, publish_noop=False,
):
    if lock_token is not None:
        repair_processed_markers(
            scan_root, manifest_root, completion_root,
            policy, policy_sha, platform, lock_token,
        )
    manifest, no_op = compute_selection(
        policy, policy_sha, cutoff, completion_root, scan_root, manifest_root,
        platform,
    )
    if manifest is None:
        result = dict(no_op)
        result["selection_receipt_published"] = False
        result["selection_receipt_sha256"] = None
        if publish_noop:
            check_lock(lock_token)
            path, digest, _ = POLICY.publish_immutable(
                noop_root, POLICY.canonical_json_bytes(no_op),
            )
            result["selection_receipt"] = path.name
            result["selection_receipt_sha256"] = digest
            result["selection_receipt_published"] = True
        return None, result
    check_lock(lock_token)
    raw = POLICY.canonical_json_bytes(manifest)
    path, digest, created = POLICY.publish_immutable(manifest_root, raw)
    return path, {
        "schema_version": 1, "status": "manifest-ready", "platform": platform, "zone": "work",
        "manifest": path.name, "manifest_sha256": digest, "created": created,
        "selected_count": len(manifest["completion_receipts"]),
        "selected_bytes": manifest["total_segment_bytes"], "skipped": manifest["skipped"],
    }


def self_test():
    with tempfile.TemporaryDirectory(prefix="trace-manifest-test-") as directory:
        root = Path(os.path.realpath(directory))
        policy = {
            "vault": {"id": "{{VAULT_ID}}", "path": str(VAULT)},
            "delegation_id": "test-delegation", "not_before": "2026-08-21T00:00:00Z",
            "roots": {
                "disposition_work": str(root / "dispositions"),
                "processed_work": str(root / "processed"),
            },
            "allowed_platform_zones": [["claude", "work"], ["codex", "work"]],
            "code": {
                "claude_hook_sha256": "c" * 64,
                "codex_session_hook_sha256": "a" * 64,
                "verifier_codex_sha256": "d" * 64,
                "scanner_sha256": code_sha256(HERE / "trace-scan.py"),
                "selector_sha256": code_sha256(Path(__file__)),
                "processed_sha256": code_sha256(HERE / "reflection-processed.py"),
            },
            "selection": {"ordering": ["completed_at", "session_id", "turn_id"],
                          "max_age_days": 30, "max_files": 2,
                          "max_file_bytes": 1000, "max_total_bytes": 1500},
        }
        policy_sha = "b" * 64

        def repair_for_test(
            scan_catalog, manifest_catalog, completion_catalog,
            test_policy=policy, test_policy_sha=policy_sha,
            test_platform="codex",
        ):
            original = globals()["check_lock"]
            globals()["check_lock"] = lambda token: None
            try:
                return repair_processed_markers(
                    scan_catalog, manifest_catalog, completion_catalog,
                    test_policy, test_policy_sha, test_platform, "f" * 64,
                )
            finally:
                globals()["check_lock"] = original

        completion = root / "completion"
        POLICY.ensure_private_directory(completion)
        for number in (1, 2):
            session_id = "%08d-1111-1111-1111-111111111111" % number
            turn_id = "%08d-2222-2222-2222-222222222222" % number
            receipt = {
                "schema_version": 2, "receipt_type": "codex-completed-turn-segment",
                "platform": "codex", "session_id": session_id, "turn_id": turn_id,
                "trace_relative": "2026/08/21/%d.jsonl" % number,
                "trace_identity": {"device": 1, "inode": number},
                "segment_start": 0, "segment_end": 100, "segment_size": 100,
                "segment_sha256": "%064x" % number, "session_meta_sha256": "%064x" % (number + 2),
                "task_started_at": "2026-08-21T12:00:0%dZ" % number,
                "completed_at": "2026-08-21T12:01:0%dZ" % number,
                "terminal_reason": "task_complete", "vault_id": "{{VAULT_ID}}",
                "vault_path": str(VAULT), "initial_zone": "work", "zone_transitions": [],
                "eligible": True, "eligibility_reason": "eligible", "delegated_by": POLICY.OWNER,
                "recorded_by": "codex:trace-session-hook", "delegation_id": "test-delegation",
                "policy_sha256": policy_sha, "session_hook_sha256": "a" * 64,
                "registration_policy_sha256": policy_sha,
                "registration_hook_sha256": "a" * 64,
            }
            POLICY.publish_immutable(completion, POLICY.canonical_json_bytes(receipt))
        claude_receipt = {
            **receipt,
            "receipt_type": "claude-stop-turn-segment", "platform": "claude",
            "session_id": "f" * 64, "turn_id": "e" * 64,
            "terminal_reason": "stop-hook", "recorded_by": "claude:stop-hook",
            "session_hook_sha256": "c" * 64,
            "registration_hook_sha256": "c" * 64,
            "prompt_sha256": "d" * 64, "prompt_sequence": 1,
        }
        _, claude_receipt_sha, _ = POLICY.publish_immutable(
            completion, POLICY.canonical_json_bytes(claude_receipt),
        )
        records, skipped = read_completion_receipts(
            completion, policy, policy_sha,
            parse_timestamp("2026-08-21T13:00:00Z", "cutoff"), "codex",
        )
        assert len(records) == 2 and not skipped
        scan_root = root / "scan"
        manifest_root = root / "manifest"
        manifest, no_op = build_manifest(
            policy, policy_sha, records, [],
            parse_timestamp("2026-08-21T13:00:00Z", "cutoff"), "codex",
        )
        assert no_op is None and len(manifest["completion_receipts"]) == 2
        for schema in (3.0, True):
            malformed = {**manifest, "schema_version": schema}
            try:
                validate_manifest_value(
                    malformed,
                    hashlib.sha256(POLICY.canonical_json_bytes(malformed)).hexdigest(),
                    policy, policy_sha, "codex", completion,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("non-integer manifest schema was accepted")
        computed, computed_no_op = compute_selection(
            policy, policy_sha,
            parse_timestamp("2026-08-21T13:00:00Z", "cutoff"),
            completion, scan_root, manifest_root, "codex",
        )
        assert computed_no_op is None and computed == manifest
        _, manifest_sha, _ = POLICY.publish_immutable(
            manifest_root, POLICY.canonical_json_bytes(manifest),
        )
        scan = {
            "schema_version": 1, "receipt_type": "reflection-scan", "status": "completed",
            "platform": "codex", "zone": "work", "manifest_sha256": manifest_sha,
            "policy_sha256": policy_sha, "delegation_id": "test-delegation",
            "scanner_sha256": policy["code"]["scanner_sha256"],
            "selector_sha256": policy["code"]["selector_sha256"],
            "parameters": selection_parameters(policy),
            "completion_receipt_sha256s": [
                item["receipt_sha256"] for item in manifest["completion_receipts"]
            ],
            "segments": [
                {"receipt_sha256": item["receipt_sha256"],
                 "segment_sha256": item["segment_sha256"], "signal_count": 0}
                for item in manifest["completion_receipts"]
            ],
            "skipped_segments": [], "signal_count": 0,
            "signal_set_sha256": hashlib.sha256(POLICY.canonical_json_bytes([])).hexdigest(),
            "verified_at": "2026-08-21T13:00:00Z",
        }
        for schema in (1.0, True):
            malformed = {**scan, "schema_version": schema}
            try:
                validate_scan_receipt(
                    malformed,
                    hashlib.sha256(POLICY.canonical_json_bytes(malformed)).hexdigest(),
                    manifest_sha, manifest, policy, policy_sha, "codex",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("non-integer scan schema was accepted")
        POLICY.publish_immutable(scan_root, POLICY.canonical_json_bytes(scan))
        processed = read_processed_manifests(
            scan_root, manifest_root, completion, policy, policy_sha, "codex",
        )
        assert set(processed) == {manifest_sha}
        assert read_processed_manifests(
            scan_root, manifest_root, completion, policy, policy_sha, "claude",
        ) == {}
        try:
            compute_selection(
                policy, policy_sha,
                parse_timestamp("2026-08-21T13:00:00Z", "cutoff"),
                completion, scan_root, manifest_root, "codex",
            )
        except ValueError as error:
            assert str(error) == "processed marker is missing"
        else:
            raise AssertionError("missing processed marker was ignored")
        repaired = repair_for_test(scan_root, manifest_root, completion)
        assert set(repaired) == {manifest_sha}
        selected_after_scan, durable_noop = compute_selection(
            policy, policy_sha,
            parse_timestamp("2026-08-21T13:00:00Z", "cutoff"),
            completion, scan_root, manifest_root, "codex",
        )
        assert selected_after_scan is None and durable_noop["catalogs"]["scan"]["count"] == 1
        noop_root = root / "noop"
        _, noop_sha, _ = POLICY.publish_immutable(
            noop_root, POLICY.canonical_json_bytes(durable_noop),
        )
        for schema in (2.0, True):
            malformed = {**durable_noop, "schema_version": schema}
            try:
                validate_noop_receipt(
                    malformed,
                    hashlib.sha256(POLICY.canonical_json_bytes(malformed)).hexdigest(),
                    policy, policy_sha, "codex", completion, scan_root,
                    manifest_root, replay=False,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("non-integer no-op schema was accepted")
        assert load_noop_receipt(
            noop_root, noop_sha, policy, policy_sha, "codex",
            completion, scan_root, manifest_root,
        )["status"] == "no-eligible-traces"
        signal_policy = {
            **policy,
            "roots": {
                **policy["roots"],
                "processed_work": str(root / "signal-processed"),
            },
        }
        signal_scan_root = root / "signal-scan"
        signal_id = "9" * 64
        signal_scan = {
            **scan,
            "segments": [dict(item) for item in scan["segments"]],
            "signal_count": 1,
            "signal_set_sha256": hashlib.sha256(
                POLICY.canonical_json_bytes([signal_id])
            ).hexdigest(),
        }
        signal_scan["segments"][0]["signal_count"] = 1
        _, signal_scan_sha, _ = POLICY.publish_immutable(
            signal_scan_root, POLICY.canonical_json_bytes(signal_scan),
        )
        assert read_processed_manifests(
            signal_scan_root, manifest_root, completion,
            signal_policy, policy_sha, "codex",
        ) == {}
        still_pending, _ = compute_selection(
            signal_policy, policy_sha,
            parse_timestamp("2026-08-21T13:00:01Z", "cutoff"),
            completion, signal_scan_root, manifest_root, "codex",
        )
        assert still_pending == manifest
        disposition_root, finalization_root = disposition_catalog(
            signal_policy, policy_sha, "codex",
        )
        checkpoint_plan = {
            "parent_sha": "1" * 40, "commit_sha": "1" * 40,
            "changed_paths": [],
        }
        signals = [{"signal_id": signal_id, "action": "rejected"}]
        plan = {
            "schema_version": 2, "verdict": "PASS", "platform": "codex",
            "policy_sha256": policy_sha, "manifest_sha256": manifest_sha,
            "scan_receipt_sha256": signal_scan_sha,
            "verifier": {
                "name": "evolution-verifier", "verdict": "PASS",
                "skill_sha256": "d" * 64,
            },
            "checkpoint": checkpoint_plan, "signals": signals,
        }
        plan_sha = hashlib.sha256(POLICY.canonical_json_bytes(plan)).hexdigest()
        disposition = {
            "schema_version": 2, "receipt_type": "reflection-disposition",
            "status": "completed", "platform": "codex", "zone": "work",
            "approval_scope": "standing-autonomous-l0-l2",
            "delegated_by": POLICY.OWNER,
            "policy_sha256": policy_sha, "delegation_id": "test-delegation",
            "manifest_sha256": manifest_sha,
            "scan_receipt_sha256": signal_scan_sha,
            "signal_count": 1,
            "signal_set_sha256": signal_scan["signal_set_sha256"],
            "plan_sha256": plan_sha, "signals": signals,
            "outcome_pages": [],
            "verifier": {
                "name": "evolution-verifier", "verdict": "PASS",
                "skill_sha256": "d" * 64, "decision_sha256": plan_sha,
                "manifest_sha256": manifest_sha,
                "scan_receipt_sha256": signal_scan_sha,
                "reviewed_checkpoint_sha256": hashlib.sha256(
                    POLICY.canonical_json_bytes(checkpoint_plan)
                ).hexdigest(),
            },
            "checkpoint": {
                **checkpoint_plan,
                "committed_at": "2026-08-21T13:00:00Z",
                "changed_paths_sha256": hashlib.sha256(
                    POLICY.canonical_json_bytes([])
                ).hexdigest(),
            },
            "retrieval_acceptance": {
                "context_receipt_sha256": "2" * 64,
                "resolver": EVIDENCE.MEMORY.resolver_identity(),
                "query_sha256": "3" * 64, "zone": "work",
                "loadout": "general", "project": None,
                "include_l0": False, "include_stale": False,
                "explain": False, "effective_item_limit": 8,
                "index_snapshot_sha256": "4" * 64,
                "loadouts_sha256": "5" * 64,
                "selected_chunks": [], "l3_pages": [],
            },
            "completed_at": "2026-08-21T13:00:00Z",
        }
        for malformed in (
            {**disposition, "schema_version": 2.0},
            {
                **disposition,
                "retrieval_acceptance": {
                    **disposition["retrieval_acceptance"], "loadout": 7,
                },
            },
        ):
            malformed_sha = hashlib.sha256(
                POLICY.canonical_json_bytes(malformed)
            ).hexdigest()
            try:
                validate_disposition_projection(
                    malformed, malformed_sha, signal_scan,
                    signal_policy, policy_sha, "codex",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("malformed disposition projection was accepted")
        _, disposition_sha, _ = POLICY.publish_immutable(
            disposition_root, POLICY.canonical_json_bytes(disposition),
        )
        finalization = build_finalization_marker(disposition, disposition_sha)
        malformed_finalization = {**finalization, "schema_version": 1.0}
        try:
            validate_finalization_marker(
                malformed_finalization,
                hashlib.sha256(POLICY.canonical_json_bytes(
                    malformed_finalization,
                )).hexdigest(),
                disposition_root, signal_scan, signal_policy, policy_sha, "codex",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("non-integer finalization schema was accepted")
        POLICY.publish_immutable(
            finalization_root, POLICY.canonical_json_bytes(finalization),
        )
        original_live_validator = EVIDENCE.validate_live_disposition
        EVIDENCE.validate_live_disposition = lambda value, *args: value
        try:
            assert set(read_processed_manifests(
                signal_scan_root, manifest_root, completion,
                signal_policy, policy_sha, "codex",
            )) == {manifest_sha}
            repaired_signal = repair_for_test(
                signal_scan_root, manifest_root, completion,
                signal_policy, policy_sha, "codex",
            )
            assert set(repaired_signal) == {manifest_sha}
            signal_after, signal_noop = compute_selection(
                signal_policy, policy_sha,
                parse_timestamp("2026-08-21T13:00:00Z", "cutoff"),
                completion, signal_scan_root, manifest_root, "codex",
            )
            assert signal_after is None and signal_noop is not None
        finally:
            EVIDENCE.validate_live_disposition = original_live_validator

        def exercise_predecessor(platform, ordinal):
            current_sha = "7" * 64
            predecessor_sha = "6" * 64
            old_claude_hook, old_codex_hook = "8" * 64, "9" * 64
            code = {
                **policy["code"],
                "verifier_claude_sha256": "d" * 64,
                "verifier_codex_sha256": "d" * 64,
            }
            base = root / ("predecessor-completion-" + platform)
            test_policy = {
                **policy,
                "roots": {
                    "completion_work": str(base),
                    "disposition_work": str(root / ("predecessor-disposition-" + platform)),
                    "processed_work": str(root / ("predecessor-processed-" + platform)),
                },
                "code": code,
                "completion_predecessors": [{
                    "policy_sha256": predecessor_sha,
                    "delegation_id": "predecessor-delegation",
                    "not_before": "2026-08-20T00:00:00Z",
                    "claude_hook_sha256": old_claude_hook,
                    "codex_session_hook_sha256": old_codex_hook,
                }],
            }
            completion_root = POLICY.generation_root(
                base, current_sha, platform,
            )
            predecessor_root = POLICY.generation_root(
                base, predecessor_sha, platform,
            )
            scan_catalog = root / ("predecessor-scan-" + platform)
            manifest_catalog = root / ("predecessor-manifest-" + platform)
            cutoff = parse_timestamp("2026-08-21T13:00:00Z", "cutoff")
            _, stale_noop = compute_selection(
                test_policy, current_sha, cutoff, completion_root,
                scan_catalog, manifest_catalog, platform,
            )
            assert stale_noop is not None
            receipt = {
                "schema_version": 2,
                "receipt_type": "codex-completed-turn-segment",
                "platform": platform,
                "session_id": "%08d-1111-1111-1111-111111111111" % ordinal,
                "turn_id": "%08d-2222-2222-2222-222222222222" % ordinal,
                "trace_relative": "2026/08/21/predecessor-%s.jsonl" % platform,
                "trace_identity": {"device": 1, "inode": ordinal},
                "segment_start": 0, "segment_end": 100, "segment_size": 100,
                "segment_sha256": "%064x" % ordinal,
                "session_meta_sha256": "%064x" % (ordinal + 20),
                "task_started_at": "2026-08-21T12:00:00Z",
                "completed_at": "2026-08-21T12:01:00Z",
                "terminal_reason": "task_complete",
                "vault_id": "{{VAULT_ID}}", "vault_path": str(VAULT),
                "initial_zone": "work", "zone_transitions": [],
                "eligible": True, "eligibility_reason": "eligible",
                "delegated_by": POLICY.OWNER,
                "recorded_by": "codex:trace-session-hook",
                "delegation_id": "predecessor-delegation",
                "policy_sha256": predecessor_sha,
                "session_hook_sha256": old_codex_hook,
                "registration_policy_sha256": predecessor_sha,
                "registration_hook_sha256": old_codex_hook,
            }
            if platform == "claude":
                receipt.update({
                    "receipt_type": "claude-stop-turn-segment",
                    "session_id": "%064x" % ordinal,
                    "turn_id": "%064x" % (ordinal + 1),
                    "terminal_reason": "stop-hook",
                    "recorded_by": "claude:stop-hook",
                    "session_hook_sha256": old_claude_hook,
                    "registration_hook_sha256": old_claude_hook,
                    "prompt_sha256": "%064x" % (ordinal + 2),
                    "prompt_sequence": 1,
                })
            _, receipt_sha, _ = POLICY.publish_immutable(
                predecessor_root, POLICY.canonical_json_bytes(receipt),
            )
            try:
                validate_noop_receipt(
                    stale_noop,
                    hashlib.sha256(
                        POLICY.canonical_json_bytes(stale_noop)
                    ).hexdigest(),
                    test_policy, current_sha, platform, completion_root,
                    scan_catalog, manifest_catalog, replay=False,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("predecessor receipt did not invalidate no-op")
            selected, no_op = compute_selection(
                test_policy, current_sha, cutoff, completion_root,
                scan_catalog, manifest_catalog, platform,
            )
            assert no_op is None
            assert [item["receipt_sha256"] for item in selected["completion_receipts"]] == [receipt_sha]
            _, selected_sha, _ = POLICY.publish_immutable(
                manifest_catalog, POLICY.canonical_json_bytes(selected),
            )
            signal = hashlib.sha256((platform + "-signal").encode()).hexdigest()
            scan_value = {
                "schema_version": 1, "receipt_type": "reflection-scan",
                "status": "completed", "platform": platform, "zone": "work",
                "manifest_sha256": selected_sha, "policy_sha256": current_sha,
                "delegation_id": test_policy["delegation_id"],
                "scanner_sha256": code["scanner_sha256"],
                "selector_sha256": code["selector_sha256"],
                "parameters": selection_parameters(test_policy),
                "completion_receipt_sha256s": [receipt_sha],
                "segments": [{
                    "receipt_sha256": receipt_sha,
                    "segment_sha256": receipt["segment_sha256"],
                    "signal_count": 1,
                }],
                "skipped_segments": [], "signal_count": 1,
                "signal_set_sha256": hashlib.sha256(
                    POLICY.canonical_json_bytes([signal])
                ).hexdigest(),
                "verified_at": "2026-08-21T13:00:00Z",
            }
            _, scan_sha, _ = POLICY.publish_immutable(
                scan_catalog, POLICY.canonical_json_bytes(scan_value),
            )
            checkpoint_plan = {
                "parent_sha": "1" * 40, "commit_sha": "1" * 40,
                "changed_paths": [],
            }
            signals = [{"signal_id": signal, "action": "rejected"}]
            plan = {
                "schema_version": 2, "verdict": "PASS", "platform": platform,
                "policy_sha256": current_sha,
                "manifest_sha256": selected_sha,
                "scan_receipt_sha256": scan_sha,
                "verifier": {
                    "name": "evolution-verifier", "verdict": "PASS",
                    "skill_sha256": code["verifier_%s_sha256" % platform],
                },
                "checkpoint": checkpoint_plan, "signals": signals,
            }
            plan_sha = hashlib.sha256(POLICY.canonical_json_bytes(plan)).hexdigest()
            disposition = {
                "schema_version": 2, "receipt_type": "reflection-disposition",
                "status": "completed", "platform": platform, "zone": "work",
                "approval_scope": "standing-autonomous-l0-l2",
                "delegated_by": POLICY.OWNER,
                "delegation_id": test_policy["delegation_id"],
                "policy_sha256": current_sha,
                "manifest_sha256": selected_sha,
                "scan_receipt_sha256": scan_sha, "signal_count": 1,
                "signal_set_sha256": scan_value["signal_set_sha256"],
                "plan_sha256": plan_sha, "signals": signals,
                "outcome_pages": [],
                "verifier": {
                    **plan["verifier"], "decision_sha256": plan_sha,
                    "manifest_sha256": selected_sha,
                    "scan_receipt_sha256": scan_sha,
                    "reviewed_checkpoint_sha256": hashlib.sha256(
                        POLICY.canonical_json_bytes(checkpoint_plan)
                    ).hexdigest(),
                },
                "checkpoint": {
                    **checkpoint_plan, "committed_at": "2026-08-21T13:00:00Z",
                    "changed_paths_sha256": hashlib.sha256(
                        POLICY.canonical_json_bytes([])
                    ).hexdigest(),
                },
                "retrieval_acceptance": {
                    "context_receipt_sha256": "2" * 64,
                    "resolver": EVIDENCE.MEMORY.resolver_identity(),
                    "query_sha256": "3" * 64, "zone": "work",
                    "loadout": "general", "project": None,
                    "include_l0": False, "include_stale": False,
                    "explain": False, "effective_item_limit": 8,
                    "index_snapshot_sha256": "4" * 64,
                    "loadouts_sha256": "5" * 64,
                    "selected_chunks": [], "l3_pages": [],
                },
                "completed_at": "2026-08-21T13:00:00Z",
            }
            disposition_root, finalization_root = disposition_catalog(
                test_policy, current_sha, platform,
            )
            _, disposition_sha, _ = POLICY.publish_immutable(
                disposition_root, POLICY.canonical_json_bytes(disposition),
            )
            POLICY.publish_immutable(
                finalization_root,
                POLICY.canonical_json_bytes(
                    build_finalization_marker(disposition, disposition_sha)
                ),
            )
            live_validator = EVIDENCE.validate_live_disposition
            EVIDENCE.validate_live_disposition = lambda value, *args: value
            try:
                processed = read_processed_manifests(
                    scan_catalog, manifest_catalog, completion_root,
                    test_policy, current_sha, platform,
                )
                assert list(processed) == [selected_sha]
                repair_for_test(
                    scan_catalog, manifest_catalog, completion_root,
                    test_policy, current_sha, platform,
                )
                after, durable = compute_selection(
                    test_policy, current_sha, cutoff, completion_root,
                    scan_catalog, manifest_catalog, platform,
                )
                assert after is None and durable is not None
                assert durable["catalogs"]["completion"][1] == {
                    "policy_sha256": predecessor_sha,
                    "catalog": catalog_identity(predecessor_root, "completion"),
                }
            finally:
                EVIDENCE.validate_live_disposition = live_validator

        exercise_predecessor("codex", 30)
        exercise_predecessor("claude", 40)
        for ordinal, platform in enumerate(("codex", "claude"), 60):
            current_sha = "%064x" % ordinal
            predecessor_sha = "%064x" % (ordinal + 1)
            receipt_sha = "%064x" % (ordinal + 2)
            scan_base = root / ("rollover-scan-" + platform)
            completion_base = root / ("rollover-completion-" + platform)
            rollover_policy = {
                **policy,
                "roots": {
                    "completion_work": str(completion_base),
                    "scan_receipt_work": str(scan_base),
                    "disposition_work": str(root / ("rollover-disposition-" + platform)),
                    "processed_work": str(root / ("rollover-processed-" + platform)),
                },
                "completion_predecessors": [{
                    "policy_sha256": predecessor_sha,
                    "delegation_id": "predecessor-delegation",
                    "not_before": "2026-08-20T00:00:00Z",
                    "claude_hook_sha256": "a" * 64,
                    "codex_session_hook_sha256": "b" * 64,
                }],
            }
            current_completion = POLICY.generation_root(
                completion_base, current_sha, platform,
            )
            current_scan = POLICY.generation_root(
                scan_base, current_sha, platform,
            )
            predecessor_scan = POLICY.generation_root(
                scan_base, predecessor_sha, platform,
            )
            rollover_manifest_root = root / ("rollover-manifest-" + platform)
            POLICY.ensure_private_directory(rollover_manifest_root)
            signal = "%064x" % (ordinal + 3)
            scan_value = {
                "schema_version": 1, "receipt_type": "reflection-scan",
                "status": "completed", "platform": platform, "zone": "work",
                "manifest_sha256": "%064x" % (ordinal + 4),
                "policy_sha256": predecessor_sha,
                "delegation_id": "predecessor-delegation",
                "scanner_sha256": "c" * 64,
                "selector_sha256": "d" * 64,
                "parameters": selection_parameters(rollover_policy),
                "completion_receipt_sha256s": [receipt_sha],
                "segments": [], "skipped_segments": [], "signal_count": 1,
                "signal_set_sha256": hashlib.sha256(
                    POLICY.canonical_json_bytes([signal])
                ).hexdigest(),
                "verified_at": "2026-08-21T13:00:00Z",
            }
            _, scan_sha, _ = POLICY.publish_immutable(
                predecessor_scan, POLICY.canonical_json_bytes(scan_value),
            )
            pending = pending_predecessor_scans(
                rollover_policy, current_sha, platform, current_scan, {},
            )
            assert [item["scan_receipt_sha256"] for item in pending] == [scan_sha]
            assert pending_predecessor_scans(
                rollover_policy, current_sha, platform, current_scan,
                {receipt_sha: {"scan_receipt_sha256": scan_sha}},
            ) == []
            try:
                compute_selection(
                    rollover_policy, current_sha,
                    parse_timestamp("2026-08-21T13:00:00Z", "cutoff"),
                    current_completion, current_scan, rollover_manifest_root,
                    platform,
                )
            except ValueError as error:
                assert str(error).startswith(
                    "unfinished reflection cycle belongs to predecessor policy"
                )
            else:
                raise AssertionError("policy rollover duplicated an unfinished cycle")
        bad_scan_root = root / "bad-scan"
        bad_scan = dict(scan)
        bad_scan["segments"] = [dict(item) for item in scan["segments"]]
        bad_scan["segments"][0]["segment_sha256"] = "9" * 64
        POLICY.publish_immutable(
            bad_scan_root, POLICY.canonical_json_bytes(bad_scan),
        )
        try:
            read_processed_manifests(
                bad_scan_root, manifest_root, completion,
                {**policy, "roots": {
                    "disposition_work": str(root / "bad-dispositions"),
                }},
                policy_sha, "codex",
            )
        except ValueError as error:
            assert str(error) == "scan-receipt-segments"
        else:
            raise AssertionError("substituted scan segment was accepted")
        rogue_root = root / "rogue-manifest"
        POLICY.ensure_private_directory(rogue_root)
        rogue = rogue_root / (manifest_sha + ".json")
        rogue.write_bytes(POLICY.canonical_json_bytes({**manifest, "skipped": [
            {"receipt_id": "9" * 64, "reason_code": "invented"},
        ]}))
        rogue.chmod(0o600)
        try:
            load_manifest_artifact(
                rogue, rogue_root, policy, policy_sha, "codex", completion,
            )
        except ValueError as error:
            assert str(error) == "manifest-content-address"
        else:
            raise AssertionError("substituted processed manifest was accepted")
        accepted, duplicate_skips = reject_duplicates(records + [dict(records[0])])
        assert len(accepted) == 1 and len(duplicate_skips) == 2
        empty, no_op = build_manifest(
            policy, policy_sha, [], [],
            parse_timestamp("2026-08-21T13:00:00Z", "cutoff"), "codex",
        )
        assert empty is None and no_op["status"] == "no-eligible-traces"
    print("trace-manifest self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    select_parser = subparsers.add_parser("select")
    select_parser.add_argument("--platform", choices=("claude", "codex"), required=True)
    select_parser.add_argument("--zone", choices=("work",), required=True)
    select_parser.add_argument("--cutoff")
    select_parser.add_argument("--publish-noop", action="store_true")
    select_parser.add_argument("--lock-token")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.command != "select":
        parser.error("choose select or --self-test")
    try:
        policy, policy_sha = POLICY.load_policy()
        cutoff = parse_timestamp(args.cutoff, "cutoff") if args.cutoff else datetime.datetime.now(datetime.timezone.utc)
        if cutoff > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
            raise ValueError("selection cutoff is in the future")
        completion_root = POLICY.generation_root(
            VAULT / policy["roots"]["completion_work"], policy_sha, args.platform,
            create=False,
        )
        scan_root = POLICY.generation_root(
            VAULT / policy["roots"]["scan_receipt_work"], policy_sha, args.platform,
            create=False,
        )
        manifest_root = POLICY.generation_root(
            VAULT / policy["roots"]["manifest_work"], policy_sha, args.platform,
            create=False,
        )
        noop_root = POLICY.generation_root(
            VAULT / policy["roots"]["selection_receipt_work"],
            policy_sha, args.platform, create=False,
        )
        if args.publish_noop:
            check_lock(args.lock_token)
        _, result = select(
            policy, policy_sha, cutoff, completion_root, scan_root, manifest_root,
            args.platform, lock_token=args.lock_token,
            noop_root=noop_root, publish_noop=args.publish_noop,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
