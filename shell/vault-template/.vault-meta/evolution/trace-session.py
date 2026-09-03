#!/usr/bin/env python3
"""Record work-zone task lifecycles and publish completed-turn receipts."""

import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
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
ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.I)
LOCK_PATH = ".vault-meta/write/work"


class TerminalEvidenceError(ValueError):
    """The completed trace is deterministically inconsistent with its state."""


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso_utc(value):
    return value.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value, label):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000.0 if value > 10_000_000_000 else value
        try:
            return datetime.datetime.fromtimestamp(
                seconds, tz=datetime.timezone.utc,
            )
        except (OSError, OverflowError, ValueError) as error:
            raise ValueError("%s is not a supported timestamp" % label) from error
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("%s is not an ISO timestamp" % label) from error
    if parsed.tzinfo is None:
        raise ValueError("%s lacks a timezone" % label)
    return parsed.astimezone(datetime.timezone.utc)


def check_lock(token):
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("a valid work writer lock token is required")
    result = subprocess.run(
        ["bash", "scripts/wiki-lock.sh", "check", LOCK_PATH, token],
        cwd=VAULT, capture_output=True, text=True,
    )
    if result.returncode or result.stdout.strip() != "owned":
        raise ValueError("work writer lock is not owned")


def lock_lifecycle(root):
    root = POLICY.ensure_private_directory(Path(root))
    descriptor = os.open(
        str(root / ".lifecycle.lock"),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
    ):
        os.close(descriptor)
        raise ValueError("trace lifecycle mutex is invalid")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def unlock_lifecycle(descriptor):
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def code_sha256():
    raw, _ = POLICY.read_regular_bytes(Path(__file__), "trace session hook")
    return hashlib.sha256(raw).hexdigest()


def locate_current_trace(trace_root, session_id):
    if not ID_RE.fullmatch(session_id or ""):
        raise ValueError("CODEX_SESSION_ID is missing or invalid")
    matches = []
    for year in sorted(Path(trace_root).iterdir()):
        if not year.is_dir() or not re.fullmatch(r"\d{4}", year.name):
            continue
        for month in sorted(year.iterdir()):
            if not month.is_dir() or not re.fullmatch(r"\d{2}", month.name):
                continue
            for day in sorted(month.iterdir()):
                if not day.is_dir() or not re.fullmatch(r"\d{2}", day.name):
                    continue
                candidate = next(
                    (item for item in day.iterdir()
                     if item.name.endswith("-%s.jsonl" % session_id)),
                    None,
                )
                if candidate is not None:
                    matches.append(candidate)
    if len(matches) != 1:
        raise ValueError("current Codex trace is not uniquely resolvable")
    candidate = Path(os.path.abspath(str(matches[0])))
    if candidate.resolve(strict=True) != candidate:
        raise ValueError("current Codex trace path contains a symlink")
    return candidate


def read_trace_bytes(path):
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_nlink != 1
        ):
            raise ValueError("Codex trace is not owner-controlled and singly linked")
        raw = handle.read()
        after = os.fstat(handle.fileno())
        if POLICY.file_identity(before) != POLICY.file_identity(after):
            raise ValueError("Codex trace changed while lifecycle metadata was read")
    return raw, after


def structural_trace(raw, expected_session_id, vault_path=VAULT):
    offset = 0
    session_meta = None
    turns = {}
    order = []
    for line in raw.splitlines(keepends=True):
        start, end = offset, offset + len(line)
        offset = end
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        item_type = item.get("type")
        payload = item.get("payload")
        if item_type == "session_meta" and isinstance(payload, dict) and session_meta is None:
            session_id = payload.get("id")
            cwd = payload.get("cwd")
            if session_id != expected_session_id or os.path.realpath(str(cwd or "")) != str(vault_path):
                raise ValueError("Codex session metadata targets another session or vault")
            session_meta = {
                "sha256": hashlib.sha256(line).hexdigest(),
                "cwd": str(vault_path),
            }
        if item_type != "event_msg" or not isinstance(payload, dict):
            continue
        event_type = payload.get("type")
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not ID_RE.fullmatch(turn_id):
            continue
        if event_type == "task_started":
            if turn_id in turns:
                raise ValueError("duplicate task_started marker")
            turns[turn_id] = {
                "turn_id": turn_id,
                "start_offset": start,
                "started_at": payload.get("started_at"),
                "completed_at": None,
                "end_offset": None,
            }
            order.append(turn_id)
        elif event_type == "task_complete" and turn_id in turns:
            if turns[turn_id]["end_offset"] is not None:
                raise ValueError("duplicate task_complete marker")
            turns[turn_id]["completed_at"] = payload.get("completed_at")
            turns[turn_id]["end_offset"] = end
    if session_meta is None:
        raise ValueError("Codex session metadata is missing")
    for turn in turns.values():
        parse_timestamp(turn["started_at"], "task_started.started_at")
        if turn["completed_at"] is not None:
            completed = parse_timestamp(turn["completed_at"], "task_complete.completed_at")
            if completed < parse_timestamp(turn["started_at"], "task_started.started_at"):
                raise ValueError("task completion predates task start")
    return session_meta, turns, order


def atomic_state_write(path, value):
    POLICY.atomic_private_json_write(path, value, "trace lifecycle state")


def validate_session_ledger(value, session_id):
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version", "platform", "session_id", "zone", "revision",
            "updated_at", "recorded_by",
        }
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("platform") != "codex"
        or value.get("session_id") != session_id
        or value.get("zone") not in ("work", "personal", "client")
        or isinstance(value.get("revision"), bool)
        or not isinstance(value.get("revision"), int)
        or value["revision"] < 0
        or value.get("recorded_by") != "codex:trace-session-hook"
    ):
        raise ValueError("Codex session zone ledger is invalid")
    parse_timestamp(value["updated_at"], "session zone updated_at")
    return value


def load_session_ledger(root, session_id, now=None):
    if not ID_RE.fullmatch(session_id or ""):
        raise ValueError("Codex session identity is invalid")
    root = POLICY.ensure_private_directory(root)
    path = root / (session_id + ".json")
    try:
        raw, _ = POLICY.read_regular_bytes(
            path, "Codex session zone ledger", owner_private=True,
        )
    except FileNotFoundError:
        instant = now or utc_now()
        return path, {
            "schema_version": 1, "platform": "codex", "session_id": session_id,
            "zone": "work", "revision": 0, "updated_at": iso_utc(instant),
            "recorded_by": "codex:trace-session-hook",
        }, False
    return path, validate_session_ledger(
        POLICY.strict_json_loads(raw, "Codex session zone ledger"), session_id,
    ), True


def write_session_ledger(path, value):
    validate_session_ledger(value, value.get("session_id"))
    atomic_state_write(path, value)


def recover_session_transactions(
    open_root, ledger_root, policy, policy_sha=None, hook_sha=None,
    current_policy_sha=None, current_hook_sha=None,
):
    transaction_root = POLICY.ensure_private_directory(Path(open_root) / ".transactions")
    POLICY.ensure_private_directory(ledger_root)
    for journal in sorted(transaction_root.iterdir()):
        if journal.name.startswith("."):
            continue
        raw, _ = POLICY.read_regular_bytes(
            journal, "Codex state-ledger transaction", owner_private=True,
        )
        transaction = POLICY._validate_transaction(
            POLICY.strict_json_loads(raw, "Codex state-ledger transaction")
        )
        session_id = transaction["ledger_after"].get("session_id")
        if (
            not ID_RE.fullmatch(str(session_id or ""))
            or journal.name != session_id + ".json"
            or transaction["ledger_name"] != session_id + ".json"
            or transaction["state_after"].get("session_id") != session_id
        ):
            raise ValueError("Codex state-ledger transaction identity is invalid")
        POLICY.recover_state_ledger_transaction(
            journal, Path(open_root), Path(ledger_root),
            validate_state=lambda item: validate_generation_state(
                item, policy, policy_sha,
                current_policy_sha or (policy_sha if hook_sha is not None else None),
                current_hook_sha or hook_sha,
            ),
            validate_ledger=lambda item: validate_session_ledger(
                item, session_id,
            ),
        )


def commit_session_transaction(
    open_root, ledger_root, state_path, state_before, state_after,
    ledger_path, ledger_before, ledger_after, policy, policy_sha, hook_sha,
    recovery_action="commit",
):
    session_id = ledger_after["session_id"]
    journal = POLICY.ensure_private_directory(
        Path(open_root) / ".transactions"
    ) / (session_id + ".json")
    POLICY.commit_state_ledger_transaction(
        journal, Path(open_root), Path(ledger_root),
        state_path, state_before, state_after,
        ledger_path, ledger_before, ledger_after,
        validate_state=lambda item: validate_generation_state(
            item, policy, item.get("policy_sha256"), policy_sha, hook_sha,
        ),
        validate_ledger=lambda item: validate_session_ledger(
            item, session_id,
        ),
        recovery_action=recovery_action,
    )


def state_name(session_id, turn_id):
    if not ID_RE.fullmatch(session_id) or not ID_RE.fullmatch(turn_id):
        raise ValueError("session or turn ID is invalid")
    return "%s-%s.json" % (session_id, turn_id)


