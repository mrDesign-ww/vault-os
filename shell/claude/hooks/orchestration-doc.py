#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart hook: inject ~/.claude/docs/orchestration.md into main sessions only.

Subagents skip it (same markers as compact-snapshot.py). Never blocks: any error prints nothing.
Selftest: orchestration-doc.py --selftest
"""
import io, json, os, sys, tempfile

DOC = os.path.expanduser("~/.claude/docs/orchestration.md")
SUB = "If you are a subagent (an orchestrator started you with a brief), ignore this whole block and continue your brief."


def is_subagent(data):
    return isinstance(data, dict) and bool(
        data.get("agent_id") or data.get("agent_type") or "/subagents/" in str(data.get("transcript_path", "")))


def output(data):
    if is_subagent(data) or not os.path.isfile(DOC):
        return ""
    # A subagent's compact can carry no marker, so the text itself tells subagents to skip it.
    text = SUB + "\n\n" + io.open(DOC, encoding="utf-8").read()
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}, ensure_ascii=False)


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        fd, DOC = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write("# Orchestration rules\n")
        assert output({"agent_id": "a1"}) == ""
        assert output({"agent_type": "builder"}) == ""
        assert output({"transcript_path": "/x/subagents/agent-1.jsonl"}) == ""
        o = json.loads(output({"source": "startup", "transcript_path": "/x/s.jsonl"}))
        assert "Orchestration rules" in o["hookSpecificOutput"]["additionalContext"]
        os.remove(DOC)
        assert output({"source": "startup"}) == ""  # no doc installed: silent
        print("selftest ok")
        sys.exit(0)
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    try:
        out = output(data)
        if out:
            print(out)
    except Exception:
        pass
