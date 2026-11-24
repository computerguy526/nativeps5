#!/bin/bash
# GPU-free tests of the prepfast pieces (patches 0041-0044): stamp / library-key text, pre-fault helper, exported flush symbol.
#   run.sh <mesa source dir (has src/kosmickrisp/bridge/kk_libkey.h)> [libvulkan_kosmickrisp.dylib]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$1"; LIB="${2:-}"
OUT="${TMPDIR:-/tmp}/kk-prepfast-test"; mkdir -p "$OUT"
cc -O1 -Wall -Werror -I"$SRC/src/kosmickrisp/bridge" "$HERE/prepfast_test.c" -o "$OUT/prepfast_test"
"$OUT/prepfast_test"
if [ -n "$LIB" ]; then
  nm -gU "$LIB" | grep -q ' T _nps5_kk_archive_flush$' && echo "ok: nps5_kk_archive_flush exported" || { echo "FAIL: nps5_kk_archive_flush not exported by $LIB"; exit 1; }
fi
