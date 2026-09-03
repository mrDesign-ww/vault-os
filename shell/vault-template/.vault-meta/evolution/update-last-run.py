#!/usr/bin/env python3
"""Atomically update evolution state using a JSON serializer."""

import argparse
import datetime
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_OUTPUT = os.path.join(HERE, "last-run.json")
LOCK_PATH = ".vault-meta/write/work"
REQUIRED_PHASES = ("curator_memory", "wiki_hygiene", "retrieval_refresh")
REFLECTION_NO_EVIDENCE = {
    "ready-autonomous-awaiting-completion",
    "blocked-delegation-invalid",
}
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
PLACEHOLDER_RE = re.compile(r"\b(?:undefined|null)\b", re.I)


def check_lock(token):
    if not isinstance(token, str) or not SHA256_RE.fullmatch(token):
        raise ValueError("a valid work writer lock token is required")
    result = subprocess.run(
        ["bash", "scripts/wiki-lock.sh", "check", LOCK_PATH, token],
        cwd=VAULT, capture_output=True, text=True,
    )
    if result.returncode or result.stdout.strip() != "owned":
        raise ValueError("work writer lock is not owned")


def validate_disposition_artifact(platform, evidence):
    if evidence is None:
        return
    path = os.path.join(HERE, "reflection-disposition.py")
    spec = importlib.util.spec_from_file_location("reflection_disposition", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy, policy_sha = module.POLICY.load_policy()
    value = module.load_published(
        evidence["disposition_sha256"], platform, policy, policy_sha,
    )
    if any(
        value[field] != evidence[field]
        for field in (
            "manifest_sha256", "scan_receipt_sha256", "signal_count",
        )
    ):
        raise ValueError("reflection run-state differs from disposition receipt")
    manifest, _ = module.published_artifacts(
        policy, policy_sha, platform,
        value["manifest_sha256"], value["scan_receipt_sha256"],
    )
    processed = module.SELECTOR.PROCESSED.read_processed(
        policy, policy_sha, platform,
    )
    marker_sha = module.SELECTOR.PROCESSED.marker_for_receipts(
        processed,
        [item["receipt_sha256"] for item in manifest["completion_receipts"]],
    )
    if marker_sha != evidence["processed_marker_sha256"]:
        raise ValueError("reflection run-state differs from processed marker")


def validate_noop_artifact(platform, evidence):
    if evidence is None:
        return
    policy_path = os.path.join(HERE, "reflection_policy.py")
    policy_spec = importlib.util.spec_from_file_location(
        "reflection_policy_last_run", policy_path,
    )
    policy_module = importlib.util.module_from_spec(policy_spec)
    policy_spec.loader.exec_module(policy_module)
    selector_path = os.path.join(HERE, "trace-manifest.py")
    selector_spec = importlib.util.spec_from_file_location(
        "trace_manifest_last_run", selector_path,
    )
    selector = importlib.util.module_from_spec(selector_spec)
    selector_spec.loader.exec_module(selector)
    policy, policy_sha = policy_module.load_policy()
    roots = {
        key: policy_module.generation_root(
            os.path.join(VAULT, policy["roots"][key]),
            policy_sha, platform, create=False,
        )
        for key in (
            "completion_work", "manifest_work", "scan_receipt_work",
            "selection_receipt_work",
        )
    }
    selector.load_noop_receipt(
        roots["selection_receipt_work"], evidence["selection_receipt_sha256"],
        policy, policy_sha, platform, roots["completion_work"],
        roots["scan_receipt_work"], roots["manifest_work"], replay=True,
    )


def parse_phases(items, run_date):
    phases = {}
    for item in items:
        if "=" not in item:
            raise ValueError("--phase must use NAME=VALUE")
        name, value = item.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name or not value:
            raise ValueError("--phase name and value cannot be empty")
        phases[name] = value
    missing = [name for name in REQUIRED_PHASES if name not in phases]
    if missing:
        raise ValueError(
            "--phase must set every required phase: %s" % ", ".join(missing)
        )
    for name in REQUIRED_PHASES:
        value = phases[name]
        if value == "complete":
            raise ValueError("required phase %s must use the run date" % name)
        try:
            phase_date = datetime.date.fromisoformat(value)
        except ValueError:
            continue
        if phase_date.isoformat() == value and value != run_date:
            raise ValueError("dated required phases must match --date")
    return phases


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def validate_run_order(prior, requested):
    if prior is None:
        return
    try:
        prior_date = datetime.date.fromisoformat(prior)
    except (TypeError, ValueError) as error:
        raise ValueError("existing platform last_run is invalid") from error
    if requested < prior_date:
        raise ValueError("last_run cannot move backwards")


def parse_retrieval_evidence(phases, run_date, pages, chunks, digest, loadouts_digest):
    values = (pages, chunks, digest, loadouts_digest)
    completed = phases.get("retrieval_refresh") == run_date
    if not completed:
        if any(value is not None for value in values):
            raise ValueError("retrieval evidence requires a completed retrieval_refresh")
        return None
    if any(value is None for value in values):
        raise ValueError(
            "completed retrieval_refresh requires pages, chunks, snapshot SHA-256 and loadout SHA-256"
        )
    if not SHA256_RE.fullmatch(digest):
        raise ValueError("--snapshot-sha256 must be exactly 64 hexadecimal characters")
    if not SHA256_RE.fullmatch(loadouts_digest):
        raise ValueError("--loadouts-sha256 must be exactly 64 hexadecimal characters")
    return {
        "pages": pages,
        "chunks": chunks,
        "snapshot_sha256": digest.lower(),
        "loadouts_sha256": loadouts_digest.lower(),
    }


def parse_reflection_evidence(
    phases, run_date, manifest_digest, receipt_digest,
    disposition_digest, processed_digest, signal_count, noop_digest,
):
    phase = phases.get("reflection")
    completed = phase == run_date
    allowed_pending = REFLECTION_NO_EVIDENCE | {"ready-autonomous-no-eligible-traces"}
    if phase is not None and not completed and phase not in allowed_pending:
        try:
            parsed = datetime.date.fromisoformat(phase)
        except ValueError as error:
            raise ValueError("reflection phase has an unsupported state") from error
        if parsed.isoformat() != phase or phase != run_date:
            raise ValueError("dated reflection phase must match --date")
    values = (
        manifest_digest, receipt_digest, disposition_digest,
        processed_digest, signal_count,
    )
    if phase == "ready-autonomous-no-eligible-traces":
        if any(value is not None for value in values):
            raise ValueError("no-eligible reflection cannot include disposition evidence")
        if not isinstance(noop_digest, str) or not SHA256_RE.fullmatch(noop_digest):
            raise ValueError("no-eligible reflection requires an exact selector receipt")
        return None, {
            "status": "no-eligible-traces",
            "selection_receipt_sha256": noop_digest.lower(),
        }
    if not completed:
        if any(value is not None for value in values) or noop_digest is not None:
            raise ValueError("reflection evidence requires a completed reflection phase")
        return None, None
    if noop_digest is not None:
        raise ValueError("completed reflection cannot include no-op selector evidence")
    if any(value is None for value in values):
        raise ValueError(
            "completed reflection requires manifest, scan receipt, disposition SHA-256 and signal count"
        )
    for label, digest in (
        ("manifest", manifest_digest),
        ("scan receipt", receipt_digest),
        ("disposition", disposition_digest),
        ("processed marker", processed_digest),
    ):
        if not SHA256_RE.fullmatch(digest):
            raise ValueError("reflection %s SHA-256 is invalid" % label)
    return {
        "manifest_sha256": manifest_digest.lower(),
        "scan_receipt_sha256": receipt_digest.lower(),
        "disposition_sha256": disposition_digest.lower(),
        "processed_marker_sha256": processed_digest.lower(),
        "signal_count": signal_count,
        "approval_scope": "standing-autonomous-l0-l2",
    }, None


def self_test():
    phases = parse_phases([
        "curator_memory=2026-08-20",
        "wiki_hygiene=2026-08-20",
        "retrieval_refresh=pending-rebuild",
    ], "2026-08-20")
    assert phases["retrieval_refresh"] == "pending-rebuild"
    complete = parse_phases([
        "curator_memory=2026-08-20",
        "wiki_hygiene=2026-08-20",
        "retrieval_refresh=2026-08-20",
    ], "2026-08-20")
    evidence = parse_retrieval_evidence(
        complete, "2026-08-20", 254, 7833, "a" * 64, "b" * 64
    )
    assert evidence == {
        "pages": 254, "chunks": 7833, "snapshot_sha256": "a" * 64,
        "loadouts_sha256": "b" * 64,
    }
    assert parse_retrieval_evidence(
        phases, "2026-08-20", None, None, None, None
    ) is None
    for invalid in (
        ["curator_memory=2026-08-20"],
        [
            "curator_memory=complete", "wiki_hygiene=2026-08-20",
            "retrieval_refresh=2026-08-20",
        ],
        [
            "curator_memory=2026-08-19", "wiki_hygiene=2026-08-20",
            "retrieval_refresh=2026-08-20",
        ],
    ):
        try:
            parse_phases(invalid, "2026-08-20")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid phase set accepted: %r" % invalid)
    for values in (
        (None, 7833, "a" * 64, "b" * 64),
        (254, 7833, "not-a-digest", "b" * 64),
        (254, 7833, "a" * 64, "not-a-digest"),
    ):
        try:
            parse_retrieval_evidence(complete, "2026-08-20", *values)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid retrieval evidence accepted: %r" % (values,))
    assert PLACEHOLDER_RE.search("snapshot undefined")
    validate_run_order("2026-08-20", datetime.date(2026, 8, 20))
    try:
        validate_run_order("2026-08-21", datetime.date(2026, 8, 20))
    except ValueError:
        pass
    else:
        raise AssertionError("last_run rollback was accepted")
    reflection_phases = dict(complete, reflection="2026-08-20")
    reflection, noop = parse_reflection_evidence(
        reflection_phases, "2026-08-20", "c" * 64, "d" * 64, "e" * 64,
        "f" * 64, 2,
        None,
    )
    assert noop is None
    assert reflection["signal_count"] == 2
    assert reflection["approval_scope"] == "standing-autonomous-l0-l2"
    for state in REFLECTION_NO_EVIDENCE:
        assert parse_reflection_evidence(
            dict(complete, reflection=state), "2026-08-20",
            None, None, None, None, None, None,
        ) == (None, None)
    assert parse_reflection_evidence(
        dict(complete, reflection="ready-autonomous-no-eligible-traces"),
        "2026-08-20", None, None, None, None, None, "f" * 64,
    )[1]["selection_receipt_sha256"] == "f" * 64
    for values in (
        (reflection_phases, None, "d" * 64, "e" * 64, "f" * 64, 2, None),
        (dict(complete, reflection="ready-autonomous-no-eligible-traces"), "c" * 64, "d" * 64, "e" * 64, "f" * 64, 2, None),
        (dict(complete, reflection="ready-autonomous-no-eligible-traces"), None, None, None, None, None, None),
        (dict(complete, reflection="complete"), None, None, None, None, None, None),
    ):
        try:
            parse_reflection_evidence(values[0], "2026-08-20", *values[1:])
        except ValueError:
            pass
        else:
            raise AssertionError("invalid reflection evidence accepted: %r" % (values,))
    print("update-last-run self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--platform", choices=("claude", "codex"), required=True)
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--phase", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--retrieval-pages", type=nonnegative_int)
    parser.add_argument("--retrieval-chunks", type=nonnegative_int)
    parser.add_argument("--snapshot-sha256")
    parser.add_argument("--loadouts-sha256")
    parser.add_argument("--reflection-manifest-sha256")
    parser.add_argument("--reflection-receipt-sha256")
    parser.add_argument("--reflection-disposition-sha256")
    parser.add_argument("--reflection-processed-sha256")
    parser.add_argument("--reflection-signal-count", type=nonnegative_int)
    parser.add_argument("--reflection-noop-sha256")
    parser.add_argument("--lock-token", required=True)
    parser.add_argument("--notes", required=True)
    args = parser.parse_args()
    try:
        requested_date = datetime.date.fromisoformat(args.date)
    except ValueError:
        parser.error("--date must be YYYY-MM-DD")
    if requested_date > datetime.date.today():
        parser.error("--date cannot be in the future")
    if not args.notes.strip():
        parser.error("--notes cannot be empty")
    if PLACEHOLDER_RE.search(args.notes):
        parser.error("--notes contains an unresolved placeholder")

    try:
        check_lock(args.lock_token)
        phases = parse_phases(args.phase, args.date)
        evidence = parse_retrieval_evidence(
            phases, args.date, args.retrieval_pages,
            args.retrieval_chunks, args.snapshot_sha256, args.loadouts_sha256,
        )
        reflection_evidence, reflection_noop_evidence = parse_reflection_evidence(
            phases, args.date, args.reflection_manifest_sha256,
            args.reflection_receipt_sha256,
            args.reflection_disposition_sha256,
            args.reflection_processed_sha256,
            args.reflection_signal_count,
            args.reflection_noop_sha256,
        )
        validate_disposition_artifact(args.platform, reflection_evidence)
        validate_noop_artifact(args.platform, reflection_noop_evidence)
    except ValueError as error:
        parser.error(str(error))

    destination = os.path.abspath(args.output)
    if os.path.commonpath((os.path.realpath(HERE), os.path.realpath(destination))) != os.path.realpath(HERE):
        parser.error("--output must stay inside .vault-meta/evolution")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    lock = open(destination + ".lock", "a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        if os.path.exists(destination):
            with open(destination, encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                raise ValueError("existing evolution state is not a JSON object")
        else:
            payload = {}

        platforms = payload.setdefault("platforms", {})
        platform_state = platforms.setdefault(args.platform, {})
        prior_run = platform_state.get("last_run")
        validate_run_order(prior_run, requested_date)
        prior_phases = platform_state.get("phases", {})
        if not isinstance(prior_phases, dict):
            raise ValueError("existing platform phases are not a JSON object")
        prior_phases.update(phases)
        platform_state.update({
            "state_schema_version": 4,
            "last_run": args.date,
            "phases": prior_phases,
            "notes": args.notes,
        })
        if evidence is None:
            platform_state.pop("retrieval_evidence", None)
        else:
            platform_state["retrieval_evidence"] = evidence
        if reflection_evidence is None:
            platform_state.pop("reflection_evidence", None)
        else:
            platform_state["reflection_evidence"] = reflection_evidence
        if reflection_noop_evidence is None:
            platform_state.pop("reflection_noop_evidence", None)
        else:
            platform_state["reflection_noop_evidence"] = reflection_noop_evidence

        # Keep legacy top-level fields for older Claude tooling.
        payload.update({
            "state_schema_version": 4,
            "last_run": args.date,
            "phases": prior_phases,
            "notes": args.notes,
        })
        if evidence is None:
            payload.pop("retrieval_evidence", None)
        else:
            payload["retrieval_evidence"] = evidence
        if reflection_evidence is None:
            payload.pop("reflection_evidence", None)
        else:
            payload["reflection_evidence"] = reflection_evidence
        if reflection_noop_evidence is None:
            payload.pop("reflection_noop_evidence", None)
        else:
            payload["reflection_noop_evidence"] = reflection_noop_evidence
        fd, temporary = tempfile.mkstemp(
            prefix=".last-run-", suffix=".json.tmp", dir=os.path.dirname(destination)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            check_lock(args.lock_token)
            os.replace(temporary, destination)
            descriptor = os.open(
                os.path.dirname(destination), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