def validate_open_state(
    state, policy, policy_sha, hook_sha, delegation_id=None,
):
    expected = {
        "schema_version", "status", "platform", "session_id", "turn_id",
        "trace_relative", "vault_id", "vault_path", "session_meta_sha256",
        "start_offset", "task_started_at", "initial_zone", "zone_transitions",
        "registered_at", "recorded_by", "delegated_by", "delegation_id",
        "policy_sha256", "session_hook_sha256", "closure_receipt_sha256",
    }
    if not isinstance(state, dict) or set(state) != expected:
        raise ValueError("trace lifecycle state schema is invalid")
    if (
        type(state.get("schema_version")) is not int
        or state.get("schema_version") != 1
        or state.get("platform") != "codex"
        or state.get("initial_zone") not in ("work", "personal", "client")
        or state.get("vault_id") != policy["vault"]["id"]
        or state.get("vault_path") != policy["vault"]["path"]
        or state.get("delegated_by") != POLICY.OWNER
        or state.get("delegation_id") != (
            delegation_id if delegation_id is not None else policy["delegation_id"]
        )
        or state.get("policy_sha256") != policy_sha
        or state.get("session_hook_sha256") != hook_sha
        or not re.fullmatch(r"[0-9a-f]{64}", str(policy_sha or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(hook_sha or ""))
        or state.get("recorded_by") != "codex:trace-session-hook"
        or state.get("status") not in ("open", "closed")
    ):
        raise ValueError("trace lifecycle state authority is invalid")
    if not ID_RE.fullmatch(str(state.get("session_id", ""))) or not ID_RE.fullmatch(str(state.get("turn_id", ""))):
        raise ValueError("trace lifecycle identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(state.get("session_meta_sha256", ""))):
        raise ValueError("trace lifecycle session metadata hash is invalid")
    closure = state.get("closure_receipt_sha256")
    if (
        (state["status"] == "open" and closure is not None)
        or (
            state["status"] == "closed" and closure is not None
            and not re.fullmatch(r"[0-9a-f]{64}", str(closure))
        )
    ):
        raise ValueError("trace lifecycle closure receipt is invalid")
    if isinstance(state.get("start_offset"), bool) or not isinstance(state.get("start_offset"), int) or state["start_offset"] < 0:
        raise ValueError("trace lifecycle start offset is invalid")
    if not isinstance(state.get("zone_transitions"), list):
        raise ValueError("trace lifecycle transitions are invalid")
    current_zone = state["initial_zone"]
    for transition in state["zone_transitions"]:
        if (
            not isinstance(transition, dict)
            or set(transition) != {"at", "from", "to", "recorded_by", "scope"}
            or transition.get("from") != current_zone
            or transition.get("to") not in ("work", "personal", "client")
            or transition.get("recorded_by") != "codex:trace-session-hook"
            or transition.get("scope") not in ("session", "turn")
        ):
            raise ValueError("trace lifecycle transitions are invalid")
        parse_timestamp(transition["at"], "zone transition")
        current_zone = transition["to"]
    parse_timestamp(state["task_started_at"], "task_started_at")
    parse_timestamp(state["registered_at"], "registered_at")
    return state


def lifecycle_authority(
    state, policy, current_policy_sha, current_hook_sha,
):
    matches = [
        item for item in POLICY.completion_authorities(
            policy, current_policy_sha, "codex", current_hook_sha,
        )
        if item["policy_sha256"] == state.get("policy_sha256")
    ]
    if len(matches) != 1:
        raise ValueError("trace lifecycle state authority is not pinned")
    authority = matches[0]
    if (
        state.get("session_hook_sha256") != authority["session_hook_sha256"]
        or state.get("delegation_id") != authority["delegation_id"]
        or parse_timestamp(state.get("registered_at"), "registered_at")
        < parse_timestamp(authority["not_before"], "not_before")
    ):
        raise ValueError("trace lifecycle state authority is not pinned")
    return authority


def validate_generation_state(
    state, policy, declared_policy_sha=None, current_policy_sha=None,
    current_hook_sha=None,
):
    policy_sha = state.get("policy_sha256") if isinstance(state, dict) else None
    hook_sha = state.get("session_hook_sha256") if isinstance(state, dict) else None
    authority = None
    if current_policy_sha is not None:
        authority = lifecycle_authority(
            state, policy, current_policy_sha, current_hook_sha,
        )
    validate_open_state(
        state, policy, policy_sha, hook_sha,
        delegation_id=authority["delegation_id"] if authority else None,
    )
    if declared_policy_sha is not None and policy_sha != declared_policy_sha:
        raise ValueError("trace lifecycle generation differs from state authority")
    return state


def load_generation_states(
    root, policy, declared_policy_sha=None, current_policy_sha=None,
    current_hook_sha=None,
):
    root = POLICY.validate_private_directory(root)
    states = []
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        if not path.name.endswith(".json"):
            raise ValueError("unexpected trace lifecycle artifact")
        raw, _ = POLICY.read_regular_bytes(
            path, "trace lifecycle state", owner_private=True,
        )
        state = POLICY.strict_json_loads(raw, "trace lifecycle state")
        validate_generation_state(
            state, policy, declared_policy_sha,
            current_policy_sha, current_hook_sha,
        )
        if path.name != state_name(state["session_id"], state["turn_id"]):
            raise ValueError("trace lifecycle filename differs from its identity")
        states.append((path, state))
    return states


def trace_path_from_state(state, policy):
    root = Path(policy["roots"]["trace_codex"])
    relative = Path(str(state.get("trace_relative", "")))
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".jsonl":
        raise ValueError("trace lifecycle path is invalid")
    candidate = Path(os.path.abspath(str(root / relative)))
    if candidate.resolve(strict=True) != candidate:
        raise ValueError("trace lifecycle path contains a symlink")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("trace lifecycle path leaves the Codex root") from error
    return candidate


def prepared_receipt_path(state_path):
    return Path(state_path).parent / ".pending" / Path(state_path).name


def load_prepared_receipt(state_path):
    try:
        raw, _ = POLICY.read_regular_bytes(
            prepared_receipt_path(state_path),
            "prepared Codex completion receipt", owner_private=True,
        )
    except FileNotFoundError:
        return None
    receipt = POLICY.strict_json_loads(raw, "prepared Codex completion receipt")
    if not isinstance(receipt, dict):
        raise ValueError("prepared Codex completion receipt is invalid")
    return receipt


def receipt_authority(
    receipt, policy, current_policy_sha, current_hook_sha,
):
    matches = [
        item for item in POLICY.completion_authorities(
            policy, current_policy_sha, "codex", current_hook_sha,
        )
        if item["policy_sha256"] == receipt.get("policy_sha256")
    ]
    if len(matches) != 1:
        raise ValueError("prepared Codex completion authority is not pinned")
    authority = matches[0]
    if (
        receipt.get("session_hook_sha256") != authority["session_hook_sha256"]
        or receipt.get("delegation_id") != authority["delegation_id"]
        or parse_timestamp(receipt.get("completed_at"), "completed_at")
        < parse_timestamp(authority["not_before"], "not_before")
    ):
        raise ValueError("prepared Codex completion authority is not pinned")
    return authority


def validate_closed_completion(
    state_path, state, policy, completion_root,
    current_policy_sha=None, current_hook_sha=None,
):
    """Prove that a closed Codex state names its exact immutable receipt."""
    digest = state.get("closure_receipt_sha256")
    if state.get("status") != "closed" or digest is None:
        return None
    prepared = load_prepared_receipt(state_path)
    if prepared is None:
        raise ValueError("closed Codex completion lacks its prepared receipt")
    validate_prepared_receipt(prepared, prepared, state)
    if current_policy_sha is not None:
        receipt_authority(
            prepared, policy, current_policy_sha, current_hook_sha,
        )
    raw = POLICY.canonical_json_bytes(prepared)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("closed Codex completion receipt drifted")
    target_root = POLICY.completion_generation_root(
        policy, completion_root, prepared, "codex",
    )
    published, _ = POLICY.read_regular_bytes(
        Path(target_root) / (digest + ".json"),
        "closed Codex completion receipt", owner_private=True,
    )
    if published != raw:
        raise ValueError("closed Codex completion artifact drifted")
    return prepared


def validate_prepared_receipt(receipt, candidate, state):
    expected_fields = {
        "schema_version", "receipt_type", "platform", "session_id", "turn_id",
        "trace_relative", "trace_identity", "segment_start", "segment_end",
        "segment_size", "segment_sha256", "session_meta_sha256",
        "task_started_at", "completed_at", "terminal_reason", "vault_id",
        "vault_path", "initial_zone", "zone_transitions", "eligible",
        "eligibility_reason", "delegated_by", "recorded_by", "delegation_id",
        "policy_sha256", "session_hook_sha256", "registration_policy_sha256",
        "registration_hook_sha256",
    }
    identity = receipt.get("trace_identity") if isinstance(receipt, dict) else None
    ranges = (
        receipt.get("segment_start"), receipt.get("segment_end"),
        receipt.get("segment_size"),
    ) if isinstance(receipt, dict) else (None, None, None)
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_fields
        or set(candidate) != expected_fields
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 2
        or receipt.get("receipt_type") != "codex-completed-turn-segment"
        or receipt.get("platform") != "codex"
        or receipt.get("session_id") != state["session_id"]
        or receipt.get("turn_id") != state["turn_id"]
        or receipt.get("trace_relative") != state["trace_relative"]
        or receipt.get("session_meta_sha256") != state["session_meta_sha256"]
        or receipt.get("segment_start") != state["start_offset"]
        or receipt.get("task_started_at") != state["task_started_at"]
        or receipt.get("vault_id") != state["vault_id"]
        or receipt.get("vault_path") != state["vault_path"]
        or receipt.get("initial_zone") != state["initial_zone"]
        or receipt.get("zone_transitions") != state["zone_transitions"]
        or receipt.get("terminal_reason") != "task_complete"
        or receipt.get("recorded_by") != "codex:trace-session-hook"
        or receipt.get("delegated_by") != POLICY.OWNER
        or receipt.get("registration_policy_sha256") != state["policy_sha256"]
        or receipt.get("registration_hook_sha256") != state["session_hook_sha256"]
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("policy_sha256", "")))
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("session_hook_sha256", "")))
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("segment_sha256", "")))
        or not isinstance(identity, dict) or set(identity) != {"device", "inode"}
        or any(type(identity[field]) is not int or identity[field] < 0 for field in identity)
        or any(type(item) is not int for item in ranges)
        or ranges[0] < 0 or ranges[1] <= ranges[0] or ranges[2] != ranges[1] - ranges[0]
        or type(receipt.get("eligible")) is not bool
        or not isinstance(receipt.get("delegation_id"), str)
        or not receipt["delegation_id"]
    ):
        raise ValueError("prepared Codex completion receipt is invalid")
    if parse_timestamp(
        receipt["completed_at"], "completed_at",
    ) < parse_timestamp(receipt["task_started_at"], "task_started_at"):
        raise ValueError("prepared Codex completion receipt time is invalid")
    expected = dict(candidate)
    for field in (
        "policy_sha256", "session_hook_sha256", "delegation_id",
        "eligible", "eligibility_reason",
    ):
        expected[field] = receipt[field]
    if state["zone_transitions"]:
        eligibility = (False, "zone-transition")
    elif state["initial_zone"] != "work":
        eligibility = (False, "initial-non-work-zone")
    else:
        eligibility = (receipt["eligible"], receipt["eligibility_reason"])
        if eligibility not in ((True, "eligible"), (False, "started-before-policy")):
            raise ValueError("prepared Codex completion eligibility is invalid")
    if (
        (receipt["eligible"], receipt["eligibility_reason"]) != eligibility
        or receipt != expected
    ):
        raise ValueError("prepared Codex completion receipt drifted")
    return receipt


