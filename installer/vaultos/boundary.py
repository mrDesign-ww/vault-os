"""The system/personal boundary inside mixed files: Claude settings, Obsidian JSON, Codex config.toml."""
import copy
import json
import re

# Keys the person owns even when a system key of the same file would otherwise claim them.
PERSONAL_KEYS = {
    "env", "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "otelHeadersHelper", "forceLoginMethod",
    "forceLoginOrgUUID", "model", "availableModels", "theme", "editorMode", "preferredNotifChannel",
    "autoUpdates", "autoUpdatesChannel", "cleanupPeriodDays", "feedbackSurveyState", "additionalDirectories",
    "sandbox", "enableAllProjectMcpServers", "enabledMcpjsonServers", "disabledMcpjsonServers", "defaultShell",
    "localPlugins", "skipDangerousModePermissionPrompt", "autoMode",
}
PERSONAL_PATTERN = re.compile(
    r"auth|token|secret|key|credential|password|login|helper|account|org|proxy|cert|mcp|aws|gcp|vertex|bedrock"
    r"|otel|dir|path|env", re.I)
# Maps replaced as a whole: the person's own entries do not survive.
WHOLE_MAPS = ("hooks", "enabledPlugins", "extraKnownMarketplaces", "skillOverrides", "modelSettings")
# Maps merged entry by entry: system entries set, other entries personal.
ENTRY_MAPS = ("env",)


def is_personal_key(key, template):
    if key in template:
        return False
    return key in PERSONAL_KEYS or bool(PERSONAL_PATTERN.search(key))


def desired_settings(current, template, old_system_env=()):
    """Rebuild a Claude settings object: personal keys of current, every system key from template.

    template is the settings a fresh install writes on an empty home. Returns (settings, notes) where notes lists
    (action, key) pairs for the plan.
    """
    current = current if isinstance(current, dict) else {}
    out, notes = {}, []
    for key, value in current.items():
        if key == "permissions" or key in ENTRY_MAPS:
            continue
        if is_personal_key(key, template):
            out[key] = copy.deepcopy(value)
        elif key not in template:
            notes.append(("remove", key))
    for key, value in template.items():
        if key == "permissions" or key in ENTRY_MAPS:
            continue
        if current.get(key) != value:
            notes.append(("set", key))
        out[key] = copy.deepcopy(value)
    for key in ENTRY_MAPS:
        sys_map = template.get(key)
        cur_map = current.get(key) if isinstance(current.get(key), dict) else {}
        merged = {}
        for name, value in cur_map.items():
            if sys_map and name in sys_map:
                continue
            if name in old_system_env:
                notes.append(("remove", "%s.%s" % (key, name)))
                continue
            merged[name] = value
        for name, value in (sys_map or {}).items():
            if cur_map.get(name) != value:
                notes.append(("set", "%s.%s" % (key, name)))
            merged[name] = value
        if merged or key in template:
            out[key] = merged
    perms = merge_permissions(current.get("permissions"), template.get("permissions"), notes)
    if perms is not None:
        out["permissions"] = perms
    return order_like(out, current, template), notes


def merge_permissions(current, template, notes):
    """defaultMode and the template's allow rules are system; every other rule and key is personal."""
    current = current if isinstance(current, dict) else {}
    template = template if isinstance(template, dict) else {}
    if not current and not template:
        return None
    out = {}
    for key, value in current.items():
        if key in ("defaultMode", "allow"):
            continue
        out[key] = copy.deepcopy(value)
    if "defaultMode" in template:
        if current.get("defaultMode") != template["defaultMode"]:
            notes.append(("set", "permissions.defaultMode"))
        out["defaultMode"] = template["defaultMode"]
    elif "defaultMode" in current:
        notes.append(("remove", "permissions.defaultMode"))
    sys_allow = list(template.get("allow", []))
    cur_allow = current.get("allow", []) if isinstance(current.get("allow"), list) else []
    allow = sys_allow + [rule for rule in cur_allow if rule not in sys_allow]
    if [r for r in sys_allow if r not in cur_allow]:
        notes.append(("set", "permissions.allow"))
    if allow or "allow" in current or "allow" in template:
        out["allow"] = allow
    return out


