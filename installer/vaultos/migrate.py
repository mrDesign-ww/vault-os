"""Vault migration: re-sort personal notes and attachments into the system's folder structure by moving them.

plan() is read-only and returns the mapping (moves, renames, link rewrites, blocked folders, warnings).
The engine applies it through the journal; verify() checks the invariants afterwards. See UPGRADE.md section 7.
"""
import json
import os
import posixpath
import re
import unicodedata
from urllib.parse import unquote

from . import fsx

KEEP_PREFIXES = (
    "wiki/workspace/projects/", "wiki/areas/", "wiki/resources/", "wiki/archives/", "wiki/personal/",
    "wiki/client/", "Reference/", "_attachments/",
)
WIKI_ROOT_KEEP = {"index.md", "overview.md", "log.md", "hot.md", "dashboard.md", "command center.base"}
NEVER_SCAN_TOP = {"scripts"}
REPEATABLE_NAMES = {"_index", "log", "readme"}  # the memory index accepts these names more than once
NOTE_EXT = ".md"
CANVAS_EXT = ".canvas"
INBOX = "wiki/workspace/projects/inbox"
PERSONAL_INBOX = "wiki/personal/inbox"
SYSTEM_PATH_REASON = "personal file at a system path"
SPECIAL_REASON = "special file (FIFO, socket or device)"
LINK_REASON = "symbolic link at a system path"
FILE_REASON = "file where the system needs a folder"
FOLDER_REASON = "folder where the system keeps a file"
BESIDE_REASONS = (LINK_REASON, FILE_REASON, FOLDER_REASON)  # renamed beside their path in phase 0 (UPGRADE.md 5.1)
HELD_REASON = "renamed beside a system path by an earlier upgrade"

BUCKETS = {
    "projects": ("projects", "project", "проекты", "проект"),
    "areas": ("areas", "area", "области", "сферы", "responsibilities"),
    "resources": ("resources", "resource", "ресурсы", "references", "knowledge", "library", "база знаний"),
    "archives": ("archives", "archive", "архив", "архивы"),
    "inbox": ("inbox", "входящие", "unsorted", "неразобранное"),
    "people": ("people", "persons", "contacts", "люди", "контакты"),
    "concepts": ("concepts", "ideas", "идеи", "концепции"),
    "templates": ("templates", "template", "шаблоны"),
    "attachments": ("attachments", "attachment", "assets", "files", "media", "images", "вложения"),
    "personal": ("personal", "private", "личное", "личные"),
    "client": ("client", "clients", "клиент", "клиенты"),
}
WORK_DEST = {
    "projects": "wiki/workspace/projects", "areas": "wiki/areas", "resources": "wiki/resources",
    "archives": "wiki/archives", "inbox": INBOX, "people": "wiki/resources/people",
    "concepts": "wiki/resources/concepts", "templates": "wiki/resources/templates", "attachments": "_attachments",
}

WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
MDLINK = re.compile(r"(!?)\[([^\[\]\n]*)\]\((<[^<>\n]+>|[^()\s<>]+)((?:\s+\"[^\"\n]*\")?)\)")
CANVAS_FILE = re.compile(r'("file"\s*:\s*)("(?:[^"\\]|\\.)*")')
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")


def normalize_name(name):
    text = unicodedata.normalize("NFC", name).strip().lower()
    text = re.sub(r"^[\W\d_]+", "", text)  # numbering and emoji prefixes: "1. Projects", "01 - Areas", "📥 Inbox"
    return text.strip()


def bucket_of(folder):
    norm = normalize_name(folder)
    for bucket, names in BUCKETS.items():
        if norm in names:
            return bucket
    return None


def keep_in_place(rel):
    low = rel.lower()
    if any(low.startswith(p.lower()) for p in KEEP_PREFIXES):
        return True
    return low.startswith("wiki/") and low.count("/") == 1 and low[5:] in WIKI_ROOT_KEEP


# ── scanning ────────────────────────────────────────────────────────────────

def case_insensitive(path):
    """True when the volume of path ignores case (read-only probe: the same folder under a changed case)."""
    path = os.path.abspath(path)
    base = os.path.basename(path.rstrip(os.sep))
    swapped = base.swapcase()
    if not base or swapped == base:
        return False
    other = os.path.join(os.path.dirname(path.rstrip(os.sep)), swapped)
    try:
        return os.path.exists(other) and os.path.samefile(path, other)
    except OSError:
        return False


