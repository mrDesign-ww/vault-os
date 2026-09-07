#!/usr/bin/env python3
"""Claude hooks: register a work turn and receipt it only after the official Stop event."""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
VAULT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location("reflection_policy", HERE / "reflection_policy.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
LOCK_PATH = ".vault-meta/write/work"
HOOK_LOCK_TTL_SECONDS = 60
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SESSION_ROUTES = {
    "перейди в личную область": "personal",
    "switch to personal": "personal",
    "личный режим": "personal",
    "перейди в область client": "client",
    "switch to client": "client",
    "режим client": "client",
    "вернись в рабочую": "work",
    "switch to work": "work",
}


class SegmentAmbiguity(ValueError):
    """The transcript range is deterministically not one completed human turn."""


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso_utc(value):
    return value.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value):
    parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp")
    return parsed.astimezone(datetime.timezone.utc)


def code_sha256():
    raw, _ = POLICY.read_regular_bytes(Path(__file__), "Claude reflection hook")
    return hashlib.sha256(raw).hexdigest()


def acquire_lock():
    result = subprocess.run(
        [
            "bash", "scripts/wiki-lock.sh", "acquire", LOCK_PATH,
            "--ttl", str(HOOK_LOCK_TTL_SECONDS),
        ],
        cwd=VAULT, capture_output=True, text=True,
    )
    token = result.stdout.strip()
    return token if result.returncode == 0 and SHA256_RE.fullmatch(token) else None


def release_lock(token):
    if token:
        subprocess.run(
            ["bash", "scripts/wiki-lock.sh", "release", LOCK_PATH, token],
            cwd=VAULT, capture_output=True, text=True,
        )


def read_hook_input(stream):
    value = POLICY.strict_json_loads(stream.buffer.read(), "Claude hook input")
    if not isinstance(value, dict):
        raise ValueError("hook-input")
    if not isinstance(value.get("session_id"), str) or not value["session_id"]:
        raise ValueError("hook-session")
    if os.path.realpath(str(value.get("cwd", ""))) != str(VAULT):
        raise ValueError("hook-vault")
    return value


def prompt_route(value):
    prompt = value.get("prompt")
    if not isinstance(prompt, str):
        return None
    stripped = prompt.strip()
    lowered = stripped.lower()
    if lowered.startswith("личное:"):
        return "personal", "turn"
    if lowered.startswith("client:"):
        return "client", "turn"
    target = SESSION_ROUTES.get(" ".join(lowered.split()))
    return (target, "session") if target else None


def transcript_path(value, policy):
    root = Path(policy["roots"]["trace_claude"])
    candidate = Path(os.path.abspath(os.path.expanduser(str(value.get("transcript_path", "")))))
    if candidate.suffix != ".jsonl":
        raise ValueError("transcript-path")
    try:
        candidate.relative_to(root)
        if candidate.exists() and candidate.resolve(strict=True) != candidate:
            raise ValueError("transcript-path")
    except (OSError, ValueError) as error:
        raise ValueError("transcript-path") from error
    return candidate


def read_transcript(path, allow_missing=False):
    try:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        if allow_missing:
            return b"", None
        raise
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or before.st_nlink != 1
        ):
            raise ValueError("transcript-identity")
        raw = handle.read()
        after = os.fstat(handle.fileno())
        if POLICY.file_identity(before) != POLICY.file_identity(after):
            raise ValueError("transcript-drift")
    return raw, after


def structural_offsets(raw):
    offset = 0
    last_user = None
    last_assistant = None
    for line in raw.splitlines(keepends=True):
        start = offset
        offset += len(line)
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        message = item.get("message") if isinstance(item, dict) else None
        role = message.get("role") if isinstance(message, dict) else None
        if item.get("type") == "user" and role == "user":
            kind, _ = POLICY.claude_user_message(message)
            if kind == "invalid":
                raise ValueError("transcript-user-shape")
            if kind == "prompt":
                last_user = start
        elif item.get("type") == "assistant" and role == "assistant":
            last_assistant = start
    start = last_user if last_user is not None and (last_assistant is None or last_user > last_assistant) else len(raw)
    return start, last_user, last_assistant


def atomic_state_write(path, value):
    POLICY.atomic_private_json_write(path, value, "Claude lifecycle state")


def validate_session_ledger(value, session_key):
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version", "platform", "session_key", "zone",
            "prompt_sequence", "revision", "updated_at", "recorded_by",
        }
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("platform") != "claude"
        or value.get("session_key") != session_key
        or value.get("zone") not in ("work", "personal", "client")
        or any(
            isinstance(value.get(field), bool)
            or not isinstance(value.get(field), int)
            or value[field] < 0
            for field in ("prompt_sequence", "revision")
        )
        or value.get("recorded_by") != "claude:prompt-hook"
    ):
        raise ValueError("Claude session zone ledger is invalid")
    parse_timestamp(value["updated_at"])
    return value


def load_session_ledger(root, session_key, now=None):
    if not SHA256_RE.fullmatch(session_key):
        raise ValueError("Claude session identity is invalid")
    root = POLICY.ensure_private_directory(root)
    path = root / (session_key + ".json")
    try:
        raw, _ = POLICY.read_regular_bytes(
            path, "Claude session zone ledger", owner_private=True,
        )
    except FileNotFoundError:
        return path, {
            "schema_version": 1, "platform": "claude", "session_key": session_key,
            "zone": "work", "prompt_sequence": 0, "revision": 0,
            "updated_at": iso_utc(now or utc_now()),
            "recorded_by": "claude:prompt-hook",
        }, False
    return path, validate_session_ledger(
        POLICY.strict_json_loads(raw, "Claude session zone ledger"), session_key,
    ), True


def write_session_ledger(path, value):
    validate_session_ledger(value, value.get("session_key"))
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
            journal, "Claude state-ledger transaction", owner_private=True,
        )
        transaction = POLICY._validate_transaction(
            POLICY.strict_json_loads(raw, "Claude state-ledger transaction")
        )
        session_key = transaction["ledger_after"].get("session_key")
        if (
            not SHA256_RE.fullmatch(str(session_key or ""))
            or journal.name != session_key + ".json"
            or transaction["ledger_name"] != session_key + ".json"
            or transaction["state_after"].get("session_key") != session_key
        ):
            raise ValueError("Claude state-ledger transaction identity is invalid")
        POLICY.recover_state_ledger_transaction(
            journal, Path(open_root), Path(ledger_root),
            validate_state=lambda item: validate_generation_state(
                item, policy, policy_sha,
                current_policy_sha or (policy_sha if hook_sha is not None else None),
                current_hook_sha or hook_sha,
            ),
            validate_ledger=lambda item: validate_session_ledger(
                item, session_key,
            ),
        )


def commit_session_transaction(
    open_root, ledger_root, state_path, state_before, state_after,
    ledger_path, ledger_before, ledger_after, policy, policy_sha, hook_sha,
    recovery_action="commit",
):
    session_key = ledger_after["session_key"]
    journal = POLICY.ensure_private_directory(
        Path(open_root) / ".transactions"
    ) / (session_key + ".json")
    POLICY.commit_state_ledger_transaction(
        journal, Path(open_root), Path(ledger_root),
        state_path, state_before, state_after,
        ledger_path, ledger_before, ledger_after,
        validate_state=lambda item: validate_generation_state(
            item, policy, item.get("policy_sha256"), policy_sha, hook_sha,
        ),
        validate_ledger=lambda item: validate_session_ledger(
            item, session_key,
        ),
        recovery_action=recovery_action,
    )


def state_filename(session_key, turn_id):
    if not SHA256_RE.fullmatch(session_key) or not SHA256_RE.fullmatch(turn_id):
        raise ValueError("state-identity")
    return "claude-%s-%s.json" % (session_key, turn_id)


def validate_pending_receipt(
    pending, state, policy, delegation_id=None,
):
    expected = {
        "schema_version", "receipt_type", "platform", "session_id", "turn_id",
        "trace_relative", "trace_identity", "segment_start", "segment_end",
        "segment_size", "segment_sha256", "session_meta_sha256",
        "prompt_sha256", "prompt_sequence", "task_started_at", "completed_at",
        "terminal_reason", "vault_id", "vault_path", "initial_zone",
        "zone_transitions", "eligible", "eligibility_reason", "delegated_by",
        "recorded_by", "delegation_id", "policy_sha256",
        "session_hook_sha256", "registration_policy_sha256",
        "registration_hook_sha256",
    }
    identity = pending.get("trace_identity") if isinstance(pending, dict) else None
    ranges = (
        pending.get("segment_start"), pending.get("segment_end"),
        pending.get("segment_size"),
    ) if isinstance(pending, dict) else (None, None, None)
    if (
        not isinstance(pending, dict)
        or set(pending) != expected
        or type(pending.get("schema_version")) is not int
        or pending.get("schema_version") != 2
        or pending.get("receipt_type") != "claude-stop-turn-segment"
        or pending.get("platform") != "claude"
        or pending.get("session_id") != state["session_key"]
        or pending.get("turn_id") != state["turn_id"]
        or pending.get("trace_relative") != state["trace_relative"]
        or pending.get("session_meta_sha256") != state["session_meta_sha256"]
        or pending.get("prompt_sha256") != state["prompt_sha256"]
        or pending.get("prompt_sequence") != state["prompt_sequence"]
        or pending.get("task_started_at") != state["task_started_at"]
        or pending.get("vault_id") != state["vault_id"]
        or pending.get("vault_path") != state["vault_path"]
        or pending.get("initial_zone") != state["initial_zone"]
        or pending.get("zone_transitions") != state["zone_transitions"]
        or pending.get("delegation_id") != (
            delegation_id if delegation_id is not None else policy["delegation_id"]
        )
        or pending.get("delegated_by") != POLICY.OWNER
        or pending.get("registration_policy_sha256") != state["policy_sha256"]
        or pending.get("registration_hook_sha256") != state["session_hook_sha256"]
        or not SHA256_RE.fullmatch(str(pending.get("policy_sha256", "")))
        or not SHA256_RE.fullmatch(str(pending.get("session_hook_sha256", "")))
        or not SHA256_RE.fullmatch(str(pending.get("segment_sha256", "")))
        or not isinstance(identity, dict) or set(identity) != {"device", "inode"}
        or any(type(identity[field]) is not int or identity[field] < 0 for field in identity)
        or any(type(item) is not int for item in ranges)
        or ranges[0] < 0 or ranges[1] <= ranges[0] or ranges[2] != ranges[1] - ranges[0]
        or ranges[0] != state["start_offset"]
        or type(pending.get("eligible")) is not bool
        or (
            pending.get("terminal_reason"), pending.get("recorded_by")
        ) not in (
            ("stop-hook", "claude:stop-hook"),
            ("next-prompt-hook", "claude:prompt-hook"),
        )
    ):
        raise ValueError("state-pending-receipt")
    if parse_timestamp(pending.get("completed_at")) < parse_timestamp(
        pending.get("task_started_at"),
    ):
        raise ValueError("state-pending-receipt")
    if state["zone_transitions"]:
        eligibility = (False, "zone-transition")
    elif state["initial_zone"] != "work":
        eligibility = (False, "initial-non-work-zone")
    else:
        eligibility = (pending["eligible"], pending["eligibility_reason"])
        if eligibility not in ((True, "eligible"), (False, "started-before-policy")):
            raise ValueError("state-pending-receipt")
    if (pending["eligible"], pending["eligibility_reason"]) != eligibility:
        raise ValueError("state-pending-receipt")
    return pending


