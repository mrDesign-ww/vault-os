#!/usr/bin/env python3
"""Generation-independent exactly-once ledger for processed completion receipts."""

import datetime
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
VAULT = HERE.parents[1]
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = load_module("reflection_processed_policy", HERE / "reflection_policy.py")


def parse_timestamp(value):
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("processed marker timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("processed marker timestamp")
    return parsed.astimezone(datetime.timezone.utc)


def authorities(policy, policy_sha):
    result = {
        policy_sha: {
            "delegation_id": policy["delegation_id"],
            "processor_sha256": policy["code"]["processed_sha256"],
        },
    }
    for predecessor in policy.get("completion_predecessors", []):
        processor = predecessor.get("processed_sha256")
        if processor is not None:
            result[predecessor["policy_sha256"]] = {
                "delegation_id": predecessor["delegation_id"],
                "processor_sha256": processor,
            }
    return result


def catalog_root(policy, platform, create=False):
    if platform not in ("claude", "codex"):
        raise ValueError("processed marker platform")
    root = VAULT / policy["roots"]["processed_work"] / platform
    if create:
        return POLICY.ensure_private_directory(root)
    try:
        return POLICY.validate_private_directory(root)
    except FileNotFoundError:
        return root


def build_marker(
    policy, policy_sha, platform, manifest, scan, scan_sha,
    finalization=None, finalization_sha=None,
):
    receipts = [
        item["receipt_sha256"] for item in manifest["completion_receipts"]
    ]
    signal_count = scan["signal_count"]
    if (
        manifest.get("policy_sha256") != policy_sha
        or scan.get("policy_sha256") != policy_sha
        or scan.get("platform") != platform
        or not SHA256_RE.fullmatch(str(scan_sha or ""))
        or scan.get("completion_receipt_sha256s") != receipts
        or not receipts or len(receipts) != len(set(receipts))
        or any(not SHA256_RE.fullmatch(str(item)) for item in receipts)
        or type(signal_count) is not int or signal_count < 0
        or (signal_count == 0) != (finalization is None)
    ):
        raise ValueError("processed marker evidence")
    if finalization is None:
        finalized_sha = None
        processed_at = scan["verified_at"]
    else:
        finalized_sha = finalization_sha
        processed_at = finalization.get("finalized_at")
        if (
            finalization.get("manifest_sha256") != scan["manifest_sha256"]
            or finalization.get("scan_receipt_sha256") != scan_sha
            or not SHA256_RE.fullmatch(str(finalized_sha or ""))
        ):
            raise ValueError("processed marker evidence")
    parse_timestamp(processed_at)
    return {
        "schema_version": 1,
        "receipt_type": "reflection-completions-processed",
        "status": "completed", "platform": platform, "zone": "work",
        "processing_policy_sha256": policy_sha,
        "delegation_id": policy["delegation_id"],
        "processor_sha256": policy["code"]["processed_sha256"],
        "manifest_sha256": scan["manifest_sha256"],
        "scan_receipt_sha256": scan_sha,
        "finalization_sha256": finalized_sha,
        "signal_count": signal_count,
        "completion_receipt_sha256s": receipts,
        "processed_at": processed_at,
    }


def validate_marker(value, digest, policy, policy_sha, platform):
    authority = authorities(policy, policy_sha).get(
        value.get("processing_policy_sha256") if isinstance(value, dict) else None
    )
    expected = {
        "schema_version", "receipt_type", "status", "platform", "zone",
        "processing_policy_sha256", "delegation_id", "processor_sha256",
        "manifest_sha256", "scan_receipt_sha256", "finalization_sha256",
        "signal_count", "completion_receipt_sha256s", "processed_at",
    }
    receipts = value.get("completion_receipt_sha256s") if isinstance(value, dict) else None
    signal_count = value.get("signal_count") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict) or set(value) != expected
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("receipt_type") != "reflection-completions-processed"
        or value.get("status") != "completed"
        or value.get("platform") != platform or value.get("zone") != "work"
        or authority is None
        or value.get("delegation_id") != authority["delegation_id"]
        or value.get("processor_sha256") != authority["processor_sha256"]
        or any(
            not SHA256_RE.fullmatch(str(value.get(field, "")))
            for field in ("manifest_sha256", "scan_receipt_sha256")
        )
        or type(signal_count) is not int or signal_count < 0
        or not isinstance(receipts, list) or not receipts
        or len(receipts) != len(set(receipts))
        or any(not SHA256_RE.fullmatch(str(item)) for item in receipts)
        or (
            signal_count == 0 and value.get("finalization_sha256") is not None
        )
        or (
            signal_count > 0
            and not SHA256_RE.fullmatch(str(value.get("finalization_sha256", "")))
        )
        or digest != hashlib.sha256(POLICY.canonical_json_bytes(value)).hexdigest()
    ):
        raise ValueError("processed marker authority")
    parse_timestamp(value["processed_at"])
    return value


def publish_marker(policy, policy_sha, marker):
    raw = POLICY.canonical_json_bytes(marker)
    validate_marker(
        marker, hashlib.sha256(raw).hexdigest(), policy, policy_sha,
        marker.get("platform") if isinstance(marker, dict) else None,
    )
    return POLICY.publish_immutable(
        catalog_root(policy, marker["platform"], create=True),
        raw,
    )


