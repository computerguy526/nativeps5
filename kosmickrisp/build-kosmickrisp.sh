#!/usr/bin/env bash
# Reproducible build of the KosmicKrisp Vulkan-on-Metal driver (Mesa).
#
# Clones Mesa at the commit in MESA_COMMIT (shallow), applies patches/*.patch
# in lexical order, builds only the KosmicKrisp Vulkan driver and its ICD,
# installs to a prefix and prints the ICD json path.
#
# Usage:
#   build-kosmickrisp.sh [--arch arm64|x86_64] [--work DIR] [--src DIR]
#                        [--prefix DIR] [--jobs N]
#
#   --arch    arm64 (native, default) or x86_64 (cross-built, for Rosetta)
#   --work    working dir for mesa source, build dirs and install (default: ./kk-build)
#   --src     Mesa source dir; reused if it already exists (default: WORK/mesa)
#   --prefix  install prefix (default: WORK/install-ARCH)
#   --jobs    parallel jobs (default: all CPUs)
#
# Environment:
#   KK_VENV   optional python venv with mako, pyyaml, packaging (prepended to PATH)
#
# Requires on the host: git, meson, ninja, python3 (mako, pyyaml, packaging),
# Homebrew llvm (for llvm-config) and bison (>= 3). Nothing is installed and
# no sudo is used.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MESA_URL="https://gitlab.freedesktop.org/mesa/mesa.git"
COMMIT="$(tr -d '[:space:]' < "$HERE/MESA_COMMIT")"

ARCH="arm64"
WORK=""
SRC=""
PREFIX=""
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

die() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*"; }