def prepare_completion_receipt(
    state_path, candidate, state, policy, current_policy_sha,
    current_hook_sha,
):
    prepared = load_prepared_receipt(state_path)
    if prepared is not None:
        prepared = validate_prepared_receipt(prepared, candidate, state)
        receipt_authority(
            prepared, policy, current_policy_sha, current_hook_sha,
        )
        return prepared
    receipt_authority(
        candidate, policy, current_policy_sha, current_hook_sha,
    )
    POLICY.atomic_private_json_write(
        prepared_receipt_path(state_path), candidate,
        "prepared Codex completion receipt",
    )
    return candidate


def _close_state(path, state, policy, policy_sha, hook_sha, completion_root, now=None):
    validate_generation_state(
        state, policy, state.get("policy_sha256"), policy_sha, hook_sha,
    )
    if state["status"] == "closed":
        return state.get("closure_receipt_sha256")
    trace_path = trace_path_from_state(state, policy)
    raw, metadata = read_trace_bytes(trace_path)
    try:
        session_meta, turns, _ = structural_trace(raw, state["session_id"])
    except ValueError as error:
        raise TerminalEvidenceError("trace-structure-invalid") from error
    turn = turns.get(state["turn_id"])
    if turn is None or turn["start_offset"] != state["start_offset"]:
        raise TerminalEvidenceError("registered-task-mismatch")
    if turn["end_offset"] is None:
        return None
    if session_meta["sha256"] != state["session_meta_sha256"]:
        raise TerminalEvidenceError("session-metadata-drift")
    segment = raw[turn["start_offset"]:turn["end_offset"]]
    if not segment:
        raise TerminalEvidenceError("completed-segment-empty")
    completed = parse_timestamp(turn["completed_at"], "completed_at")
    current = now or utc_now()
    if completed > current + datetime.timedelta(minutes=5):
        raise ValueError("task completion is in the future")
    not_before = parse_timestamp(policy["not_before"], "not_before")
    transitions = state["zone_transitions"]
    eligible = (
        state["initial_zone"] == "work" and not transitions
        and parse_timestamp(turn["started_at"], "started_at") >= not_before
    )
    reason = "eligible" if eligible else (
        "zone-transition" if transitions else (
            "initial-non-work-zone" if state["initial_zone"] != "work"
            else "started-before-policy"
        )
    )
    candidate = {
        "schema_version": 2,
        "receipt_type": "codex-completed-turn-segment",
        "platform": "codex",
        "session_id": state["session_id"],
        "turn_id": state["turn_id"],
        "trace_relative": state["trace_relative"],
        "trace_identity": {"device": metadata.st_dev, "inode": metadata.st_ino},
        "segment_start": turn["start_offset"],
        "segment_end": turn["end_offset"],
        "segment_size": len(segment),
        "segment_sha256": hashlib.sha256(segment).hexdigest(),
        "session_meta_sha256": session_meta["sha256"],
        "task_started_at": iso_utc(parse_timestamp(turn["started_at"], "started_at")),
        "completed_at": iso_utc(completed),
        "terminal_reason": "task_complete",
        "vault_id": policy["vault"]["id"],
        "vault_path": policy["vault"]["path"],
        "initial_zone": state["initial_zone"],
        "zone_transitions": transitions,
        "eligible": eligible,
        "eligibility_reason": reason,
        "delegated_by": POLICY.OWNER,
        "recorded_by": "codex:trace-session-hook",
        "delegation_id": policy["delegation_id"],
        "policy_sha256": policy_sha,
        "session_hook_sha256": hook_sha,
        "registration_policy_sha256": state["policy_sha256"],
        "registration_hook_sha256": state["session_hook_sha256"],
    }
    receipt = prepare_completion_receipt(
        path, candidate, state, policy, policy_sha, hook_sha,
    )
    target_root = POLICY.completion_generation_root(
        policy, completion_root, receipt, "codex",
    )
    _, receipt_sha, _ = POLICY.publish_immutable(
        target_root, POLICY.canonical_json_bytes(receipt),
    )
    state = dict(state)
    state["status"] = "closed"
    state["closure_receipt_sha256"] = receipt_sha
    try:
        atomic_state_write(path, state)
    except (OSError, ValueError):
        observed_raw, _ = POLICY.read_regular_bytes(
            path, "trace lifecycle state", owner_private=True,
        )
        if POLICY.strict_json_loads(observed_raw, "trace lifecycle state") != state:
            raise
    return receipt_sha


def close_state(
    path, state, policy, policy_sha, hook_sha, completion_root, now=None,
    mutex_root=None,
):
    mutex = lock_lifecycle(mutex_root or Path(path).parent)
    try:
        raw, _ = POLICY.read_regular_bytes(
            path, "trace lifecycle state", owner_private=True,
        )
        current = POLICY.strict_json_loads(raw, "trace lifecycle state")
        return _close_state(
            path, current, policy, policy_sha, hook_sha, completion_root, now=now,
        )
    finally:
        unlock_lifecycle(mutex)


def load_states(root, policy, policy_sha, hook_sha):
    root = POLICY.ensure_private_directory(root)
    states = []
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        try:
            if not path.name.endswith(".json"):
                raise ValueError("unexpected trace lifecycle artifact")
            raw, _ = POLICY.read_regular_bytes(path, "trace lifecycle state", owner_private=True)
            state = POLICY.strict_json_loads(raw, "trace lifecycle state")
            validate_open_state(state, policy, policy_sha, hook_sha)
            if path.name != state_name(state["session_id"], state["turn_id"]):
                raise ValueError("trace lifecycle filename differs from its identity")
            states.append((path, state))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return states


def retire_state(path, state, policy_sha, hook_sha, reason):
    if not re.fullmatch(r"[a-z0-9-]+", reason):
        raise ValueError("trace lifecycle retirement reason is invalid")
    if load_prepared_receipt(path) is not None:
        raise ValueError("prepared Codex completion cannot be retired")
    recovery = {
        "schema_version": 1,
        "receipt_type": "codex-lifecycle-retirement",
        "platform": "codex",
        "session_id": state["session_id"],
        "turn_id": state["turn_id"],
        "trace_relative": state["trace_relative"],
        "reason_code": reason,
        "policy_sha256": policy_sha,
        "session_hook_sha256": hook_sha,
    }
    _, recovery_sha, _ = POLICY.publish_immutable(
        path.parent / ".recovery", POLICY.canonical_json_bytes(recovery),
    )
    if state["status"] == "open":
        updated = dict(state)
        updated["status"] = "closed"
        atomic_state_write(path, updated)
    return recovery_sha


def acknowledge_prepared_completion(path, state, completed_receipt):
    prepared = load_prepared_receipt(path)
    if prepared is None:
        return False
    validate_prepared_receipt(prepared, completed_receipt, state)
    desired = {
        **state, "status": "closed",
        "closure_receipt_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes(prepared)
        ).hexdigest(),
    }
    atomic_state_write(path, desired)
    return True


def _reconcile(
    policy, policy_sha, hook_sha, open_root, completion_root, now=None,
    records=None, require_terminal=False,
):
    closed = []
    retired = []
    records = records if records is not None else load_states(
        open_root, policy, policy_sha, hook_sha,
    )
    for path, state in records:
        try:
            receipt = _close_state(
                path, state, policy, policy_sha, hook_sha, completion_root, now=now,
            )
            if receipt is None and require_terminal:
                raise ValueError("prior policy generation task is still open")
            if receipt and state["status"] == "open":
                closed.append(receipt)
        except TerminalEvidenceError as error:
            retired.append(retire_state(
                path, state, policy_sha, hook_sha, str(error),
            ))
    return closed, retired


def reconcile(
    policy, policy_sha, hook_sha, open_root, completion_root, now=None,
    mutex_root=None, lock_token=None,
):
    ledger_root = Path(mutex_root or (Path(open_root) / ".sessions"))
    mutex = lock_lifecycle(ledger_root)
    try:
        if lock_token is not None:
            check_lock(lock_token)
        recover_session_transactions(
            open_root, ledger_root, policy, policy_sha, hook_sha,
        )
        result = _reconcile(
            policy, policy_sha, hook_sha, open_root, completion_root, now=now,
        )
        if lock_token is not None:
            check_lock(lock_token)
        return result
    finally:
        unlock_lifecycle(mutex)


