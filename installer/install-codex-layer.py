#!/usr/bin/env python3
"""Merge the Vault OS Codex layer into a Codex home (default ~/.codex).

config.toml: top-level keys and [tables] of shell/codex/config.template.toml that the user's file lacks are added;
anything the user already has is left as it is. hooks.json: the hooks of shell/codex/hooks.template.json are added
when their command is not there yet; macOS-only hooks (afplay, keep-awake) only on macOS. --mcp NAME adds a URL-only
[mcp_servers.NAME] block from shell/claude/mcp-servers.json (OAuth on first use, no token). Both files are backed up
before a change. Never touches auth.json, [hooks.state], sessions or history. Standard library only.

  python3 installer/install-codex-layer.py --vault /abs/vault [--codex-home PATH] [--platform darwin|linux|win32]
      [--mcp NAME ...] [--dry-run]

Prints a JSON report.
"""
import argparse, json, os, re, shutil, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODEX = os.path.join(ROOT, "shell", "codex")
MCP = os.path.join(ROOT, "shell", "claude", "mcp-servers.json")
HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")
KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
MAC_ONLY = ("afplay", "keep-awake.sh")


def norm(header):
    return re.sub(r"\s*\.\s*", ".", header.strip())


def split_toml(text):
    """(top-level key lines {key: line}, [(header, block text)]) of a simple TOML file."""
    top, blocks, current = {}, [], None
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            current = [norm(m.group(1)), [line]]
            blocks.append(current)
            continue
        if current is None:
            k = KEY.match(line)
            if k and not line.lstrip().startswith("#"):
                top[k.group(1)] = line
        elif line.strip() and not line.lstrip().startswith("#"):
            current[1].append(line)
    return top, [(h, "\n".join(lines)) for h, lines in blocks]


def merge_config(current, template, mcp_blocks=()):
    cur_top, cur_blocks = split_toml(current)
    tpl_top, tpl_blocks = split_toml(template)
    have = {h for h, _ in cur_blocks}
    notes, add_top, add_blocks = [], [], []
    for key, line in tpl_top.items():
        if key not in cur_top:
            add_top.append(line)
            notes.append("config: added %s" % key)
    for header, block in list(tpl_blocks) + list(mcp_blocks):
        if header not in have:
            add_blocks.append(block)
            have.add(header)
            notes.append("config: added [%s]" % header)
    if not notes:
        return current, notes
    lines = current.splitlines()
    first = next((i for i, line in enumerate(lines) if HEADER.match(line)), len(lines))
    out = lines[:first] + add_top + lines[first:]
    text = "\n".join(out).rstrip("\n")
    if add_blocks:
        text += "\n\n# --- Added by Vault OS (install-codex-layer.py) ---\n" + "\n\n".join(add_blocks)
    return text.lstrip("\n") + "\n", notes


def merge_hooks(current, template, platform):
    merged = json.loads(json.dumps(current or {}))
    hooks = merged.setdefault("hooks", {})
    notes = []
    for event, groups in template["hooks"].items():
        existing = {h.get("command") for g in hooks.get(event, []) for h in g.get("hooks", [])}
        for group in groups:
            keep = [h for h in group["hooks"] if platform == "darwin" or not any(m in h["command"] for m in MAC_ONLY)]
            keep = [h for h in keep if h["command"] not in existing]
            if not keep:
                continue
            entry = dict(group, hooks=keep)
            hooks.setdefault(event, []).append(entry)
            for h in keep:
                existing.add(h["command"])
                notes.append("hooks %s: %s" % (event, h["command"][:60]))
    return merged, notes


def config_template():
    """shell/codex/config.template.toml plus the held-back plugins of shell/claude/optional-plugins.json, each added
    disabled. The engine stages Codex from this too, so both routes agree."""
    with open(os.path.join(CODEX, "config.template.toml"), encoding="utf-8") as f:
        template = f.read()
    with open(os.path.join(ROOT, "shell", "claude", "optional-plugins.json"), encoding="utf-8") as f:
        optional = json.load(f)
    for p in optional.get("plugins", []):
        template += '\n[marketplaces.%s]\nsource_type = "git"\nsource = "https://github.com/%s.git"\n' % (
            p["marketplace"], p["github"])
        template += '\n[plugins."%s"]\nenabled = false\n' % p["plugin"]
    return template


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", required=True, help="absolute vault path, substituted for {{VAULT_PATH}}")
    ap.add_argument("--codex-home", default=os.path.join(os.path.expanduser("~"), ".codex"))
    ap.add_argument("--platform", choices=("darwin", "linux", "win32"))
    ap.add_argument("--mcp", action="append", default=[], help="add a URL-only MCP server block by name")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    platform = a.platform or ("darwin" if sys.platform == "darwin" else "win32" if sys.platform.startswith("win") else "linux")
    home = os.path.abspath(os.path.expanduser(a.codex_home))
    vault = os.path.abspath(os.path.expanduser(a.vault)).replace("\\", "/")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report = {"config": [], "hooks": [], "backed_up": []}

    with open(MCP, encoding="utf-8") as f:
        servers = {s["name"]: s for s in json.load(f)["servers"]}
    unknown = set(a.mcp) - set(servers)
    if unknown:
        raise SystemExit("unknown MCP server: %s" % ", ".join(sorted(unknown)))
    mcp_blocks = [("mcp_servers.%s" % n, '[mcp_servers.%s]\nurl = "%s"' % (n, servers[n]["url"])) for n in a.mcp]

    def write(path, text, notes, key):
        report[key] = notes
        if not notes or a.dry_run:
            return
        os.makedirs(home, exist_ok=True)
        if os.path.exists(path):
            shutil.copy2(path, "%s.backup-%s" % (path, stamp))
            report["backed_up"].append("%s.backup-%s" % (path, stamp))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    cfg = os.path.join(home, "config.toml")
    current = open(cfg, encoding="utf-8").read() if os.path.exists(cfg) else ""
    text, notes = merge_config(current, config_template(), mcp_blocks)
    write(cfg, text, notes, "config")

    hp = os.path.join(home, "hooks.json")
    current_hooks = json.load(open(hp, encoding="utf-8")) if os.path.exists(hp) else {}
    with open(os.path.join(CODEX, "hooks.template.json"), encoding="utf-8") as f:
        tpl = json.loads(f.read().replace("{{VAULT_PATH}}", vault))
    merged, notes = merge_hooks(current_hooks, tpl, platform)
    write(hp, json.dumps(merged, indent=2, ensure_ascii=False) + "\n", notes, "hooks")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
