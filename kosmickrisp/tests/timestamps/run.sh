#!/bin/bash
# Builds and runs the timestamp query test against a KosmicKrisp build (patch 0004).
#   run.sh <kosmickrisp icd json> [build dir]        (needs Homebrew vulkan-loader, vulkan-headers)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-ts-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
cc -O1 -Wall -I"$BREW/include" "$HERE/ts_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/ts_test"
VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" gtimeout -s KILL 60 "$OUT/ts_test"
