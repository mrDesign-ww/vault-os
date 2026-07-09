#!/usr/bin/env python3
"""lint-scan.py — zone-aware детерминированный вики-lint (Vault Evolution, Фаза 2).

Классифицирует wikilinks честно, чтобы «dead» не раздувалось ложными срабатываниями:
  - ok           резолвится внутри своей зоны
  - memory-ref   цель = memory-факт (живёт вне vault, в memory/) — не поломка
  - cross-zone   цель в ДРУГОЙ зоне (обычно легитимная nav-ссылка между индексами;
                 всё, что НЕ между индексами, помечается как возможное нарушение изоляции)
  - dead         не резолвится нигде — вот это настоящие битые/заготовки

Также: orphans (страница без входящих ссылок, кроме служебных) и frontmatter-пробелы.
Резолвит путевые wikilinks ([[folder/page]]) и экранированные пайпы в таблицах ([[page\\|alias]]).
Только отчёт, ничего не меняет.
"""
import argparse
import json
import os
import re

VAULT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WIKI = os.path.join(VAULT, "wiki")
_vault_for_mem = os.environ.get("VAULT_ROOT") or VAULT
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                   re.sub(r"[ /]", "-", _vault_for_mem.rstrip("/")), "memory")

def _load_zones():
    """Zones from .vault-meta/mode.json (portable across vaults); fall back to work-only.
    Each: {'root': abspath, 'exclude': [other zones' top folder names]}."""
    meta = os.path.join(VAULT, ".vault-meta", "mode.json")
    try:
        z = json.load(open(meta, encoding="utf-8"))["zones"]
    except Exception:
        return {"work": {"root": WIKI, "exclude": []}}
    skip = {"default", "session_scoped", "reset_on_new_session", "isolation", "switch"}
    extra = [k for k, v in z.items()
             if k not in skip and not k.startswith("_") and isinstance(v, dict)
             and v.get("root") and k != "work"]
    zones = {"work": {"root": WIKI,
                      "exclude": [os.path.basename(z[k]["root"].rstrip("/")) for k in extra]}}
    for k in extra:
        zones[k] = {"root": os.path.join(VAULT, z[k]["root"].rstrip("/")), "exclude": []}
    return zones


ZONES = _load_zones()
SERVICE = {"index", "_index", "hot", "log", "overview", "dashboard"}
WIKILINK = re.compile(r"!?\[\[([^\]|#]+)")
FM_FIELDS = ("type", "status", "created", "updated", "tags")
ATTACH = re.compile(r"\.(png|jpe?g|gif|svg|pdf|canvas|excalidraw|webp|mp4|mov)$", re.I)


def zone_files(z):
    root, excl = ZONES[z]["root"], set(ZONES[z]["exclude"])
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        top = rel.split(os.sep)[0] if rel != "." else "."
        if top in excl:
            dirnames[:] = []
            continue
        out += [os.path.join(dirpath, fn) for fn in filenames if fn.endswith(".md")]
    return out


def base(p):
    return os.path.splitext(os.path.basename(p))[0]


def load_names():
    zone_names = {z: {base(p) for p in zone_files(z)} for z in ZONES}
    mem = set()
    if os.path.isdir(MEM):
        mem = {os.path.splitext(f)[0] for f in os.listdir(MEM) if f.endswith(".md")}
    return zone_names, mem


def scan_zone(z, zone_names, mem):
    files = zone_files(z)
    names = zone_names[z]
    others = {n for oz in ZONES if oz != z for n in zone_names[oz]}
    inbound = {n: 0 for n in names}
    dead, cross, memrefs, fm_gaps = [], [], [], []
    for p in files:
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            continue
        if text.startswith("---"):
            end = text.find("\n---", 3)
            block = text[3:end] if end != -1 else ""
            miss = [f for f in FM_FIELDS if not re.search(r"(?m)^\s*%s:" % f, block)]
        else:
            miss = ["no-frontmatter"]
        if miss:
            fm_gaps.append({"page": base(p), "missing": miss})
        for m in WIKILINK.finditer(text):
            target = m.group(1).split("\\")[0].strip()
            if not target or ATTACH.search(target):
                continue
            tb = target.split("/")[-1].strip()
            if tb in names:
                if tb != base(p):
                    inbound[tb] = inbound.get(tb, 0) + 1
            elif tb in mem:
                memrefs.append({"page": base(p), "target": tb})
            elif tb in others:
                cross.append({"page": base(p), "target": tb})
            else:
                dead.append({"page": base(p), "target": target})
    orphans = sorted(n for n in names
                     if inbound.get(n, 0) == 0 and n not in SERVICE and not n.startswith("_"))
    return {"files": len(files), "dead": dead, "cross_zone": cross,
            "memory_refs": memrefs, "orphans": orphans, "fm_gaps": fm_gaps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", choices=list(ZONES) + ["all"], default="all")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    zones = list(ZONES) if args.zone == "all" else [args.zone]
    zone_names, mem = load_names()
    result = {z: scan_zone(z, zone_names, mem) for z in zones}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    for z in zones:
        r = result[z]
        print("\n===== ZONE %s (%d pages) =====" % (z, r["files"]))
        print("DEAD (genuinely unresolved): %d" % len(r["dead"]))
        for d in r["dead"][:30]:
            print("  %-36s -> [[%s]]" % (d["page"][:36], d["target"][:40]))
        if len(r["dead"]) > 30:
            print("  ... +%d more" % (len(r["dead"]) - 30))
        print("memory-refs: %d | cross-zone: %d | orphans: %d | fm-gaps: %d"
              % (len(r["memory_refs"]), len(r["cross_zone"]), len(r["orphans"]), len(r["fm_gaps"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
