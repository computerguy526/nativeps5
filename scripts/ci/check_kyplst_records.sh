#!/bin/bash
# Every games/*/pipelines.kyplst needs a pipelines.kyplst.verified whose sha256 is the list's and whose result line is
# the no-game-code proof. (The proof itself, scripts/verify-pipeline-list.sh, needs the player's own game dump and the
# kyty tool, so it cannot run in CI; a maintainer reruns it before taking a list.)
cd "$(git rev-parse --show-toplevel)" || exit 2
rc=0; n=0
bash -n scripts/verify-pipeline-list.sh || rc=1
for f in games/*/pipelines.kyplst games/*/contrib/*.kyplst; do
  [ -f "$f" ] || continue
  n=$((n+1)); v="$f.verified"
  if [ ! -f "$v" ]; then echo "kyplst: $f has no $v"; rc=1; continue; fi
  want=$(sed -n 's/^sha256=//p' "$v"); have=$(shasum -a 256 "$f" | cut -d' ' -f1)
  [ "$want" = "$have" ] || { echo "kyplst: $v sha256 does not match $f"; rc=1; }
  grep -qx 'result=verify: no shader code in the list' "$v" || { echo "kyplst: $v lacks the no-shader-code result line"; rc=1; }
done
[ $rc = 0 ] && echo "kyplst records: ok ($n list(s))"
exit $rc
