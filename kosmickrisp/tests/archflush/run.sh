#!/bin/bash
# archflush tests. GPU-free part always; T1 only with PROBE=1 (needs a GPU, ~seconds; never with a game window open).
#   run.sh <mesa source dir> [libvulkan_kosmickrisp.dylib]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$1"; LIB="${2:-}"
OUT="${TMPDIR:-/tmp}/kk-archflush-test"; mkdir -p "$OUT"
bash "$HERE/lint.sh" "$SRC" $LIB
cc -O1 -Wall -Werror -pthread "$HERE/gate_stress.c" -o "$OUT/gate_stress" && "$OUT/gate_stress"
if [ "${PROBE:-0}" = 1 ]; then
  clang -O1 -Wall -fno-objc-arc -framework Metal -framework Foundation "$HERE/archflush_probe.m" -o "$OUT/archflush_probe"
  "$OUT/archflush_probe" "${PROBE_ROUNDS:-20}"
fi