def lifecycle_records(
    open_base, ledger_root, policy, current_policy_sha, current_hook_sha,
    session_id=None,
):
    records = []
    allowed_policy_shas = {
        item["policy_sha256"] for item in POLICY.completion_authorities(
            policy, current_policy_sha, "codex", current_hook_sha,
        )
    }
    for declared_policy_sha, root in POLICY.lifecycle_generation_roots(
        open_base, "codex", current_policy_sha=current_policy_sha,
        allowed_policy_shas=allowed_policy_shas,
    ):
        recover_session_transactions(
            root, ledger_root, policy, policy_sha=declared_policy_sha,
            current_policy_sha=current_policy_sha,
            current_hook_sha=current_hook_sha,
        )
        for path, state in load_generation_states(
            root, policy, declared_policy_sha,
            current_policy_sha, current_hook_sha,
        ):
            if session_id is None or state["session_id"] == session_id:
                records.append((declared_policy_sha, root, path, state))
    return records


def lifecycle_identity(state):
    return (
        state["session_id"], state["turn_id"], state["trace_relative"],
        state["session_meta_sha256"], state["start_offset"],
        state["task_started_at"], state["initial_zone"],
        POLICY.canonical_json_bytes(state["zone_transitions"]),
    )


def lifecycle_groups(
    records, policy=None, completion_root=None,
    current_policy_sha=None, current_hook_sha=None,
):
    groups = {}
    for record in records:
        state = record[3]
        groups.setdefault((state["session_id"], state["turn_id"]), []).append(record)
    result = []
    for group in groups.values():
        if len({lifecycle_identity(record[3]) for record in group}) != 1:
            raise ValueError("conflicting lifecycle state across policy generations")
        prepared = {}
        completed = {}
        for record in group:
            receipt = load_prepared_receipt(record[2])
            if receipt is not None:
                validate_prepared_receipt(receipt, receipt, record[3])
                if current_policy_sha is None:
                    raise ValueError("prepared Codex completion authority is unavailable")
                receipt_authority(
                    receipt, policy, current_policy_sha, current_hook_sha,
                )
                prepared[record[2]] = POLICY.canonical_json_bytes(receipt)
            if record[3]["status"] == "closed" and record[3]["closure_receipt_sha256"] is not None:
                if policy is None or completion_root is None:
                    raise ValueError("closed Codex completion authority is unavailable")
                receipt = validate_closed_completion(
                    record[2], record[3], policy, completion_root,
                    current_policy_sha, current_hook_sha,
                )
                completed[record[2]] = POLICY.canonical_json_bytes(receipt)
        if len(set(prepared.values())) > 1:
            raise ValueError("conflicting prepared receipt across policy generations")
        if len(set(completed.values())) > 1:
            raise ValueError("conflicting closed receipt across policy generations")
        if completed and prepared and set(completed.values()) != set(prepared.values()):
            raise ValueError("closed and prepared Codex receipts conflict")
        group.sort(key=lambda record: (
            0 if record[2] in completed else 1 if record[2] in prepared else 2,
            parse_timestamp(record[3]["registered_at"], "registered_at"),
            str(record[2]),
        ))
        result.append(group)
    return result


def reconcile_prior_generations(
    policy, policy_sha, hook_sha, open_base, current_root, completion_root,
    ledger_root, session_id, now=None, lock_token=None,
):
    mutex = lock_lifecycle(ledger_root)
    try:
        if lock_token is not None:
            check_lock(lock_token)
        records = lifecycle_records(
            open_base, ledger_root, policy, policy_sha, hook_sha,
            session_id=session_id,
        )
        records = [
            record for record in records
            if record[3]["status"] == "open"
            or record[3]["closure_receipt_sha256"] is not None
        ]
        canonical = []
        retired = []
        existing = []
        for group in lifecycle_groups(
            records, policy, completion_root, policy_sha, hook_sha,
        ):
            active = [record for record in group if record[3]["status"] == "open"]
            if not active:
                continue
            if not any(record[1] != Path(current_root) for record in group):
                continue
            completed = [
                record for record in group
                if record[3]["status"] == "closed"
                and record[3]["closure_receipt_sha256"] is not None
            ]
            if completed:
                existing.append(completed[0][3]["closure_receipt_sha256"])
                completed_receipt = validate_closed_completion(
                    completed[0][2], completed[0][3], policy,
                    completion_root, policy_sha, hook_sha,
                )
                for _, _, path, state in active:
                    if not acknowledge_prepared_completion(
                        path, state, completed_receipt,
                    ):
                        retired.append(retire_state(
                            path, state, policy_sha, hook_sha,
                            "duplicate-completed-lifecycle",
                        ))
                continue
            canonical_record = next(record for record in group if record in active)
            canonical.append((canonical_record[2], canonical_record[3]))
            for _, _, path, state in active:
                if path == canonical_record[2]:
                    continue
                retired.append(retire_state(
                    path, state, policy_sha, hook_sha,
                    "duplicate-lifecycle-state",
                ))
        closed, semantic_retired = _reconcile(
            policy, policy_sha, hook_sha, current_root, completion_root,
            now=now, records=canonical, require_terminal=True,
        )
        if lock_token is not None:
            check_lock(lock_token)
        return existing + closed, retired + semantic_retired
    finally:
        unlock_lifecycle(mutex)


def _register_current(
    policy, policy_sha, hook_sha, open_root, trace_path, session_id,
    requested_zone, ledger_root, completion_root=None, now=None,
):
    raw, _ = read_trace_bytes(trace_path)
    session_meta, turns, order = structural_trace(raw, session_id)
    instant = now or utc_now()
    ledger_path, ledger, ledger_exists = load_session_ledger(
        ledger_root, session_id, now=instant,
    )
    if requested_zone != ledger["zone"]:
        raise ValueError("requested Codex zone differs from the durable session zone")
    open_turns = [turns[turn_id] for turn_id in order if turns[turn_id]["end_offset"] is None]
    if not open_turns:
        raise ValueError("current Codex task is not open")
    turn = open_turns[-1]
    for previous_path, previous in load_states(open_root, policy, policy_sha, hook_sha):
        if (
            previous["session_id"] == session_id
            and previous["status"] == "open"
            and previous["turn_id"] != turn["turn_id"]
        ):
            if completion_root is None:
                raise ValueError("prior Codex task requires terminal reconciliation")
            receipt = _close_state(
                previous_path, previous, policy, policy_sha, hook_sha,
                completion_root, now=instant,
            )
            if receipt is None:
                raise ValueError("prior Codex task is still open")
    root = Path(policy["roots"]["trace_codex"])
    trace_relative = trace_path.relative_to(root).as_posix()
    path = Path(open_root) / state_name(session_id, turn["turn_id"])
    if path.exists():
        raw_state, _ = POLICY.read_regular_bytes(path, "trace lifecycle state", owner_private=True)
        state = POLICY.strict_json_loads(raw_state, "trace lifecycle state")
        validate_open_state(state, policy, policy_sha, hook_sha)
        if state["status"] != "open" or state["start_offset"] != turn["start_offset"]:
            raise ValueError("current task registration conflicts with existing state")
        if not ledger_exists:
            write_session_ledger(ledger_path, ledger)
        return path, False
    state = {
        "schema_version": 1,
        "status": "open",
        "platform": "codex",
        "session_id": session_id,
        "turn_id": turn["turn_id"],
        "trace_relative": trace_relative,
        "vault_id": policy["vault"]["id"],
        "vault_path": policy["vault"]["path"],
        "session_meta_sha256": session_meta["sha256"],
        "start_offset": turn["start_offset"],
        "task_started_at": iso_utc(parse_timestamp(turn["started_at"], "started_at")),
        "initial_zone": ledger["zone"],
        "zone_transitions": [],
        "registered_at": iso_utc(instant),
        "recorded_by": "codex:trace-session-hook",
        "delegated_by": POLICY.OWNER,
        "delegation_id": policy["delegation_id"],
        "policy_sha256": policy_sha,
        "session_hook_sha256": hook_sha,
        "closure_receipt_sha256": None,
    }
    commit_session_transaction(
        open_root, ledger_root, path, None, state,
        ledger_path, ledger if ledger_exists else None, ledger,
        policy, policy_sha, hook_sha,
    )
    return path, True


def register_current(
    policy, policy_sha, hook_sha, open_root, trace_path, session_id,
    requested_zone="work", ledger_root=None, completion_root=None, now=None,
    lock_token=None,
):
    POLICY.ensure_private_directory(open_root)
    ledger_root = Path(ledger_root or (Path(open_root) / ".sessions"))
    mutex = lock_lifecycle(ledger_root)
    try:
        if lock_token is not None:
            check_lock(lock_token)
        recover_session_transactions(
            open_root, ledger_root, policy, policy_sha, hook_sha,
        )
        result = _register_current(
            policy, policy_sha, hook_sha, open_root, trace_path, session_id,
            requested_zone, ledger_root, completion_root=completion_root, now=now,
        )
        if lock_token is not None:
            check_lock(lock_token)
        return result
    finally:
        unlock_lifecycle(mutex)


