#!/usr/bin/env python3
"""Claude Code status line: who and where, resources, agents at work.

Reads the status JSON on stdin and prints three lines. Agent and background-task
counts come from the session transcript, scanned incrementally (byte offset cached
per session), so a 1 GB transcript costs one full pass once and only the new tail
afterwards. Runs locally: the model never sees this output, it costs no tokens.

Labels: English by default, Russian with --lang ru (the installer passes it when the language is Russian).
The weekly usage it saves feeds ~/.claude/hooks/weekly-limit-guard.py, which pauses all work at 98%.

Self-check: python3 statusline.py --selftest
"""
import json, os, re, sys, time
from datetime import datetime

def rgb(hex_color):
    return "\033[38;2;%d;%d;%dm" % tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


# Truecolor palette for a dark VS Code terminal (background about #1e1e1e). Text colours
# keep a contrast of 6:1 or more; TRACK and LINE are decoration only. The dim attribute
# and the basic ANSI blue are not used: both melt into a dark background.
R, B = "\033[0m", "\033[1m"
DIM = rgb("a6a6a6")      # secondary text
LINE = rgb("707070")     # separators
TRACK = rgb("3c3c3c")    # empty part of a bar: a solid dark track
TRACK_BG = "\033[48;2;60;60;60m"  # behind the partial cell, so the bar has no gap
RED, GREEN, YELLOW, ORANGE = rgb("ff7b7b"), rgb("5fd38d"), rgb("f2cc60"), rgb("ffab70")
BLUE, MAGENTA, CYAN = rgb("6cb6ff"), rgb("d2a8ff"), rgb("56d4dd")
SEP = f"  {LINE}│{R}  "
CACHE_DIR = os.environ.get("CLAUDE_STATUSLINE_CACHE") or os.path.expanduser("~/.cache/claude-statusline")  # env: tests only
# a task "running" longer than this is treated as lost (session crashed or was
# restarted, its notification never came); raise it if real agents run longer.
STALE_AFTER = 6 * 3600
MODEL_COLORS = {"Opus": MAGENTA, "Sonnet": CYAN, "Haiku": GREEN, "Fable": ORANGE}
EFFORT_COLORS = {"low": DIM, "medium": BLUE, "high": GREEN, "xhigh": YELLOW, "max": RED}
NOTIF = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>.*?<status>([^<]+)</status>", re.S)
LABELS = {
    "en": {"s": "s", "m": "m", "h": "h", "d": "d", "M": "M", "k": "k", "draft": "draft", "context": "context",
           "of": "of", "5h": "5h", "week": "week", "cache": "cache", "left": "left", "cold": "cold", "agents": "agents",
           "idle": "none running", "bg": "bg", "session": "session", "more": "and"},
    "ru": {"s": "с", "m": "м", "h": "ч", "d": "д", "M": "М", "k": "к", "draft": "черновик", "context": "контекст",
           "of": "из", "5h": "5ч", "week": "неделя", "cache": "кэш", "left": "ещё", "cold": "остыл", "agents": "агенты",
           "idle": "нет в работе", "bg": "фон", "session": "сессия", "more": "и ещё"},
}
L = LABELS["en"]
KEYS = (b'"Agent"', b'"Task"', b"run_in_background", b"agentId", b"task-notification", b'"is_error":true')


# ── formatting ──────────────────────────────────────────────────────────────
def bar(pct, width):
    """Cells of a bar: full blocks, the eighth-block remainder (0-7) and empty cells."""
    eighths = round(max(0, min(100, pct)) * width * 8 / 100)
    full, part = divmod(eighths, 8)
    return full, part, width - full - (1 if part else 0)


def level(pct):
    return RED if pct >= 85 else YELLOW if pct >= 60 else GREEN


def gauge(label, pct, width, tail=""):
    c = level(pct)
    full, part, empty = bar(pct, width)
    cells = f"{c}{'█' * full}" + (f"{TRACK_BG}{'▏▎▍▌▋▊▉'[part - 1]}" if part else "")
    return f"{label} {cells}{R}{TRACK}{'█' * empty}{R} {c}{B}{pct:.0f}%{R}{tail}"


