#!/bin/bash
# Builds and runs the layered-draw-as-a-later-render-pass test against a KosmicKrisp build.
#   run_layered.sh <kosmickrisp icd json> [build dir] [case filter]   (needs Homebrew glslang, vulkan-loader, vulkan-headers)
# Exit 0 = every case wrote every layer. The run is killed after 100 s.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-layered-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
for f in layer.mesh layerpc.mesh layerbig.mesh flat.mesh layer.vert flat.vert flatvb.vert layer.frag varying.frag; do
  glslangValidator --quiet -V --target-env vulkan1.3 "$HERE/$f" -o "$OUT/$f.spv"
done
cc -O1 -Wall -I"$BREW/include" "$HERE/layered_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/layered_test"
VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" gtimeout -s KILL 100 "$OUT/layered_test" "$OUT" ${3:-}
