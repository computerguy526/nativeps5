#!/bin/bash
# Robust vector SSBO load test (KosmicKrisp patch 0005).  run.sh <kosmickrisp icd json> [out dir]
# Needs Homebrew glslang, vulkan-loader, vulkan-headers.  Killed after 60 s.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-vecload-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
glslangValidator --quiet -V --target-env vulkan1.3 "$HERE/vecload.comp" -o "$OUT/vecload.spv"
cc -O1 -Wall -I"$BREW/include" "$HERE/vecload_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/vecload_test"
VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" ${KK_MSL_COLLECT_DIR:+KK_MSL_COLLECT_DIR=$KK_MSL_COLLECT_DIR} gtimeout -s KILL 60 "$OUT/vecload_test" "$OUT/vecload.spv"
