"""Detect, plan, apply, verify, roll back. The CLI is installer/vault-os.py; the design is installer/UPGRADE.md."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import boundary, fsx, journal, manifest, migrate

STAMP_REL = "vault-os/installed.json"
LOCK_NAME = ".vault-os.lock"
REPO_SLUG = "mrDesign-ww/vault-os"
WINDOWS_REFUSAL = "not yet on Windows: run the upgrade inside WSL (detect, the dry run and parity also work here)"
LAYER_ORDER = ("claude", "codex", "agents", "vault")
ANSWER_KEYS = ("owner", "language", "vault", "title", "client_label", "python", "codex", "upstream_skills",
               "bypass_permissions")
VAULT_TOOL_PATHS = (".vault-meta/memory-index", ".vault-meta/write", ".vault-meta/locks-v2",
                    ".vault-meta/evolution/lifecycle-degraded.log", ".vault-meta/.wiki-lock.meta")
VAULT_TOOL_DIRS = (".vault-meta/memory-index", ".vault-meta/write", ".vault-meta/locks-v2")  # the index tools write here
# Apply order per layer (UPGRADE.md 5.1). The vault: phase 0 renames beside and phase 1 moves (the migration), then
# phase 2 writes, then phase 3 removals. The homes have no renames or moves: a link or file in the way is archived and
# removed there, so their removals run first.
OP_ORDER = {"vault": {"write": 1, "tree": 1, "split": 2, "seed": 3, "remove": 4},
            "home": {"remove": 0, "write": 1, "tree": 1, "split": 2, "seed": 3}}
GIT_META = ("index", "HEAD", "refs", "logs", "packed-refs", "COMMIT_EDITMSG", "ORIG_HEAD")
ZONES = ("work", "personal", "client")


class Stop(Exception):
    """A refusal with an exit code: nothing was changed."""

    def __init__(self, message, code=2):
        Exception.__init__(self, message)
        self.code = code


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def child_env():
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


# ── homes, stamp, detection ─────────────────────────────────────────────────

def homes_from(opts):
    home = os.path.expanduser("~")
    return {
        "claude": os.path.abspath(os.path.expanduser(opts.get("claude_home") or os.path.join(home, ".claude"))),
        "codex": os.path.abspath(os.path.expanduser(opts.get("codex_home") or os.path.join(home, ".codex"))),
        "agents": os.path.abspath(os.path.expanduser(opts.get("agents_home") or os.path.join(home, ".agents"))),
        "backups": os.path.abspath(os.path.expanduser(opts.get("backup_root") or os.path.join(home, "vault-os-backups"))),
    }


def read_stamp(claude_home):
    path = os.path.join(claude_home, *STAMP_REL.split("/"))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {"state": "invalid"}
    except (OSError, ValueError):
        return {"state": "invalid"}


def incomplete_archives(backup_root):
    return [a for a in journal.list_archives(backup_root) if journal.status(a) == "incomplete"]


def repo_skill_names(repo):
    folder = os.path.join(repo, "shell", "claude", "skills")
    return {n for n in os.listdir(folder) if os.path.isdir(os.path.join(folder, n))}


def detect(repo, homes):
    stamp = read_stamp(homes["claude"])
    pending = incomplete_archives(homes["backups"])
    out = {"stamp": stamp, "incomplete_archives": pending, "version_available": manifest.read_version(repo)}
    if pending or (stamp and stamp.get("state") == "upgrading"):
        out["result"] = "interrupted"
        return out
    if stamp and stamp.get("state") == "installed":
        out["result"] = "installed"
        out["version"] = stamp.get("version")
        out["outdated"] = version_key(stamp.get("version")) < version_key(out["version_available"])
        return out
    claude = homes["claude"]
    signs = []
    for name in ("CLAUDE.md", "settings.json"):
        if os.path.isfile(os.path.join(claude, name)):
            signs.append(name)
    for folder in ("skills", "agents", "hooks", "commands", "output-styles"):
        path = os.path.join(claude, folder)
        if os.path.isdir(path) and [n for n in os.listdir(path) if not n.startswith(".")]:
            signs.append(folder + "/")
    out["signs"] = signs
    if not signs:
        out["result"] = "fresh"
        return out
    ours = repo_skill_names(repo)
    skills = os.path.join(claude, "skills")
    have = set(os.listdir(skills)) if os.path.isdir(skills) else set()
    head = ""
    try:
        with open(os.path.join(claude, "CLAUDE.md"), encoding="utf-8") as handle:
            head = handle.readline()
    except OSError:
        pass
    vault_os = len(ours & have) >= 5 or head.startswith("# Global rules for")
    out["result"] = "pre-stamp" if vault_os else "foreign"
    return out


def version_key(value):
    parts = []
    for piece in str(value or "0").lstrip("v").split("."):
        parts.append(int(piece) if piece.isdigit() else 0)
    return tuple(parts)


# ── answers ─────────────────────────────────────────────────────────────────

def infer_python(settings):
    for groups in ((settings or {}).get("hooks") or {}).values():
        for group in groups if isinstance(groups, list) else []:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                command = str(hook.get("command", ""))
                if "/hooks/orchestration-doc.py" in command and command.startswith('"'):
                    return command[1:].split('"', 1)[0]
    return None


def resolve_answers(repo, opts, homes, stamp):
    answers, sources = {}, {}
    previous = (stamp or {}).get("answers") or {}

    def put(key, value, source):
        if key not in answers and value is not None and value != "":
            answers[key] = value
            sources[key] = source

    for key in ANSWER_KEYS:
        put(key, opts.get(key), "flag")
    for key in ANSWER_KEYS:
        put(key, previous.get(key), "stamp")
    if answers.get("vault"):
        answers["vault"] = os.path.realpath(os.path.expanduser(answers["vault"]))
    vault = answers.get("vault")
    if vault and os.path.isdir(vault):
        mode = manifest.read_json(os.path.join(vault, ".vault-meta", "mode.json")) or {}
        put("owner", ((mode.get("memory_model") or {}).get("owner")), "vault mode.json")
        put("client_label", (((mode.get("zones") or {}).get("client") or {}).get("label")), "vault mode.json")
        try:
            with open(os.path.join(vault, "CLAUDE.md"), encoding="utf-8") as handle:
                first = handle.readline().strip()
            if first.startswith("# ") and first.endswith(": LLM Wiki (PARA)"):
                put("title", first[2:-len(": LLM Wiki (PARA)")], "vault CLAUDE.md")
        except OSError:
            pass
    settings = manifest.read_json(os.path.join(homes["claude"], "settings.json")) or {}
    put("language", settings.get("language"), "settings.json")
    put("python", infer_python(settings), "settings.json hooks")
    put("bypass_permissions", (settings.get("permissions") or {}).get("defaultMode") == "bypassPermissions",
        "settings.json")
    upstream = manifest.read_json(opts.get("upstream_manifest") or default_upstream(repo)) or {}
    records = (stamp or {}).get("upstream") if isinstance((stamp or {}).get("upstream"), dict) else {}
    pinned = False
    for item in upstream.get("skills", []):
        name = item.get("name")
        if not name:
            continue
        checkout = os.path.join(homes["claude"], "skills", name, ".git")
        # Only a pinned install counts: a git checkout (setup kind) or a stamp record (folder kind). A folder of the
        # same name, such as the remotion copy 28777ca bundled, says nothing about the choice.
        if name in records or (item.get("kind") == "setup" and fsx.lexists(checkout)):
            pinned = True
    put("upstream_skills", pinned, "pinned upstream install")
    codex_signs = False
    for name, marker in (("hooks.json", "Reloading vault context"), ("config.toml", "agricidaniel-claude-obsidian")):
        try:
            with open(os.path.join(homes["codex"], name), encoding="utf-8") as handle:
                codex_signs = codex_signs or marker in handle.read()
        except OSError:
            pass
    put("codex", codex_signs, "codex home")
    put("language", "English", "default")
    put("python", sys.executable, "default")
    if vault:
        put("title", os.path.basename(vault.rstrip("/")) or "Vault", "default")
    put("client_label", "Client", "default")
    questions = []
    if not answers.get("owner"):
        questions.append("owner name (--owner)")
    if not answers.get("vault"):
        questions.append("absolute vault path (--vault)")
    rv = manifest.load_installers(repo)[2]
    for key, label in (("owner", "owner"), ("title", "title"), ("language", "language"),
                       ("client_label", "client label")):
        if answers.get(key):
            try:
                answers[key] = rv.validate_field(answers[key], label)
            except ValueError as error:
                questions.append("%s: %s" % (label, error))
    if vault and ('"' in vault or "\\" in vault):
        questions.append("vault path contains unsupported characters")
    icl = manifest.load_installers(repo)[0]
    answers["platform"] = icl.platform_name(opts.get("platform"))
    for key in ("codex", "upstream_skills", "bypass_permissions"):
        answers[key] = bool(answers.get(key))
    return answers, sources, questions


def default_upstream(repo):
    return os.path.join(repo, "shell", "claude", "upstream-skills.json")


# ── plan ────────────────────────────────────────────────────────────────────

class Context:
    """Everything a confirmed plan needs to run: the staging folder, desired layers and computed bytes."""

    def __init__(self, stage):
        self.stage = stage
        self.layers = {}
        self.upstream = []
        self.data = {}  # (layer, rel) -> bytes of split and rewritten files
        self.migration = None
        self.migration_before = {}
        self.migration_links = {}
        self.migration_extra = []

    def cleanup(self):
        shutil.rmtree(self.stage, ignore_errors=True)


def link_parent(root, rel):
    parts = rel.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if os.path.islink(os.path.join(root, *prefix.split("/"))):
            return prefix
    return None


def split_bytes(layer, rel, split, ctx_info, absent=False):
    """(bytes to write or None when the live file already matches, notes). absent: the live entry leaves first."""
    path = os.path.join(layer.root, *rel.split("/"))
    exists = not absent and os.path.isfile(path) and not os.path.islink(path)
    if split["kind"] == "codex-toml":
        current = ""
        if exists:
            with open(path, encoding="utf-8") as handle:
                current = handle.read()
        if exists and boundary.codex_system_view(current, split["template"]) == \
                boundary.codex_system_view(split["template"], split["template"]):
            return None, []
        notes = boundary.codex_config_notes(current, split["template"])
        return boundary.desired_codex_config(current, split["template"]).encode("utf-8"), notes
    if split["kind"] == "gitignore":
        current = None
        if exists:
            with open(path, "rb") as handle:
                current = handle.read().decode("utf-8", "surrogateescape")
        desired = boundary.desired_gitignore(current, split["template"], ctx_info.get("gitignore_rules", ()))
        if desired == current:
            return None, []
        rules = len(ctx_info.get("gitignore_rules", ()))
        return desired.encode("utf-8", "surrogateescape"), (["%d rules for moved ignored files" % rules]
                                                             if rules else [])
    if split["kind"] in ("core-plugins", "community-plugins"):
        current = manifest.read_json(path) if exists else None
        if split["kind"] == "core-plugins":
            if isinstance(current, list):  # the older Obsidian format: a list of enabled ids
                current = {str(item): True for item in current}
            desired = boundary.desired_core_plugins(current, split["template"])
        else:
            desired = boundary.desired_community_plugins(current, split["template"], ctx_info.get("old_plugins", ()),
                                                         ctx_info.get("drop_old_plugins", False))
        if exists and desired == current:
            return None, []
        return boundary.dump_json(desired), []
    current = {}
    if exists:
        current = manifest.read_json(path)
        if not isinstance(current, dict):
            raise Stop("%s is not a JSON object; fix it or move it away, then plan again" % path, 3)
    if split["kind"] == "json-keys":
        desired, notes = boundary.desired_json_keys(current, split["template"]), []
    else:
        desired, notes = manifest.desired_claude_settings(
            current, split, ctx_info.get("keep_upstream", ()), ctx_info.get("old_env", ()),
            ctx_info.get("old_allow", ()))
        notes = ["%s %s" % pair for pair in notes]
    if exists and desired == current:
        return None, []
    return boundary.dump_json(desired), notes


def layer_actions(layer, ctx, info):
    root = layer.root
    actions = []
    removing = set()
    gone = layer.gone  # vault: links the migration renames away before any write (SPOT-CHECK MAJOR-1)

    def through_gone(rel):
        return any(rel == g or rel.startswith(g + "/") for g in gone)

    def remove(rel, why):
        if rel in removing or rel in layer.protected:
            return
        removing.add(rel)
        actions.append({"layer": layer.name, "op": "remove", "path": rel, "why": why,
                        "was": fsx.entry_state(os.path.join(root, *rel.split("/")))})

    if layer.name != "vault":  # a home: a link or file at a folder above a system path is archived and removed
        owned = tuple(d + "/" for d in layer.owned_dirs)
        above = set()
        for rel in list(layer.entries) + list(layer.splits) + list(layer.seeds) + \
                ([STAMP_REL] if layer.name == "claude" else []):
            parts = rel.split("/")
            above |= {"/".join(parts[:i]) for i in range(1, len(parts))}
        cleared = []
        for folder in sorted(above, key=lambda p: (p.count("/"), p)):
            if folder in layer.owned_dirs or folder.startswith(owned) or any(folder.startswith(c + "/")
                                                                             for c in cleared):
                continue  # an owned folder has its own rule below; below a cleared folder everything is new
            if fsx.shape(os.path.join(root, *folder.split("/"))) not in ("absent", "folder"):
                remove(folder, "a symbolic link or file where the system keeps a folder")
                cleared.append(folder)
    for rel in sorted(layer.absent):
        if through_gone(rel) or link_parent(root, rel):
            continue  # never removed through a link: it leaves with its link, or stays in the link's target
        if fsx.lexists(os.path.join(root, *rel.split("/"))):
            remove(rel, "not shipped by this release")
    for folder, names in sorted(layer.owned_dirs.items()):
        if through_gone(folder):
            continue
        path = os.path.join(root, *folder.split("/"))
        lp = link_parent(root, folder + "/x")
        if lp:
            remove(lp, "a symbolic link at a system folder")
            continue
        if os.path.islink(path) or (fsx.lexists(path) and not os.path.isdir(path)):
            remove(folder, "a symbolic link or file where the system keeps a folder")
            continue
        if not os.path.isdir(path):
            continue
        for child in sorted(os.listdir(path)):
            if child.startswith(".") or fsx.is_junk(child) or child in names:
                continue
            old = layer.leftovers.get(folder, {}).get(child)
            if old is not None and fsx.entry_state(os.path.join(path, child)).get("sha256") not in old:
                continue  # an earlier mirrored copy the person changed or added to: kept (UPGRADE.md 2.1)
            remove(folder + "/" + child, "not part of the system")
    for rel, entry in sorted(layer.entries.items()):
        if rel in layer.protected:
            continue
        if through_gone(rel):
            lp, live = None, {"type": "absent"}
        else:
            lp = link_parent(root, rel)
            if lp:
                remove(lp, "a symbolic link at a system folder")
            live = fsx.entry_state(os.path.join(root, *rel.split("/")))
        if lp is None and fsx.same_state(live, entry["state"]):
            continue
        state = entry["state"]
        if state["type"] == "tree":
            actions.append({"layer": layer.name, "op": "tree", "path": rel, "sha256": state["sha256"],
                            "files": state["files"], "was": live["type"]})
        else:
            actions.append({"layer": layer.name, "op": "write", "path": rel, "sha256": state["sha256"],
                            "exec": state.get("exec", False), "was": live["type"]})
    for rel, split in sorted(layer.splits.items()):
        data, notes = split_bytes(layer, rel, split, info, through_gone(rel))
        if data is None:
            continue
        ctx.data[(layer.name, rel)] = data
        actions.append({"layer": layer.name, "op": "split", "path": rel, "sha256": fsx.sha256_bytes(data),
                        "notes": notes})
    for rel, src in sorted(layer.seeds.items()):
        if through_gone(rel) or not fsx.lexists(os.path.join(root, *rel.split("/"))):
            actions.append({"layer": layer.name, "op": "seed", "path": rel, "sha256": fsx.sha256_file(src)})
    return actions


def vault_render_answers(answers, date):
    out = dict(answers)
    out["date"] = date
    return out


def approval_date(repo, answers, stage, today):
    """(date, approval needed): keep the live approval date while the bundled doctrine reads the same."""
    vault = answers["vault"]
    trust = manifest.live_trust(vault) if os.path.isdir(vault) else {"approved_on": None}
    candidate = trust.get("approved_on")
    try:
        if candidate and dt.date.fromisoformat(candidate) <= dt.date.today():
            staged = manifest.render_vault(repo, vault_render_answers(answers, candidate),
                                           os.path.join(stage, "vault-probe"))
            same = True
            for rel in manifest.DOCTRINE:
                live = os.path.join(vault, *rel.split("/"))
                if not os.path.isfile(live):
                    same = False
                    break
                with open(live, encoding="utf-8") as a, open(os.path.join(staged, *rel.split("/")),
                                                             encoding="utf-8") as b:
                    if manifest.normalized_doctrine(a.read()) != manifest.normalized_doctrine(b.read()):
                        same = False
                        break
            if same:
                return candidate, False, trust
    except ValueError:
        pass
    return today, True, trust


# ── vault: origin, personal files at note paths, ignore rules, index prediction (UPGRADE.md 2.1, 7) ──────

def vault_origin(vault, stamp):
    """"vault-os" when the vault had Vault OS before (a stamp for this vault or a Vault OS mode.json), else
    "foreign". The stamp keeps the first answer, so later plans of the same install agree."""
    stamp = stamp if isinstance(stamp, dict) and stamp.get("state") != "invalid" else None
    same = stamp and os.path.realpath(os.path.expanduser(str((stamp.get("answers") or {}).get("vault") or ""))) == vault
    if same and stamp.get("vault_origin") in ("vault-os", "foreign"):
        return stamp["vault_origin"]
    if same:
        return "vault-os"
    mode = manifest.read_json(os.path.join(vault, ".vault-meta", "mode.json"))
    if isinstance(mode, dict) and isinstance(mode.get("memory_model"), dict) \
            and mode["memory_model"].get("model") == "l0-l3":
        return "vault-os"
    return "foreign"


def personal_at_note_paths(vault, layer, old_vault, owner):
    """B1: system note paths whose live file is the person's own. A live file is the system's only when it equals
    the desired bytes, the stamped bytes or a shipped version of that template, or, for a doctrine page, when the
    live approvals list it under l3."""
    approvals = manifest.read_json(os.path.join(vault, *manifest.APPROVALS.split("/")))
    l3 = approvals.get("l3") if isinstance(approvals, dict) and isinstance(approvals.get("l3"), dict) else {}
    stamped = (old_vault or {}).get("entries", {})
    out = []
    for rel, entry in sorted(layer.entries.items()):
        if manifest.vault_path_class(rel) != "note" or rel in layer.protected or link_parent(vault, rel):
            continue
        path = os.path.join(vault, *rel.split("/"))
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        sha = fsx.sha256_file(path)
        if sha == entry["state"].get("sha256") or sha == (stamped.get(rel) or {}).get("sha256"):
            continue
        if manifest.is_shipped_note(rel, path, owner) or (rel in manifest.DOCTRINE and rel in l3):
            continue
        out.append(rel)
    return out


def in_zone(rel):
    """A vault path the memory index reads as part of a zone: below wiki/, no dot folder or file on the way."""
    return zone_of(rel) is not None and not any(p.startswith(".") for p in rel.split("/"))


def needed_shapes(layer, dests=()):
    """Path-shape preflight, the expected half (UPGRADE.md 5.1 step 0): {vault path: "file" or "folder"} for every path
    the plan writes, splits or seeds (a file there, or nothing), every folder above one of them or above a move
    destination, the owned folders and the folders the vault tools write into (a real folder, or nothing). Paths inside
    an owned folder are left out: every child there is the system's, archived and replaced (2.1)."""
    need = {}

    def above(rel):
        parts = rel.split("/")
        for i in range(1, len(parts)):
            need["/".join(parts[:i])] = "folder"

    for rel, entry in layer.entries.items():
        if rel not in layer.protected:
            above(rel)
            need.setdefault(rel, "folder" if entry["state"]["type"] == "tree" else "file")
    for rel in list(layer.splits) + list(layer.seeds):
        if rel not in layer.protected:
            above(rel)
            need.setdefault(rel, "file")
    for rel in list(layer.owned_dirs) + list(VAULT_TOOL_DIRS):
        above(rel)
        need[rel] = "folder"
    for rel in dests:
        above(rel)
    owned = tuple(d + "/" for d in layer.owned_dirs)
    return {rel: kind for rel, kind in need.items() if not rel.startswith(owned)}


