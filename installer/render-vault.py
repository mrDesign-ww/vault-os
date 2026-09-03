#!/usr/bin/env python3
"""Render the empty Vault OS template and bind its local trust anchors."""

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "shell" / "vault-template"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


def canonical_json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(path, pattern, replacement):
    raw = path.read_text()
    updated, count = re.subn(pattern, replacement, raw, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError("trust anchor missing in %s" % path)
    path.write_text(updated)


def render_placeholders(root, values):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        rendered = text
        for key, value in values.items():
            rendered = rendered.replace("{{%s}}" % key, value)
        if rendered != text:
            path.write_text(rendered)


def note(kind, title, date, level, tags, body, provenance=None, cache=False):
    lines = [
        "---", "type: %s" % kind, 'title: "%s"' % title,
        "status: evergreen", "created: %s" % date, "updated: %s" % date,
    ]
    if cache:
        lines.append("memory_role: cache")
    elif level:
        lines.append("memory_level: %s" % level)
    if provenance:
        lines.append('memory_provenance: "%s"' % provenance)
    lines.extend(["tags:"] + ["  - %s" % tag for tag in tags] + ["---", "", body.strip(), ""])
    return "\n".join(lines)


def write_scaffold(root, date, title, client_label):
    folders = [
        "wiki/workspace/projects/inbox", "wiki/areas", "wiki/resources/concepts",
        "wiki/resources/people", "wiki/resources/incoming", "wiki/archives",
        "wiki/personal/projects", "wiki/personal/inbox",
        "wiki/client/projects", "wiki/client/inbox",
    ]
    for folder in folders:
        path = root / folder
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitkeep").touch(exist_ok=True)

    pages = {
        "wiki/index.md": note(
            "meta", "Vault Index", date, "l2", ["meta", "index"],
            "# Vault Index\n\n%s uses PARA with three isolated zones: work, personal, and %s.\n\n"
            "## Work navigation\n\n- [[overview|Overview]]\n- [[workspace/projects/_index|Projects]]\n"
            "- [[areas/_index|Areas]]\n- [[resources/_index|Resources]]\n- [[archives/_index|Archives]]"
            % (title, client_label),
            "active work-zone PARA indexes and linked pages",
        ),
        "wiki/overview.md": note(
            "meta", "Overview", date, "l2", ["meta", "overview"],
            "# Overview\n\nThis is a private, local-first knowledge base. Obsidian is the source of truth. "
            "PARA determines where knowledge lives, and L0-L3 determines how it is trusted.",
            "vault operating rules and current zone indexes",
        ),
        "wiki/log.md": note(
            "meta", "Operation Log", date, "l0", ["meta", "log"],
            "# Operation Log\n\nNewest entries go at the top. Past entries are append-only.",
        ),
        "wiki/hot.md": note(
            "meta", "Hot Cache", date, None, ["meta", "hot-cache"],
            "# Recent Context\n\nThe work zone is ready. No project context has been added yet.", cache=True,
        ),
        "wiki/workspace/projects/_index.md": note(
            "meta", "Projects", date, "l2", ["meta", "index", "para/projects"],
            "# Projects\n\nNo active projects yet.", "linked work-zone project index pages",
        ),
        "wiki/areas/_index.md": note(
            "meta", "Areas", date, "l2", ["meta", "index", "para/areas"],
            "# Areas\n\nNo areas defined yet.", "linked work-zone area pages",
        ),
        "wiki/resources/_index.md": note(
            "meta", "Resources", date, "l2", ["meta", "index", "para/resources"],
            "# Resources\n\n## Operating doctrine\n\n"
            "- [[L0-L3 Memory Model for the Vault]]\n"
            "- [[Autonomous Reflection Delegation Policy]]\n\nNo other resources yet.",
            "linked work-zone resource pages",
        ),
        "wiki/archives/_index.md": note(
            "meta", "Archives", date, "l2", ["meta", "index", "para/archives"],
            "# Archives\n\nNo archived work yet.", "linked archived project and area pages",
        ),
    }
    for slug, label in (("personal", "Personal"), ("client", client_label)):
        pages["wiki/%s/_index.md" % slug] = note(
            "meta", "%s Zone" % label, date, "l2", ["meta", "index", "zone/%s" % slug],
            "# %s Zone\n\nThis zone is isolated. No projects yet." % label,
            "linked %s-zone project pages" % slug,
        )
        pages["wiki/%s/log.md" % slug] = note(
            "meta", "%s Log" % label, date, "l0", ["meta", "log", "zone/%s" % slug],
            "# %s Log\n\nNewest entries go at the top." % label,
        )
        pages["wiki/%s/hot.md" % slug] = note(
            "meta", "%s Hot Cache" % label, date, None,
            ["meta", "hot-cache", "zone/%s" % slug],
            "# Recent Context\n\nThe %s zone is ready." % label, cache=True,
        )
    for relative, content in pages.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def normalized_file_hash(path, substitutions):
    raw = path.read_bytes()
    for pattern, replacement in substitutions:
        raw, count = re.subn(pattern, replacement, raw, count=1, flags=re.MULTILINE)
        if count != 1:
            raise ValueError("normalization anchor missing in %s" % path)
    return hashlib.sha256(raw).hexdigest()


def approvals_template_hash(approvals):
    normalized = copy.deepcopy(approvals)
    for entry in normalized["l3"].values():
        entry["sha256"] = "<PAGE_SHA256>"
    return hashlib.sha256(canonical_json(normalized)).hexdigest()


def bind_trust(root, owner, date, now, vault_id, claude_trace):
    metadata = root / ".vault-meta"
    evolution = metadata / "evolution"
    memory_model = root / "scripts" / "memory-model.py"
    loader = evolution / "reflection_policy.py"
    evals = {"schema_version": 1, "zone": "work", "cases": []}
    approvals = {
        "schema_version": 1,
        "l3": {
            "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md": {
                "approved_by": owner, "approved_on": date, "sha256": "0" * 64,
            },
            "wiki/resources/concepts/L0-L3 Memory Model for the Vault.md": {
                "approved_by": owner, "approved_on": date, "sha256": "0" * 64,
            },
        },
    }
    write_json(metadata / "memory-evals.json", evals)
    write_json(metadata / "memory-approvals.json", approvals)

    code_paths = {
        "scanner_sha256": evolution / "trace-scan.py",
        "selector_sha256": evolution / "trace-manifest.py",
        "codex_contract_sha256": root / "AGENTS.md",
        "codex_session_hook_sha256": evolution / "trace-session.py",
        "claude_contract_sha256": root / "CLAUDE.md",
        "claude_hook_sha256": evolution / "claude-trace-hook.py",
        "claude_settings_sha256": root / ".claude" / "settings.json",
        "disposition_sha256": evolution / "reflection-disposition.py",
        "evidence_sha256": evolution / "reflection-evidence.py",
        "processed_sha256": evolution / "reflection-processed.py",
        "wiki_lock_py_sha256": root / "scripts" / "wiki-lock.py",
        "wiki_lock_sh_sha256": root / "scripts" / "wiki-lock.sh",
        "update_last_run_sha256": evolution / "update-last-run.py",
        "health_check_sha256": evolution / "health-check.py",
        "verifier_codex_sha256": root / ".agents" / "skills" / "evolution-verifier" / "SKILL.md",
        "verifier_claude_sha256": root / ".claude" / "skills" / "evolution-verifier" / "SKILL.md",
        "evolution_codex_sha256": root / ".agents" / "skills" / "evolution" / "SKILL.md",
        "evolution_claude_sha256": root / ".claude" / "skills" / "evolution" / "SKILL.md",
        "evolution_playbook_sha256": evolution / "EVOLUTION.md",
    }
    code = {field: sha256(path) for field, path in code_paths.items()}
    code["loader_template_sha256"] = normalized_file_hash(loader, [
        (rb'^POLICY_SHA256 = "[^"]+"$', b'POLICY_SHA256 = "<POLICY_SHA256>"'),
    ])
    code["memory_model_template_sha256"] = normalized_file_hash(memory_model, [
        (rb'^APPROVALS_MANIFEST_SHA256 = "[0-9a-f]{64}"$', b'APPROVALS_MANIFEST_SHA256 = "<APPROVALS_MANIFEST_SHA256>"'),
        (rb'^EVALS_MANIFEST_SHA256 = "[0-9a-f]{64}"$', b'EVALS_MANIFEST_SHA256 = "<EVALS_MANIFEST_SHA256>"'),
    ])
    code["approvals_template_sha256"] = approvals_template_hash(approvals)
    policy = {
        "schema_version": 3, "owner": owner, "approved_on": date,
        "status": "active", "revoked": False,
        "delegation_id": "vault-os-work-reflection-%s-v1" % date,
        "mode": "standing-autonomous-delegation", "valid_from": date,
        "valid_until": "9999-12-31", "not_before": now,
        "allowed_platform_zones": [["claude", "work"], ["codex", "work"]],
        "vault": {"id": vault_id, "path": str(root)},
        "roots": {
            "trace_claude": str(claude_trace),
            "trace_codex": str(Path.home() / ".codex" / "sessions"),
            "open_work": ".vault-meta/evolution/trace-open/work",
            "completion_work": ".vault-meta/evolution/trace-completions/work",
            "manifest_work": ".vault-meta/evolution/trace-manifests/work",
            "scan_receipt_work": ".vault-meta/evolution/trace-scan-receipts/work",
            "selection_receipt_work": ".vault-meta/evolution/trace-selection-receipts/work",
            "disposition_work": ".vault-meta/evolution/reflection-dispositions/work",
            "processed_work": ".vault-meta/evolution/reflection-processed/work",
        },
        "code": code,
        "selection": {
            "ordering": ["completed_at", "session_id", "turn_id"],
            "max_age_days": 90, "max_files": 8, "max_file_bytes": 50_000_000,
            "max_total_bytes": 200_000_000, "require_task_complete": True,
            "reject_zone_transitions": True,
        },
        "scopes": ["read-completed-work-turn-segments-only", "publish-content-free-receipts", "propose-l0-l1-l2-only"],
        "proposal_policy": {
            "allowed_targets": ["l1", "l2"], "ambiguous_target": "l2",
            "ambiguous_trust": "provisional-synthesis", "continue_without_prompt": True,
            "effective_memory_level": "l0", "promotion_eligible_when_ambiguous": False,
        },
        "l3_policy": {
            "delegated_l3_enabled": False, "direct_owner_directive_only": True,
            "forbidden_autonomous_changes": [
                "approval-machinery", "credential-handling", "delegation-policy",
                "destructive-authority", "external-egress", "privacy-relaxation",
                "verifier-code", "zone-isolation",
            ],
        },
        "doctrine_path": "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md",
        "completion_predecessors": [],
    }
    policy_path = evolution / "reflection-policy.json"
    write_json(policy_path, policy)
    policy_hash = sha256(policy_path)
    replace_once(loader, r'^POLICY_SHA256 = "[0-9a-f]{64}"$', 'POLICY_SHA256 = "%s"' % policy_hash)

    doctrine = root / policy["doctrine_path"]
    doctrine.write_text(doctrine.read_text().replace("{{REFLECTION_POLICY_SHA256}}", policy_hash))
    for relative, entry in approvals["l3"].items():
        entry["sha256"] = sha256(root / relative)
    write_json(metadata / "memory-approvals.json", approvals)
    replace_once(
        memory_model, r'^APPROVALS_MANIFEST_SHA256 = "[0-9a-f]{64}"$',
        'APPROVALS_MANIFEST_SHA256 = "%s"' % sha256(metadata / "memory-approvals.json"),
    )
    replace_once(
        memory_model, r'^EVALS_MANIFEST_SHA256 = "[0-9a-f]{64}"$',
        'EVALS_MANIFEST_SHA256 = "%s"' % sha256(metadata / "memory-evals.json"),
    )
    return policy_hash


def validate_field(value, label):
    value = value.strip()
    if not value or any(char in value for char in ('"', "\\", "\n", "\r", "{{", "}}")):
        raise ValueError("%s contains unsupported characters" % label)
    return value


def main():
    parser = argparse.ArgumentParser(description="Render a private, empty Vault OS vault")
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--language", default="English")
    parser.add_argument("--client-label", default="Client")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--approve-memory-policy", action="store_true")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("the hardened L0-L3 and reflection runtime currently requires macOS or Linux")
    if not args.approve_memory_policy:
        parser.error("explicit --approve-memory-policy is required to create owner-approved L3 doctrine")
    try:
        install_date = dt.date.fromisoformat(args.date)
        if install_date > dt.date.today():
            raise ValueError("date cannot be in the future")
        owner = validate_field(args.owner, "owner")
        title = validate_field(args.title, "title")
        language = validate_field(args.language, "language")
        client_label = validate_field(args.client_label, "client label")
    except ValueError as error:
        parser.error(str(error))
    target = args.target.expanduser().resolve()
    if '"' in str(target) or "\\" in str(target):
        parser.error("target path contains unsupported characters")
    if target == TEMPLATE or TEMPLATE in target.parents or (target.exists() and any(target.iterdir())):
        parser.error("target must be a new or empty directory outside this repository")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE, target, dirs_exist_ok=True)
    vault_id = "vault-os-" + hashlib.sha256(str(target).encode()).hexdigest()[:16]
    claude_trace = Path.home() / ".claude" / "projects" / str(target).replace(os.sep, "-")
    render_placeholders(target, {
        "OWNER_NAME": owner, "VAULT_TITLE": title, "DATE": args.date,
        "LANGUAGE": language, "CLIENT_LABEL": client_label,
        "VAULT_ID": vault_id, "CLAUDE_TRACE_PATH": str(claude_trace),
    })
    (target / "CLAUDE.template.md").rename(target / "CLAUDE.md")
    (target / "AGENTS.template.md").rename(target / "AGENTS.md")
    (target / ".vault-meta" / "mode.template.json").rename(target / ".vault-meta" / "mode.json")
    write_scaffold(target, args.date, title, client_label)
    now = args.date + "T00:00:00Z"
    policy_hash = bind_trust(target, owner, args.date, now, vault_id, claude_trace)
    unresolved = []
    for path in target.rglob("*"):
        if path.is_file():
            try:
                if PLACEHOLDER_RE.search(path.read_text()):
                    unresolved.append(str(path.relative_to(target)))
            except UnicodeDecodeError:
                pass
    if unresolved:
        raise ValueError("unresolved placeholders: %s" % ", ".join(unresolved))
    print(json.dumps({
        "target": str(target), "zones": ["work", "personal", "client"],
        "vault_id": vault_id, "reflection_policy_sha256": policy_hash,
    }, indent=2))


if __name__ == "__main__":
    main()