def dur(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}{L['s']}"
    if secs < 3600:
        return f"{secs // 60}{L['m']}"
    if secs < 86400:
        h, m = divmod(secs // 60, 60)
        return f"{h}{L['h']}{m:02d}{L['m']}" if m else f"{h}{L['h']}"
    d, h = divmod(secs // 3600, 24)
    return f"{d}{L['d']}{h}{L['h']}" if h else f"{d}{L['d']}"


def tokens(n):
    return f"{n / 1e6:.1f}".rstrip("0").rstrip(".") + L["M"] if n >= 1e6 else f"{n // 1000}{L['k']}"


def model_name(s):
    s = (s or "").lower()
    return next((name for name in MODEL_COLORS if name.lower() in s), "")


def clip(text, n):
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ── transcript scan ─────────────────────────────────────────────────────────
def stamp(entry):
    try:
        return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


def finish(st, tool_use_id, status):
    task = st["agents"].get(tool_use_id) or st["bg"].get(tool_use_id)
    if task:
        task["status"] = status if status in ("completed", "killed") else "failed"


def apply(e, st):
    content = e.get("content") if e.get("type") == "queue-operation" else (e.get("message") or {}).get("content")
    if isinstance(content, str):
        for tid, status in NOTIF.findall(content):
            finish(st, tid, status)
        return
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "text" and e.get("type") == "user":
            for tid, status in NOTIF.findall(b.get("text") or ""):
                finish(st, tid, status)
        elif kind == "tool_use":
            inp = b.get("input") or {}
            if b.get("name") in ("Agent", "Task"):
                st["agents"][b["id"]] = {
                    "type": (inp.get("subagent_type") or "general").replace("general-purpose", "general"),
                    "model": model_name(inp.get("model")),
                    "desc": inp.get("description") or "",
                    "start": stamp(e),
                    "status": "running",
                }
            elif b.get("name") == "Bash" and inp.get("run_in_background") in (True, "true"):
                st["bg"][b["id"]] = {"start": stamp(e), "status": "running"}
        elif kind == "tool_result":
            tid = b.get("tool_use_id")
            agent, result = st["agents"].get(tid), e.get("toolUseResult")
            if agent and isinstance(result, dict):
                agent["model"] = model_name(result.get("resolvedModel")) or agent["model"]
                if not result.get("isAsync"):  # inline agent: the result is the finish
                    agent["status"] = "failed" if b.get("is_error") else "completed"
            elif b.get("is_error"):  # the launch itself failed
                finish(st, tid, "failed")


def scan(path, session_id):
    cache = os.path.join(CACHE_DIR, f"{session_id}.json")
    try:
        with open(cache) as f:
            st = json.load(f)
    except Exception:
        st = None
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"agents": {}, "bg": {}}
    if not st or st.get("path") != path or st.get("offset", 0) > size:
        st = {"path": path, "offset": 0, "agents": {}, "bg": {}}
    if size > st["offset"]:
        with open(path, "rb") as f:
            f.seek(st["offset"])
            for line in f:
                if not line.endswith(b"\n"):  # line still being written: take it next time
                    break
                st["offset"] += len(line)
                if any(k in line for k in KEYS):
                    try:
                        apply(json.loads(line), st)
                    except Exception:
                        pass
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache + ".tmp", "w") as f:
            json.dump(st, f)
        os.replace(cache + ".tmp", cache)
    return st


# ── lines ───────────────────────────────────────────────────────────────────
def line_identity(d):
    model = re.sub(r"\s*\((\S+) context\)", r" · \1", (d.get("model") or {}).get("display_name") or "Claude")
    ws = d.get("workspace") or {}
    cwd = ws.get("current_dir") or d.get("cwd") or ""
    root = ws.get("project_dir") or cwd
    parts = [f"{B}{model}{R}"]

    where = [f"{BLUE}{B}{os.path.basename(root) or cwd}{R}"] if root or cwd else []
    if d.get("session_name"):
        where.append(d["session_name"])
    if where:
        parts.append(f" {LINE}›{R} ".join(where))

    branch = ws.get("git_worktree") or (d.get("worktree") or {}).get("branch") or ""
    if not branch:
        try:
            head = open(os.path.join(root, ".git", "HEAD")).read().strip()
            branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref:") else head[:7]
        except OSError:
            pass
    if branch:
        parts.append(f"{DIM}⎇{R} {BLUE}{branch}{R}")

    pr = d.get("pr") or {}
    if pr.get("number"):
        sc, sym = {"approved": (GREEN, "✓"), "changes_requested": (RED, "✗"), "draft": (DIM, L["draft"]),
                   "pending": (YELLOW, "?")}.get(pr.get("review_state"), (BLUE, "open"))
        parts.append(f"PR #{pr['number']} {sc}{sym}{R}")

    effort = (d.get("effort") or {}).get("level")
    if effort:
        parts.append(f"{EFFORT_COLORS.get(effort, BLUE)}{B}{effort}{R}")
    if d.get("fast_mode"):
        parts.append(f"{YELLOW}{B}⚡ fast{R}")

    line = SEP.join(parts)
    vim = (d.get("vim") or {}).get("mode")
    return f"{GREEN if vim == 'INSERT' else BLUE}[{vim}]{R}  {line}" if vim else line