def shape_question(rel, shape, want):
    zone = zone_of(rel + "/")  # a zone's own folder (wiki/personal) counts as that zone
    if shape in fsx.LINK_SHAPES:
        return ("symbolic link %s (%s) is at a system path inside the %s zone; the memory index refuses links there: "
                "replace it with the real file or folder, or move it out of wiki/, then run the dry run again"
                % (rel, shape, zone))
    if want == "folder":
        return ("%s is a file where the system needs a folder (inside the %s zone): rename or move it, then run the "
                "dry run again" % (rel, zone))
    return ("%s is a folder where the system keeps a file (inside the %s zone): rename or move it, then run the dry "
            "run again" % (rel, zone))


def shape_preflight(vault, need):
    """Path-shape preflight, the found half: lstat each needed path top-down. A shape the plan does not expect there
    (a link of any kind, a file where a folder is needed, a folder where a file is) is renamed beside its path in phase
    0 (beside: {path: migrate reason}), or, inside a zone, it is a plan question with a way out (asked: {path:
    question}). Nothing below a wrong path is looked at: below a renamed one everything plans as absent."""
    beside, asked = {}, {}
    for rel in sorted(need, key=lambda p: (p.count("/"), p)):
        if any(rel.startswith(w + "/") for w in list(beside) + list(asked)):
            continue
        shape = fsx.shape(os.path.join(vault, *rel.split("/")))
        if shape in ("absent", need[rel]):
            continue
        if in_zone(rel):
            asked[rel] = shape_question(rel, shape, need[rel])
        elif shape in fsx.LINK_SHAPES:
            beside[rel] = migrate.LINK_REASON
        else:
            beside[rel] = migrate.FILE_REASON if need[rel] == "folder" else migrate.FOLDER_REASON
    return beside, asked


