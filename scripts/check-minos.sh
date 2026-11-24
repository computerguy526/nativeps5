#!/bin/bash
# check-minos.sh [--max 26.0] <mach-o file>...   Fails (exit 1) when a built binary's minimum macOS (LC_BUILD_VERSION minos) is
# newer than the oldest macOS we support. A library built on a newer macOS than the one it runs on compiles
# `@available(macOS 27, *)` checks away and calls APIs that do not exist there (it crashed every macOS 26 start).
set -euo pipefail
MAX=26.0
[ "${1:-}" = --max ] && { MAX="$2"; shift 2; }
[ $# -ge 1 ] || { echo "usage: check-minos.sh [--max 26.0] <mach-o>..." >&2; exit 2; }
newer() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" != "$2" ] && [ "$1" != "$2" ]; }
bad=0
for f in "$@"; do
  minos=$(otool -l "$f" 2>/dev/null | awk '/LC_BUILD_VERSION/{b=1} b&&$1=="minos"{print $2; exit}')
  [ -n "$minos" ] || { echo "check-minos: $f: no LC_BUILD_VERSION" >&2; bad=1; continue; }
  if newer "$minos" "$MAX"; then echo "check-minos: $f: minos $minos is newer than $MAX (set MACOSX_DEPLOYMENT_TARGET=$MAX and rebuild)" >&2; bad=1
  else echo "check-minos: $f: minos $minos ok (<= $MAX)"; fi
done
exit $bad
