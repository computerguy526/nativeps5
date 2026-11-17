#!/bin/bash
# Alias of `nps5 run` (scripts/nps5lib/run.py), kept for the old call:
#   scripts/run-title.sh <title id> <seconds> ["EXTRA=env WORDS=..."]   ==   scripts/nps5 run <id> --secs <seconds> --env EXTRA=env ...
# The safety rules (one title run at a time through the queue, memory limit, no GPU profiling, run length cap and
# NPS5_RUN_REASON, scrubbed environment) live in nps5 run now. Needs games/<id>/ and titles/<id>.env (titles/README.md).
set -euo pipefail
[ $# -ge 2 ] || { sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
ID="$1"; SECS="$2"; EXTRA="${3:-}"
case "$SECS" in ''|*[!0-9]*) echo "run-title: seconds must be a number" >&2; exit 2;; esac
args=(); for w in $EXTRA; do args+=(--env "$w"); done
exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5" run "$ID" --secs "$SECS" ${args[@]+"${args[@]}"}