def held_paths(vault, stamp):
    """Paths an earlier upgrade of this vault renamed beside a system path (the stamp keeps them): kept in place."""
    stamp = stamp if isinstance(stamp, dict) and stamp.get("state") != "invalid" else None
    if not stamp or os.path.realpath(os.path.expanduser(str((stamp.get("answers") or {}).get("vault") or ""))) \
            != vault:
        return []
    return sorted(p for p in stamp.get("beside") or [] if isinstance(p, str) and p and not p.startswith("/")
                  and ".." not in p.split("/") and fsx.lexists(os.path.join(vault, *p.split("/"))))


class ShapeView:
    """The shapes the plan's phases leave, path by path, read-only: lstat on disk, overlaid by each simulated step."""

    def __init__(self, root, fold):
        self.root, self.fold, self.over = root, fold, {}

    def key(self, rel):
        return rel.lower() if self.fold else rel

    def at(self, rel):
        """(shape at rel, the first folder above rel that is not a real folder or None)."""
        parts = rel.split("/")
        fresh = False
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            k = self.key(prefix)
            if k in self.over:
                shape = self.over[k]
            elif fresh:
                shape = "absent"
            else:
                shape = fsx.shape(os.path.join(self.root, *parts[:i]))
            if i == len(parts):
                return ("folder" if shape == "new folder" else shape), None
            if shape == "new folder":
                fresh = True
            elif shape == "absent":
                return "absent", None
            elif shape != "folder":
                return "absent", (prefix, shape)
        return "absent", None

    def parents(self, rel):
        """Check every folder above rel is a real folder or absent (then made here); None or the problem."""
        shape, blocked = self.at(rel)
        if blocked:
            return "goes through %s (%s)" % blocked
        parts = rel.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if self.at(prefix)[0] == "absent":
                self.over[self.key(prefix)] = "new folder"
        return None

    def set(self, rel, shape):
        k = self.key(rel)
        for other in [o for o in self.over if o.startswith(k + "/")]:
            del self.over[other]
        self.over[k] = shape


def plan_shape_errors(plan, ctx, roots, fold):
    """The path-shape invariant of a runnable plan: replay its phases in apply order over the lstat shapes. No move or
    write may go through a link or a file (a path phase 0 renamed included), no move may land on an existing path, no
    write may replace a folder or link outside an owned folder, and every removal sees real folders above it.
    Returns the problems; build() stops on them with exit 8."""
    problems = []
    layers = getattr(ctx, "layers", {})
    for name in LAYER_ORDER:
        if name not in roots or name not in layers:
            continue
        view = ShapeView(roots[name], fold if name == "vault" else False)
        layer = layers[name]
        owned = tuple(d + "/" for d in layer.owned_dirs)
        acts = ordered(name, [a for a in plan["actions"] if a["layer"] == name and a["op"] != "upstream"])
        if name == "claude":  # the stamp folder is cleared before the interim stamp, written next
            first = [a for a in acts if a["op"] == "remove" and STAMP_REL.startswith(a["path"] + "/")]
            for a in first:
                view.set(a["path"], "absent")
            problem = view.parents(STAMP_REL)
            if problem:
                problems.append("claude: the stamp %s %s" % (STAMP_REL, problem))
            view.set(STAMP_REL, "file")
            acts = [a for a in acts if a not in first]
        if name == "vault":
            for src, dst, _ in plan["migration"]["moves"]:
                shape, blocked = view.at(src)
                if blocked or shape == "absent":
                    problems.append("vault: move %s -> %s: the source %s" % (
                        src, dst, "goes through %s (%s)" % blocked if blocked else "is not there"))
                    continue
                problem = view.parents(dst)
                if problem is None and view.at(dst)[0] != "absent":
                    problem = "lands on an existing %s" % view.at(dst)[0]
                if problem:
                    problems.append("vault: move %s -> %s %s" % (src, dst, problem))
                    continue
                view.set(src, "absent")
                view.set(dst, "unit" if shape == "folder" else shape)
        for a in acts:
            rel = a["path"]
            if a["op"] == "remove":
                shape, blocked = view.at(rel)
                if blocked:
                    problems.append("%s: remove %s goes through %s (%s)" % ((name, rel) + blocked))
                view.set(rel, "absent")
                continue
            problem = view.parents(rel)
            if problem:
                problems.append("%s: %s %s %s" % (name, a["op"], rel, problem))
                continue
            shape = view.at(rel)[0]
            if name == "vault" and not rel.startswith(owned) and (
                    (a["op"] == "seed" and shape != "absent") or
                    (a["op"] in ("write", "split") and shape not in ("absent", "file"))):
                problems.append("vault: %s %s replaces a %s" % (a["op"], rel, shape))
            view.set(rel, "unit" if a["op"] == "tree" else "file")
    return problems


def ordered(layer, actions):
    """Actions of one layer in apply order (OP_ORDER); the plan's order within one operation."""
    order = OP_ORDER["vault" if layer == "vault" else "home"]
    return sorted(actions, key=lambda a: order[a["op"]])


def plan_conflicts(plan):
    """Kept paths the plan also writes, removes or moves (or a path below them). Never valid: the plan stops."""
    m = plan["migration"]
    changed = {a["path"].lower() for a in plan["actions"] if a["layer"] == "vault"}
    for src, dst, _ in m["moves"]:
        changed |= {src.lower(), dst.lower()}
    return sorted(rel for rel, _ in m.get("kept", [])
                  if any(c == rel.lower() or c.startswith(rel.lower() + "/") for c in changed))