def validate_state(
    state, policy, policy_sha, hook_sha, delegation_id=None,
    pending_delegation_id=None,
):
    expected = {
        "schema_version", "status", "platform", "session_key", "turn_id",
        "trace_relative", "vault_id", "vault_path", "session_meta_sha256",
        "start_offset", "task_started_at", "initial_zone", "zone_transitions",
        "registered_at", "recorded_by", "delegated_by", "delegation_id",
        "policy_sha256", "session_hook_sha256", "closure_receipt_sha256",
        "prompt_sha256", "prompt_sequence", "pending_receipt",
    }
    if not isinstance(state, dict) or set(state) != expected:
        raise ValueError("state-schema")
    if (
        type(state.get("schema_version")) is not int
        or state.get("schema_version") != 1 or state.get("platform") != "claude"
        or state.get("status") not in ("open", "closing", "closed")
        or state.get("initial_zone") not in ("work", "personal", "client")
        or state.get("recorded_by") != "claude:prompt-hook"
        or state.get("delegated_by") != POLICY.OWNER
        or state.get("delegation_id") != (
            delegation_id if delegation_id is not None else policy["delegation_id"]
        )
        or state.get("policy_sha256") != policy_sha or state.get("session_hook_sha256") != hook_sha
        or not SHA256_RE.fullmatch(str(policy_sha or ""))
        or not SHA256_RE.fullmatch(str(hook_sha or ""))
        or state.get("vault_id") != policy["vault"]["id"] or state.get("vault_path") != policy["vault"]["path"]
        or not SHA256_RE.fullmatch(str(state.get("session_key", "")))
        or not SHA256_RE.fullmatch(str(state.get("turn_id", "")))
        or not SHA256_RE.fullmatch(str(state.get("prompt_sha256", "")))
        or isinstance(state.get("prompt_sequence"), bool)
        or not isinstance(state.get("prompt_sequence"), int)
        or state["prompt_sequence"] < 1
        or not isinstance(state.get("zone_transitions"), list)
    ):
        raise ValueError("state-authority")
    current_zone = state["initial_zone"]
    for transition in state["zone_transitions"]:
        if (
            not isinstance(transition, dict)
            or set(transition) != {"at", "from", "to", "recorded_by", "scope"}
            or transition.get("from") != current_zone
            or transition.get("to") not in ("work", "personal", "client")
            or transition.get("scope") not in ("session", "turn")
        ):
            raise ValueError("state-zone-ledger")
        parse_timestamp(transition["at"])
        current_zone = transition["to"]
    pending = state.get("pending_receipt")
    closure = state.get("closure_receipt_sha256")
    if state["status"] == "closing":
        if closure is not None:
            raise ValueError("state-closure-receipt")
        validate_pending_receipt(
            pending, state, policy,
            delegation_id=(
                pending_delegation_id
                if pending_delegation_id is not None else policy["delegation_id"]
            ),
        )
    elif pending is not None:
        raise ValueError("state-pending-receipt")
    if (
        (state["status"] == "open" and closure is not None)
        or (
            state["status"] == "closed" and closure is not None
            and not SHA256_RE.fullmatch(str(closure))
        )
    ):
        raise ValueError("state-closure-receipt")
    parse_timestamp(state["task_started_at"])
    parse_timestamp(state["registered_at"])
    return state


def lifecycle_authority(state, policy, current_policy_sha, current_hook_sha):
    if not isinstance(state, dict):
        raise ValueError("state-authority-not-pinned")
    matches = [
        item for item in POLICY.completion_authorities(
            policy, current_policy_sha, "claude", current_hook_sha,
        )
        if item["policy_sha256"] == state.get("policy_sha256")
    ]
    if len(matches) != 1:
        raise ValueError("state-authority-not-pinned")
    authority = matches[0]
    if (
        state.get("session_hook_sha256") != authority["session_hook_sha256"]
        or state.get("delegation_id") != authority["delegation_id"]
        or parse_timestamp(state.get("registered_at"))
        < parse_timestamp(authority["not_before"])
    ):
        raise ValueError("state-authority-not-pinned")
    return authority


def receipt_authority(receipt, policy, current_policy_sha, current_hook_sha):
    if not isinstance(receipt, dict):
        raise ValueError("completion-authority-not-pinned")
    matches = [
        item for item in POLICY.completion_authorities(
            policy, current_policy_sha, "claude", current_hook_sha,
        )
        if item["policy_sha256"] == receipt.get("policy_sha256")
    ]
    if len(matches) != 1:
        raise ValueError("completion-authority-not-pinned")
    authority = matches[0]
    if (
        receipt.get("session_hook_sha256") != authority["session_hook_sha256"]
        or receipt.get("delegation_id") != authority["delegation_id"]
        or parse_timestamp(receipt.get("completed_at"))
        < parse_timestamp(authority["not_before"])
    ):
        raise ValueError("completion-authority-not-pinned")
    return authority


def validate_generation_state(
    state, policy, declared_policy_sha=None, current_policy_sha=None,
    current_hook_sha=None,
):
    policy_sha = state.get("policy_sha256") if isinstance(state, dict) else None
    hook_sha = state.get("session_hook_sha256") if isinstance(state, dict) else None
    authority = None
    pending_authority = None
    if current_policy_sha is not None:
        authority = lifecycle_authority(
            state, policy, current_policy_sha, current_hook_sha,
        )
        if isinstance(state, dict) and isinstance(state.get("pending_receipt"), dict):
            pending_authority = receipt_authority(
                state["pending_receipt"], policy,
                current_policy_sha, current_hook_sha,
            )
    validate_state(
        state, policy, policy_sha, hook_sha,
        delegation_id=authority["delegation_id"] if authority else None,
        pending_delegation_id=(
            pending_authority["delegation_id"] if pending_authority else None
        ),
    )
    if declared_policy_sha is not None and policy_sha != declared_policy_sha:
        raise ValueError("state-generation-drift")
    return state


def load_generation_states(
    root, policy, declared_policy_sha=None, current_policy_sha=None,
    current_hook_sha=None,
):
    root = POLICY.validate_private_directory(root)
    result = []
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        raw, _ = POLICY.read_regular_bytes(
            path, "Claude lifecycle state", owner_private=True,
        )
        state = POLICY.strict_json_loads(raw, "Claude lifecycle state")
        validate_generation_state(
            state, policy, declared_policy_sha,
            current_policy_sha, current_hook_sha,
        )
        if path.name != state_filename(state["session_key"], state["turn_id"]):
            raise ValueError("state-filename")
        result.append((path, state))
    return result


def load_states(root, policy, policy_sha, hook_sha):
    root = POLICY.ensure_private_directory(root)
    result = []
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        try:
            raw, _ = POLICY.read_regular_bytes(path, "Claude lifecycle state", owner_private=True)
            state = POLICY.strict_json_loads(raw, "Claude lifecycle state")
            validate_state(state, policy, policy_sha, hook_sha)
            if path.name != state_filename(state["session_key"], state["turn_id"]):
                raise ValueError("state-filename")
            result.append((path, state))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return result


def validate_closed_completion(
    state, policy, completion_root, current_policy_sha, current_hook_sha,
):
    """Prove that a closed Claude state names one exact immutable receipt."""
    digest = state.get("closure_receipt_sha256")
    if state.get("status") != "closed" or digest is None:
        return None
    authorities = POLICY.completion_authorities(
        policy, current_policy_sha, "claude", current_hook_sha,
    )
    base_value = policy.get("roots", {}).get("completion_work")
    if isinstance(base_value, str) and base_value:
        base = Path(base_value)
        if not base.is_absolute():
            base = POLICY.VAULT / base
        roots = [
            (
                authority,
                POLICY.generation_root(
                    base, authority["policy_sha256"], "claude", create=False,
                ),
            )
            for authority in authorities
        ]
    else:
        roots = [(None, Path(completion_root))]
    receipts = {}
    for declared_authority, root in roots:
        path = root / (digest + ".json")
        try:
            raw, _ = POLICY.read_regular_bytes(
                path, "closed Claude completion receipt", owner_private=True,
            )
        except FileNotFoundError:
            continue
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("closed Claude completion artifact drifted")
        receipt = POLICY.strict_json_loads(raw, "closed Claude completion receipt")
        authority = receipt_authority(
            receipt, policy, current_policy_sha, current_hook_sha,
        )
        validate_pending_receipt(
            receipt, state, policy, authority["delegation_id"],
        )
        if (
            declared_authority is not None
            and authority["policy_sha256"]
            != declared_authority["policy_sha256"]
        ):
            raise ValueError("closed Claude completion generation is invalid")
        if raw != POLICY.canonical_json_bytes(receipt):
            raise ValueError("closed Claude completion artifact drifted")
        receipts[raw] = receipt
    if len(receipts) != 1:
        raise ValueError("closed Claude completion artifact is missing or conflicting")
    return next(iter(receipts.values()))


def finalize_pending(
    state_path, state, policy, completion_root,
    current_policy_sha, current_hook_sha,
):
    if state["status"] != "closing":
        return state.get("closure_receipt_sha256")
    authority = receipt_authority(
        state["pending_receipt"], policy,
        current_policy_sha, current_hook_sha,
    )
    validate_pending_receipt(
        state["pending_receipt"], state, policy, authority["delegation_id"],
    )
    raw = POLICY.canonical_json_bytes(state["pending_receipt"])
    target_root = POLICY.completion_generation_root(
        policy, completion_root, state["pending_receipt"], "claude",
    )
    _, receipt_sha, _ = POLICY.publish_immutable(target_root, raw)
    state = dict(state)
    state["status"] = "closed"
    state["closure_receipt_sha256"] = receipt_sha
    state["pending_receipt"] = None
    try:
        atomic_state_write(state_path, state)
    except (OSError, ValueError):
        observed_raw, _ = POLICY.read_regular_bytes(
            state_path, "Claude lifecycle state", owner_private=True,
        )
        if POLICY.strict_json_loads(observed_raw, "Claude lifecycle state") != state:
            raise
    return receipt_sha


