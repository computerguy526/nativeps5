#!/bin/bash
# Build the compiled pipeline caches for a title LOCALLY from its committed pipeline list and your own dump, so
# the first visit to a scene does not stutter on compiles.
#   scripts/prepare-pipelines.sh <title id>
# Reads games/<title id>/pipelines.kyplst (keys, hashes and state only) and titles/<title id>.env (your binary and
# dump). The back end reads each listed shader's code from YOUR copy of the game, uses it only when the hashes
# match, builds every permutation and pipeline through KosmicKrisp, and exits before the game runs.
# What it fills (all under NPS5_SCRATCH, all git-ignored, never committed): the Vulkan driver cache, the program
# cache, the KosmicKrisp pipeline archive and the private OS Metal cache.
# It is a GPU-using run: it goes through run-title.sh (lock, memory limit, one GPU process). About 1.5 minutes
# for 1,260 pipelines on an M4 Pro. Rerun it after a KosmicKrisp change that alters MSL; the list itself survives.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5-env.sh"
[ $# -eq 1 ] || { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
ID="$1"; P="$NPS5_PUBLIC_DIR/games/$ID"; LIST="$P/pipelines.kyplst"
[ -f "$LIST" ] || { echo "prepare-pipelines: no $LIST" >&2; exit 2; }
T="$NPS5_PUBLIC_DIR/titles/$ID.env"
[ -f "$T" ] || { echo "prepare-pipelines: no $T (copy titles/example.env)" >&2; exit 2; }
APP0=""; . "$T"
VER="$(awk -F'\t' '$1=="pipeline_list_app_ver"{print $2}' "$P/game.tsv")"
[ -n "$VER" ] || { echo "prepare-pipelines: games/$ID/game.tsv has no pipeline_list_app_ver" >&2; exit 2; }
NPS5_RUN_REASON="${NPS5_RUN_REASON:-pipeline prepare from games/$ID/pipelines.kyplst}" \
  exec "$NPS5_PUBLIC_DIR/scripts/run-title.sh" "$ID" 180 \
  "KYTY_PIPELINE_LIST=$LIST KYTY_PIPELINE_LIST_PREPARE=1 KYTY_PIPELINE_LIST_APP_VER=$VER KYTY_PIPELINE_LIST_EXE=$APP0/eboot.bin"
