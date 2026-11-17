#!/bin/bash
# batch.sh <list-file> [--out DIR] [--opt N] [--kk DIR] [--no-air]   -> CSV on stdout (also <out>/batch.csv)
# list: one input per line (msl hash | .spv | .metal | shaderlab id); '#' comments. A failing shader gets a row with ERROR; exit 1 if any failed.
cd "$(dirname "$0")" || exit 2
list=$1; shift
[ -n "$list" ] && [ -f "$list" ] || { echo "batch.sh: ERROR: list file '$list' not found (usage: batch.sh <list> [xray opts])" >&2; exit 2; }
out=$PWD/out; args=()
while [ $# -gt 0 ]; do case $1 in --out) out=$2; args+=(--out "$2"); shift 2;; *) args+=("$1"); shift;; esac; done
mkdir -p "$out"
exec nice python3 -I - "$list" "$out" "${args[@]}" <<'PY'
import sys, subprocess, os, csv, io
list_f, out = sys.argv[1], sys.argv[2]; extra = sys.argv[3:]
cols = ['input','id','msl_b3','L1','L2','L3','L4','L5','L6','L7','msl_lines','msl_gloads','msl_gload_scalar','msl_gload_vec','msl_coherent','msl_loops','msl_atomics',
        'air_dload_total','air_dload_scalar','air_dload_vec','air_loop_br','air_calls','air_atomics','agx_instr','agx_gpr','agx_spill','agx_dload','agx_dload_vec','spv_ins','error']
rows = []; bad = 0
for line in open(list_f):
    t = line.split('#')[0].strip()
    if not t: continue
    p = subprocess.run(['./xray.sh', t] + extra, capture_output=True, text=True)
    r = {'input': t}
    if p.returncode == 2 or p.returncode == 1 and not p.stdout:
        r['error'] = (p.stderr.strip().splitlines() or ['failed'])[-1]; bad += 1
    else:
        for kv in p.stdout.splitlines()[0].split():
            k, _, v = kv.partition('='); r[k] = v
        if p.returncode != 0: r['error'] = 'rc=%d' % p.returncode; bad += 1
    r['input'] = t
    rows.append(r)
buf = io.StringIO(); w = csv.DictWriter(buf, cols, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
open(os.path.join(out, 'batch.csv'), 'w').write(buf.getvalue()); sys.stdout.write(buf.getvalue())
sys.exit(1 if bad else 0)
PY
