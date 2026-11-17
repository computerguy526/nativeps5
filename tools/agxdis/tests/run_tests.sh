#!/bin/bash
# work/agxdis/tests/run_tests.sh -- end-to-end tests for agxdis (SHADER X-RAY layer 6).
# OFFLINE: compiles ~40 tiny pipelines (MTLBinaryArchive / xcrun metal-tt). Nothing is drawn or
# dispatched and no GPU counters are used.
# Device-route expectations are for this Mac's GPU family (G15/G16 ISA); on a G13/G14 Mac the
# device-route block is skipped and the same fixtures are checked through --arch only.
# Usage: run_tests.sh [-v]      exit 0 = all passed
set -u
D="$(cd "$(dirname "$0")/.." && pwd)"
FX="$D/tests/fixtures"
A="$D/agxdis.sh"
VERBOSE=0; [ "${1:-}" = "-v" ] && VERBOSE=1
TMP="$(mktemp -d "${TMPDIR:-/tmp}/agxdis-tests.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0; FAILED=()

ok()   { PASS=$((PASS+1)); [ $VERBOSE = 1 ] && echo "  ok   $1"; return 0; }
bad()  { FAIL=$((FAIL+1)); FAILED+=("$1"); echo "  FAIL $1"; [ -n "${2:-}" ] && echo "       $2"; return 0; }
# field <stats line> <key>
field() { echo "$1" | tr ' ' '\n' | sed -n "s/^$2=//p" | head -1; }
# eq <name> <stats line> key=value...
eq() {
  local name="$1" line="$2" kv k want got; shift 2
  for kv in "$@"; do
    k="${kv%%=*}"; want="${kv#*=}"; got="$(field "$line" "$k")"
    if [ "$got" = "$want" ]; then ok "$name: $k=$want"; else bad "$name: $k" "want $want, got '$got'"; fi
  done
}
# cmpn <name> <stats line> <key> <op> <number>
cmpn() {
  local got; got="$(field "$2" "$3")"
  if [ -n "$got" ] && [ "$got" "$4" "$5" ] 2>/dev/null; then ok "$1: $3 $4 $5 (=$got)"; else bad "$1: $3 $4 $5" "got '$got'"; fi
}
# rc <name> <want rc> <stderr must match regex or ''> cmd...
rc() {
  local name="$1" want="$2" re="$3" got; shift 3
  "$@" >"$TMP/rc.out" 2>"$TMP/rc.err"; got=$?
  if [ "$got" != "$want" ]; then bad "$name: exit code" "want $want, got $got: $(head -2 "$TMP/rc.err" | tr '\n' ' ')"; return; fi
  if [ -n "$re" ] && ! grep -qE "$re" "$TMP/rc.err"; then bad "$name: message" "stderr lacks /$re/: $(head -2 "$TMP/rc.err" | tr '\n' ' ')"; return; fi
  ok "$name: exit $want${re:+ + message}"
}
# stats <fixture> [stage] [extra args] -> the STATS line of that stage
stats() { local f="$1" st="${2:-compute}"; shift; shift 2>/dev/null; "$A" "$FX/$f.metal" --stats-only "$@" 2>"$TMP/err" | grep "^STATS stage=$st " | head -1; }
# selfcheck <name> <agxdis args...>: STATS must agree with an independent recount of the listing
selfcheck() {
  local name="$1"; shift
  "$A" "$@" >"$TMP/sc.txt" 2>/dev/null || { bad "$name: selfcheck run"; return; }
  python3 - "$TMP/sc.txt" <<'EOF' && ok "$name: STATS == independent recount of listing" || bad "$name: STATS != independent recount"
import re, sys
ins = bts = dl = ds = 0
okay = True
for line in open(sys.argv[1]):
    m = re.match(r"\s*[0-9a-f]+:[ ~?] ([0-9a-f]+)\s+(\S+)", line)
    if m:
        ins += 1; bts += len(m.group(1)) // 2
        dl += m.group(2).split(".")[0] == "device_load"; ds += m.group(2).split(".")[0] == "device_store"
    elif line.startswith("STATS"):
        kv = dict(t.split("=", 1) for t in line.split()[1:])
        got = (int(kv["instr"]), int(kv["bytes"]), int(kv["dload"]), int(kv["dstore"]))
        if got != (ins, bts, dl, ds):
            print("mismatch", kv["stage"], got, (ins, bts, dl, ds)); okay = False
        w = sum(int(x.split(":")[1]) for x in kv["dload_w"].split(",")) if kv["dload_w"] != "-" else 0
        if w != dl:
            print("width histogram does not sum to dload", w, dl); okay = False
        ins = bts = dl = ds = 0
sys.exit(0 if okay else 1)
EOF
}

