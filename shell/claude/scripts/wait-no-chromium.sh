#!/bin/bash
# Wait until no headless Chromium timing gate (or ms-playwright-hold process) runs on this Mac. Never kills anything.
# Usage: wait-no-chromium.sh [max_seconds=7200]. Exit 0 = clear, 1 = timed out.
# The bracket in the pattern keeps pgrep from matching any waiter whose own command line quotes it.
max=${1:-7200}; t0=$(date +%s)
while pgrep -f '[m]s-playwright' >/dev/null; do
  [ $(( $(date +%s) - t0 )) -ge "$max" ] && exit 1
  sleep 60
done
exit 0
