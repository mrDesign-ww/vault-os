"""The desired state of every system path, built in a staging folder by the installers' own code.

Fresh install and upgrade agree by construction: the Claude layer is staged with install-claude-layer.py's
Installer on an empty home, the Codex hooks with install-codex-layer.py's merge on an empty file, and the vault
with render-vault.py's render_files and bind_trust for the real vault path. See UPGRADE.md sections 2 and 3.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys

from . import boundary, fsx

CLAUDE_OWNED_DIRS = ("agents", "hooks", "docs", "templates", "statusline", "scripts", "skills", "commands",
                     "output-styles")
VAULT_CLAUDE_DIRS = ("skills", "agents", "commands", "output-styles", "hooks")
DOCTRINE = (
    "wiki/resources/concepts/Autonomous Reflection Delegation Policy.md",
    "wiki/resources/concepts/L0-L3 Memory Model for the Vault.md",
)
DOCTRINE_SHA = re.compile(r'(?m)^(reflection_policy_sha256: ")([0-9a-f]{64}|\{\{REFLECTION_POLICY_SHA256\}\})(")')
JSON_KEYS = re.compile(r"^\.obsidian/(app\.json|plugins/[^/]+/data\.json)$")
VAULT_SPLIT_KINDS = {".gitignore": "gitignore", ".obsidian/core-plugins.json": "core-plugins",
                     ".obsidian/community-plugins.json": "community-plugins"}
NOTE_SUFFIXES = (".md", ".canvas", ".base")
SHIPPED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shipped-versions.json")
LEGACY_MIRROR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legacy-mirror.json")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
VAULT_SETTINGS_SPLIT = ".claude/settings.local.json"
APPROVALS = ".vault-meta/memory-approvals.json"
EVALS = ".vault-meta/memory-evals.json"
MEMORY_MODEL = "scripts/memory-model.py"


class Layer:
    """Desired state of one root: exact entries, owned folders, paths that must be absent, split files, seeds."""

    def __init__(self, name, root):
        self.name, self.root = name, root
        self.entries = {}     # rel -> {"src": staged path, "state": fsx.entry_state}
        self.owned_dirs = {}  # rel dir -> set of child names that may exist
        self.absent = set()   # rel paths that must not exist
        self.protected = set()  # rel paths left exactly as they are (adopted upstream installs)
        self.splits = {}      # rel -> {"kind": ..., "template": ...}
        self.seeds = {}       # rel -> staged path, created only when missing
        self.leftovers = {}   # owned dir -> {child: tree shas of earlier mirrored copies}; removed only when equal
        self.gone = set()     # vault: links the migration renames away first; their paths plan as absent

    def add_entry(self, rel, src):
        self.entries[rel] = {"src": src, "state": fsx.entry_state(src)}

    def own_dir(self, rel, staged_dir):
        """Every non-dot child of staged_dir is an entry; any other non-dot child of the live folder is removed."""
        names = set()
        if os.path.isdir(staged_dir):
            for name in sorted(os.listdir(staged_dir)):
                if name.startswith(".") or fsx.is_junk(name):
                    continue
                self.add_entry(rel + "/" + name, os.path.join(staged_dir, name))
                names.add(name)
        self.owned_dirs[rel] = names

    def system_paths(self):
        return sorted(set(self.entries) | set(self.splits) | set(self.protected))

    def describe(self):
        return {
            "entries": {rel: e["state"] for rel, e in sorted(self.entries.items())},
            "owned_dirs": {rel: sorted(n) for rel, n in sorted(self.owned_dirs.items())},
            "absent": sorted(self.absent), "protected": sorted(self.protected),
            "splits": sorted(self.splits), "seeds": sorted(self.seeds),
        }


def mirror_leftovers(layer, staged_skills, claude_skills):
    """Earlier releases mirrored every bundled Claude skill into Codex and ~/.agents. A folder this release no longer
    ships there goes only when it still equals what 28777ca or the Claude set installed; an edited one stays."""
    with open(LEGACY_MIRROR_PATH, encoding="utf-8") as handle:
        old = json.load(handle)["skills"]
    found = {}
    for name in sorted(os.listdir(claude_skills)):
        src = os.path.join(claude_skills, name)
        if name.startswith(".") or not os.path.isdir(src) or os.path.exists(os.path.join(staged_skills, name)):
            continue
        found[name] = {fsx.tree_sha(src)[0]} | ({old[name]} if name in old else set())
    for name, sha in old.items():
        if name not in found and not os.path.exists(os.path.join(staged_skills, name)):
            found[name] = {sha}
    layer.leftovers["skills"] = found


def load_installers(repo):
    icl = fsx.load_module(os.path.join(repo, "installer", "install-claude-layer.py"), "vaultos_icl")
    icx = fsx.load_module(os.path.join(repo, "installer", "install-codex-layer.py"), "vaultos_icx")
    rv = fsx.load_module(os.path.join(repo, "installer", "render-vault.py"), "vaultos_rv")
    return icl, icx, rv


# ── Claude, Codex and agents layers ──────────────────────────────────────────

def stage_claude(repo, answers, homes, stage):
    icl, icx, _ = load_installers(repo)
    staged_home = os.path.join(stage, "claude")
    inst = icl.Installer(staged_home, False, False, answers["platform"], answers["python"], answers["owner"],
                         answers["language"])
    inst.values["{{CLAUDE_HOME}}"] = icl.slash(homes["claude"])
    inst.bypass = bool(answers.get("bypass_permissions"))
    inst.file_layers()
    inst.skills(os.path.join(staged_home, "skills"), "skills")
    inst.claude_md()
    inst.settings()
    with open(os.path.join(staged_home, "settings.json"), encoding="utf-8") as handle:
        settings = json.load(handle)
    layer = Layer("claude", homes["claude"])
    if os.path.isfile(os.path.join(staged_home, "CLAUDE.md")):
        layer.add_entry("CLAUDE.md", os.path.join(staged_home, "CLAUDE.md"))
    else:
        layer.absent.add("CLAUDE.md")
    for folder in CLAUDE_OWNED_DIRS:
        layer.own_dir(folder, os.path.join(staged_home, folder))
    layer.splits["settings.json"] = {"kind": "claude-settings", "template": settings}
    layers = {"claude": layer}
    if answers.get("codex"):
        layers.update(stage_codex(repo, icl, icx, answers, homes, stage))
    return layers


def stage_codex(repo, icl, icx, answers, homes, stage):
    codex_dir = os.path.join(stage, "codex")
    agents_dir = os.path.join(stage, "agents")
    inst = icl.Installer(os.path.join(stage, "unused-claude"), False, False, answers["platform"], answers["python"],
                         answers["owner"], answers["language"])
    inst.skills(os.path.join(codex_dir, "skills"), "codex")
    inst.skills(os.path.join(agents_dir, "skills"), "agents")
    with open(os.path.join(repo, "shell", "codex", "hooks.template.json"), encoding="utf-8") as handle:
        tpl = json.loads(handle.read().replace("{{VAULT_PATH}}", answers["vault"].replace("\\", "/")))
    merged, _ = icx.merge_hooks({}, tpl, answers["platform"])
    os.makedirs(codex_dir, exist_ok=True)
    with open(os.path.join(codex_dir, "hooks.json"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    config = icx.config_template()
    codex = Layer("codex", homes["codex"])
    codex.add_entry("hooks.json", os.path.join(codex_dir, "hooks.json"))
    codex.own_dir("skills", os.path.join(codex_dir, "skills"))
    codex.own_dir("prompts", os.path.join(codex_dir, "prompts"))
    codex.absent.add("AGENTS.md")
    codex.splits["config.toml"] = {"kind": "codex-toml", "template": config}
    agents = Layer("agents", homes["agents"])
    agents.own_dir("skills", os.path.join(agents_dir, "skills"))
    mirror_leftovers(codex, os.path.join(codex_dir, "skills"), os.path.join(stage, "claude", "skills"))
    mirror_leftovers(agents, os.path.join(agents_dir, "skills"), os.path.join(stage, "claude", "skills"))
    return {"codex": codex, "agents": agents}


# ── upstream skills ─────────────────────────────────────────────────────────

def git_head(path):
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def upstream_children(skills_dir, name, recorded=()):
    """Entries an upstream setup created next to its own folder: recorded ones, <name>-*, _<name>*, folders that
    carry its ownership marker (.<name>-owned), links into it."""
    out = set(recorded)
    if not os.path.isdir(skills_dir):
        return sorted(n for n in out if fsx.lexists(os.path.join(skills_dir, n)))
    main = os.path.realpath(os.path.join(skills_dir, name))
    marker = ".%s-owned" % name
    for child in os.listdir(skills_dir):
        if child == name or child.startswith("."):
            continue
        path = os.path.join(skills_dir, child)
        if child.startswith((name + "-", "_" + name)):
            out.add(child)
        elif os.path.isdir(path) and not os.path.islink(path) and os.path.isfile(os.path.join(path, marker)):
            out.add(child)
        elif os.path.islink(path):
            target = os.path.realpath(path)
            if target == main or target.startswith(main + os.sep):
                out.add(child)
    return sorted(n for n in out if fsx.lexists(os.path.join(skills_dir, n)))


def plan_upstream(manifest_path, answers, homes, stamp):
    """[{item, status: adopt|install|unavailable, children, reason, hosts}] for the selected upstream skills."""
    if not answers.get("upstream_skills"):
        return []
    with open(manifest_path, encoding="utf-8") as handle:
        items = json.load(handle)["skills"]
    recorded = ((stamp or {}).get("upstream") or {})
    skills_dir = os.path.join(homes["claude"], "skills")
    out = []
    for item in items:
        if answers["platform"] not in item["platforms"]:
            continue
        name = item["name"]
        dest = os.path.join(skills_dir, name)
        rec = recorded.get(name) or {}
        children = upstream_children(skills_dir, name, rec.get("children", ())) if item["kind"] == "setup" else []
        missing = [tool for tool in item.get("requires", []) if shutil.which(tool) is None]
        status, reason = "install", "not installed"
        if item["kind"] == "setup" and os.path.isdir(dest) and not os.path.islink(dest):
            head = git_head(dest)
            if head == item["commit"]:
                status, reason = "adopt", "installed at the pinned commit"
            else:
                reason = "installed at %s, pinned %s" % ((head or "unknown")[:8], item["commit"][:8])
        elif item["kind"] == "folder" and os.path.isdir(dest) and not os.path.islink(dest):
            digest, _ = fsx.tree_sha(dest)
            if rec.get("commit") == item["commit"] and rec.get("tree_sha256") == digest:
                status, reason = "adopt", "installed at the pinned commit"
            else:
                reason = "folder differs from the pinned commit"
        if status == "install" and missing:
            status, reason = "unavailable", "needs %s" % ", ".join(missing)
        out.append({"item": item, "status": status, "children": children, "reason": reason,
                    "hosts": plan_upstream_hosts(item, answers, homes, rec, status, missing)})
    return out


UPSTREAM_HOSTS = ("codex", "agents")


def plan_upstream_hosts(item, answers, homes, rec, claude_status, missing):
    """The item's copies for Codex and ~/.agents (only with --codex): {host: {name, status, children, reason}}.
    A setup kind runs its own setup for the host from the Claude checkout (gstack: --host codex); a folder kind
    places the same pinned folder under the host's name."""
    out = {}
    if not answers.get("codex"):
        return out
    for host, cfg in sorted((item.get("hosts") or {}).items()):
        if host not in UPSTREAM_HOSTS:
            continue
        name = cfg.get("name", item["name"])
        skills_dir = os.path.join(homes[host], "skills")
        dest = os.path.join(skills_dir, name)
        hrec = (rec.get("hosts") or {}).get(host) or {}
        live = os.path.isdir(dest) and not os.path.islink(dest)
        children, status, reason = [], "install", "not installed for %s" % host
        if item["kind"] == "setup":
            children = upstream_children(skills_dir, name, hrec.get("children", ()))
            if claude_status == "install":
                reason = "the Claude install is redone"
            elif live and hrec.get("commit", item["commit"]) == item["commit"]:
                status, reason = "adopt", "set up at the pinned commit"
            if status == "install" and claude_status == "unavailable":
                status, reason = "unavailable", "the Claude install is unavailable"
        elif live:
            if hrec.get("commit") == item["commit"] and hrec.get("tree_sha256") == fsx.tree_sha(dest)[0]:
                status, reason = "adopt", "installed at the pinned commit"
            else:
                reason = "folder differs from the pinned commit"
        if status == "install" and missing:
            status, reason = "unavailable", "needs %s" % ", ".join(missing)
        out[host] = {"name": name, "status": status, "children": children, "reason": reason}
    return out


