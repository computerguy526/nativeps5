#!/bin/bash
# Build the compiled pipeline caches for a title LOCALLY from its committed pipeline list and your own dump, so
# the first visit to a scene does not stutter on compiles.
#   scripts/prepare-pipelines.sh <title id>          ==   scripts/nps5 cache prepare <title id>
# Kept for the old call; the work is `nps5 cache prepare` (scripts/nps5lib/cache.py): it reads
# games/<title id>/pipelines.kyplst (keys, hashes and state only), the `cache` row of game.tsv and
# titles/<title id>.env (your binary and dump), starts the title once through `nps5 run` (lock, memory limit, one
# GPU process) in the mode that builds every listed pipeline and exits before the game runs, and records the
# result under NPS5_SCRATCH (git-ignored, never committed). `nps5 cache status <title id>` says whether the caches
# match the current stack. About 1.5 minutes for 1,260 pipelines on an M4 Pro.
set -euo pipefail
[ $# -eq 1 ] || { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5" cache prepare "$1"