def read_processed(policy, policy_sha, platform):
    root = catalog_root(policy, platform)
    if not root.exists():
        return {}
    root = POLICY.validate_private_directory(root)
    result = {}
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            raise ValueError("processed marker artifact")
        raw, _ = POLICY.read_regular_bytes(
            path, "processed marker", owner_private=True,
        )
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise ValueError("processed marker content address")
        marker = POLICY.strict_json_loads(raw, "processed marker")
        validate_marker(marker, path.stem, policy, policy_sha, platform)
        for receipt_sha in marker["completion_receipt_sha256s"]:
            prior = result.get(receipt_sha)
            evidence = {
                "marker_sha256": path.stem,
                "manifest_sha256": marker["manifest_sha256"],
                "scan_receipt_sha256": marker["scan_receipt_sha256"],
                "finalization_sha256": marker["finalization_sha256"],
            }
            if prior is not None and prior != evidence:
                raise ValueError("processed marker overlap")
            result[receipt_sha] = evidence
    return result


def marker_for_receipts(processed, receipt_sha256s):
    if (
        not isinstance(processed, dict)
        or not isinstance(receipt_sha256s, list)
        or not receipt_sha256s
        or any(not SHA256_RE.fullmatch(str(item)) for item in receipt_sha256s)
    ):
        raise ValueError("processed marker coverage")
    try:
        markers = {
            processed[receipt_sha]["marker_sha256"]
            for receipt_sha in receipt_sha256s
        }
    except (KeyError, TypeError) as error:
        raise ValueError("processed marker coverage") from error
    if len(markers) != 1:
        raise ValueError("processed marker coverage")
    return next(iter(markers))


def self_test():
    with tempfile.TemporaryDirectory(prefix="processed-test-") as directory:
        root = Path(os.path.realpath(directory))
        policy = {
            "delegation_id": "current", "roots": {"processed_work": str(root)},
            "code": {"processed_sha256": "a" * 64},
            "completion_predecessors": [{
                "policy_sha256": "b" * 64, "delegation_id": "old",
                "processed_sha256": "c" * 64,
            }],
        }
        marker = {
            "schema_version": 1,
            "receipt_type": "reflection-completions-processed",
            "status": "completed", "platform": "codex", "zone": "work",
            "processing_policy_sha256": "b" * 64,
            "delegation_id": "old", "processor_sha256": "c" * 64,
            "manifest_sha256": "d" * 64, "scan_receipt_sha256": "e" * 64,
            "finalization_sha256": None, "signal_count": 0,
            "completion_receipt_sha256s": ["f" * 64],
            "processed_at": "2026-08-21T12:00:00Z",
        }
        _, digest, _ = publish_marker(policy, "a" * 64, marker)
        assert read_processed(policy, "a" * 64, "codex")["f" * 64][
            "marker_sha256"
        ] == digest
        chain_root = root / "chain"
        policy_a = {
            "delegation_id": "A", "roots": {"processed_work": str(chain_root)},
            "code": {"processed_sha256": "1" * 64},
            "completion_predecessors": [],
        }
        marker_a = {
            **marker,
            "processing_policy_sha256": "a" * 64,
            "delegation_id": "A", "processor_sha256": "1" * 64,
            "completion_receipt_sha256s": ["2" * 64],
        }
        publish_marker(policy_a, "a" * 64, marker_a)
        policy_b = {
            "delegation_id": "B", "roots": {"processed_work": str(chain_root)},
            "code": {"processed_sha256": "3" * 64},
            "completion_predecessors": [{
                "policy_sha256": "a" * 64, "delegation_id": "A",
                "processed_sha256": "1" * 64,
            }],
        }
        assert "2" * 64 in read_processed(policy_b, "b" * 64, "codex")
        policy_c = {
            "delegation_id": "C", "roots": {"processed_work": str(chain_root)},
            "code": {"processed_sha256": "4" * 64},
            "completion_predecessors": [
                {
                    "policy_sha256": "a" * 64, "delegation_id": "A",
                    "processed_sha256": "1" * 64,
                },
                {
                    "policy_sha256": "b" * 64, "delegation_id": "B",
                    "processed_sha256": "3" * 64,
                },
            ],
        }
        assert "2" * 64 in read_processed(policy_c, "c" * 64, "codex")
        assert marker_for_receipts(
            read_processed(policy_c, "c" * 64, "codex"), ["2" * 64],
        ) == hashlib.sha256(POLICY.canonical_json_bytes(marker_a)).hexdigest()
        try:
            marker_for_receipts({}, ["2" * 64])
        except ValueError:
            pass
        else:
            raise AssertionError("missing processed marker coverage was accepted")
        try:
            read_processed(
                {**policy_c, "completion_predecessors": policy_c[
                    "completion_predecessors"
                ][1:]},
                "c" * 64, "codex",
            )
        except ValueError as error:
            assert str(error) == "processed marker authority"
        else:
            raise AssertionError("dropped processed predecessor was accepted")
        for schema in (1.0, True):
            malformed = {**marker, "schema_version": schema}
            empty_root = root / ("invalid-" + str(schema).lower())
            invalid_policy = {
                **policy, "roots": {"processed_work": str(empty_root)},
            }
            try:
                publish_marker(invalid_policy, "a" * 64, malformed)
            except ValueError:
                pass
            else:
                raise AssertionError("non-integer processed marker was accepted")
            assert not empty_root.exists()
    print("reflection-processed self-test: PASS")


if __name__ == "__main__":
    self_test()