def apply_upstream_to_layer(layer, upstream):
    """Upstream folders are not staged: adopted and unavailable ones are protected, installs are planned.
    The claude layer takes the items themselves, the codex and agents layers their host copies."""
    skills = layer.owned_dirs.setdefault("skills", set())
    for u in upstream:
        if layer.name == "claude":
            name, status, children = u["item"]["name"], u["status"], u["children"]
        else:
            host = (u.get("hosts") or {}).get(layer.name)
            if not host:
                continue
            name, status, children = host["name"], host["status"], host["children"]
        rel = "skills/" + name
        layer.entries.pop(rel, None)
        skills.add(name)
        if status in ("adopt", "unavailable"):
            if fsx.lexists(os.path.join(layer.root, "skills", name)):
                layer.protected.add(rel)
            for child in children:
                skills.add(child)
                layer.protected.add("skills/" + child)
        else:
            if layer.name != "claude" and u["item"]["kind"] == "setup" \
                    and fsx.lexists(os.path.join(layer.root, "skills", name)):
                layer.absent.add(rel)  # a host setup rebuilds its own runtime folder: archive the old one first
            for child in children:
                layer.absent.add("skills/" + child)


UPSTREAM_HOOK = "/skills/%s(/|-)"


def is_upstream_group(group, names):
    if not names:
        return False
    pattern = re.compile("|".join(UPSTREAM_HOOK % re.escape(n) for n in names))
    return any(pattern.search(str(h.get("command", ""))) for h in group.get("hooks", []) if isinstance(h, dict))


