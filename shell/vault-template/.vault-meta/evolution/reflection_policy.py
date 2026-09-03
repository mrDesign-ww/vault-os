#!/usr/bin/env python3
"""Strict standing-policy loader and immutable local artifact publisher."""

import datetime
import hashlib
import importlib.util
import json
import os
import re
import stat
import tempfile
from pathlib import Path


VAULT = Path(__file__).resolve().parents[2]
POLICY_PATH = VAULT / ".vault-meta" / "evolution" / "reflection-policy.json"
POLICY_SHA256 = "0000000000000000000000000000000000000000000000000000000000000000"
OWNER = "{{OWNER_NAME}}"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def strict_json_loads(raw, label):
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key in %s" % label)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=no_duplicates)


def canonical_json_bytes(value):
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def claude_user_message(message):
    """Classify one outer Claude user envelope without treating tool results as prompts."""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return "prompt", content
    if not isinstance(content, list) or not content:
        return "invalid", None
    block_types = [
        block.get("type") if isinstance(block, dict) else None
        for block in content
    ]
    if all(block_type == "tool_result" for block_type in block_types):
        return "continuation", None
    if "tool_result" in block_types or any(block_type is None for block_type in block_types):
        return "invalid", None
    return "prompt", "".join(
        block.get("text", "") for block in content
        if block.get("type") == "text"
    )


def atomic_private_json_write(path, value, label="private JSON artifact"):
    """Replace one owner-private JSON file durably and verify the exact bytes."""
    path = Path(path)
    root = ensure_private_directory(path.parent)
    raw = canonical_json_bytes(value)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=root, prefix=".state-", suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(root)
        observed, _ = read_regular_bytes(path, label, owner_private=True)
        if observed != raw:
            raise ValueError("private JSON publication verification failed")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _optional_private_json(path, label):
    try:
        raw, _ = read_regular_bytes(path, label, owner_private=True)
    except FileNotFoundError:
        return None
    return strict_json_loads(raw, label)


def _transaction_payload(
    state_name, ledger_name, state_before, state_after,
    ledger_before, ledger_after, recovery_action,
):
    if recovery_action not in ("commit", "rollback"):
        raise ValueError("state-ledger transaction recovery action is invalid")
    core = {
        "schema_version": 1,
        "transaction_type": "state-ledger-write-ahead",
        "recovery_action": recovery_action,
        "state_name": state_name,
        "ledger_name": ledger_name,
        "state_before": state_before,
        "state_after": state_after,
        "ledger_before": ledger_before,
        "ledger_after": ledger_after,
    }
    return {
        **core,
        "transaction_sha256": hashlib.sha256(canonical_json_bytes(core)).hexdigest(),
    }


def _validate_transaction(value):
    expected = {
        "schema_version", "transaction_type", "transaction_sha256",
        "recovery_action",
        "state_name", "ledger_name", "state_before", "state_after",
        "ledger_before", "ledger_after",
    }
    if (
        not isinstance(value, dict) or set(value) != expected
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("transaction_type") != "state-ledger-write-ahead"
        or value.get("recovery_action") not in ("commit", "rollback")
    ):
        raise ValueError("state-ledger transaction schema is invalid")
    for field in ("state_name", "ledger_name"):
        name = value.get(field)
        if (
            not isinstance(name, str) or not name.endswith(".json")
            or name.startswith(".") or Path(name).name != name
        ):
            raise ValueError("state-ledger transaction path is invalid")
    if not isinstance(value.get("state_after"), dict) or not isinstance(
        value.get("ledger_after"), dict
    ):
        raise ValueError("state-ledger transaction target is invalid")
    for field in ("state_before", "ledger_before"):
        if value.get(field) is not None and not isinstance(value[field], dict):
            raise ValueError("state-ledger transaction predecessor is invalid")
    core = {key: value[key] for key in expected if key != "transaction_sha256"}
    if value.get("transaction_sha256") != hashlib.sha256(
        canonical_json_bytes(core)
    ).hexdigest():
        raise ValueError("state-ledger transaction identity is invalid")
    return value


