#!/usr/bin/env python3
"""fm-fix.py — дозаполнение недостающих полей frontmatter (Vault Evolution, Фаза 2 fix).

Только страницы с СУЩЕСТВУЮЩИМ frontmatter-блоком и частичными пробелами.
НЕ создаёт frontmatter с нуля -> sources/ (immutable по CLAUDE.md), output-файлы и README
без шапки НЕ трогаются. Не трогает body и существующие поля.

Значения: created/updated из git (fallback mtime); type/status/tags из пути-эвристики.
Dry-run по умолчанию; --apply чтобы записать. Пишет напрямую (bash), PostToolUse-хук не триггерит.
"""
import argparse
import datetime
import os
import re
import subprocess

VAULT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WIKI = os.path.join(VAULT, "wiki")
FM = ("type", "status", "created", "updated", "tags")
def _load_zones():
    """Zones from .vault-meta/mode.json; fall back to work-only. Each: (root, (excluded folder names))."""
    import json
    meta = os.path.join(VAULT, ".vault-meta", "mode.json")
    try:
        z = json.load(open(meta, encoding="utf-8"))["zones"]
    except Exception:
        return {"work": (WIKI, ())}
    skip = {"default", "session_scoped", "reset_on_new_session", "isolation", "switch"}
    extra = [k for k, v in z.items()
             if k not in skip and not k.startswith("_") and isinstance(v, dict)
             and v.get("root") and k != "work"]
    zones = {"work": (WIKI, tuple(os.path.basename(z[k]["root"].rstrip("/")) for k in extra))}
    for k in extra:
        zones[k] = (os.path.join(VAULT, z[k]["root"].rstrip("/")), ())
    return zones


ZONES = _load_zones()


def files(root, excl):
    out = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        top = rel.split(os.sep)[0] if rel != "." else "."
        if top in excl:
            dn[:] = []
            continue
        out += [os.path.join(dp, f) for f in fn if f.endswith(".md")]
    return out


def git_date(path, first):
    rel = os.path.relpath(path, VAULT)
    try:
        if first:
            args = ["git", "-C", VAULT, "log", "--follow", "--diff-filter=A",
                    "--format=%ad", "--date=short", "--", rel]
        else:
            args = ["git", "-C", VAULT, "log", "-1", "--format=%ad", "--date=short", "--", rel]
        out = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        if out:
            return out[-1] if first else out[0]
    except Exception:
        pass
    return datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()


def infer(path):
    rel = os.path.relpath(path, VAULT).lower()
    typ = ("board" if "/boards/" in rel else "spec" if "/specs/" in rel
           else "session" if "/inbox/" in rel else "concept" if "/concepts/" in rel
           else "entity" if "/people/" in rel else "note")
    st = "archived" if "/archives/" in rel else "draft" if "/inbox/" in rel else "active"
    tags = []
    m = re.search(r"/projects/([^/]+)/", rel)
    if m:
        tags = [m.group(1)]
    return typ, st, tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--zone", default="all")
    args = ap.parse_args()
    zones = list(ZONES) if args.zone == "all" else [args.zone]

    changed, samples = 0, []
    for z in zones:
        root, excl = ZONES[z]
        for p in files(root, excl):
            t = open(p, encoding="utf-8").read()
            if not t.startswith("---"):
                continue  # no-FM: sources/outputs/README — не трогаем
            end = t.find("\n---", 3)
            if end == -1:
                continue
            block = t[3:end]
            name = os.path.splitext(os.path.basename(p))[0]
            if name in ("index", "_index", "hot", "log", "overview", "dashboard"):
                continue  # служебные каталоги/кэши/логи — особый случай, не трогаем
            if "kanban-plugin:" in block:
                continue  # доска Obsidian Kanban-плагина — frontmatter за плагином
            miss = [f for f in FM if not re.search(r"(?m)^\s*%s:" % f, block)]
            if not miss:
                continue
            typ, st, tags = infer(p)
            add = []
            for f in miss:
                if f == "created":
                    add.append("created: %s" % git_date(p, True))
                elif f == "updated":
                    add.append("updated: %s" % git_date(p, False))
                elif f == "type":
                    add.append("type: %s" % typ)
                elif f == "status":
                    add.append("status: %s" % st)
                elif f == "tags":
                    add.append("tags: [%s]" % ", ".join(tags))
            new = t[:end].rstrip("\n") + "\n" + "\n".join(add) + t[end:]
            changed += 1
            if len(samples) < 10:
                samples.append((os.path.relpath(p, WIKI), miss))
            if args.apply:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(new)

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print("=== fm-fix %s ===" % mode)
    print("pages to update: %d" % changed)
    for rel, miss in samples:
        print("  %-60s += %s" % (rel[:60], ", ".join(miss)))
    if not args.apply:
        print("\n(dry-run; re-run with --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