def scan(vault, system_paths, kept=None, fold_case=False, held=()):
    """Every personal file of the vault (relative posix paths), dot folders, scripts/ and system files excluded.
    Symbolic links, folders that hold their own Git repository and held paths (renamed beside a system path by an
    earlier upgrade) are not personal files to move: they stay in place and, when kept is a dict, are listed there
    as {rel: {"reason", "files"}} (UPGRADE.md section 7)."""
    held = {p.lower() if fold_case else p for p in held}
    system = {p.lower() for p in system_paths}
    system_dirs = tuple(p.lower().rstrip("/") + "/" for p in system_paths)
    never = {n.lower() for n in NEVER_SCAN_TOP} if fold_case else NEVER_SCAN_TOP
    kept = {} if kept is None else kept
    out = []
    for current, dirs, files in os.walk(vault, followlinks=False):
        rel_dir = os.path.relpath(current, vault).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        keep = []
        for d in sorted(dirs):
            rel = rel_dir + d
            if d.startswith(".") or fsx.is_junk(d) or (rel_dir == "" and (d.lower() if fold_case else d) in never):
                continue
            if rel.lower() in system:
                continue
            path = os.path.join(current, d)
            if os.path.islink(path):
                kept[rel] = {"reason": "symbolic link", "files": [rel]}
            elif (rel.lower() if fold_case else rel) in held:
                kept[rel] = {"reason": HELD_REASON, "files": repo_files(vault, rel)}
            elif os.path.lexists(os.path.join(path, ".git")):
                kept[rel] = {"reason": "Git repository", "files": repo_files(vault, rel)}
            else:
                keep.append(d)
        dirs[:] = keep
        for name in sorted(files):
            if name.startswith(".") or fsx.is_junk(name):
                continue
            rel = rel_dir + name
            if rel.lower() in system or rel.lower().startswith(system_dirs):
                continue
            if os.path.islink(os.path.join(current, name)):
                kept[rel] = {"reason": "symbolic link", "files": [rel]}
                continue
            if not fsx.is_regular(os.path.join(current, name)):
                kept[rel] = {"reason": SPECIAL_REASON, "files": [rel]}
                continue
            if (rel.lower() if fold_case else rel) in held:
                kept[rel] = {"reason": HELD_REASON, "files": [rel]}
                continue
            out.append(rel)
    return out


def repo_files(vault, rel_dir):
    """Files of a nested Git repository (its .git and dot folders excluded): link targets, never moved."""
    out = []
    root = os.path.join(vault, *rel_dir.split("/"))
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not fsx.is_junk(d))
        sub = os.path.relpath(current, vault).replace(os.sep, "/")
        out.extend(sub + "/" + n for n in sorted(files) if not n.startswith(".") and not fsx.is_junk(n))
    return out


def read_text(path):
    if not fsx.is_regular(path):
        raise OSError("%s is not a regular file (FIFO, socket or device): it is not read" % path)
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "surrogateescape")


def frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    data, key = {}, None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", line)
        if m:
            key, value = m.group(1).lower(), m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                data[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
            else:
                data[key] = value.strip("\"'")
            continue
        m = re.match(r"^\s*-\s*(.+)$", line)
        if m and key:
            if not isinstance(data.get(key), list):
                data[key] = [] if data.get(key) in ("", None) else [data[key]]
            data[key].append(m.group(1).strip().strip("\"'"))
    return data


# ── layout and link resolution ──────────────────────────────────────────────

class Layout:
    def __init__(self, paths):
        self.paths = set(paths)
        self.lower = {}
        self.by_name = {}
        for p in self.paths:
            self.lower.setdefault(p.lower(), p)
            self.by_name.setdefault(posixpath.basename(p).lower(), []).append(p)

    def exists(self, rel):
        return self.lower.get(rel.lower())


def pick(candidates, src):
    folder = posixpath.dirname(src)
    return sorted(candidates, key=lambda p: (posixpath.dirname(p) != folder, p.count("/"), len(p), p))[0]


def clean(rel):
    rel = posixpath.normpath(rel)
    return None if rel.startswith("..") or rel == "." else rel.lstrip("/")


def resolve_wiki(target, src, layout):
    t = target.strip().replace("\\", "/")
    if not t:
        return None
    for cand in (t + NOTE_EXT, t):
        if "/" in cand:
            for base in ("", posixpath.dirname(src)):
                rel = clean(posixpath.join(base, cand))
                if rel and layout.exists(rel):
                    return layout.exists(rel)
            low = "/" + cand.lower().lstrip("/")
            hits = [p for p in layout.paths if ("/" + p.lower()).endswith(low)]
            if hits:
                return pick(hits, src)
        else:
            hits = layout.by_name.get(cand.lower())
            if hits:
                return pick(hits, src)
    return None


def resolve_md(dest, src, layout):
    raw = dest[1:-1] if dest.startswith("<") and dest.endswith(">") else dest
    if SCHEME.match(raw) or raw.startswith("#"):
        return None
    path = unquote(raw.split("#", 1)[0])
    if not path:
        return None
    for cand in (path, path + NOTE_EXT):
        for base in (posixpath.dirname(src), ""):
            rel = clean(posixpath.join(base, cand.lstrip("/") if base == "" else cand))
            if rel and layout.exists(rel):
                return layout.exists(rel)
    return None


def split_wiki_inner(inner):
    """target, anchor, separator, alias of the text between [[ and ]]."""
    sep, alias = "", None
    for s in ("\\|", "|"):
        if s in inner:
            inner, alias = inner.split(s, 1)
            sep = s
            break
    anchor = ""
    for mark in ("#", "^"):
        if mark in inner:
            i = inner.index(mark)
            inner, anchor = inner[:i], inner[i:] + anchor
            break
    return inner, anchor, sep, alias


def code_mask(text):
    """Spans (start, end) of fenced code blocks and inline code, where links are not links."""
    spans, pos, fence = [], 0, None
    start = 0
    for line in text.splitlines(True):
        m = FENCE.match(line)
        if fence is None and m:
            fence, start = m.group(1)[0] * 3, pos
        elif fence is not None and m and m.group(1).startswith(fence):
            spans.append((start, pos + len(line)))
            fence = None
        pos += len(line)
    if fence is not None:
        spans.append((start, len(text)))
    for m in re.finditer(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", text, re.S):
        spans.append((m.start(), m.end()))
    return spans


def in_spans(i, spans):
    return any(a <= i < b for a, b in spans)


def links_of(rel, text):
    """[(start, end, kind, parts)] of the cross-file links in a note or canvas."""
    out = []
    if rel.lower().endswith(CANVAS_EXT):
        for m in CANVAS_FILE.finditer(text):
            try:
                value = json.loads(m.group(2))
            except ValueError:
                continue
            out.append((m.start(2), m.end(2), "canvas", {"path": value}))
        return out
    spans = code_mask(text)
    for m in WIKILINK.finditer(text):
        if in_spans(m.start(), spans):
            continue
        target, anchor, sep, alias = split_wiki_inner(m.group(2))
        out.append((m.start(), m.end(), "wiki", {"embed": m.group(1), "target": target, "anchor": anchor,
                                                  "sep": sep, "alias": alias}))
    for m in MDLINK.finditer(text):
        if in_spans(m.start(), spans):
            continue
        out.append((m.start(), m.end(), "md", {"embed": m.group(1), "label": m.group(2), "dest": m.group(3),
                                                "title": m.group(4)}))
    return out


def resolve(kind, parts, src, layout):
    if kind == "wiki":
        return resolve_wiki(parts["target"], src, layout)
    if kind == "md":
        return resolve_md(parts["dest"], src, layout)
    return layout.exists(parts["path"])


def link_candidates(kind, parts, src):
    """The vault paths a link would try, as (path, relative to the note) pairs, in the order resolve tries them."""
    if kind == "canvas":
        return [(clean(parts["path"]), False)]
    out = []
    if kind == "wiki":
        t = parts["target"].strip().replace("\\", "/")
        for cand in (t + NOTE_EXT, t) if t else ():
            if "/" in cand:
                out += [(clean(posixpath.join(base, cand)), bool(base)) for base in ("", posixpath.dirname(src))]
        return out
    raw = parts["dest"][1:-1] if parts["dest"].startswith("<") and parts["dest"].endswith(">") else parts["dest"]
    path = unquote(raw.split("#", 1)[0])
    if SCHEME.match(raw) or raw.startswith("#") or not path:
        return []
    src_dir = posixpath.dirname(src)
    for cand in (path, path + NOTE_EXT):
        out += [(clean(posixpath.join(base, cand.lstrip("/") if base == "" else cand)), base != "" or not src_dir)
                for base in (src_dir, "")]
    return out


def rebase_through_link(kind, parts, src, new_src, folder_links):
    """A link that goes through a symbolic link, by path, only the path string is rebased (the link's target is never
    listed or read): the link's old prefix becomes its final path, and the text is measured from the note's new place.
    folder_links is {old path: final path}. Returns the new link text, or None when nothing needs to change."""
    for rel, relative in link_candidates(kind, parts, src):
        if not rel:
            continue
        for old, final in folder_links.items():
            if not rel.lower().startswith(old.lower() + "/"):
                continue
            new_rel = final + rel[len(old):]
            if new_rel == rel and (not relative or posixpath.dirname(new_src) == posixpath.dirname(src)):
                return None
            if kind == "md":
                return rewrite_link("md", parts, new_src, new_rel, None)
            if kind == "canvas":
                return json.dumps(new_rel, ensure_ascii=False)
            is_note = rel.lower().endswith(NOTE_EXT) and not parts["target"].lower().endswith(NOTE_EXT)
            target = new_rel[:-len(NOTE_EXT)] if is_note else new_rel
            alias, sep = parts["alias"], parts["sep"]
            if alias is None and not parts["embed"] and target != parts["target"]:
                alias, sep = parts["target"], sep or "|"
            return "%s[[%s%s%s]]" % (parts["embed"], target, parts["anchor"], "" if alias is None else sep + alias)
    return None


def rewrite_link(kind, parts, new_src, new_target, layout):
    """The shortest link text from new_src that reaches new_target in layout."""
    if kind == "canvas":
        return json.dumps(new_target, ensure_ascii=False)
    if kind == "md":
        rel = posixpath.relpath(new_target, posixpath.dirname(new_src) or ".")
        raw = parts["dest"]
        anchor = ""
        inner = raw[1:-1] if raw.startswith("<") else raw
        if "#" in inner:
            anchor = "#" + inner.split("#", 1)[1]
        if raw.startswith("<"):
            dest = "<%s%s>" % (rel, anchor)
        else:
            dest = rel.replace(" ", "%20").replace("(", "%28").replace(")", "%29") + anchor
        return "%s[%s](%s%s)" % (parts["embed"], parts["label"], dest, parts["title"])
    is_note = new_target.lower().endswith(NOTE_EXT)
    name = posixpath.basename(new_target)
    short = name[:-len(NOTE_EXT)] if is_note else name
    full = new_target[:-len(NOTE_EXT)] if is_note else new_target
    target = short if len(layout.by_name.get(name.lower(), [])) == 1 else full
    if resolve_wiki(target, new_src, layout) != new_target:
        target = new_target
    alias, sep = parts["alias"], parts["sep"]
    if alias is None and not parts["embed"] and target != parts["target"]:
        alias, sep = parts["target"], sep or "|"
    tail = "" if alias is None else sep + alias
    return "%s[[%s%s%s]]" % (parts["embed"], target, parts["anchor"], tail)


# ── destinations ────────────────────────────────────────────────────────────

def zone_roots(vault):
    """Zone roots of an old vault (from .vault-meta/mode.json), for the blocked-zone rule."""
    try:
        with open(os.path.join(vault, ".vault-meta", "mode.json"), encoding="utf-8") as handle:
            zones = json.load(handle).get("zones", {})
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for slug, value in zones.items():
        if isinstance(value, dict) and isinstance(value.get("root"), str):
            out[value["root"].rstrip("/")] = slug
    return out


def looks_like_zone(vault, folder):
    names = {n.lower() for n in os.listdir(os.path.join(vault, folder))} if os.path.isdir(
        os.path.join(vault, folder)) else set()
    return "_index.md" in names and bool({"hot.md", "log.md"} & names)


def region_of(rel):
    """(prefix kept out of the mapping, region path whose first folder decides)."""
    low = rel.lower()
    if low.startswith("wiki/workspace/"):
        return rel[:15], rel[15:]
    if low.startswith("wiki/"):
        return rel[:5], rel[5:]
    return "", rel


def compose(zone, bucket, rest):
    rest = rest.strip("/")
    if zone in ("personal", "client"):
        sub = "projects" if bucket == "projects" else "inbox"
        return posixpath.join("wiki", zone, sub, rest) if rest else posixpath.join("wiki", zone, sub)
    return posixpath.join(WORK_DEST[bucket], rest)


def destination(rel, meta, maps, inbox=INBOX):
    """(destination, reason) of one personal file, or (None, reason) when it stays. inbox takes unsorted notes."""
    for src, dst in maps:
        if rel.lower().startswith(src.lower().rstrip("/") + "/"):
            return posixpath.join(dst, rel[len(src.rstrip("/")) + 1:]), "map %s" % src
    _, region = region_of(rel)
    parts = region.split("/")
    first = parts[0] if len(parts) > 1 else None
    bucket = bucket_of(first) if first else None
    zone = None
    if bucket in ("personal", "client"):
        zone, rest_parts = bucket, parts[1:]
        sub = bucket_of(rest_parts[0]) if len(rest_parts) > 1 else None
        bucket = "projects" if sub == "projects" else "inbox"
        if sub == "projects":
            rest_parts = rest_parts[1:]
        rest = "/".join(rest_parts)
    elif bucket:
        rest = "/".join(parts[1:])
    else:
        rest = region
    reason = "folder %s" % first if bucket else "unsorted"
    fm_zone = (meta.get("zone") or "").lower() if isinstance(meta.get("zone"), str) else ""
    fm_bucket = None
    para = meta.get("para") or meta.get("category")
    if isinstance(para, str) and bucket_of(para) in WORK_DEST:
        fm_bucket = bucket_of(para)
    tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    for tag in tags:
        m = re.match(r"^#?para/(\w+)", str(tag))
        if m and bucket_of(m.group(1)) in WORK_DEST:
            fm_bucket = fm_bucket or bucket_of(m.group(1))
    if isinstance(meta.get("status"), str) and meta["status"].lower() in ("archived", "archive"):
        fm_bucket = fm_bucket or "archives"
    if fm_zone in ("work", "personal", "client") and zone is None:  # never widens a folder's zone
        zone = None if fm_zone == "work" else fm_zone
        reason = "frontmatter zone"
    if fm_bucket:
        bucket = fm_bucket
        reason = "frontmatter %s" % fm_bucket
    if bucket is None:
        bucket = "inbox"
    if bucket == "attachments" and rel.lower().endswith(NOTE_EXT):
        bucket = "inbox"
        rest = region
    if zone is None and bucket in ("personal", "client"):
        zone, bucket = bucket, "inbox"
    if zone is None and bucket == "inbox" and fm_zone != "work":
        return posixpath.join(inbox, rest.strip("/")), reason
    return compose(zone, bucket, rest), reason


def suffixed(path, n):
    stem, ext = posixpath.splitext(path)
    return "%s (%d)%s" % (stem, n, ext)


# ── the plan ────────────────────────────────────────────────────────────────

def is_link_doc(rel):
    return rel.lower().endswith((NOTE_EXT, CANVAS_EXT))


def plan(vault, system_paths, reserved_paths=(), maps=(), displaced=(), inbox=INBOX, fold_case=False, detail=None,
         links=(), held=()):
    """Read-only migration plan. reserved_paths are system and seed paths a move must not land on; displaced are
    personal files found at a system path, moved below the inbox before the system file is written there; links
    ({path: reason}, or paths of symbolic links) are the wrong shapes the path-shape preflight found at a system path
    (a link, a file where a folder is needed, a folder where a file is), renamed beside it with " (n)" in phase 0;
    held are paths an earlier upgrade renamed so, kept in place."""
    kept = {}
    displaced = sorted(set(displaced))
    link_set = dict(links) if isinstance(links, dict) else {rel: LINK_REASON for rel in links}
    fold = (lambda p: p.lower()) if fold_case else (lambda p: p)
    gone = {fold(rel) for rel in link_set}

    def through_beside(rel):
        low = fold(rel)
        return low in gone or any(low.startswith(g + "/") for g in gone)

    scanned = [f for f in scan(vault, system_paths, kept, fold_case, held) if not through_beside(f)]
    files = sorted(set(scanned) | set(displaced) | set(link_set))
    for rel in list(kept):
        if through_beside(rel):
            kept.pop(rel)  # a path renamed in phase 0 (or inside one) moves: never both kept and changed
    kept_paths = sorted({f for item in kept.values() for f in item["files"]})
    reserved = sorted(set(reserved_paths))
    texts, metas = {}, {}
    for rel in files:
        if is_link_doc(rel) and rel not in link_set:
            texts[rel] = read_text(os.path.join(vault, *rel.split("/")))
            metas[rel] = frontmatter(texts[rel]) if rel.lower().endswith(NOTE_EXT) else {}
    zones = zone_roots(vault)
    blocked, warnings = {}, []
    moves, reasons = {}, {}
    map_pairs = sorted(maps, key=lambda m: -len(m[0]))
    for rel in displaced:
        moves[rel] = posixpath.join(inbox, rel)
        reasons[rel] = SYSTEM_PATH_REASON
    for rel in files:
        if rel in moves or rel in link_set:
            continue
        if keep_in_place(rel) and not any(rel.lower().startswith(s.lower().rstrip("/") + "/") for s, _ in map_pairs):
            continue
        prefix, region = region_of(rel)
        top = (prefix + region.split("/")[0]) if "/" in region else None
        mapped = any(rel.lower().startswith(s.lower().rstrip("/") + "/") for s, _ in map_pairs)
        if top and not mapped:
            slug = zones.get(top)
            name = normalize_name(region.split("/")[0])
            if (slug or looks_like_zone(vault, top)) and name not in ("personal", "client") \
                    and (slug or name) not in ("work", "personal", "client") and top != "wiki":
                blocked.setdefault(top, []).append(rel)
                continue
        dest, reason = destination(rel, metas.get(rel, {}), map_pairs, inbox)
        if dest and dest != rel:
            moves[rel] = dest
            reasons[rel] = reason
    live_reserved = [p for p in reserved if os.path.lexists(os.path.join(vault, *p.split("/")))]
    old_extra = sorted(set(kept_paths) | set(live_reserved))
    new_extra = sorted(set(kept_paths) | set(reserved))
    old = Layout(list(files) + old_extra)
    # Attachments follow a note that moves for its frontmatter and is their only user.
    users = {}
    for rel, text in texts.items():
        for _, _, kind, parts in links_of(rel, text):
            target = resolve(kind, parts, rel, old)
            if target and target != rel:
                users.setdefault(target, set()).add(rel)
    for rel in files:
        if is_link_doc(rel) or rel in blocked or rel in link_set or reasons.get(rel) == SYSTEM_PATH_REASON:
            continue
        who = users.get(rel, set())
        if len(who) != 1:
            continue
        note = next(iter(who))
        if note not in moves or not reasons.get(note, "").startswith("frontmatter"):
            continue
        note_dir = posixpath.dirname(note)
        if note_dir and not rel.startswith(note_dir + "/"):
            continue
        sub = rel[len(note_dir) + 1:] if note_dir else rel
        moves[rel] = posixpath.join(posixpath.dirname(moves[note]), sub)
        reasons[rel] = "follows %s" % note
    # Collisions: paths (case-insensitive), then unique note names for moved notes (staying notes, kept files and
    # the system's own non-dot notes count, so a moved index.md or hot.md does not shadow wiki/index.md).
    staying = [f for f in files if f not in moves]
    taken = {p.lower() for p in staying} | {p.lower() for p in reserved} | {p.lower() for p in kept_paths}
    names = {posixpath.basename(p).lower() for p in staying + kept_paths if p.lower().endswith(NOTE_EXT)}
    names |= {posixpath.basename(p).lower() for p in reserved if p.lower().endswith(NOTE_EXT)
              and not any(part.startswith(".") for part in p.split("/"))}
    renamed = {}
    order = sorted(moves, key=lambda p: (p.count("/"), len(p), p))
    # A destination must also be free on disk (an unscanned file or folder there), and never be a folder another
    # destination or a system path needs (path-shape preflight, UPGRADE.md 5.1).
    need_dirs = {"/".join(p.lower().split("/")[:i]) for p in list(moves.values()) + reserved
                 for i in range(1, p.count("/") + 1)}

    def on_disk(rel):
        return not through_beside(rel) and os.path.lexists(os.path.join(vault, *rel.split("/")))

    for rel in order:
        dest = moves[rel]
        is_note = dest.lower().endswith(NOTE_EXT)
        stem = posixpath.splitext(posixpath.basename(dest))[0].lower()
        n, cand = 1, dest
        while cand.lower() in taken or cand.lower() in need_dirs or on_disk(cand) or (
                is_note and stem not in REPEATABLE_NAMES and posixpath.basename(cand).lower() in names):
            n += 1
            cand = suffixed(dest, n)
        if cand != dest:
            renamed[rel] = cand
        moves[rel] = cand
        taken.add(cand.lower())
        if is_note:
            names.add(posixpath.basename(cand).lower())
    # A wrong shape at a system path (a link, a file where a folder is needed, a folder where a file is) is renamed in
    # its own folder: its bytes stay, a relative link target keeps resolving, and it never lands in a zone the memory
    # index reads (SPOT-CHECK MAJOR-1, SPOT-CHECK-2 MAJOR-1 and MAJOR-2).
    taken_dirs = {"/".join(p.split("/")[:i]) for p in taken for i in range(1, p.count("/") + 1)} | need_dirs
    for rel in sorted(link_set):
        n = 2
        cand = suffixed(rel, n)
        while cand.lower() in taken or cand.lower() in taken_dirs \
                or os.path.lexists(os.path.join(vault, *cand.split("/"))):
            n += 1
            cand = suffixed(rel, n)
        moves[rel] = cand
        reasons[rel] = link_set[rel]
        taken.add(cand.lower())
    final = {rel: moves.get(rel, rel) for rel in files}
    new = Layout(list(final.values()) + new_extra)
    rewrites = {}
    folder_links = {rel: moves.get(rel, rel) for rel in sorted(set(kept) | set(link_set))
                    if os.path.islink(os.path.join(vault, *rel.split("/")))}
    for rel, text in texts.items():
        new_src = final[rel]
        edits = []
        for start, end, kind, parts in links_of(rel, text):
            target = resolve(kind, parts, rel, old)
            if target is None:
                # A link by path into a symbolic link to a folder: the layout holds no path below it, so only the
                # path string is rebased.
                replacement = rebase_through_link(kind, parts, rel, new_src, folder_links) if folder_links else None
                if replacement and replacement != text[start:end]:
                    edits.append([start, text[start:end], replacement])
                continue
            want = final.get(target, target)
            if resolve(kind, parts, new_src, new) == want:
                continue
            replacement = rewrite_link(kind, parts, new_src, want, new)
            if replacement != text[start:end]:
                edits.append([start, text[start:end], replacement])
        if edits:
            rewrites[rel] = edits
    # Files inside a kept Git repository are never rewritten: name the links the moves break there.
    for unit, item in sorted(kept.items()):
        for rel in item["files"]:
            if item["reason"] != "Git repository" or not is_link_doc(rel):
                continue
            try:
                text = read_text(os.path.join(vault, *rel.split("/")))
            except OSError:
                continue
            for _, _, kind, parts in links_of(rel, text):
                target = resolve(kind, parts, rel, old)
                if target in moves and resolve(kind, parts, rel, new) != moves[target]:
                    warnings.append("%s (inside the Git repository %s, kept as it is) links to %s, which moves to %s"
                                    % (rel, unit, target, moves[target]))
    for rel in sorted({r for r, item in kept.items() if item["reason"] == "symbolic link"} | set(link_set)):
        path = os.path.join(vault, *rel.split("/"))
        if not os.path.islink(path) or os.path.isabs(os.readlink(path)):
            continue
        target = clean(posixpath.join(posixpath.dirname(rel), os.readlink(path).replace(os.sep, "/")))
        where = "%s (now %s)" % (rel, moves[rel]) if rel in link_set else rel
        inside = sorted(src for src in moves if target and src.startswith(target + "/") and src not in link_set)
        if target and target in moves:
            warnings.append("symbolic link %s points to %s, which moves to %s; the link is kept as it is"
                            % (where, target, moves[target]))
        elif inside:  # a folder target whose files move: the link now reaches an emptier folder or none
            warnings.append("symbolic link %s points to folder %s, whose %d files move (%s -> %s%s); the link is "
                            "kept as it is" % (where, target, len(inside), inside[0], moves[inside[0]],
                                               ", ..." if len(inside) > 1 else ""))
    folders = sorted({m.split("/", 1)[0] for m in moves if "/" in m})
    for cfg in obsidian_configs(vault):
        body = read_text(cfg)
        hits = [f for f in folders if '"%s' % f in body]
        if hits:
            warnings.append("%s names moved folder %s" % (os.path.relpath(cfg, vault), ", ".join(hits)))
    if detail is not None:
        detail.update(files=files, final=final, texts=texts, kept=kept, kept_paths=kept_paths,
                      old_extra=old_extra, new_extra=new_extra)
    return {
        "files": len(files),
        # apply order (UPGRADE.md 5.1): phase 0 renames beside first, then the moves, each group by source path
        "moves": sorted(([src, dst, reasons.get(src, "")] for src, dst in moves.items()),
                        key=lambda m: (m[2] not in BESIDE_REASONS, m[0])),
        "renamed": sorted([src, dst] for src, dst in renamed.items()),
        "rewrites": {rel: edits for rel, edits in sorted(rewrites.items())},
        "blocked": {k: len(v) for k, v in sorted(blocked.items())},
        "kept": sorted([rel, item["reason"]] for rel, item in kept.items()),
        "warnings": warnings,
    }


def obsidian_configs(vault):
    base = os.path.join(vault, ".obsidian")
    out = []
    if os.path.isdir(base):
        out += [os.path.join(base, n) for n in sorted(os.listdir(base))
                if n.endswith(".json") and os.path.isfile(os.path.join(base, n))]
        plugins = os.path.join(base, "plugins")
        if os.path.isdir(plugins):
            for name in sorted(os.listdir(plugins)):
                data = os.path.join(plugins, name, "data.json")
                if os.path.isfile(data):
                    out.append(data)
    return out


def apply_edits(text, edits):
    out, pos = [], 0
    for start, old, new in sorted(edits, key=lambda e: e[0]):
        out.append(text[pos:start])
        out.append(new)
        pos = start + len(old)
    out.append(text[pos:])
    return "".join(out)


def revert_edits(text, edits):
    """Undo apply_edits on its output; None when the text does not carry the recorded edits."""
    out, pos, shift = [], 0, 0
    for start, old, new in sorted(edits, key=lambda e: e[0]):
        at = start + shift
        if text[at:at + len(new)] != new:
            return None
        out.append(text[pos:at])
        out.append(old)
        pos = at + len(new)
        shift += len(new) - len(old)
    out.append(text[pos:])
    return "".join(out)


def encode(text):
    return text.encode("utf-8", "surrogateescape")


# ── verification and undo ───────────────────────────────────────────────────

def identity(path):
    """SHA-256 of a file's bytes, of "link\0<target>" for a symbolic link (never followed, broken ones too), or of
    "tree\0<tree hash>" for a folder renamed beside a system path as one unit."""
    if os.path.islink(path):
        return fsx.sha256_text("link\0" + os.readlink(path))
    if os.path.isdir(path):
        return fsx.sha256_text("tree\0" + fsx.tree_sha(path)[0])
    return fsx.sha256_file(path)


def fingerprint(vault, files):
    return {rel: identity(os.path.join(vault, *rel.split("/"))) for rel in files}


def verify(vault, before, mapping, old_links, extra=()):
    """Invariants after the migration: counts, hashes (reversing logged rewrites) and link targets. extra are the
    paths that are link targets without being moved files (system files, kept links and repositories)."""
    problems = []
    final = {src: dst for src, dst, _ in mapping["moves"]}
    rewrites = mapping["rewrites"]
    for rel, sha in before.items():
        dest = final.get(rel, rel)
        path = os.path.join(vault, *dest.split("/"))
        if not fsx.lexists(path):
            problems.append("missing after migration: %s (was %s)" % (dest, rel))
            continue
        now = identity(path)
        if rel in rewrites:
            original = revert_edits(read_text(path), rewrites[rel])
            if original is None or fsx.sha256_bytes(encode(original)) != sha:
                problems.append("content changed beyond the logged link rewrites: %s" % dest)
        elif now != sha:
            problems.append("content changed: %s" % dest)
    after_files = [final.get(rel, rel) for rel in before]
    if len(set(p.lower() for p in after_files)) != len(before):
        problems.append("file count changed")
    layout = Layout(list(after_files) + list(extra))
    for rel, links in old_links.items():
        dest = final.get(rel, rel)
        text = read_text(os.path.join(vault, *dest.split("/")))
        now = {}
        for _, _, kind, parts in links_of(dest, text):
            target = resolve(kind, parts, dest, layout)
            now.setdefault(target, 0)
            now[target] += 1
        for target, count in links.items():
            want = final.get(target, target)
            if now.get(want, 0) < count:
                problems.append("link from %s no longer reaches %s" % (dest, want))
    return problems


def link_targets(vault, files, extra=()):
    """{note: {target: count}} of resolvable links before the migration."""
    layout = Layout(list(files) + list(extra))
    out = {}
    for rel in files:
        path = os.path.join(vault, *rel.split("/"))
        if not rel.lower().endswith((NOTE_EXT, CANVAS_EXT)) or os.path.islink(path) or not os.path.isfile(path):
            continue
        text = read_text(os.path.join(vault, *rel.split("/")))
        counts = {}
        for _, _, kind, parts in links_of(rel, text):
            target = resolve(kind, parts, rel, layout)
            if target and target != rel:
                counts[target] = counts.get(target, 0) + 1
        if counts:
            out[rel] = counts
    return out


UNDO_SCRIPT = r'''#!/usr/bin/env python3
"""Undo the Vault OS migration recorded in mapping.json next to this script. Never deletes anything:
rewritten notes get their original bytes back from ../files/vault/, moved files go back to their old place.
Usage: python3 undo.py [--dry-run]"""
import hashlib, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DRY = "--dry-run" in sys.argv
mapping = json.load(open(os.path.join(HERE, "mapping.json"), encoding="utf-8"))
vault = mapping["vault"]
backups = os.path.join(os.path.dirname(HERE), "files", "vault")

def p(rel):
    return os.path.join(vault, *rel.split("/"))

def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

skipped = []
for src, dst, _ in mapping["moves"]:
    if src in mapping["rewrites"]:
        copy = os.path.join(backups, *dst.split("/"))
        if os.path.isfile(copy) and os.path.isfile(p(dst)) and sha(p(dst)) == mapping["after"].get(dst):
            print("restore original bytes of", dst)
            if not DRY:
                shutil.copy2(copy, p(dst))
        else:
            skipped.append(dst)
for rel in mapping["rewrites"]:
    if rel in {m[0] for m in mapping["moves"]}:
        continue
    copy = os.path.join(backups, *rel.split("/"))
    if os.path.isfile(copy) and os.path.isfile(p(rel)) and sha(p(rel)) == mapping["after"].get(rel):
        print("restore original bytes of", rel)
        if not DRY:
            shutil.copy2(copy, p(rel))
    else:
        skipped.append(rel)
for src, dst, _ in reversed(mapping["moves"]):
    if os.path.lexists(p(dst)) and not os.path.lexists(p(src)):
        print("move back", dst, "->", src)
        if not DRY:
            os.makedirs(os.path.dirname(p(src)), exist_ok=True)
            os.rename(p(dst), p(src))
    else:
        skipped.append(dst)
for folder in sorted(mapping.get("created_dirs", []), key=len, reverse=True):
    if os.path.isdir(p(folder)) and not os.listdir(p(folder)):
        if not DRY:
            os.rmdir(p(folder))
for folder in sorted(mapping.get("removed_dirs", []), key=len):
    if not os.path.exists(p(folder)) and not DRY:
        os.makedirs(p(folder))
if skipped:
    print("left as they are (changed since the migration or already back):", *skipped, sep="\n  ")
'''
