#!/bin/bash
# Builds and runs the 3D-image-through-2D-array-views test against a KosmicKrisp build.
#   run.sh <kosmickrisp icd json> [build dir] [slices]       (needs Homebrew vulkan-loader, vulkan-headers)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-vol3d-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
cc -O1 -Wall -I"$BREW/include" "$HERE/vol3d_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/vol3d_test"
VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" gtimeout -s KILL 60 "$OUT/vol3d_test" ${3:-}