def git_ignored(vault, paths):
    """The paths (files or folders) the vault's own ignore rules match. Read-only: git check-ignore against a
    temporary empty repository, so it works without a vault repository and ignores no tracked state."""
    if not paths or not shutil.which("git"):
        return set()
    tmp = tempfile.mkdtemp(prefix="vault-os-ignore-")
    try:
        if run(["git", "init", "-q", "--bare", tmp], tmp, timeout=60).returncode:
            return set()
        exclude = os.path.join(vault, ".git", "info", "exclude")
        if os.path.isfile(exclude):
            os.makedirs(os.path.join(tmp, "info"), exist_ok=True)
            shutil.copyfile(exclude, os.path.join(tmp, "info", "exclude"))
        out = subprocess.run(["git", "-c", "core.excludesFile=" + os.devnull, "--git-dir=" + tmp,
                              "--work-tree=" + vault, "check-ignore", "--no-index", "--stdin", "-z"],
                             input="\0".join(paths) + "\0", cwd=vault, env=child_env(), capture_output=True,
                             text=True, timeout=300)
        if out.returncode not in (0, 1):
            raise Stop("could not read the vault's ignore rules: %s" % out.stderr.strip()[-200:], 3)
        return {p for p in out.stdout.split("\0") if p}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ignore_rules(vault, migration, detail):
    """M2: an anchored rule for the new path of every moved file the old rules ignored, one folder rule when a whole
    ignored folder moves as one into a folder that holds nothing else. Tracked files are not ignored by Git, so
    they get no rule."""
    moves = {src: dst for src, dst, _ in migration["moves"]}
    tracked = set()
    if moves and os.path.isdir(os.path.join(vault, ".git")):
        out = git(vault, "ls-files", "-z")
        if out.returncode == 0:
            tracked = set(out.stdout.split("\0"))
    srcs = sorted(s for s in moves if s not in tracked)
    folders = sorted({"/".join(s.split("/")[:i]) for s in srcs for i in range(1, s.count("/") + 1)})
    ignored = git_ignored(vault, srcs + folders)
    if not ignored:
        return []
    files = detail.get("files", [])
    landed = [p for p in list(detail.get("final", {}).values()) + list(detail.get("new_extra", []))]
    rules, covered, whole = [], set(), []
    for folder in sorted(folders, key=lambda f: (f.count("/"), f)):
        if folder not in ignored or any(folder.startswith(w + "/") for w in whole):
            continue
        inside = [f for f in files if f.startswith(folder + "/")]
        if not inside or any(f not in moves or f in tracked for f in inside):
            continue
        heads = {moves[f][:len(moves[f]) - len(f) + len(folder)] for f in inside
                 if moves[f].endswith(f[len(folder):])}
        if len(heads) != 1 or any(not moves[f].endswith(f[len(folder):]) for f in inside):
            continue
        head = heads.pop()
        moved = {moves[f] for f in inside}
        if any(p.startswith(head + "/") and p not in moved for p in landed):
            continue
        rule = boundary.gitignore_rule(head, folder=True)
        if rule:
            rules.append(rule)
            covered.update(inside)
            whole.append(folder)
    for src in srcs:
        if src in covered:
            continue
        parents = ["/".join(src.split("/")[:i]) for i in range(1, src.count("/") + 1)]
        if src in ignored or any(p in ignored for p in parents):
            rule = boundary.gitignore_rule(moves[src])
            if rule:
                rules.append(rule)
    return rules


INDEX_HINTS = {
    "cache-must-use-zone-hot-path": "a hot.md (or memory_role: cache) outside the zone's own hot.md path",
    "exactly-one-canonical-hot-required": "the zone needs exactly one hot.md at its own path",
    "evidence-cannot-self-promote": "a note in an evidence folder (inbox, sources, raw) claims a memory_level above l0",
    "invalid-memory-level": "memory_level is not one of l0, l1, l2, l3",
    "hot-cache-cannot-have-memory-level": "a hot cache page carries memory_level",
    "memory-candidate-must-use-zone-inbox": "a memory candidate outside the zone's inbox/memory-candidates/",
    "invalid-memory-candidate-identity": "a memory candidate with broken identity fields",
    "invalid-provisional-memory-contract": "memory_approval_mode or promotion_eligible without the provisional "
                                           "L2 contract",
}


def zone_of(rel):
    if rel.startswith("wiki/personal/"):
        return "personal"
    if rel.startswith("wiki/client/"):
        return "client"
    return "work" if rel.startswith("wiki/") else None


def indexed(rel):
    """The memory index reads this path: a .md note below a zone root, no dot or excluded folder on the way."""
    parts = rel.split("/")
    return zone_of(rel) is not None and rel.endswith(".md") and not any(
        p.startswith(".") or p in ("node_modules", ".git", ".next", "dist", "build", "coverage") for p in parts)


def predict_index(vault, staged_vault, layer, migration, detail, asked=()):
    """M1: run the STAGED memory-model.py over the layout the upgrade leaves (final paths, the bytes they will hold)
    before anything changes. Every issue that would stop the index build is returned as a plan question. asked are
    the paths the path-shape preflight already asks about."""
    try:
        mm = fsx.load_module(os.path.join(staged_vault, *manifest.MEMORY_MODEL.split("/")),
                             "vaultos_stage_memory_model")
    except ImportError:  # no fcntl (Windows): the upgrade itself is refused there
        return []
    rewrites = migration.get("rewrites", {})
    origin = {}
    sources = {}
    for rel in detail.get("files", []):
        final = detail["final"][rel]
        origin[final] = rel
        sources[final] = (os.path.join(vault, *rel.split("/")), rewrites.get(rel), True)
    for rel, entry in layer.entries.items():
        if entry["state"].get("type") == "file":
            sources[rel] = (entry["src"], None, False)
    for rel, src in layer.seeds.items():
        live = os.path.join(vault, *rel.split("/"))
        sources[rel] = (live, None, True) if fsx.lexists(live) else (src, None, False)
    for rel in detail.get("kept_paths", []):
        sources.setdefault(rel, (os.path.join(vault, *rel.split("/")), None, True))
    questions = []

    def name(rel):
        return rel if origin.get(rel, rel) == rel else "%s (now %s)" % (origin[rel], rel)

    named = set(asked)
    for rel, item in sorted((detail.get("kept") or {}).items()):
        if rel in named:
            continue
        inside = zone_of(rel) and not any(p.startswith(".") for p in rel.split("/"))
        if item["reason"] == "symbolic link" and inside:
            named.add(rel)
            questions.append("symbolic link %s is inside the %s zone, which the memory index refuses: move it out of "
                             "wiki/ or replace it with the real file" % (rel, zone_of(rel)))
    for src, dst, why in migration.get("moves", []):
        if why == migrate.LINK_REASON and zone_of(dst) and not any(p.startswith(".") for p in dst.split("/")):
            named.add(dst)
            questions.append("symbolic link %s is at a system path inside the %s zone; the memory index refuses links "
                             "there: replace it with the real file or folder, or move it out of wiki/"
                             % (src, zone_of(dst)))
    records = {zone: [] for zone in ZONES}
    for rel, (path, edits, live) in sorted(sources.items()):
        if not indexed(rel):
            continue
        if rel in named or any(rel.startswith(a + "/") for a in asked):
            continue
        if live:
            info = os.lstat(path)
            if os.path.isdir(path) and not os.path.islink(path):
                continue  # a folder, not a note (renamed beside its path, or asked about)
            if os.path.islink(path):
                if rel not in named:
                    questions.append("symbolic link %s is inside the %s zone, which the memory index refuses: move it "
                                     "out of wiki/ or replace it with the real file" % (name(rel), zone_of(rel)))
                continue
            if info.st_nlink != 1:
                questions.append("note %s has %d hard links; the memory index needs one file per note: copy it to "
                                 "a separate file" % (name(rel), info.st_nlink))
                continue
        with open(path, "rb") as handle:
            raw = handle.read()
        if edits:
            raw = migrate.encode(migrate.apply_edits(raw.decode("utf-8", "surrogateescape"), edits))
        fm, body = mm.parse_text(raw.decode("utf-8", errors="replace"))
        records[zone_of(rel)].append((mm.VAULT / rel, fm, body, fsx.sha256_bytes(raw), [0, 0, 0, len(raw), 0]))
    check = mm.validate_candidate_identity
    mm.validate_candidate_identity = lambda zone, fm, body, verify_sources=True, verify_project=True: check(
        zone, fm, body, verify_sources=False, verify_project=False)
    for zone in ZONES:
        try:
            result = mm.scan_zone(zone, sorted(records[zone], key=lambda r: str(r[0])))
        except (ValueError, OSError) as error:
            questions.append("the memory index check of zone %s could not run: %s" % (zone, error))
            continue
        for item in result["invalid_levels"]:
            for issue in item["issues"] or ["invalid-memory-level"]:
                questions.append("note %s: %s (%s); the %s index would not build" % (
                    name(item["path"]), issue, INDEX_HINTS.get(issue, "see the L0-L3 memory model page"), zone))
        for item in result["l3_without_approval"]:
            questions.append("note %s: memory_level l3 without a matching owner approval (%s); the %s index would "
                             "not build" % (name(item["path"]), item["reason"], zone))
        for path in result["l1_without_provenance"]:
            questions.append("note %s: memory_level l1 without memory_provenance; the %s index would not build"
                             % (name(path), zone))
        for item in result["duplicate_linkable_filenames"]:
            questions.append("note name %s is used %d times in the %s zone (%s); rename all but one" % (
                item["filename"], len(item["pages"]), zone, ", ".join(name(p["path"]) for p in item["pages"])))
    return questions


def build(repo, answers, homes, stamp, opts, today=None):
    """The full plan for this machine and a context to apply it. Read-only on every live path."""
    today = today or dt.date.today().isoformat()
    stage = tempfile.mkdtemp(prefix="vault-os-stage-")
    ctx = Context(stage)
    try:
        return _build(repo, answers, homes, stamp, opts, today, ctx)
    except BaseException:
        ctx.cleanup()
        raise


