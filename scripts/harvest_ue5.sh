#!/bin/bash
# Build the warm shader list of a UE5 title (cache ... code=ue5) from the pipeline / shader journals of real play sessions.
#   scripts/harvest_ue5.sh <title id> --game <app0 folder> [--out DIR] [--write] [--code-store FILE] [--require-runtime PCT] [--drop-hashes FILE] [--probe [--probe-rt auto|on|off] [--probe-jobs N] [--probe-timeout SEC]] <cache dir>...
# Per cache dir: `kyty_pipeline_list export --game <app0> ... --runtime-code` (a journaled code found nowhere else becomes a <runtime> shader), then
# `merge --allow-partial` of every export, `check --game` (exit 2 = title/version mismatch; prints "runtime: X resolved, Y pending"; with --code-store the store resolves them;
# --require-runtime PCT, default 0 = report only, makes pending above 100-PCT percent exit 1), then scripts/verify-pipeline-list.sh
# (no game code in the list, runtime shaders covered by the journals; writes <list>.verified). Files go to --out
# (default $NPS5_SCRATCH/cache/<id>/harvest-ue5): pipelines.kyplst, pipelines.kyplst.verified, counts.txt. --write then copies the
# list and its .verified into games/<id>/ (only after every step passed). A cache dir holds <id>[.session_<tag>].shaders.journal (a .static. one is skipped) and the
# matching <id>[.name].pipelines.journal; dirs without such a pair are skipped (named). Nothing from the game is printed or stored.
# --drop-hashes FILE: programs the compile probe found unbuildable (scripts/ue5_probe_hashes.sh output: 0x<hash> lines) are removed from the merged list
# --probe: the list-builder compile probe. Every program of the merged list is translated through the title's own recompiler path in
# CHILD processes (kyty_spv_dump --probe, one permutation per child, so a fatal EXIT / assert / signal / hang kills the child only), from the
# program caches (<id>*.programs.bin) of the cache dirs; programs it refuses are dropped like --drop-hashes, each hash with its reason in
# --out/probe.tsv (all results: probe.log; listed programs found in no cache: unprobed.txt, kept). --probe-rt on drops ray-tracing
# (BVH) programs too; auto (default) does so only when the title's games/<id>/game.tsv turns ray tracing off (r.RayTracing.Enable=0), so a
# title that runs ray tracing is not affected. Tool: $NPS5_SPVDUMP_TOOL, else <kyty build>/kyty_spv_dump. No recompiler change.
# The rows go to --out/dropped.tsv and the count to counts.txt. Tool: $NPS5_KYPLST_TOOL, else <kyty build>/kyty_pipeline_list. No GPU, no title run. Exit: 0 ok, 1 a step failed, 2 usage.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/nps5-env.sh"
usage() { awk 'NR>1 && /^#/ {print; next} NR>1 {exit}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 1 ] || usage
case "$1" in -h|--help) awk 'NR>1 && /^#/ {print; next} NR>1 {exit}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;; esac
TID="$1"; shift
[[ "$TID" =~ ^[A-Z]{4}[0-9]{5}$ ]] || { echo "harvest_ue5: title id expected (PPSA12345), got '$TID'" >&2; exit 2; }
val() { [ $# -ge 2 ] && [ -n "$2" ] || { echo "harvest_ue5: $1 needs a value" >&2; exit 2; }; }   # a usage error is exit 2, never the shell's own abort (1)
GAME=""; OUT=""; WRITE=0; STORE=""; REQ=0; DROP=""; DIRS=(); PROBE=0; PROBE_RT=auto; PROBE_JOBS=4; PROBE_TIMEOUT=120
while [ $# -gt 0 ]; do
  case "$1" in
    --game) val "$@"; GAME="$2"; shift 2;;
    --out) val "$@"; OUT="$2"; shift 2;;
    --write) WRITE=1; shift;;
    --code-store) val "$@"; STORE="$2"; shift 2;;
    --require-runtime) val "$@"; REQ="$2"; shift 2;;
    --drop-hashes) val "$@"; DROP="$2"; shift 2;;
    --probe) PROBE=1; shift;;
    --probe-rt) val "$@"; PROBE_RT="$2"; shift 2;;
    --probe-jobs) val "$@"; PROBE_JOBS="$2"; shift 2;;
    --probe-timeout) val "$@"; PROBE_TIMEOUT="$2"; shift 2;;
    -h|--help) usage;;
    -*) echo "harvest_ue5: unknown option $1" >&2; exit 2;;
    *) DIRS+=("$1"); shift;;
  esac