def desired_claude_settings(current, split, keep_upstream=(), old_env=(), old_allow=()):
    """Settings rebuilt by the boundary rules; hooks of kept upstream installs survive after the system hooks."""
    template = copy.deepcopy(split["template"])
    current = current if isinstance(current, dict) else {}
    if keep_upstream:
        hooks = template.setdefault("hooks", {})
        for event, groups in (current.get("hooks") or {}).items():
            for group in groups if isinstance(groups, list) else []:
                if isinstance(group, dict) and is_upstream_group(group, keep_upstream) \
                        and group not in hooks.get(event, []):
                    hooks.setdefault(event, []).append(copy.deepcopy(group))
    out, notes = boundary.desired_settings(current, template, old_env)
    tpl_allow = (template.get("permissions") or {}).get("allow", [])
    if old_allow and isinstance(out.get("permissions"), dict) and isinstance(out["permissions"].get("allow"), list):
        kept = [r for r in out["permissions"]["allow"] if r in tpl_allow or r not in old_allow]
        if kept != out["permissions"]["allow"]:
            notes.append(("remove", "permissions.allow (old system rules)"))
            out["permissions"]["allow"] = kept
    return out, notes


# ── vault ───────────────────────────────────────────────────────────────────

def normalized_doctrine(text):
    return DOCTRINE_SHA.sub(r"\1<POLICY>\3", text)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def live_trust(vault):
    """What the live vault already carries: the doctrine approval date, own L3 approvals, own eval cases."""
    approvals = read_json(os.path.join(vault, *APPROVALS.split("/"))) or {}
    l3 = approvals.get("l3") if isinstance(approvals.get("l3"), dict) else {}
    doctrine = l3.get(DOCTRINE[0]) or {}
    evals = read_json(os.path.join(vault, *EVALS.split("/"))) or {}
    cases = evals.get("cases") if isinstance(evals.get("cases"), list) else []
    own = {path: entry for path, entry in l3.items() if path not in DOCTRINE and isinstance(entry, dict)}
    return {"approved_on": doctrine.get("approved_on"), "approved_by": doctrine.get("approved_by"),
            "own_approvals": own, "own_cases": cases}