def _switch_state(
    policy, policy_sha, hook_sha, open_root, path, state, session_id,
    new_zone, ledger_root, scope="session", now=None,
):
    if load_prepared_receipt(path) is not None:
        raise ValueError("Codex task completion is already prepared")
    ledger_path, ledger, ledger_exists = load_session_ledger(
        ledger_root, session_id, now=now,
    )
    session_zone = state["initial_zone"]
    for transition in state["zone_transitions"]:
        if transition["scope"] == "session":
            session_zone = transition["to"]
    if not ledger_exists or session_zone != ledger["zone"]:
        raise ValueError("Codex session zone ledger differs from the open turn")
    current_zone = (
        state["zone_transitions"][-1]["to"]
        if state["zone_transitions"] else state["initial_zone"]
    )
    if new_zone == current_zone:
        if scope != "session" or ledger["zone"] == new_zone:
            return path, False
        if not state["zone_transitions"]:
            raise ValueError("Codex session route cannot be promoted")
        prior_state = state
        state = dict(state)
        state["zone_transitions"] = list(state["zone_transitions"])
        state["zone_transitions"][-1] = {
            **state["zone_transitions"][-1],
            "at": iso_utc(now or utc_now()),
            "scope": "session",
        }
    else:
        prior_state = state
        state = dict(state)
        state["zone_transitions"] = list(state["zone_transitions"]) + [{
            "at": iso_utc(now or utc_now()), "from": current_zone, "to": new_zone,
            "recorded_by": "codex:trace-session-hook", "scope": scope,
        }]
    if scope == "session":
        updated_ledger = dict(ledger)
        if updated_ledger["zone"] != new_zone:
            updated_ledger["zone"] = new_zone
            updated_ledger["revision"] += 1
            updated_ledger["updated_at"] = state["zone_transitions"][-1]["at"]
        commit_session_transaction(
            open_root, ledger_root, path, prior_state, state,
            ledger_path, ledger, updated_ledger,
            policy, policy_sha, hook_sha, recovery_action="rollback",
        )
    else:
        atomic_state_write(path, state)
    return path, True


def _switch_current(
    policy, policy_sha, hook_sha, open_root, session_id, new_zone,
    ledger_root, scope="session", now=None,
):
    candidates = [
        (path, state) for path, state in load_states(open_root, policy, policy_sha, hook_sha)
        if state["session_id"] == session_id and state["status"] == "open"
    ]
    if len(candidates) != 1:
        raise ValueError("current work task lifecycle is not uniquely open")
    path, state = candidates[0]
    return _switch_state(
        policy, policy_sha, hook_sha, open_root, path, state, session_id,
        new_zone, ledger_root, scope=scope, now=now,
    )


def switch_current(
    policy, policy_sha, hook_sha, open_root, session_id, new_zone,
    ledger_root=None, scope="session", now=None, lock_token=None,
):
    if scope not in ("session", "turn"):
        raise ValueError("Codex zone transition scope is invalid")
    POLICY.ensure_private_directory(open_root)
    ledger_root = Path(ledger_root or (Path(open_root) / ".sessions"))
    mutex = lock_lifecycle(ledger_root)
    try:
        if lock_token is not None:
            check_lock(lock_token)
        recover_session_transactions(
            open_root, ledger_root, policy, policy_sha, hook_sha,
        )
        result = _switch_current(
            policy, policy_sha, hook_sha, open_root, session_id, new_zone,
            ledger_root, scope=scope, now=now,
        )
        if lock_token is not None:
            check_lock(lock_token)
        return result
    finally:
        unlock_lifecycle(mutex)


def switch_across_generations(
    policy, policy_sha, hook_sha, open_base, session_id, new_zone,
    ledger_root, scope="session", now=None, lock_token=None,
    completion_root=None,
):
    if scope not in ("session", "turn"):
        raise ValueError("Codex zone transition scope is invalid")
    mutex = lock_lifecycle(ledger_root)
    try:
        if lock_token is not None:
            check_lock(lock_token)
        records = lifecycle_records(
            open_base, ledger_root, policy, policy_sha, hook_sha,
            session_id=session_id,
        )
        active_keys = {
            (record[3]["session_id"], record[3]["turn_id"])
            for record in records if record[3]["status"] == "open"
        }
        records = [
            record for record in records
            if (record[3]["session_id"], record[3]["turn_id"]) in active_keys
            and (
                record[3]["status"] == "open"
                or record[3]["closure_receipt_sha256"] is not None
            )
        ]
        groups = [
            group for group in lifecycle_groups(
                records, policy if completion_root is not None else None,
                completion_root, policy_sha, hook_sha,
            )
            if any(record[3]["status"] == "open" for record in group)
        ]
        if len(groups) != 1:
            raise ValueError("current work task lifecycle is not uniquely open")
        group = groups[0]
        completed = [
            record for record in group
            if record[3]["status"] == "closed"
            and record[3]["closure_receipt_sha256"] is not None
        ]
        active = [record for record in group if record[3]["status"] == "open"]
        if completed:
            completed_receipt = validate_closed_completion(
                completed[0][2], completed[0][3], policy,
                completion_root, policy_sha, hook_sha,
            )
            for _, _, duplicate_path, duplicate_state in active:
                if not acknowledge_prepared_completion(
                    duplicate_path, duplicate_state, completed_receipt,
                ):
                    retire_state(
                        duplicate_path, duplicate_state, policy_sha, hook_sha,
                        "duplicate-completed-lifecycle",
                    )
            raise ValueError("Codex task is already completed")
        _, open_root, path, state = active[0]
        retired = [
            retire_state(
                duplicate_path, duplicate_state, policy_sha, hook_sha,
                "duplicate-lifecycle-state",
            )
            for _, _, duplicate_path, duplicate_state in active[1:]
        ]
        result_path, changed = _switch_state(
            policy, policy_sha, hook_sha, open_root, path, state, session_id,
            new_zone, ledger_root, scope=scope, now=now,
        )
        if lock_token is not None:
            check_lock(lock_token)
        return result_path, changed, retired
    finally:
        unlock_lifecycle(mutex)


