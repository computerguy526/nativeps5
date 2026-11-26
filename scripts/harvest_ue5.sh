#!/bin/bash
# Build the warm shader list of a UE5 title (cache ... code=ue5) from the pipeline / shader journals of real play sessions.
#   scripts/harvest_ue5.sh <title id> --game <app0 folder> [--out DIR] [--write] [--code-store FILE] [--require-runtime PCT] <cache dir>...
# Per cache dir: `kyty_pipeline_list export --game <app0> ... --runtime-code` (a journaled code found nowhere else becomes a <runtime> shader), then
# `merge --allow-partial` of every export, `check --game` (exit 2 = title/version mismatch; prints "runtime: X resolved, Y pending"; with --code-store the store resolves them;
# --require-runtime PCT, default 0 = report only, makes pending above 100-PCT percent exit 1), then scripts/verify-pipeline-list.sh
# (no game code in the list, runtime shaders covered by the journals; writes <list>.verified). Files go to --out
# (default $NPS5_SCRATCH/cache/<id>/harvest-ue5): pipelines.kyplst, pipelines.kyplst.verified, counts.txt. --write then copies the
# list and its .verified into games/<id>/ (only after every step passed). A cache dir holds <id>[.session_<tag>].shaders.journal (a .static. one is skipped) and the
# matching <id>[.name].pipelines.journal; dirs without such a pair are skipped (named). Nothing from the game is printed or stored.
# Tool: $NPS5_KYPLST_TOOL, else <kyty build>/kyty_pipeline_list. No GPU, no title run. Exit: 0 ok, 1 a step failed, 2 usage.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/nps5-env.sh"
usage() { sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 1 ] || usage
TID="$1"; shift
[[ "$TID" =~ ^[A-Z]{4}[0-9]{5}$ ]] || { echo "harvest_ue5: title id expected (PPSA12345), got '$TID'" >&2; exit 2; }
GAME=""; OUT=""; WRITE=0; STORE=""; REQ=0; DIRS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --game) GAME="${2:?--game needs a folder}"; shift 2;;
    --out) OUT="${2:?--out needs a folder}"; shift 2;;
    --write) WRITE=1; shift;;
    --code-store) STORE="${2:?--code-store needs a file}"; shift 2;;
    --require-runtime) REQ="${2:?--require-runtime needs a percentage}"; shift 2;;
    -h|--help) usage;;
    -*) echo "harvest_ue5: unknown option $1" >&2; exit 2;;
    *) DIRS+=("$1"); shift;;
  esac
done
[[ "$REQ" =~ ^[0-9]+$ ]] && [ "$REQ" -le 100 ] || { echo "harvest_ue5: --require-runtime is a percentage 0..100" >&2; exit 2; }
[ ${#DIRS[@]} -gt 0 ] || { echo "harvest_ue5: no cache dirs given" >&2; exit 2; }
[ -n "$GAME" ] && [ -d "$GAME" ] || { echo "harvest_ue5: --game <app0 folder> is required (the verify step reads your copy of the game)" >&2; exit 2; }
TOOL="${NPS5_KYPLST_TOOL:-$NPS5_KYTY_DIR/build-arm64/kyty_pipeline_list}"
[ -x "$TOOL" ] || { echo "harvest_ue5: no tool at $TOOL (build the kyty fork's kyty_pipeline_list, or set NPS5_KYPLST_TOOL)" >&2; exit 2; }
XSTORE=(); [ -z "$STORE" ] || XSTORE=(--code-store "$STORE")
GAMES="${NPS5_GAMES_DIR:-$NPS5_PUBLIC_DIR/games}"
[ -z "$OUT" ] && OUT="$NPS5_SCRATCH/cache/$TID/harvest-ue5"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT";; esac
mkdir -p "$OUT"
rm -f "$OUT"/j*.kyplst "$OUT/pipelines.kyplst" "$OUT/pipelines.kyplst.verified" "$OUT/counts.txt"

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
CK=("$TOOL" check "$LIST" --game "$GAME")
[ -n "$STORE" ] && CK+=(--code-store "$STORE")
[ "$REQ" -gt 0 ] && CK+=(--require-runtime "$REQ")
CKOUT="$("${CK[@]}" 2>&1)" || { echo "$CKOUT"; echo "harvest_ue5: check FAILED (runtime shaders below --require-runtime $REQ %?)" >&2; exit 1; }
echo "$CKOUT" | tail -3
INFO="$("$TOOL" info "$LIST")"
{ echo "$INFO"; echo "$CKOUT" | grep '^runtime:' || true; echo "inputs: $n journal pair(s) from $((${#DIRS[@]} - skipped)) dir(s)"; } > "$OUT/counts.txt"
VOUT="$(bash "$HERE/verify-pipeline-list.sh" "$LIST" "$GAME" "${JOURNALS[@]}" 2>&1)" \
  || { echo "$VOUT" | tail -3; echo "harvest_ue5: verify FAILED; do not commit this list" >&2; exit 1; }
echo "verified: $LIST.verified"
echo "counts ($OUT/counts.txt):"; sed 's/^/  /' "$OUT/counts.txt"
if [ "$WRITE" = 1 ]; then
  [ -d "$GAMES/$TID" ] || { echo "harvest_ue5: no $GAMES/$TID" >&2; exit 1; }
  cp "$LIST" "$GAMES/$TID/pipelines.kyplst.tmp" && mv "$GAMES/$TID/pipelines.kyplst.tmp" "$GAMES/$TID/pipelines.kyplst"
  cp "$LIST.verified" "$GAMES/$TID/pipelines.kyplst.verified"
  echo "wrote games/$TID/pipelines.kyplst and .verified"
else
  echo "not adopted (--write copies the list and .verified into games/$TID/)"
fi
