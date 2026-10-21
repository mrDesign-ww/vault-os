#!/usr/bin/env python3
"""PreToolUse guard for the Bash tool.

Blocks two command shapes that break unattended agent work:
  1. killing processes by name or pattern: pkill, killall, kill fed by pgrep
     (BSD `pkill -f "x" -P 1` once killed every process with "x" in its line);
  2. rm with a shell glob (a background agent hangs or deletes more than it meant).
Everything else passes. Exit 2 + stderr = the call is refused and the message goes to the model.
Any internal error passes the command through (exit 0): the guard must never brick a session.

ceiling: sees only the command text of the Bash tool. A script file that calls pkill
inside is not seen; upgrade path = scan the script on `bash file.sh`.
Self-check: guard-dangerous-bash.py --selftest
"""
import json
import re
import shlex
import sys

WRAPPERS = {"sudo", "exec", "nohup", "command", "time", "env", "xargs", "nice", "taskpolicy", "caffeinate", "timeout"}
OPERATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
SHELLS = {"bash", "sh", "zsh"}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
GLOB = re.compile(r"[*?\[]")

MSG_KILL = ("Blocked by the guard hook: killing processes by name or pattern (pkill, killall, kill + pgrep) is not allowed. "
            "Stop only your own process: kill <pid> with the PID from your own pidfile.")
MSG_RM = ("Blocked by the guard hook: rm with a glob is not allowed. "
          "Name the files explicitly, or create a new folder instead of cleaning one; replaced files are moved, not deleted.")


def segments(command):
    """Split a command line into simple commands: lists of words between shell operators."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    seg = []
    for tok in lex:
        if tok in OPERATORS or set(tok) <= set(";&|()"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def verdict(command, depth=0):
    """Return a refusal message, or None when the command may run."""
    text = HEREDOC.sub("\n", command)
    text = text.replace("$(", " ( ").replace("`", " ; ")
    for line in text.split("\n"):
        try:
            segs = list(segments(line))
        except ValueError:
            # Unbalanced quotes (a multi-line string): fall back to a plain look at command positions.
            if re.search(r"(^|[;&|(]\s*)(sudo\s+)?(pkill|killall)\b", line):
                return MSG_KILL
            continue
        for seg in segs:
            words = list(seg)
            # Drop leading VAR=value assignments and wrappers such as sudo or xargs.
            while words and (re.match(r"^\w+=", words[0]) or words[0] in WRAPPERS or (words[0].startswith("-") and len(words) > 1)):
                words.pop(0)
            if not words:
                continue
            cmd = words[0].rsplit("/", 1)[-1]
            args = words[1:]
            if cmd in ("pkill", "killall"):
                return MSG_KILL
            if cmd == "kill" and "pgrep" in line:
                return MSG_KILL
            if cmd == "rm" and any(GLOB.search(a) for a in args if not a.startswith("-")):
                return MSG_RM
            if cmd in SHELLS and "-c" in args and depth < 3:
                inner = args[args.index("-c") + 1:args.index("-c") + 2]
                if inner:
                    found = verdict(inner[0], depth + 1)
                    if found:
                        return found
    return None


def selftest():
    blocked = [
        "pkill -f vite", "killall node", "sudo pkill -9 chrome", "cd /tmp && pkill -f 'node server'",
        "kill $(pgrep -f vite)", "pgrep -f soak | xargs kill", "bash -c \"pkill foo\"", "/usr/bin/pkill x",
        "rm -f dir/*", "cd x && rm -rf build/*.o", "rm -rf \"$DIR\"/*", "rm tmp/file?.txt", "FOO=1 rm -f out/[ab].log",
        "echo start; rm -rf ./cache/*; echo done",
    ]
    allowed = [
        "kill 12345", "kill -TERM \"$(cat server.pid)\"", "rm -f file.txt", "rm -rf \"$S3/dir\"", "rm -f a.txt b.txt",
        "grep -n \"pkill\" notes.txt", "echo \"rm *\"", "find . -name \"*.tmp\" -print", "ls *.txt",
        "cat > f.txt <<'EOF'\nnever pkill\nrm -f dir/*\nEOF", "python3 - <<'PY'\nprint('killall')\nPY\necho ok",
        "git rm --cached file.txt", "echo 'unbalanced", "mv old new && mkdir -p out",
    ]
    bad = [c for c in blocked if verdict(c) is None] + [c for c in allowed if verdict(c) is not None]
    for c in bad:
        print("WRONG:", repr(c))
    print("selftest: %d blocked, %d allowed, %d wrong" % (len(blocked), len(allowed), len(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return 0
        message = verdict(str((data.get("tool_input") or {}).get("command") or ""))
    except Exception:
        return 0
    if message:
        sys.stderr.write(message + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