def line_resources(d, now):
    parts = []
    ctx = d.get("context_window") or {}
    if ctx.get("used_percentage") is not None:
        size = ctx.get("context_window_size") or 200_000
        used = ctx.get("total_input_tokens") or 0
        parts.append(gauge(L["context"], ctx["used_percentage"], 10, f" {DIM}{tokens(used)} {L['of']} {tokens(size)}{R}"))
    else:
        parts.append(f"{L['context']} {TRACK}{'█' * 10}{R} {DIM}—{R}")

    limits = d.get("rate_limits") or {}
    for key, label, width in (("five_hour", L["5h"], 8), ("seven_day", L["week"], 8)):
        lim = limits.get(key) or {}
        if lim.get("used_percentage") is not None:
            tail = f" {DIM}↺ {dur(lim['resets_at'] - now)}{R}" if lim.get("resets_at") else ""
            parts.append(gauge(label, lim["used_percentage"], width, tail))

    cache = d.get("prompt_cache") or {}
    if cache.get("warm") and cache.get("expires_at"):
        left = cache["expires_at"] - now
        parts.append(f"{L['cache']} {GREEN if left > 600 else YELLOW}●{R} {DIM}{L['left']} {dur(left)}{R}")
    elif cache.get("caching_observed"):
        parts.append(f"{L['cache']} {DIM}○ {L['cold']}{R}")
    return SEP.join(parts)


def line_work(d, st, now):
    session_model = model_name((d.get("model") or {}).get("id"))
    agents = list(st["agents"].values())
    running = sorted((a for a in agents if a["status"] == "running" and now - a["start"] < STALE_AFTER),
                     key=lambda a: a["start"])
    done = sum(a["status"] == "completed" for a in agents)
    failed = sum(a["status"] == "failed" for a in agents)
    killed = sum(a["status"] == "killed" for a in agents)
    bg = sum(t["status"] == "running" and now - t["start"] < STALE_AFTER for t in st["bg"].values())

    if running:
        models = {}
        for a in running:
            a["model"] = a["model"] or session_model
            models[a["model"]] = models.get(a["model"], 0) + 1
        mix = " ".join(f"{MODEL_COLORS.get(m, BLUE)}●{R}{n} {DIM}{m or '?'}{R}" for m, n in models.items())
        work = f"{B}{L['agents']}{R} {GREEN}{B}▶ {len(running)}{R}  {mix}"
    else:
        work = f"{L['agents']} {DIM}{L['idle']}{R}"
    tally = [f"{GREEN}✓ {done}{R}" if done else "", f"{RED}✗ {failed}{R}" if failed else "",
             f"{DIM}⏹ {killed}{R}" if killed else ""]
    if any(tally):
        work += "  " + " ".join(t for t in tally if t)
    parts = [work]
    if bg:
        parts.append(f"{L['bg']} {YELLOW}{B}▶ {bg}{R}")

    cost = d.get("cost") or {}
    session = [f"{DIM}{L['session']}{R} {dur((cost.get('total_duration_ms') or 0) / 1000)}"]
    if cost.get("total_cost_usd") is not None:
        session.append(f"{DIM}${cost['total_cost_usd']:.2f}{R}")
    added, removed = cost.get("total_lines_added") or 0, cost.get("total_lines_removed") or 0
    if added or removed:
        session.append(f"{GREEN}+{added}{R} {RED}−{removed}{R}")
    parts.append(f" {LINE}·{R} ".join(session))
    lines = [SEP.join(parts)]

    if running:  # one more line naming what is running, oldest first
        shown = []
        for a in running[:3]:
            who = f"{a['type']}·{a['model']}" if a["model"] else a["type"]
            shown.append(f"{MODEL_COLORS.get(a['model'], BLUE)}●{R} {who} {DIM}{dur(now - a['start'])}{R} "
                         f"{DIM}«{clip(a['desc'], 24)}»{R}")
        more = f"   {DIM}{L['more']} {len(running) - 3}{R}" if len(running) > 3 else ""
        lines.append(f"  {DIM}↳{R} " + "   ".join(shown) + more)
    return "\n".join(lines)


def save_limits(d, now):
    """Weekly usage for ~/.claude/hooks/weekly-limit-guard.py, which pauses all work at 98%."""
    week = (d.get("rate_limits") or {}).get("seven_day") or {}
    if week.get("used_percentage") is None:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = os.path.join(CACHE_DIR, "limits.json.%d" % os.getpid())
    with open(tmp, "w") as f:
        json.dump({"seven_day": week, "saved_at": now}, f)
    os.replace(tmp, os.path.join(CACHE_DIR, "limits.json"))


def render(d, now=None):
    now = now or time.time()
    try:
        save_limits(d, now)
    except Exception:
        pass
    st = {"agents": {}, "bg": {}}
    if d.get("transcript_path") and d.get("session_id"):
        try:
            st = scan(d["transcript_path"], d["session_id"])
        except Exception:
            pass
    return "\n".join((line_identity(d), line_resources(d, now), line_work(d, st, now)))


