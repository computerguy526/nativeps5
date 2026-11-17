#!/bin/bash
# machine_guard.sh — keep nativeps5 test/build load from taking the Mac down.
# Thresholds come from this Mac's RAM and core count (NPS5_GUARD_* from scripts/nps5-env.sh, formulas in scripts/nps5lib/hw.py;
# the numbers written here are the 24 GB / 10+4 core values and the fallback). GUARD_* variables override.
# Runs in the background; every 2 s it checks memory pressure, swap and our processes:
#   - one big game run at a time: if more than MAX_BIG runs exceed BIG_MB resident, the newest is killed
#   - per-run cap: a run above RUN_CAP_MB resident is killed
#   - pressure WARN: our builds/translators are deprioritised (renice 20, background QoS)
#   - pressure CRITICAL or swap above SWAP_MB: the largest of our runs is killed (then the largest build)
#   - START HOLD (2026-11-16, after the IOGPUFamily kernel panic): while load avg (1 min) > HOLD_LOAD or swap > HOLD_SWAP_MB
#     the guard writes work/guard/start.hold; run_sandboxed's title lock waits while it exists, so no NEW title run starts
#     on a machine that is already thrashing (a running title is not touched by this)
#   - GPU-heavy limit: besides the title, at most MAX_GPU_EXTRA GPU-heavy processes (agc_frame_replay + mesa/KK/driver
#     GPU test binaries) at once; the newest are killed
#   - a run above FAST_MB (3/4 of RAM; 18 GB on 24 GB) is sampled every 0.5 s instead of 2 s, and its footprint is logged each 10 s
# Kills use SIGKILL (never SIGSTOP: a frozen Metal client can stall WindowServer). Every action is logged to
# $LOG with the command line, so an agent that sees rc=137 can check why.
# Only touches processes under the project dirs / named like our runs; never system or agent processes.
set -u
ROOT=${NPS5_ROOT:?set NPS5_ROOT (see BUILDING.md)}
_ge="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/nps5-env.sh"
[ -n "${NPS5_GUARD_RUN_CAP_MB:-}" ] || { [ -f "$_ge" ] && . "$_ge"; }
LOG=${GUARD_LOG:-$ROOT/work/guard/guard.log}
BIG_MB=${GUARD_BIG_MB:-4096}
MAX_BIG=${GUARD_MAX_BIG:-1}
RUN_CAP_MB=${GUARD_RUN_CAP_MB:-${NPS5_GUARD_RUN_CAP_MB:-20480}}   # phys_footprint (includes compressed + GPU/IOSurface), not RSS
GPU_TEST_MAX_S=${GUARD_GPU_TEST_MAX_S:-120}   # GPU/KK test binaries (mesa tests, work/s3, *_test, *tests*) older than this are killed
HOLD_LOAD=${GUARD_HOLD_LOAD:-${NPS5_GUARD_HOLD_LOAD:-12}}          # no new title run while the 1-min load average is above this
HOLD_SWAP_MB=${GUARD_HOLD_SWAP_MB:-${NPS5_GUARD_HOLD_SWAP_MB:-12288}}  # 2026-11-16 19:00: stale swap after spikes held all runs at normal pressure; pressure level >= 2 (WARN) now also holds;  # ... or swap used is above this (running titles are not killed by this, see SWAP_MB)
MAX_GPU_EXTRA=${GUARD_MAX_GPU_EXTRA:-${GUARD_MAX_REPLAYS:-1}}   # GPU-heavy processes besides the title (replays + GPU tests)
FAST_MB=${GUARD_FAST_MB:-${NPS5_GUARD_FAST_MB:-18432}}           # a run above this footprint is polled every 0.5 s
SWAP_MB=${GUARD_SWAP_MB:-${NPS5_GUARD_SWAP_MB:-12288}}   # was 6144: macOS keeps swap high after a spike, so 6 GB killed builds continuously at only WARN pressure (17:15)
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
trap '[ -s "$(dirname "$LOG")/paused.list" ] && while read -r bp; do kill -CONT "$bp" 2>/dev/null; done < "$(dirname "$LOG")/paused.list"; : > "$(dirname "$LOG")/paused.list"; rm -f "$(dirname "$LOG")/start.hold"' EXIT
log "guard start (pid $$) [phys_footprint] big>${BIG_MB}MB max ${MAX_BIG}, run cap ${RUN_CAP_MB}MB, swap ${SWAP_MB}MB, start hold load>${HOLD_LOAD} swap>${HOLD_SWAP_MB}MB, max ${MAX_GPU_EXTRA} GPU-heavy besides the title, fast poll >${FAST_MB}MB"
HOLD=$(dirname "$LOG")/start.hold; rm -f "$HOLD"
holding=0; last_fp_log=0

