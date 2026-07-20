#!/usr/bin/env python3
"""trace-scan.py — извлекатель сигналов трения из session-трейсов (Vault Evolution, Фаза 4 / reflection).

Читает последние N session .jsonl, вытаскивает РЕАЛЬНЫЕ сообщения владельца (не local-command,
не tool_result) и помечает те, что похожи на поправку / повтор инструкции / откат / вызов.

Это только СЫРЫЕ КАНДИДАТЫ. Суждение (реальный ли это урок и какую правку он требует)
делает агент в reflection-фазе, и любое изменение инструкций/памяти/скилла идёт через
verifier + апрув владельца. Скрипт ничего не меняет.
"""
import argparse
import datetime
import glob
import json
import os
import re

def _default_trace_dir():
    """Claude Code session-traces dir: ~/.claude/projects/<slug> (slug = vault path
    with '/' and spaces as '-'). Override with VAULT_ROOT; else derive from location."""
    vault = os.environ.get("VAULT_ROOT") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".."))
    slug = re.sub(r"[ /]", "-", vault.rstrip("/"))
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug)


CLAUDE_DEFAULT_DIR = _default_trace_dir()
CODEX_DEFAULT_DIR = os.path.expanduser("~/.codex/sessions")

FRICTION = [
    (re.compile(r"я же (говорил|сказал|просил|писал)", re.I), "repeat-instruction"),
    (re.compile(r"\b(не так|не туда|не то\b|неверно|неправиль|не надо|не нужно|не должен)", re.I), "correction"),
    (re.compile(r"\b(стоп|погоди|подожди|отставить)\b", re.I), "halt"),
    (re.compile(r"\b(верни|откати|отмени|убери это|не делай)\b", re.I), "revert"),
    (re.compile(r"(зачем|почему) ты\b", re.I), "challenge"),
    (re.compile(r"не (критик|правильно понял|так понял|то понял|понял)", re.I), "misunderstanding"),
    (re.compile(r"\b(no,|not like that|revert|undo that|that.s wrong|stop,|don.t do)\b", re.I), "en-correction"),
]

SKIP_PREFIXES = ("<local-command", "<command-name>", "<system-reminder", "<bash-", "caveat:")