echo "== build + environment"
"$D/build.sh" >"$TMP/build.log" 2>&1 && ok "build.sh" || { bad "build.sh" "$(tail -3 "$TMP/build.log")"; echo "cannot continue"; exit 1; }
DEV="$("$D/build/getbin" --info 2>/dev/null)"; [ -n "$DEV" ] && ok "Metal device: $DEV" || bad "getbin --info"
"$A" "$FX/scalar4.metal" --stats-only --keep "$TMP/dev_scalar4.bin" >"$TMP/dev.stats" 2>"$TMP/err"; DRC=$?
GPU="$(field "$(cat "$TMP/dev.stats")" gpu)"; ISA="$(field "$(cat "$TMP/dev.stats")" isa)"
echo "   device archive arch: ${GPU:-?} (isa ${ISA:-?}, rc $DRC)"

echo "== unit tests of the stats parser (no compiler)"
if python3 "$D/tests/test_stats.py" >"$TMP/unit.log" 2>&1; then
  n="$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$TMP/unit.log")"; PASS=$((PASS+n)); echo "   $n unit tests ok"
else bad "test_stats.py" "$(tail -5 "$TMP/unit.log" | tr '\n' ' ')"; fi

# ------------------------------------------------------------------ fixtures with known properties
check_fixtures() {  # $1 = label, rest = extra agxdis args (e.g. --arch applegpu_g16s)
  local L="$1"; shift
  local s
  # --- the load-merging question
  s="$(stats scalar4 compute "$@")";  eq "$L scalar4 (4 adjacent scalar float loads)" "$s" dload=1 dload_w=i32x4:1 dload_bytes=16 dstore=1 dstore_w=i32x1:1 loops=0 if=0 spill=0 unknown=0 partial=0 complete=1
  local w_scalar; w_scalar="$(field "$s" dload_w)"
  s="$(stats vec4 compute "$@")";     eq "$L vec4 (one float4 load)" "$s" dload=1 dload_w=i32x4:1 dload_bytes=16 unknown=0 complete=1
  [ "$w_scalar" = "$(field "$s" dload_w)" ] && ok "$L scalar4 and vec4 compile to the SAME device load width ($w_scalar)" || bad "$L scalar4 vs vec4 load width differ"
  s="$(stats gap4 compute "$@")";     eq "$L gap4 (non-adjacent)" "$s" dload=4 dload_w=i32x1:4 dload_bytes=16
  s="$(stats alias4 compute "$@")";   eq "$L alias4 (loads separated by stores to another buffer)" "$s" dload=1 dload_w=i32x4:1 dstore=1 dstore_w=i32x4:1
  s="$(stats scalar8 compute "$@")";  eq "$L scalar8 (8 adjacent uint loads)" "$s" dload=2 dload_w=i32x4:2 dload_bytes=32
  s="$(stats twobuf compute "$@")";   eq "$L twobuf (2+2 from two buffers)" "$s" dload=2 dload_w=i32x2:2 dload_bytes=16
  s="$(stats ushort4 compute "$@")";  eq "$L ushort4 (4 adjacent 16-bit loads)" "$s" dload=1 dload_w=i16x4:1 dload_bytes=8
  s="$(stats store4 compute "$@")";   eq "$L store4 (4 adjacent scalar stores)" "$s" dstore=1 dstore_w=i32x4:1 dstore_bytes=16
  s="$(stats kkstyle4 compute "$@")"; eq "$L kkstyle4 (integer-address loads, translator style)" "$s" dload=4 dload_w=i32x1:4
  s="$(stats kkstyle4c compute "$@")"; eq "$L kkstyle4c (coherent device)" "$s" dload=4 dload_w=i32x1:4
  s="$(stats kkstyle4if compute "$@")"; eq "$L kkstyle4if (2 of 4 loads in an if)" "$s" dload=4 dload_w=i32x1:4 if=1
  s="$(stats kkstyle4v compute "$@")"; eq "$L kkstyle4v (source-level packed_uint4 load)" "$s" dload=1 dload_w=i32x4:1
  # --- spills
  s="$(stats nospill compute "$@")";  eq "$L nospill" "$s" spill=0 stack_ld=0 stack_st=0 spill_ld=0 spill_st=0 spill_slots=0 dload=1 dstore=1 dstore_w=i32x4:1 loops=0 if=0 unknown=0 partial=0
  s="$(stats spill compute "$@")";    eq "$L spill (512-float local array)" "$s" spill=1 loops=2 complete=1
  cmpn "$L spill (array lives in stack memory)" "$s" stack_ld -gt 0; cmpn "$L spill (array lives in stack memory)" "$s" stack_st -gt 0
  s="$(stats regspill compute "$@")"; eq "$L regspill (600 live values)" "$s" spill=1 dload=150 dload_w=i32x4:150 complete=1
  REGSPILL="$s"
  # --- control flow
  s="$(stats loop compute "$@")";     eq "$L loop" "$s" loops=1 dstore=1
  s="$(stats loop2 compute "$@")";    eq "$L loop2" "$s" loops=2
  s="$(stats branch compute "$@")";   eq "$L branch (if/else with stores)" "$s" loops=0 if=1 else=1 dstore=2
  # --- textures
  s="$(stats texsample fragment "$@")"; eq "$L texsample fragment" "$s" tex_sample=1 tex_load=0 tex_store=0 complete=1
  s="$(stats texsample vertex "$@")";   eq "$L texsample vertex" "$s" tex_sample=0 tex_load=0 tex_store=0 dload=0
  s="$(stats texsample3 fragment "$@")"; eq "$L texsample3 fragment" "$s" tex_sample=3 tex_load=0
  s="$(stats texnames compute "$@")";   eq "$L texnames (compute sample, no read)" "$s" tex_sample=1 tex_load=0 tex_store=0
}