def render_vault(repo, answers, stage_dir, own_approvals=None, own_cases=None):
    """Render and bind the vault template in stage_dir for the real vault path; return the staged root."""
    _, _, rv = load_installers(repo)
    from pathlib import Path
    root = Path(stage_dir)
    target = Path(answers["vault"])
    if root.exists():
        shutil.rmtree(str(root))
    root.mkdir(parents=True)
    vault_id, claude_trace = rv.render_files(root, target, answers["owner"], answers["title"], answers["language"],
                                             answers["client_label"], answers["date"])
    rv.bind_trust(root, answers["owner"], answers["date"], answers["date"] + "T00:00:00Z", vault_id, claude_trace,
                  vault_path=target)
    if own_approvals or own_cases:
        reseal_with_own(rv, root, own_approvals or {}, own_cases or [])
    return str(root)


def reseal_with_own(rv, root, own_approvals, own_cases):
    """Carry the person's own L3 approvals and eval cases into the staged chain and re-seal it there."""
    approvals_path = root / ".vault-meta" / "memory-approvals.json"
    approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
    for path, entry in own_approvals.items():
        approvals["l3"].setdefault(path, entry)
    rv.write_json(approvals_path, approvals)
    evals_path = root / ".vault-meta" / "memory-evals.json"
    evals = json.loads(evals_path.read_text(encoding="utf-8"))
    evals["cases"] = list(own_cases)
    rv.write_json(evals_path, evals)
    model = root / "scripts" / "memory-model.py"
    rv.replace_once(model, r'^EVALS_MANIFEST_SHA256 = "[0-9a-f]{64}"$',
                    'EVALS_MANIFEST_SHA256 = "%s"' % rv.sha256(evals_path))
    reseal = fsx.load_module(root / ".vault-meta" / "evolution" / "reseal-policy.py", "vaultos_stage_reseal")
    for _ in range(3):
        contents = reseal.sealed_contents()
        changed = reseal.drifted(contents)
        if not changed:
            return
        for path, text in contents.items():
            path.write_text(text, encoding="utf-8")
    raise RuntimeError("the staged trust chain did not converge")