def real_user_text(obj, platform="claude"):
    if platform == "codex":
        if obj.get("type") != "event_msg":
            return None
        payload = obj.get("payload", {}) or {}
        if payload.get("type") != "user_message":
            return None
        s = payload.get("message")
        if not isinstance(s, str):
            return None
    else:
        if obj.get("type") != "user":
            return None
        msg = obj.get("message", {}) or {}
        if msg.get("role") != "user":
            return None
        c = msg.get("content")
        if isinstance(c, str):
            s = c
        elif isinstance(c, list):
            parts = [b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text"]
            if not parts:
                return None
            s = " ".join(parts)
        else:
            return None
    s = " ".join(s.split()).strip()
    if not s:
        return None
    low = s[:40].lower()
    if any(low.startswith(p) or p in low for p in SKIP_PREFIXES):
        return None
    sl = s.lower()
    if "<command-name>" in s or "local-command-" in s:
        return None
    if "this session is being continued" in sl or "base directory for this skill" in sl:
        return None
    if platform == "claude" and (
        "image source" in sl or "image-cache" in sl or "/users/" in sl
    ):
        return None
    if "triggers on:" in sl or "skill tool" in sl or "usage —" in sl or "description:" in sl:
        return None
    return s


def scan_file(path, platform="claude"):
    hits = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                s = real_user_text(obj, platform)
                if not s:
                    continue
                for rx, kind in FRICTION:
                    if rx.search(s):
                        quote = s if len(s) <= 200 else s[:197] + "..."
                        hits.append({"kind": kind, "quote": quote})
                        break
    except OSError:
        pass
    return hits


# --- Детект повторяющихся процедур (кандидаты на капсуляцию в скилл/плейбук) ---
STOP = set("""это как для при был при что чтобы если когда надо нужно можно вот там тут еще ещё
уже или них она они оно его ему нее неё что-то там-то этот эта эти тот все всё так там como
this that with have from your will just about which them then here there what when make need
your into должен также очень чтобы теперь можешь давай нужно сделай сделать делать посмотри
image cache source users claude node http https www conversation save concept title
skip naming duplicate exists usage workflow offer update valuable specific explicitly creating
session content analyze full same name note already read summary being continued command
местами можно нужно хочу think work zone task""".split())
TERM = re.compile(r"[a-zа-яё0-9]{4,}", re.I)


def session_terms(path, platform="claude", max_msgs=3):
    """Термы/биграммы из ПЕРВЫХ max_msgs реальных сообщений сессии (её намерение, не весь шум)."""
    words = []
    count = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                s = real_user_text(obj, platform)
                if not s:
                    continue
                count += 1
                if count > max_msgs:
                    break
                s = re.sub(r"https?://\S+", " ", s.lower())  # выбросить URL
                toks = [w for w in TERM.findall(s) if w not in STOP and not w.isdigit()]
                words += toks
    except OSError:
        return set()
    terms = set(w for w in words if len(w) >= 4)
    bigrams = set("%s %s" % (words[i], words[i + 1]) for i in range(len(words) - 1)
                  if words[i] not in STOP and words[i + 1] not in STOP)
    return terms | bigrams


def scan_repeats(files, platform="claude", min_sessions=3):
    """Термы/биграммы, встречающиеся в >= min_sessions РАЗНЫХ сессий — кандидаты процедур."""
    from collections import Counter
    counter = Counter()
    for p in files:
        for t in session_terms(p, platform):
            counter[t] += 1
    # биграммы информативнее униграмм; поднимем их и отфильтруем по порогу
    cand = [(t, n) for t, n in counter.items() if n >= min_sessions]
    cand.sort(key=lambda x: (-(x[0].count(" ")), -x[1]))  # сначала биграммы, потом по частоте
    return cand


def session_label(path, platform):
    stem = os.path.splitext(os.path.basename(path))[0]
    if platform == "codex":
        match = re.search(
            r"([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            stem,
            re.I,
        )
        if match:
            return match.group(1)
    return stem[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=("claude", "codex"), default="claude")
    ap.add_argument("--dir")
    ap.add_argument("--sessions", type=int, default=8, help="последних N сессий по mtime (для трения)")
    ap.add_argument("--repeats", action="store_true", help="+ детект повторяющихся процедур (кандидаты капсуляции)")
    ap.add_argument("--min-sessions", type=int, default=3, help="порог повтора для капсуляции")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    trace_dir = args.dir or (CODEX_DEFAULT_DIR if args.platform == "codex" else CLAUDE_DEFAULT_DIR)
    pattern = (os.path.join(trace_dir, "**", "*.jsonl")
               if args.platform == "codex" else os.path.join(trace_dir, "*.jsonl"))

    all_files = sorted(
        glob.glob(pattern, recursive=args.platform == "codex"),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    files = all_files[: args.sessions]

    report = []
    for p in files:
        hits = scan_file(p, args.platform)
        if hits:
            mtime = datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat()
            report.append({
                "session": session_label(p, args.platform),
                "date": mtime,
                "signals": hits,
            })

    total = sum(len(r["signals"]) for r in report)

    if args.json:
        out = {"platform": args.platform, "dir": trace_dir,
               "sessions_scanned": len(files), "signal_count": total, "sessions": report}
        if args.repeats:
            out["repeats"] = [{"term": t, "sessions": n}
                              for t, n in scan_repeats(all_files, args.platform, args.min_sessions)]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("=== TRACE SCAN (friction signals) ===")
    print("sessions scanned: %d | signals: %d" % (len(files), total))
    for r in report:
        print("\n[%s] %s" % (r["session"], r["date"]))
        for h in r["signals"]:
            print("  %-18s %s" % (h["kind"] + ":", h["quote"]))
    print("\nNOTE: сырые кандидаты. В reflection-фазе оцени каждый: реальный ли урок ->")
    print("      предложение правки инструкций / wiki / skill через verifier + апрув владельца.")

    if args.repeats:
        cand = scan_repeats(all_files, args.platform, args.min_sessions)
        print("\n=== REPEATED PROCEDURES (capsule candidates, >= %d sessions) ===" % args.min_sessions)
        print("scanned %d sessions | %d recurring terms/bigrams" % (len(all_files), len(cand)))
        for t, n in cand[:25]:
            kind = "bigram" if " " in t else "term"
            print("  %-7s x%-2d  %s" % (kind, n, t))
        print("\nNOTE: сырьё для суждения. Я фильтрую -> реально устойчивая процедура? ->")
        print("      черновик плейбука/скилла тебе на апрув (порог %d, двойной фильтр)." % args.min_sessions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