if [ "$DRC" = 0 ] && [ "$ISA" = g15 ]; then
  echo "== fixtures, device route ($GPU)"
  check_fixtures "dev"
  # G15/G16 only: register-pressure spills go to stack-slot registers, arrays to stack memory
  eq "dev regspill: all 96 GPRs r0..r95 in use" "$REGSPILL" gpr=96 gpr_max=r95 stack_ld=0 stack_st=0
  cmpn "dev regspill" "$REGSPILL" spill_slots -gt 100; cmpn "dev regspill" "$REGSPILL" spill_ld -gt 100; cmpn "dev regspill" "$REGSPILL" spill_st -gt 100
  s="$(stats spill)"; eq "dev spill: array is not register-spilled" "$s" spill_slots=0
  s="$(stats texrw)"; eq "dev texrw (1 read + 1 write)" "$s" tex_load=1 tex_store=1 tex_sample=0 complete=1
  # exact instruction listings, checked by hand (will change if Apple changes the compiler)
  eq "dev scalar4 exact size" "$(stats scalar4)" instr=7 bytes=58 gpr=5 gpr_max=r4
  eq "dev nospill exact size" "$(stats nospill)" instr=8 bytes=62 gpr=8 gpr_max=r7
  "$A" "$FX/scalar4.metal" 2>/dev/null | awk '/^ +[0-9a-f]+:/{print $3}' | tr '\n' ' ' >"$TMP/mn"
  [ "$(cat "$TMP/mn")" = "get_sr iadd device_load fmul ffma device_store stop " ] && ok "dev scalar4 mnemonic sequence" || bad "dev scalar4 mnemonic sequence" "$(cat "$TMP/mn")"
  "$A" "$FX/scalar4.metal" 2>/dev/null | grep -q 'device_load .*i32, xyzw, r0_r1_r2_r3' && ok "dev scalar4: listing shows one xyzw load into r0_r1_r2_r3" || bad "dev scalar4 listing"
  echo "== fragment-only input (stub vertex stage)"
  "$A" "$FX/fragonly.metal" --stats-only >"$TMP/fo" 2>"$TMP/err"; frc=$?
  [ $frc = 0 ] && ok "fragonly: exit 0" || bad "fragonly: exit" "rc=$frc $(head -2 "$TMP/err")"
  [ "$(grep -c '^STATS' "$TMP/fo")" = 1 ] && ok "fragonly: exactly one STATS line (stub vertex hidden)" || bad "fragonly: STATS count" "$(grep -c '^STATS' "$TMP/fo")"
  eq "fragonly" "$(cat "$TMP/fo")" stage=fragment tex_sample=1 complete=1
  s="$("$A" "$FX/fragdepth.metal" --stats-only 2>/dev/null)"
  [ "$(echo "$s" | grep -c '^STATS')" = 1 ] && eq "fragdepth (depth output)" "$s" stage=fragment complete=1 || bad "fragdepth" "$s"
  echo "== offline route must equal the device (--arch $GPU)"
  for f in scalar4 spill loop2 texsample regspill fragonly fragdepth kkstyle4 branch; do
    "$A" "$FX/$f.metal" >"$TMP/a.txt" 2>/dev/null; "$A" "$FX/$f.metal" --arch "$GPU" >"$TMP/b.txt" 2>/dev/null
    cmp -s "$TMP/a.txt" "$TMP/b.txt" && [ -s "$TMP/a.txt" ] && ok "$f: device == metal-tt $GPU (identical listing)" || bad "$f: device vs metal-tt $GPU differ"
  done
