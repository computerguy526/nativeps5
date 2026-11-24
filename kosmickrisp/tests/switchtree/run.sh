#!/bin/bash
# NPS5_KK_SWITCH_TREE test: a 24-leaf if (x < K) compute kernel gives the CPU-reference results with the switch off and on.
#   run.sh <kosmickrisp icd json> [build dir]   (needs Homebrew glslang, vulkan-loader, vulkan-headers; run it through
#   tools-host/gpu_run.sh, never during a title run). Exit 0 = both runs equal the reference. Killed after 60 s each.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-switchtree-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
glslangValidator --quiet -V --target-env vulkan1.3 "$HERE/switchtree.comp" -o "$OUT/switchtree.spv"
spirv-val --target-env vulkan1.3 "$OUT/switchtree.spv"
cc -O1 -Wall -I"$BREW/include" "$HERE/switchtree_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/switchtree_test"
TIMEOUT="$(command -v gtimeout || command -v timeout || true)"
for sw in 0 1; do
  echo "NPS5_KK_SWITCH_TREE=$sw"
  NPS5_KK_SWITCH_TREE=$sw VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" ${TIMEOUT:+$TIMEOUT -s KILL 60} "$OUT/switchtree_test" "$OUT/switchtree.spv"
done
