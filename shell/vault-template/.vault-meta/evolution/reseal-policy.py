#!/usr/bin/env python3
"""Re-seal the reflection policy hash chain after an intentional, owner-approved edit.

The chain is a cycle across five files: reflection-policy.json records a hash of every
guarded file, reflection_policy.py anchors the policy digest, the doctrine page carries
that digest in its frontmatter, memory-approvals.json records the doctrine page hash,
and memory-model.py anchors the manifest hash. Editing any one of them by hand breaks
the next link, which is what the hand-maintained chain kept drifting on.

None of the five rewritten files is itself hashed by content in the policy, and the
three template hashes normalise out exactly the anchors this rewrites, so the whole
cycle resolves in a single pass.

    /usr/bin/python3 .vault-meta/evolution/reseal-policy.py --check
    /usr/bin/python3 .vault-meta/evolution/reseal-policy.py --apply

--check never writes. Rewriting a file with identical bytes still moves its mtime, and
the memory index pins the logical file state of every page it covers.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

VAULT = Path(__file__).resolve().parents[2]
POLICY_JSON = VAULT / ".vault-meta/evolution/reflection-policy.json"
POLICY_PY = VAULT / ".vault-meta/evolution/reflection_policy.py"
APPROVALS = VAULT / ".vault-meta/memory-approvals.json"
MEMORY_MODEL = VAULT / "scripts/memory-model.py"
DOCTRINE = "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md"

GENERATION_ROOTS = (
    ".vault-meta/evolution/trace-open/work",
    ".vault-meta/evolution/trace-completions/work",
)

# Mirrors load_policy() in reflection_policy.py. Keep both lists in step.
CODE_PATHS = {
    "scanner_sha256": ".vault-meta/evolution/trace-scan.py",
    "selector_sha256": ".vault-meta/evolution/trace-manifest.py",
    "codex_contract_sha256": "AGENTS.md",
    "codex_session_hook_sha256": ".vault-meta/evolution/trace-session.py",
    "claude_contract_sha256": "CLAUDE.md",
    "claude_hook_sha256": ".vault-meta/evolution/claude-trace-hook.py",
    "claude_settings_sha256": ".claude/settings.json",
    "disposition_sha256": ".vault-meta/evolution/reflection-disposition.py",
    "evidence_sha256": ".vault-meta/evolution/reflection-evidence.py",
    "processed_sha256": ".vault-meta/evolution/reflection-processed.py",
    "wiki_lock_py_sha256": "scripts/wiki-lock.py",
    "wiki_lock_sh_sha256": "scripts/wiki-lock.sh",
    "update_last_run_sha256": ".vault-meta/evolution/update-last-run.py",
    "health_check_sha256": ".vault-meta/evolution/health-check.py",
    "verifier_codex_sha256": ".agents/skills/evolution-verifier/SKILL.md",
    "verifier_claude_sha256": ".claude/skills/evolution-verifier/SKILL.md",
    "evolution_codex_sha256": ".agents/skills/evolution/SKILL.md",
    "evolution_claude_sha256": ".claude/skills/evolution/SKILL.md",
    "evolution_playbook_sha256": ".vault-meta/evolution/EVOLUTION.md",
}


def load_helpers():
    """Import reflection_policy for its hash helpers, without validating the policy."""
    spec = importlib.util.spec_from_file_location("reseal_reflection_policy", POLICY_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def replace_once(text, pattern, value, label):
    updated, count = re.subn(
        pattern, lambda match: match.group(1) + value + match.group(2), text, count=1,
    )
    if count != 1:
        raise ValueError("anchor not found: %s" % label)
    return updated


def generation_exists(policy_sha):
    return any(
        (VAULT / root / policy_sha / platform).is_dir()
        for root in GENERATION_ROOTS
        for platform in ("claude", "codex")
    )


def carry_over(policy, outgoing_digest, outgoing_code):
    """Keep the outgoing generation trusted so its open turns can still be closed.

    Re-sealing mints a new policy digest, and the lifecycle refuses to read any
    generation directory whose digest is neither current nor a declared predecessor.
    Without this, every turn recorded under the previous seal becomes unreadable.
    """
    predecessors = policy["completion_predecessors"]
    known = {item["policy_sha256"] for item in predecessors}
    if outgoing_digest in known or not generation_exists(outgoing_digest):
        return predecessors
    return sorted(predecessors + [{
        "policy_sha256": outgoing_digest,
        "delegation_id": policy["delegation_id"],
        "not_before": policy["not_before"],
        "claude_hook_sha256": outgoing_code["claude_hook_sha256"],
        "codex_session_hook_sha256": outgoing_code["codex_session_hook_sha256"],
        "processed_sha256": outgoing_code["processed_sha256"],
        "processed_scan_catalogs": None,
    }], key=lambda item: item["policy_sha256"])


def sealed_contents():
    """Return {path: text} for the five chain files, sealed against the current tree."""
    helpers = load_helpers()
    raw = POLICY_JSON.read_bytes()
    outgoing_digest = hashlib.sha256(raw).hexdigest()
    policy = json.loads(raw)
    outgoing_code = dict(policy["code"])

    for field, relative in CODE_PATHS.items():
        target = VAULT / relative
        if not target.exists():
            raise ValueError("guarded file is missing: %s" % relative)
        policy["code"][field] = sha256_file(target)
    # The template hashes normalise out the very anchors rewritten below, so reading
    # them off the current tree is stable across the rewrite.
    policy["code"]["loader_template_sha256"] = helpers.loader_template_sha256()
    policy["code"]["memory_model_template_sha256"] = helpers.memory_model_template_sha256()
    policy["code"]["approvals_template_sha256"] = helpers.approvals_template_sha256()
    if policy["code"] != outgoing_code:
        policy["completion_predecessors"] = carry_over(
            policy, outgoing_digest, outgoing_code,
        )

    # The policy file is canonical JSON; keep it byte-identical in shape.
    policy_text = json.dumps(policy, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    digest = sha256_text(policy_text)

    policy_py_text = replace_once(
        POLICY_PY.read_text(encoding="utf-8"),
        r'(?m)^(POLICY_SHA256 = ")[0-9a-f]{64}(")', digest,
        "reflection_policy.py/POLICY_SHA256",
    )
    doctrine_text = replace_once(
        (VAULT / DOCTRINE).read_text(encoding="utf-8"),
        r'(?m)^(reflection_policy_sha256: ")[0-9a-f]{64}(")', digest,
        "doctrine/reflection_policy_sha256",
    )
    approvals_text = replace_once(
        APPROVALS.read_text(encoding="utf-8"),
        # The manifest is written by whichever tool sealed it last, so match the
        # doctrine entry without assuming key order or whitespace inside it.
        r'("%s"\s*:\s*\{[^{}]*?"sha256"\s*:\s*")[0-9a-f]{64}(")' % re.escape(DOCTRINE),
        sha256_text(doctrine_text), "memory-approvals.json/doctrine sha256",
    )
    memory_model_text = replace_once(
        MEMORY_MODEL.read_text(encoding="utf-8"),
        r'(?m)^(APPROVALS_MANIFEST_SHA256 = ")[0-9a-f]{64}(")',
        sha256_text(approvals_text), "memory-model.py/APPROVALS_MANIFEST_SHA256",
    )
    return {
        POLICY_JSON: policy_text,
        POLICY_PY: policy_py_text,
        VAULT / DOCTRINE: doctrine_text,
        APPROVALS: approvals_text,
        MEMORY_MODEL: memory_model_text,
    }


def purge_bytecode(paths):
    """Drop cached bytecode for a rewritten source file.

    Every rewrite here swaps one 64-hex anchor for another, so the source keeps its
    exact size, and two rewrites inside the same second leave a .pyc whose recorded
    (mtime, size) still validates against content it no longer matches. Python then
    serves the stale module, and the hook silently checks the wrong policy anchor.
    """
    for path in paths:
        if path.suffix != ".py":
            continue
        cache = Path(importlib.util.cache_from_source(str(path.resolve())))
        if cache.exists():
            os.unlink(cache)


def drifted(contents):
    return [
        path for path, text in contents.items()
        if path.read_text(encoding="utf-8") != text
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report drift, write nothing")
    group.add_argument("--apply", action="store_true", help="re-seal the chain")
    args = parser.parse_args()

    contents = sealed_contents()
    changed = drifted(contents)
    names = ", ".join(sorted(path.relative_to(VAULT).as_posix() for path in changed))

    if args.check:
        if changed:
            print("chain drift in: %s" % names)
            return 1
        print("reflection policy chain is sealed")
        return 0

    for path in changed:
        path.write_text(contents[path], encoding="utf-8")
    purge_bytecode(changed)
    if drifted(sealed_contents()):
        raise SystemExit("chain did not converge; inspect the guarded files by hand")
    load_helpers().load_policy()
    print("re-sealed: %s" % names if changed else "chain was already sealed")
    if changed:
        print("next: rebuild the zone index, then commit the index authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
