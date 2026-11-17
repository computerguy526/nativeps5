#!/bin/bash
# tests/compare_env.sh: compare the env word set `games/game.py PPSA21564 --env` produces with what the current canonical scripts export.
#   kyty:   work/kyty/k4run.sh        vs  game.py PPSA21564 --env --gpu kyty   --scene worldmap
#   anyps5: work/ppsa21564/run_astro.sh vs game.py PPSA21564 --env --gpu anyps5 --scene worldmap
# The scripts are NOT run: transformed copies (scratch dir) print NPS5_EXTRA_ENV / the inner env string instead of calling the sandbox.
# Ignored on both sides: words whose value is a machine path, and the runner words listed in RUNNER below.
# CMP_VIA=run: the new side is `scripts/nps5 run PPSA21564 --scene worldmap --dry-run --gpu G` (the final env of the runner) instead of game.py.
# Usage: [CMP_VIA=run] tests/compare_env.sh [kyty|anyps5|all]    exit 0 when the only differences are the documented ones (EXPECTED_*).
NPS=${NPS5_ROOT:-$(cd "$(dirname "$0")/../../../.." && pwd)}
PUB=$(cd "$(dirname "$0")/.." && pwd)
WHICH=${1:-all}
T=$(mktemp -d "${TMPDIR:-/tmp}/cmpenv.XXXXXX"); trap 'rm -rf "$T"' EXIT
# runner words: set per run by the runner/machine, not by the title profile
# base words `nps5 run` adds (run_sandboxed.sh adds them outside NPS5_EXTRA_ENV)
BASE="NPS5_AUDIO_NULL PS4_RECOMP_HEADLESS PS4_NO_DIALOGS PS4_LANG NPS5_TITLE_RUN"
RUNNER="$BASE NPS5_PERF NPS5_RUN_CLASS NPS5_TITLE_PRIO NPS5_RUN_TAG NPS5_SELF_MEM_LIMIT_MB NPS5_VPAD_SCRIPT NPS5_VPAD_TYPE NPS5_WORKDIR"
# documented differences: new switches the profile adds, and one old word that is back-end specific
EXPECTED_NEW="NPS5_SCENE_OPEN_KEY=levels/"
EXPECTED_OLD_kyty="APS5_HOST_IMPORT_MIB=16384"   # host_import_mib is emitted for anyps5 only (the Kyty back end does not read it)
EXPECTED_OLD_anyps5=""

# stub that prints the env string it receives
cat > $T/sandbox.sh <<'S'
#!/bin/bash
printf '%s\n' "$NPS5_EXTRA_ENV" > "$CMP_OUT"
S
chmod +x $T/sandbox.sh
# transformed run_astro.sh: no mkdir/touch/cd/log redirects/manifest verify; the sandbox call is the stub
sed -e 's#^\. .*astro_profile_ban\.sh.*#:#' -e '/^mkdir -p \$R/d' -e '/^touch /d' -e '/^cd \$B\/out/d' \
    -e 's#\$WT/tools/run_sandboxed\.sh#'$T'/sandbox.sh#' -e 's#> \$B/logs/\$TAG\.out 2> \$B/logs/\$TAG\.err#> /dev/null 2>\&1#' \
    -e '/^rc=\$?/,$d' -e '/^mkdir/d' $NPS/work/ppsa21564/run_astro.sh > $T/run_astro.sh
sed -e '/^rm -f \$NPS\/work\/kyty\/\$1\.fp/d' -e 's#; mkdir -p \$D##' -e 's#; mkdir -p \$C##' \
    -e 's#\$NPS/work/ppsa21564/run_astro\.sh#'$T'/run_astro.sh#' -e 's#> work/kyty/\$1\.wrap\.log 2>&1#> /dev/null 2>\&1#' \
    -e '/^echo "rc=/d' $NPS/work/kyty/k4run.sh > $T/k4run.sh
chmod +x $T/run_astro.sh $T/k4run.sh

norm() {  # one word per line, sorted, minus path-valued words and runner words; the old alias becomes the new word
  tr ' ' '\n' | grep -v '^$' | grep '=' | sed 's#^NPS5_ASTRO_REALTIME=1$#NPS5_FRAMERATE_BLOCK=0x93d4040#' |
  grep -v -E '=(/|~|\$)|=[^ ]*/Users/' | grep -v -E "^($(echo $RUNNER | tr ' ' '|'))=" |
  awk -F= '{ v[$1] = $0 } END { for (k in v) print v[k] }' | sort   # a repeated KEY: the last word wins (as in the shell)
}
rc=0
run_one() {
  local gpu=$1 desc
  export CMP_OUT=$T/old.$gpu
  rm -f $CMP_OUT
  if [ $gpu = kyty ]; then ( cd $NPS && NPS5_ROOT=$NPS bash $T/k4run.sh cmp 90 "" ) >/dev/null 2>&1; desc="work/kyty/k4run.sh"
  else ( cd $NPS && bash $T/run_astro.sh cmp 90 "" ) >/dev/null 2>&1; desc="work/ppsa21564/run_astro.sh"; fi
  [ -s $CMP_OUT ] || { echo "[$gpu] FAIL: could not extract the env of $desc (script changed?)"; rc=2; return; }
  norm < $CMP_OUT > $T/old.$gpu.n
  if [ "${CMP_VIA:-}" = run ]; then
    python3 -I $PUB/scripts/nps5 run PPSA21564 --scene worldmap --dry-run --gpu $gpu 2>/dev/null | sed -n '/^env:$/,/^command:/p' | grep '^  ' | sed 's/^  //' | norm > $T/new.$gpu.n
  else
    python3 -I $PUB/games/game.py PPSA21564 --env --gpu $gpu --scene worldmap | norm > $T/new.$gpu.n
  fi
  echo "== $gpu: $desc  vs  game.py PPSA21564 --env --gpu $gpu --scene worldmap"
  echo "   old $(wc -l < $T/old.$gpu.n | tr -d ' ') words, new $(wc -l < $T/new.$gpu.n | tr -d ' ') words (machine paths and runner words ignored: $RUNNER)"
  local eo=EXPECTED_OLD_$gpu
  comm -23 $T/old.$gpu.n $T/new.$gpu.n > $T/only_old; comm -13 $T/old.$gpu.n $T/new.$gpu.n > $T/only_new
  local bad=0
  while read -r w; do [ -z "$w" ] && continue
    if [ "$w" = "${!eo}" ]; then echo "   only in old (expected): $w"; else echo "   only in old: $w"; bad=1; fi; done < $T/only_old
  while read -r w; do [ -z "$w" ] && continue
    if [ "$w" = "$EXPECTED_NEW" ]; then echo "   only in new (expected): $w"; else echo "   only in new: $w"; bad=1; fi; done < $T/only_new
  [ $bad = 0 ] && echo "   RESULT: same word set apart from the expected differences" || { echo "   RESULT: DIFFERENT"; rc=1; }
}
case $WHICH in kyty|anyps5) run_one $WHICH;; all) run_one kyty; run_one anyps5;; *) echo "usage: $0 [kyty|anyps5|all]" >&2; exit 64;; esac
exit $rc