done
[[ "$REQ" =~ ^[0-9]+$ ]] && [ "$REQ" -le 100 ] || { echo "harvest_ue5: --require-runtime is a percentage 0..100" >&2; exit 2; }
[ "$REQ" -eq 0 ] || [ -n "$STORE" ] || { echo "harvest_ue5: --require-runtime $REQ needs --code-store FILE (without the code store every runtime shader stays pending)" >&2; exit 2; }
[ ${#DIRS[@]} -gt 0 ] || { echo "harvest_ue5: no cache dirs given" >&2; exit 2; }
case "$PROBE_RT" in auto|on|off) ;; *) echo "harvest_ue5: --probe-rt is auto, on or off" >&2; exit 2;; esac
[[ "$PROBE_JOBS" =~ ^[0-9]+$ && "$PROBE_TIMEOUT" =~ ^[0-9]+$ ]] || { echo "harvest_ue5: --probe-jobs and --probe-timeout are numbers" >&2; exit 2; }
[ -z "$DROP" ] || [ -f "$DROP" ] || { echo "harvest_ue5: --drop-hashes $DROP is not a file" >&2; exit 2; }
[ -n "$GAME" ] && [ -d "$GAME" ] || { echo "harvest_ue5: --game <app0 folder> is required (the verify step reads your copy of the game)" >&2; exit 2; }
TOOL="${NPS5_KYPLST_TOOL:-$NPS5_KYTY_DIR/build-arm64/kyty_pipeline_list}"
[ -x "$TOOL" ] || { echo "harvest_ue5: no tool at $TOOL (build the kyty fork's kyty_pipeline_list, or set NPS5_KYPLST_TOOL)" >&2; exit 2; }
XSTORE=(); [ -z "$STORE" ] || XSTORE=(--code-store "$STORE")
GAMES="${NPS5_GAMES_DIR:-$NPS5_PUBLIC_DIR/games}"
[ -z "$OUT" ] && OUT="$NPS5_SCRATCH/cache/$TID/harvest-ue5"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT";; esac
mkdir -p "$OUT"
rm -f "$OUT/probe.tsv" "$OUT/probe.log" "$OUT/unprobed.txt" "$OUT/probe.hashes" "$OUT/drop.hashes" "$OUT"/j*.kyplst "$OUT/pipelines.kyplst" "$OUT/pipelines.kyplst.verified" "$OUT/counts.txt" "$OUT/dropped.tsv"

LISTS=(); JOURNALS=(); n=0; skipped=0
for d in "${DIRS[@]}"; do
  found=0
  for sj in "$d"/"$TID"*.shaders.journal; do
    [ -f "$sj" ] || continue
    case "$(basename "$sj")" in "$TID".static.*) continue;; esac     # a static journal is not played: skipped
    pj="${sj%.shaders.journal}.pipelines.journal"
    [ -f "$pj" ] || continue
    found=1; n=$((n + 1)); out="$OUT/j$n.kyplst"
    if ! text="$("$TOOL" export --out "$out" --game "$GAME" --shaders "$sj" --pipelines "$pj" --runtime-code ${XSTORE[@]+"${XSTORE[@]}"} --producer "nps5-harvest-ue5-$n" 2>&1)"; then
      echo "$text"; echo "harvest_ue5: export of $(basename "$d")/$(basename "$sj") FAILED" >&2; exit 1
    fi
    echo "export $n ($(basename "$d")): $(echo "$text" | tail -1)"
    LISTS+=("$out"); JOURNALS+=("$sj")
  done
  [ "$found" = 1 ] || { echo "skipped $(basename "$d"): no $TID*.shaders.journal + .pipelines.journal pair"; skipped=$((skipped + 1)); }
