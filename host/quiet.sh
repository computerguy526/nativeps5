#!/bin/bash
# quiet.sh start|end — perf measurement window WITHOUT freezing anything.
# SIGSTOP is banned: a frozen Metal/windowed process stalled WindowServer and hung the Mac (2026-11-14 21:47).
# start: every other nativeps5 build/run gets nice 20 + background QoS (E-cores, throttled I/O).
# end:   restores nice 0 and default QoS.  Skip patterns: QUIET_SKIP="pat1|pat2" (substring match).
set -u
ROOT=${NPS5_ROOT:?set NPS5_ROOT (see BUILDING.md)}
STATE=$ROOT/work/guard/quiet.pids
mkdir -p "$(dirname "$STATE")"
if [ "${1:-}" = end ]; then
  [ -f "$STATE" ] && while read -r p; do renice 0 -p "$p" >/dev/null 2>&1; taskpolicy -B -p "$p" >/dev/null 2>&1; done < "$STATE"
  rm -f "$STATE"; echo "window ended: priorities restored"; exit 0
fi
: > "$STATE"
IFS='|' read -r -a SKIP <<< "${QUIET_SKIP:-}"
for p in $(pgrep -u "$(id -u)" -f "$ROOT|ps4-recomp|clang|ninja|ld64|go-build|go test|go build|nps5"); do
  cmd=$(ps -o command= -p "$p" 2>/dev/null) || continue
  case "$cmd" in *claude*|*/zsh*|*/bash*) continue;; esac
  s=0; for k in "${SKIP[@]+"${SKIP[@]}"}"; do [ -n "$k" ] && case "$cmd" in *"$k"*) s=1;; esac; done
  [ $s = 1 ] && continue
  renice 20 -p "$p" >/dev/null 2>&1 && taskpolicy -b -p "$p" >/dev/null 2>&1; echo "$p" >> "$STATE"
done
echo "window started: deprioritised $(wc -l < "$STATE" | tr -d ' ') processes (nothing frozen)"