def _build(repo, answers, homes, stamp, opts, today, ctx):
    stage = ctx.stage
    homes = dict(homes, vault=answers["vault"])
    ctx.layers = manifest.stage_claude(repo, answers, homes, stage)
    upstream_manifest = opts.get("upstream_manifest") or default_upstream(repo)
    ctx.upstream = manifest.plan_upstream(upstream_manifest, answers, homes, stamp)
    for name in ("claude",) + manifest.UPSTREAM_HOSTS:  # the items in Claude, their host copies in codex, agents
        if name in ctx.layers:
            manifest.apply_upstream_to_layer(ctx.layers[name], ctx.upstream)
    date, approval, trust = approval_date(repo, answers, stage, today)
    answers = dict(answers, date=date)
    staged_vault = manifest.render_vault(repo, answers, os.path.join(stage, "vault"))
    vault_layer = manifest.vault_layer(staged_vault, answers["vault"])
    old = (stamp or {}).get("manifest") or {}
    # Migration first: own L3 approvals follow their notes, so the bound files depend on the moves.
    maps = [tuple(m) for m in opts.get("maps", [])]
    migration = {"files": 0, "moves": [], "renamed": [], "rewrites": {}, "blocked": {}, "kept": [], "warnings": []}
    vault = answers["vault"]
    old_vault = old.get("vault") or {}
    origin = vault_origin(vault, stamp)
    kept_leftovers, detail, beside, asked, held, fold = set(), {}, {}, {}, [], False
    if os.path.isdir(vault):
        kept_leftovers = manifest.changed_leftovers(vault_layer, old_vault)
        displaced = personal_at_note_paths(vault, vault_layer, old_vault, answers["owner"])
        system = (set(vault_layer.entries) | set(vault_layer.splits) | set(vault_layer.absent) |
                  set(vault_layer.owned_dirs) | set(old_vault.get("entries", {}))) - set(displaced) - kept_leftovers
        reserved = set(vault_layer.entries) | set(vault_layer.seeds) | set(vault_layer.splits)
        inbox = migrate.PERSONAL_INBOX if origin == "foreign" else migrate.INBOX
        fold = migrate.case_insensitive(vault)
        held = held_paths(vault, stamp)
        # Step 0, the path-shape preflight (UPGRADE.md 5.1): the expected shapes come from the plan, so the move
        # destinations add the folders above them until nothing new is needed (the set only grows).
        need = needed_shapes(vault_layer)
        for _ in range(16):
            beside, asked = shape_preflight(vault, need)
            on_disk = {on_disk_case(vault, rel) if fold else rel: why for rel, why in beside.items()}
            detail = {}
            migration = migrate.plan(vault, system, reserved, maps, displaced, inbox, fold, detail, on_disk, held)
            more = needed_shapes(vault_layer, [dst for _, dst, why in migration["moves"]
                                               if why not in migrate.BESIDE_REASONS])
            if set(more) <= set(need):
                break
            need = dict(more, **need)
        else:
            raise Stop("engine error: the path-shape preflight did not settle; nothing was written. Please report "
                       "this as a bug", 8)
        ctx.migration_before = migrate.fingerprint(vault, detail["files"])
        ctx.migration_links = migrate.link_targets(vault, detail["files"], detail["old_extra"])
        ctx.migration_extra = detail["new_extra"]
    ctx.migration = migration
    moves = {src: dst for src, dst, _ in migration["moves"]}
    own, approval_reasons = {}, []
    if approval:
        approval_reasons.append("the two bundled L3 doctrine pages are new or changed")
    for path, entry in (trust.get("own_approvals") or {}).items():
        if path in migration["rewrites"] and path in detail.get("texts", {}):
            text = migrate.apply_edits(detail["texts"][path], migration["rewrites"][path])
            entry = dict(entry, sha256=fsx.sha256_bytes(migrate.encode(text)))
            approval_reasons.append("your approved L3 page %s gets link rewrites; confirming approves its new "
                                    "text again" % moves.get(path, path))
        own[moves.get(path, path)] = entry
    cases = trust.get("own_cases") or []
    if own or cases:
        staged_vault = manifest.render_vault(repo, answers, os.path.join(stage, "vault"), own, cases)
        vault_layer = manifest.vault_layer(staged_vault, answers["vault"])
    vault_layer.gone = set(beside)  # renamed away in phase 0: the system paths there and below plan as absent
    ctx.beside = sorted(set(held) | {dst for _, dst, why in migration["moves"] if why in migrate.BESIDE_REASONS})
    ctx.layers["vault"] = vault_layer
    for name, layer in ctx.layers.items():
        manifest.leftovers(layer, old.get(name), kept_leftovers if name == "vault" else ())
    tpl = ctx.layers["claude"].splits["settings.json"]["template"]
    old_settings = (stamp or {}).get("settings") or {}
    info = {
        "keep_upstream": [u["item"]["name"] for u in ctx.upstream if u["status"] in ("adopt", "unavailable")],
        "old_env": [n for n in old_settings.get("system_env", []) if n not in (tpl.get("env") or {})],
        "old_allow": old_settings.get("system_allow", []),
    }
    infos = {"claude": info, "vault": {
        "gitignore_rules": ignore_rules(vault, migration, detail) if os.path.isdir(vault) else [],
        "old_plugins": manifest.shipped_versions().get("community_plugins_old", []),
        "drop_old_plugins": origin == "vault-os"}}
    actions = []
    for name in LAYER_ORDER:
        if name in ctx.layers:
            actions.extend(layer_actions(ctx.layers[name], ctx, infos.get(name, {})))
    for u in ctx.upstream:
        if u["status"] == "install":
            actions.append({"layer": "claude", "op": "upstream", "path": "skills/" + u["item"]["name"],
                            "commit": u["item"]["commit"], "kind": u["item"]["kind"], "reason": u["reason"]})
        for host, h in sorted((u.get("hosts") or {}).items()):
            if h["status"] == "install" and host in ctx.layers:
                actions.append({"layer": host, "op": "upstream", "path": "skills/" + h["name"],
                                "commit": u["item"]["commit"], "kind": u["item"]["kind"], "reason": h["reason"]})
    warnings = list(migration.get("warnings", []))
    for u in ctx.upstream:
        if u["status"] == "unavailable":
            warnings.append("upstream skill %s skipped: %s" % (u["item"]["name"], u["reason"]))
        for host, h in sorted((u.get("hosts") or {}).items()):
            if h["status"] == "unavailable" and u["status"] != "unavailable":
                warnings.append("upstream skill %s for %s skipped: %s" % (u["item"]["name"], host, h["reason"]))
    questions = [asked[rel] for rel in sorted(asked)]
    for folder, count in sorted(migration.get("blocked", {}).items()):
        questions.append("folder %s (%d files) looks like a separate zone of your old system: say where it goes "
                         "with --map \"%s=wiki/<zone or folder>\"" % (folder, count, folder))
    if os.path.isdir(vault):
        questions += predict_index(vault, staged_vault, vault_layer, migration, detail, asked)
    vault_changes = [a for a in actions if a["layer"] == "vault"] + migration["moves"] + \
        list(migration["rewrites"])
    stamp_needed = not stamp or stamp.get("state") != "installed" or bool(actions) or bool(migration["moves"]) \
        or bool(migration["rewrites"]) or stamp.get("version") != manifest.read_version(repo) \
        or (stamp.get("source") or {}).get("tree_sha256") != manifest.source_identity(repo)["tree_sha256"]
    plan = {
        "schema_version": 1,
        "version": manifest.read_version(repo),
        "source": manifest.source_identity(repo),
        "answers": answers,
        "approval_needed": bool(approval_reasons),
        "approval_reasons": approval_reasons,
        "vault_origin": origin,
        "actions": actions,
        "migration": migration,
        "vault_git": bool(vault_changes) or not os.path.isdir(os.path.join(vault, ".git")),
        "stamp": stamp_needed,
        "warnings": warnings,
        "questions": questions,
    }
    # A path the preflight asks about stays as it is until the person answers; the plan cannot run before that.
    conflicts = [c for c in plan_conflicts(plan) if not any(c == a or c.startswith(a + "/") for a in asked)]
    if conflicts:
        raise Stop("engine error: the plan keeps %s in place and also changes it; nothing was written. Please report "
                   "this as a bug" % ", ".join(conflicts[:5]), 8)
    if not asked:  # a plan the person can confirm never fails on a path's shape (UPGRADE.md 5.1)
        roots = {"claude": homes["claude"], "codex": homes["codex"], "agents": homes["agents"], "vault": vault}
        shapes = plan_shape_errors(plan, ctx, roots, fold)
        if shapes:
            raise Stop("engine error: the plan would fail on a path's shape (%s); nothing was written. Please report "
                       "this as a bug" % "; ".join(shapes[:5]), 8)
    plan["id"] = fsx.sha256_value(plan)[:16]
    return plan, ctx