def recover_state_ledger_transaction(
    journal_path, state_root, ledger_root,
    validate_state=None, validate_ledger=None, action=None,
):
    """Commit or roll back a prepared two-file mutation, then remove its journal."""
    journal_path = Path(journal_path)
    try:
        raw, _ = read_regular_bytes(
            journal_path, "state-ledger transaction", owner_private=True,
        )
    except FileNotFoundError:
        return False
    value = _validate_transaction(
        strict_json_loads(raw, "state-ledger transaction")
    )
    action = action or value["recovery_action"]
    if action not in ("commit", "rollback"):
        raise ValueError("state-ledger transaction recovery action is invalid")
    state_root = validate_private_directory(state_root)
    ledger_root = validate_private_directory(ledger_root)
    if journal_path.parent != validate_private_directory(state_root / ".transactions"):
        raise ValueError("state-ledger transaction catalog is invalid")
    state_path = state_root / value["state_name"]
    ledger_path = ledger_root / value["ledger_name"]
    for item in (value["state_before"], value["state_after"]):
        if item is not None and validate_state is not None:
            validate_state(item)
    for item in (value["ledger_before"], value["ledger_after"]):
        if item is not None and validate_ledger is not None:
            validate_ledger(item)

    def apply(path, before, after, label):
        current = _optional_private_json(path, label)
        if current == after:
            return
        if current != before:
            raise ValueError("state-ledger transaction predecessor drifted")
        if after is None:
            os.unlink(path)
            _fsync_directory(path.parent)
        else:
            atomic_private_json_write(path, after, label)

    state_target = value["state_after" if action == "commit" else "state_before"]
    state_source = value["state_before" if action == "commit" else "state_after"]
    ledger_target = value["ledger_after" if action == "commit" else "ledger_before"]
    ledger_source = value["ledger_before" if action == "commit" else "ledger_after"]

    operations = (
        ((ledger_path, ledger_source, ledger_target, "transaction session ledger"),
         (state_path, state_source, state_target, "transaction lifecycle state"))
        if action == "rollback" else
        ((state_path, state_source, state_target, "transaction lifecycle state"),
         (ledger_path, ledger_source, ledger_target, "transaction session ledger"))
    )
    for operation in operations:
        apply(*operation)
    if (
        _optional_private_json(state_path, "transaction lifecycle state")
        != state_target
        or _optional_private_json(ledger_path, "transaction session ledger")
        != ledger_target
    ):
        raise ValueError("state-ledger transaction verification failed")
    os.unlink(journal_path)
    _fsync_directory(journal_path.parent)
    return True


def commit_state_ledger_transaction(
    journal_path, state_root, ledger_root, state_path, state_before, state_after,
    ledger_path, ledger_before, ledger_after,
    validate_state=None, validate_ledger=None, recovery_action="commit",
):
    """Prepare and replay one crash-recoverable state plus ledger mutation."""
    state_root = ensure_private_directory(state_root)
    ledger_root = ensure_private_directory(ledger_root)
    journal_root = ensure_private_directory(state_root / ".transactions")
    journal_path = Path(journal_path)
    if journal_path.parent != journal_root:
        raise ValueError("state-ledger transaction catalog is invalid")
    recover_state_ledger_transaction(
        journal_path, state_root, ledger_root,
        validate_state=validate_state, validate_ledger=validate_ledger,
    )
    if Path(state_path).parent != state_root or Path(ledger_path).parent != ledger_root:
        raise ValueError("state-ledger transaction target is invalid")
    if (
        _optional_private_json(state_path, "transaction lifecycle state")
        != state_before
        or _optional_private_json(ledger_path, "transaction session ledger")
        != ledger_before
    ):
        raise ValueError("state-ledger transaction snapshot drifted")
    payload = _transaction_payload(
        Path(state_path).name, Path(ledger_path).name,
        state_before, state_after, ledger_before, ledger_after, recovery_action,
    )
    atomic_private_json_write(
        journal_path, payload, "state-ledger transaction",
    )
    recover_state_ledger_transaction(
        journal_path, state_root, ledger_root,
        validate_state=validate_state, validate_ledger=validate_ledger,
        action="commit",
    )


def file_identity(metadata, include_size=True):
    identity = (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid,
        metadata.st_nlink,
    )
    if include_size:
        identity += (
            metadata.st_size,
            getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1_000_000_000)),
        )
    return identity


def read_regular_bytes(path, label, owner_private=False):
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("%s is not a regular file" % label)
        if (
            before.st_uid != os.getuid()
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or (owner_private and stat.S_IMODE(before.st_mode) != 0o600)
            or before.st_nlink != 1
        ):
            raise ValueError("%s is not owner-controlled and singly linked" % label)
        raw = handle.read()
        after = os.fstat(handle.fileno())
        if file_identity(before) != file_identity(after):
            raise ValueError("%s changed while being read" % label)
    current = os.stat(path, follow_symlinks=False)
    if file_identity(current) != file_identity(after):
        raise ValueError("%s changed after read" % label)
    return raw, after


