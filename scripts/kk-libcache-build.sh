#!/bin/bash
# kk-libcache-build.sh <KK_LIBCACHE_DIR> [jobs]: build <dir>/msl/*.metal (collected by a run with KK_LIBCACHE_DIR=<dir>) into
# <dir>/lib/<key>.metallib with Apple's Metal toolchain; idempotent (skips built keys). `nps5 cache prepare` calls this between its passes.
#
# prepfast (v1.1+): the key already hashes MSL + KosmicKrisp build + macOS build + GPU, so a metallib is valid wherever its key is.
#   - NPS5_METALLIB_STORE=<dir> (`nps5 cache prepare` sets <cache root>/metallib-store; unset/empty = off): content-addressed
#     store shared by every cache dir. A key found there is APFS-cloned (cp -c, no compile); every newly built metallib is added to it.
#     Cloning a cache dir, re-preparing after the list grew, or preparing a second title version builds only the NEW keys.
#   - biggest-first (LPT) order: the build time is dominated by a few multi-MB texts (a 5 MB text takes 10-15 s, a 10 KB one 0.3 s);
#     alphabetical order let the biggest file start last and the other cores idle (sample of 48: 13.5 s -> 5.5 s at 4 jobs).
#   - the Metal tools are resolved ONCE (no `xcrun` spawn per file), one temp .air per key, no per-file mktemp/trap.
#   - NPS5_BUILD_QOS=utility|background runs the compilers under `taskpolicy -c` (default: plain nice 5; measured on a loaded Mac
#     utility was ~1.35x slower and background ~3.4x slower than nice, so it is opt-in).
# Same compiler flags as AnyPS5's tools/kk-metallib/build_metallib.sh (no AnyPS5 checkout needed any more); MANIFEST.txt as before.
set -euo pipefail
d=${1:?usage: kk-libcache-build.sh <KK_LIBCACHE_DIR> [jobs]}; jobs=${2:-4}
[ -d "$d/msl" ] || { echo "no $d/msl (nothing collected)"; exit 0; }
mtool=$(xcrun -sdk macosx -f metal 2>/dev/null) || { echo "kk-libcache-build: Metal toolchain not installed. Run: xcodebuild -downloadComponent MetalToolchain" >&2; exit 2; }
ltool=$(xcrun -sdk macosx -f metallib)
out="$d/lib"; mkdir -p "$out"
store=${NPS5_METALLIB_STORE-}
[ -n "$store" ] && mkdir -p "$store" 2>/dev/null || store=""
{
  echo "built: $(date -u +%FT%TZ)"
  echo "macos: $(sw_vers -productVersion) ($(sysctl -n kern.osversion))"
  "$mtool" --version | head -1
} > "$out/MANIFEST.txt"

# 1. keys already built are skipped; keys in the store are cloned; the rest is the work list (size, path), biggest first
pend=$(mktemp); trap 'rm -f "$pend"' EXIT
cloned=0; have=0
for f in "$d"/msl/*.metal; do
  [ -e "$f" ] || continue
  k=${f##*/}; k=${k%.metal}
  if [ -f "$out/$k.metallib" ]; then have=$((have+1)); continue; fi
  if [ -n "$store" ] && [ -f "$store/$k.metallib" ]; then
    cp -c "$store/$k.metallib" "$out/$k.metallib.tmp$$" 2>/dev/null || cp "$store/$k.metallib" "$out/$k.metallib.tmp$$"
    mv "$out/$k.metallib.tmp$$" "$out/$k.metallib"; cloned=$((cloned+1)); continue
  fi
  printf '%s %s\n' "$(stat -f%z "$f")" "$f" >> "$pend"
done
n=$(wc -l < "$pend" | tr -d ' ')
echo "libraries: $have already built, $cloned from the store${store:+ ($store)}, $n to build with $jobs jobs"

build_one() {
  f=$1; out=$2; mtool=$3; ltool=$4
  key=${f##*/}; key=${key%.metal}
  opts="${f%.metal}.opts"
  msl=$(sed -n 's/^msl=//p' "$opts"); math=$(sed -n 's/^math=//p' "$opts"); fp=$(sed -n 's/^fp=//p' "$opts")
  maj=$((0x$msl >> 16)); min=$((0x$msl & 0xffff))
  case $math in 2) mm=fast;; 1) mm=relaxed;; *) mm=safe;; esac
  case $fp in 0) ff=fast;; *) ff=precise;; esac
  inv=""; grep -q 'position, invariant' "$f" && inv=-fpreserve-invariance   # KK_VS_INVARIANT=1 MSL needs the invariance option
  air="$out/.$key.air.$$"
  if "$mtool" -c -std=metal$maj.$min -fmetal-math-mode=$mm -fmetal-math-fp32-functions=$ff $inv -w "$f" -o "$air" &&
     "$ltool" "$air" -o "$out/$key.metallib.tmp$$"; then
    mv "$out/$key.metallib.tmp$$" "$out/$key.metallib"; rm -f "$air"
    echo "built $key"
  else
    rm -f "$air" "$out/$key.metallib.tmp$$"; echo "FAILED $key" >&2; return 1
  fi
}
export -f build_one
q=()
case "${NPS5_BUILD_QOS:-}" in utility|background|maintenance) q=(taskpolicy -c "$NPS5_BUILD_QOS");; esac
rc=0
if [ "$n" -gt 0 ]; then
  sort -rn "$pend" | cut -d' ' -f2- | tr '\n' '\0' |
    nice -n 5 ${q[@]+"${q[@]}"} xargs -0 -P "$jobs" -I{} bash -c 'build_one "$1" "$2" "$3" "$4"' _ {} "$out" "$mtool" "$ltool" || rc=$?
fi
# 2. add what was built to the store (clone, no copy), once, so the next cache dir / re-prepare finds it
added=0
if [ -n "$store" ]; then
  for f in "$out"/*.metallib; do
    [ -e "$f" ] || continue
    k=${f##*/}
    [ -f "$store/$k" ] || { cp -c "$f" "$store/$k.tmp$$" 2>/dev/null || cp "$f" "$store/$k.tmp$$"; mv "$store/$k.tmp$$" "$store/$k"; added=$((added+1)); }
  done
fi
echo "done: $(ls "$out" | grep -c '\.metallib$') metallibs in $out ($added added to the store)"
exit $rc