def summary(plan):
    lines = ["Vault OS %s plan %s" % (plan["version"], plan["id"])]
    by = {}
    for a in plan["actions"]:
        by.setdefault((a["layer"], a["op"]), []).append(a)
    labels = {"write": "replace or add file", "tree": "replace or add folder", "remove": "remove (archived first)",
              "split": "rewrite system keys", "seed": "create starter page", "upstream": "install upstream skill"}
    for (layer, op), items in sorted(by.items()):
        lines.append("%s: %s x%d" % (layer, labels[op], len(items)))
        for a in items[:40]:
            extra = ""
            if op == "remove":
                extra = " (%s)" % a["why"]
            elif op == "split" and a.get("notes"):
                extra = " (%s)" % ", ".join(a["notes"][:8]) + (" ..." if len(a["notes"]) > 8 else "")
            elif op == "upstream":
                extra = " (%s at %s)" % (a["reason"], a["commit"][:8])
            lines.append("  %s%s" % (a["path"], extra))
        if len(items) > 40:
            lines.append("  ... and %d more (--json for all)" % (len(items) - 40))
    m = plan["migration"]
    if m["moves"]:
        lines.append("vault: move personal files x%d (never deleted)" % len(m["moves"]))
        for src, dst, why in m["moves"][:60]:
            lines.append("  %s -> %s (%s)" % (src, dst, why))
        if len(m["moves"]) > 60:
            lines.append("  ... and %d more (--json for all)" % (len(m["moves"]) - 60))
    if m["rewrites"]:
        lines.append("vault: rewrite links x%d in %d notes" % (sum(len(e) for e in m["rewrites"].values()),
                                                                len(m["rewrites"])))
    if m.get("kept"):
        lines.append("vault: kept in place, not moved x%d" % len(m["kept"]))
        for rel, why in m["kept"][:40]:
            lines.append("  %s (%s)" % (rel, why))
    for reason in plan.get("approval_reasons") or (["the two bundled L3 doctrine pages are new or changed"]
                                                   if plan["approval_needed"] else []):
        lines.append("memory approval: %s; confirming needs --approve-memory-policy" % reason)
    if any(a["op"] == "upstream" for a in plan["actions"]):
        lines.append("note: upstream setups may write outside the Claude home (gstack: ~/.gstack, ~/.bun, browser "
                     "caches); those writes are not in the backup archive and a rollback does not undo them")
    lines.append("note: the engine installs no MCP servers; add them with python3 installer/install-claude-layer.py "
                 "--mcp default")
    for w in plan["warnings"]:
        lines.append("warning: " + w)
    for q in plan["questions"]:
        lines.append("question: " + q)
    if not plan["actions"] and not m["moves"] and not m["rewrites"]:
        lines.append("nothing to change" + ("; the stamp will be written" if plan["stamp"] else ""))
    return "\n".join(lines)


# ── apply ───────────────────────────────────────────────────────────────────

def pid_alive(pid):
    """True while the process exists. A missing or bad pid counts as dead. Never sends a real signal."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Lock:
    """<backup root>/.vault-os.lock, held by one upgrade, rollback or recover. Only the holder releases it."""

    def __init__(self, backup_root):
        self.path = os.path.join(backup_root, LOCK_NAME)
        self.held = False

    def holder(self):
        """The lock's content ({} when unreadable), or None when there is no lock."""
        if not fsx.lexists(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def refusal(self):
        pid = (self.holder() or {}).get("pid")
        if pid_alive(pid):
            return Stop("another Vault OS upgrade, rollback or recover is running (process %s holds %s): wait for it "
                        "to finish" % (pid, self.path), 4)
        return Stop("a stale lock %s is left by a process that is no longer running: run recover" % self.path, 4)

    def acquire(self, archive_hint=""):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise self.refusal()
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "archive": archive_hint}))
        self.held = True

    def release(self):
        """Remove the lock only when this process took it."""
        if not self.held:
            return
        self.held = False
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def clear_stale(self):
        """Remove a lock whose process is gone (a missing or bad pid counts as gone); refuse while it lives."""
        found = self.holder()
        if found is None:
            return
        if pid_alive(found.get("pid")):
            raise self.refusal()
        if self.holder() == found:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass


def preflight(answers, homes, write=True):
    if answers["platform"] == "win32" and write:
        raise Stop(WINDOWS_REFUSAL, 5)
    if incomplete_archives(homes["backups"]):
        raise Stop("an earlier upgrade was interrupted: run recover first (%s)"
                   % incomplete_archives(homes["backups"])[-1], 4)
    lock = Lock(homes["backups"])
    if lock.holder() is not None:
        raise lock.refusal()


def prefetch(repo, ctx, answers, workdir):
    icl = manifest.load_installers(repo)[0]
    inst = icl.Installer(os.path.join(workdir, "unused"), False, False, answers["platform"], answers["python"],
                         answers["owner"], answers["language"])
    out = {}
    for u in ctx.upstream:
        item = u["item"]
        # A host copy of a folder kind needs the pinned source too; a host setup runs from the Claude checkout.
        hosts = [h for h in (u.get("hosts") or {}).values() if h["status"] == "install"]
        if u["status"] != "install" and not (hosts and item["kind"] == "folder"):
            continue
        dest = os.path.join(workdir, "upstream", item["name"])
        if item["kind"] == "folder":
            src = os.path.join(workdir, "fetch-" + item["name"])
            if not inst._fetch(item["repo"], item["commit"], src):
                raise Stop("could not fetch %s at %s; nothing was changed" % (item["repo"], item["commit"][:8]), 6)
            shutil.copytree(os.path.join(src, *item["path"].split("/")), dest, symlinks=True)
        else:
            if not inst._fetch(item["repo"], item["commit"], dest):
                raise Stop("could not fetch %s at %s; nothing was changed" % (item["repo"], item["commit"][:8]), 6)
        out[item["name"]] = dest
    return out


def stamp_doc(plan, homes, state, archive, upstream_records, ctx):
    tpl = ctx.layers["claude"].splits["settings.json"]["template"]
    return {
        "schema_version": 1, "system": "vault-os", "state": state,
        "version": plan["version"], "repo": REPO_SLUG, "source": plan["source"],
        "installed_at": utc_now(), "archive": archive,
        "answers": plan["answers"],
        "vault_origin": plan.get("vault_origin"),
        "beside": list(getattr(ctx, "beside", [])),
        "homes": {k: homes[k] for k in ("claude", "codex", "agents")},
        "manifest": {name: layer.describe() for name, layer in ctx.layers.items()},
        "upstream": upstream_records,
        "settings": {"system_env": sorted((tpl.get("env") or {}).keys()),
                     "system_allow": list((tpl.get("permissions") or {}).get("allow", []))},
    }


