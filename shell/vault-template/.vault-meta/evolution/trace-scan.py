#!/usr/bin/env python3
"""Scan only policy-authorized, structurally completed work-turn segments."""

import argparse
import collections
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
POLICY_SPEC = importlib.util.spec_from_file_location(
    "reflection_policy", HERE / "reflection_policy.py",
)
POLICY = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(POLICY)
SELECTOR_SPEC = importlib.util.spec_from_file_location(
    "trace_manifest", HERE / "trace-manifest.py",
)
SELECTOR = importlib.util.module_from_spec(SELECTOR_SPEC)
SELECTOR_SPEC.loader.exec_module(SELECTOR)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
LOCK_PATH = ".vault-meta/write/work"

FRICTION = [
    (re.compile(r"я же (говорил|сказал|просил|писал)", re.I), "repeat-instruction"),
    (re.compile(r"\b(не так|не туда|не то\b|неверно|неправиль|не надо|не нужно|не должен)", re.I), "correction"),
    (re.compile(r"\b(стоп|погоди|подожди|отставить)\b", re.I), "halt"),
    (re.compile(r"\b(верни|откати|отмени|убери это|не делай)\b", re.I), "revert"),
    (re.compile(r"(зачем|почему) ты\b", re.I), "challenge"),
    (re.compile(r"не (критик|правильно понял|так понял|то понял|понял)", re.I), "misunderstanding"),
    (re.compile(r"\b(no,|not like that|revert|undo that|that.s wrong|stop,|don.t do)\b", re.I), "en-correction"),
]
SKIP_PREFIXES = ("<local-command", "<command-name>", "<system-reminder", "<bash-", "caveat:")
STOP = set("""это как для при был что чтобы если когда надо нужно можно вот там тут еще ещё уже
или она они оно его ему этот эта эти тот все всё так this that with have from your will just about
which them then here there what when make need into должен также очень теперь можешь давай сделай
сделать делать посмотри image cache source users owner codex node http https www conversation
save concept title skip naming duplicate exists usage workflow offer update valuable specific
explicitly creating session content analyze full same name note already read summary being continued
command местами хочу think work zone task""".split())
TERM = re.compile(r"[a-zа-яё0-9]{4,}", re.I)


def check_lock(token):
    if not isinstance(token, str) or not SHA256_RE.fullmatch(token):
        raise ValueError("writer-lock")
    result = subprocess.run(
        ["bash", "scripts/wiki-lock.sh", "check", LOCK_PATH, token],
        cwd=VAULT, capture_output=True, text=True,
    )
    if result.returncode or result.stdout.strip() != "owned":
        raise ValueError("writer-lock")


def code_sha256(path):
    raw, _ = POLICY.read_regular_bytes(path, "reflection code")
    return hashlib.sha256(raw).hexdigest()


def real_user_text(item, platform):
    if platform == "codex":
        if item.get("type") != "event_msg":
            return None
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "user_message":
            return None
        text = payload.get("message")
        if not isinstance(text, str):
            return None
    else:
        if item.get("type") != "user":
            return None
        message = item.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            return None
        kind, text = POLICY.claude_user_message(message)
        if kind == "invalid":
            raise ValueError("trace-stop-shape")
        if kind == "continuation":
            return None
    text = " ".join(text.split()).strip()
    if not text:
        return None
    low = text[:80].lower()
    if any(low.startswith(prefix) or prefix in low for prefix in SKIP_PREFIXES):
        return None
    if "<command-name>" in low or "local-command-" in low:
        return None
    if "this session is being continued" in low or "base directory for this skill" in low:
        return None
    return text


def trace_path(record, policy):
    root = Path(policy["roots"]["trace_" + record["platform"]])
    relative = Path(str(record.get("trace_relative", "")))
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".jsonl":
        raise ValueError("trace-path")
    candidate = Path(os.path.abspath(str(root / relative)))
    try:
        if candidate.resolve(strict=True) != candidate:
            raise ValueError("trace-path")
        candidate.relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError("trace-path") from error
    return candidate


