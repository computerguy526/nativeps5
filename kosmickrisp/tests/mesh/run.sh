#!/bin/bash
# Builds and runs the VK_EXT_mesh_shader pixel test against a KosmicKrisp build.
#   run.sh <kosmickrisp icd json> [build dir]         (needs Homebrew glslang, vulkan-loader, vulkan-headers)
# Exit 0 = every pixel check passed. PPM dumps of the three targets land in the build dir.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-mesh-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
# glslang's built-in limit is 128 mesh invocations per workgroup; the AGC recompiler needs 256.
glslangValidator -c | sed -e 's/^MaxMeshWorkGroupSizeX_EXT .*/MaxMeshWorkGroupSizeX_EXT 256/' > "$OUT/limits.conf"
for f in tri.mesh grid.mesh big.mesh many.mesh huge.mesh common.frag big.frag many.frag plain.vert plain.frag; do
  glslangValidator --quiet -V --target-env vulkan1.3 "$HERE/$f" "$OUT/limits.conf" -o "$OUT/$f.spv"
  spirv-val --target-env vulkan1.3 "$OUT/$f.spv"
done
cc -O1 -Wall -I"$BREW/include" "$HERE/mesh_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/mesh_test"
VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" "$OUT/mesh_test" "$OUT" "$OUT/"