def write_archive_files(archive, plan, stamp):
    with open(os.path.join(archive.path, "plan.json"), "w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2, ensure_ascii=False)
    with open(os.path.join(archive.path, "stamp-before.json"), "w", encoding="utf-8") as handle:
        json.dump(stamp, handle, indent=2, ensure_ascii=False)
    with open(os.path.join(archive.path, "README.txt"), "w", encoding="utf-8") as handle:
        handle.write(
            "Vault OS upgrade archive. Nothing here is deleted automatically.\n\n"
            "files/<layer>/   copies of everything the upgrade replaced or removed (claude, codex, agents, vault)\n"
            "journal.jsonl    every change, recorded before it happened\n"
            "plan.json        the plan that was confirmed\n"
            "migration/       moves and link rewrites of personal notes, with undo.py\n\n"
            "Roll the whole upgrade back:\n"
            "  python3 installer/vault-os.py rollback \"%s\"\n" % archive.path)


def run(cmd, cwd, env=None, timeout=1800):
    return subprocess.run(cmd, cwd=cwd, env=env or child_env(), capture_output=True, text=True, timeout=timeout)


def install_upstream(archive, ctx, homes, fetched, answers, log, layer="claude", records=None):
    """Install the upstream items (layer claude) or their host copies (layer codex or agents, right after that
    layer is applied). Returns the stamp records, host copies under records[name]["hosts"][layer]."""
    records = {} if records is None else records
    if layer != "claude":
        return install_upstream_hosts(archive, ctx, homes, fetched, layer, records, log)
    skills = os.path.join(homes["claude"], "skills")
    for u in ctx.upstream:
        item, name = u["item"], u["item"]["name"]
        if u["status"] in ("adopt", "unavailable"):
            dest = os.path.join(skills, name)
            rec = {"kind": item["kind"], "commit": item["commit"], "children": u["children"], "status": u["status"]}
            if item["kind"] == "folder" and os.path.isdir(dest):
                rec["tree_sha256"] = fsx.tree_sha(dest)[0]
            records[name] = rec
            continue
        rel = "skills/" + name
        archive.put_tree("claude", rel, fetched[name])
        rec = {"kind": item["kind"], "commit": item["commit"], "children": [], "status": "installed"}
        if item["kind"] == "folder":
            rec["tree_sha256"] = fsx.tree_sha(os.path.join(skills, name))[0]
        else:
            before = set(os.listdir(skills))
            archive.external("claude", "skills")
            archive.external("claude", "")
            archive.expect_tool_change("claude", "settings.json")
            env = dict(child_env(), HOME=os.path.dirname(homes["claude"].rstrip("/")))
            dest = os.path.join(skills, name)
            out = run(item["setup"], dest, env)
            if out.returncode != 0:
                raise RuntimeError("upstream setup of %s failed: %s" % (name, (out.stderr or out.stdout)[-300:]))
            config = os.path.join(dest, "bin", "gstack-config")
            for key, value in item.get("config", {}).items():
                if os.path.exists(config):
                    run([config, "set", key, value], dest, env, 60)
            rec["children"] = sorted(set(os.listdir(skills)) - before - {name})
            rec["children"] = manifest.upstream_children(skills, name, rec["children"])
        records[name] = rec
        log("installed upstream %s at %s" % (name, item["commit"][:8]))
    return records


def install_upstream_hosts(archive, ctx, homes, fetched, layer, records, log):
    """Host copies for Codex or ~/.agents: a folder kind gets the pinned folder under the host's name; a setup kind
    runs its host setup from the Claude checkout (gstack: ./setup --host codex), no new fetch."""
    skills = os.path.join(homes[layer], "skills")
    for u in ctx.upstream:
        h = (u.get("hosts") or {}).get(layer)
        if not h:
            continue
        item, name = u["item"], u["item"]["name"]
        rec = records.setdefault(name, {"kind": item["kind"], "commit": item["commit"], "children": [],
                                        "status": u["status"]})
        host = {"name": h["name"], "commit": item["commit"], "children": h["children"], "status": h["status"]}
        rel = "skills/" + h["name"]
        if h["status"] == "install" and item["kind"] == "folder":
            archive.put_tree(layer, rel, fetched[name])
            host["status"] = "installed"
        elif h["status"] == "install":
            checkout = os.path.join(homes["claude"], "skills", name)
            archive.mkdir(layer, "skills")
            archive.external(layer, "skills")
            archive.expect_tool_change("claude", "skills/%s/.agents" % name)  # the host render inside the checkout
            archive.expect_tool_change("claude", "settings.json")
            env = dict(child_env(), HOME=os.path.dirname(homes["claude"].rstrip("/")), CODEX_HOME=homes["codex"])
            out = run(item["hosts"][layer]["setup"], checkout, env)
            if out.returncode != 0:
                raise RuntimeError("upstream %s setup of %s failed: %s" % (layer, name,
                                                                          (out.stderr or out.stdout)[-300:]))
            host["children"] = manifest.upstream_children(skills, h["name"])
            host["status"] = "installed"
        if item["kind"] == "folder" and os.path.isdir(os.path.join(skills, h["name"])):
            host["tree_sha256"] = fsx.tree_sha(os.path.join(skills, h["name"]))[0]
        rec.setdefault("hosts", {})[layer] = host
        if host["status"] == "installed":
            log("installed upstream %s for %s at %s" % (name, layer, item["commit"][:8]))
    return records


def apply_layer(archive, ctx, actions, fetched_unused=None):
    for a in actions:
        layer = ctx.layers[a["layer"]]
        rel = a["path"]
        if a["op"] == "remove":
            archive.remove(a["layer"], rel)
        elif a["op"] == "write":
            archive.copy_file(a["layer"], rel, layer.entries[rel]["src"])
        elif a["op"] == "tree":
            archive.put_tree(a["layer"], rel, layer.entries[rel]["src"])
        elif a["op"] == "split":
            archive.write_file(a["layer"], rel, ctx.data[(a["layer"], rel)])
        elif a["op"] == "seed":
            archive.copy_file(a["layer"], rel, layer.seeds[rel])


def apply_migration(archive, ctx, vault, log):
    m = ctx.migration
    if not m["moves"] and not m["rewrites"]:
        return None
    for rel, sha in ctx.migration_before.items():
        if migrate.identity(os.path.join(vault, *rel.split("/"))) != sha:
            raise RuntimeError("a personal file changed after the plan: %s" % rel)
    created_before = {r["path"] for r in journal.read_journal(archive.journal_path) if r["op"] == "mkdir"}
    moves = {src: dst for src, dst, _ in m["moves"]}
    for src, dst, _ in m["moves"]:
        archive.move("vault", src, dst)
    after = {}
    for rel, edits in m["rewrites"].items():
        final = moves.get(rel, rel)
        path = os.path.join(vault, *final.split("/"))
        text = migrate.read_text(path)
        archive.write_file("vault", final, migrate.encode(migrate.apply_edits(text, edits)), fsx.is_exec(path))
    removed = []
    parents = sorted({os.path.dirname(src) for src in moves if "/" in src}, key=lambda p: -p.count("/"))
    layer = getattr(ctx, "layers", {}).get("vault")
    system = set(layer.entries) | set(layer.seeds) | set(layer.splits) | set(layer.owned_dirs) if layer else set()
    keep = {"/".join(p.split("/")[:i]).lower() for p in system for i in range(1, p.count("/") + 1)}
    seen = set()
    for folder in parents:
        while folder and folder not in seen and folder.lower() not in keep:  # no rmdir, then mkdir (B1)
            seen.add(folder)
            path = os.path.join(vault, *folder.split("/"))
            if os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path):
                archive.rmdir("vault", folder)
                removed.append(folder)
                folder = os.path.dirname(folder)
            else:
                break
    for rel in ctx.migration_before:
        final = moves.get(rel, rel)
        after[final] = migrate.identity(os.path.join(vault, *final.split("/")))
    created = [r["path"] for r in journal.read_journal(archive.journal_path)
               if r["op"] == "mkdir" and r["layer"] == "vault" and r["path"] not in created_before]
    mapping = dict(m, vault=vault, before=ctx.migration_before, after=after, created_dirs=created,
                   removed_dirs=removed)
    folder = os.path.join(archive.path, "migration")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "mapping.json"), "w", encoding="utf-8") as handle:
        json.dump(mapping, handle, indent=2, ensure_ascii=False)
    with open(os.path.join(folder, "undo.py"), "w", encoding="utf-8") as handle:
        handle.write(migrate.UNDO_SCRIPT)
    problems = migrate.verify(vault, ctx.migration_before, m, ctx.migration_links, getattr(ctx, "migration_extra", ()))
    if problems:
        raise RuntimeError("migration check failed: " + "; ".join(problems[:5]))
    log("moved %d personal files, rewrote links in %d notes" % (len(m["moves"]), len(m["rewrites"])))
    return mapping


def git(vault, *args, identity=False):
    cmd = ["git"]
    if identity:
        cmd += ["-c", "user.name=Vault OS", "-c", "user.email=vault-os@localhost"]
    return run(cmd + list(args), vault, timeout=300)


def vault_checkpoint(archive, ctx, plan, vault, log):
    """Commit the system paths, rebuild the zone indexes, commit their authority receipts. A failed commit, index
    build or validation raises: the upgrade rolls back (UPGRADE.md 2.2, 7) and never reports complete."""
    warnings = []
    if os.path.isdir(os.path.join(vault, ".git")):
        for name in GIT_META:
            archive.expect_tool_change("vault", ".git/" + name)
        archive.external("vault", ".git")
    else:
        archive.expect_tool_change("vault", ".git")
    for rel in VAULT_TOOL_PATHS:
        archive.expect_tool_change("vault", rel)
    archive.external("vault", ".vault-meta")
    if not os.path.isdir(os.path.join(vault, ".git")):
        out = git(vault, "init", "-q")
        if out.returncode:
            raise RuntimeError("git init of the vault failed: " + out.stderr.strip()[-200:])
    named = git(vault, "config", "user.name").stdout.strip() and git(vault, "config", "user.email").stdout.strip()
    layer = ctx.layers["vault"]
    seeds = [a["path"] for a in plan["actions"] if a["layer"] == "vault" and a["op"] == "seed"]
    splits = {rel for rel in layer.splits if rel != manifest.VAULT_SETTINGS_SPLIT}
    paths = sorted(set(layer.entries) | splits | set(seeds) | {a["path"] for a in plan["actions"]
                                                               if a["layer"] == "vault" and a["op"] == "remove"})
    paths = [p for p in paths if p not in layer.protected]
    commit_paths(vault, paths, "Vault OS %s: system files" % plan["version"], not named, warnings)
    for zone in ZONES:
        token = run(["bash", "scripts/wiki-lock.sh", "acquire", ".vault-meta/write/" + zone, "--ttl", "3600"], vault)
        if token.returncode:
            raise RuntimeError("memory index of zone %s: lock failed: %s" % (zone, token.stderr.strip()[-200:]))
        tok = token.stdout.strip()
        try:
            for step in ("build", "validate"):
                out = run([sys.executable, "-B", "scripts/memory-model.py", step, "--zone", zone, "--lock-token", tok],
                          vault)
                if out.returncode:
                    raise RuntimeError("memory index of zone %s: %s failed: %s"
                                       % (zone, step, (out.stderr or out.stdout).strip()[-300:]))
        finally:
            run(["bash", "scripts/wiki-lock.sh", "release", ".vault-meta/write/" + zone, tok], vault)
    index_dir = os.path.join(vault, ".vault-meta", "memory-index")
    receipts = sorted(".vault-meta/memory-index/" + n for n in os.listdir(index_dir)
                      if n.endswith(".authority.json")) if os.path.isdir(index_dir) else []
    if receipts:
        commit_paths(vault, receipts, "Vault OS %s: memory index authority" % plan["version"], not named, warnings)
    for w in warnings:
        log("warning: " + w)
    return warnings


def on_disk_case(root, rel):
    """rel spelled as the file system stores it (a case-insensitive volume keeps the first spelling: the person's
    Scripts/ holds the system scripts/ files). Parts that do not exist keep the given spelling."""
    out, current = [], root
    for i, part in enumerate(rel.split("/")):
        try:
            names = os.listdir(current)
        except OSError:
            return "/".join(out + rel.split("/")[i:])
        if part not in names:
            match = [n for n in names if n.lower() == part.lower() and os.path.samefile(os.path.join(current, n),
                                                                                       os.path.join(current, part))] \
                if os.path.lexists(os.path.join(current, part)) else []
            part = match[0] if match else part
        out.append(part)
        current = os.path.join(current, part)
    return "/".join(out)


def commit_paths(vault, paths, message, identity, warnings):
    """Commit exactly these paths. A path that is a folder now goes as "path/", so Git replaces the link or file it
    tracked there (SPOT-CHECK MAJOR-1). A failed add or commit leaves nothing staged and raises (MINOR 5)."""
    present = []
    for p in paths:
        p = on_disk_case(vault, p)
        full = os.path.join(vault, *p.split("/"))
        if fsx.lexists(full) or git(vault, "ls-files", "--", p).stdout.strip():
            present.append(p + "/" if os.path.isdir(full) and not os.path.islink(full) else p)
    if not present:
        return
    for i in range(0, len(present), 200):
        out = git(vault, "add", "-A", "--", *present[i:i + 200])
        if out.returncode:
            unstage(vault, present)
            raise RuntimeError("vault checkpoint: git add failed: " + out.stderr.strip()[-200:])
    staged = git(vault, "diff", "--cached", "--name-only", "--", *present).stdout.strip()
    if not staged:
        return
    out = git(vault, "commit", "-q", "-m", message, "--", *present, identity=identity)
    if out.returncode:
        unstage(vault, present)
        raise RuntimeError("vault checkpoint: git commit failed (%s): %s"
                           % (message, (out.stderr or out.stdout).strip()[-200:]))


