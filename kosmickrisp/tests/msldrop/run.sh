#!/bin/bash
# KK_MSL_COMPRESS regression: pipelines built fresh and recreated from a pipeline
# cache (written with the MSL text dropped / kept, read in either mode) must render
# identically; the compressed cache must be smaller.
#   run.sh <kosmickrisp icd json> [work dir]   (needs Homebrew glslang, vulkan-loader, vulkan-headers)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ICD="$1"; OUT="${2:-${TMPDIR:-/tmp}/kk-msldrop-test}"
BREW="${HOMEBREW_PREFIX:-/opt/homebrew}"
mkdir -p "$OUT"
for f in tri.vert tri.frag fill.comp; do
  glslangValidator --quiet -V --target-env vulkan1.3 "$HERE/$f" -o "$OUT/$f.spv"
done
cc -O1 -Wall -I"$BREW/include" "$HERE/msldrop_test.c" -L"$BREW/lib" -lvulkan -Wl,-rpath,"$BREW/lib" -o "$OUT/msldrop_test"
run() { # name compress cache-in cache-out
  local in="" ; [ -n "$3" ] && in="$OUT/$3"
  VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD" KK_MSL_COMPRESS="$2" KK_MSL_STATS=1 \
    "$OUT/msldrop_test" "$OUT" $in ${4:+"$OUT/$4"} 2> "$OUT/$1.err" | tee "$OUT/$1.out" | sed "s/^/$1: /"
}
run raw_fresh 0 "" raw.cache
run z_fresh 1 "" z.cache
run z_from_z 1 z.cache ""
run raw_from_z 0 z.cache ""
run z_from_raw 1 raw.cache ""
run raw_from_raw 0 raw.cache ""
sums=$(cat "$OUT"/*.out | awk '{print $2}' | sort -u | wc -l | tr -d " ")
[ "$sums" = 1 ] || { echo "FAIL: checksums differ"; exit 1; }
rawc=$(awk '{print $4}' "$OUT/raw_fresh.out"); zc=$(awk '{print $4}' "$OUT/z_fresh.out")
[ "$zc" -lt "$rawc" ] || { echo "FAIL: compressed cache ($zc) not smaller than raw ($rawc)"; exit 1; }
grep -q "compressed [1-9][0-9]* B" "$OUT/z_from_z.err" || { echo "FAIL: no compressed MSL live in z_from_z"; exit 1; }
echo "PASS: 6 runs identical, cache raw $rawc -> compressed $zc bytes"