def generation_records(
    open_base, ledger_root, policy, current_policy_sha, current_hook_sha,
    session_key=None,
):
    records = []
    allowed_policy_shas = {
        item["policy_sha256"] for item in POLICY.completion_authorities(
            policy, current_policy_sha, "claude", current_hook_sha,
        )
    }
    for declared_policy_sha, root in POLICY.lifecycle_generation_roots(
        open_base, "claude", current_policy_sha=current_policy_sha,
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
            if session_key is None or state["session_key"] == session_key:
                records.append((declared_policy_sha, root, path, state))
    return records


def generation_identity(state):
    return (
        state["session_key"], state["trace_relative"], state["start_offset"],
        state["prompt_sha256"], state["initial_zone"],
        POLICY.canonical_json_bytes(state["zone_transitions"]),
    )


def generation_groups(
    records, policy=None, completion_root=None,
    current_policy_sha=None, current_hook_sha=None,
):
    groups = {}
    for record in records:
        state = record[3]
        groups.setdefault((
            state["session_key"], state["trace_relative"],
            state["start_offset"], state["prompt_sha256"],
        ), []).append(record)
    result = []
    for group in groups.values():
        if len({generation_identity(record[3]) for record in group}) != 1:
            raise ValueError("conflicting Claude lifecycle state across policy generations")
        pending = set()
        for record in group:
            receipt = record[3]["pending_receipt"]
            if receipt is None:
                continue
            if current_policy_sha is None:
                raise ValueError("prepared Claude completion authority is unavailable")
            authority = receipt_authority(
                receipt, policy, current_policy_sha, current_hook_sha,
            )
            validate_pending_receipt(
                receipt, record[3], policy, authority["delegation_id"],
            )
            pending.add(POLICY.canonical_json_bytes(receipt))
        completed = {
            POLICY.canonical_json_bytes(validate_closed_completion(
                record[3], policy, completion_root,
                current_policy_sha, current_hook_sha,
            ))
            for record in group
            if record[3]["status"] == "closed"
            and record[3]["closure_receipt_sha256"] is not None
        } if policy is not None and completion_root is not None else set()
        if any(
            record[3]["status"] == "closed"
            and record[3]["closure_receipt_sha256"] is not None
            for record in group
        ) and (policy is None or completion_root is None):
            raise ValueError("closed Claude completion authority is unavailable")
        if len(pending) > 1:
            raise ValueError("conflicting prepared Claude receipt across policy generations")
        if len(completed) > 1:
            raise ValueError("conflicting closed Claude receipt across policy generations")
        if completed and pending and completed != pending:
            raise ValueError("closed and prepared Claude receipts conflict")
        group.sort(key=lambda record: (
            0 if (
                record[3]["status"] == "closed"
                and record[3]["closure_receipt_sha256"] is not None
            ) else 1 if record[3]["status"] == "closing" else 2,
            parse_timestamp(record[3]["registered_at"]),
            str(record[2]),
        ))
        result.append(group)
    return result


def register_prompt(
    value, policy, policy_sha, hook_sha, open_root, completion_root,
    ledger_root=None, now=None,
):
    prompt = value.get("prompt")
    if not isinstance(prompt, str):
        raise ValueError("hook-prompt")
    path = transcript_path(value, policy)
    raw, _ = read_transcript(path, allow_missing=True)
    start, _, _ = structural_offsets(raw)
    session_key = hashlib.sha256(value["session_id"].encode("utf-8")).hexdigest()
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    instant = now or utc_now()
    POLICY.ensure_private_directory(open_root)
    ledger_root = Path(ledger_root or (Path(open_root) / ".sessions"))
    recover_session_transactions(
        open_root, ledger_root, policy, policy_sha, hook_sha,
    )
    ledger_path, ledger, ledger_exists = load_session_ledger(
        ledger_root, session_key, now=instant,
    )
    states = load_states(open_root, policy, policy_sha, hook_sha)
    for previous_path, previous in states:
        if previous["session_key"] == session_key and previous["status"] == "closing":
            finalize_pending(
                previous_path, previous, policy, completion_root,
                policy_sha, hook_sha,
            )
    states = load_states(open_root, policy, policy_sha, hook_sha)
    for previous_path, previous in states:
        if previous["session_key"] != session_key or previous["status"] != "open":
            continue
        if (
            previous["prompt_sha256"] == prompt_sha
            and previous["start_offset"] == start
            and previous["trace_relative"] == path.relative_to(Path(policy["roots"]["trace_claude"])).as_posix()
        ):
            if not ledger_exists or ledger["prompt_sequence"] < previous["prompt_sequence"]:
                healed = dict(ledger)
                healed["prompt_sequence"] = previous["prompt_sequence"]
                healed["zone"] = previous["initial_zone"]
                for transition in previous["zone_transitions"]:
                    if transition["scope"] == "session":
                        healed["zone"] = transition["to"]
                healed["updated_at"] = iso_utc(instant)
                write_session_ledger(ledger_path, healed)
            return previous_path, False
        try:
            recover_previous_prompt(
                previous_path, previous, policy, policy_sha, hook_sha,
                completion_root, path, start, now=instant,
            )
        except SegmentAmbiguity:
            retire_open_state(
                previous_path, previous, policy_sha, hook_sha,
                "ambiguous-next-prompt-segment",
            )
    sequence = ledger["prompt_sequence"] + 1
    turn_id = hashlib.sha256(
        (session_key + "\0" + policy_sha + "\0" + str(sequence) + "\0" + prompt_sha).encode("utf-8")
    ).hexdigest()
    state_path = Path(open_root) / state_filename(session_key, turn_id)
    if state_path.exists():
        raise ValueError("state-collision")
    root = Path(policy["roots"]["trace_claude"])
    current_zone = ledger["zone"]
    transitions = []
    route = prompt_route(value)
    if route is not None:
        target, scope = route
        if target != current_zone:
            transitions.append({
                "at": iso_utc(instant), "from": current_zone, "to": target,
                "recorded_by": "claude:prompt-hook", "scope": scope,
            })
    subset = {
        "session_key": session_key, "transcript_relative": path.relative_to(root).as_posix(),
        "cwd": str(VAULT), "hook_event_name": "UserPromptSubmit",
        "prompt_sha256": prompt_sha, "prompt_sequence": sequence,
    }
    state = {
        "schema_version": 1, "status": "open", "platform": "claude",
        "session_key": session_key, "turn_id": turn_id,
        "trace_relative": path.relative_to(root).as_posix(),
        "vault_id": policy["vault"]["id"], "vault_path": policy["vault"]["path"],
        "session_meta_sha256": hashlib.sha256(POLICY.canonical_json_bytes(subset)).hexdigest(),
        "start_offset": start, "task_started_at": iso_utc(instant),
        "initial_zone": current_zone,
        "zone_transitions": transitions, "registered_at": iso_utc(instant),
        "recorded_by": "claude:prompt-hook", "delegated_by": POLICY.OWNER,
        "delegation_id": policy["delegation_id"], "policy_sha256": policy_sha,
        "session_hook_sha256": hook_sha, "closure_receipt_sha256": None,
        "prompt_sha256": prompt_sha, "prompt_sequence": sequence,
        "pending_receipt": None,
    }
    updated_ledger = dict(ledger)
    updated_ledger["prompt_sequence"] = sequence
    updated_ledger["updated_at"] = iso_utc(instant)
    if route is not None and route[1] == "session" and route[0] != current_zone:
        updated_ledger["zone"] = route[0]
        updated_ledger["revision"] += 1
    commit_session_transaction(
        open_root, ledger_root, state_path, None, state,
        ledger_path, ledger if ledger_exists else None, updated_ledger,
        policy, policy_sha, hook_sha,
    )
    return state_path, True


def completed_shape(segment, prompt_sha):
    user_hashes = []
    assistant_after_user = False
    for line in segment.splitlines(keepends=True):
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        message = item.get("message") if isinstance(item, dict) else None
        role = message.get("role") if isinstance(message, dict) else None
        if item.get("type") == "user" and role == "user":
            kind, text = POLICY.claude_user_message(message)
            if kind == "invalid":
                raise SegmentAmbiguity("segment-prompt-shape")
            if kind == "continuation":
                continue
            user_hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
        elif item.get("type") == "assistant" and role == "assistant" and user_hashes:
            assistant_after_user = True
    return user_hashes == [prompt_sha] and assistant_after_user


def prepare_close(
    state_path, state, policy, policy_sha, hook_sha, completion_root,
    raw, metadata, end, terminal_reason, recorded_by, now=None,
):
    start = state["start_offset"]
    if metadata is None or start < 0 or end <= start or end > len(raw):
        raise SegmentAmbiguity("segment-range")
    segment = raw[start:end]
    if not completed_shape(segment, state["prompt_sha256"]):
        raise SegmentAmbiguity("segment-not-completed")
    instant = now or utc_now()
    transitions = state["zone_transitions"]
    eligible = (
        state["initial_zone"] == "work" and not transitions
        and parse_timestamp(state["task_started_at"]) >= parse_timestamp(policy["not_before"])
    )
    receipt = {
        "schema_version": 2, "receipt_type": "claude-stop-turn-segment",
        "platform": "claude", "session_id": state["session_key"],
        "turn_id": state["turn_id"], "trace_relative": state["trace_relative"],
        "trace_identity": {"device": metadata.st_dev, "inode": metadata.st_ino},
        "segment_start": start, "segment_end": end, "segment_size": len(segment),
        "segment_sha256": hashlib.sha256(segment).hexdigest(),
        "session_meta_sha256": state["session_meta_sha256"],
        "prompt_sha256": state["prompt_sha256"],
        "prompt_sequence": state["prompt_sequence"],
        "task_started_at": state["task_started_at"], "completed_at": iso_utc(instant),
        "terminal_reason": terminal_reason, "vault_id": policy["vault"]["id"],
        "vault_path": policy["vault"]["path"], "initial_zone": state["initial_zone"],
        "zone_transitions": transitions, "eligible": eligible,
        "eligibility_reason": "eligible" if eligible else (
            "zone-transition" if transitions else (
                "initial-non-work-zone" if state["initial_zone"] != "work"
                else "started-before-policy"
            )
        ),
        "delegated_by": POLICY.OWNER, "recorded_by": recorded_by,
        "delegation_id": policy["delegation_id"], "policy_sha256": policy_sha,
        "session_hook_sha256": hook_sha,
        "registration_policy_sha256": state["policy_sha256"],
        "registration_hook_sha256": state["session_hook_sha256"],
    }
    closing = dict(state)
    closing["status"] = "closing"
    closing["pending_receipt"] = receipt
    atomic_state_write(state_path, closing)
    return finalize_pending(
        state_path, closing, policy, completion_root, policy_sha, hook_sha,
    )


def retire_open_state(state_path, state, policy_sha, hook_sha, reason_code):
    if not re.fullmatch(r"[a-z0-9-]+", reason_code):
        raise ValueError("Claude retirement reason is invalid")
    if state.get("pending_receipt") is not None:
        raise ValueError("prepared Claude completion cannot be retired")
    retirement = {
        "schema_version": 1, "receipt_type": "claude-lifecycle-retirement",
        "platform": "claude", "session_id": state["session_key"],
        "turn_id": state["turn_id"], "trace_relative": state["trace_relative"],
        "reason_code": reason_code, "policy_sha256": policy_sha,
        "session_hook_sha256": hook_sha,
    }
    _, digest, _ = POLICY.publish_immutable(
        state_path.parent / ".recovery", POLICY.canonical_json_bytes(retirement),
    )
    retired = dict(state)
    retired["status"] = "closed"
    retired["pending_receipt"] = None
    retired["closure_receipt_sha256"] = None
    atomic_state_write(state_path, retired)
    return digest


def acknowledge_pending_completion(state_path, state, completed_receipt):
    pending = state.get("pending_receipt")
    if pending is None:
        return False
    if POLICY.canonical_json_bytes(pending) != POLICY.canonical_json_bytes(
        completed_receipt
    ):
        raise ValueError("closed and prepared Claude receipts conflict")
    desired = {
        **state, "status": "closed", "pending_receipt": None,
        "closure_receipt_sha256": hashlib.sha256(
            POLICY.canonical_json_bytes(pending)
        ).hexdigest(),
    }
    atomic_state_write(state_path, desired)
    return True


def closed_stop_retry(
    value, records, policy, completion_root, policy_sha, hook_sha,
):
    path = transcript_path(value, policy)
    raw, metadata = read_transcript(path)
    root = Path(policy["roots"]["trace_claude"])
    relative = path.relative_to(root).as_posix()
    matches = set()
    for _, _, _, state in records:
        if (
            state["status"] != "closed"
            or state["closure_receipt_sha256"] is None
            or state["trace_relative"] != relative
        ):
            continue
        receipt = validate_closed_completion(
            state, policy, completion_root, policy_sha, hook_sha,
        )
        start, end = receipt["segment_start"], receipt["segment_end"]
        if (
            end == len(raw)
            and receipt["trace_identity"] == {
                "device": metadata.st_dev, "inode": metadata.st_ino,
            }
            and hashlib.sha256(raw[start:end]).hexdigest()
            == receipt["segment_sha256"]
        ):
            matches.add(state["closure_receipt_sha256"])
    if len(matches) != 1:
        raise ValueError("open-turn-count")
    return next(iter(matches))


def recover_previous_prompt(
    state_path, state, policy, policy_sha, hook_sha, completion_root,
    current_trace, current_boundary, now=None,
):
    root = Path(policy["roots"]["trace_claude"])
    previous_trace = root / state["trace_relative"]
    previous_trace = transcript_path({"transcript_path": str(previous_trace)}, policy)
    raw, metadata = read_transcript(previous_trace)
    end = current_boundary if previous_trace == current_trace else len(raw)
    return prepare_close(
        state_path, state, policy, policy_sha, hook_sha, completion_root,
        raw, metadata, end, "next-prompt-hook", "claude:prompt-hook", now=now,
    )


def close_stop(value, policy, policy_sha, hook_sha, open_root, completion_root, now=None):
    session_key = hashlib.sha256(value["session_id"].encode("utf-8")).hexdigest()
    candidates = [
        (path, state) for path, state in load_states(open_root, policy, policy_sha, hook_sha)
        if state["session_key"] == session_key and state["status"] in ("open", "closing")
    ]
    if len(candidates) != 1:
        raise ValueError("open-turn-count")
    state_path, state = candidates[0]
    if state["status"] == "closing":
        return finalize_pending(
            state_path, state, policy, completion_root, policy_sha, hook_sha,
        )
    path = transcript_path(value, policy)
    root = Path(policy["roots"]["trace_claude"])
    if path.relative_to(root).as_posix() != state["trace_relative"]:
        raise ValueError("transcript-mismatch")
    raw, metadata = read_transcript(path)
    return prepare_close(
        state_path, state, policy, policy_sha, hook_sha, completion_root,
        raw, metadata, len(raw), "stop-hook", "claude:stop-hook", now=now,
    )


def close_stop_across_generations(
    value, policy, policy_sha, hook_sha, open_base, ledger_root,
    completion_root, now=None,
):
    session_key = hashlib.sha256(value["session_id"].encode("utf-8")).hexdigest()
    records = generation_records(
        open_base, ledger_root, policy, policy_sha, hook_sha,
        session_key=session_key,
    )
    all_records = list(records)
    records = [
        record for record in records
        if record[3]["status"] in ("open", "closing")
        or record[3]["closure_receipt_sha256"] is not None
    ]
    active_keys = {
        (
            record[3]["session_key"], record[3]["trace_relative"],
            record[3]["start_offset"], record[3]["prompt_sha256"],
        )
        for record in records if record[3]["status"] in ("open", "closing")
    }
    if not active_keys:
        return closed_stop_retry(
            value, all_records, policy, completion_root, policy_sha, hook_sha,
        )
    records = [
        record for record in records
        if (
            record[3]["session_key"], record[3]["trace_relative"],
            record[3]["start_offset"], record[3]["prompt_sha256"],
        ) in active_keys
    ]
    groups = generation_groups(
        records, policy, completion_root, policy_sha, hook_sha,
    )
    if len(groups) != 1:
        raise ValueError("open-turn-count")
    group = groups[0]
    completed = [
        record for record in group
        if record[3]["status"] == "closed"
        and record[3]["closure_receipt_sha256"] is not None
    ]
    active = [
        record for record in group
        if record[3]["status"] in ("open", "closing")
    ]
    if completed:
        completed_receipt = validate_closed_completion(
            completed[0][3], policy, completion_root, policy_sha, hook_sha,
        )
        for _, _, duplicate_path, duplicate_state in active:
            if not acknowledge_pending_completion(
                duplicate_path, duplicate_state, completed_receipt,
            ):
                retire_open_state(
                    duplicate_path, duplicate_state, policy_sha, hook_sha,
                    "duplicate-completed-lifecycle",
                )
        return completed[0][3]["closure_receipt_sha256"]
    _, _, state_path, state = active[0]
    for _, _, duplicate_path, duplicate_state in active[1:]:
        retire_open_state(
            duplicate_path, duplicate_state, policy_sha, hook_sha,
            "duplicate-lifecycle-state",
        )
    if state["status"] == "closing":
        return finalize_pending(
            state_path, state, policy, completion_root, policy_sha, hook_sha,
        )
    path = transcript_path(value, policy)
    root = Path(policy["roots"]["trace_claude"])
    if path.relative_to(root).as_posix() != state["trace_relative"]:
        raise ValueError("transcript-mismatch")
    raw, metadata = read_transcript(path)
    return prepare_close(
        state_path, state, policy, policy_sha, hook_sha, completion_root,
        raw, metadata, len(raw), "stop-hook", "claude:stop-hook", now=now,
    )


def close_prior_generation_before_prompt(
    value, policy, policy_sha, hook_sha, open_base, current_root,
    ledger_root, completion_root, now=None,
):
    session_key = hashlib.sha256(value["session_id"].encode("utf-8")).hexdigest()
    path = transcript_path(value, policy)
    raw, _ = read_transcript(path, allow_missing=True)
    boundary, _, _ = structural_offsets(raw)
    records = generation_records(
        open_base, ledger_root, policy, policy_sha, hook_sha,
        session_key=session_key,
    )
    records = [
        record for record in records
        if record[3]["status"] in ("open", "closing")
        or record[3]["closure_receipt_sha256"] is not None
    ]
    active_keys = {
        (
            record[3]["session_key"], record[3]["trace_relative"],
            record[3]["start_offset"], record[3]["prompt_sha256"],
        )
        for record in records if record[3]["status"] in ("open", "closing")
    }
    records = [
        record for record in records
        if (
            record[3]["session_key"], record[3]["trace_relative"],
            record[3]["start_offset"], record[3]["prompt_sha256"],
        ) in active_keys
    ]
    groups = generation_groups(
        records, policy, completion_root, policy_sha, hook_sha,
    )
    prior_groups = [
        group for group in groups
        if any(record[1] != Path(current_root) for record in group)
    ]
    if len(prior_groups) > 1 or (prior_groups and len(groups) != 1):
        raise ValueError("prior-generation-open-turn-count")
    for group in prior_groups:
        completed = [
            record for record in group
            if record[3]["status"] == "closed"
            and record[3]["closure_receipt_sha256"] is not None
        ]
        active = [
            record for record in group
            if record[3]["status"] in ("open", "closing")
        ]
        if completed:
            completed_receipt = validate_closed_completion(
                completed[0][3], policy, completion_root,
                policy_sha, hook_sha,
            )
            for _, _, duplicate_path, duplicate_state in active:
                if not acknowledge_pending_completion(
                    duplicate_path, duplicate_state, completed_receipt,
                ):
                    retire_open_state(
                        duplicate_path, duplicate_state, policy_sha, hook_sha,
                        "duplicate-completed-lifecycle",
                    )
            continue
        _, _, state_path, state = active[0]
        for _, _, duplicate_path, duplicate_state in active[1:]:
            retire_open_state(
                duplicate_path, duplicate_state, policy_sha, hook_sha,
                "duplicate-lifecycle-state",
            )
        if state["status"] == "closing":
            finalize_pending(
                state_path, state, policy, completion_root,
                policy_sha, hook_sha,
            )
            continue
        try:
            recover_previous_prompt(
                state_path, state, policy, policy_sha, hook_sha,
                completion_root, path, boundary, now=now,
            )
        except SegmentAmbiguity:
            retire_open_state(
                state_path, state, policy_sha, hook_sha,
                "ambiguous-prior-generation-segment",
            )


def _switch_open_state(
    policy, policy_sha, hook_sha, open_root, path, state, session_key, zone,
    ledger_root, now=None,
):
    ledger_path, ledger, ledger_exists = load_session_ledger(
        ledger_root, session_key, now=now,
    )
    session_zone = state["initial_zone"]
    for transition in state["zone_transitions"]:
        if transition["scope"] == "session":
            session_zone = transition["to"]
    if not ledger_exists or session_zone != ledger["zone"]:
        raise ValueError("Claude session zone ledger differs from the open turn")
    current = state["zone_transitions"][-1]["to"] if state["zone_transitions"] else state["initial_zone"]
    if zone == current:
        if ledger["zone"] == zone:
            return path, False
        if not state["zone_transitions"]:
            raise ValueError("Claude session route cannot be promoted")
        prior_state = state
        state = dict(state)
        state["zone_transitions"] = list(state["zone_transitions"])
        state["zone_transitions"][-1] = {
            **state["zone_transitions"][-1],
            "at": iso_utc(now or utc_now()),
            "recorded_by": "claude:zone-switch",
            "scope": "session",
        }
    else:
        prior_state = state
        state = dict(state)
        state["zone_transitions"] = list(state["zone_transitions"]) + [{
            "at": iso_utc(now or utc_now()), "from": current, "to": zone,
            "recorded_by": "claude:zone-switch", "scope": "session",
        }]
    updated_ledger = dict(ledger)
    if updated_ledger["zone"] != zone:
        updated_ledger["zone"] = zone
        updated_ledger["revision"] += 1
        updated_ledger["updated_at"] = state["zone_transitions"][-1]["at"]
    commit_session_transaction(
        open_root, ledger_root, path, prior_state, state,
        ledger_path, ledger, updated_ledger,
        policy, policy_sha, hook_sha, recovery_action="rollback",
    )
    return path, True


def switch_open(
    policy, policy_sha, hook_sha, open_root, session_id, zone,
    ledger_root=None, now=None,
):
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    ledger_root = Path(ledger_root or (Path(open_root) / ".sessions"))
    recover_session_transactions(
        open_root, ledger_root, policy, policy_sha, hook_sha,
    )
    candidates = [
        (path, state) for path, state in load_states(open_root, policy, policy_sha, hook_sha)
        if state["status"] == "open" and state["session_key"] == session_key
    ]
    if len(candidates) != 1:
        raise ValueError("open-turn-count")
    path, state = candidates[0]
    return _switch_open_state(
        policy, policy_sha, hook_sha, open_root, path, state, session_key,
        zone, ledger_root, now=now,
    )


def switch_open_across_generations(
    policy, policy_sha, hook_sha, open_base, session_id, zone,
    ledger_root, now=None, completion_root=None,
):
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    records = generation_records(
        open_base, ledger_root, policy, policy_sha, hook_sha,
        session_key=session_key,
    )
    active_keys = {
        (
            record[3]["session_key"], record[3]["trace_relative"],
            record[3]["start_offset"], record[3]["prompt_sha256"],
        )
        for record in records if record[3]["status"] in ("open", "closing")
    }
    records = [
        record for record in records
        if (
            record[3]["session_key"], record[3]["trace_relative"],
            record[3]["start_offset"], record[3]["prompt_sha256"],
        ) in active_keys
        and (
            record[3]["status"] in ("open", "closing")
            or record[3]["closure_receipt_sha256"] is not None
        )
    ]
    groups = generation_groups(
        records, policy if completion_root is not None else None,
        completion_root, policy_sha, hook_sha,
    )
    if len(groups) != 1:
        raise ValueError("open-turn-count")
    group = groups[0]
    completed = [
        record for record in group
        if record[3]["status"] == "closed"
        and record[3]["closure_receipt_sha256"] is not None
    ]
    active = [
        record for record in group
        if record[3]["status"] in ("open", "closing")
    ]
    if completed:
        completed_receipt = validate_closed_completion(
            completed[0][3], policy, completion_root, policy_sha, hook_sha,
        )
        for _, _, duplicate_path, duplicate_state in active:
            if not acknowledge_pending_completion(
                duplicate_path, duplicate_state, completed_receipt,
            ):
                retire_open_state(
                    duplicate_path, duplicate_state, policy_sha, hook_sha,
                    "duplicate-completed-lifecycle",
                )
        raise ValueError("open-turn-count")
    if not active or active[0][3]["status"] != "open":
        raise ValueError("open-turn-count")
    _, open_root, path, state = active[0]
    for _, _, duplicate_path, duplicate_state in active[1:]:
        retire_open_state(
            duplicate_path, duplicate_state, policy_sha, hook_sha,
            "duplicate-lifecycle-state",
        )
    return _switch_open_state(
        policy, policy_sha, hook_sha, open_root, path, state, session_key,
        zone, ledger_root, now=now,
    )


def self_test():
    assert HOOK_LOCK_TTL_SECONDS >= 60
    settings_raw, _ = POLICY.read_regular_bytes(
        VAULT / ".claude/settings.json", "Claude settings",
    )
    settings = POLICY.strict_json_loads(settings_raw, "Claude settings")
    stop_command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "|| true" not in stop_command
    with tempfile.TemporaryDirectory(prefix="claude-stop-hook-test-") as directory:
        project = Path(directory)
        hook = project / ".vault-meta/evolution/claude-trace-hook.py"
        hook.parent.mkdir(parents=True)
        hook.write_text("raise SystemExit(2)\n", encoding="utf-8")
        result = subprocess.run(
            ["/bin/sh", "-c", stop_command],
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
            capture_output=True, text=True,
        )
        assert result.returncode == 2
    with tempfile.TemporaryDirectory(prefix="claude-trace-hook-test-") as directory:
        root = Path(os.path.realpath(directory))
        trace_root = root / "projects"
        trace_root.mkdir()
        transcript = trace_root / "session.jsonl"
        transcript.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "исправь"},
        }) + "\n", encoding="utf-8")
        policy = {
            "roots": {"trace_claude": str(trace_root)},
            "vault": {"id": "{{VAULT_ID}}", "path": str(VAULT)},
            "delegation_id": "test", "not_before": "2026-08-21T00:00:00Z",
        }
        base = {
            "session_id": "session", "transcript_path": str(transcript),
            "cwd": str(VAULT), "hook_event_name": "UserPromptSubmit",
        }
        value = {**base, "prompt": "исправь"}
        open_root, completion_root = root / "open", root / "complete"
        ledger_root = root / "sessions"
        now = parse_timestamp("2026-08-21T12:00:00Z")
        state_path, created = register_prompt(
            value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, ledger_root=ledger_root, now=now,
        )
        assert created and state_path.exists()
        _, duplicate = register_prompt(
            value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, ledger_root=ledger_root, now=now,
        )
        assert not duplicate
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
            handle.write(json.dumps({
                "type": "user", "message": {"role": "user", "content": "следующий ход"},
            }) + "\n")
        second_value = {**base, "prompt": "следующий ход"}
        second_path, second_created = register_prompt(
            second_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:00:30Z"),
        )
        assert second_created and second_path != state_path
        prior_raw, _ = POLICY.read_regular_bytes(state_path, "state", owner_private=True)
        prior = POLICY.strict_json_loads(prior_raw, "state")
        assert prior["status"] == "closed" and prior["closure_receipt_sha256"]
        recovered_raw, _ = POLICY.read_regular_bytes(
            completion_root / (prior["closure_receipt_sha256"] + ".json"),
            "receipt", owner_private=True,
        )
        recovered = POLICY.strict_json_loads(recovered_raw, "receipt")
        assert recovered["terminal_reason"] == "next-prompt-hook"
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово 2"},
            }) + "\n")
        receipt_sha = close_stop(
            second_value, policy, "a" * 64, code_sha256(), open_root, completion_root,
            parse_timestamp("2026-08-21T12:01:00Z"),
        )
        raw, _ = POLICY.read_regular_bytes(completion_root / (receipt_sha + ".json"), "receipt", owner_private=True)
        receipt = POLICY.strict_json_loads(raw, "receipt")
        assert receipt["eligible"] and receipt["terminal_reason"] == "stop-hook"
        assert receipt["prompt_sequence"] == 2

        tool_transcript = trace_root / "tool-session.jsonl"
        tool_transcript.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "проверь инструмент"},
        }) + "\n", encoding="utf-8")
        tool_value = {
            **base, "session_id": "tool-session",
            "transcript_path": str(tool_transcript), "prompt": "проверь инструмент",
        }
        tool_path, _ = register_prompt(
            tool_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:01:10Z"),
        )
        with tool_transcript.open("a", encoding="utf-8") as handle:
            for item in (
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {}},
                ]}},
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"},
                ]}},
                {"type": "assistant", "message": {"role": "assistant", "content": "готово"}},
            ):
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        tool_receipt = close_stop(
            tool_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, parse_timestamp("2026-08-21T12:01:20Z"),
        )
        assert tool_path.exists() and SHA256_RE.fullmatch(tool_receipt)

        def append_turn(prompt, answer, instant):
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "type": "user", "message": {"role": "user", "content": prompt},
                }) + "\n")
            turn_value = {**base, "prompt": prompt}
            turn_path, _ = register_prompt(
                turn_value, policy, "a" * 64, code_sha256(), open_root,
                completion_root, ledger_root=ledger_root,
                now=parse_timestamp(instant),
            )
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "type": "assistant", "message": {"role": "assistant", "content": answer},
                }) + "\n")
            return turn_value, turn_path

        one_off_value, one_off_path = append_turn(
            "личное: проверить", "личное готово", "2026-08-21T12:02:00Z",
        )
        one_off_sha = close_stop(
            one_off_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, parse_timestamp("2026-08-21T12:02:30Z"),
        )
        one_off_raw, _ = POLICY.read_regular_bytes(
            completion_root / (one_off_sha + ".json"), "receipt", owner_private=True,
        )
        assert not POLICY.strict_json_loads(one_off_raw, "receipt")["eligible"]

        switch_value, _ = append_turn(
            "перейди в личную область", "переключено", "2026-08-21T12:03:00Z",
        )
        close_stop(
            switch_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, parse_timestamp("2026-08-21T12:03:30Z"),
        )
        personal_value, personal_path = append_turn(
            "личная задача", "готово", "2026-08-21T12:04:00Z",
        )
        personal_raw, _ = POLICY.read_regular_bytes(
            personal_path, "state", owner_private=True,
        )
        personal_state = POLICY.strict_json_loads(personal_raw, "state")
        assert personal_state["initial_zone"] == "personal"
        assert not personal_state["zone_transitions"]
        personal_sha = close_stop(
            personal_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, parse_timestamp("2026-08-21T12:04:30Z"),
        )
        personal_receipt_raw, _ = POLICY.read_regular_bytes(
            completion_root / (personal_sha + ".json"), "receipt", owner_private=True,
        )
        assert not POLICY.strict_json_loads(personal_receipt_raw, "receipt")["eligible"]
        work_switch_value, _ = append_turn(
            "вернись в рабочую", "переключено", "2026-08-21T12:05:00Z",
        )
        close_stop(
            work_switch_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, parse_timestamp("2026-08-21T12:05:30Z"),
        )
        replay_value, replay_path = append_turn(
            "рабочая задача", "готово", "2026-08-21T12:06:00Z",
        )
        replay_raw, _ = POLICY.read_regular_bytes(replay_path, "state", owner_private=True)
        assert not POLICY.strict_json_loads(replay_raw, "state")["zone_transitions"]
        original_write = globals()["atomic_state_write"]
        failed = {"value": False}

        def fail_final_close(path, state):
            if state.get("status") == "closed" and not failed["value"]:
                failed["value"] = True
                raise OSError("injected-final-close")
            return original_write(path, state)

        globals()["atomic_state_write"] = fail_final_close
        try:
            try:
                close_stop(
                    replay_value, policy, "a" * 64, code_sha256(), open_root,
                    completion_root, parse_timestamp("2026-08-21T12:06:30Z"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("injected close failure was not observed")
        finally:
            globals()["atomic_state_write"] = original_write
        before_retry = sorted(completion_root.glob("*.json"))
        replay_sha = close_stop(
            replay_value, policy, "a" * 64, code_sha256(), open_root,
            completion_root, parse_timestamp("2026-08-21T12:07:30Z"),
        )
        assert sorted(completion_root.glob("*.json")) == before_retry
        replay_receipt_raw, _ = POLICY.read_regular_bytes(
            completion_root / (replay_sha + ".json"), "receipt", owner_private=True,
        )
        assert POLICY.strict_json_loads(replay_receipt_raw, "receipt")["completed_at"] == "2026-08-21T12:06:30Z"

        fault_trace = trace_root / "fault-session.jsonl"
        fault_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "исправь атомарно"},
        }) + "\n", encoding="utf-8")
        fault_value = {
            **base, "session_id": "fault-session",
            "transcript_path": str(fault_trace), "prompt": "исправь атомарно",
        }
        fault_open, fault_complete = root / "fault-open", root / "fault-complete"
        fault_ledger = root / "fault-ledger"
        original_private_write = POLICY.atomic_private_json_write
        injected = {"ledger": False}

        def fail_ledger_once(path, value, label="private JSON artifact"):
            if Path(path).parent == fault_ledger and not injected["ledger"]:
                injected["ledger"] = True
                raise OSError("injected-ledger-write")
            return original_private_write(path, value, label)

        POLICY.atomic_private_json_write = fail_ledger_once
        try:
            try:
                register_prompt(
                    fault_value, policy, "a" * 64, code_sha256(), fault_open,
                    fault_complete, ledger_root=fault_ledger,
                    now=parse_timestamp("2026-08-21T12:07:40Z"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("injected ledger failure was not observed")
        finally:
            POLICY.atomic_private_json_write = original_private_write
        fault_key = hashlib.sha256(b"fault-session").hexdigest()
        assert not (fault_ledger / (fault_key + ".json")).exists()
        assert (fault_open / ".transactions" / (fault_key + ".json")).exists()
        with fault_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
            handle.write(json.dumps({
                "type": "user", "message": {"role": "user", "content": "следующий"},
            }) + "\n")
        fault_next = {**fault_value, "prompt": "следующий"}
        _, fault_created = register_prompt(
            fault_next, policy, "a" * 64, code_sha256(), fault_open,
            fault_complete, ledger_root=fault_ledger,
            now=parse_timestamp("2026-08-21T12:07:50Z"),
        )
        _, fault_ledger_value, fault_ledger_exists = load_session_ledger(
            fault_ledger, fault_key,
        )
        assert fault_created and fault_ledger_exists
        assert fault_ledger_value["prompt_sequence"] == 2
        assert not (fault_open / ".transactions" / (fault_key + ".json")).exists()
        assert len(list(fault_complete.glob("*.json"))) == 1

        close_fault_trace = trace_root / "close-fault.jsonl"
        close_fault_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "сохрани точно"},
        }) + "\n", encoding="utf-8")
        close_fault_value = {
            **base, "session_id": "close-fault",
            "transcript_path": str(close_fault_trace), "prompt": "сохрани точно",
        }
        close_fault_open = root / "close-fault-open"
        close_fault_complete = root / "close-fault-complete"
        close_fault_ledger = root / "close-fault-ledger"
        prior_close_path, _ = register_prompt(
            close_fault_value, policy, "a" * 64, code_sha256(),
            close_fault_open, close_fault_complete,
            ledger_root=close_fault_ledger,
            now=parse_timestamp("2026-08-21T12:07:51Z"),
        )
        with close_fault_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "сохранено"},
            }) + "\n")
            handle.write(json.dumps({
                "type": "user", "message": {"role": "user", "content": "новый ход"},
            }) + "\n")
        close_fault_next = {**close_fault_value, "prompt": "новый ход"}
        original_state_write = globals()["atomic_state_write"]
        injected_close = {"value": False}

        def fail_closing_state_once(path, state):
            if state.get("status") == "closing" and not injected_close["value"]:
                injected_close["value"] = True
                raise OSError("injected-closing-state")
            return original_state_write(path, state)

        globals()["atomic_state_write"] = fail_closing_state_once
        try:
            try:
                register_prompt(
                    close_fault_next, policy, "a" * 64, code_sha256(),
                    close_fault_open, close_fault_complete,
                    ledger_root=close_fault_ledger,
                    now=parse_timestamp("2026-08-21T12:07:52Z"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("closing-state failure was not propagated")
        finally:
            globals()["atomic_state_write"] = original_state_write
        prior_close_raw, _ = POLICY.read_regular_bytes(
            prior_close_path, "prior close state", owner_private=True,
        )
        assert POLICY.strict_json_loads(prior_close_raw, "prior close state")["status"] == "open"

        original_publish = POLICY.publish_immutable
        injected_publish = {"value": False}

        def fail_completion_publish_once(destination, raw):
            if Path(destination) == close_fault_complete and not injected_publish["value"]:
                injected_publish["value"] = True
                raise OSError("injected-completion-publish")
            return original_publish(destination, raw)

        POLICY.publish_immutable = fail_completion_publish_once
        try:
            try:
                register_prompt(
                    close_fault_next, policy, "a" * 64, code_sha256(),
                    close_fault_open, close_fault_complete,
                    ledger_root=close_fault_ledger,
                    now=parse_timestamp("2026-08-21T12:07:53Z"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("completion publication failure was not propagated")
        finally:
            POLICY.publish_immutable = original_publish
        prior_close_raw, _ = POLICY.read_regular_bytes(
            prior_close_path, "prior closing state", owner_private=True,
        )
        assert POLICY.strict_json_loads(prior_close_raw, "prior closing state")["status"] == "closing"
        _, next_created = register_prompt(
            close_fault_next, policy, "a" * 64, code_sha256(),
            close_fault_open, close_fault_complete,
            ledger_root=close_fault_ledger,
            now=parse_timestamp("2026-08-21T12:07:54Z"),
        )
        prior_close_raw, _ = POLICY.read_regular_bytes(
            prior_close_path, "prior closed state", owner_private=True,
        )
        prior_closed = POLICY.strict_json_loads(prior_close_raw, "prior closed state")
        assert next_created and prior_closed["status"] == "closed"
        assert prior_closed["closure_receipt_sha256"]
        assert len(list(close_fault_complete.glob("*.json"))) == 1
        close_fault_recovery = close_fault_open / ".recovery"
        assert not close_fault_recovery.exists() or not list(
            close_fault_recovery.glob("*.json")
        )

        switch_trace = trace_root / "switch-fault.jsonl"
        switch_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "открыть ход"},
        }) + "\n", encoding="utf-8")
        switch_value = {
            **base, "session_id": "switch-fault",
            "transcript_path": str(switch_trace), "prompt": "открыть ход",
        }
        switch_open_root, switch_ledger_root = root / "switch-open", root / "switch-ledger"
        register_prompt(
            switch_value, policy, "a" * 64, code_sha256(), switch_open_root,
            root / "switch-complete", ledger_root=switch_ledger_root,
            now=parse_timestamp("2026-08-21T12:07:55Z"),
        )
        injected = {"ledger": False}

        def fail_switch_ledger_once(path, value, label="private JSON artifact"):
            if Path(path).parent == switch_ledger_root and not injected["ledger"]:
                injected["ledger"] = True
                raise OSError("injected-switch-ledger")
            return original_private_write(path, value, label)

        POLICY.atomic_private_json_write = fail_switch_ledger_once
        try:
            try:
                switch_open(
                    policy, "a" * 64, code_sha256(), switch_open_root,
                    "switch-fault", "personal", ledger_root=switch_ledger_root,
                    now=parse_timestamp("2026-08-21T12:07:56Z"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("injected switch failure was not observed")
        finally:
            POLICY.atomic_private_json_write = original_private_write
        _, switch_retry = switch_open(
            policy, "a" * 64, code_sha256(), switch_open_root,
            "switch-fault", "personal", ledger_root=switch_ledger_root,
            now=parse_timestamp("2026-08-21T12:07:57Z"),
        )
        switch_key = hashlib.sha256(b"switch-fault").hexdigest()
        _, switch_ledger, _ = load_session_ledger(switch_ledger_root, switch_key)
        assert switch_retry and switch_ledger["zone"] == "personal"

        injected = {"ledger": False}
        POLICY.atomic_private_json_write = fail_switch_ledger_once
        try:
            try:
                switch_open(
                    policy, "a" * 64, code_sha256(), switch_open_root,
                    "switch-fault", "work", ledger_root=switch_ledger_root,
                    now=parse_timestamp("2026-08-21T12:07:58Z"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("injected reverse switch failure was not observed")
        finally:
            POLICY.atomic_private_json_write = original_private_write
        with switch_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {
                    "role": "assistant", "content": "личный ответ",
                },
            }) + "\n")
            handle.write(json.dumps({
                "type": "user", "message": {
                    "role": "user", "content": "личный следующий",
                },
            }) + "\n")
        personal_next = {**switch_value, "prompt": "личный следующий"}
        personal_path, personal_created = register_prompt(
            personal_next, policy, "a" * 64, code_sha256(), switch_open_root,
            root / "switch-complete", ledger_root=switch_ledger_root,
            now=parse_timestamp("2026-08-21T12:07:59Z"),
        )
        personal_raw, _ = POLICY.read_regular_bytes(
            personal_path, "personal next state", owner_private=True,
        )
        personal_state = POLICY.strict_json_loads(personal_raw, "personal next state")
        _, switch_ledger, _ = load_session_ledger(switch_ledger_root, switch_key)
        assert personal_created and personal_state["initial_zone"] == "personal"
        assert not personal_state["zone_transitions"] and switch_ledger["zone"] == "personal"
        assert not list((switch_open_root / ".transactions").glob("*.json"))

        rollover_base = root / "rollover-open"
        rollover_ledger = root / "rollover-ledger"
        rollover_complete = root / "rollover-complete"
        POLICY.ensure_private_directory(rollover_base)
        rollover_policy = {
            **policy,
            "completion_predecessors": [{
                "policy_sha256": "a" * 64,
                "delegation_id": "old-test",
                "not_before": "2026-08-21T00:00:00Z",
                "claude_hook_sha256": "1" * 64,
                "codex_session_hook_sha256": None,
                "processed_sha256": None,
            }],
        }
        rollover_a_policy = {**policy, "delegation_id": "old-test"}
        generation_a = POLICY.generation_root(rollover_base, "a" * 64, "claude")
        generation_b = POLICY.generation_root(rollover_base, "b" * 64, "claude")
        rollover_trace = trace_root / "rollover-stop.jsonl"
        rollover_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "старый policy"},
        }) + "\n", encoding="utf-8")
        rollover_value = {
            **base, "session_id": "rollover-stop",
            "transcript_path": str(rollover_trace), "prompt": "старый policy",
        }
        rollover_state_path, _ = register_prompt(
            rollover_value, rollover_a_policy, "a" * 64, "1" * 64,
            generation_a, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:10Z"),
        )
        with rollover_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
        rollover_receipt_sha = close_stop_across_generations(
            rollover_value, rollover_policy, "b" * 64, "2" * 64,
            rollover_base, rollover_ledger, rollover_complete,
            now=parse_timestamp("2026-08-21T12:08:20Z"),
        )
        rollover_receipt_raw, _ = POLICY.read_regular_bytes(
            rollover_complete / (rollover_receipt_sha + ".json"),
            "rollover receipt", owner_private=True,
        )
        rollover_receipt = POLICY.strict_json_loads(
            rollover_receipt_raw, "rollover receipt",
        )
        assert rollover_receipt["policy_sha256"] == "b" * 64
        assert rollover_receipt["registration_policy_sha256"] == "a" * 64
        rollover_state_raw, _ = POLICY.read_regular_bytes(
            rollover_state_path, "rollover state", owner_private=True,
        )
        assert POLICY.strict_json_loads(rollover_state_raw, "rollover state")["status"] == "closed"

        rollover_switch_trace = trace_root / "rollover-switch.jsonl"
        rollover_switch_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "старый switch"},
        }) + "\n", encoding="utf-8")
        rollover_switch_value = {
            **base, "session_id": "rollover-switch",
            "transcript_path": str(rollover_switch_trace), "prompt": "старый switch",
        }
        rollover_switch_path, _ = register_prompt(
            rollover_switch_value, rollover_a_policy, "a" * 64, "1" * 64,
            generation_a, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:21Z"),
        )
        _, rollover_switched = switch_open_across_generations(
            rollover_policy, "b" * 64, "2" * 64, rollover_base,
            "rollover-switch", "personal", rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:22Z"),
        )
        assert rollover_switched
        rollover_switch_raw, _ = POLICY.read_regular_bytes(
            rollover_switch_path, "rollover switch state", owner_private=True,
        )
        rollover_switch_state = POLICY.strict_json_loads(
            rollover_switch_raw, "rollover switch state",
        )
        assert rollover_switch_state["policy_sha256"] == "a" * 64
        assert rollover_switch_state["zone_transitions"][0]["to"] == "personal"
        with rollover_switch_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
        rollover_switch_receipt_sha = close_stop_across_generations(
            rollover_switch_value, rollover_policy, "b" * 64, "2" * 64,
            rollover_base, rollover_ledger, rollover_complete,
            now=parse_timestamp("2026-08-21T12:08:23Z"),
        )
        rollover_switch_receipt_raw, _ = POLICY.read_regular_bytes(
            rollover_complete / (rollover_switch_receipt_sha + ".json"),
            "rollover switch receipt", owner_private=True,
        )
        rollover_switch_receipt = POLICY.strict_json_loads(
            rollover_switch_receipt_raw, "rollover switch receipt",
        )
        assert not rollover_switch_receipt["eligible"]
        assert rollover_switch_receipt["eligibility_reason"] == "zone-transition"
        assert rollover_switch_receipt["registration_policy_sha256"] == "a" * 64
        assert rollover_switch_receipt["policy_sha256"] == "b" * 64

        rollover_prompt_trace = trace_root / "rollover-prompt.jsonl"
        rollover_prompt_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "предыдущий ход"},
        }) + "\n", encoding="utf-8")
        rollover_prompt_value = {
            **base, "session_id": "rollover-prompt",
            "transcript_path": str(rollover_prompt_trace), "prompt": "предыдущий ход",
        }
        prior_generation_path, _ = register_prompt(
            rollover_prompt_value, rollover_a_policy, "a" * 64, "1" * 64,
            generation_a, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:30Z"),
        )
        with rollover_prompt_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "ответ"},
            }) + "\n")
            handle.write(json.dumps({
                "type": "user", "message": {"role": "user", "content": "новый policy ход"},
            }) + "\n")
        rollover_next = {**rollover_prompt_value, "prompt": "новый policy ход"}
        close_prior_generation_before_prompt(
            rollover_next, rollover_policy, "b" * 64, "2" * 64,
            rollover_base, generation_b, rollover_ledger, rollover_complete,
            now=parse_timestamp("2026-08-21T12:08:40Z"),
        )
        current_generation_path, current_generation_created = register_prompt(
            rollover_next, rollover_policy, "b" * 64, "2" * 64,
            generation_b, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:41Z"),
        )
        prior_generation_raw, _ = POLICY.read_regular_bytes(
            prior_generation_path, "prior generation state", owner_private=True,
        )
        current_generation_raw, _ = POLICY.read_regular_bytes(
            current_generation_path, "current generation state", owner_private=True,
        )
        assert current_generation_created
        assert POLICY.strict_json_loads(prior_generation_raw, "prior generation state")["status"] == "closed"
        assert POLICY.strict_json_loads(current_generation_raw, "current generation state")["policy_sha256"] == "b" * 64

        duplicate_trace = trace_root / "rollover-duplicate.jsonl"
        duplicate_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "один lifecycle"},
        }) + "\n", encoding="utf-8")
        duplicate_value = {
            **base, "session_id": "rollover-duplicate",
            "transcript_path": str(duplicate_trace), "prompt": "один lifecycle",
        }
        duplicate_a_path, _ = register_prompt(
            duplicate_value, rollover_a_policy, "a" * 64, "1" * 64,
            generation_a, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:42Z"),
        )
        duplicate_b_path, _ = register_prompt(
            duplicate_value, rollover_policy, "b" * 64, "2" * 64,
            generation_b, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:43Z"),
        )
        with duplicate_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
        duplicate_before = len(list(rollover_complete.glob("*.json")))
        close_stop_across_generations(
            duplicate_value, rollover_policy, "b" * 64, "2" * 64,
            rollover_base, rollover_ledger, rollover_complete,
            now=parse_timestamp("2026-08-21T12:08:44Z"),
        )
        assert len(list(rollover_complete.glob("*.json"))) == duplicate_before + 1
        for duplicate_path in (duplicate_a_path, duplicate_b_path):
            duplicate_raw, _ = POLICY.read_regular_bytes(
                duplicate_path, "closed duplicate state", owner_private=True,
            )
            assert POLICY.strict_json_loads(
                duplicate_raw, "closed duplicate state",
            )["status"] == "closed"

        conflict_trace = trace_root / "rollover-conflict.jsonl"
        conflict_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "конфликт lifecycle"},
        }) + "\n", encoding="utf-8")
        conflict_value = {
            **base, "session_id": "rollover-conflict",
            "transcript_path": str(conflict_trace), "prompt": "конфликт lifecycle",
        }
        conflict_a_path, _ = register_prompt(
            conflict_value, rollover_a_policy, "a" * 64, "1" * 64,
            generation_a, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:45Z"),
        )
        conflict_b_path, _ = register_prompt(
            conflict_value, rollover_policy, "b" * 64, "2" * 64,
            generation_b, rollover_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:46Z"),
        )
        conflict_b_raw, _ = POLICY.read_regular_bytes(
            conflict_b_path, "conflict B state", owner_private=True,
        )
        conflict_b_state = POLICY.strict_json_loads(
            conflict_b_raw, "conflict B state",
        )
        conflict_b_state = {
            **conflict_b_state,
            "zone_transitions": [{
                "at": "2026-08-21T12:08:46Z", "from": "work", "to": "personal",
                "recorded_by": "claude:prompt-hook", "scope": "turn",
            }],
        }
        atomic_state_write(conflict_b_path, conflict_b_state)
        with conflict_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
        conflict_before = len(list(rollover_complete.glob("*.json")))
        try:
            close_stop_across_generations(
                conflict_value, rollover_policy, "b" * 64, "2" * 64,
                rollover_base, rollover_ledger, rollover_complete,
                now=parse_timestamp("2026-08-21T12:08:47Z"),
            )
        except ValueError as error:
            assert str(error) == "conflicting Claude lifecycle state across policy generations"
        else:
            raise AssertionError("conflicting Claude lifecycle generations were accepted")
        assert len(list(rollover_complete.glob("*.json"))) == conflict_before

        untrusted_root = POLICY.generation_root(
            rollover_base, "c" * 64, "claude",
        )
        try:
            generation_records(
                rollover_base, rollover_ledger, rollover_policy,
                "b" * 64, "2" * 64,
            )
        except ValueError as error:
            assert str(error) == "untrusted reflection lifecycle generation"
        else:
            raise AssertionError("unpinned Claude lifecycle generation was accepted")
        untrusted_root.rmdir()
        untrusted_root.parent.rmdir()

        crash_trace = trace_root / "rollover-close-crash.jsonl"
        crash_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "exact receipt"},
        }) + "\n", encoding="utf-8")
        crash_value = {
            **base, "session_id": "rollover-close-crash",
            "transcript_path": str(crash_trace), "prompt": "exact receipt",
        }
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
            crash_completion_base, "a" * 64, "claude",
        )
        crash_b_complete = POLICY.generation_root(
            crash_completion_base, "b" * 64, "claude",
        )
        crash_state_path, _ = register_prompt(
            crash_value, crash_a_policy, "a" * 64, "1" * 64,
            generation_a, crash_a_complete, ledger_root=rollover_ledger,
            now=parse_timestamp("2026-08-21T12:08:48Z"),
        )
        with crash_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
        original_state_write = globals()["atomic_state_write"]
        injected_close = {"value": False}

        def fail_rollover_close_once(path, state):
            if path == crash_state_path and state.get("status") == "closed" and not injected_close["value"]:
                injected_close["value"] = True
                raise OSError("injected-post-publication-state-write")
            return original_state_write(path, state)

        globals()["atomic_state_write"] = fail_rollover_close_once
        try:
            try:
                close_stop(
                    crash_value, crash_a_policy, "a" * 64, "1" * 64,
                    generation_a, crash_a_complete,
                    now=parse_timestamp("2026-08-21T12:08:49Z"),
                )
            except OSError:
                pass
            else:
                raise AssertionError("post-publication Claude close failure was not observed")
        finally:
            globals()["atomic_state_write"] = original_state_write
        crash_state_raw, _ = POLICY.read_regular_bytes(
            crash_state_path, "closing crash state", owner_private=True,
        )
        assert POLICY.strict_json_loads(
            crash_state_raw, "closing crash state",
        )["status"] == "closing"
        assert len(list(crash_a_complete.glob("*.json"))) == 1
        for _ in range(2):
            try:
                switch_open_across_generations(
                    crash_policy, "b" * 64, "2" * 64, rollover_base,
                    "rollover-close-crash", "personal", rollover_ledger,
                    now=parse_timestamp("2026-08-21T12:08:50Z"),
                    completion_root=crash_b_complete,
                )
            except ValueError as error:
                assert str(error) == "open-turn-count"
            else:
                raise AssertionError("prepared Claude completion was switched")
        replaced = {"value": False}

        def replace_then_fail(path, state):
            original_state_write(path, state)
            if (
                path == crash_state_path and state.get("status") == "closed"
                and not replaced["value"]
            ):
                replaced["value"] = True
                raise ValueError("injected-post-replace-Claude-state")

        globals()["atomic_state_write"] = replace_then_fail
        try:
            crash_receipt_sha = close_stop_across_generations(
                crash_value, crash_policy, "b" * 64, "2" * 64,
                rollover_base, rollover_ledger, crash_b_complete,
                now=parse_timestamp("2026-08-21T12:08:50Z"),
            )
        finally:
            globals()["atomic_state_write"] = original_state_write
        assert close_stop_across_generations(
            crash_value, crash_policy, "b" * 64, "2" * 64,
            rollover_base, rollover_ledger, crash_b_complete,
            now=parse_timestamp("2026-08-21T12:08:50Z"),
        ) == crash_receipt_sha
        assert len(list(crash_a_complete.glob("*.json"))) == 1
        assert not list(crash_b_complete.glob("*.json"))

        crash_receipt_raw, _ = POLICY.read_regular_bytes(
            crash_a_complete / (crash_receipt_sha + ".json"),
            "Claude misplaced receipt fixture", owner_private=True,
        )
        POLICY.publish_immutable(crash_b_complete, crash_receipt_raw)
        closed_crash_raw, _ = POLICY.read_regular_bytes(
            crash_state_path, "Claude closed crash state", owner_private=True,
        )
        closed_crash_state = POLICY.strict_json_loads(
            closed_crash_raw, "Claude closed crash state",
        )
        try:
            validate_closed_completion(
                closed_crash_state, crash_policy, crash_b_complete,
                "b" * 64, "2" * 64,
            )
        except ValueError as error:
            assert str(error) == "closed Claude completion generation is invalid"
        else:
            raise AssertionError("misplaced Claude completion receipt was accepted")

        mixed_trace = trace_root / "mixed-scope.jsonl"
        mixed_trace.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "личное: временно"},
        }) + "\n", encoding="utf-8")
        mixed_value = {
            **base, "session_id": "mixed-scope",
            "transcript_path": str(mixed_trace), "prompt": "личное: временно",
        }
        mixed_open = root / "mixed-open"
        mixed_complete = root / "mixed-complete"
        mixed_ledger = root / "mixed-ledger"
        register_prompt(
            mixed_value, policy, "a" * 64, code_sha256(), mixed_open,
            mixed_complete, ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:08:51Z"),
        )
        _, mixed_promoted = switch_open(
            policy, "a" * 64, code_sha256(), mixed_open,
            "mixed-scope", "personal", ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:08:52Z"),
        )
        mixed_key = hashlib.sha256(b"mixed-scope").hexdigest()
        _, mixed_ledger_value, _ = load_session_ledger(mixed_ledger, mixed_key)
        assert mixed_promoted and mixed_ledger_value["zone"] == "personal"
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
        close_stop(
            mixed_value, policy, "a" * 64, code_sha256(), mixed_open,
            mixed_complete, now=parse_timestamp("2026-08-21T12:08:53Z"),
        )
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "user", "message": {"role": "user", "content": "обычный личный ход"},
            }) + "\n")
        mixed_next_value = {**mixed_value, "prompt": "обычный личный ход"}
        mixed_next_path, _ = register_prompt(
            mixed_next_value, policy, "a" * 64, code_sha256(), mixed_open,
            mixed_complete, ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:08:54Z"),
        )
        mixed_next_raw, _ = POLICY.read_regular_bytes(
            mixed_next_path, "mixed-scope next state", owner_private=True,
        )
        assert POLICY.strict_json_loads(
            mixed_next_raw, "mixed-scope next state",
        )["initial_zone"] == "personal"
        mixed_next_state = POLICY.strict_json_loads(
            mixed_next_raw, "mixed-scope next state",
        )
        mixed_next_state["zone_transitions"] = [{
            "at": "2026-08-21T12:08:55Z", "from": "personal", "to": "work",
            "recorded_by": "claude:prompt-hook", "scope": "turn",
        }]
        atomic_state_write(mixed_next_path, mixed_next_state)
        _, mixed_reverse_promoted = switch_open(
            policy, "a" * 64, code_sha256(), mixed_open,
            "mixed-scope", "work", ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:08:56Z"),
        )
        _, mixed_ledger_value, _ = load_session_ledger(mixed_ledger, mixed_key)
        assert mixed_reverse_promoted and mixed_ledger_value["zone"] == "work"
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "message": {"role": "assistant", "content": "готово"},
            }) + "\n")
        close_stop(
            mixed_next_value, policy, "a" * 64, code_sha256(), mixed_open,
            mixed_complete, now=parse_timestamp("2026-08-21T12:08:57Z"),
        )
        with mixed_trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "user", "message": {"role": "user", "content": "обычный рабочий ход"},
            }) + "\n")
        mixed_final_value = {**mixed_value, "prompt": "обычный рабочий ход"}
        mixed_final_path, _ = register_prompt(
            mixed_final_value, policy, "a" * 64, code_sha256(), mixed_open,
            mixed_complete, ledger_root=mixed_ledger,
            now=parse_timestamp("2026-08-21T12:08:58Z"),
        )
        mixed_final_raw, _ = POLICY.read_regular_bytes(
            mixed_final_path, "mixed-scope reverse state", owner_private=True,
        )
        mixed_final_state = POLICY.strict_json_loads(
            mixed_final_raw, "mixed-scope reverse state",
        )
        assert mixed_final_state["initial_zone"] == "work"
        assert not mixed_final_state["zone_transitions"]

        generation_switch, _ = append_turn(
            "перейди в личную область", "переключено", "2026-08-21T12:08:00Z",
        )
        close_stop(
            generation_switch, policy, "a" * 64, code_sha256(), open_root,
            completion_root, parse_timestamp("2026-08-21T12:08:30Z"),
        )
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "user", "message": {"role": "user", "content": "после policy"},
            }) + "\n")
        changed_policy_value = {**base, "prompt": "после policy"}
        changed_path, _ = register_prompt(
            changed_policy_value, policy, "b" * 64, code_sha256(),
            root / "open-b", root / "complete-b", ledger_root=ledger_root,
            now=parse_timestamp("2026-08-21T12:09:00Z"),
        )
        changed_raw, _ = POLICY.read_regular_bytes(
            changed_path, "state", owner_private=True,
        )
        changed_state = POLICY.strict_json_loads(changed_raw, "state")
        assert changed_state["initial_zone"] == "personal"
        assert changed_state["prompt_sequence"] == 9
        stale = dict(prior, session_key="c" * 64, turn_id="d" * 64, policy_sha256="f" * 64)
        atomic_state_write(
            open_root / state_filename(stale["session_key"], stale["turn_id"]), stale,
        )
        assert all(
            state["policy_sha256"] == "a" * 64
            for _, state in load_states(open_root, policy, "a" * 64, code_sha256())
        )
    print("claude-trace-hook self-test: PASS")