def unstage(vault, paths):
    """Leave nothing of a failed checkpoint staged in the person's Git index (MINOR 3)."""
    head = git(vault, "rev-parse", "-q", "--verify", "HEAD").returncode == 0
    for i in range(0, len(paths), 200):
        if head:
            git(vault, "reset", "-q", "--", *paths[i:i + 200])
        else:
            git(vault, "rm", "-q", "-r", "--cached", "--ignore-unmatch", "--", *paths[i:i + 200])


def validate_zones(vault):
    """The verify step: every zone index validates against the committed authority receipt (no lock token)."""
    for zone in ZONES:
        out = run([sys.executable, "-B", "scripts/memory-model.py", "validate", "--zone", zone], vault)
        if out.returncode:
            raise RuntimeError("memory index of zone %s does not validate: %s"
                               % (zone, (out.stderr or out.stdout).strip()[-300:]))


def chain_check(vault):
    out = run([sys.executable, "-B", ".vault-meta/evolution/reseal-policy.py", "--check"], vault, timeout=120)
    if out.returncode:
        return "trust chain: " + (out.stdout + out.stderr).strip()[-300:]
    probe = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('p',sys.argv[1]);"
             "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.load_policy()")
    out = run([sys.executable, "-B", "-c", probe, ".vault-meta/evolution/reflection_policy.py"], vault, timeout=120)
    if out.returncode:
        return "reflection policy: " + (out.stdout + out.stderr).strip()[-300:]
    return None


def upgrade(repo, answers, homes, stamp, opts, plan, ctx, log=print):
    """Apply a confirmed plan. Returns (archive path or None, warnings). Rolls back by itself on any failure."""
    preflight(answers, homes)
    if plan["questions"]:
        raise Stop("the plan has open questions; answer them and plan again", 3)
    if plan["approval_needed"] and not opts.get("approve_memory_policy"):
        reasons = "; ".join(plan.get("approval_reasons") or ["the two bundled L3 doctrine pages are new or changed"])
        raise Stop("the plan re-approves L3 memory (%s): ask the person, then add --approve-memory-policy" % reasons,
                   3)
    m = plan["migration"]
    if not plan["actions"] and not m["moves"] and not m["rewrites"] and not plan["stamp"]:
        log("nothing to change: the installed system already matches this release")
        return None, []
    workdir = tempfile.mkdtemp(prefix="vault-os-fetch-")
    lock = Lock(homes["backups"])
    archive = None
    try:
        fetched = prefetch(repo, ctx, answers, workdir)
        roots = {"claude": homes["claude"], "codex": homes["codex"], "agents": homes["agents"],
                 "vault": answers["vault"]}
        os.makedirs(homes["backups"], exist_ok=True)
        archive = journal.Archive.create(homes["backups"], roots)
        try:
            lock.acquire(archive.path)
        except Stop:
            os.rmdir(os.path.join(archive.path, "files"))  # the empty archive this run just made
            os.rmdir(archive.path)
            raise
        archive.record("begin", plan_id=plan["id"], version=plan["version"], roots=roots, at=utc_now())
        write_archive_files(archive, plan, stamp)
        log("backup archive: " + archive.path)
        upstream_records = {}
        try:
            first = [a for a in plan["actions"] if a["layer"] == "claude" and a["op"] == "remove"
                     and STAMP_REL.startswith(a["path"] + "/")]  # a link or file where the stamp folder goes
            apply_layer(archive, ctx, first)
            interim = stamp_doc(plan, homes, "upgrading", archive.path, {}, ctx)
            archive.write_file("claude", STAMP_REL, boundary.dump_json(interim))
            for name in LAYER_ORDER:
                layer_acts = ordered(name, [a for a in plan["actions"] if a["layer"] == name and a["op"] != "upstream"
                                            and a not in first])
                if name == "vault":  # phase 0 renames beside and phase 1 moves before any write (B1, UPGRADE.md 5.1)
                    apply_migration(archive, ctx, answers["vault"], log)
                apply_layer(archive, ctx, layer_acts)
                if name in ("claude",) + manifest.UPSTREAM_HOSTS:
                    upstream_records = install_upstream(archive, ctx, homes, fetched, answers, log, name,
                                                        upstream_records)
            warnings = list(plan["warnings"])
            if plan["vault_git"]:
                warnings += vault_checkpoint(archive, ctx, plan, answers["vault"], log)
            final = stamp_doc(plan, homes, "installed", archive.path, upstream_records, ctx)
            archive.write_file("claude", STAMP_REL, boundary.dump_json(final))
            problems = verify(repo, homes, opts)
            if problems:
                raise RuntimeError("parity after the upgrade is not 0: " + "; ".join(problems[:8]))
            if plan["vault_git"]:
                validate_zones(answers["vault"])
            archive.settle(regenerable=[("vault", p) for p in VAULT_TOOL_PATHS + (".git/index",)])
            archive.record("commit", at=utc_now())
            log("upgrade complete: parity 0")
            return archive.path, warnings
        except BaseException as error:
            log("upgrade failed (%s); rolling back" % error)
            journal.rollback(archive.path, log)
            raise Stop("the upgrade failed and was rolled back: %s" % error, 7)
    finally:
        lock.release()
        shutil.rmtree(workdir, ignore_errors=True)


def verify(repo, homes, opts):
    """Parity of the machine against the installed stamp: every difference as one line."""
    stamp = read_stamp(homes["claude"])
    answers = dict((stamp or {}).get("answers") or {})
    answers.pop("date", None)
    plan, ctx = build(repo, answers, homes, stamp, opts)
    try:
        problems = differences(plan)
        if not problems:
            chain = chain_check(answers["vault"])
            if chain:
                problems.append(chain)
        return problems
    finally:
        ctx.cleanup()


def differences(plan):
    out = ["%s %s %s" % (a["layer"], a["op"], a["path"]) for a in plan["actions"]]
    out += ["vault move %s -> %s" % (src, dst) for src, dst, _ in plan["migration"]["moves"]]
    out += ["vault links %s" % rel for rel in plan["migration"]["rewrites"]]
    out += ["question: " + q for q in plan["questions"]]
    if plan["approval_needed"]:
        out.append("vault doctrine approval differs from this release")
    return out


# ── rollback and recovery ───────────────────────────────────────────────────

def rollback_archive(path, homes, answers_platform, confirm=None, log=print):
    """Without confirm: the preview (changes nothing). With the preview's id: apply exactly that rollback."""
    if answers_platform == "win32":
        raise Stop(WINDOWS_REFUSAL, 5)
    path = os.path.abspath(path)
    status = journal.status(path)
    if status == "rolled_back":
        raise Stop("this archive was already rolled back", 2)
    if status == "empty":
        raise Stop("not an upgrade archive: %s" % path, 2)
    if status == "incomplete":
        raise Stop("this upgrade was interrupted: run recover", 4)
    live = [a for a in journal.list_archives(os.path.dirname(path)) if journal.status(a) in ("committed", "incomplete")]
    if live and os.path.abspath(live[-1]) != path:
        raise Stop("only the latest upgrade can be rolled back; the latest is %s" % live[-1], 2)
    if confirm is None:
        return journal.preview(path)
    lock = Lock(os.path.dirname(path))
    lock.acquire(path)
    try:
        current = journal.preview(path)
        if current["id"] != confirm:
            raise Stop("the machine changed since the preview (rollback %s, confirmed %s): show the new preview and "
                       "ask again" % (current["id"], confirm), 3)
        journal.rollback(path, log)
    finally:
        lock.release()
    log("rolled back: " + path)
    return None


def rollback_summary(p):
    """The preview as text, ending with the exact command that applies it."""
    lines = ["Rollback preview for %s: nothing has changed yet." % p["archive"],
             "  restores %d paths from the archive (system files, settings, notes back to their old places)"
             % p["restores"],
             "  stays in place (%d)%s" % (len(p["stays"]), ":" if p["stays"] else "")]
    lines += ["    %s %s: %s" % (s["layer"], s["path"] or ".", s["reason"]) for s in p["stays"]]
    lines.append("  moves to the holding folder %s%s (%d)%s" % (p["holding_folder"], os.sep, len(p["displaced"]),
                                                               ":" if p["displaced"] else ""))
    lines += ["    %s %s: %s" % (d["layer"], d["path"] or ".", d["reason"]) for d in p["displaced"]]
    if p["git"] == "restored":
        lines.append("  the vault's Git branch goes back to where it was; the upgrade's commits stay as unreferenced "
                     "objects until git gc prunes them")
    lines.append("")
    lines.append("To apply exactly this rollback: python3 installer/vault-os.py rollback \"%s\" --confirm %s"
                 % (p["archive"], p["id"]))
    return "\n".join(lines)


def recover(homes, platform, log=print):
    """Roll back every interrupted upgrade, unattended. Refuses while a live process holds the lock."""
    if platform == "win32":
        raise Stop(WINDOWS_REFUSAL, 5)
    lock = Lock(homes["backups"])
    lock.clear_stale()
    pending = incomplete_archives(homes["backups"])
    if not pending:
        log("nothing to recover")
        return None
    lock.acquire("recover")
    try:
        for path in reversed(pending):
            journal.rollback(path, log)
            log("recovered: rolled back the interrupted upgrade " + path)
    finally:
        lock.release()
    return pending[-1]
