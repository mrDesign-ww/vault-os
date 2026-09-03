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
import sqlite3
import sys

CLAUDE_DEFAULT_DIR = "{{CLAUDE_TRACE_PATH}}/memory"
CODEX_DEFAULT_DB = os.path.expanduser("~/.codex/memories_1.sqlite")
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
    """Первые два дефис-сегмента как ключ кластера (project-alpha-build-status -> project-alpha)."""
    stem = name[:-3] if name.endswith(".md") else name
    parts = stem.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else stem


def age_days(path, today):
    mtime = datetime.date.fromtimestamp(os.path.getmtime(path))
    return (today - mtime).days


def audit_codex(db_path):
    """Read-only health audit for Codex's generated local memory database."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError("Codex memory DB not found: %s" % db_path)
    # immutable=1 prevents SQLite from creating lock or journal files beside the
    # generated DB, which is required in Codex's read-only sandbox.
    conn = sqlite3.connect("file:%s?mode=ro&immutable=1" % db_path, uri=True)
    try:
        integrity_rows = conn.execute("PRAGMA quick_check").fetchall()
        integrity = [row[0] for row in integrity_rows]
        memories = conn.execute("SELECT COUNT(*) FROM stage1_outputs").fetchone()[0]
        selected = conn.execute(
            "SELECT COUNT(*) FROM stage1_outputs WHERE selected_for_phase2 = 1"
        ).fetchone()[0]
        empty_rows = conn.execute(
            "SELECT COUNT(*) FROM stage1_outputs "
            "WHERE TRIM(raw_memory) = '' OR TRIM(rollout_summary) = ''"
        ).fetchone()[0]
        job_rows = conn.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY status"
        ).fetchall()
    finally:
        conn.close()

    integrity_ok = integrity == ["ok"]
    return {
        "platform": "codex",
        "dir": db_path,
        "integrity": integrity,
        "memories": memories,
        "selected_for_phase2": selected,
        "empty_rows": empty_rows,
        "jobs": {status: count for status, count in job_rows},
        "orphans": [],
        "dead_links": [],
        "frontmatter_gaps": {},
        "stale": [],
        "status_stale": [],
        "consolidation_candidates": {},
        "issue_count": (0 if integrity_ok else 1) + empty_rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=("claude", "codex"), default="claude")
    ap.add_argument("--dir", help="Claude memory dir or Codex memory SQLite path")
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--cluster-min", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.platform == "codex":
        db_path = args.dir or CODEX_DEFAULT_DB
        try:
            result = audit_codex(db_path)
        except (OSError, sqlite3.Error) as exc:
            print("ERR: %s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        print("=== CODEX MEMORY AUDIT ===")
        print("db:        %s" % db_path)
        print("integrity: %s" % ", ".join(result["integrity"]))
        print("memories:  %d  |  selected phase 2: %d  |  empty rows: %d"
              % (result["memories"], result["selected_for_phase2"], result["empty_rows"]))
        print("jobs:      %s" % (result["jobs"] or "none"))
        print("summary:   %d hard issues" % result["issue_count"])
        return 0

    mem_dir = args.dir or CLAUDE_DEFAULT_DIR
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
        "platform": "claude",
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
