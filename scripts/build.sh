#!/bin/bash
# Build the pieces, CPU only, at nice 5 with NPS5_BUILD_JOBS (default 4) jobs.
#   scripts/build.sh recomp        Go translator + packager (ps4-recomp, nps5) and its unit tests
#   scripts/build.sh kosmickrisp   Mesa KosmicKrisp with kosmickrisp/patches, into $NPS5_KK_WORK
#   scripts/build.sh kyty          Kyty GPU dylib (kyty_gpu.dylib)
#   scripts/build.sh anyps5        AnyPS5 arm64 host libraries (PS5 system libraries, AGC driver, libnps5_gpu.dylib)
#   scripts/build.sh all           recomp, kosmickrisp, kyty, anyps5
# Steps and their prerequisites are described in BUILDING.md; each step prints what it produced.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5-env.sh"
J="$NPS5_BUILD_JOBS"; N="nice -n 5"
need() { for t in "$@"; do command -v "$t" >/dev/null || { echo "build: missing tool '$t' (BUILDING.md, Prerequisites)" >&2; exit 2; }; done; }

recomp() {
  need go git
  cd "$NPS5_RECOMP_DIR"
  # audio decoders compiled into every translated program's runtime (nps5 build stops without them)
  git submodule update --init --depth 1 3rdparty/minimp3 3rdparty/libatrac9
  mkdir -p bin
  $N go build -p "$J" -o bin/ps4-recomp ./cmd/ps4-recomp
  $N go build -p "$J" -o bin/nps5 ./cmd/nps5
  echo "built: $NPS5_RECOMP_DIR/bin/ps4-recomp, bin/nps5"
}
kosmickrisp() {
  need git meson ninja python3
  local kk="$NPS5_PUBLIC_DIR/kosmickrisp"
  # build-kosmickrisp.sh applies <its folder>/patches/*.patch on the commit in <its folder>/MESA_COMMIT
  [ -n "${KK_VENV:-}" ] || python3 -c 'import mako, yaml, packaging' 2>/dev/null || {
    echo "build: python needs mako, pyyaml, packaging: python3 -m venv \$NPS5_KK_WORK/venv && \$NPS5_KK_WORK/venv/bin/pip install mako pyyaml packaging, then KK_VENV=\$NPS5_KK_WORK/venv" >&2; exit 2; }
  $N "$kk/build-kosmickrisp.sh" --arch arm64 --work "$NPS5_KK_WORK" --jobs "$J"
  if [ -f "$kk/EXPECTED_TREE" ] && [ -d "$NPS5_KK_WORK/mesa/.git" ]; then
    echo "expected source tree (BUILDING.md, step 3, has the command to compare): $(cat "$kk/EXPECTED_TREE")"
  fi
}
anyps5() {
  need cmake ninja glslangValidator
  [ -d "$NPS5_ANYPS5_DIR/.git" ] || { echo "build: no AnyPS5 checkout at $NPS5_ANYPS5_DIR: run scripts/fetch-components.sh anyps5 (BUILDING.md, step 1)" >&2; exit 2; }
  [ -e "$NPS5_BREW_PREFIX/lib/cmake/SDL2" ] || { echo "build: missing SDL2 under $NPS5_BREW_PREFIX (brew install sdl2-compat)" >&2; exit 2; }
  cd "$NPS5_ANYPS5_DIR"
  git submodule update --init --depth 1 3rdparty/ZArchive 3rdparty/stb 3rdparty/libjpeg-turbo 3rdparty/zstd 3rdparty/Vulkan-Headers 3rdparty/SPIRV-Headers 3rdparty/glslang
  $N cmake -S . -B build-arm64 -G Ninja -DAPS5_HOST_ARM64=ON -DCMAKE_BUILD_TYPE=Release
  # nps5_gpu (libnps5_gpu.dylib: the runtime's bridge, with the host side of the Kyty back end) is not part of `all`
  $N cmake --build build-arm64 -j "$J" --target libSceAgc libSceAgcDriver libkernel libc nps5_gpu
  local out="$NPS5_ANYPS5_DIR/build-arm64/core/libs/libs/unpatched"
  for f in libSceAgc.prx libSceAgcDriver.prx libkernel.prx libc.prx libnps5_gpu.dylib; do
    [ -f "$out/$f" ] || { echo "build: $out/$f was not produced" >&2; exit 1; }
  done
  echo "built: $out (NPS5_ANYPS5_BUILD defaults to $NPS5_ANYPS5_DIR/build-arm64)"
}
kyty() {
  need cmake ninja glslangValidator
  [ -e "$NPS5_BREW_PREFIX/lib/libSDL3.0.dylib" ] || { echo "build: missing $NPS5_BREW_PREFIX/lib/libSDL3.0.dylib (brew install sdl3)" >&2; exit 2; }
  cd "$NPS5_KYTY_DIR"
  git submodule update --init --recursive --depth 1
  $N cmake -S . -B build-arm64 -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 -DKYTY_BUILD_LAUNCHER=OFF \
     -DKYTY_GPU_SDL3_DYLIB="$NPS5_BREW_PREFIX/lib/libSDL3.0.dylib"
  $N cmake --build build-arm64 -j "$J" --target kyty_gpu
  echo "built: $NPS5_KYTY_DIR/build-arm64/kyty_gpu.dylib (set NPS5_KYTY_GPU_LIB to it)"
}
for step in "${@:-help}"; do
  case "$step" in
    recomp|kosmickrisp|anyps5|kyty) "$step" ;;
    all) recomp; kosmickrisp; kyty; anyps5 ;;
    *) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2 ;;
  esac
done
