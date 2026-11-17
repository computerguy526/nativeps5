#!/bin/bash
# Prove that a pipeline list holds no game code, and record the proof next to it.
#   scripts/verify-pipeline-list.sh games/<title id>/pipelines.kyplst <your dump's app0 folder> [shader journal ...]
# Runs the kyty fork's `kyty_pipeline_list verify`: it reads the listed shaders' code from YOUR copy of the game
# (read-only) and fails if any 64-byte window of that code, or of a journaled shader's code, occurs in the list
# or in any of its decompressed sections. On success it writes <list>.verified (hash of the list, result line,
# counts), which scripts/check.sh requires before it accepts the list. Nothing from the game is written anywhere.
# Tool: $NPS5_KYPLST_TOOL, else <kyty build>/kyty_pipeline_list. No GPU is used.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5-env.sh"
[ $# -ge 2 ] || { sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
LIST="$1"; GAME="$2"; shift 2
TOOL="${NPS5_KYPLST_TOOL:-$NPS5_KYTY_DIR/build-arm64/kyty_pipeline_list}"
[ -x "$TOOL" ] || { echo "verify-pipeline-list: no tool at $TOOL (build the kyty fork's pipeline-list tool, or set NPS5_KYPLST_TOOL)" >&2; exit 2; }
[ -f "$LIST" ] && [ -d "$GAME" ] || { echo "verify-pipeline-list: need a list file and a game folder" >&2; exit 2; }
J=(); for j in "$@"; do J+=(--shaders "$j"); done
INFO="$("$TOOL" info "$LIST")"
OUT="$("$TOOL" verify "$LIST" --game "$GAME" ${J[@]+"${J[@]}"})" || { echo "$OUT"; echo "verify-pipeline-list: FAILED; do not commit this list" >&2; exit 1; }
echo "$OUT"
RESULT="$(echo "$OUT" | tail -1)"
[ "$RESULT" = "verify: no shader code in the list" ] || { echo "verify-pipeline-list: unexpected result line" >&2; exit 1; }
{
  echo "# Record of the no-game-code proof for $(basename "$LIST"), written by scripts/verify-pipeline-list.sh."
  echo "# The proof reads the listed shaders' code from the verifier's own copy of the game and finds no 64-byte window"
  echo "# of it in the file or in any decompressed section. scripts/check.sh accepts the list only with this record."
  echo "sha256=$(shasum -a 256 "$LIST" | cut -d' ' -f1)"
  echo "result=$RESULT"
  echo "title=$(echo "$INFO" | sed -n 's/^ *title \([^,]*\), version \([^,]*\),.*/\1 \2/p')"
  echo "counts=$(echo "$INFO" | sed -n 's/^ *\(shaders [0-9].*\)$/\1/p' | sed 's/;.*/)/'), $(echo "$INFO" | sed -n 's/^ *\(permutations [0-9]*, pipelines [0-9]*\).*/\1/p')"
  echo "tool=kyty_pipeline_list verify (kyty fork, pipeline-list tool)"
  echo "date=$(date -u +%Y-%m-%d)"
} > "$LIST.verified"
echo "verify-pipeline-list: wrote $LIST.verified"
