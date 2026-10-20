#!/usr/bin/env python3
"""Install the global Claude layer of Vault OS into a Claude home (default ~/.claude).

Layers: agents, hooks, docs, templates, statusline, scripts, skills, plus a rendered CLAUDE.md and a
settings.json merge. Optional steps: skills installed from their upstream source at a pinned commit
(--upstream-skills, from shell/claude/upstream-skills.json), the owner's bypass permission mode
(--bypass-permissions) and user-scope MCP servers (--mcp, from shell/claude/mcp-servers.json, URL only,
OAuth on first use). Standard library only; works on macOS, Linux and Windows.

Never overwrites a user file. An existing file or skill folder that differs is skipped, or, with
--backup, moved aside to <name>.backup-<timestamp> before the new copy lands. settings.json is always
backed up first, then only missing keys and entries are added: existing user values always win.
Hooks come from shell/claude/hooks/hooks.json and are filtered per platform, so macOS-only hooks
never reach Windows or Linux.

  python3 installer/install-claude-layer.py --owner "Owner Name" --language English [--backup]
      [--claude-home PATH] [--platform darwin|linux|win32] [--python PATH] [--extra-skills-dir DIR ...]
      [--upstream-skills] [--bypass-permissions] [--mcp default|all|NAME ...] [--claude-bin PATH] [--dry-run]

Prints a JSON report of every action.

Replacing an existing system instead of adding to it (the full upgrade, see installer/UPGRADE.md):
  python3 installer/install-claude-layer.py --upgrade --vault /abs/vault [answers] [--confirm PLAN_ID]
  python3 installer/install-claude-layer.py --rollback ARCHIVE [--confirm ROLLBACK_ID]
Both hand over to installer/vault-os.py, which plans or previews first and changes nothing without --confirm.
"""
import argparse, filecmp, json, os, shutil, stat, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL = os.path.join(ROOT, "shell", "claude")
# Skills only Codex (~/.codex/skills) and the shared ~/.agents/skills get, each to the targets listed in the JSON.
HOST_SKILLS = os.path.join(ROOT, "shell", "codex", "skills")
HOST_SKILL_TARGETS = os.path.join(ROOT, "shell", "codex", "skill-targets.json")
SKILL_HOSTS = {".codex": "codex", ".agents": "agents"}  # parent folder of a skills dir -> host
FILE_LAYERS = {  # layer folder -> extensions copied
    "agents": (".md",),
    "hooks": (".py",),
    "docs": (".md",),
    "templates": (".md",),
    "statusline": (".py",),
    "scripts": (".sh",),
}
POSIX_ONLY_SCRIPTS = {"keep-awake.sh", "wait-no-chromium.sh"}  # shell scripts: not copied on Windows
PLATFORMS = ("darwin", "linux", "win32")
MAP_KEYS = ("enabledPlugins", "extraKnownMarketplaces", "skillOverrides", "modelSettings", "env")  # merged entry by entry
# The owner's permission mode, written only with --bypass-permissions (an explicit quiz answer).
BYPASS_ALLOW = ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)", "WebFetch(*)", "WebSearch(*)", "Agent(*)", "Glob(*)",
                "Grep(*)", "LS(*)"]


def statusline_lang(language):
    """Status line labels: Russian when the chosen language is Russian, else English."""
    low = language.strip().lower()
    return "ru" if low.startswith(("ru", "рус")) else "en"


def platform_name(value=None):
    p = value or sys.platform
    if p.startswith("linux"):
        return "linux"
    if p.startswith("win") or p == "cygwin":
        return "win32"
    if p == "darwin":
        return "darwin"
    raise SystemExit("unsupported platform: %s" % p)


def slash(path):
    return path.replace("\\", "/")


def skills_host(target, prefix=None):
    """'codex' or 'agents' for a Codex or shared agents skills folder (by prefix or by its parent), else None."""
    if prefix in SKILL_HOSTS.values():
        return prefix
    return SKILL_HOSTS.get(os.path.basename(os.path.dirname(os.path.abspath(target).rstrip("/\\"))))


