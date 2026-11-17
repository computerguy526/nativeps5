#!/bin/bash
# Builds and runs the 64-bit buffer word test against a KosmicKrisp build.
#   run.sh <kosmickrisp icd json> [build dir] [test arguments]   (needs Homebrew glslang, vulkan-loader, vulkan-headers)
# Always: plain aligned 64-bit loads/stores must not tear. With kk_int64_atomics=true in the environment
# (shaderBufferInt64Atomics exposed): every 64-bit atomic against its CPU reference under contention.
# Exit 0 = all pass. The whole run is killed after 60 s: a hung GPU kernel takes the window server down.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-atomics-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
for op in 0 1 2 3 4 5 6 7 8 9 10 11 12 13; do
  glslangValidator --quiet -V --target-env vulkan1.3 -DOP=$op "$HERE/atomics.comp" -o "$OUT/op$op.spv"
  spirv-val --target-env vulkan1.3 "$OUT/op$op.spv"
  glslangValidator --quiet -V --target-env vulkan1.3 -DOP=$op -DBDA "$HERE/atomics.comp" -o "$OUT/op$op.bda.spv"
  spirv-val --target-env vulkan1.3 "$OUT/op$op.bda.spv"
done
cc -O1 -Wall -I"$BREW/include" "$HERE/atomics_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/atomics_test"
TIMEOUT="$(command -v gtimeout || command -v timeout || true)"
VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" ${TIMEOUT:+$TIMEOUT -s KILL 60} "$OUT/atomics_test" "$OUT" "${@:3}"