usage() {
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)   [[ $# -ge 2 ]] || die "--arch needs a value"; ARCH="$2"; shift 2 ;;
        --work)   [[ $# -ge 2 ]] || die "--work needs a value"; WORK="$2"; shift 2 ;;
        --src)    [[ $# -ge 2 ]] || die "--src needs a value"; SRC="$2"; shift 2 ;;
        --prefix) [[ $# -ge 2 ]] || die "--prefix needs a value"; PREFIX="$2"; shift 2 ;;
        --jobs)   [[ $# -ge 2 ]] || die "--jobs needs a value"; JOBS="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

case "$ARCH" in
    arm64)  ICD_ARCH="aarch64" ;;
    x86_64) ICD_ARCH="x86_64" ;;
    *) die "--arch must be arm64 or x86_64" ;;
esac

[[ "$(uname -s)" == "Darwin" ]] || die "macOS only"
[[ "$(uname -m)" == "arm64" ]] || die "build host must be Apple silicon (arm64); x86_64 is cross-built from it"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "MESA_COMMIT is not a full commit hash"

# Tools: Homebrew llvm and bison first, optional venv for mako/pyyaml/packaging.
for d in /opt/homebrew/opt/bison/bin /opt/homebrew/opt/llvm/bin; do
    [[ -d "$d" ]] && PATH="$d:$PATH"
done
[[ -n "${KK_VENV:-}" ]] && PATH="$KK_VENV/bin:$PATH"
export PATH

for tool in git meson ninja python3 bison; do
    command -v "$tool" >/dev/null 2>&1 || die "missing tool on PATH: $tool"
done
LLVM_CONFIG="/opt/homebrew/opt/llvm/bin/llvm-config"
[[ -x "$LLVM_CONFIG" ]] || die "missing $LLVM_CONFIG (brew install llvm)"
python3 -c 'import mako, yaml, packaging' 2>/dev/null \
    || die "python3 lacks mako/pyyaml/packaging; set KK_VENV to a venv that has them"

WORK="${WORK:-$PWD/kk-build}"
mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd)"
SRC="${SRC:-$WORK/mesa}"
PREFIX="${PREFIX:-$WORK/install-$ARCH}"
mkdir -p "$(dirname "$PREFIX")"
PREFIX="$(cd "$(dirname "$PREFIX")" && pwd)/$(basename "$PREFIX")"

# ---- 1. source: clone at the pinned commit, or reuse an existing checkout ----
if [[ ! -d "$SRC/.git" ]]; then
    [[ ! -e "$SRC" ]] || die "$SRC exists and is not a git checkout"
    log "cloning Mesa $COMMIT (shallow) into $SRC"
    mkdir -p "$SRC"
    git -C "$SRC" init -q
    git -C "$SRC" remote add origin "$MESA_URL"
    git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
    git -C "$SRC" checkout -q --detach FETCH_HEAD
fi
HEAD_NOW="$(git -C "$SRC" rev-parse HEAD)"
[[ "$HEAD_NOW" == "$COMMIT" ]] || die "$SRC is at $HEAD_NOW, expected $COMMIT"

# ---- 2. patches: check before apply; skip ones already applied (reuse case) ----
log "applying patches from $HERE/patches"
shopt -s nullglob
patches=("$HERE"/patches/*.patch)
shopt -u nullglob
if [[ ${#patches[@]} -eq 0 ]]; then
    log "no patches to apply"
fi
# Stacked patches touch the same files, so an earlier patch no longer reverse-applies once a later one
# is in. The applied series is therefore recorded in SRC/.nps5-patches (name + sha256 per patch).
STAMP="$SRC/.nps5-patches"
series=""
for p in ${patches[@]+"${patches[@]}"}; do
    series+="$(basename "$p") $(shasum -a 256 "$p" | cut -d' ' -f1)"$'\n'
done
if [[ -f "$STAMP" && "$(cat "$STAMP")"$'\n' == "$series" ]]; then
    log "patch series already applied (${#patches[@]} patches)"
else
    [[ -f "$STAMP" ]] && die "$SRC has a different patch series applied (see $STAMP); use a fresh checkout"
    for p in ${patches[@]+"${patches[@]}"}; do
        name="$(basename "$p")"
        if git -C "$SRC" apply --reverse --check "$p" 2>/dev/null; then
            log "already applied: $name"
            continue
        fi
        git -C "$SRC" apply --check "$p" || die "patch does not apply cleanly: $name"
        git -C "$SRC" apply "$p"
        log "applied: $name"
    done
    printf '%s' "$series" > "$STAMP"
fi

# Common meson options, from the shadPS4 mesa-kosmickrisp wrapper.
COMMON_OPTS=(
    -Dplatforms=macos
    -Dvulkan-drivers=kosmickrisp
    -Dgallium-drivers=
    -Dopengl=false
    -Dzstd=disabled
)
TARGET_TARGETS=(vulkan_kosmickrisp kosmickrisp_mesa_icd)

# ---- 3. configure and build ----
if [[ "$ARCH" == "arm64" ]]; then
    BUILD="$WORK/build-arm64"
    log "meson setup (arm64 native) -> $BUILD"
    meson setup "$BUILD" "$SRC" --buildtype=release --prefer-static \
        --prefix="$PREFIX" "${COMMON_OPTS[@]}"
    log "building KosmicKrisp targets"
    meson compile -C "$BUILD" -j "$JOBS" "${TARGET_TARGETS[@]}"
else
    [[ -f "$HERE/mesa-x86_64.ini" ]] || die "missing cross file $HERE/mesa-x86_64.ini"
    # Stage 1: native host tools (mesa_clc, vtn_bindgen2, kk_clc) used by the cross build.
    TOOLS="$WORK/build-tools"
    log "meson setup (native tools) -> $TOOLS"
    LLVM_CONFIG="$LLVM_CONFIG" meson setup "$TOOLS" "$SRC" --buildtype=release \
        -Dinstall-mesa-clc=true -Dinstall-precomp-compiler=true "${COMMON_OPTS[@]}"
    log "building native tools"
    LLVM_CONFIG="$LLVM_CONFIG" meson compile -C "$TOOLS" -j "$JOBS" \
        mesa_clc vtn_bindgen2 kk_clc
    # Stage 2: x86_64 cross build using those tools.
    BUILD="$WORK/build-x86_64"
    log "meson setup (x86_64 cross) -> $BUILD"
    MACOSX_DEPLOYMENT_TARGET=26.0 \
    PATH="$TOOLS/src/compiler/clc:$TOOLS/src/compiler/spirv:$TOOLS/src/kosmickrisp/clc:$PATH" \
    meson setup "$BUILD" "$SRC" --buildtype=release \
        --cross-file="$HERE/mesa-x86_64.ini" --prefer-static \
        --prefix="$PREFIX" \
        -Dmesa-clc=system -Dprecomp-compiler=system -Dspirv-tools=disabled \
        "${COMMON_OPTS[@]}"
    log "building KosmicKrisp targets (x86_64)"
    MACOSX_DEPLOYMENT_TARGET=26.0 \
    PATH="$TOOLS/src/compiler/clc:$TOOLS/src/compiler/spirv:$TOOLS/src/kosmickrisp/clc:$PATH" \
    meson compile -C "$BUILD" -j "$JOBS" "${TARGET_TARGETS[@]}"
fi

# ---- 4. install (driver + ICD only) ----
log "installing to $PREFIX"
meson install -C "$BUILD" --no-rebuild
LIB="$PREFIX/lib/libvulkan_kosmickrisp.dylib"
ICD="$PREFIX/share/vulkan/icd.d/kosmickrisp_mesa_icd.$ICD_ARCH.json"
[[ -f "$LIB" ]] || die "missing installed driver: $LIB"
[[ -f "$ICD" ]] || die "missing installed ICD: $ICD"

# Rewrite any @rpath/libz reference to the system libz (as in the x86_64 build notes).
if otool -L "$LIB" | grep -q '@rpath/libz\.1\.dylib'; then
    log "fixing libz install name"
    install_name_tool -change @rpath/libz.1.dylib /usr/lib/libz.1.dylib "$LIB"
    codesign -f -s - "$LIB"
fi

log "built KosmicKrisp $COMMIT ($ARCH)"
echo "driver: $LIB"
echo "ICD:    $ICD"
echo "use:    VK_DRIVER_FILES=$ICD vulkaninfo --summary"