DEGRADED_LOG = HERE / "lifecycle-degraded.log"


def report_degraded(command, message):
    """Record a lifecycle failure without blocking the owner's turn.

    A non-zero UserPromptSubmit exit discards the prompt outright, which locks the
    owner out of the very session that would repair the fault. Nothing false is
    written either way, so log the failure, hand it to Claude as context and let the
    turn through. SessionStart health-check reports the same fault on every launch.
    """
    try:
        with DEGRADED_LOG.open("a", encoding="utf-8") as handle:
            handle.write("%s\t%s\t%s\n" % (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                command, message,
            ))
    except OSError:
        pass
    print(message, file=sys.stderr)
    if command == "prompt-start":
        print(
            "\u26a0\ufe0f Claude memory lifecycle is degraded and this turn is not "
            "being recorded: %s\nRepair with `/usr/bin/python3 "
            ".vault-meta/evolution/reseal-policy.py --check` then `--apply`, and "
            "rebuild the zone index." % message,
        )
    return 0


def hook_main(command):
    token = None
    route = None
    try:
        value = read_hook_input(sys.stdin)
        if value.get("hook_event_name") != ("UserPromptSubmit" if command == "prompt-start" else "Stop"):
            return 0
        if command == "prompt-start":
            if not isinstance(value.get("prompt"), str):
                return 0
            route = prompt_route(value)
        policy, policy_sha = POLICY.load_policy()
        token = acquire_lock()
        if token is None:
            return report_degraded(
                command, "Claude memory lifecycle lock is unavailable.",
            )
        hook_sha = code_sha256()
        open_base = POLICY.ensure_private_directory(
            VAULT / policy["roots"]["open_work"],
        )
        open_root = POLICY.generation_root(
            open_base, policy_sha, "claude",
        )
        ledger_root = POLICY.ensure_private_directory(
            VAULT / policy["roots"]["open_work"] / ".sessions" / "claude",
        )
        completion_root = POLICY.generation_root(
            VAULT / policy["roots"]["completion_work"], policy_sha, "claude",
        )
        if command == "prompt-start":
            close_prior_generation_before_prompt(
                value, policy, policy_sha, hook_sha, open_base, open_root,
                ledger_root, completion_root,
            )
            register_prompt(
                value, policy, policy_sha, hook_sha, open_root,
                completion_root, ledger_root=ledger_root,
            )
        else:
            close_stop_across_generations(
                value, policy, policy_sha, hook_sha, open_base, ledger_root,
                completion_root,
            )
    except Exception as error:
        return report_degraded(
            command,
            "Claude memory lifecycle was not durably recorded: %s" % str(error)[:120],
        )
    finally:
        release_lock(token)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", choices=("prompt-start", "stop", "switch"))
    parser.add_argument("--zone", choices=("work", "personal", "client"))
    parser.add_argument("--session-id")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.command in ("prompt-start", "stop"):
        return hook_main(args.command)
    if args.command == "switch" and args.zone and args.session_id:
        token = None
        try:
            policy, policy_sha = POLICY.load_policy()
            token = acquire_lock()
            if token is None:
                parser.error("work lifecycle lock is unavailable")
            open_base = POLICY.ensure_private_directory(
                VAULT / policy["roots"]["open_work"],
            )
            completion_root = POLICY.generation_root(
                VAULT / policy["roots"]["completion_work"],
                policy_sha, "claude",
            )
            switch_open_across_generations(
                policy, policy_sha, code_sha256(), open_base,
                args.session_id, args.zone,
                POLICY.ensure_private_directory(
                    open_base / ".sessions" / "claude",
                ),
                completion_root=completion_root,
            )
        except Exception as error:
            parser.error(str(error))
        finally:
            release_lock(token)
        return 0
    parser.error("choose a hook command or --self-test")


if __name__ == "__main__":
    raise SystemExit(main())