is_run() {   # game / test executables we launch
  case "$1" in
    *claude*|*/zsh*|*/bash*|*gtimeout*|*run_sandboxed*|*ps4-recomp*|*fragment-serve*) return 1;;
    *nps5-title*|*/native|*/native\ *|*/native_*|*/native_out*|*/t.exe*|*eboot*|*/out*/native*) return 0;;
  esac
  return 1
}
is_build() {
  case "$1" in
    *claude*|*/zsh*|*/bash*) return 1;;
    *clang*|*ninja*|*ld64*|*/ld\ *|*go-build*|*"go test"*|*"go build"*|*ps4-recomp*|*fexasm*|*meson*) return 0;;
  esac
  return 1
}
ours() { case "$1" in *"$ROOT"*|*/Users/Shared/nps5*|*nps5*|*ps4-recomp*|*/tmp/go-build*|*clang*|*ninja*|*ld64*) return 0;; esac; return 1; }

kill_pid() { # pid rss_mb reason cmd
  kill -KILL "$1" 2>/dev/null && log "KILL pid $1 rss ${2}MB: $3 :: ${4:0:220}"
}

# macOS ps has no etimes: psx prints "pid rss elapsed_seconds command"
psx() { ps -axo pid=,rss=,etime=,command= 2>/dev/null | awk '{e=$3; d=0; if (index(e,"-")) {split(e,a,"-"); d=a[1]; e=a[2]} n=split(e,t,":"); s=0; for(i=1;i<=n;i++) s=s*60+t[i]; $3=d*86400+s; print}'; }