else
  echo "== device route skipped: device archive rc=$DRC isa=${ISA:-?} ($(head -1 "$TMP/err"))"
  [ "$DRC" = 8 ] && ok "device GPU unsupported -> exit 8" || bad "device route" "rc=$DRC"
fi

echo "== fixtures, offline route for other GPU generations"
s="$(stats scalar4 compute --arch applegpu_g13g)"; eq "g13g scalar4 (M1)" "$s" gpu=applegpu_g13g isa=g13 dload=1 dload_w=i32x4:1 unknown=0 partial=0 complete=1
s="$(stats gap4 compute --arch applegpu_g13g)";    eq "g13g gap4" "$s" dload=4 dload_w=i32x1:4
s="$(stats kkstyle4 compute --arch applegpu_g13g)"; eq "g13g kkstyle4" "$s" dload=4 dload_w=i32x1:4
s="$(stats regspill compute --arch applegpu_g13g)"; eq "g13g regspill: 128 GPRs then stack memory" "$s" gpr=128 gpr_max=r127 spill=1 spill_slots=0 unknown=0
cmpn "g13g regspill" "$s" stack_ld -gt 100; cmpn "g13g regspill" "$s" stack_st -gt 100
s="$(stats texsample fragment --arch applegpu_g13g)"; eq "g13g texsample fragment" "$s" tex_sample=1 tex_load=0 unknown=0
s="$(stats loop2 compute --arch applegpu_g13g)";   eq "g13g loop2" "$s" loops=2
s="$(stats scalar4 compute --arch applegpu_g14g)"; eq "g14g scalar4 (M2)" "$s" gpu=applegpu_g14g isa=g13 dload=1 dload_w=i32x4:1 unknown=0
s="$(stats scalar4 compute --arch applegpu_g15g)"; eq "g15g scalar4 (M3)" "$s" gpu=applegpu_g15g isa=g15 dload=1 dload_w=i32x4:1 unknown=0 complete=1
s="$(stats kkstyle4 compute --arch applegpu_g15g)"; eq "g15g kkstyle4" "$s" dload=4 dload_w=i32x1:4
s="$(stats scalar4 compute --arch applegpu_g16g)"; eq "g16g scalar4 (M4)" "$s" gpu=applegpu_g16g isa=g15 dload=1 dload_w=i32x4:1 unknown=0 complete=1

echo "== STATS line vs independent recount of the listing"
for f in scalar4 gap4 spill regspill loop2 branch texsample texrw kkstyle4if; do selfcheck "$f" "$FX/$f.metal" --arch applegpu_g16s; done
selfcheck "scalar8 g13g" "$FX/scalar8.metal" --arch applegpu_g13g
selfcheck "regspill g13g" "$FX/regspill.metal" --arch applegpu_g13g

echo "== determinism"
for f in scalar4 spill texsample3 regspill; do
  "$A" "$FX/$f.metal" --arch applegpu_g16s >"$TMP/d1" 2>&1; "$A" "$FX/$f.metal" --arch applegpu_g16s >"$TMP/d2" 2>&1
  cmp -s "$TMP/d1" "$TMP/d2" && [ -s "$TMP/d1" ] && ok "$f: two offline runs byte-identical" || bad "$f: offline runs differ"