def vault_layer(staged_root, vault):
    layer = Layer("vault", vault)
    for rel, path in fsx.walk_entries(staged_root):
        if rel.startswith(".claude/") or rel.startswith(".agents/"):
            continue
        if rel.startswith("wiki/") and rel not in DOCTRINE:
            layer.seeds[rel] = path
        elif rel in VAULT_SPLIT_KINDS:
            kind = VAULT_SPLIT_KINDS[rel]
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            layer.splits[rel] = {"kind": kind, "template": text if kind == "gitignore" else json.loads(text)}
        elif JSON_KEYS.match(rel):
            layer.splits[rel] = {"kind": "json-keys", "template": read_json(path)}
        else:
            layer.add_entry(rel, path)
    claude_dir = os.path.join(staged_root, ".claude")
    for name in VAULT_CLAUDE_DIRS:
        rel = ".claude/" + name
        if os.path.isdir(os.path.join(claude_dir, name)):
            layer.own_dir(rel, os.path.join(claude_dir, name))
        else:
            layer.absent.add(rel)
    layer.add_entry(".claude/settings.json", os.path.join(claude_dir, "settings.json"))
    layer.splits[VAULT_SETTINGS_SPLIT] = {"kind": "claude-settings",
                                          "template": read_json(os.path.join(claude_dir, "settings.local.json"))}
    layer.own_dir(".agents/skills", os.path.join(staged_root, ".agents", "skills"))
    for rel in sorted(os.listdir(os.path.join(staged_root, ".agents"))):
        if rel != "skills" and not rel.startswith("."):
            layer.add_entry(".agents/" + rel, os.path.join(staged_root, ".agents", rel))
    return layer