def read_segment(record, policy, analyze=True):
    candidate = trace_path(record, policy)
    descriptor = os.open(str(candidate), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        expected_identity = record["trace_identity"]
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_nlink != 1
            or before.st_dev != expected_identity["device"]
            or before.st_ino != expected_identity["inode"]
        ):
            raise ValueError("trace-identity")
        start, size = record["segment_start"], record["segment_size"]
        if before.st_size < record["segment_end"]:
            raise ValueError("trace-truncated")
        segment = os.pread(descriptor, size, start)
        after = os.fstat(descriptor)
        if POLICY.file_identity(before, include_size=False) != POLICY.file_identity(after, include_size=False):
            raise ValueError("trace-identity-drift")
    finally:
        os.close(descriptor)
    if len(segment) != size or hashlib.sha256(segment).hexdigest() != record["segment_sha256"]:
        raise ValueError("trace-segment-drift")

    parsed = []
    for raw_line in segment.splitlines(keepends=True):
        try:
            parsed.append(POLICY.strict_json_loads(raw_line, "trace segment line"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("trace-segment-json") from error
    if not parsed:
        raise ValueError("trace-segment-empty")
    if record["platform"] == "codex":
        first_payload = parsed[0].get("payload") if isinstance(parsed[0], dict) else None
        last_payload = parsed[-1].get("payload") if isinstance(parsed[-1], dict) else None
        if (
            parsed[0].get("type") != "event_msg" or not isinstance(first_payload, dict)
            or first_payload.get("type") != "task_started"
            or first_payload.get("turn_id") != record["turn_id"]
            or parsed[-1].get("type") != "event_msg" or not isinstance(last_payload, dict)
            or last_payload.get("type") != "task_complete"
            or last_payload.get("turn_id") != record["turn_id"]
        ):
            raise ValueError("trace-terminal-markers")
        starts = [item for item in parsed if isinstance(item, dict) and item.get("type") == "event_msg"
                  and isinstance(item.get("payload"), dict) and item["payload"].get("type") == "task_started"]
        completes = [item for item in parsed if isinstance(item, dict) and item.get("type") == "event_msg"
                     and isinstance(item.get("payload"), dict) and item["payload"].get("type") == "task_complete"]
        if len(starts) != 1 or len(completes) != 1:
            raise ValueError("trace-terminal-markers")
    else:
        user_hashes = []
        assistant_after_user = False
        for item in parsed:
            message = item.get("message") if isinstance(item, dict) else None
            role = message.get("role") if isinstance(message, dict) else None
            if item.get("type") == "user" and role == "user":
                kind, text = POLICY.claude_user_message(message)
                if kind == "invalid":
                    raise ValueError("trace-stop-shape")
                if kind == "continuation":
                    continue
                user_hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
            elif item.get("type") == "assistant" and role == "assistant" and user_hashes:
                assistant_after_user = True
        if user_hashes != [record.get("prompt_sha256")] or not assistant_after_user:
            raise ValueError("trace-stop-shape")
    if not analyze:
        return {"record": record, "signals": [], "terms": set()}

    signals = []
    words = []
    message_ordinal = 0
    for item in parsed:
        text = real_user_text(item, record["platform"]) if isinstance(item, dict) else None
        if not text:
            continue
        message_ordinal += 1
        message_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for pattern, kind in FRICTION:
            if pattern.search(text):
                signal_id = hashlib.sha256((
                    record["receipt_sha256"] + "\0" + str(message_ordinal)
                    + "\0" + kind + "\0" + message_sha
                ).encode("utf-8")).hexdigest()
                signals.append({
                    "signal_id": signal_id,
                    "kind": kind,
                    "quote": text if len(text) <= 200 else text[:197] + "...",
                    "message_ordinal": message_ordinal,
                    "message_sha256": message_sha,
                    "effective_memory_level": "l0",
                    "allowed_suggested_target_levels": ["l1", "l2"],
                    "ambiguous_target": "l2",
                    "ambiguous_trust": "provisional-synthesis",
                    "promotion_eligible_when_ambiguous": False,
                    "autonomous_disposition_allowed": True,
                    "l3_eligible": False,
                })
                break
        normalized = re.sub(r"https?://\S+", " ", text.lower())
        words.extend(word for word in TERM.findall(normalized) if word not in STOP and not word.isdigit())
    terms = set(word for word in words if len(word) >= 4)
    terms.update(
        "%s %s" % (words[index], words[index + 1])
        for index in range(len(words) - 1)
        if words[index] not in STOP and words[index + 1] not in STOP
    )
    return {"record": record, "signals": signals, "terms": terms}


def catalog_roots(policy, policy_sha, platform):
    return (
        POLICY.generation_root(
            VAULT / policy["roots"]["completion_work"], policy_sha, platform,
            create=False,
        ),
        POLICY.generation_root(
            VAULT / policy["roots"]["scan_receipt_work"], policy_sha, platform,
            create=False,
        ),
        POLICY.generation_root(
            VAULT / policy["roots"]["manifest_work"], policy_sha, platform,
            create=False,
        ),
    )


def load_manifest(path, policy, policy_sha, platform):
    completion_root, scan_root, manifest_root = catalog_roots(
        policy, policy_sha, platform,
    )
    candidate, digest, value = SELECTOR.load_manifest_artifact(
        path, manifest_root, policy, policy_sha, platform, completion_root,
    )
    processed = SELECTOR.read_processed_manifests(
        scan_root, manifest_root, completion_root, policy, policy_sha, platform,
    )
    if digest not in processed:
        cutoff = SELECTOR.parse_timestamp(value["selection_cutoff"], "selection_cutoff")
        expected, no_op = SELECTOR.compute_selection(
            policy, policy_sha, cutoff, completion_root, scan_root,
            manifest_root, platform,
        )
        if no_op is not None or expected != value:
            raise ValueError("manifest-selection-drift")
    return candidate, digest, value, value["completion_receipts"]


def repeat_terms(snapshots, minimum):
    counts = collections.Counter()
    for snapshot in snapshots:
        counts.update(snapshot["terms"])
    result = [(term, count) for term, count in counts.items() if count >= minimum]
    return sorted(result, key=lambda item: (-(item[0].count(" ")), -item[1], item[0]))


def make_scan_receipt(manifest_sha, manifest, snapshots, skipped, signal_ids, verified_at):
    return {
        "schema_version": 1,
        "receipt_type": "reflection-scan",
        "status": "completed",
        "platform": manifest["platform"],
        "zone": "work",
        "manifest_sha256": manifest_sha,
        "policy_sha256": manifest["policy_sha256"],
        "delegation_id": manifest["delegation_id"],
        "scanner_sha256": manifest["scanner_sha256"],
        "selector_sha256": manifest["selector_sha256"],
        "parameters": manifest["parameters"],
        "completion_receipt_sha256s": [record["receipt_sha256"] for record in manifest["completion_receipts"]],
        "segments": [
            {
                "receipt_sha256": snapshot["record"]["receipt_sha256"],
                "segment_sha256": snapshot["record"]["segment_sha256"],
                "signal_count": len(snapshot["signals"]),
            }
            for snapshot in snapshots
        ],
        "skipped_segments": skipped,
        "signal_count": len(signal_ids),
        "signal_set_sha256": hashlib.sha256(POLICY.canonical_json_bytes(signal_ids)).hexdigest(),
        "verified_at": verified_at,
    }


def validate_scan_receipt(
    value, receipt_sha, manifest_sha, manifest, policy, policy_sha, platform,
):
    return SELECTOR.validate_scan_receipt(
        value, receipt_sha, manifest_sha, manifest,
        policy, policy_sha, platform,
    )


def load_scan_receipt(root, receipt_sha, manifest_sha, manifest, policy, policy_sha, platform):
    if not SHA256_RE.fullmatch(str(receipt_sha)):
        raise ValueError("scan-receipt-path")
    root = POLICY.validate_private_directory(root)
    path = root / (receipt_sha + ".json")
    raw, _ = POLICY.read_regular_bytes(path, "scan receipt", owner_private=True)
    if hashlib.sha256(raw).hexdigest() != receipt_sha:
        raise ValueError("scan-receipt-content-address")
    value = POLICY.strict_json_loads(raw, "scan receipt")
    return path, validate_scan_receipt(
        value, receipt_sha, manifest_sha, manifest, policy, policy_sha, platform,
    )


def lock_catalog(root):
    root = POLICY.ensure_private_directory(root)
    descriptor = os.open(
        str(root / ".catalog.lock"),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
    ):
        os.close(descriptor)
        raise ValueError("scan-catalog-lock")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def unlock_catalog(descriptor):
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def find_existing_receipt(root, manifest_sha, expected):
    root = POLICY.ensure_private_directory(root)
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        raw, _ = POLICY.read_regular_bytes(path, "scan receipt", owner_private=True)
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise ValueError("scan-receipt-content-address")
        value = POLICY.strict_json_loads(raw, "scan receipt")
        if value.get("manifest_sha256") == manifest_sha:
            comparable = dict(value)
            comparable["verified_at"] = expected["verified_at"]
            if comparable != expected:
                raise ValueError("scan-receipt-replay-drift")
            return path, path.stem
        prior = value.get("completion_receipt_sha256s")
        current = expected.get("completion_receipt_sha256s")
        if (
            value.get("platform") == expected.get("platform")
            and isinstance(prior, list) and isinstance(current, list)
            and set(prior).intersection(current)
        ):
            raise ValueError("scan-receipt-overlap")
    return None, None


def self_test():
    with tempfile.TemporaryDirectory(prefix="trace-scan-test-") as directory:
        root = Path(os.path.realpath(directory))
        trace_root = root / "sessions"
        trace_root.mkdir()
        turn_id = "22222222-2222-2222-2222-222222222222"
        lines = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id, "started_at": "2026-08-21T12:00:00Z"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "не так, исправь"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn_id, "completed_at": "2026-08-21T12:01:00Z"}},
        ]
        segment = b"".join(json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n" for item in lines)
        trace = trace_root / "turn.jsonl"
        trace.write_bytes(segment)
        metadata = trace.stat()
        record = {
            "receipt_sha256": "a" * 64, "platform": "codex",
            "session_id": "11111111-1111-1111-1111-111111111111",
            "turn_id": turn_id, "trace_relative": "turn.jsonl",
            "trace_identity": {"device": metadata.st_dev, "inode": metadata.st_ino},
            "segment_start": 0, "segment_end": len(segment), "segment_size": len(segment),
            "segment_sha256": hashlib.sha256(segment).hexdigest(), "session_meta_sha256": "b" * 64,
            "task_started_at": "2026-08-21T12:00:00Z", "completed_at": "2026-08-21T12:01:00Z",
        }
        snapshot = read_segment(record, {"roots": {"trace_codex": str(trace_root)}})
        assert snapshot["signals"][0]["kind"] == "correction"
        with trace.open("ab") as handle:
            handle.write(b'{"type":"event_msg"}\n')
        assert read_segment(record, {"roots": {"trace_codex": str(trace_root)}})["signals"][0]["signal_id"] == snapshot["signals"][0]["signal_id"]
        trace.write_bytes(b"x" + segment[1:])
        try:
            read_segment(record, {"roots": {"trace_codex": str(trace_root)}})
        except ValueError as error:
            assert str(error) == "trace-segment-drift"
        else:
            raise AssertionError("mutated completed segment was accepted")

        claude_lines = [
            {"type": "user", "message": {"role": "user", "content": "рабочий вопрос"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "ответ"}},
        ]
        claude_segment = b"".join(
            json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n"
            for item in claude_lines
        )
        claude_trace = trace_root / "claude.jsonl"
        claude_trace.write_bytes(claude_segment)
        claude_meta = claude_trace.stat()
        claude_record = {
            **record,
            "platform": "claude", "session_id": "c" * 64, "turn_id": "d" * 64,
            "trace_relative": "claude.jsonl",
            "trace_identity": {"device": claude_meta.st_dev, "inode": claude_meta.st_ino},
            "segment_end": len(claude_segment), "segment_size": len(claude_segment),
            "segment_sha256": hashlib.sha256(claude_segment).hexdigest(),
            "prompt_sha256": hashlib.sha256("рабочий вопрос".encode("utf-8")).hexdigest(),
            "prompt_sequence": 1,
        }
        assert read_segment(
            claude_record, {"roots": {"trace_claude": str(trace_root)}},
        )["record"] == claude_record
        tool_lines = [
            {"type": "user", "message": {"role": "user", "content": "рабочий вопрос"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {}},
            ]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"},
            ]}},
            {"type": "assistant", "message": {"role": "assistant", "content": "ответ"}},
        ]
        tool_segment = b"".join(
            json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n"
            for item in tool_lines
        )
        claude_trace.write_bytes(tool_segment)
        tool_meta = claude_trace.stat()
        tool_record = {
            **claude_record,
            "trace_identity": {"device": tool_meta.st_dev, "inode": tool_meta.st_ino},
            "segment_end": len(tool_segment), "segment_size": len(tool_segment),
            "segment_sha256": hashlib.sha256(tool_segment).hexdigest(),
        }
        assert read_segment(
            tool_record, {"roots": {"trace_claude": str(trace_root)}},
        )["record"] == tool_record
        mixed_segment = claude_segment + json.dumps({
            "type": "user", "message": {"role": "user", "content": "личное: не сканировать"},
        }, ensure_ascii=False).encode("utf-8") + b"\n"
        claude_trace.write_bytes(mixed_segment)
        mixed_record = {
            **claude_record,
            "segment_end": len(mixed_segment), "segment_size": len(mixed_segment),
            "segment_sha256": hashlib.sha256(mixed_segment).hexdigest(),
        }
        try:
            read_segment(
                mixed_record, {"roots": {"trace_claude": str(trace_root)}},
            )
        except ValueError as error:
            assert str(error) == "trace-stop-shape"
        else:
            raise AssertionError("mixed Claude prompts were accepted")
        manifest = {
            "policy_sha256": "c" * 64, "delegation_id": "test",
            "scanner_sha256": "d" * 64, "selector_sha256": "e" * 64,
            "parameters": {}, "completion_receipts": [record], "platform": "codex",
        }
        receipt = make_scan_receipt(
            "f" * 64, manifest, [snapshot], [],
            [snapshot["signals"][0]["signal_id"]], "2026-08-21T12:02:00Z",
        )
        assert receipt["signal_count"] == 1 and receipt["segments"][0]["signal_count"] == 1
        scan_root = root / "scan"
        receipt_path, receipt_sha, _ = POLICY.publish_immutable(
            scan_root, POLICY.canonical_json_bytes(receipt),
        )
        found_path, found_sha = find_existing_receipt(
            scan_root, "f" * 64, {**receipt, "verified_at": "2026-08-21T12:03:00Z"},
        )
        assert found_path == receipt_path and found_sha == receipt_sha
        try:
            find_existing_receipt(
                scan_root, "1" * 64,
                {**receipt, "manifest_sha256": "1" * 64,
                 "verified_at": "2026-08-21T12:04:00Z"},
            )
        except ValueError as error:
            assert str(error) == "scan-receipt-overlap"
        else:
            raise AssertionError("overlapping manifest was accepted")
    print("trace-scan self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("claude", "codex"), required=False)
    parser.add_argument("--zone", choices=("work",))
    parser.add_argument("--manifest")
    parser.add_argument("--repeats", action="store_true")
    parser.add_argument("--min-sessions", type=int, default=3)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--publish-receipt", action="store_true")
    parser.add_argument("--lock-token")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.zone or not args.manifest:
        parser.error("--zone and --manifest are required")
    if args.publish_receipt and (args.verify_only or not args.lock_token):
        parser.error("receipt publication requires a normal scan and --lock-token")
    if args.min_sessions < 1:
        parser.error("--min-sessions must be positive")
    try:
        policy, policy_sha = POLICY.load_policy()
        platform = args.platform or "claude"
        manifest_path, manifest_sha, manifest, records = load_manifest(
            args.manifest, policy, policy_sha, platform,
        )
        snapshots = []
        skipped = []
        for record in records:
            try:
                snapshots.append(read_segment(record, policy, analyze=not args.verify_only))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                reason = str(error)
                if not reason.startswith("trace-"):
                    reason = "trace-unreadable"
                skipped.append({"receipt_id": record["receipt_sha256"], "reason_code": reason})
        if skipped or len(snapshots) != len(records):
            result = {
                "schema_version": 1,
                "mode": "verify-only" if args.verify_only else "autonomous-proposal-only",
                "status": "incomplete",
                "platform": platform,
                "zone": "work",
                "manifest_sha256": manifest_sha,
                "policy_sha256": policy_sha,
                "verified_segments": len(snapshots),
                "skipped_segments": skipped,
                "segment_sha256s": [
                    item["record"]["segment_sha256"] for item in snapshots
                ],
            }
            result["receipt_sha256"] = hashlib.sha256(
                POLICY.canonical_json_bytes(result)
            ).hexdigest()
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 2
        if args.verify_only:
            result = {
                "schema_version": 1, "mode": "verify-only", "status": "verified",
                "platform": platform, "zone": "work", "manifest_sha256": manifest_sha,
                "policy_sha256": policy_sha, "verified_segments": len(snapshots),
                "skipped_segments": skipped,
                "segment_sha256s": [item["record"]["segment_sha256"] for item in snapshots],
            }
            result["receipt_sha256"] = hashlib.sha256(POLICY.canonical_json_bytes(result)).hexdigest()
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else "autonomous reflection verify-only: PASS")
            return 0

        signal_ids = sorted(signal["signal_id"] for item in snapshots for signal in item["signals"])
        verified_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        receipt = make_scan_receipt(manifest_sha, manifest, snapshots, skipped, signal_ids, verified_at)
        receipt_path = None
        receipt_sha = hashlib.sha256(POLICY.canonical_json_bytes(receipt)).hexdigest()
        if args.publish_receipt:
            check_lock(args.lock_token)
            _, scan_root, _ = catalog_roots(policy, policy_sha, platform)
            catalog_lock = lock_catalog(scan_root)
            try:
                check_lock(args.lock_token)
                existing_path, existing_sha = find_existing_receipt(
                    scan_root, manifest_sha, receipt,
                )
                if existing_path is not None:
                    receipt_path, receipt_sha = existing_path, existing_sha
                else:
                    check_lock(args.lock_token)
                    receipt_path, receipt_sha, _ = POLICY.publish_immutable(
                        scan_root, POLICY.canonical_json_bytes(receipt),
                    )
            finally:
                unlock_catalog(catalog_lock)
            if receipt["signal_count"] == 0:
                check_lock(args.lock_token)
                _, published_scan = load_scan_receipt(
                    scan_root, receipt_sha, manifest_sha, manifest,
                    policy, policy_sha, platform,
                )
                marker = SELECTOR.PROCESSED.build_marker(
                    policy, policy_sha, platform, manifest, published_scan,
                    receipt_sha,
                )
                check_lock(args.lock_token)
                SELECTOR.PROCESSED.publish_marker(policy, policy_sha, marker)
                check_lock(args.lock_token)
        result = {
            "schema_version": 3, "mode": "autonomous-proposal-only",
            "proposal_only": True, "platform": platform, "zone": "work",
            "delegation_id": policy["delegation_id"], "policy_sha256": policy_sha,
            "manifest_sha256": manifest_sha, "manifest": manifest_path.name,
            "effective_memory_level": "l0", "allowed_suggested_target_levels": ["l1", "l2"],
            "ambiguous_target": "l2", "ambiguous_trust": "provisional-synthesis",
            "promotion_eligible_when_ambiguous": False, "l3_automatic": False,
            "sessions_scanned": len(snapshots), "skipped_segments": skipped,
            "signal_count": len(signal_ids),
            "sessions": [
                {"session_id": item["record"]["session_id"], "turn_id": item["record"]["turn_id"],
                 "receipt_sha256": item["record"]["receipt_sha256"], "signals": item["signals"]}
                for item in snapshots if item["signals"]
            ],
            "scan_receipt_sha256": receipt_sha,
            "scan_receipt_published": receipt_path is not None,
        }
        if args.repeats:
            result["repeats"] = [
                {"term": term, "sessions": count}
                for term, count in repeat_terms(snapshots, args.min_sessions)
            ]
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        reason = str(error)
        if not re.fullmatch(r"[a-z0-9-]+", reason):
            reason = "reflection-gate-invalid"
        parser.error(reason)


if __name__ == "__main__":
    raise SystemExit(main())