def order_like(out, current, template):
    """Keep the person's key order, then new system keys in template order (stable output, minimal diffs)."""
    ordered = {}
    for key in list(current) + list(template) + list(out):
        if key in out and key not in ordered:
            ordered[key] = out[key]
    return ordered


def settings_diff(current, desired):
    """Keys whose value differs between two settings objects (0 means parity)."""
    keys = set(current or {}) | set(desired or {})
    return sorted(k for k in keys if (current or {}).get(k) != (desired or {}).get(k))


def desired_json_keys(current, template):
    """Obsidian-style JSON: template keys are system, other keys personal."""
    current = current if isinstance(current, dict) else {}
    out = {k: copy.deepcopy(v) for k, v in current.items() if k not in template}
    for key, value in template.items():
        out[key] = copy.deepcopy(value)
    return order_like(out, current, template)


def dump_json(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ── Codex config.toml ─────────────────────────────────────────────────────────

HEADER = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(#.*)?$")
KEY = re.compile(r"^\s*([A-Za-z0-9_\-\"'.]+?)\s*=")
MARK_OPEN = "# >>> vault-os managed"
MARK_CLOSE = "# <<< vault-os managed"
SYSTEM_TABLE_ROOTS = ("marketplaces", "plugins")


def norm_header(text):
    return re.sub(r"\s*\.\s*", ".", text.strip())


def header_root(header):
    return re.split(r"[.\s]", header.strip().strip('"'), 1)[0]


def strip_managed(lines):
    out, inside = [], False
    for line in lines:
        if line.startswith(MARK_OPEN):
            inside = True
            continue
        if line.startswith(MARK_CLOSE):
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


def split_toml(text, keep_managed=False):
    """(preamble lines, [(normalized header, [lines])]) with multi-line strings kept inside their block."""
    preamble, blocks, current = [], [], None
    in_string = None
    lines = text.splitlines() if keep_managed else strip_managed(text.splitlines())
    for line in lines:
        if in_string is None:
            match = HEADER.match(line)
            if match:
                current = (norm_header(match.group(1)), [line])
                blocks.append(current)
                continue
        (preamble if current is None else current[1]).append(line)
        for quote in ('"""', "'''"):
            if line.count(quote) % 2 == 1 and in_string in (None, quote):
                in_string = None if in_string == quote else quote
    return preamble, blocks


def top_key(line):
    if line.lstrip().startswith("#"):
        return None
    match = KEY.match(line)
    return match.group(1).strip('"\'') if match else None


def is_system_header(header, template_headers):
    return header in template_headers or header_root(header) in SYSTEM_TABLE_ROOTS


def desired_codex_config(current, template):
    """Personal keys and tables of current byte for byte, the template's keys and tables in marked blocks."""
    t_pre, t_blocks = split_toml(template)
    t_keys = [line for line in t_pre if top_key(line)]
    t_names = {top_key(line) for line in t_keys}
    t_headers = {h for h, _ in t_blocks}
    c_pre, c_blocks = split_toml(current or "")
    personal_pre = []
    for line in c_pre:
        key = top_key(line)
        if key and (key in t_names or header_root(key) in SYSTEM_TABLE_ROOTS):
            continue
        personal_pre.append(line)
    personal_blocks = [lines for h, lines in c_blocks if not is_system_header(h, t_headers)]
    while personal_pre and not personal_pre[0].strip():
        personal_pre.pop(0)
    out = [MARK_OPEN + " keys: the upgrade sets these >>>"] + t_keys + [MARK_CLOSE + " keys <<<"]
    body = list(personal_pre)
    for lines in personal_blocks:
        body.extend(lines)
    while body and not body[-1].strip():
        body.pop()
    if body:
        out += [""] + body
    tables = []
    for _, lines in t_blocks:
        kept = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
        tables.append("\n".join(kept))
    out += ["", MARK_OPEN + " tables: the upgrade replaces marketplaces, plugins and these tables >>>",
            "\n\n".join(tables), MARK_CLOSE + " tables <<<"]
    return "\n".join(out) + "\n"


def codex_system_view(text, template):
    """The system part of a config.toml as comparable values: template keys and the system tables, comments and
    layout ignored. Two files with the same view need no rewrite."""
    t_pre, t_blocks = split_toml(template)
    t_names = {top_key(line) for line in t_pre if top_key(line)}
    t_headers = {h for h, _ in t_blocks}
    pre, blocks = split_toml(text or "", keep_managed=True)
    keys = sorted(line.strip() for line in pre if top_key(line) and (
        top_key(line) in t_names or header_root(top_key(line)) in SYSTEM_TABLE_ROOTS))
    tables = sorted((h, tuple(line.strip() for line in lines[1:] if line.strip() and not line.lstrip().startswith("#")))
                    for h, lines in blocks if is_system_header(h, t_headers))
    return keys, tables


def codex_config_notes(current, template):
    _, t_blocks = split_toml(template)
    t_headers = {h for h, _ in t_blocks}
    _, c_blocks = split_toml(current or "")
    return ["[%s]" % h for h, _ in c_blocks if is_system_header(h, t_headers) and h not in t_headers]


# ── vault .gitignore and Obsidian plugin lists ───────────────────────────────

def gitignore_rule(rel, folder=False):
    """An anchored rule that matches exactly the vault path rel (special characters escaped); None when a rule
    cannot express it."""
    if "\n" in rel or "\r" in rel:
        return None
    out = "".join("\\" + ch if ch in "\\*?[" else ch for ch in rel)
    stripped = out.rstrip(" ")
    out = stripped + "\\ " * (len(out) - len(stripped))
    return "/" + out + ("/" if folder else "")


def desired_gitignore(current, template, extra_rules=()):
    """The person's rules (outside the managed block, minus exact template lines) first, then the system block.
    No file, or nothing personal, gives the template exactly. Git lets the last matching rule win, so system rules
    such as the authority-receipt negation keep working."""
    tpl_lines = template.splitlines()
    tpl_set = {line.rstrip() for line in tpl_lines if line.strip()}
    personal = []
    if current is not None:
        inside = False
        for line in current.splitlines():
            if line.startswith(MARK_OPEN):
                inside = True
                continue
            if line.startswith(MARK_CLOSE):
                inside = False
                continue
            if not inside and line.rstrip() not in tpl_set:
                personal.append(line)
    have = {line.rstrip() for line in personal}
    extra = [rule for rule in extra_rules if rule and rule not in have]
    while personal and not personal[-1].strip():
        personal.pop()
    while personal and not personal[0].strip():
        personal.pop(0)
    if not personal and not extra:
        return template
    body = personal + extra
    block = [MARK_OPEN + ": system rules, replaced on upgrade >>>"] + tpl_lines + [MARK_CLOSE + " <<<"]
    return "\n".join(body + [""] + block) + "\n"


def desired_core_plugins(current, template):
    """Template plugins set true are on; template plugins set false keep the person's value; other keys personal."""
    current = current if isinstance(current, dict) else {}
    out = {k: copy.deepcopy(v) for k, v in current.items()}
    for key, value in template.items():
        if value is True or key not in current:
            out[key] = copy.deepcopy(value)
    return order_like(out, current, template)


def desired_community_plugins(current, template, old_only=(), drop_old=False):
    """The person's plugin ids in their order, missing template ids appended. Ids only an earlier release's template
    listed are dropped only when the vault had Vault OS before (drop_old)."""
    if not isinstance(current, list):
        return list(template)
    out = []
    for item in current:
        if drop_old and item in old_only and item not in template:
            continue
        if item not in out:
            out.append(item)
    for item in template:
        if item not in out:
            out.append(item)
    return out
