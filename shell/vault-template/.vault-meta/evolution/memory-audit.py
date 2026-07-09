#!/usr/bin/env python3
"""memory-audit.py — детерминированная диагностика memory/ для Curator (Vault Evolution, Фаза 1).

Находит:
  - orphans      файл есть на диске, но его нет в MEMORY.md
  - dead links   MEMORY.md ссылается на несуществующий файл
  - frontmatter  пробелы в обязательных полях (name / description / type)
  - stale        файл не трогался дольше порога (--stale-days)
  - status-stale старый файл со статус-маркерами (кандидат «подтверди/обнови»)
  - clusters     группы по префиксу имени (кандидаты на консолидацию)

Ничего не меняет. Только отчёт. Вывод человекочитаемый или --json (для health-check).
"""
import argparse
import datetime
import json
import os
import re
import sys

def _default_memory_dir():
    """Claude Code memory dir for this vault: ~/.claude/projects/<slug>/memory,
    where <slug> is the vault's absolute path with '/' and spaces turned into '-'.
    Override with the VAULT_ROOT env var; otherwise derive from this file's location."""
    vault = os.environ.get("VAULT_ROOT") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".."))
    slug = re.sub(r"[ /]", "-", vault.rstrip("/"))
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug, "memory")


DEFAULT_DIR = _default_memory_dir()
INDEX_NAME = "MEMORY.md"
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")
STATUS_RE = re.compile(
    r"\b(status|M\d+\b|next|done|pending|TODO|WIP|in progress|milestone|phase)\b", re.I
)


def parse_index(index_path):
    if not os.path.isfile(index_path):
        return set()
    with open(index_path, encoding="utf-8") as f:
        text = f.read()
    return {os.path.basename(m.group(1)) for m in LINK_RE.finditer(text)}


def frontmatter_gaps(path):
    """Список отсутствующих обязательных полей frontmatter, или None если блока нет вовсе."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ["unreadable"]
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end]
    missing = []
    for field in ("name", "description", "type"):
        if not re.search(r"(?m)^\s*%s:\s*\S" % field, block):
            missing.append(field)
    return missing


def cluster_key(name):
    """Первые два дефис-сегмента как ключ кластера (project-x-build-status -> project-x)."""
    stem = name[:-3] if name.endswith(".md") else name
    parts = stem.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else stem


def age_days(path, today):
    mtime = datetime.date.fromtimestamp(os.path.getmtime(path))
    return (today - mtime).days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--cluster-min", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem_dir = args.dir
    index_path = os.path.join(mem_dir, INDEX_NAME)
    if not os.path.isdir(mem_dir):
        print("ERR: memory dir not found: %s" % mem_dir, file=sys.stderr)
        return 2

    today = datetime.date.today()
    indexed = parse_index(index_path)
    files = sorted(
        f for f in os.listdir(mem_dir) if f.endswith(".md") and f != INDEX_NAME
    )
    fileset = set(files)

    orphans = sorted(fileset - indexed)
    dead_links = sorted(indexed - fileset)

    fm_gaps = {}
    stale = []
    status_stale = []
    clusters = {}
    for f in files:
        path = os.path.join(mem_dir, f)
        gaps = frontmatter_gaps(path)
        if gaps is None:
            fm_gaps[f] = ["no-frontmatter-block"]
        elif gaps:
            fm_gaps[f] = gaps
        age = age_days(path, today)
        if age > args.stale_days:
            stale.append((f, age))
            try:
                body = open(path, encoding="utf-8").read()
            except OSError:
                body = ""
            if STATUS_RE.search(body):
                status_stale.append((f, age))
        clusters.setdefault(cluster_key(f), []).append(f)

    consolidation = {
        k: sorted(v) for k, v in clusters.items() if len(v) >= args.cluster_min
    }

    result = {
        "dir": mem_dir,
        "files": len(files),
        "indexed": len(indexed),
        "orphans": orphans,
        "dead_links": dead_links,
        "frontmatter_gaps": fm_gaps,
        "stale": [{"file": f, "age_days": a} for f, a in sorted(stale, key=lambda x: -x[1])],
        "status_stale": [{"file": f, "age_days": a} for f, a in sorted(status_stale, key=lambda x: -x[1])],
        "consolidation_candidates": consolidation,
        "issue_count": len(orphans) + len(dead_links) + len(fm_gaps),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    def head(t):
        print("\n" + t)

    print("=== MEMORY AUDIT ===")
    print("dir:     %s" % mem_dir)
    print("files:   %d  |  indexed: %d  |  stale-threshold: %dd" % (len(files), len(indexed), args.stale_days))

    head("ORPHANS (on disk, not in MEMORY.md): %d" % len(orphans))
    for f in orphans:
        print("  + %s" % f)

    head("DEAD LINKS (in MEMORY.md, no file): %d" % len(dead_links))
    for f in dead_links:
        print("  - %s" % f)

    head("FRONTMATTER GAPS: %d" % len(fm_gaps))
    for f, gaps in sorted(fm_gaps.items()):
        print("  ! %s -> %s" % (f, ", ".join(gaps)))

    head("STATUS-STALE (old + status markers -> verify/refresh): %d" % len(status_stale))
    for f, a in sorted(status_stale, key=lambda x: -x[1]):
        print("  ? %s (%dd)" % (f, a))

    head("CONSOLIDATION CANDIDATES (cluster >= %d): %d" % (args.cluster_min, len(consolidation)))
    for k, v in sorted(consolidation.items(), key=lambda x: -len(x[1])):
        print("  * %s (%d files):" % (k, len(v)))
        for f in v:
            print("      %s" % f)

    head("STALE (> %dd by mtime): %d" % (args.stale_days, len(stale)))
    for f, a in sorted(stale, key=lambda x: -x[1]):
        print("  . %s (%dd)" % (f, a))

    print("\nsummary: %d hard issues (orphans+dead+fm), %d consolidation clusters, %d status-stale"
          % (result["issue_count"], len(consolidation), len(status_stale)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
