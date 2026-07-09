#!/usr/bin/env python3
"""health-check.py — SessionStart-нудж для Vault Evolution.

Запускается хуком при старте сессии (cwd = vault root). Читает last-run.json,
считает давность + быстрые сигналы (memory orphans, log.md length, retrieval lag).
Если пороги превышены — печатает нудж в stdout (хук инжектит его в контекст).
Иначе молчит. Никогда не падает: любой сбой -> тихий выход 0 (старт сессии важнее).
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.abspath(os.path.join(HERE, "..", ".."))
LAST_RUN = os.path.join(HERE, "last-run.json")
AUDIT = os.path.join(HERE, "memory-audit.py")

# Пороги нуджа
DAYS_THRESHOLD = 7
LOG_LINES_THRESHOLD = 250
RETRIEVAL_LAG_THRESHOLD = 40  # непроиндексированных страниц


def days_since_last():
    try:
        with open(LAST_RUN, encoding="utf-8") as f:
            last = json.load(f).get("last_run")
        d = datetime.date.fromisoformat(last)
        return (datetime.date.today() - d).days, last
    except Exception:
        return None, None


def memory_orphans():
    try:
        out = subprocess.run(
            [sys.executable, AUDIT, "--json"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        data = json.loads(out)
        return len(data.get("orphans", [])), len(data.get("dead_links", []))
    except Exception:
        return None, None


def log_lines():
    path = os.path.join(VAULT, "wiki", "log.md")
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return None


def retrieval_lag():
    try:
        chunks = os.path.join(VAULT, ".vault-meta", "chunks")
        n_chunks = len([x for x in os.listdir(chunks)]) if os.path.isdir(chunks) else 0
        n_pages = 0
        for root, _, files in os.walk(os.path.join(VAULT, "wiki")):
            n_pages += sum(1 for x in files if x.endswith(".md"))
        return max(0, n_pages - n_chunks), n_chunks, n_pages
    except Exception:
        return None, None, None


def main():
    reasons = []

    days, last = days_since_last()
    if days is None:
        reasons.append("гигиена ещё не запускалась")
    elif days >= DAYS_THRESHOLD:
        reasons.append("последняя гигиена %d дн. назад" % days)

    orphans, dead = memory_orphans()
    if orphans:
        reasons.append("memory orphans: %d" % orphans)
    if dead:
        reasons.append("memory dead-links: %d" % dead)

    lines = log_lines()
    if lines and lines > LOG_LINES_THRESHOLD:
        reasons.append("log.md: %d строк (пора fold)" % lines)

    lag, nc, npg = retrieval_lag()
    if lag and lag >= RETRIEVAL_LAG_THRESHOLD:
        reasons.append("retrieval отстал: %d/%d страниц не в индексе" % (lag, npg))

    if not reasons:
        return 0  # тихо

    print("🧹 VAULT EVOLUTION — пора прибраться")
    for r in reasons:
        print("  - %s" % r)
    print("Запусти: скажи «прогони evolution» (плейбук: .vault-meta/evolution/EVOLUTION.md)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
