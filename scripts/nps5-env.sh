#!/bin/bash
# Source this: resolves the workspace layout and the machine limits for every other script.
#   . scripts/nps5-env.sh          (reads ./nps5.conf of this checkout when present; the environment wins)
_nps5_here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_nps5_conf="${NPS5_CONF:-$_nps5_here/nps5.conf}"
if [ -f "$_nps5_conf" ]; then
  while IFS='=' read -r _k _v; do
    case "$_k" in ''|\#*) continue;; NPS5_[A-Z0-9_]*) ;; *) echo "nps5-env: ignoring '$_k' in $_nps5_conf" >&2; continue;; esac
    [ -n "$(eval "printf %s \"\${$_k:-}\"")" ] && continue          # the environment wins
    _v="${_v%%[[:space:]]#*}"; eval "export $_k=\"$_v\""
  done < "$_nps5_conf"
fi
if [ -z "${NPS5_ROOT:-}" ]; then
  # the workspace root is the folder that holds this checkout (BUILDING.md, step 1), unless the components
  # were already cloned inside the checkout (older layout; .gitignore covers it)
  if [ -d "$_nps5_here/ps5recomp" ] && [ ! -d "$_nps5_here/../ps5recomp" ]; then NPS5_ROOT="$_nps5_here"; else NPS5_ROOT="$(cd "$_nps5_here/.." && pwd)"; fi
fi
export NPS5_ROOT
export NPS5_PUBLIC_DIR="$_nps5_here"
export NPS5_RECOMP_DIR="${NPS5_RECOMP_DIR:-$NPS5_ROOT/ps5recomp}"
export NPS5_ANYPS5_DIR="${NPS5_ANYPS5_DIR:-$NPS5_ROOT/anyps5}"
export NPS5_KYTY_DIR="${NPS5_KYTY_DIR:-$NPS5_ROOT/kyty}"
export NPS5_KK_WORK="${NPS5_KK_WORK:-$NPS5_ROOT/build/kosmickrisp}"
export NPS5_SCRATCH="${NPS5_SCRATCH:-$NPS5_ROOT/scratch}"
export NPS5_BREW_PREFIX="${NPS5_BREW_PREFIX:-$(brew --prefix 2>/dev/null || echo /usr/local)}"
# Machine class and the limits derived from this Mac (scripts/nps5lib/hw.py; docs/reference/hardware.md): NPS5_HW_CLASS,
# NPS5_HW_CACHE_KEY, NPS5_SELF_MEM_LIMIT_MB, NPS5_BUILD_JOBS, NPS5_GUARD_* and NPS5_FUNC_HOLD_LOAD. Names already set are kept.
eval "$(python3 -I "$_nps5_here/scripts/nps5lib/hw.py" env 2>/dev/null || true)"
export NPS5_BUILD_JOBS="${NPS5_BUILD_JOBS:-4}"
if [ -z "${NPS5_SELF_MEM_LIMIT_MB:-}" ]; then      # hw.py could not tell (no python3, not Apple silicon): the same formula
  _ram_mb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 17179869184) / 1048576 ))
  export NPS5_SELF_MEM_LIMIT_MB=$(( _ram_mb * 37 / 48 ))
fi
export NPS5_KYTY_GPU_LIB="${NPS5_KYTY_GPU_LIB:-$NPS5_KYTY_DIR/build-arm64/kyty_gpu.dylib}"
export NPS5_KK_ICD="${NPS5_KK_ICD:-$NPS5_KK_WORK/install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json}"
# what `nps5 doctor` / `nps5 build` (ps5recomp) look for
export NPS5_KK_INSTALL="${NPS5_KK_INSTALL:-$NPS5_KK_WORK/install-arm64}"
export NPS5_KK_PREFIX="${NPS5_KK_PREFIX:-$NPS5_KK_WORK/install-arm64}"
# AnyPS5 is optional (not public): the variable is set only when a build is there, so tools report it as absent otherwise
[ -n "${NPS5_ANYPS5_BUILD:-}" ] || { [ -d "$NPS5_ANYPS5_DIR/build-arm64" ] && export NPS5_ANYPS5_BUILD="$NPS5_ANYPS5_DIR/build-arm64"; } || true
export NPS5_VULKAN_LIB="${NPS5_VULKAN_LIB:-$NPS5_BREW_PREFIX/lib/libvulkan.1.dylib}"
unset _nps5_here _nps5_conf _k _v _ram_mb