done
if [ "$DRC" = 0 ]; then
  for f in scalar4 loop2 fragonly; do
    "$A" "$FX/$f.metal" >"$TMP/d1" 2>&1; "$A" "$FX/$f.metal" >"$TMP/d2" 2>&1; "$A" "$FX/$f.metal" >"$TMP/d3" 2>&1
    cmp -s "$TMP/d1" "$TMP/d2" && cmp -s "$TMP/d1" "$TMP/d3" && [ -s "$TMP/d1" ] && ok "$f: three device runs byte-identical" || bad "$f: device runs differ"
  done
fi
"$A" "$FX/loop.metal" --arch applegpu_g16s --json "$TMP/j1.json" >/dev/null 2>&1; "$A" "$FX/loop.metal" --arch applegpu_g16s --json "$TMP/j2.json" >/dev/null 2>&1
cmp -s "$TMP/j1.json" "$TMP/j2.json" && python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d[0]['loops']==1 and d[0]['stage']=='compute' and d[0]['hist']['device_load']==2" "$TMP/j1.json" 2>/dev/null \
  && ok "--json deterministic and consistent (loops=1)" || bad "--json"

echo "== input kinds: MSL == metallib == binary archive == raw code"
"$A" "$FX/loop2.metal" --arch applegpu_g16s --keep "$TMP/loop2.bin" >"$TMP/i_msl" 2>&1
xcrun metal -o "$TMP/loop2.metallib" "$FX/loop2.metal" 2>/dev/null
"$A" "$TMP/loop2.metallib" --arch applegpu_g16s >"$TMP/i_lib" 2>&1
"$A" "$TMP/loop2.bin" >"$TMP/i_bin" 2>&1
cmp -s "$TMP/i_msl" "$TMP/i_lib" && [ -s "$TMP/i_msl" ] && ok "metallib input == MSL input" || bad "metallib input != MSL input" "$(head -1 "$TMP/i_lib")"
cmp -s "$TMP/i_msl" "$TMP/i_bin" && ok "archive input == MSL input" || bad "archive input != MSL input" "$(head -1 "$TMP/i_bin")"
"$D/build/metal-archive-extractor" --extract-compute - "$TMP/loop2.bin" | "$D/build/metal-archive-extractor" --extract-named-shader _agc.main --output "$TMP/loop2.raw" - 2>/dev/null
s="$("$A" "$TMP/loop2.raw" --raw --isa g15 --stats-only 2>/dev/null)"; t="$(grep '^STATS' "$TMP/i_msl")"
eq "raw input" "$s" instr="$(field "$t" instr)" loops=2 dload="$(field "$t" dload)" stage=raw
if [ "$DRC" = 0 ]; then
  "$A" "$TMP/loop2.metallib" >"$TMP/i_libdev" 2>&1; "$A" "$FX/loop2.metal" >"$TMP/i_msldev" 2>&1
  cmp -s "$TMP/i_libdev" "$TMP/i_msldev" && [ -s "$TMP/i_msldev" ] && ok "device route: metallib input == MSL input" || bad "device route: metallib != MSL"
  s="$("$A" "$FX/texsample.metal" -f vs --stats-only 2>/dev/null)"
  [ "$(echo "$s" | grep -c '^STATS')" = 1 ] && eq "-f vs selects only the vertex function" "$s" stage=vertex || bad "-f vs" "$s"
fi
s="$("$A" "$FX/texsample.metal" -f vs --stats-only --arch applegpu_g16s 2>/dev/null)"
[ "$(echo "$s" | grep -c '^STATS')" = 1 ] && eq "-f vs (offline) selects only the vertex function" "$s" stage=vertex || bad "-f vs offline" "$s"
"$A" "$FX/scalar4.metal" --arch applegpu_g16s --prolog 2>/dev/null | grep -c '^STATS' >"$TMP/np"
[ "$(cat "$TMP/np")" = 2 ] && ok "--prolog adds the constant-program function" || bad "--prolog" "$(cat "$TMP/np") STATS lines"

