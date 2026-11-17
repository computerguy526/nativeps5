#!/bin/bash
# Clone the component repositories at the commits in components.lock into $NPS5_ROOT.
#   scripts/fetch-components.sh [ps5recomp] [kyty]      (NPS5_FORK_BASE=<URL prefix> for a mirror or your own forks)
# AnyPS5 is optional, not public and never fetched here (components.lock).
# A component that already exists is left alone (its commit is printed and compared with the lock).
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5-env.sh"
NPS5_FORK_BASE="${NPS5_FORK_BASE:-https://github.com/computerguy526}"
want="${*:-ps5recomp kyty}"
grep -v '^#' "$NPS5_PUBLIC_DIR/components.lock" | while IFS='|' read -r name lic up base fork branch commit; do
  name="$(echo $name)"
  case " $want " in *" $name "*) ;; *) continue;; esac
  fork="$(echo $fork)"; fork="${fork/\$\{NPS5_FORK_BASE\}/$NPS5_FORK_BASE}"; branch="$(echo $branch)"; commit="$(echo $commit)"; up="$(echo $up)"
  case "$name" in ps5recomp) dir="$NPS5_RECOMP_DIR";; kyty) dir="$NPS5_KYTY_DIR";; *) continue;; esac
  if [ -d "$dir/.git" ]; then
    echo "$name: exists at $(git -C "$dir" rev-parse --short HEAD) (lock: $commit)"; continue
  fi
  echo "$name: cloning $fork ($branch)"
  git clone --branch "$branch" "$fork" "$dir"
  git -C "$dir" remote add upstream "$up"
  case "$commit" in "("*|"") ;; *) git -C "$dir" checkout -q "$commit";; esac
  # Submodules are fetched by the build step of the component that needs them (scripts/build.sh), not here.
done