def leftover_states(layer, old_manifest):
    """{rel: stamped state} of the system paths of the previous release that this release no longer ships."""
    if not old_manifest:
        return {}
    current = set(layer.entries) | set(layer.splits) | set(layer.seeds) | layer.protected
    out = {}
    for rel, state in old_manifest.get("entries", {}).items():
        if rel in current or any(rel.startswith(d + "/") for d in layer.owned_dirs):
            continue
        out[rel] = state
    return out


def leftovers(layer, old_manifest, keep=()):
    """Mark the previous release's leftovers absent; keep names leftovers that stay (changed by the person)."""
    for rel in leftover_states(layer, old_manifest):
        if rel not in keep:
            layer.absent.add(rel)


def changed_leftovers(layer, old_manifest):
    """Vault leftovers whose live state differs from the stamped one: personal now, they stay (UPGRADE.md 2.1)."""
    out = set()
    for rel, state in leftover_states(layer, old_manifest).items():
        live = fsx.entry_state(os.path.join(layer.root, *rel.split("/")))
        if live["type"] != "absent" and not fsx.same_state(live, state):
            out.add(rel)
    return out


# ── vault path classes and shipped note versions (UPGRADE.md 2.1) ────────────

def is_mixed_path(rel):
    return rel == ".gitignore" or (rel.startswith(".obsidian/") and rel.endswith(".json"))


def is_note_path(rel):
    if is_mixed_path(rel):
        return False
    if rel.startswith("Reference/") or rel in DOCTRINE:
        return True
    return rel.lower().endswith(NOTE_SUFFIXES) and rel not in ("CLAUDE.md", "AGENTS.md") \
        and rel.split("/")[0].lower() != "scripts" and not any(part.startswith(".") for part in rel.split("/"))


def is_config_path(rel):
    return not is_mixed_path(rel) and not is_note_path(rel)


def vault_path_class(rel):
    """note (a person may own a file there: moved, never replaced in place), config (archived and replaced) or
    mixed (merged)."""
    classes = [name for name, test in (("note", is_note_path), ("config", is_config_path),
                                       ("mixed", is_mixed_path)) if test(rel)]
    if len(classes) != 1:
        raise ValueError("vault path %s has classes %s" % (rel, classes))
    return classes[0]


def normalized_note(text, owner=None):
    """A shipped note with its render-time values (dates, owner, policy hash) folded into tokens."""
    text = normalized_doctrine(text.replace("\r\n", "\n"))
    text = ISO_DATE.sub("<DATE>", text.replace("{{DATE}}", "<DATE>")).replace("{{OWNER_NAME}}", "<OWNER>")
    if owner:
        text = text.replace(owner, "<OWNER>")
    return text


def normalized_note_sha(text, owner=None):
    return fsx.sha256_text(normalized_note(text, owner))


def shipped_versions():
    return read_json(SHIPPED_PATH) or {"notes": {}, "community_plugins_old": []}


def is_shipped_note(rel, path, owner=None):
    """The live file at path equals a version of the template note rel that a release (28777ca or later) shipped."""
    known = set(shipped_versions().get("notes", {}).get(rel, []))
    if not known:
        return False
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return normalized_note_sha(text) in known or normalized_note_sha(text, owner) in known


def source_identity(repo):
    """Commit (when the repository is a git checkout) and a hash of the shipped tree."""
    commit = git_head(repo)
    lines = []
    for top in ("installer", "shell", "VERSION"):
        path = os.path.join(repo, top)
        if os.path.isdir(path):
            lines.extend(top + "/" + line for line in fsx.tree_lines(path))
        elif os.path.isfile(path):
            lines.append("%s\0-\0%s" % (top, fsx.sha256_file(path)))
    return {"commit": commit, "tree_sha256": fsx.sha256_text("\n".join(lines))}


def read_version(repo):
    try:
        with open(os.path.join(repo, "VERSION"), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return "0"


def python_default():
    return sys.executable
