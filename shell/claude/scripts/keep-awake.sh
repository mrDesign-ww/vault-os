#!/bin/bash
# Keep the Mac awake while a Claude Code or Codex session is working, and let it sleep when it is idle.
#
#   keep-awake.sh start   (UserPromptSubmit hook; reads the hook JSON on stdin)
#   keep-awake.sh self-test
#
# One watcher per session holds `caffeinate -ims -w <watcher pid>`. The session counts as busy while
# its transcript or its subagent transcripts were written in the last IDLE seconds, or while a Bash
# tool command of that Claude process is running. When neither is true, or Claude exits, the watcher
# exits and caffeinate ends with it. A closed lid still sleeps the Mac on battery.
set -u
STATE="${KEEP_AWAKE_STATE:-$HOME/.claude/keep-awake}"
IDLE="${KEEP_AWAKE_IDLE:-300}"      # seconds without activity before sleep is allowed again
POLL="${KEEP_AWAKE_POLL:-30}"

claude_pid() {
  local p=$PPID comm
  while [ "${p:-1}" -gt 1 ]; do
    comm=$(ps -o comm= -p "$p" 2>/dev/null)
    case "$comm" in *claude*|*codex*) echo "$p"; return 0 ;; esac
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  done
  return 1
}

busy() {  # $1 claude pid, $2 transcript file, $3 session dir, $4 prompt marker
  local since
  since=$(date -v-"${IDLE}"S "+%Y-%m-%d %H:%M:%S")
  [ -n "$(find "$2" "$3" "$4" -type f -newermt "$since" 2>/dev/null | head -1)" ] && return 0
  pgrep -P "$1" -f 'shell-snapshots|keep-awake-test-bash' >/dev/null 2>&1 && return 0
  return 1
}

watch() {  # $1 claude pid, $2 transcript file, $3 session dir, $4 prompt marker
  caffeinate -ims -w $$ &
  while kill -0 "$1" 2>/dev/null && busy "$1" "$2" "$3" "$4"; do
    sleep "$POLL"
  done
}

start() {
  local input session transcript cpid dir pidfile
  input=$(cat)
  session=$(printf '%s' "$input" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
  transcript=$(printf '%s' "$input" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)
  [ -n "$session" ] && [ -n "$transcript" ] || exit 0
  cpid=${KEEP_AWAKE_PID:-$(claude_pid)} || exit 0
  [ -n "$cpid" ] || exit 0
  mkdir -p "$STATE"
  pidfile="$STATE/$session.pid"
  touch "$STATE/$session.prompt"   # a new prompt is activity even before it reaches the transcript
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    exit 0  # this session already has a live watcher; new activity keeps it going
  fi
  dir="${transcript%.jsonl}"
  nohup "$0" _watch "$cpid" "$transcript" "$dir" "$pidfile" "$STATE/$session.prompt" >/dev/null 2>&1 &
  exit 0
}

case "${1:-}" in
  start) start ;;
  _watch)
    echo $$ > "$5"
    watch "$2" "$3" "$4" "$6"
    rm -f "$5" "$6"
    ;;
  self-test)
    set -e
    T=$(mktemp -d)
    sleep 120 >/dev/null 2>&1 & fake=$!
    trap 'kill "$fake" 2>/dev/null; rm -rf "$T"' EXIT
    : > "$T/t.jsonl"
    export KEEP_AWAKE_STATE="$T/state" KEEP_AWAKE_IDLE=3 KEEP_AWAKE_POLL=1 KEEP_AWAKE_PID=$fake
    printf '{"session_id":"s1","transcript_path":"%s"}' "$T/t.jsonl" | "$0" start
    sleep 1
    w=$(cat "$T/state/s1.pid"); kill -0 "$w"
    pgrep -f "caffeinate -ims -w $w" >/dev/null || { echo "FAIL: no caffeinate while busy"; exit 1; }
    printf '{"session_id":"s1","transcript_path":"%s"}' "$T/t.jsonl" | "$0" start
    [ "$(pgrep -f "_watch $fake" | wc -l | tr -d ' ')" = "1" ] || { echo "FAIL: second watcher for one session"; exit 1; }
    for i in 1 2 3 4; do touch "$T/t.jsonl"; sleep 1; done
    kill -0 "$w" || { echo "FAIL: watcher quit while the transcript was active"; exit 1; }
    sleep 6
    if kill -0 "$w" 2>/dev/null; then echo "FAIL: watcher still alive after idle"; exit 1; fi
    pgrep -f "caffeinate -ims -w $w" >/dev/null && { echo "FAIL: caffeinate survived idle"; exit 1; }
    printf '{"session_id":"s2","transcript_path":"%s"}' "$T/t.jsonl" | "$0" start
    sleep 1; w2=$(cat "$T/state/s2.pid") || { echo "FAIL: prompt after idle got no watcher"; exit 1; }
    pgrep -f "caffeinate -ims -w $w2" >/dev/null || { echo "FAIL: prompt after idle got no caffeinate"; exit 1; }
    kill "$fake"; sleep 3
    if kill -0 "$w2" 2>/dev/null; then echo "FAIL: watcher outlived claude"; exit 1; fi
    echo "keep-awake self-test: PASS"
    ;;
  *) echo "usage: keep-awake.sh start|self-test" >&2; exit 2 ;;
esac