def selftest():
    import tempfile
    global CACHE_DIR, L
    tmp = tempfile.mkdtemp()
    CACHE_DIR = os.path.join(tmp, "cache")
    path = os.path.join(tmp, "t.jsonl")
    t0 = "2026-10-10T20:00:00Z"
    use = lambda i, name, inp: {"type": "assistant", "timestamp": t0, "message": {"content": [
        {"type": "tool_use", "id": i, "name": name, "input": inp}]}}
    res = lambda i, r, err=False: {"type": "user", "timestamp": t0, "toolUseResult": r, "message": {"content": [
        {"type": "tool_result", "tool_use_id": i, "is_error": err}]}}
    notif = lambda i, s: {"type": "queue-operation", "operation": "enqueue", "timestamp": t0,
                          "content": f"<task-notification><task-id>x</task-id><tool-use-id>{i}</tool-use-id><status>{s}</status></task-notification>"}
    rows = [
        use("a1", "Agent", {"subagent_type": "builder", "description": "Fix review findings"}),
        res("a1", {"isAsync": True, "agentId": "x1", "resolvedModel": "claude-opus-5-5"}),
        use("a2", "Agent", {"subagent_type": "worker", "description": "Deploy"}),
        res("a2", {"isAsync": True, "agentId": "x2", "resolvedModel": "claude-sonnet-5-5"}),
        use("a3", "Agent", {"description": "Inline look-up"}),
        res("a3", {"isAsync": False, "agentId": "x3"}),
        use("a4", "Agent", {"subagent_type": "reviewer", "description": "Review"}),
        res("a4", {"isAsync": True, "agentId": "x4"}),
        use("b1", "Bash", {"command": "npm test", "run_in_background": True}),
        notif("a2", "completed"),
        notif("a4", "failed"),
        use("a5", "Agent", {"subagent_type": "general-purpose", "description": "Stopped"}),
        res("a5", {"isAsync": True, "agentId": "x5"}),
        notif("a5", "killed"),
    ]
    with open(path, "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
        f.write('{"partial": ')  # unfinished line must wait
    st = scan(path, "s")
    status = {k: v["status"] for k, v in st["agents"].items()}
    assert status == {"a1": "running", "a2": "completed", "a3": "completed", "a4": "failed", "a5": "killed"}, status
    assert st["agents"]["a5"]["type"] == "general"
    assert st["agents"]["a1"]["model"] == "Opus" and st["bg"]["b1"]["status"] == "running"
    with open(path, "a") as f:  # incremental: only the tail is read
        f.write('"x"}\n' + json.dumps(notif("a1", "completed")) + "\n")
    assert scan(path, "s")["agents"]["a1"]["status"] == "completed"
    assert bar(0, 10) == (0, 0, 10) and bar(100, 8) == (8, 0, 0) and bar(9, 10) == (0, 7, 9)
    for lang, mega, kilo, hours, of, bg, idle in (("en", "1M", "109k", "2h18m", "of", "bg", "none running"),
                                                  ("ru", "1М", "109к", "2ч18м", "из", "фон", "нет в работе")):
        L = LABELS[lang]
        assert tokens(1_000_000) == mega and tokens(109_313) == kilo and dur(8280) == hours
        now = datetime.fromisoformat(t0.replace("Z", "+00:00")).timestamp() + 240
        out = render({"session_id": "s", "transcript_path": path, "model": {"display_name": "Opus 5.5 (1M context)"},
                      "context_window": {"used_percentage": 11, "total_input_tokens": 109_313, "context_window_size": 1_000_000}},
                     now)
        plain = re.sub(r"\033\[[0-9;]*m", "", out)
        assert "Opus 5.5 · 1M" in plain and f"{kilo} {of} {mega}" in plain and "✓ 3" in plain and "✗ 1" in plain and "⏹ 1" in plain, plain
        assert f"{bg} ▶ 1" in plain and idle in plain, plain
    render({"rate_limits": {"seven_day": {"used_percentage": 98.5, "resets_at": now + 60}}}, now)
    with open(os.path.join(CACHE_DIR, "limits.json")) as f:
        assert json.load(f)["seven_day"]["used_percentage"] == 98.5
    print(out)
    print("selftest ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--lang" in args:
        i = args.index("--lang")
        L = LABELS.get(args[i + 1] if i + 1 < len(args) else "en", LABELS["en"])
        del args[i:i + 2]
    if args == ["--selftest"]:
        selftest()
    else:
        try:
            data = json.load(sys.stdin)
        except Exception:
            sys.exit(0)
        print(render(data))