def self_test():
    with tempfile.TemporaryDirectory(prefix="trace-session-test-") as directory:
        root = Path(os.path.realpath(directory))
        trace_root = root / "sessions" / "2026" / "08" / "21"
        trace_root.mkdir(parents=True)
        session_id = "11111111-1111-1111-1111-111111111111"
        turn_id = "22222222-2222-2222-2222-222222222222"
        trace = trace_root / ("rollout-" + session_id + ".jsonl")
        started = "2026-08-21T12:00:00Z"
        trace.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": session_id, "cwd": str(VAULT)}}) + "\n"
            + json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id, "started_at": started}}) + "\n"
            + json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "не так"}}) + "\n",
            encoding="utf-8",
        )
        policy = {
            "vault": {"id": "{{VAULT_ID}}", "path": str(VAULT)},
            "roots": {"trace_codex": str(root / "sessions")},
            "delegation_id": "test-delegation",
            "not_before": "2026-08-21T00:00:00Z",
        }
        policy_sha, hook_sha = "a" * 64, code_sha256()
        open_root, completion_root = root / "open", root / "complete"
        ledger_root = root / "session-ledger"
        path, created = register_current(
            policy, policy_sha, hook_sha, open_root, trace, session_id,
            ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:01:00Z", "now"),
        )
        assert created
        _, duplicate = register_current(
            policy, policy_sha, hook_sha, open_root, trace, session_id,
            ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:01:01Z", "now"),
        )
        assert not duplicate
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "event_msg", "payload": {
                    "type": "task_complete", "turn_id": turn_id,
                    "completed_at": "2026-08-21T12:02:00Z",
                },
            }) + "\n")
        raw_state, _ = POLICY.read_regular_bytes(path, "state", owner_private=True)
        state = POLICY.strict_json_loads(raw_state, "state")
        receipt_sha = close_state(
            path, state, policy, policy_sha, hook_sha, completion_root,
            mutex_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:03:00Z", "now"),
        )
        assert re.fullmatch(r"[0-9a-f]{64}", receipt_sha)
        receipt_raw, _ = POLICY.read_regular_bytes(
            completion_root / (receipt_sha + ".json"), "receipt", owner_private=True,
        )
        receipt = POLICY.strict_json_loads(receipt_raw, "receipt")
        assert receipt["eligible"] and receipt["terminal_reason"] == "task_complete"
        assert receipt["segment_end"] <= trace.stat().st_size
        second_turn = "33333333-3333-3333-3333-333333333333"
        third_turn = "44444444-4444-4444-4444-444444444444"
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": second_turn,
                "started_at": "2026-08-21T12:04:00Z",
            }}) + "\n")
        second_path, _ = register_current(
            policy, policy_sha, hook_sha, open_root, trace, session_id,
            ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:04:01Z", "now"),
        )
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": second_turn,
                "completed_at": "2026-08-21T12:04:50Z",
            }}) + "\n")
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": third_turn,
                "started_at": "2026-08-21T12:05:00Z",
            }}) + "\n")
        third_path, third_created = register_current(
            policy, policy_sha, hook_sha, open_root, trace, session_id,
            ledger_root=ledger_root, completion_root=completion_root,
            now=parse_timestamp("2026-08-21T12:05:01Z", "now"),
        )
        second_raw, _ = POLICY.read_regular_bytes(second_path, "state", owner_private=True)
        second_state = POLICY.strict_json_loads(second_raw, "state")
        assert third_created and third_path != second_path
        assert second_state["status"] == "closed" and second_state["closure_receipt_sha256"]
        _, switched = switch_current(
            policy, policy_sha, hook_sha, open_root, session_id, "personal",
            ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:05:10Z", "now"),
        )
        assert switched
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": third_turn,
                "completed_at": "2026-08-21T12:05:30Z",
            }}) + "\n")
        third_raw, _ = POLICY.read_regular_bytes(
            third_path, "state", owner_private=True,
        )
        third_state = POLICY.strict_json_loads(third_raw, "state")
        third_receipt_sha = close_state(
            third_path, third_state, policy, policy_sha, hook_sha,
            completion_root, mutex_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:05:40Z", "now"),
        )
        third_receipt_raw, _ = POLICY.read_regular_bytes(
            completion_root / (third_receipt_sha + ".json"),
            "receipt", owner_private=True,
        )
        assert not POLICY.strict_json_loads(third_receipt_raw, "receipt")["eligible"]

        fourth_turn = "77777777-7777-7777-7777-777777777777"
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": fourth_turn,
                "started_at": "2026-08-21T12:06:00Z",
            }}) + "\n")
        fourth_path, _ = register_current(
            policy, policy_sha, hook_sha, open_root, trace, session_id,
            requested_zone="personal", ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:06:01Z", "now"),
        )
        fourth_raw, _ = POLICY.read_regular_bytes(
            fourth_path, "state", owner_private=True,
        )
        assert POLICY.strict_json_loads(fourth_raw, "state")["initial_zone"] == "personal"
        _, returned = switch_current(
            policy, policy_sha, hook_sha, open_root, session_id, "work",
            ledger_root=ledger_root, scope="session",
            now=parse_timestamp("2026-08-21T12:06:10Z", "now"),
        )
        assert returned
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": fourth_turn,
                "completed_at": "2026-08-21T12:06:20Z",
            }}) + "\n")
        fourth_receipt = close_state(
            fourth_path, {}, policy, policy_sha, hook_sha, completion_root,
            mutex_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:06:30Z", "now"),
        )
        fourth_receipt_raw, _ = POLICY.read_regular_bytes(
            completion_root / (fourth_receipt + ".json"), "receipt", owner_private=True,
        )
        assert not POLICY.strict_json_loads(fourth_receipt_raw, "receipt")["eligible"]

        fifth_turn = "88888888-8888-8888-8888-888888888888"
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": fifth_turn,
                "started_at": "2026-08-21T12:07:00Z",
            }}) + "\n")
        fifth_path, _ = register_current(
            policy, policy_sha, hook_sha, open_root, trace, session_id,
            requested_zone="work", ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:07:01Z", "now"),
        )
        _, one_off = switch_current(
            policy, policy_sha, hook_sha, open_root, session_id, "personal",
            ledger_root=ledger_root, scope="turn",
            now=parse_timestamp("2026-08-21T12:07:10Z", "now"),
        )
        assert one_off
        ledger_path, ledger, exists = load_session_ledger(ledger_root, session_id)
        assert exists and ledger_path.exists() and ledger["zone"] == "work"

        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": fifth_turn,
                "completed_at": "2026-08-21T12:07:20Z",
            }}) + "\n")
        original_publish = POLICY.publish_immutable
        injected_publish = {"value": False}

        def fail_completion_publish_once(destination, raw):
            if Path(destination) == completion_root and not injected_publish["value"]:
                injected_publish["value"] = True
                raise OSError("injected-codex-completion-publish")
            return original_publish(destination, raw)

        POLICY.publish_immutable = fail_completion_publish_once
        try:
            try:
                reconcile(
                    policy, policy_sha, hook_sha, open_root, completion_root,
                    mutex_root=ledger_root,
                    now=parse_timestamp("2026-08-21T12:07:30Z", "now"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("Codex completion publication failure was retired")
        finally:
            POLICY.publish_immutable = original_publish
        fifth_raw, _ = POLICY.read_regular_bytes(
            fifth_path, "fifth state", owner_private=True,
        )
        assert POLICY.strict_json_loads(fifth_raw, "fifth state")["status"] == "open"
        closed_after_fault, retired_after_fault = reconcile(
            policy, policy_sha, hook_sha, open_root, completion_root,
            mutex_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:07:31Z", "now"),
        )
        assert len(closed_after_fault) == 1 and not retired_after_fault
        fifth_raw, _ = POLICY.read_regular_bytes(
            fifth_path, "fifth closed state", owner_private=True,
        )
        fifth_closed = POLICY.strict_json_loads(fifth_raw, "fifth closed state")
        assert fifth_closed["status"] == "closed" and fifth_closed["closure_receipt_sha256"]

        mixed_session = "18181818-1818-1818-1818-181818181818"
        mixed_turn_one = "19191919-1919-1919-1919-191919191919"
        mixed_trace_root = root / "mixed-sessions" / "2026" / "08" / "21"
        mixed_trace_root.mkdir(parents=True)
        mixed_trace = mixed_trace_root / ("rollout-" + mixed_session + ".jsonl")
        mixed_trace.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "id": mixed_session, "cwd": str(VAULT),
            }}) + "\n" + json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": mixed_turn_one,
                "started_at": "2026-08-21T12:07:32Z",
            }}) + "\n",
            encoding="utf-8",
        )
        mixed_policy = {
            **policy, "roots": {"trace_codex": str(root / "mixed-sessions")},
        }
        mixed_open = root / "mixed-open"
        mixed_ledger = root / "mixed-ledger"
        mixed_complete = root / "mixed-complete"
        mixed_one_path, _ = register_current(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_trace,
            mixed_session, ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:07:33Z", "now"),
        )
        switch_current(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_session,
            "personal", ledger_root=mixed_ledger, scope="turn",
            now=parse_timestamp("2026-08-21T12:07:34Z", "now"),
        )
        _, promoted = switch_current(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_session,
            "personal", ledger_root=mixed_ledger, scope="session",
            now=parse_timestamp("2026-08-21T12:07:35Z", "now"),
        )
        _, mixed_ledger_value, _ = load_session_ledger(
            mixed_ledger, mixed_session,
        )
        assert promoted and mixed_ledger_value["zone"] == "personal"
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": mixed_turn_one,
                "completed_at": "2026-08-21T12:07:36Z",
            }}) + "\n")
        reconcile(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_complete,
            mutex_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:07:37Z", "now"),
        )
        mixed_turn_two = "20202020-2020-2020-2020-202020202020"
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": mixed_turn_two,
                "started_at": "2026-08-21T12:07:38Z",
            }}) + "\n")
        mixed_two_path, _ = register_current(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_trace,
            mixed_session, requested_zone="personal", ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:07:39Z", "now"),
        )
        mixed_two_raw, _ = POLICY.read_regular_bytes(
            mixed_two_path, "mixed-scope next state", owner_private=True,
        )
        assert POLICY.strict_json_loads(
            mixed_two_raw, "mixed-scope next state",
        )["initial_zone"] == "personal"
        switch_current(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_session,
            "work", ledger_root=mixed_ledger, scope="turn",
            now=parse_timestamp("2026-08-21T12:07:40Z", "now"),
        )
        _, reverse_promoted = switch_current(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_session,
            "work", ledger_root=mixed_ledger, scope="session",
            now=parse_timestamp("2026-08-21T12:07:41Z", "now"),
        )
        _, mixed_ledger_value, _ = load_session_ledger(
            mixed_ledger, mixed_session,
        )
        assert reverse_promoted and mixed_ledger_value["zone"] == "work"
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": mixed_turn_two,
                "completed_at": "2026-08-21T12:07:42Z",
            }}) + "\n")
        reconcile(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_complete,
            mutex_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:07:43Z", "now"),
        )
        mixed_turn_three = "21212121-2121-2121-2121-212121212121"
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": mixed_turn_three,
                "started_at": "2026-08-21T12:07:44Z",
            }}) + "\n")
        mixed_three_path, _ = register_current(
            mixed_policy, policy_sha, hook_sha, mixed_open, mixed_trace,
            mixed_session, requested_zone="work", ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:07:45Z", "now"),
        )
        mixed_three_raw, _ = POLICY.read_regular_bytes(
            mixed_three_path, "mixed-scope reverse state", owner_private=True,
        )
        mixed_three_state = POLICY.strict_json_loads(
            mixed_three_raw, "mixed-scope reverse state",
        )
        assert mixed_three_state["initial_zone"] == "work"
        assert not mixed_three_state["zone_transitions"]

        fault_session = "99999999-9999-9999-9999-999999999999"
        fault_turn = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        fault_trace_root = root / "fault-sessions" / "2026" / "08" / "21"
        fault_trace_root.mkdir(parents=True)
        fault_trace = fault_trace_root / ("rollout-" + fault_session + ".jsonl")
        fault_trace.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "id": fault_session, "cwd": str(VAULT),
            }}) + "\n" + json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": fault_turn,
                "started_at": "2026-08-21T12:08:00Z",
            }}) + "\n",
            encoding="utf-8",
        )
        fault_policy = {
            **policy, "roots": {"trace_codex": str(root / "fault-sessions")},
        }
        fault_open, fault_ledger = root / "fault-open", root / "fault-ledger"
        fault_path, _ = register_current(
            fault_policy, policy_sha, hook_sha, fault_open, fault_trace,
            fault_session, ledger_root=fault_ledger,
            now=parse_timestamp("2026-08-21T12:08:01Z", "now"),
        )
        original_private_write = POLICY.atomic_private_json_write
        injected = {"ledger": False}

        def fail_fault_ledger_once(path, value, label="private JSON artifact"):
            if Path(path).parent == fault_ledger and not injected["ledger"]:
                injected["ledger"] = True
                raise OSError("injected-codex-ledger")
            return original_private_write(path, value, label)

        POLICY.atomic_private_json_write = fail_fault_ledger_once
        try:
            try:
                switch_current(
                    fault_policy, policy_sha, hook_sha, fault_open,
                    fault_session, "personal", ledger_root=fault_ledger,
                    now=parse_timestamp("2026-08-21T12:08:02Z", "now"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("injected Codex ledger failure was not observed")
        finally:
            POLICY.atomic_private_json_write = original_private_write
        _, fault_retry = switch_current(
            fault_policy, policy_sha, hook_sha, fault_open,
            fault_session, "personal", ledger_root=fault_ledger,
            now=parse_timestamp("2026-08-21T12:08:03Z", "now"),
        )
        _, fault_ledger_value, _ = load_session_ledger(
            fault_ledger, fault_session,
        )
        assert fault_retry and fault_ledger_value["zone"] == "personal"
        assert not list((fault_open / ".transactions").glob("*.json"))

        injected = {"ledger": False}
        POLICY.atomic_private_json_write = fail_fault_ledger_once
        try:
            try:
                switch_current(
                    fault_policy, policy_sha, hook_sha, fault_open,
                    fault_session, "work", ledger_root=fault_ledger,
                    now=parse_timestamp("2026-08-21T12:08:04Z", "now"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("injected reverse Codex switch failure was not observed")
        finally:
            POLICY.atomic_private_json_write = original_private_write
        next_turn = "55555555-5555-5555-5555-555555555555"
        with fault_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": fault_turn,
                "completed_at": "2026-08-21T12:08:05Z",
            }}) + "\n")
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": next_turn,
                "started_at": "2026-08-21T12:08:06Z",
            }}) + "\n")
        reconcile(
            fault_policy, policy_sha, hook_sha, fault_open,
            root / "fault-complete", mutex_root=fault_ledger,
            now=parse_timestamp("2026-08-21T12:08:07Z", "now"),
        )
        next_path, next_created = register_current(
            fault_policy, policy_sha, hook_sha, fault_open, fault_trace,
            fault_session, requested_zone="personal", ledger_root=fault_ledger,
            now=parse_timestamp("2026-08-21T12:08:08Z", "now"),
        )
        next_raw, _ = POLICY.read_regular_bytes(
            next_path, "next Codex state", owner_private=True,
        )
        next_state = POLICY.strict_json_loads(next_raw, "next Codex state")
        _, fault_ledger_value, _ = load_session_ledger(fault_ledger, fault_session)
        assert next_created and next_state["initial_zone"] == "personal"
        assert not next_state["zone_transitions"] and fault_ledger_value["zone"] == "personal"
        assert not list((fault_open / ".transactions").glob("*.json"))

        fault_before, _ = POLICY.read_regular_bytes(
            fault_path, "fault state", owner_private=True,
        )
        original_check_lock = globals()["check_lock"]
        globals()["check_lock"] = lambda token: (_ for _ in ()).throw(
            ValueError("work writer lock is not owned")
        )
        try:
            try:
                switch_current(
                    fault_policy, policy_sha, hook_sha, fault_open,
                    fault_session, "work", ledger_root=fault_ledger,
                    lock_token="f" * 64,
                )
            except ValueError as error:
                assert str(error) == "work writer lock is not owned"
            else:
                raise AssertionError("expired writer lock was accepted")
        finally:
            globals()["check_lock"] = original_check_lock
        fault_after, _ = POLICY.read_regular_bytes(
            fault_path, "fault state", owner_private=True,
        )
        assert fault_after == fault_before

        rollover_session = "12121212-1212-1212-1212-121212121212"
        rollover_turn = "13131313-1313-1313-1313-131313131313"
        rollover_trace_root = root / "rollover-sessions" / "2026" / "08" / "21"
        rollover_trace_root.mkdir(parents=True)
        rollover_trace = rollover_trace_root / (
            "rollout-" + rollover_session + ".jsonl"
        )
        rollover_trace.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "id": rollover_session, "cwd": str(VAULT),
            }}) + "\n" + json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": rollover_turn,
                "started_at": "2026-08-21T12:09:00Z",
            }}) + "\n",
            encoding="utf-8",
        )
        rollover_policy = {
            **policy, "roots": {"trace_codex": str(root / "rollover-sessions")},
            "completion_predecessors": [
                {
                    "policy_sha256": "a" * 64,
                    "delegation_id": "old-delegation",
                    "not_before": "2026-08-21T00:00:00Z",
                    "claude_hook_sha256": None,
                    "codex_session_hook_sha256": "1" * 64,
                    "processed_sha256": None,
                },
                {
                    "policy_sha256": "c" * 64,
                    "delegation_id": "test-delegation",
                    "not_before": "2026-08-21T00:00:00Z",
                    "claude_hook_sha256": None,
                    "codex_session_hook_sha256": "3" * 64,
                    "processed_sha256": None,
                },
            ],
        }
        rollover_a_policy = {
            **policy,
            "roots": {"trace_codex": str(root / "rollover-sessions")},
            "delegation_id": "old-delegation",
        }
        rollover_c_policy = {
            **policy,
            "roots": {"trace_codex": str(root / "rollover-sessions")},
        }
        rollover_base = root / "rollover-open"
        rollover_ledger = root / "rollover-ledger"
        rollover_complete = root / "rollover-complete"
        POLICY.ensure_private_directory(rollover_base)
        rollover_a = POLICY.generation_root(rollover_base, "a" * 64, "codex")
        rollover_b = POLICY.generation_root(rollover_base, "b" * 64, "codex")
        rollover_c = POLICY.generation_root(rollover_base, "c" * 64, "codex")
        rollover_a_path, _ = register_current(
            rollover_a_policy, "a" * 64, "1" * 64, rollover_a,
            rollover_trace, rollover_session, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:09:01Z", "now"),
        )
        rollover_c_path, _ = register_current(
            rollover_c_policy, "c" * 64, "3" * 64, rollover_c,
            rollover_trace, rollover_session, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:09:02Z", "now"),
        )
        rollover_switch_path, rollover_switched, rollover_switch_retired = (
            switch_across_generations(
                rollover_policy, "b" * 64, "2" * 64, rollover_base,
                rollover_session, "personal", rollover_ledger,
                now=parse_timestamp("2026-08-21T12:09:03Z", "now"),
            )
        )
        assert rollover_switch_path == rollover_a_path
        assert rollover_switched and len(rollover_switch_retired) == 1
        with rollover_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": rollover_turn,
                "completed_at": "2026-08-21T12:09:10Z",
            }}) + "\n")
        rollover_closed, rollover_retired = reconcile_prior_generations(
            rollover_policy, "b" * 64, "2" * 64, rollover_base,
            rollover_b, rollover_complete, rollover_ledger,
            rollover_session,
            now=parse_timestamp("2026-08-21T12:09:20Z", "now"),
        )
        assert len(rollover_closed) == 1 and not rollover_retired
        assert len(list(rollover_complete.glob("*.json"))) == 1
        rollover_receipt_raw, _ = POLICY.read_regular_bytes(
            rollover_complete / (rollover_closed[0] + ".json"),
            "rollover completion", owner_private=True,
        )
        rollover_receipt = POLICY.strict_json_loads(
            rollover_receipt_raw, "rollover completion",
        )
        assert rollover_receipt["policy_sha256"] == "b" * 64
        assert rollover_receipt["registration_policy_sha256"] == "a" * 64
        assert not rollover_receipt["eligible"]
        assert rollover_receipt["eligibility_reason"] == "zone-transition"
        rollover_a_raw, _ = POLICY.read_regular_bytes(
            rollover_a_path, "rollover A state", owner_private=True,
        )
        rollover_c_raw, _ = POLICY.read_regular_bytes(
            rollover_c_path, "rollover C state", owner_private=True,
        )
        assert POLICY.strict_json_loads(rollover_a_raw, "rollover A state")["status"] == "closed"
        assert POLICY.strict_json_loads(rollover_c_raw, "rollover C state")["status"] == "closed"

        untrusted_root = POLICY.generation_root(
            rollover_base, "d" * 64, "codex",
        )
        try:
            lifecycle_records(
                rollover_base, rollover_ledger, rollover_policy,
                "b" * 64, "2" * 64,
            )
        except ValueError as error:
            assert str(error) == "untrusted reflection lifecycle generation"
        else:
            raise AssertionError("unpinned Codex lifecycle generation was accepted")
        untrusted_root.rmdir()
        untrusted_root.parent.rmdir()

        crash_session = "23232323-2323-2323-2323-232323232323"
        crash_turn = "24242424-2424-2424-2424-242424242424"
        crash_trace = rollover_trace_root / (
            "rollout-" + crash_session + ".jsonl"
        )
        crash_trace.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "id": crash_session, "cwd": str(VAULT),
            }}) + "\n" + json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": crash_turn,
                "started_at": "2026-08-21T12:09:30Z",
            }}) + "\n",
            encoding="utf-8",
        )
        crash_completion_base = root / "rollover-completion-generations"
        crash_policy = {
            **rollover_policy,
            "roots": {
                **rollover_policy["roots"],
                "completion_work": str(crash_completion_base),
            },
        }
        crash_a_policy = {
            **rollover_a_policy,
            "roots": {
                **rollover_a_policy["roots"],
                "completion_work": str(crash_completion_base),
            },
        }
        crash_a_complete = POLICY.generation_root(
            crash_completion_base, "a" * 64, "codex",
        )
        crash_b_complete = POLICY.generation_root(
            crash_completion_base, "b" * 64, "codex",
        )
        crash_path, _ = register_current(
            crash_a_policy, "a" * 64, "1" * 64, rollover_a,
            crash_trace, crash_session, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:09:31Z", "now"),
        )
        with crash_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": crash_turn,
                "completed_at": "2026-08-21T12:09:40Z",
            }}) + "\n")
        original_state_write = globals()["atomic_state_write"]
        injected_close = {"value": False}

        def fail_crash_close_once(path, state):
            if path == crash_path and state.get("status") == "closed" and not injected_close["value"]:
                injected_close["value"] = True
                raise OSError("injected-post-publication-state-write")
            return original_state_write(path, state)

        globals()["atomic_state_write"] = fail_crash_close_once
        try:
            try:
                reconcile(
                    crash_a_policy, "a" * 64, "1" * 64, rollover_a,
                    crash_a_complete, mutex_root=rollover_ledger,
                    now=parse_timestamp("2026-08-21T12:09:41Z", "now"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("post-publication Codex close failure was not observed")
        finally:
            globals()["atomic_state_write"] = original_state_write
        crash_state_raw, _ = POLICY.read_regular_bytes(
            crash_path, "crash rollover state", owner_private=True,
        )
        assert POLICY.strict_json_loads(
            crash_state_raw, "crash rollover state",
        )["status"] == "open"
        assert len(list(crash_a_complete.glob("*.json"))) == 1
        for scope in ("turn", "session"):
            try:
                switch_across_generations(
                    crash_policy, "b" * 64, "2" * 64, rollover_base,
                    crash_session, "personal", rollover_ledger, scope=scope,
                    now=parse_timestamp("2026-08-21T12:09:42Z", "now"),
                    completion_root=crash_b_complete,
                )
            except ValueError as error:
                assert str(error) == "Codex task completion is already prepared"
            else:
                raise AssertionError("prepared Codex completion was switched")
        replaced = {"value": False}

        def replace_then_fail(path, state):
            original_state_write(path, state)
            if (
                path == crash_path and state.get("status") == "closed"
                and not replaced["value"]
            ):
                replaced["value"] = True
                raise ValueError("injected-post-replace-Codex-state")

        globals()["atomic_state_write"] = replace_then_fail
        try:
            recovered, recovered_retired = reconcile_prior_generations(
                crash_policy, "b" * 64, "2" * 64, rollover_base,
                rollover_b, crash_b_complete, rollover_ledger, crash_session,
                now=parse_timestamp("2026-08-21T12:09:42Z", "now"),
            )
        finally:
            globals()["atomic_state_write"] = original_state_write
        assert len(recovered) == 1 and not recovered_retired
        assert len(list(crash_a_complete.glob("*.json"))) == 1
        assert not list(crash_b_complete.glob("*.json"))
        assert reconcile_prior_generations(
            crash_policy, "b" * 64, "2" * 64, rollover_base,
            rollover_b, crash_b_complete, rollover_ledger, crash_session,
            now=parse_timestamp("2026-08-21T12:09:43Z", "now"),
        ) == ([], [])

        duplicate_session = "14141414-1414-1414-1414-141414141414"
        duplicate_turn = "15151515-1515-1515-1515-151515151515"
        duplicate_trace = rollover_trace_root / (
            "rollout-" + duplicate_session + ".jsonl"
        )
        duplicate_trace.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "id": duplicate_session, "cwd": str(VAULT),
            }}) + "\n" + json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": duplicate_turn,
                "started_at": "2026-08-21T12:10:00Z",
            }}) + "\n",
            encoding="utf-8",
        )
        duplicate_a_path, _ = register_current(
            rollover_a_policy, "a" * 64, "1" * 64, rollover_a,
            duplicate_trace, duplicate_session, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:10:01Z", "now"),
        )
        duplicate_b_path, _ = register_current(
            rollover_policy, "b" * 64, "2" * 64, rollover_b,
            duplicate_trace, duplicate_session, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:10:02Z", "now"),
        )
        with duplicate_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": duplicate_turn,
                "completed_at": "2026-08-21T12:10:10Z",
            }}) + "\n")
        duplicate_complete = root / "duplicate-complete"
        duplicate_closed, duplicate_retired = reconcile_prior_generations(
            rollover_policy, "b" * 64, "2" * 64, rollover_base,
            rollover_b, duplicate_complete, rollover_ledger,
            duplicate_session,
            now=parse_timestamp("2026-08-21T12:10:20Z", "now"),
        )
        assert len(duplicate_closed) == 1 and len(duplicate_retired) == 1
        current_closed, current_retired = reconcile(
            rollover_policy, "b" * 64, "2" * 64, rollover_b,
            duplicate_complete, now=parse_timestamp("2026-08-21T12:10:21Z", "now"),
            mutex_root=rollover_ledger,
        )
        assert not current_closed and not current_retired
        assert len(list(duplicate_complete.glob("*.json"))) == 1
        for duplicate_path in (duplicate_a_path, duplicate_b_path):
            duplicate_raw, _ = POLICY.read_regular_bytes(
                duplicate_path, "cross-generation duplicate", owner_private=True,
            )
            assert POLICY.strict_json_loads(
                duplicate_raw, "cross-generation duplicate",
            )["status"] == "closed"

        conflict_session = "16161616-1616-1616-1616-161616161616"
        conflict_turn = "17171717-1717-1717-1717-171717171717"
        conflict_trace = rollover_trace_root / (
            "rollout-" + conflict_session + ".jsonl"
        )
        conflict_trace.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "id": conflict_session, "cwd": str(VAULT),
            }}) + "\n" + json.dumps({"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": conflict_turn,
                "started_at": "2026-08-21T12:11:00Z",
            }}) + "\n",
            encoding="utf-8",
        )
        conflict_a_path, _ = register_current(
            rollover_a_policy, "a" * 64, "1" * 64, rollover_a,
            conflict_trace, conflict_session, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:11:01Z", "now"),
        )
        conflict_b_path, _ = register_current(
            rollover_policy, "b" * 64, "2" * 64, rollover_b,
            conflict_trace, conflict_session, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:11:02Z", "now"),
        )
        _, conflict_switched = switch_current(
            rollover_policy, "b" * 64, "2" * 64, rollover_b,
            conflict_session, "personal", ledger_root=rollover_ledger,
            scope="turn", now=parse_timestamp("2026-08-21T12:11:03Z", "now"),
        )
        assert conflict_switched
        with conflict_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": conflict_turn,
                "completed_at": "2026-08-21T12:11:10Z",
            }}) + "\n")
        conflict_complete = root / "conflict-complete"
        try:
            reconcile_prior_generations(
                rollover_policy, "b" * 64, "2" * 64, rollover_base,
                rollover_b, conflict_complete, rollover_ledger,
                conflict_session,
                now=parse_timestamp("2026-08-21T12:11:20Z", "now"),
            )
        except ValueError as error:
            assert str(error) == "conflicting lifecycle state across policy generations"
        else:
            raise AssertionError("conflicting lifecycle generations were accepted")
        assert not list(conflict_complete.glob("*.json"))
        for conflict_path in (conflict_a_path, conflict_b_path):
            conflict_raw, _ = POLICY.read_regular_bytes(
                conflict_path, "conflicting lifecycle state", owner_private=True,
            )
            assert POLICY.strict_json_loads(
                conflict_raw, "conflicting lifecycle state",
            )["status"] == "open"

        stale = dict(second_state, turn_id="55555555-5555-5555-5555-555555555555", policy_sha256="f" * 64)
        atomic_state_write(
            open_root / state_name(session_id, stale["turn_id"]), stale,
        )
        assert all(
            state["policy_sha256"] == policy_sha
            for _, state in load_states(open_root, policy, policy_sha, hook_sha)
        )
        missing_turn = "66666666-6666-6666-6666-666666666666"
        missing_state = dict(
            second_state, status="open", turn_id=missing_turn,
            trace_relative="2026/08/21/missing.jsonl",
            closure_receipt_sha256=None,
        )
        missing_path = open_root / state_name(session_id, missing_turn)
        atomic_state_write(missing_path, missing_state)
        try:
            reconcile(
                policy, policy_sha, hook_sha, open_root, completion_root,
                now=parse_timestamp("2026-08-21T12:06:00Z", "now"),
            )
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("missing trace was silently retired")
        missing_raw, _ = POLICY.read_regular_bytes(
            missing_path, "missing trace state", owner_private=True,
        )
        assert POLICY.strict_json_loads(missing_raw, "missing trace state")["status"] == "open"
    print("trace-session self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    register = subparsers.add_parser("register")
    register.add_argument("--zone", choices=("work", "personal", "client"), required=True)
    register.add_argument("--lock-token", required=True)
    switch = subparsers.add_parser("switch")
    switch.add_argument("--zone", choices=("work", "personal", "client"), required=True)
    switch.add_argument("--scope", choices=("session", "turn"), default="session")
    switch.add_argument("--lock-token", required=True)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.command:
        parser.error("choose register, switch or --self-test")
    try:
        policy, policy_sha = POLICY.load_policy()
        hook_sha = code_sha256()
        check_lock(args.lock_token)
        open_base = POLICY.ensure_private_directory(
            VAULT / policy["roots"]["open_work"],
        )
        open_root = POLICY.generation_root(
            open_base, policy_sha, "codex",
        )
        ledger_root = POLICY.ensure_private_directory(
            VAULT / policy["roots"]["open_work"] / ".sessions" / "codex"
        )
        completion_root = POLICY.generation_root(
            VAULT / policy["roots"]["completion_work"], policy_sha, "codex",
        )
        session_id = os.environ.get("CODEX_SESSION_ID", "")
        if args.command == "register":
            prior_closed, prior_retired = reconcile_prior_generations(
                policy, policy_sha, hook_sha, open_base, open_root,
                completion_root, ledger_root, session_id,
                lock_token=args.lock_token,
            )
            closed, retired = reconcile(
                policy, policy_sha, hook_sha, open_root, completion_root,
                mutex_root=ledger_root, lock_token=args.lock_token,
            )
            closed = prior_closed + closed
            retired = prior_retired + retired
            check_lock(args.lock_token)
            trace = locate_current_trace(policy["roots"]["trace_codex"], session_id)
            path, created = register_current(
                policy, policy_sha, hook_sha, open_root, trace, session_id,
                requested_zone=args.zone, ledger_root=ledger_root,
                completion_root=completion_root,
                lock_token=args.lock_token,
            )
            check_lock(args.lock_token)
            print(json.dumps({
                "schema_version": 1, "operation": "register-work-task",
                "state": path.name, "created": created,
                "completed_receipts_published": len(closed),
                "lifecycle_states_retired": len(retired),
            }, sort_keys=True))
        else:
            path, changed, _ = switch_across_generations(
                policy, policy_sha, hook_sha, open_base, session_id, args.zone,
                ledger_root, scope=args.scope,
                lock_token=args.lock_token,
                completion_root=completion_root,
            )
            check_lock(args.lock_token)
            print(json.dumps({
                "schema_version": 1, "operation": "record-zone-transition",
                "state": path.name, "changed": changed,
            }, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
