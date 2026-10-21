#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse hook: pause all work at 98% of the weekly usage limit, so the last 2% records where work stopped.

The status line (`~/.claude/statusline/statusline.py`) saves the account's weekly usage to LIMITS. At or above
THRESHOLD and before the reset, every tool is blocked except those needed to record where work stopped and to
resume. Override for one limit window, only on the owner's word: write the resets_at number from LIMITS into OVERRIDE.
Never blocks on an error or a missing file; a reset time in the past releases the pause.
Selftest: weekly-limit-guard.py --selftest
"""
import json, os, re, sys, time

LIMITS = os.path.expanduser("~/.cache/claude-statusline/limits.json")
OVERRIDE = os.path.expanduser("~/.cache/claude-statusline/limit-override")
THRESHOLD = 98
ALLOWED = {"Read", "Write", "Edit", "Glob", "Grep", "ToolSearch", "CronCreate", "CronList", "CronDelete", "PushNotification",
           "TaskStop", "AskUserQuestion"}
BASH_ALLOWED = re.compile(r"^\s*date\b[^;&|`$<>\n]*$")


def load(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def verdict(data, limits_text, override_text, now):
    """The block message, or "" to allow."""
    week = (json.loads(limits_text) if limits_text else {}).get("seven_day") or {}
    pct, reset = week.get("used_percentage"), week.get("resets_at")
    if pct is None or pct < THRESHOLD or not reset or reset <= now:
        return ""
    if override_text.strip() == str(reset):
        return ""
    tool = data.get("tool_name") or ""
    if tool in ALLOWED:
        return ""
    if tool == "Bash" and BASH_ALLOWED.match(str((data.get("tool_input") or {}).get("command") or "")):
        return ""
    when = time.strftime("%d.%m %H:%M", time.localtime(reset))
    when_resume = time.strftime("%d.%m %H:%M", time.localtime(reset + 600))  # 10 min after the reset
    return (
        f"Weekly limit at {pct:.0f}% (rule: pause at {THRESHOLD}%). Blocked: {tool}. Pause now, start no new work "
        f"or agents. Record where you stopped. A subagent writes a handoff (done, in progress, the exact next step) "
        f"into its result file and ends. The main session writes the project's STATE.md AUTO-RESUME block (running "
        f"agents with ids, last step, next step), deletes every recurring pulse (CronList, CronDelete: at {THRESHOLD}% "
        f"each fire is a paid turn), creates ONE one-shot pulse (recurring false) for {when_resume} with the auto-resume "
        f"prompt, sends PushNotification \"Weekly limit {THRESHOLD}%: work paused until {when}\" and ends the turn. "
        f"The pause lifts at the reset, {when}. Allowed now: Read, Write, Edit, Glob, Grep, CronCreate, CronList, "
        f"CronDelete, PushNotification, TaskStop, Bash for date (without ; | & or redirects). Override only on the "
        f"owner's word: write {reset} into {OVERRIDE}."
    )


def selftest():
    now = 1_000_000
    lim = lambda pct, reset=now + 3600: json.dumps({"seven_day": {"used_percentage": pct, "resets_at": reset}})
    call = lambda tool, cmd="": {"tool_name": tool, "tool_input": {"command": cmd}}
    cases = [
        (call("Agent"), lim(98.4), "", True),
        (call("Bash", "npm test"), lim(99), "", True),
        (call("mcp__figma__use_figma"), lim(100), "", True),
        (call("Agent"), lim(97.9), "", False),
        (call("Agent"), lim(99, now - 1), "", False),          # reset passed
        (call("Agent"), lim(99), str(now + 3600), False),      # owner override for this window
        (call("Agent"), lim(99), str(now - 5), True),          # override of an old window
        (call("Write"), lim(99), "", False),
        (call("CronDelete"), lim(99), "", False),
        (call("Bash", "date '+%H:%M'"), lim(99), "", False),
        (call("Bash", "rm -rf x; date"), lim(99), "", True),
        (call("Bash", "date; rm -rf x"), lim(99), "", True),
        (call("Bash", "date > /tmp/x"), lim(99), "", True),
        (call("Agent"), "", "", False),                         # no file yet
        (call("Agent"), json.dumps({"seven_day": {}}), "", False),
    ]
    bad = [c for c in cases if bool(verdict(c[0], c[1], c[2], now)) != c[3]]
    for c in bad:
        print("WRONG:", c)
    print("selftest: %d cases, %d wrong" % (len(cases), len(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    try:
        data = json.load(sys.stdin)
        message = verdict(data, load(LIMITS), load(OVERRIDE), time.time())
    except Exception:
        return 0
    if message:
        sys.stderr.write(message + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
