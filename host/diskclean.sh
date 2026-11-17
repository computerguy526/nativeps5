#!/bin/bash
# diskclean.sh — safe cleanup of nativeps5 leftovers when disk runs low.
#
#   tools-host/diskclean.sh                 dry run: list what would go, with sizes (deletes nothing)
#   tools-host/diskclean.sh --apply         delete, step by step, stopping once free space >= target
#   tools-host/diskclean.sh --apply --target 25   (GB; default 20)
#   tools-host/diskclean.sh --all           dry run of every step, ignoring the target
#
# Safety rules for every candidate (all must hold, else it is kept):
#   - not used by a running process (path appears in no process command line or cwd)
#   - not referenced by a script (*.sh) under work/, tools-host/, wt/fastbase/tools/
#   - not protected (built-in list below + tools-host/diskclean.protect, one substring per line)
#   - older than the step's minimum age
# Never touches: the game dump, git repos/branches, canonical builds/workdirs, worktrees (use a git-aware
# worktree cleanup for those), anything outside the project folder.
set -u
ROOT=${NPS5_ROOT:?set NPS5_ROOT (see BUILDING.md)}
LOG=$ROOT/work/guard/guard.log
APPLY=0; TARGET=20; ALL=0
while [ $# -gt 0 ]; do case "$1" in --apply) APPLY=1;; --target) TARGET=$2; shift;; --all) ALL=1;; -h|--help) sed -n 2,16p "$0"; exit 0;; *) echo "unknown arg $1" >&2; exit 2;; esac; shift; done

# Protected substrings (canonical stacks, golden caches, never-delete items). Extend via diskclean.protect.
PROTECT='boot/w_astro2
boot/userdata_astro
boot/native_int4
boot/native_int3
boot/out/
boot/app0
fast/w_int6
fast/w_int7
fast/w_sf5
fast/w_ic
fast/w_int4
fast/w_up1
fast/out_ic
fast/native_ic
fast/out_int4
fast/native_int4
boot/b2
boot/overlay
fast/userdata_gameplay
fast/userdata_play
fast/native_sf
boot/overlay_canon
overlay_shd07b
s3/kk-astro
s3/kk-bary
s3/kk-int4
s3/kk-poison
s3/kk-mesh48
anyps5-astro-int4
anyps5-astro-int3
anyps5-dark4
anyps5-up1
anyps5-int4
wt/int4
wt/fastbase'
[ -f "$ROOT/tools-host/diskclean.protect" ] && PROTECT="$PROTECT
$(grep -v '^#' "$ROOT/tools-host/diskclean.protect" | sed '/^$/d')"

free_gb() { df -g "$ROOT" | awk 'NR==2{print $4}'; }
log() { echo "$(date '+%F %T') diskclean: $*" >> "$LOG"; echo "$*"; }
enough() { [ "$ALL" = 0 ] && [ "$APPLY" = 1 ] && [ "$(free_gb)" -ge "$TARGET" ]; }
PS_CACHE=$(ps -axo command= 2>/dev/null; for p in $(ps -axo pid=); do lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'; done)
SCRIPTS=$(find "$ROOT/work" "$ROOT/tools-host" "$ROOT/wt/fastbase/tools" -maxdepth 4 -name '*.sh' -not -path '*/runs_by_tag/*' 2>/dev/null)
protected() { local s; while IFS= read -r s; do [ -n "$s" ] && case "$1" in *"$s"*) return 0;; esac; done <<< "$PROTECT"; return 1; }
busy() { printf '%s\n' "$PS_CACHE" | grep -qF "$1"; }
referenced() { local rel=${1#$ROOT/}; local base; base=$(basename "$1")
  [ -n "$SCRIPTS" ] && grep -lF -e "$rel" $SCRIPTS 2>/dev/null | grep -qv "$0" && return 0
  # bare-name refs like boot/w_foo or fast/w_foo
  local short; short=$(echo "$rel" | sed -E 's#.*/(boot|fast|s3)/#\1/#')
  [ -n "$SCRIPTS" ] && grep -lwF -e "$short" $SCRIPTS 2>/dev/null | grep -qv "$0" && return 0
  return 1; }
older() { [ -z "$(find "$1" -maxdepth 0 -mmin -"$2" 2>/dev/null)" ]; }
TOTAL=0
consider() { # path minage-minutes reason
  local p=$1 age=$2 why=$3 sz
  [ -e "$p" ] || return 0
  protected "$p" && return 0
  older "$p" "$age" || return 0
  busy "$p" && { echo "  keep (in use)  $p"; return 0; }
  referenced "$p" && { echo "  keep (script ref)  $p"; return 0; }
  sz=$(du -sk "$p" 2>/dev/null | cut -f1); TOTAL=$((TOTAL + sz))
  if [ "$APPLY" = 1 ]; then rm -rf "$p" && log "removed ($why, $((sz/1024)) MB) ${p#$ROOT/}"; else printf '  would remove %6d MB  %s  [%s]\n' $((sz/1024)) "${p#$ROOT/}" "$why"; fi; }

echo "free: $(free_gb) GB, target $TARGET GB, mode $([ $APPLY = 1 ] && echo APPLY || echo dry-run)"
[ "$APPLY" = 1 ] && log "start: $(free_gb) GB free, target $TARGET GB"
[ "$APPLY" = 1 ] && [ "$ALL" = 0 ] && [ "$(free_gb)" -ge "$TARGET" ] && { log "already above target"; exit 0; }

echo "1. stale GPU-cache versions (gpucache_prune.py)"
if [ "$APPLY" = 1 ]; then python3 -I "$ROOT/tools-host/gpucache_prune.py" --apply >> "$LOG" 2>&1; else python3 -I "$ROOT/tools-host/gpucache_prune.py" 2>/dev/null | tail -3; fi
enough && exit 0

echo "2. run artefacts: big files (>20 MB, e.g. pcmap.bin) and frame dumps older than 60 min"
for base in "$ROOT/work/ppsa21564/boot/runs_by_tag" "$ROOT/work/ppsa30552/boot/fast/runs_by_tag" "$ROOT/work/ppsa30552/boot/runs_by_tag"; do
  [ -d "$base" ] || continue
  while IFS= read -r f; do consider "$f" 60 "run artefact"; done < <(find "$base" -type f \( -size +20M -o -name 'frame_*.bmp' -o -name 'k3_*.bmp' \) ! -name 'perf*' ! -name 'summary*' ! -name '*.txt' ! -name '*.csv' -mmin +60 2>/dev/null)
done
enough && exit 0

echo "3. Kyty dump folders older than 6 h"
for d in "$ROOT"/work/kyty/dumps/*/; do consider "${d%/}" 360 "kyty dumps"; done
enough && exit 0

echo "4. agent workdir clones (boot/w_*, fast/w_*) untouched for 12 h"
for d in "$ROOT"/work/ppsa21564/boot/w_* "$ROOT"/work/ppsa30552/boot/fast/w_* "$ROOT"/work/ppsa21564/boot/userdata_* "$ROOT"/work/ppsa30552/boot/fast/userdata_*; do consider "$d" 720 "workdir clone"; done
enough && exit 0

echo "5. relink out-clones (boot/out_*, fast/out_*) untouched for 6 h"
for d in "$ROOT"/work/ppsa21564/boot/out_* "$ROOT"/work/ppsa30552/boot/fast/out_*; do consider "$d" 360 "relink clone"; done
enough && exit 0

echo "6. worktree build dirs (wt/*/build-arm64, work/kyty/build-*) untouched for 24 h"
for d in "$ROOT"/wt/*/build-arm64 "$ROOT"/work/kyty/build-*; do consider "$d" 1440 "build dir"; done
enough && exit 0

echo "7. old KosmicKrisp copies (work/s3/kk-*) untouched for 24 h"
for d in "$ROOT"/work/s3/kk-*; do consider "$d" 1440 "KK copy"; done
enough && exit 0

echo "8. regression scratch (work/regress*) untouched for 6 h"
for d in "$ROOT"/work/regress*; do consider "$d" 360 "regress scratch"; done

echo "total $([ $APPLY = 1 ] && echo removed || echo removable): $((TOTAL/1024)) MB; free now $(free_gb) GB"
[ "$APPLY" = 1 ] && log "done: $(free_gb) GB free"
[ "$APPLY" = 1 ] && [ "$(free_gb)" -lt "$TARGET" ] && log "still below target: consider worktree cleanup (git-aware) or protected items"
exit 0