echo "== errors"
rc "missing file"            3 "cannot read input"         "$A" "$TMP/does_not_exist.metal"
: >"$TMP/empty.metal"
rc "empty file"              3 "is empty"                  "$A" "$TMP/empty.metal"
head -c 4096 /dev/urandom >"$TMP/garbage.bin"; printf '\x00\xff\xfe' >>"$TMP/garbage.bin"
rc "binary garbage"          3 "not MSL text"              "$A" "$TMP/garbage.bin"
rc "MSL syntax error (offline)" 4 "undeclared identifier"  "$A" "$FX/bad_syntax.metal" --arch applegpu_g16s
rc "MSL without entry point (offline)" 5 "no vertex/fragment/kernel" "$A" "$FX/no_entry.metal" --arch applegpu_g16s
rc "unknown -f (offline)"    5 "not in library"            "$A" "$FX/scalar4.metal" --arch applegpu_g16s -f nosuchfn
if [ "$DRC" = 0 ]; then
  rc "MSL syntax error (device)"   4 "undeclared identifier"     "$A" "$FX/bad_syntax.metal"
  rc "MSL without entry point (device)" 5 "no vertex/fragment/kernel" "$A" "$FX/no_entry.metal"
  rc "unknown -f (device)"         5 "not in library"            "$A" "$FX/scalar4.metal" -f nosuchfn
fi
rc "bogus --arch"            8 "does not know architecture" "$A" "$FX/scalar4.metal" --arch applegpu_g99z
rc "unsupported generation G17 (M5)" 8 "unsupported GPU generation G17.*Raw code extracted OK" "$A" "$FX/scalar4.metal" --arch applegpu_g17g
"$A" "$FX/scalar4.metal" --arch applegpu_g16g --keep "$TMP/thin.bin" >/dev/null 2>&1
python3 - "$TMP/thin.bin" "$TMP/sub999.bin" <<'EOF'
import struct, sys
d = bytearray(open(sys.argv[1], "rb").read())
assert struct.unpack("<I", d[:4])[0] == 0xFEEDFACF
d[8:12] = struct.pack("<I", 999)
open(sys.argv[2], "wb").write(d)
EOF
rc "archive with unknown GPU subtype" 8 "unsupported GPU: unknown applegpu cpusubtype 999" "$A" "$TMP/sub999.bin"
s="$("$A" "$TMP/sub999.bin" --isa g15 --stats-only 2>/dev/null)"; eq "unknown subtype + --isa g15 override" "$s" dload=1 gpu="?"
rc "Mach-O that is not a shader archive" 10 "not an applegpu shader archive|no applegpu slice" "$A" /bin/ls
head -c 600 "$TMP/thin.bin" >"$TMP/trunc.bin"
"$A" "$TMP/trunc.bin" >/dev/null 2>"$TMP/err"; r=$?
[ $r != 0 ] && [ -s "$TMP/err" ] && ok "truncated archive: non-zero exit ($r) + message" || bad "truncated archive" "rc=$r"
rc "--raw without --isa"     2 "needs --isa"               "$A" "$TMP/loop2.raw" --raw
rc "bad option"              2 ""                          "$A" "$FX/scalar4.metal" --no-such-option
rc "no arguments"            2 ""                          "$A"
printf '\x07\x0f\xff\xff\xff\xff' >"$TMP/badraw.bin"
rc "raw bytes with unknown instruction length" 9 "decode incomplete" "$A" "$TMP/badraw.bin" --raw --isa g15
s="$("$A" "$TMP/badraw.bin" --raw --isa g15 --stats-only 2>/dev/null)"; eq "truncated decode is reported" "$s" complete=0 trunc=6
rc "--strict with partially decoded instructions" 9 "decode incomplete" "$A" "$FX/texsample.metal" --arch applegpu_g16s --strict
rc "same input without --strict" 0 ""                      "$A" "$FX/texsample.metal" --arch applegpu_g16s
rc "--strict on a fully decoded shader" 0 ""               "$A" "$FX/scalar4.metal" --arch applegpu_g16s --strict

echo
echo "agxdis tests: $PASS passed, $FAIL failed (device: ${DEV:-none}, arch ${GPU:-?})"
if [ $FAIL != 0 ]; then printf '  failed: %s\n' "${FAILED[@]}"; exit 1; fi
exit 0
