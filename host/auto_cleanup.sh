#!/bin/bash
# auto_cleanup.sh — started by machine_guard.sh when the data volume runs low (free < AUTO_CLEAN_GB).
# Frees space in safe steps, stopping as soon as free >= AUTO_CLEAN_TARGET_GB. Never touches the game dump,
# never-delete workdirs' current caches, canonical builds, or anything a running process uses. Logs to guard.log.
set -u
ROOT=${NPS5_ROOT:?set NPS5_ROOT (see BUILDING.md)}
LOG=$ROOT/work/guard/guard.log
TARGET=${AUTO_CLEAN_TARGET_GB:-20}
AGE_MIN=${AUTO_CLEAN_AGE_MIN:-60}   # 21:45: was 360; pcmap.bin (618 MB per run) filled the disk          # run artefacts older than this (minutes) may go
CACHE_AGE_MIN=${AUTO_CLEAN_CACHE_AGE_MIN:-720}
CANON_BUILDS='anyps5-dark4|anyps5-up1|anyps5-int4|anyps5-astrobase|anyps5-astro3|anyps5-astro-int'
log() { echo "$(date '+%F %T') auto_cleanup: $*" >> "$LOG"; }
free_gb() { df -g "$ROOT" | awk 'NR==2{print $4}'; }
enough() { [ "$(free_gb)" -ge "$TARGET" ]; }
busy() { ps -axo command= | grep -v grep | grep -v auto_cleanup | grep -qF "$1"; }

log "start: $(free_gb) GB free, target $TARGET GB"
enough && { log "already above target"; exit 0; }

# 1. stale GPU-cache version folders (safe tool: keeps current + 1 spare, skips busy and protected workdirs)
python3 -I "$ROOT/tools-host/gpucache_prune.py" --apply >> "$LOG" 2>&1 && log "step1 gpucache prune done: $(free_gb) GB"
enough && exit 0

# 2. old frame dumps and big run artefacts (keep perf/summary/log text files)
for base in "$ROOT/work/ppsa21564/boot/runs_by_tag" "$ROOT/work/ppsa30552/boot/fast/runs_by_tag"; do
  [ -d "$base" ] || continue
  find "$base" -type f \( -name 'frame_*.bmp' -o -size +20M \) -mmin +"$AGE_MIN" \
       ! -name 'perf*' ! -name 'summary*' ! -name '*.txt' ! -name '*.csv' -print -delete 2>/dev/null | wc -l | xargs -I{} log "step2 removed {} old dump/big files under $base"
done
log "step2 done: $(free_gb) GB"
enough && exit 0

# 3. old translate logs (keep newest 2)
if [ -d "$ROOT/work/ppsa21564/boot/truns" ]; then
  ls -dt "$ROOT"/work/ppsa21564/boot/truns/*/ 2>/dev/null | tail -n +3 | while read -r d; do rm -rf "$d"; done
  log "step3 truns trimmed: $(free_gb) GB"
fi
enough && exit 0

# 4. build-dir shader caches of non-canonical builds not touched recently and not in use
for sc in "$ROOT"/wt/*/build-arm64/core/libs/prx/libSceAgcDriver/shader_cache; do
  [ -d "$sc" ] || continue
  wt=$(echo "$sc" | sed -E 's#.*/wt/([^/]+)/.*#\1#')
  echo "$wt" | grep -qE "^($CANON_BUILDS)$" && continue
  busy "/wt/$wt/" && continue
  [ -n "$(find "$sc" -maxdepth 0 -mmin -"$CACHE_AGE_MIN")" ] && continue
  rm -rf "$sc" && log "step4 removed $sc"
done
log "done: $(free_gb) GB free"
enough || log "WARNING still below target: manual cleanup needed (finished agents' workdir clones, old worktrees)"