def host_skill_names(host):
    """Names of shell/codex/skills that ship to this host, from shell/codex/skill-targets.json."""
    if host is None or not os.path.isfile(HOST_SKILL_TARGETS):
        return []
    with open(HOST_SKILL_TARGETS, encoding="utf-8") as f:
        return list(json.load(f).get(host, []))


def host_mirror_names(host):
    """Claude-bundled skill folders that the owner's Codex or ~/.agents folder holds (skill-targets.json
    claude_mirror); None for any other target, which gets every Claude skill."""
    if host is None or not os.path.isfile(HOST_SKILL_TARGETS):
        return None
    with open(HOST_SKILL_TARGETS, encoding="utf-8") as f:
        return set(json.load(f).get("claude_mirror", {}).get(host, [])) | {"vault-os-update"}


class Installer:
    def __init__(self, home, backup, dry_run, platform, python, owner, language):
        self.home, self.backup, self.dry = home, backup, dry_run
        self.platform, self.python = platform, python
        self.values = {"{{OWNER_NAME}}": owner, "{{LANGUAGE}}": language,
                       "{{CLAUDE_HOME}}": slash(home), "{{PYTHON}}": '"%s"' % slash(python),
                       "{{STATUSLINE_LANG}}": statusline_lang(language)}
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        self.report = {"installed": [], "same": [], "skipped": [], "backed_up": [], "settings": [], "upstream": [],
                       "mcp": []}
        self.bypass = False

    def render(self, text):
        for k, v in self.values.items():
            text = text.replace(k, v)
        if "{{" in text and "}}" in text.split("{{", 1)[1]:
            raise SystemExit("unresolved placeholder in rendered text: " + text.split("{{", 1)[1][:40])
        return text

    def render_obj(self, obj):
        if isinstance(obj, dict):
            return {k: self.render_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.render_obj(v) for v in obj]
        return self.render(obj) if isinstance(obj, str) else obj

    def _aside(self, dest):
        moved = "%s.backup-%s" % (dest, self.stamp)
        if not self.dry:
            os.replace(dest, moved)
        self.report["backed_up"].append(slash(moved))

    def _make_executable(self, dest):
        if self.platform != "win32" and not self.dry:
            os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def put_file(self, src, dest, text=None):
        """Copy src (or write text) to dest without overwriting a different user file."""
        rel = slash(os.path.relpath(dest, self.home))
        if os.path.exists(dest):
            same = (open(dest, encoding="utf-8").read() == text) if text is not None else filecmp.cmp(src, dest, shallow=False)
            if same:
                self.report["same"].append(rel)
                return
            if not self.backup:
                self.report["skipped"].append(rel)
                return
            self._aside(dest)
        if not self.dry:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if text is not None:
                with open(dest, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
            else:
                shutil.copy2(src, dest)
            if dest.endswith((".py", ".sh")):
                self._make_executable(dest)
        self.report["installed"].append(rel)

    def put_tree(self, src, dest, label):
        if os.path.exists(dest):
            cmp = filecmp.dircmp(src, dest)
            if not _tree_differs(cmp):
                self.report["same"].append(label)
                return
            if not self.backup:
                self.report["skipped"].append(label)
                return
            self._aside(dest)
        if not self.dry:
            shutil.copytree(src, dest)
        self.report["installed"].append(label)

    def file_layers(self):
        for layer, exts in FILE_LAYERS.items():
            folder = os.path.join(SHELL, layer)
            for name in sorted(os.listdir(folder)):
                if not name.endswith(exts):
                    continue
                if self.platform == "win32" and name in POSIX_ONLY_SCRIPTS:
                    self.report["skipped"].append("%s/%s (POSIX only)" % (layer, name))
                    continue
                self.put_file(os.path.join(folder, name), os.path.join(self.home, layer, name))

    def skills(self, target, prefix):
        folder = os.path.join(SHELL, "skills")
        mirror = host_mirror_names(skills_host(target, prefix))
        for name in sorted(os.listdir(folder)):
            src = os.path.join(folder, name)
            if os.path.isdir(src):
                if mirror is not None and name not in mirror:  # Codex and ~/.agents hold the live sets only
                    continue
                self.put_tree(src, os.path.join(target, name), "%s/%s/" % (prefix, name))
            elif name.endswith((".md", ".txt")):
                self.put_file(src, os.path.join(target, name))
        for name in host_skill_names(skills_host(target, prefix)):  # Codex and ~/.agents only
            src = os.path.join(HOST_SKILLS, name)
            if not os.path.isdir(src) or os.path.isdir(os.path.join(folder, name)):
                raise SystemExit("shell/codex/skill-targets.json: %s is missing or also a Claude skill" % name)
            self.put_tree(src, os.path.join(target, name), "%s/%s/" % (prefix, name))

    def claude_md(self):
        with open(os.path.join(SHELL, "CLAUDE.template.md"), encoding="utf-8") as f:
            text = self.render(f.read())
        self.put_file(None, os.path.join(self.home, "CLAUDE.md"), text=text)

    def hook_entries(self):
        with open(os.path.join(SHELL, "hooks", "hooks.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        out = []
        for h in manifest["hooks"]:
            if self.platform not in h["platforms"]:
                continue
            cmd = {"type": "command", "command": self.render(h["command"]), "timeout": h["timeout"]}
            if h.get("async"):
                cmd["async"] = True
            entry = {"hooks": [cmd]}
            if h.get("matcher"):
                entry = {"matcher": h["matcher"], "hooks": [cmd]}
            out.append((h["event"], entry))
        return out

    def settings(self):
        with open(os.path.join(SHELL, "settings.template.json"), encoding="utf-8") as f:
            template = self.render_obj(json.load(f))
        template.pop("_comment", None)
        with open(os.path.join(SHELL, "optional-plugins.json"), encoding="utf-8") as f:
            optional = json.load(f)
        for p in optional.get("plugins", []):  # plugins held back until the owner decides: shipped disabled
            template["enabledPlugins"][p["plugin"]] = False
            template["extraKnownMarketplaces"][p["marketplace"]] = {"source": {"source": "github", "repo": p["github"]}}
        path = os.path.join(self.home, "settings.json")
        current = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                current = json.load(f)
        merged, notes = merge_settings(current, template, self.hook_entries(), self.bypass)
        self.report["settings"] = notes
        if merged == current and os.path.exists(path):
            return merged  # nothing to add: no write, no backup
        if os.path.exists(path):
            backup = "%s.backup-%s" % (path, self.stamp)
            if not self.dry:
                shutil.copy2(path, backup)
            self.report["backed_up"].append(slash(backup))
        if not self.dry:
            os.makedirs(self.home, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
                f.write("\n")
        return merged

    # ── upstream skills ─────────────────────────────────────────────────────
    def _git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=900)

    def _fetch(self, repo, commit, dest):
        """Check out repo at commit into dest: a shallow fetch by commit, else a full clone."""
        os.makedirs(dest)
        steps = [("init", "-q"), ("remote", "add", "origin", repo), ("fetch", "-q", "--depth", "1", "origin", commit),
                 ("checkout", "-q", "FETCH_HEAD")]
        if all(self._git(*step, cwd=dest).returncode == 0 for step in steps):
            return True
        shutil.rmtree(dest, ignore_errors=True)
        if self._git("clone", "-q", repo, dest).returncode != 0:
            return False
        return self._git("checkout", "-q", commit, cwd=dest).returncode == 0

    def upstream_skills(self, manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        skills_dir = os.path.join(self.home, "skills")
        for item in manifest["skills"]:
            name, label = item["name"], "skills/%s/ (upstream %s)" % (item["name"], item["commit"][:8])
            if self.platform not in item["platforms"]:
                self.report["skipped"].append("%s: not on %s" % (label, self.platform))
                continue
            missing = [tool for tool in item.get("requires", []) if shutil.which(tool) is None]
            if missing:
                self.report["skipped"].append("%s: needs %s" % (label, ", ".join(missing)))
                continue
            dest = os.path.join(skills_dir, name)
            if item["kind"] == "setup" and os.path.basename(self.home.rstrip("/\\")) != ".claude":
                self.report["skipped"].append("%s: its setup writes to $HOME/.claude, so the Claude home must be a .claude folder" % label)
                continue
            if os.path.exists(dest):
                head = self._git("rev-parse", "HEAD", cwd=dest) if item["kind"] == "setup" else None
                if head is not None and head.returncode == 0 and head.stdout.strip() == item["commit"]:
                    self.report["same"].append(label)
                    continue
                if item["kind"] == "folder" and os.path.isdir(dest):
                    self.report["skipped"].append(label + ": folder exists")
                    continue
                if not self.backup:
                    self.report["skipped"].append(label + ": folder exists")
                    continue
                self._aside(dest)
            if self.dry:
                self.report["upstream"].append("would install " + label)
                continue
            os.makedirs(skills_dir, exist_ok=True)
            if item["kind"] == "folder":
                tmp = tempfile.mkdtemp(prefix="vault-os-upstream-")
                try:
                    if not self._fetch(item["repo"], item["commit"], os.path.join(tmp, "src")):
                        self.report["skipped"].append(label + ": fetch failed")
                        continue
                    shutil.copytree(os.path.join(tmp, "src", *item["path"].split("/")), dest)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            else:
                if not self._fetch(item["repo"], item["commit"], dest):
                    self.report["skipped"].append(label + ": fetch failed")
                    continue
                env = dict(os.environ, HOME=os.path.dirname(self.home.rstrip("/\\")))
                out = subprocess.run(item["setup"], cwd=dest, env=env, capture_output=True, text=True, timeout=1800)
                if out.returncode != 0:
                    self.report["skipped"].append("%s: setup failed (%s)" % (label, (out.stderr or out.stdout).strip()[-200:]))
                    continue
                config = os.path.join(dest, "bin", "gstack-config")
                for key, value in item.get("config", {}).items():
                    if os.path.exists(config):
                        subprocess.run([config, "set", key, value], cwd=dest, env=env, capture_output=True, timeout=60)
            self.report["upstream"].append("installed " + label)

    def upstream_hosts(self, manifest_path, host_dirs):
        """The Codex and ~/.agents copies of the upstream skills ('hosts' in the manifest), for the skills folders
        given with --extra-skills-dir: a setup kind runs its host setup from the Claude checkout at the pin, a folder
        kind is fetched into the host folder under the host's name. Never replaces an existing entry."""
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        for item in manifest["skills"]:
            for host, cfg in sorted((item.get("hosts") or {}).items()):
                if host not in host_dirs or self.platform not in item["platforms"]:
                    continue
                name = cfg.get("name", item["name"])
                dest = os.path.join(host_dirs[host], name)
                label = "%s/%s/ (upstream %s for %s)" % (slash(host_dirs[host]), name, item["commit"][:8], host)
                missing = [tool for tool in item.get("requires", []) if shutil.which(tool) is None]
                if os.path.lexists(dest):
                    self.report["same" if item["kind"] == "setup" else "skipped"].append(
                        label if item["kind"] == "setup" else label + ": folder exists")
                    continue
                if missing:
                    self.report["skipped"].append("%s: needs %s" % (label, ", ".join(missing)))
                    continue
                checkout = os.path.join(self.home, "skills", item["name"])
                if item["kind"] == "setup":
                    head = self._git("rev-parse", "HEAD", cwd=checkout) if os.path.isdir(checkout) else None
                    if head is None or head.returncode != 0 or head.stdout.strip() != item["commit"]:
                        self.report["skipped"].append(label + ": needs the Claude install at the pinned commit")
                        continue
                if self.dry:
                    self.report["upstream"].append("would install " + label)
                    continue
                if item["kind"] == "folder":
                    tmp = tempfile.mkdtemp(prefix="vault-os-upstream-")
                    try:
                        if not self._fetch(item["repo"], item["commit"], os.path.join(tmp, "src")):
                            self.report["skipped"].append(label + ": fetch failed")
                            continue
                        os.makedirs(host_dirs[host], exist_ok=True)
                        shutil.copytree(os.path.join(tmp, "src", *item["path"].split("/")), dest)
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                else:
                    env = dict(os.environ, HOME=os.path.dirname(self.home.rstrip("/\\")),
                               CODEX_HOME=os.path.dirname(host_dirs[host].rstrip("/\\")))
                    out = subprocess.run(cfg["setup"], cwd=checkout, env=env, capture_output=True, text=True,
                                         timeout=1800)
                    if out.returncode != 0:
                        self.report["skipped"].append("%s: setup failed (%s)" % (label, (out.stderr or out.stdout).strip()[-200:]))
                        continue
                self.report["upstream"].append("installed " + label)

    # ── MCP servers ─────────────────────────────────────────────────────────
    def mcp(self, wanted, claude_bin, manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            servers = json.load(f)["servers"]
        if "all" in wanted:
            chosen = servers
        else:
            chosen = [s for s in servers if s["name"] in wanted or ("default" in wanted and s.get("default"))]
            unknown = set(wanted) - {s["name"] for s in servers} - {"default"}
            if unknown:
                raise SystemExit("unknown MCP server: %s" % ", ".join(sorted(unknown)))
        binary = shutil.which(claude_bin) or (claude_bin if os.path.exists(claude_bin) else None)
        for s in chosen:
            cmd = ["claude", "mcp", "add", "--scope", "user", "--transport", s["transport"], s["name"], s["url"]]
            if self.dry:
                self.report["mcp"].append("would run: " + " ".join(cmd))
                continue
            if not binary:
                self.report["skipped"].append("mcp %s: claude CLI not found" % s["name"])
                continue
            if subprocess.run([binary, "mcp", "get", s["name"]], capture_output=True, timeout=60).returncode == 0:
                self.report["same"].append("mcp " + s["name"])
                continue
            out = subprocess.run([binary] + cmd[1:], capture_output=True, text=True, timeout=120)
            if out.returncode == 0:
                self.report["mcp"].append("added " + s["name"])
            else:
                self.report["skipped"].append("mcp %s: %s" % (s["name"], (out.stderr or out.stdout).strip()[-200:]))


def _tree_differs(cmp):
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return True
    return any(_tree_differs(sub) for sub in cmp.subdirs.values())


def merge_settings(current, template, hook_entries, bypass=False):
    """Add what is missing; never change a value the user already has."""
    merged = json.loads(json.dumps(current))
    notes = []
    for key in MAP_KEYS:
        if key not in template:
            continue
        target = merged.setdefault(key, {})
        for name, value in template.get(key, {}).items():
            if name not in target:
                target[name] = value
                notes.append("%s: added %s" % (key, name))
    perms = merged.setdefault("permissions", {})
    if bypass:
        if "defaultMode" not in perms:
            perms["defaultMode"] = "bypassPermissions"
            notes.append("permissions: bypassPermissions (chosen in the quiz)")
        allow = perms.setdefault("allow", [])
        for rule in BYPASS_ALLOW:
            if rule not in allow:
                allow.append(rule)
                notes.append("permissions: allow %s" % rule)
    elif "defaultMode" not in perms:
        perms["defaultMode"] = template.get("permissions", {}).get("defaultMode", "default")
        notes.append("permissions: defaultMode %s" % perms["defaultMode"])
    for key, value in template.items():
        if key in MAP_KEYS or key == "permissions":
            continue
        if key not in merged:
            merged[key] = value
            notes.append("added %s" % key)
    hooks = merged.setdefault("hooks", {})
    existing = {h.get("command") for groups in hooks.values() for g in groups for h in g.get("hooks", [])}
    for event, entry in hook_entries:
        command = entry["hooks"][0]["command"]
        if command in existing:
            continue
        hooks.setdefault(event, []).append(entry)
        existing.add(command)
        notes.append("hook %s: %s" % (event, command.replace('"', "").split("/")[-1]))
    mode = (merged.get("permissions") or {}).get("defaultMode")
    if mode == "bypassPermissions" and not bypass:
        notes.append("left the user's own permissions.defaultMode untouched")
    return merged, notes


NOT_IN_UPGRADE = ("--mcp", "--extra-skills-dir", "--claude-bin")  # MCP servers are personal; Codex mirror: --codex


def delegate(argv):
    """--upgrade and --rollback hand over to installer/vault-os.py, the full upgrade engine."""
    args = [a for a in argv if a not in ("--backup", "--dry-run")]  # the upgrade always archives; no --confirm = dry run
    for flag in NOT_IN_UPGRADE:
        if any(a == flag or a.startswith(flag + "=") for a in args):
            print("%s is not part of --upgrade or --rollback: run it as a separate install step" % flag, file=sys.stderr)
            return 2
    if "--rollback" in args:
        i = args.index("--rollback")
        if i + 1 >= len(args):
            print("--rollback needs the archive folder", file=sys.stderr)
            return 2
        cmd = ["rollback", args[i + 1]] + args[:i] + args[i + 2:]
    else:
        args.remove("--upgrade")
        cmd = ["upgrade"] + args
    return subprocess.call([sys.executable, os.path.join(ROOT, "installer", "vault-os.py")] + cmd)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--upgrade" in argv or "--rollback" in argv:
        return delegate(argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--claude-home", default=os.path.join(os.path.expanduser("~"), ".claude"))
    ap.add_argument("--backup", action="store_true", help="move differing user files aside instead of skipping them")
    ap.add_argument("--platform", choices=PLATFORMS)
    ap.add_argument("--python", default=sys.executable, help="interpreter written into hook commands")
    ap.add_argument("--extra-skills-dir", action="append", default=[],
                    help="also copy the skills here (Codex: ~/.codex/skills and ~/.agents/skills, which also get "
                         "their own skills from shell/codex/skill-targets.json)")
    ap.add_argument("--upstream-skills", action="store_true",
                    help="also install the skills of shell/claude/upstream-skills.json from upstream at the pinned commit")
    ap.add_argument("--upstream-manifest", default=os.path.join(SHELL, "upstream-skills.json"), help=argparse.SUPPRESS)
    ap.add_argument("--bypass-permissions", action="store_true",
                    help="write the owner's permission mode (bypassPermissions and the tool allow list); quiz answer only")
    ap.add_argument("--mcp", action="append", default=[],
                    help="add user-scope MCP servers from shell/claude/mcp-servers.json: default, all, or a name")
    ap.add_argument("--claude-bin", default="claude", help="the claude CLI used for --mcp")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upgrade", action="store_true",
                    help="replace an existing system with this release (installer/vault-os.py upgrade; plan first)")
    ap.add_argument("--rollback", metavar="ARCHIVE",
                    help="undo an upgrade from its backup archive: a preview, then --confirm ID applies it")
    a = ap.parse_args(argv)
    home = os.path.abspath(os.path.expanduser(a.claude_home))
    inst = Installer(home, a.backup, a.dry_run, platform_name(a.platform), a.python, a.owner, a.language)
    inst.bypass = a.bypass_permissions
    inst.file_layers()
    inst.skills(os.path.join(home, "skills"), "skills")
    for extra in a.extra_skills_dir:
        target = os.path.abspath(os.path.expanduser(extra))
        inst.skills(target, slash(target))
    inst.claude_md()
    inst.settings()
    if a.upstream_skills:  # after the settings merge: an upstream setup may register its own hooks
        inst.upstream_skills(a.upstream_manifest)
        hosts = {skills_host(t): os.path.abspath(os.path.expanduser(t)) for t in a.extra_skills_dir}
        inst.upstream_hosts(a.upstream_manifest, {h: t for h, t in hosts.items() if h})
    if a.mcp:
        inst.mcp(a.mcp, a.claude_bin, os.path.join(SHELL, "mcp-servers.json"))
    print(json.dumps(inst.report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
