#!/bin/bash
# kk-libcache-build.sh <KK_LIBCACHE_DIR> [jobs]: build <dir>/msl/*.metal (collected by a run with KK_LIBCACHE_DIR=<dir>) into
# <dir>/lib/<key>.metallib with Apple's Metal toolchain; idempotent (skips built keys). `nps5 cache prepare` calls this between its two passes.
# Needs the AnyPS5 checkout's tools/kk-metallib/build_metallib.sh ($NPS5_ANYPS5_DIR, default $NPS5_ROOT/anyps5).
set -euo pipefail
d=${1:?usage: kk-libcache-build.sh <KK_LIBCACHE_DIR> [jobs]}; jobs=${2:-4}
b="${NPS5_ANYPS5_DIR:-${NPS5_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}/anyps5}/tools/kk-metallib/build_metallib.sh"
[ -x "$b" ] || { echo "kk-libcache-build: no $b (NPS5_ANYPS5_DIR)" >&2; exit 2; }
[ -d "$d/msl" ] || { echo "no $d/msl (nothing collected)"; exit 0; }
nice -n 5 "$b" "$d/msl" "$d/lib" "$jobs"
