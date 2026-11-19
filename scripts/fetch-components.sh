#!/bin/bash
# Clone the component repositories at the commits in components.lock into $NPS5_ROOT.
# A release lock names an annotated tag (sixth field) and the commit it points at (seventh): the clone is refused when the
# tag does not point at that commit, so a moved or re-created tag cannot change what you build.
#   scripts/fetch-components.sh [ps5recomp] [kyty] [anyps5]   (NPS5_FORK_BASE=<URL prefix> for a mirror or your own forks)
# Without arguments all three are cloned: a title run needs every one of them (BUILDING.md).
# A component that already exists is left alone: its commit is printed, and a commit other than the lock's is named
# (your own branch is fine; BUILDING.md's verified table is about the lock's commits).
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5-env.sh"
NPS5_FORK_BASE="${NPS5_FORK_BASE:-https://github.com/computerguy526}"
want="${*:-ps5recomp kyty anyps5}"
# A clone is made in <dir>.fetch-<pid> and moved into place only when it is verified: a refused tag or a failed fetch never
# leaves a <dir> behind that a second run would take as "exists" (v1.1 left the no-checkout clone of a refused tag there).
tmpd=""
trap '[ -z "$tmpd" ] || rm -rf "$tmpd"' EXIT
while IFS='|' read -r name lic up base fork branch commit; do
  name="$(echo $name)"
  [ -n "$name" ] || continue
  case " $want " in *" $name "*) ;; *) continue;; esac
  fork="$(echo $fork)"; fork="${fork/\$\{NPS5_FORK_BASE\}/$NPS5_FORK_BASE}"; branch="$(echo $branch)"; commit="$(echo $commit)"; up="$(echo $up)"
  case "$name" in ps5recomp) dir="$NPS5_RECOMP_DIR";; kyty) dir="$NPS5_KYTY_DIR";; anyps5) dir="$NPS5_ANYPS5_DIR";; *) continue;; esac
  if [ -d "$dir/.git" ]; then
    have="$(git -C "$dir" rev-parse HEAD)"
    case "$commit" in "$have"*) echo "$name: exists at ${have:0:10}, the commit of the lock";;
                      *) echo "$name: exists at ${have:0:10}; DIFFERS from the lock ($commit)";; esac
    continue
  fi
  [ ! -e "$dir" ] || { echo "$name: $dir exists but is not a git checkout; move it away first" >&2; exit 1; }
  echo "$name: cloning $fork ($branch)"
  tmpd="$dir.fetch-$$"; rm -rf "$tmpd"; mkdir -p "$(dirname "$dir")"
  case "$branch" in
    v[0-9]*)  # a release lock: the sixth field is an annotated tag, the seventh the commit it must point at (immutable pin)
      [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "$name: the lock has a tag but no 40-hex commit" >&2; exit 1; }
      git clone --no-checkout "$fork" "$tmpd"
      git -C "$tmpd" fetch -q origin "+refs/tags/$branch:refs/tags/$branch"
      [ "$(git -C "$tmpd" cat-file -t "refs/tags/$branch")" = tag ] || { echo "$name: $branch is not an annotated tag; refusing" >&2; exit 1; }
      tagged="$(git -C "$tmpd" rev-parse "refs/tags/$branch^{commit}")"
      [ "$tagged" = "$commit" ] || { echo "$name: tag $branch is $tagged, the lock pins $commit; refusing" >&2; exit 1; }
      git -C "$tmpd" checkout -q --detach "$commit"
      [ "$(git -C "$tmpd" rev-parse HEAD)" = "$commit" ] || { echo "$name: checkout of $commit failed" >&2; exit 1; }
      git -C "$tmpd" remote add upstream "$up"
      verified="tag $branch = ${commit:0:10} verified";;
    *)
      git clone --branch "$branch" "$fork" "$tmpd"
      git -C "$tmpd" remote add upstream "$up"
      verified="branch $branch"
      case "$commit" in "("*|"") ;; *) git -C "$tmpd" checkout -q "$commit"
           [ "$(git -C "$tmpd" rev-parse HEAD)" = "$(git -C "$tmpd" rev-parse "$commit^{commit}")" ] || { echo "$name: checkout of $commit failed" >&2; exit 1; }
           verified="branch $branch at ${commit:0:10}";; esac;;
  esac
  mv "$tmpd" "$dir"; tmpd=""
  echo "$name: $verified"
  # Submodules are fetched by the build step of the component that needs them (scripts/build.sh), not here.
done < <(grep -v '^#' "$NPS5_PUBLIC_DIR/components.lock")