last_warn=0
while true; do
  level=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 1)
  swap=$(sysctl -n vm.swapusage 2>/dev/null | sed -E 's/.*used = ([0-9.]+)M.*/\1/' | cut -d. -f1)
  swap=${swap:-0}
  runs=(); builds=(); nap=2
  while read -r pid rss etime cmd; do
    [ -z "$pid" ] && continue
    ours "$cmd" || continue
    mb=$((rss / 1024))
    if is_run "$cmd"; then
      # RSS misses compressed/swapped and GPU memory: a title showed 6.5 GB RSS at 20 GB phys_footprint
      fp=$(footprint -p "$pid" 2>/dev/null | awk '/phys_footprint:/{v=$2; u=$3; if(u=="GB")v*=1024; else if(u=="KB")v/=1024; print int(v); exit}')
      [ -n "$fp" ] && [ "$fp" -gt "$mb" ] && mb=$fp
      runs+=("$pid $mb $etime $cmd")
    elif is_build "$cmd"; then builds+=("$pid $mb $etime $cmd"); fi
  done < <(psx)

  # per-run cap (and fast polling above FAST_MB: a loading spike can add GB in seconds)
  for r in "${runs[@]+"${runs[@]}"}"; do
    set -- $r; pid=$1; mb=$2; shift 3
    if [ "$mb" -gt "$FAST_MB" ]; then
      nap=0.5
      now_f=$(date +%s)
      if [ $((now_f - last_fp_log)) -ge 10 ]; then last_fp_log=$now_f; log "HIGH footprint pid $pid ${mb}MB (> ${FAST_MB}MB, cap ${RUN_CAP_MB}MB), swap ${swap}MB, level $level :: ${*:0:100}"; fi
    fi
    [ "$mb" -gt "$RUN_CAP_MB" ] && kill_pid "$pid" "$mb" "run above ${RUN_CAP_MB}MB cap" "$*"
  done
  # start hold: no NEW title run while the machine is thrashing (run_sandboxed's title lock waits on this file)
  load1=$(sysctl -n vm.loadavg 2>/dev/null | awk '{print int($2)}'); load1=${load1:-0}
  if [ "$load1" -gt "$HOLD_LOAD" ] || [ "$swap" -gt "$HOLD_SWAP_MB" ] || [ "$(sysctl -n kern.memorystatus_vm_pressure_level)" -ge 2 ]; then
    [ "$holding" = 0 ] && log "START HOLD on: load ${load1} (> ${HOLD_LOAD}) or swap ${swap}MB (> ${HOLD_SWAP_MB}MB): new title runs wait"
    holding=1; echo "load=$load1 swap=${swap}MB since $(date '+%T')" > "$HOLD"
  elif [ "$holding" = 1 ]; then
    holding=0; rm -f "$HOLD"; log "START HOLD off: load ${load1}, swap ${swap}MB"
  fi
  # GPU test binaries must not run long: a hung compute test can stall WindowServer
  while read -r pid et cmd; do
    [ -z "$pid" ] && continue
    case "$cmd" in *claude*|*/zsh*|*/bash*|*gtimeout*|*clang*|*ninja*|*"go test"*) continue;; esac
    case "$cmd" in */mesa/tests/*|*/work/s3/*/tests/*|*kk-*/*test*|*_test\ *|*_test|*/tests/*/t.exe*|*agc_driver_*tests*|*/mesh/*test*|*atomics*test*)
      [ "$et" -gt "$GPU_TEST_MAX_S" ] && kill_pid "$pid" "?" "GPU/KK test running ${et}s > ${GPU_TEST_MAX_S}s" "$cmd";;
    esac
  done < <(psx | awk '{$2=""; print}' | grep -F "$ROOT")

  # one big run at a time: keep the oldest (largest etime), kill newer ones
  big=$(for r in "${runs[@]+"${runs[@]}"}"; do set -- $r; [ "$2" -gt "$BIG_MB" ] && echo "$3 $1 $2"; done | sort -rn)
  n=0
  while read -r et pid mb; do
    [ -z "$pid" ] && continue
    n=$((n + 1))
    if [ "$n" -gt "$MAX_BIG" ]; then
      cmd=$(ps -o command= -p "$pid" 2>/dev/null)
      kill_pid "$pid" "$mb" "more than $MAX_BIG big run(s) (>${BIG_MB}MB) at once; newest killed" "$cmd"
    fi
  done <<< "$big"

  # one PPSA30552 title process at a time, whatever its size (orphans whose wrapper/lock died included):
  # keep the oldest, kill newer ones. Matches the executable itself (first argv), not wrappers.
  tl=$(psx | awk '$4 ~ /(ppsa30552|ppsa21564)\/.*\/native[^\/ ]*$/ {print $3, $1}' | sort -rn)
  n=0
  while read -r et pid; do
    [ -z "$pid" ] && continue
    n=$((n + 1))
    [ "$n" -gt 1 ] && kill_pid "$pid" "?" "second PPSA30552 title process (one at a time); newest killed" "$(ps -o command= -p "$pid" 2>/dev/null)"
  done <<< "$tl"

  # offline GPU replays (rl: used by perf quiet below) ...
  rl=$(psx | awk '$4 ~ /\/agc_frame_replay$/ {print $3, $1}' | sort -rn)
  # ... and GPU-heavy processes besides the title: agc_frame_replay + mesa/KK/driver GPU test binaries; at most
  # MAX_GPU_EXTRA at once, oldest kept, newest killed (8 parallel replays starved title runs; 2026-11-16 panic)
  gl=$(psx | awk -v root="$ROOT" '{p=$1; e=$3; $1=$2=$3=""; c=substr($0,4); if (index(c, root) && (c ~ /\/agc_frame_replay( |$)/ || c ~ /\/mesa\/tests\/|\/work\/s3\/.*\/tests\/|\/kk-[^ ]*\/[^ ]*test|agc_driver_[^ ]*tests|\/mesh\/[^ ]*test|atomics[^ ]*test/) && c !~ /claude|\/zsh|\/bash|gtimeout|clang|ninja|go test/) print e, p}' | sort -rn)
  n=0
  while read -r et pid; do
    [ -z "$pid" ] && continue
    n=$((n + 1))
    [ "$n" -gt "$MAX_GPU_EXTRA" ] && kill_pid "$pid" "?" "more than $MAX_GPU_EXTRA GPU-heavy process(es) besides the title (replay/GPU test); newest killed" "$(ps -o command= -p "$pid" 2>/dev/null)"
  done <<< "$gl"

  # perf quiet mode: while work/guard/perf.quiet names a live title-run wrapper, our CPU-only builds/translators are
  # paused (SIGSTOP; never GPU/Metal processes) and new agc_frame_replay are killed; everything paused is resumed
  # (SIGCONT) when the run ends or the file goes. Paused pids are kept in work/guard/paused.list.
  QF=$(dirname "$LOG")/perf.quiet; PL=$(dirname "$LOG")/paused.list
  qpid=$(cat "$QF" 2>/dev/null)
  if [ -n "$qpid" ] && kill -0 "$qpid" 2>/dev/null; then
    for b in "${builds[@]+"${builds[@]}"}"; do
      set -- $b; bp=$1; shift 3
      case "$*" in *agc_frame_replay*|*native*|*Metal*|*metal*|*clangd*|*sourcekit*) continue;; esac
      grep -qx "$bp" "$PL" 2>/dev/null && continue
      kill -STOP "$bp" 2>/dev/null && echo "$bp" >> "$PL" && log "PAUSE pid $bp for perf run $qpid :: ${*:0:160}"
    done
    while read -r et rp; do
      [ -z "$rp" ] && continue
      [ "$et" -lt "$(( $(date +%s) - $(stat -f %m "$QF") ))" ] && kill_pid "$rp" "?" "agc_frame_replay started during a perf title run (perf quiet)" "$(ps -o command= -p "$rp" 2>/dev/null)"
    done <<< "$rl"
  elif [ -s "$PL" ]; then
    while read -r bp; do kill -CONT "$bp" 2>/dev/null; done < "$PL"
    log "RESUME $(wc -l < "$PL" | tr -d ' ') paused pid(s)"; : > "$PL"; rm -f "$(dirname "$LOG")/perf.quiet"
  fi

  # disk: log (and drop a flag file agents can check) when the data volume runs low
  now_d=$(date +%s)
  if [ $((now_d - ${last_disk:-0})) -ge 120 ]; then
    last_disk=$now_d
    # auto cleanup (user 2026-11-16): below AUTO_CLEAN_GB free, run tools-host/auto_cleanup.sh (at most every 30 min, one at a time)
    afree=$(df -g "$ROOT" 2>/dev/null | awk 'NR==2{print $4}')
    if [ -n "$afree" ] && [ "$afree" -lt "${AUTO_CLEAN_GB:-15}" ] && [ $((now_d - ${last_clean:-0})) -ge 1800 ] && ! pgrep -f 'tools-host/auto_cleanup.sh' >/dev/null; then
      last_clean=$now_d; log "auto cleanup started: ${afree} GB free (< ${AUTO_CLEAN_GB:-15} GB)"
      nohup "$ROOT/tools-host/auto_cleanup.sh" >/dev/null 2>&1 &
    fi
    free_gb=$(df -g "$ROOT" 2>/dev/null | awk 'NR==2{print $4}')
    if [ -n "$free_gb" ] && [ "$free_gb" -lt "${GUARD_DISK_MIN_GB:-8}" ]; then
      log "DISK LOW: ${free_gb} GB free (< ${GUARD_DISK_MIN_GB:-8} GB) - clean finished agents' caches/builds"
      echo "$free_gb" > "$(dirname "$LOG")/disk.low"
    else
      rm -f "$(dirname "$LOG")/disk.low"
    fi
  fi

  # pressure handling
  if [ "$level" -ge 4 ] || [ "$swap" -gt "$SWAP_MB" ]; then
    # builds first (cheap to resume: ninja is incremental); a title/test run only if no build is left (main 17:15)
    victim=$(for b in "${builds[@]+"${builds[@]}"}"; do set -- $b; echo "$2 $1"; done | sort -rn | head -1)
    [ -z "$victim" ] && victim=$(for r in "${runs[@]+"${runs[@]}"}"; do set -- $r; echo "$2 $1"; done | sort -rn | head -1)
    if [ -n "$victim" ]; then
      set -- $victim; cmd=$(ps -o command= -p "$2" 2>/dev/null)
      kill_pid "$2" "$1" "memory pressure level $level, swap ${swap}MB" "$cmd"
      sleep 5
    fi
  elif [ "$level" -ge 2 ]; then
    now=$(date +%s)
    if [ $((now - last_warn)) -ge 30 ]; then
      for b in "${builds[@]+"${builds[@]}"}"; do set -- $b; renice 20 -p "$1" >/dev/null 2>&1; taskpolicy -b -p "$1" >/dev/null 2>&1; done
      log "pressure WARN (swap ${swap}MB): deprioritised ${#builds[@]} build process(es)"
      last_warn=$now
    fi
  fi
  sleep $nap
done
