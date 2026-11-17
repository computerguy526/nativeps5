#!/bin/bash
# Clone the component repositories at the commits in components.lock into $NPS5_ROOT.
#   scripts/fetch-components.sh [ps5recomp] [kyty] [anyps5]   (NPS5_FORK_BASE=<URL prefix> for a mirror or your own forks)
# Without arguments all three are cloned: a title run needs every one of them (BUILDING.md).
# A component that already exists is left alone: its commit is printed, and a commit other than the lock's is named
# (your own branch is fine; BUILDING.md's verified table is about the lock's commits).
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5-env.sh"
NPS5_FORK_BASE="${NPS5_FORK_BASE:-https://github.com/computerguy526}"
want="${*:-ps5recomp kyty anyps5}"
grep -v '^#' "$NPS5_PUBLIC_DIR/components.lock" | while IFS='|' read -r name lic up base fork branch commit; do
  name="$(echo $name)"
  case " $want " in *" $name "*) ;; *) continue;; esac
  fork="$(echo $fork)"; fork="${fork/\$\{NPS5_FORK_BASE\}/$NPS5_FORK_BASE}"; branch="$(echo $branch)"; commit="$(echo $commit)"; up="$(echo $up)"
  case "$name" in ps5recomp) dir="$NPS5_RECOMP_DIR";; kyty) dir="$NPS5_KYTY_DIR";; anyps5) dir="$NPS5_ANYPS5_DIR";; *) continue;; esac
  if [ -d "$dir/.git" ]; then
    have="$(git -C "$dir" rev-parse HEAD)"
    case "$commit" in "$have"*) echo "$name: exists at ${have:0:10}, the commit of the lock";;
                      *) echo "$name: exists at ${have:0:10}; DIFFERS from the lock ($commit)";; esac
    continue
  fi
  echo "$name: cloning $fork ($branch)"
  git clone --branch "$branch" "$fork" "$dir"
  git -C "$dir" remote add upstream "$up"
  case "$commit" in "("*|"") ;; *) git -C "$dir" checkout -q "$commit"
       [ "$(git -C "$dir" rev-parse HEAD)" = "$(git -C "$dir" rev-parse "$commit^{commit}")" ] || { echo "$name: checkout of $commit failed" >&2; exit 1; };; esac
  # Submodules are fetched by the build step of the component that needs them (scripts/build.sh), not here.
done