done
[ ${#LISTS[@]} -gt 0 ] || { echo "harvest_ue5: no usable journals in ${#DIRS[@]} dir(s)" >&2; exit 1; }

LIST="$OUT/pipelines.kyplst"
"$TOOL" merge --allow-partial --out "$LIST" "${LISTS[@]}" || { echo "harvest_ue5: merge FAILED" >&2; exit 1; }
DROPLINE=""; PROBELINE=""
DROPFILE=""
if [ "$PROBE" = 1 ]; then
  SPV="${NPS5_SPVDUMP_TOOL:-$NPS5_KYTY_DIR/build-arm64/kyty_spv_dump}"
  [ -x "$SPV" ] || { echo "harvest_ue5: no probe tool at $SPV (build the kyty fork's kyty_spv_dump, or set NPS5_SPVDUMP_TOOL)" >&2; exit 2; }
  RTFLAG=()
  case "$PROBE_RT" in
    on) RTFLAG=(--rt);;
    auto) if grep -q -E '^env[[:space:]].*r\.RayTracing\.Enable=0' "$GAMES/$TID/game.tsv" 2>/dev/null; then RTFLAG=(--rt); fi;;
  esac
  PROGS=()
  for d in "${DIRS[@]}"; do for f in "$d"/"$TID"*.programs.bin; do [ -f "$f" ] && PROGS+=(--programs "$f"); done; done
  [ ${#PROGS[@]} -gt 0 ] || { echo "harvest_ue5: --probe found no $TID*.programs.bin in the cache dirs" >&2; exit 1; }
  "$TOOL" hashes "$LIST" > "$OUT/probe.hashes" || { echo "harvest_ue5: hashes FAILED" >&2; exit 1; }
  PROBELINE="$("$SPV" --probe --hashes "$OUT/probe.hashes" "${PROGS[@]}" --out "$OUT/probe.tsv" --log "$OUT/probe.log" --unprobed "$OUT/unprobed.txt" \
    --jobs "$PROBE_JOBS" --timeout "$PROBE_TIMEOUT" ${RTFLAG[@]+"${RTFLAG[@]}"} 2>"$OUT/probe.err" | tail -1)" \
    || { tail -5 "$OUT/probe.err"; echo "harvest_ue5: probe FAILED" >&2; exit 1; }
  echo "$PROBELINE (ray tracing programs dropped: $([ ${#RTFLAG[@]} -gt 0 ] && echo yes || echo no))"
  DROPFILE="$OUT/drop.hashes"; cat "$OUT/probe.tsv" > "$DROPFILE"
fi
if [ -n "$DROP" ]; then if [ -n "$DROPFILE" ]; then cat "$DROP" >> "$DROPFILE"; else DROPFILE="$DROP"; fi; fi
if [ -n "$DROPFILE" ]; then
  DROPLINE="$("$TOOL" drop --list "$LIST" --hashes "$DROPFILE" --out "$LIST.drop" --dropped "$OUT/dropped.tsv" 2>&1)" \
    || { echo "$DROPLINE"; echo "harvest_ue5: drop FAILED" >&2; exit 1; }
  mv "$LIST.drop" "$LIST"; echo "$DROPLINE" | tail -1
fi
CK=("$TOOL" check "$LIST" --game "$GAME")
[ -n "$STORE" ] && CK+=(--code-store "$STORE")
[ "$REQ" -gt 0 ] && CK+=(--require-runtime "$REQ")
CKOUT="$("${CK[@]}" 2>&1)" || { echo "$CKOUT"; echo "harvest_ue5: check FAILED (runtime shaders below --require-runtime $REQ %?)" >&2; exit 1; }
echo "$CKOUT" | tail -3
INFO="$("$TOOL" info "$LIST")"
{ echo "$INFO"; [ -z "$PROBELINE" ] || echo "$PROBELINE"; [ -z "$DROPLINE" ] || echo "dropped: $(echo "$DROPLINE" | tail -1)"; echo "$CKOUT" | grep '^runtime:' || true; echo "inputs: $n journal pair(s) from $((${#DIRS[@]} - skipped)) dir(s)"; } > "$OUT/counts.txt"
VOUT="$(bash "$HERE/verify-pipeline-list.sh" "$LIST" "$GAME" "${JOURNALS[@]}" 2>&1)" \
  || { echo "$VOUT" | tail -3; echo "harvest_ue5: verify FAILED; do not commit this list" >&2; exit 1; }
echo "verified: $LIST.verified"
echo "counts ($OUT/counts.txt):"; sed 's/^/  /' "$OUT/counts.txt"
if [ "$WRITE" = 1 ]; then
  [ -d "$GAMES/$TID" ] || { echo "harvest_ue5: no $GAMES/$TID" >&2; exit 1; }
  G="$GAMES/$TID"
  # stage both files under temporary names first, then move both: a failure in between never leaves a new list with the old record
  cp "$LIST" "$G/pipelines.kyplst.new" && cp "$LIST.verified" "$G/pipelines.kyplst.verified.new" \
    || { rm -f "$G/pipelines.kyplst.new" "$G/pipelines.kyplst.verified.new"; echo "harvest_ue5: could not stage the list in $G" >&2; exit 1; }
  [ ! -f "$G/pipelines.kyplst" ] || cp "$G/pipelines.kyplst" "$G/pipelines.kyplst.bak"
  [ ! -f "$G/pipelines.kyplst.verified" ] || cp "$G/pipelines.kyplst.verified" "$G/pipelines.kyplst.verified.bak"
  mv "$G/pipelines.kyplst.verified.new" "$G/pipelines.kyplst.verified" && mv "$G/pipelines.kyplst.new" "$G/pipelines.kyplst"
  echo "wrote games/$TID/pipelines.kyplst and .verified"
else
  echo "not adopted (--write copies the list and .verified into games/$TID/)"
fi
