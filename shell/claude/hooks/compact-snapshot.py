#!/usr/bin/env python3
"""Compaction memory, kept small on purpose: re-reading after a compact must not refill the context.

pre  (PreCompact hook):  read the transcript, write ~/.claude/compact-snapshots/<session>.md
                         with what a summary tends to lose: the owner's last messages verbatim,
                         background agents still running, the state files touched. Never blocks.
post (SessionStart compact hook): inject that snapshot (about 1-2k tokens) as additionalContext.
"""
import json, os, re, sys, time

SNAP_DIR = os.environ.get("COMPACT_SNAP_DIR") or os.path.expanduser("~/.claude/compact-snapshots")  # env: tests only
TASK_RE = re.compile(r"<task-id>(\w+)</task-id>.*?<status>(\w+)</status>")
STATE_RE = re.compile(r"[\w/.~-]*STATE[\w-]*\.md")
MIDTURN_RE = re.compile(r"The user sent a new message while you were working:\\n(.*?)\\n\\nThis is how Claude Code surfaces")


def owner_text(d):
    """Return the owner's own typed text from a transcript entry, else None."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary"):
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
    if not isinstance(c, str):
        return None
    c = c.strip()
    return c if c and not c.startswith(("<", "[Request interrupted")) and not re.fullmatch(r"/\S+", c) else None


def scan(path):
    launched, status, owner, states = {}, {}, [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            for tid, st in TASK_RE.findall(line):
                status[tid] = st
            for m in MIDTURN_RE.findall(line):
                owner.append(json.loads('"' + m + '"').lstrip("- "))
            try:
                d = json.loads(line)
            except ValueError:
                continue
            r = d.get("toolUseResult")
            if isinstance(r, dict) and r.get("status") == "async_launched" and r.get("agentId"):
                launched[r["agentId"]] = r.get("description", "")
                status.pop(r["agentId"], None)
            t = owner_text(d)
            if t:
                owner.append(t)
            for b in (d.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Edit", "Write"):
                    p = (b.get("input") or {}).get("file_path", "")
                    if "STATE" in os.path.basename(p):
                        states.append(p)
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                    states += STATE_RE.findall((b.get("input") or {}).get("command", ""))
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SendMessage":
                    # a resumed agent counts as running again; ids by name are not resolved
                    status.pop((b.get("input") or {}).get("to", ""), None)
    running = [(a, desc) for a, desc in launched.items() if a not in status]
    uniq_states = list(dict.fromkeys(reversed(states)))[:3]
    return running, owner[-3:], uniq_states


def pre(inp):
    tp = inp.get("transcript_path", "")
    running, owner, states = scan(tp) if os.path.exists(tp) else ([], [], [])
    lines = [f"# Pre-compact snapshot {time.strftime('%Y-%m-%d %H:%M')} ({inp.get('trigger', '?')})",
             f"Full transcript before the compact (grep it, never read whole): {tp}", "",
             "## Owner's last messages (verbatim, cut at 700 chars)"]
    lines += [f"- {m[:700]}" for m in owner] or ["- none"]
    lines += ["", "## Background agents still running (do not respawn; wait for their notifications)"]
    lines += [f"- {a}: {desc}" for a, desc in running] or ["- none"]
    lines += ["", "## State files edited this session (read only the section the next step needs)"]
    lines += [f"- {p}" for p in states] or ["- none"]
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(os.path.join(SNAP_DIR, f"{inp.get('session_id', 'unknown')}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def post(inp):
    p = os.path.join(SNAP_DIR, f"{inp.get('session_id', 'unknown')}.md")
    if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 900:  # a failed pre() leaves the last compact's snapshot
        return
    ctx = ("If you are a subagent (an orchestrator started you with a brief), ignore this whole block and continue your brief.\n\nA compaction just happened. The summary above is the main record. Below is a small mechanical "
           "snapshot. Do NOT re-read state files or the transcript whole: continue the last task, and read only "
           "the exact section or grep hit the next step needs.\n\n" + open(p, encoding="utf-8").read())
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    # A subagent's compact can send the parent's session_id and transcript_path and no marker, so post() text
    # starts with a line telling subagents to ignore it. The marker check stays for harness versions that send one.
    if isinstance(data, dict) and (data.get("agent_id") or data.get("agent_type") or "/subagents/" in str(data.get("transcript_path", ""))
                                   or os.environ.get("CLAUDE_AGENT_ID")):
        sys.exit(0)
    try:
        {"pre": pre, "post": post}[mode](data)
    except Exception as e:  # a hook failure must never block or break the compact
        print(f"compact-snapshot {mode}: {e}", file=sys.stderr)
    sys.exit(0)