def validate_private_directory(path):
    path = Path(os.path.abspath(str(path)))
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise ValueError("artifact directory is not canonical")
    metadata = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("artifact directory must be owner-only 0700")
    return path


def ensure_private_directory(path):
    path = Path(os.path.abspath(str(path)))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return validate_private_directory(path)


def _fsync_directory(root):
    descriptor = os.open(str(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_staged_bytes(path):
    """Read a publisher stage, including the expected two-link crash state."""
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink not in (1, 2)
        ):
            raise ValueError("staged artifact is not an owner-private publication stage")
        raw = handle.read()
        after = os.fstat(handle.fileno())
        if file_identity(before) != file_identity(after):
            raise ValueError("staged artifact changed while being read")
    current = os.stat(path, follow_symlinks=False)
    if file_identity(current) != file_identity(after):
        raise ValueError("staged artifact changed after read")
    return raw, after


def _recover_staged_temps(root, destination, digest, raw):
    """Finish or discard owner-private stages left by an interrupted publisher."""
    prefix = ".publish-%s-" % digest
    changed = False
    for name in os.listdir(root):
        if not name.startswith(prefix) or not name.endswith(".tmp"):
            continue
        candidate = root / name
        staged, metadata = _read_staged_bytes(candidate)
        if staged == raw and hashlib.sha256(staged).hexdigest() == digest:
            try:
                destination_meta = os.stat(destination, follow_symlinks=False)
            except FileNotFoundError:
                if metadata.st_nlink != 1:
                    raise ValueError("staged artifact has an unexplained hard link")
                os.link(candidate, destination, follow_symlinks=False)
            else:
                same_inode = (
                    destination_meta.st_dev == metadata.st_dev
                    and destination_meta.st_ino == metadata.st_ino
                )
                if same_inode:
                    if metadata.st_nlink != 2:
                        raise ValueError("post-link publication state is invalid")
                else:
                    if metadata.st_nlink != 1:
                        raise ValueError("staged artifact has an unexplained hard link")
                    existing, _ = read_regular_bytes(
                        destination, "immutable artifact", owner_private=True,
                    )
                    if existing != raw:
                        raise ValueError("content-addressed artifact collision")
            os.unlink(candidate)
            changed = True
            published, _ = read_regular_bytes(
                destination, "recovered immutable artifact", owner_private=True,
            )
            if published != raw:
                raise ValueError("recovered immutable artifact differs")
        elif metadata.st_nlink == 1:
            os.unlink(candidate)
            changed = True
        else:
            raise ValueError("foreign staged artifact has an unsafe link count")
    if changed:
        _fsync_directory(root)


def publish_immutable(root, raw, suffix=".json"):
    """Publish verified bytes without overwrite; recover the link/unlink crash window."""
    root = ensure_private_directory(Path(root))
    digest = hashlib.sha256(raw).hexdigest()
    destination = root / (digest + suffix)
    _recover_staged_temps(root, destination, digest, raw)
    try:
        existing, _ = read_regular_bytes(destination, "immutable artifact", owner_private=True)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing != raw:
            raise ValueError("content-addressed artifact differs from requested bytes")
        return destination, digest, False

    descriptor, temporary_name = tempfile.mkstemp(
        dir=root, prefix=".publish-%s-" % digest, suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        staged, _ = read_regular_bytes(temporary, "staged artifact", owner_private=True)
        if staged != raw:
            raise ValueError("staged artifact verification failed")
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            existing, _ = read_regular_bytes(
                destination, "immutable artifact", owner_private=True,
            )
            if existing != raw:
                raise ValueError("content-addressed artifact collision")
        os.unlink(temporary)
        _fsync_directory(root)
        published, metadata = read_regular_bytes(
            destination, "immutable artifact", owner_private=True,
        )
        if published != raw or hashlib.sha256(published).hexdigest() != digest:
            raise ValueError("published artifact verification failed")
        if metadata.st_nlink != 1:
            raise ValueError("published artifact has an unsafe link count")
        return destination, digest, True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def generation_root(root, policy_sha, platform, create=True):
    """Return a private catalog directory isolated by policy and platform."""
    if not SHA256_RE.fullmatch(str(policy_sha)) or platform not in ("claude", "codex"):
        raise ValueError("reflection catalog generation is invalid")
    root = Path(os.path.abspath(str(root)))
    target = root / policy_sha / platform
    if create:
        ensure_private_directory(root)
        (root / policy_sha).mkdir(mode=0o700, exist_ok=True)
        validate_private_directory(root / policy_sha)
        return ensure_private_directory(target)
    try:
        return validate_private_directory(target)
    except FileNotFoundError:
        return target


def completion_generation_root(policy, fallback_root, receipt, platform):
    """Keep a prepared receipt in the policy generation that authorized it."""
    base_value = policy.get("roots", {}).get("completion_work")
    if not isinstance(base_value, str) or not base_value:
        return Path(fallback_root)
    base = Path(base_value)
    if not base.is_absolute():
        base = VAULT / base
    return generation_root(base, receipt["policy_sha256"], platform)


def completion_authorities(policy, policy_sha, platform, current_hook_sha=None):
    """Return current plus explicitly pinned predecessor receipt authorities."""
    if platform not in ("claude", "codex") or not SHA256_RE.fullmatch(str(policy_sha)):
        raise ValueError("reflection completion authority is invalid")
    hook_field = (
        "claude_hook_sha256" if platform == "claude"
        else "codex_session_hook_sha256"
    )
    result = [{
        "policy_sha256": policy_sha,
        "delegation_id": policy["delegation_id"],
        "not_before": policy["not_before"],
        "session_hook_sha256": (
            current_hook_sha
            if current_hook_sha is not None else policy["code"][hook_field]
        ),
    }]
    for predecessor in policy.get("completion_predecessors", []):
        predecessor_hook = predecessor[hook_field]
        if predecessor_hook is None:
            continue
        result.append({
            "policy_sha256": predecessor["policy_sha256"],
            "delegation_id": predecessor["delegation_id"],
            "not_before": predecessor["not_before"],
            "session_hook_sha256": predecessor_hook,
        })
    return result


def lifecycle_generation_roots(
    root, platform, current_policy_sha=None, allowed_policy_shas=None,
):
    """List legacy and policy-scoped lifecycle roots for one work platform."""
    if platform not in ("claude", "codex"):
        raise ValueError("reflection lifecycle platform is invalid")
    base = ensure_private_directory(Path(root))
    roots = []
    legacy = base / platform
    if legacy.exists():
        roots.append((None, validate_private_directory(legacy)))
    for child in sorted(base.iterdir()):
        if child.name.startswith("."):
            continue
        if child.name in ("claude", "codex"):
            validate_private_directory(child)
            continue
        if not SHA256_RE.fullmatch(child.name):
            raise ValueError("unexpected reflection lifecycle generation")
        generation = validate_private_directory(child)
        target = generation / platform
        if target.exists():
            if (
                allowed_policy_shas is not None
                and child.name not in allowed_policy_shas
            ):
                raise ValueError("untrusted reflection lifecycle generation")
            roots.append((child.name, validate_private_directory(target)))
    if current_policy_sha is not None:
        current = generation_root(base, current_policy_sha, platform)
        if all(path != current for _, path in roots):
            roots.append((current_policy_sha, current))
    return sorted(roots, key=lambda item: (item[0] is not None, item[0] or ""))


def catalog_identity(root, label):
    root = Path(root)
    if not root.exists():
        items = []
    else:
        root = validate_private_directory(root)
        items = []
        for path in sorted(root.iterdir()):
            if path.name.startswith("."):
                continue
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                raise ValueError("unexpected %s artifact" % label)
            raw, _ = read_regular_bytes(
                path, label, owner_private=True,
            )
            if hashlib.sha256(raw).hexdigest() != path.stem:
                raise ValueError("%s content address drifted" % label)
            items.append(path.stem)
    return {
        "count": len(items),
        "sha256": hashlib.sha256(canonical_json_bytes(items)).hexdigest(),
    }


def valid_catalog_identity(value):
    return (
        isinstance(value, dict)
        and set(value) == {"count", "sha256"}
        and type(value.get("count")) is int
        and value["count"] >= 0
        and SHA256_RE.fullmatch(str(value.get("sha256", ""))) is not None
    )


def loader_template_sha256(path=None):
    raw, _ = read_regular_bytes(
        Path(path or __file__), "reflection policy loader",
    )
    normalized, count = re.subn(
        rb'POLICY_SHA256 = "[^"]+"',
        b'POLICY_SHA256 = "<POLICY_SHA256>"',
        raw,
        count=1,
    )
    if count != 1:
        raise ValueError("reflection policy anchor declaration is missing")
    return hashlib.sha256(normalized).hexdigest()


def memory_model_template_sha256(path=None):
    raw, _ = read_regular_bytes(
        Path(path or (VAULT / "scripts" / "memory-model.py")),
        "memory resolver",
    )
    normalized = raw
    for name in (b"APPROVALS_MANIFEST_SHA256", b"EVALS_MANIFEST_SHA256"):
        normalized, count = re.subn(
            rb'(?m)^' + name + rb' = "[0-9a-f]{64}"$',
            name + b' = "<' + name + b'>"',
            normalized, count=1,
        )
        if count != 1:
            raise ValueError("memory resolver anchor declaration is missing")
    return hashlib.sha256(normalized).hexdigest()


def approvals_template_sha256(path=None):
    raw, _ = read_regular_bytes(
        Path(path or (VAULT / ".vault-meta" / "memory-approvals.json")),
        "L3 approval manifest",
    )
    value = strict_json_loads(raw, "L3 approval manifest")
    if not isinstance(value, dict) or set(value) != {"schema_version", "l3"}:
        raise ValueError("L3 approval manifest template is invalid")
    normalized = json.loads(json.dumps(value))
    entries = normalized.get("l3")
    if not isinstance(entries, dict):
        raise ValueError("L3 approval manifest template is invalid")
    for entry in entries.values():
        if not isinstance(entry, dict) or not SHA256_RE.fullmatch(str(entry.get("sha256", ""))):
            raise ValueError("L3 approval manifest template is invalid")
        entry["sha256"] = "<PAGE_SHA256>"
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def _iso_date(value, label):
    try:
        parsed = datetime.date.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError("%s is not an ISO date" % label) from error
    return parsed


def _iso_datetime(value, label):
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("%s is not an ISO timestamp" % label) from error
    if parsed.tzinfo is None:
        raise ValueError("%s must include a timezone" % label)
    return parsed.astimezone(datetime.timezone.utc)


def validate_policy_shape(policy, digest, code_paths=None, today=None):
    expected = {
        "schema_version", "owner", "approved_on", "status", "revoked",
        "delegation_id", "mode", "valid_from", "valid_until", "not_before",
        "allowed_platform_zones", "vault", "roots", "code", "selection",
        "scopes", "proposal_policy", "l3_policy", "doctrine_path",
        "completion_predecessors",
    }
    if not isinstance(policy, dict) or set(policy) != expected:
        raise ValueError("unsupported reflection policy fields")
    if (
        type(policy.get("schema_version")) is not int
        or policy.get("schema_version") != 3
        or policy.get("owner") != OWNER
        or policy.get("status") != "active"
        or policy.get("revoked") is not False
        or policy.get("mode") != "standing-autonomous-delegation"
        or policy.get("allowed_platform_zones") != [
            ["claude", "work"], ["codex", "work"],
        ]
    ):
        raise ValueError("reflection policy is inactive or unsupported")
    current = today or datetime.date.today()
    approved = _iso_date(policy["approved_on"], "approved_on")
    valid_from = _iso_date(policy["valid_from"], "valid_from")
    valid_until = _iso_date(policy["valid_until"], "valid_until")
    not_before = _iso_datetime(policy["not_before"], "not_before")
    if approved > current or not valid_from <= current <= valid_until:
        raise ValueError("reflection policy is outside its validity window")
    if not_before.date() < valid_from:
        raise ValueError("not_before predates policy validity")

    predecessors = policy.get("completion_predecessors")
    predecessor_fields = {
        "policy_sha256", "delegation_id", "not_before",
        "claude_hook_sha256", "codex_session_hook_sha256",
        "processed_sha256", "processed_scan_catalogs",
    }
    if not isinstance(predecessors, list):
        raise ValueError("reflection completion predecessors are invalid")
    seen_predecessors = set()
    for predecessor in predecessors:
        if (
            not isinstance(predecessor, dict)
            or set(predecessor) != predecessor_fields
            or not SHA256_RE.fullmatch(str(predecessor.get("policy_sha256", "")))
            or predecessor["policy_sha256"] == digest
            or predecessor["policy_sha256"] in seen_predecessors
            or not isinstance(predecessor.get("delegation_id"), str)
            or not predecessor["delegation_id"]
            or all(
                predecessor.get(field) is None
                for field in ("claude_hook_sha256", "codex_session_hook_sha256")
            )
            or any(
                predecessor.get(field) is not None
                and not SHA256_RE.fullmatch(predecessor[field])
                for field in ("claude_hook_sha256", "codex_session_hook_sha256")
            )
            or (
                predecessor.get("processed_sha256") is not None
                and not SHA256_RE.fullmatch(predecessor["processed_sha256"])
            )
        ):
            raise ValueError("reflection completion predecessors are invalid")
        proofs = predecessor.get("processed_scan_catalogs")
        if predecessor.get("processed_sha256") is None:
            if not isinstance(proofs, dict) or set(proofs) != {"claude", "codex"}:
                raise ValueError("legacy processed catalog proof is invalid")
            for platform, hook_field in (
                ("claude", "claude_hook_sha256"),
                ("codex", "codex_session_hook_sha256"),
            ):
                proof = proofs[platform]
                if predecessor[hook_field] is None:
                    if proof is not None:
                        raise ValueError("legacy processed catalog proof is invalid")
                elif (
                    not isinstance(proof, dict)
                    or set(proof) != {"generation", "legacy"}
                    or not valid_catalog_identity(proof["generation"])
                    or not valid_catalog_identity(proof["legacy"])
                ):
                    raise ValueError("legacy processed catalog proof is invalid")
        elif proofs is not None:
            raise ValueError("processed predecessor has an unexpected legacy proof")
        _iso_datetime(predecessor["not_before"], "predecessor not_before")
        seen_predecessors.add(predecessor["policy_sha256"])
    if predecessors != sorted(predecessors, key=lambda item: item["policy_sha256"]):
        raise ValueError("reflection completion predecessors are not canonical")

    vault = policy.get("vault")
    if not isinstance(vault, dict) or set(vault) != {"id", "path"}:
        raise ValueError("reflection vault identity is invalid")
    if vault.get("id") != "{{VAULT_ID}}" or vault.get("path") != str(VAULT):
        raise ValueError("reflection policy targets another vault")
    roots = policy.get("roots")
    expected_roots = {
        "trace_claude", "trace_codex", "open_work", "completion_work",
        "manifest_work", "scan_receipt_work", "selection_receipt_work",
        "disposition_work", "processed_work",
    }
    if not isinstance(roots, dict) or set(roots) != expected_roots:
        raise ValueError("reflection policy roots are invalid")
    home = Path(os.path.expanduser("~"))
    required_roots = {
        "trace_claude": "{{CLAUDE_TRACE_PATH}}",
        "trace_codex": str(home / ".codex" / "sessions"),
        "open_work": ".vault-meta/evolution/trace-open/work",
        "completion_work": ".vault-meta/evolution/trace-completions/work",
        "manifest_work": ".vault-meta/evolution/trace-manifests/work",
        "scan_receipt_work": ".vault-meta/evolution/trace-scan-receipts/work",
        "selection_receipt_work": ".vault-meta/evolution/trace-selection-receipts/work",
        "disposition_work": ".vault-meta/evolution/reflection-dispositions/work",
        "processed_work": ".vault-meta/evolution/reflection-processed/work",
    }
    if roots != required_roots:
        raise ValueError("reflection policy root identity changed")
    scan_base = VAULT / roots["scan_receipt_work"]
    for predecessor in predecessors:
        if predecessor["processed_sha256"] is not None:
            continue
        for platform in ("claude", "codex"):
            proof = predecessor["processed_scan_catalogs"][platform]
            if proof is None:
                continue
            actual = {
                "generation": catalog_identity(
                    scan_base / predecessor["policy_sha256"] / platform,
                    "legacy processed generation scan",
                ),
                "legacy": catalog_identity(
                    scan_base / platform, "legacy processed flat scan",
                ),
            }
            if actual != proof:
                raise ValueError("legacy processed catalog proof drifted")

    code = policy.get("code")
    expected_code = {
        "claude_contract_sha256", "claude_hook_sha256",
        "claude_settings_sha256", "codex_contract_sha256",
        "codex_session_hook_sha256",
        "disposition_sha256", "evidence_sha256", "loader_template_sha256",
        "processed_sha256",
        "memory_model_template_sha256", "approvals_template_sha256",
        "wiki_lock_py_sha256", "wiki_lock_sh_sha256",
        "update_last_run_sha256", "health_check_sha256", "scanner_sha256",
        "selector_sha256", "verifier_claude_sha256", "verifier_codex_sha256",
        "evolution_claude_sha256", "evolution_codex_sha256",
        "evolution_playbook_sha256",
    }
    if not isinstance(code, dict) or set(code) != expected_code:
        raise ValueError("reflection policy code identity is invalid")
    if any(not isinstance(code[key], str) or not SHA256_RE.fullmatch(code[key]) for key in code):
        raise ValueError("reflection policy contains an invalid code hash")
    if code["loader_template_sha256"] != loader_template_sha256():
        raise ValueError("reflection policy loader drifted")
    if code["memory_model_template_sha256"] != memory_model_template_sha256():
        raise ValueError("memory resolver drifted")
    if code["approvals_template_sha256"] != approvals_template_sha256():
        raise ValueError("L3 approval manifest structure drifted")
    if code_paths:
        for field, path in code_paths.items():
            raw, _ = read_regular_bytes(path, field)
            if hashlib.sha256(raw).hexdigest() != code.get(field):
                raise ValueError("reflection code drifted: %s" % field)

    selection = policy.get("selection")
    if not isinstance(selection, dict) or set(selection) != {
        "ordering", "max_age_days", "max_files", "max_file_bytes",
        "max_total_bytes", "require_task_complete", "reject_zone_transitions",
    }:
        raise ValueError("reflection selection rules are invalid")
    if selection.get("ordering") != ["completed_at", "session_id", "turn_id"]:
        raise ValueError("reflection ordering is not deterministic")
    for field, minimum, maximum in (
        ("max_age_days", 1, 3650), ("max_files", 1, 32),
        ("max_file_bytes", 1, 500_000_000),
        ("max_total_bytes", 1, 1_000_000_000),
    ):
        value = selection.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError("reflection selection limit is invalid")
    if selection.get("require_task_complete") is not True or selection.get("reject_zone_transitions") is not True:
        raise ValueError("reflection completion or zone safeguard is disabled")
    if policy.get("scopes") != [
        "read-completed-work-turn-segments-only", "publish-content-free-receipts",
        "propose-l0-l1-l2-only",
    ]:
        raise ValueError("reflection delegation scope changed")
    proposal = policy.get("proposal_policy")
    if proposal != {
        "allowed_targets": ["l1", "l2"],
        "ambiguous_target": "l2",
        "ambiguous_trust": "provisional-synthesis",
        "continue_without_prompt": True,
        "effective_memory_level": "l0",
        "promotion_eligible_when_ambiguous": False,
    }:
        raise ValueError("reflection proposal policy changed")
    l3 = policy.get("l3_policy")
    if l3 != {
        "delegated_l3_enabled": False,
        "direct_owner_directive_only": True,
        "forbidden_autonomous_changes": [
            "approval-machinery", "credential-handling", "delegation-policy",
            "destructive-authority", "external-egress", "privacy-relaxation",
            "verifier-code", "zone-isolation",
        ],
    }:
        raise ValueError("reflection L3 policy changed")
    doctrine_path = policy.get("doctrine_path")
    if doctrine_path != "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md":
        raise ValueError("reflection doctrine path changed")
    if digest != POLICY_SHA256:
        raise ValueError("reflection policy differs from its code anchor")
    return policy


def load_policy(policy_path=POLICY_PATH):
    raw, _ = read_regular_bytes(policy_path, "reflection policy")
    digest = hashlib.sha256(raw).hexdigest()
    policy = strict_json_loads(raw, "reflection policy")
    code_paths = {
        "scanner_sha256": VAULT / ".vault-meta/evolution/trace-scan.py",
        "selector_sha256": VAULT / ".vault-meta/evolution/trace-manifest.py",
        "codex_contract_sha256": VAULT / "AGENTS.md",
        "codex_session_hook_sha256": VAULT / ".vault-meta/evolution/trace-session.py",
        "claude_contract_sha256": VAULT / "CLAUDE.md",
        "claude_hook_sha256": VAULT / ".vault-meta/evolution/claude-trace-hook.py",
        "claude_settings_sha256": VAULT / ".claude/settings.json",
        "disposition_sha256": VAULT / ".vault-meta/evolution/reflection-disposition.py",
        "evidence_sha256": VAULT / ".vault-meta/evolution/reflection-evidence.py",
        "processed_sha256": VAULT / ".vault-meta/evolution/reflection-processed.py",
        "wiki_lock_py_sha256": VAULT / "scripts/wiki-lock.py",
        "wiki_lock_sh_sha256": VAULT / "scripts/wiki-lock.sh",
        "update_last_run_sha256": VAULT / ".vault-meta/evolution/update-last-run.py",
        "health_check_sha256": VAULT / ".vault-meta/evolution/health-check.py",
        "verifier_codex_sha256": VAULT / ".agents/skills/evolution-verifier/SKILL.md",
        "verifier_claude_sha256": VAULT / ".claude/skills/evolution-verifier/SKILL.md",
        "evolution_codex_sha256": VAULT / ".agents/skills/evolution/SKILL.md",
        "evolution_claude_sha256": VAULT / ".claude/skills/evolution/SKILL.md",
        "evolution_playbook_sha256": VAULT / ".vault-meta/evolution/EVOLUTION.md",
    }
    validate_policy_shape(policy, digest, code_paths=code_paths)
    spec = importlib.util.spec_from_file_location(
        "vault_memory_model", VAULT / "scripts" / "memory-model.py",
    )
    memory_model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(memory_model)
    page = VAULT / policy["doctrine_path"]
    frontmatter, _, page_sha256 = memory_model.read_page(page)
    approved, _ = memory_model.l3_approval(page, frontmatter, page_hash=page_sha256)
    if not approved or frontmatter.get("reflection_policy_sha256") != digest:
        raise ValueError("reflection policy is not bound by direct-owner L3 doctrine")
    return policy, digest


def self_test():
    assert SHA256_RE.fullmatch(loader_template_sha256())
    payload = {"b": 2, "a": 1}
    assert canonical_json_bytes(payload) == b'{"a":1,"b":2}\n'
    with tempfile.TemporaryDirectory(prefix="reflection-policy-test-") as directory:
        root = Path(os.path.realpath(directory)) / "private"
        raw = canonical_json_bytes({"value": "safe"})
        first, digest, created = publish_immutable(root, raw)
        second, same_digest, recreated = publish_immutable(root, raw)
        assert created and not recreated and first == second and digest == same_digest
        assert stat.S_IMODE(first.stat().st_mode) == 0o600
        assert first.stat().st_nlink == 1
        interrupted_raw = canonical_json_bytes({"value": "recover-before-link"})
        interrupted_digest = hashlib.sha256(interrupted_raw).hexdigest()
        interrupted = root / (
            ".publish-%s-interrupted.tmp" % interrupted_digest
        )
        interrupted.write_bytes(interrupted_raw)
        interrupted.chmod(0o600)
        recovered, recovered_digest, recovered_created = publish_immutable(
            root, interrupted_raw,
        )
        assert recovered_digest == interrupted_digest and not recovered_created
        assert recovered.exists() and not interrupted.exists()
        post_link_raw = canonical_json_bytes({"value": "recover-after-link"})
        post_link_digest = hashlib.sha256(post_link_raw).hexdigest()
        post_link_stage = root / (
            ".publish-%s-interrupted.tmp" % post_link_digest
        )
        post_link_stage.write_bytes(post_link_raw)
        post_link_stage.chmod(0o600)
        post_link_destination = root / (post_link_digest + ".json")
        os.link(post_link_stage, post_link_destination, follow_symlinks=False)
        assert post_link_stage.stat().st_nlink == 2
        post_link_recovered, _, post_link_created = publish_immutable(
            root, post_link_raw,
        )
        assert not post_link_created and not post_link_stage.exists()
        assert post_link_recovered.stat().st_nlink == 1
        policy_copy = root.parent / "policy.json"
        policy_copy.write_bytes(raw)
        policy_copy.chmod(0o644)
        assert read_regular_bytes(policy_copy, "policy")[0] == raw
        policy_copy.chmod(0o666)
        try:
            read_regular_bytes(policy_copy, "policy")
        except ValueError:
            pass
        else:
            raise AssertionError("group-writable policy was accepted")
        lifecycle = ensure_private_directory(root.parent / "shared-lifecycle")
        ensure_private_directory(lifecycle / "claude")
        ensure_private_directory(lifecycle / "codex")
        generation_root(lifecycle, "a" * 64, "claude")
        generation_root(lifecycle, "a" * 64, "codex")
        for platform in ("claude", "codex"):
            found = lifecycle_generation_roots(
                lifecycle, platform, current_policy_sha="a" * 64,
                allowed_policy_shas={"a" * 64},
            )
            assert found == [
                (None, lifecycle / platform),
                ("a" * 64, lifecycle / ("a" * 64) / platform),
            ]
        ensure_private_directory(lifecycle / "unexpected")
        try:
            lifecycle_generation_roots(lifecycle, "codex")
        except ValueError as error:
            assert str(error) == "unexpected reflection lifecycle generation"
        else:
            raise AssertionError("unexpected lifecycle directory was accepted")
    print("reflection-policy self-test: PASS")


if __name__ == "__main__":
    self_test()
