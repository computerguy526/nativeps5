#!/bin/bash
# xray test-suite. Offline only (pipeline creation + xcrun metal; no dispatch, no GPU counters). Exit 0 only if every test passes.
# usage: tests/run_tests.sh [--quick]   (--quick skips the 3 validation shaders / kk-mslover linkage)
cd "$(dirname "$0")/.." || exit 2
X=$PWD; F=$X/tests/fixtures; T=$(mktemp -d "${TMPDIR:-/tmp}/xray_tests.XXXXXX"); trap 'rm -rf "$T"' EXIT
export XRAY_AGXDIS=$T/none   # layer 6 off by default in tests (owned by agxdis); hook tests below set it
QUICK=0; [ "$1" = --quick ] && QUICK=1
PASS=0; FAIL=0; NAMES=()
ok()   { PASS=$((PASS+1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL $1 :: $2"; }
eq()   { if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1" "got '$2' want '$3'"; fi; }
nonempty() { if [ -s "$2" ]; then ok "$1"; else bad "$1" "missing/empty $2"; fi; }
kv()   { tr ' ' '\n' < "$1/summary.txt" | sed -n "s/^$2=//p" | head -1; }   # kv <outdir> <key>
st()   { sed -n "s/^$2: //p" "$1" | head -1; }                               # st <statsfile> <key>
xr()   { ./xray.sh "$@"; }
SUM()  { local d=$1; shift; xr "$@" --out "$d" > "$d.log" 2> "$d.err"; echo $?; }   # runs, prints rc

echo "== 1. environment"
for t in b3sum spirv-dis xcrun; do command -v $t >/dev/null && ok "tool $t" || bad "tool $t" missing; done
[ -x kscan_dummy ] 2>/dev/null; [ -x ../kscan/bin/kk_spv_gfx ] && ok "kk_spv_gfx" || bad "kk_spv_gfx" missing
ls ../s3/kk-xray/install-arm64/lib/libvulkan_kosmickrisp.dylib >/dev/null 2>&1 && ok "kk-xray build (NIR hook)" || bad "kk-xray build" "run work/s3/kk-xray/kb.sh"

echo "== 2. hash function = KK's own MSL hash (BLAKE3 first 8 bytes) on 6 mslover dumps"
n=0; for f in $(ls ../mslover/dump_h/*.info | head -400 | awk 'NR%70==1'); do h=$(basename $f .info); m=$(b3sum --no-names ../mslover/dump_h/$h.metal | cut -c1-16); n=$((n+1)); eq "b3($h)==name" "$m" "$h"; done
[ $n -ge 5 ] && ok "sampled $n dumps" || bad "sampling" "only $n"

echo "== 3. MSL fixtures with KNOWN load counts (layer 4 text, layer 5 AIR)"
fx() { # name msl_scalar msl_vec air_scalar air_vec air_coherent
  local d=$T/fx_$1; local rc; rc=$(SUM $d $F/$1.metal); eq "$1 rc" "$rc" 0
  local o=$d/$(ls $d | grep -v '\.' | head -1)
  eq "$1 L4 scalar loads" "$(st $o/4_msl_stats.txt gload_scalar)" $2
  eq "$1 L4 vector loads" "$(st $o/4_msl_stats.txt gload_vec)" $3
  eq "$1 L5 scalar loads" "$(st $o/5_air_stats.txt dload_scalar)" $4
  eq "$1 L5 vector loads" "$(st $o/5_air_stats.txt dload_vec)" $5
  eq "$1 L5 coherent calls" "$(st $o/5_air_stats.txt dload_coherent)" $6
}
fx scalar4 4 0 4 0 0          # 4 adjacent scalar loads: Apple front end (-O1 IR) does NOT merge them
fx vec4 0 1 0 1 0             # 1 float4 load stays 1 vector load
fx mixed 3 1 3 1 0            # float4 + 2 float + 1 uint
fx coherent4 4 0 4 0 4        # coherent scalar loads = opaque air.load.device_coherent calls
fx coherent_vec4 0 1 4 0 4    # ONE coherent float4 load in MSL becomes 4 scalar coherent calls in AIR
fx guarded 1 0 1 0 0          # guarded load stays 1 (not hoisted/duplicated)
d=$T/fx_loop2; rc=$(SUM $d $F/loop2.metal); eq "loop2 rc" "$rc" 0; o=$d/$(ls $d | grep -v '\.' | head -1)
eq "loop2 L4 loops" "$(st $o/4_msl_stats.txt loops)" 2
eq "loop2 L5 loop branches" "$(st $o/5_air_stats.txt loop_br)" 2

echo "== 4. SPIR-V fixtures (layers 2,3,4,5; two KK_MSL_OPT values)"
for opt in 1039 33807; do for s in scalar4 vec4; do
  d=$T/sp_${s}_$opt; rc=$(SUM $d $F/$s.spv --opt $opt); eq "$s o$opt rc" "$rc" 0; o=$d/$(ls $d | head -1)
  nonempty "$s o$opt L2 spirv" $o/2_spirv.txt; grep -q OpEntryPoint $o/2_spirv.txt && ok "$s o$opt L2 has OpEntryPoint" || bad "$s L2" "no OpEntryPoint"
  eq "$s o$opt L3 status" "$(kv $o L3)" ok; nonempty "$s o$opt L3 nir" $o/3_nir.txt
  grep -q '^######## .*3_to_msl' $o/3_nir.txt && ok "$s o$opt L3 has final NIR section" || bad "$s L3" "no 3_to_msl"
  [ $opt = 33807 ] && { grep -q 'after_vec' $o/3_nir.txt && ok "$s o33807 NIR shows the vectorize point" || bad "$s L3" "no after_vec section"; }
  nonempty "$s o$opt L4" $o/4_msl.metal; eq "$s o$opt L5 status" "$(kv $o L5)" ok
  eq "$s o$opt msl_b3 == b3(4_msl.metal)" "$(kv $o msl_b3)" "$(b3sum --no-names $o/4_msl.metal | cut -c1-16)"
done; done
o1=$T/sp_vec4_1039/$(ls $T/sp_vec4_1039|head -1); eq "vec4.spv has exactly 1 vector global load in MSL" "$(st $o1/4_msl_stats.txt gload_vec)" 1
o2=$T/sp_scalar4_1039/$(ls $T/sp_scalar4_1039|head -1); eq "scalar4.spv has 0 vector global loads in MSL (opt 1039)" "$(st $o2/4_msl_stats.txt gload_vec)" 0
o3=$T/sp_scalar4_33807/$(ls $T/sp_scalar4_33807|head -1)
[ "$(kv $o2 id)" != "$(kv $o3 id)" ] && ok "dir id differs per KK_MSL_OPT" || bad "id" "same id for two opts"
eq "scalar4 SPIR-V id equal across opts (same spv)" "$(kv $o2 id | cut -d_ -f1)" "$(kv $o3 id | cut -d_ -f1)"

echo "== 5. hash linkage: SPIR-V -> MSL equals KK's own hash (kk-mslover KK_MSL_DUMP_DIR)"
if [ $QUICK = 0 ] && [ -f ../s3/kk-mslover/install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json ]; then
  mkdir -p $T/dd; DYLD_LIBRARY_PATH=/opt/homebrew/lib VK_DRIVER_FILES=$X/../s3/kk-mslover/install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json kk_fake_depth_bounds=true KK_MSL_DUMP_DIR=$T/dd MESA_SHADER_CACHE_DISABLE=true ../kscan/bin/kk_spv_gfx $F/vec4.spv >/dev/null 2>&1
  d=$T/lk; rc=$(SUM $d $F/vec4.spv --kk $X/../s3/kk-mslover --opt 0); eq "xray via kk-mslover rc" "$rc" 0; o=$d/$(ls $d|head -1)
  h=$(kv $o msl_b3); [ -f $T/dd/$h.metal ] && ok "xray msl_b3 $h is a file KK itself dumped (same shader, same hash)" || bad "linkage" "KK dump dir has no $h: $(ls $T/dd | tr '\n' ' ')"
  [ -f $T/dd/$h.metal ] && cmp -s $T/dd/$h.metal $o/4_msl.metal && ok "KK's dumped MSL == xray 4_msl.metal byte for byte" || bad "linkage" "MSL text differs"
else echo "  skip (quick or kk-mslover missing)"; fi
# same shader in every layer: spv id/hash recorded, same MSL used by L4 and L5
o=$T/sp_vec4_1039/$(ls $T/sp_vec4_1039|head -1)
grep -q "source_filename = \".*4_msl.metal\"" $o/5_air.ll && ok "L5 AIR was compiled from this folder's 4_msl.metal" || bad "L5 provenance" "source_filename differs"
eq "L2 file is the input module (spirv-dis of vec4.spv)" "$(spirv-dis $F/vec4.spv | b3sum --no-names | cut -c1-16)" "$(spirv-dis --no-color $F/vec4.spv | b3sum --no-names | cut -c1-16)"
diff <(spirv-dis --no-color $F/vec4.spv) $o/2_spirv.txt >/dev/null && ok "2_spirv.txt == spirv-dis(input)" || bad "L2 content" differs

echo "== 6. validation shaders (hoover megakernel, hot world-map FS, kkvec-changed CS at 1039/33807)"
if [ $QUICK = 0 ]; then
  for h in c4f61f4396e04fc0 36f5ae3d57b4756c; do
    d=$T/v_$h; rc=$(SUM $d $h); eq "$h rc" "$rc" 0; o=$d/$h
    for f in 1_isa.txt 2_spirv.txt 3_nir.txt 4_msl.metal 4_msl_stats.txt 5_air.ll 5_air_stats.txt 7_cost.txt summary.txt; do nonempty "$h $f" $o/$f; done
    ls $o/6_agx >/dev/null 2>&1 && ok "$h 6_agx dir" || bad "$h 6_agx" missing
    eq "$h b3(4_msl.metal)==hash" "$(b3sum --no-names $o/4_msl.metal | cut -c1-16)" "$h"
    eq "$h L5 ok" "$(kv $o L5)" ok; eq "$h summary is one line" "$(wc -l < $o/summary.txt | tr -d ' ')" 1
    [ "$(kv $o L7)" = ok ] && ok "$h cost data joined" || bad "$h L7" "no cost data"
  done
  for opt in 1039 33807; do d=$T/v_s6120_$opt; rc=$(SUM $d s6120.a0_0 --opt $opt); eq "s6120 o$opt rc" "$rc" 0; o=$d/$(ls $d|head -1)
    for f in 2_spirv.txt 3_nir.txt 4_msl.metal 5_air.ll summary.txt; do nonempty "s6120 o$opt $f" $o/$f; done; done
else echo "  skip (quick)"; fi

echo "== 7. idempotence (two runs, byte-identical folders)"
for case in "$F/scalar4.metal" "$F/scalar4.spv --opt 1039"; do
  a=$T/ia; b=$T/ib; rm -rf $a $b; xr $case --out $a >/dev/null 2>&1; xr $case --out $b >/dev/null 2>&1
  diff -r $a $b >/dev/null && ok "idempotent: $(basename ${case%% *})" || bad "idempotent" "$(diff -rq $a $b | head -3)"
done
a=$T/ic; xr $F/scalar4.metal --out $a >/dev/null 2>&1; xr $F/scalar4.metal --out $a >/dev/null 2>&1; [ $(ls $a | wc -l) = 1 ] && ok "re-run into same out dir leaves one folder" || bad "rerun" "$(ls $a)"

echo "== 8. errors are loud: non-zero exit + message, never silent"
err() { # name want_rc stderr_regex cmd...
  local n=$1 w=$2 re=$3; shift 3; local e; e=$("$@" 2>&1 >/dev/null); local rc=$?
  [ $rc = $w ] && ok "$n rc=$rc" || bad "$n rc" "got $rc want $w"
  echo "$e" | grep -qE "$re" && ok "$n message" || bad "$n message" "'$e' !~ $re"
}
err "unknown hash" 2 "ERROR: bad input: no MSL dump" ./xray.sh deadbeefdeadbeef --out $T/e
err "garbage name" 2 "ERROR: bad input" ./xray.sh not-a-shader --out $T/e
echo hello > $T/junk.spv; err "spv without magic" 2 "not SPIR-V" ./xray.sh $T/junk.spv --out $T/e
echo hello > $T/junk.txt; err "unknown extension" 2 "neither .spv nor .metal" ./xray.sh $T/junk.txt --out $T/e
err "no argument" 2 "usage" ./xray.sh
printf '\x03\x02\x23\x07garbage' > $T/trunc.spv; err "truncated spv" 2 "ERROR" ./xray.sh $T/trunc.spv --out $T/e
err "missing KK install" 2 "no KosmicKrisp install" ./xray.sh $F/scalar4.spv --kk /nonexistent --out $T/e
printf 'kernel void k( {' > $T/broken.metal; e=$(./xray.sh $T/broken.metal --out $T/e 2>&1 >/dev/null); rc=$?; [ $rc != 0 ] && ok "broken MSL rc=$rc" || bad "broken MSL" "rc 0"; 
grep -q "ERROR" $T/e/*/5_air.ll 2>/dev/null && ok "broken MSL: 5_air.ll records the compiler error" || bad "broken MSL" "no error recorded"
grep -q "L5=FAIL" $T/e/*/summary.txt 2>/dev/null && ok "broken MSL: summary L5=FAIL" || bad "broken MSL" "summary not FAIL"
e=$(PATH=/usr/bin:/bin ./xray.sh $F/scalar4.metal --out $T/e 2>&1 >/dev/null); rc=$?; [ $rc = 2 ] && echo "$e" | grep -q "missing tool" && ok "missing tool (b3sum/spirv-dis off PATH) rc=2 + message" || bad "missing tool" "rc=$rc '$e'"
err "batch missing list" 2 "list file" ./batch.sh $T/nolist.txt

echo "== 9. layer-6 hook (stub agxdis via XRAY_AGXDIS)"
printf '#!/bin/bash\necho "stub saw $1 $2"\n' > $T/agx_ok.sh; chmod +x $T/agx_ok.sh
d=$T/h1; XRAY_AGXDIS=$T/agx_ok.sh xr $F/scalar4.metal --out $d >/dev/null 2>&1; o=$d/$(ls $d|head -1)
[ -s $o/6_agx/agx.txt ] && grep -q "4_msl.metal --json" $o/6_agx/agx.txt && ok "hook ran as <hook> 4_msl.metal --json <6_agx>/agx_stats.json, stdout in 6_agx/agx.txt" || bad "hook" "no output"
eq "hook L6 status" "$(kv $o L6)" ok
printf '#!/bin/bash\necho boom >&2; exit 3\n' > $T/agx_bad.sh; chmod +x $T/agx_bad.sh
d=$T/h2; XRAY_AGXDIS=$T/agx_bad.sh xr $F/scalar4.metal --out $d >/dev/null 2>&1; rc=$?; o=$d/$(ls $d|head -1)
[ $rc = 1 ] && ok "failing hook -> xray rc=1" || bad "failing hook" "rc=$rc"; eq "failing hook L6" "$(kv $o L6)" "FAIL(rc=3)"
eq "no hook -> L6 na" "$(kv $T/ia/$(ls $T/ia|head -1) L6)" na

echo "== 10. batch == per-shader"
printf '# list\n%s\n%s\n%s\n' $F/scalar4.metal $F/vec4.metal $F/coherent4.metal > $T/list.txt
./batch.sh $T/list.txt --out $T/bo > $T/b.csv 2>$T/b.err; eq "batch rc" $? 0
eq "batch rows" "$(($(wc -l < $T/b.csv)-1))" 3
for s in scalar4 vec4 coherent4; do
  s1=$T/s1_$s; xr $F/$s.metal --out $s1 >/dev/null 2>&1; o=$s1/$(ls $s1|head -1)
  row=$(python3 -I -c "
import csv,sys
r=[x for x in csv.DictReader(open('$T/b.csv')) if x['input'].endswith('$s.metal')][0]
print(r['id'],r['msl_gloads'],r['msl_gload_vec'],r['air_dload_total'],r['air_dload_vec'],r['L5'])")
  eq "batch row == single ($s)" "$row" "$(kv $o id) $(kv $o msl_gloads) $(kv $o msl_gload_vec) $(kv $o air_dload_total) $(kv $o air_dload_vec) $(kv $o L5)"
done
printf '%s\ndeadbeefdeadbeef\n%s\n' $F/scalar4.metal $F/vec4.metal > $T/list2.txt
./batch.sh $T/list2.txt --out $T/bo2 > $T/b2.csv 2>/dev/null; eq "batch with a bad row: rc" $? 1
grep -q deadbeefdeadbeef.*ERROR $T/b2.csv && ok "bad row reported with ERROR" || bad "bad row" "$(cat $T/b2.csv | tail -2)"
eq "batch continues after a bad row (3 rows)" "$(($(wc -l < $T/b2.csv)-1))" 3

echo "== 11. layer 6 (real agxdis, AGX machine code on this Mac's GPU; compile only) on known fixtures"
if [ -x ../agxdis/agxdis.sh ] && [ $QUICK = 0 ]; then
  unset XRAY_AGXDIS
  for c in "scalar4 1 1 i32x4" "vec4 1 1 i32x4" "coherent4 4 0 i32x1"; do set -- $c
    d=$T/g6_$1; rc=$(SUM $d $F/$1.metal); o=$d/$(ls $d | head -1)
    eq "$1 L6 status" "$(kv $o L6)" ok; nonempty "$1 6_agx/agx.txt" $o/6_agx/agx.txt
    eq "$1 AGX device loads (Apple back end)" "$(kv $o agx_dload)" $2
    eq "$1 AGX vector device loads" "$(kv $o agx_dload_vec)" $3
    grep -q "$4" $o/6_agx/agx_stats.json && ok "$1 AGX load width $4" || bad "$1 width" "no $4"
  done
else echo "  skip (agxdis absent or --quick)"; fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ $FAIL = 0 ]
