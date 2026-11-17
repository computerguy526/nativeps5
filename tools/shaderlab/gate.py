#!/usr/bin/env python3
"""KosmicKrisp codegen regression/benefit gate (offline, CPU-only apart from KK pipeline create).  See research/shaderlab.md sec 8.
  gate.py select                       build gate_ids.txt (fixed subset, ~300 modules) from results.jsonl
  gate.py run <kk_install_dir> [--baseline] [-j 3] [--out gate_out.jsonl]
        per module: KK create (own install) -> MSL -> xcrun metal compile x2 (median CPU) -> compare with gate_base.json
        --baseline: record gate_base.json + gate_base_msl/ from this install instead of comparing
"""
import os, sys, re, json, gzip, time, shutil, statistics, tempfile, random, argparse, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab
L = lab.LAB
IDS = os.path.join(L, 'gate_ids.txt'); BASE = os.path.join(L, 'gate_base.json'); BMSL = os.path.join(L, 'gate_base_msl')
FP = {'barrier': 'threadgroup_barrier', 'atomic': 'atomic_', 'sample': '.sample(', 'read': '.read(', 'write': '.write(', 'gather': '.gather(',
      'discard': 'discard_fragment', 'simd': 'simd_', 'for': 'for (', 'while': 'while (', 'ret': 'return'}
CAL = ['s1.a0_0', 's2.a0_0', 's4013_0']   # calibration modules (stored baseline MSL recompiled to measure machine noise)

def select():
    R = [json.loads(l) for l in open(os.path.join(L, 'results.jsonl'))]
    rnd = random.Random(20261117)
    def cc(r, p): return r.get(p, {}).get('cc', {})
    both = [r for r in R if r.get('kk', {}).get('ok') and cc(r, 'kk').get('ok') and r.get('sc', {}).get('ok') and cc(r, 'sc').get('ok')]
    ps = sorted((r for r in both if r['stage'] == 'ps' and cc(r, 'sc')['cpu'] > 0.2), key=lambda r: -cc(r, 'kk')['cpu'] / cc(r, 'sc')['cpu'])[:50]
    cs = sorted((r for r in R if r['stage'] == 'cs' and r['kk'].get('ok')), key=lambda r: -r['kk']['stmt'])[:40]
    fail = [l for l in open(os.path.join(L, 'ids_fail.txt')).read().replace('\n', ',').split(',') if l]
    chosen = {r['id'] for r in ps + cs} | set(fail)
    ok = [r for r in R if r['kk'].get('ok') and cc(r, 'kk').get('ok') and r['id'] not in chosen]
    rr = []
    for st in ('vs', 'ps', 'cs'):
        c = sorted((r['id'] for r in ok if r['stage'] == st)); rr += rnd.sample(c, 50)
    ids = [r['id'] for r in ps] + [r['id'] for r in cs] + rr + fail + CAL
    seen = []; [seen.append(i) for i in ids if i not in seen]
    open(IDS, 'w').write('\n'.join(seen) + '\n'); print(len(seen), 'ids (ps50 cs40 rnd150 wa17', len(fail), '+cal)')

def fingerprint(t):
    d = {k: t.count(v) for k, v in FP.items()}
    m = re.search(r'\b(vk_\w+|main\w*)\(([^)]*)\)\s*\{', t, re.S)
    d['sig'] = re.sub(r'\s+', ' ', m.group(2)).strip() if m else ''
    return d

def med_compile(msl, tmp, n=2):
    rs = [lab.metal_compile(msl, tmp) for _ in range(n)]
    if not all(r['ok'] for r in rs): return rs[0] if not rs[0]['ok'] else rs[1]
    r = dict(rs[0]); r['cpu'] = round(statistics.median(x['cpu'] for x in rs), 3); return r

def job(j):
    sid, key, stage, install, baseline, icdp = j
    lab.KKICD = icdp   # pool workers re-import lab (spawn): the parent's lab.KKICD is lost, so pass it in the job
    tmp = tempfile.mkdtemp(prefix='gate', dir=os.path.join(L, 'tmp')); res = dict(id=sid, hash=key, stage=stage)
    try:
        if sid in CAL:   # recompile the stored original MSL: pure machine-noise calibration
            p = os.path.join(tmp, 'c.metal'); open(p, 'wb').write(gzip.open(os.path.join(L, 'msl/kk', key + '.metal.gz')).read())
            res['cal'] = med_compile(p, tmp)['cpu']; return res
        d = os.path.join(tmp, 'kk'); os.makedirs(d)
        env = lab.kk_env(''); env['KK_MSL_COLLECT_DIR'] = d
        rc, out, err, cpu, rss, wall = lab.run([lab.KKSPV, os.path.join(lab.SPV, sid + '.spv')], env=env, timeout=600)
        m = re.search(r'create (ok|FAILED) ([\d.]+) ms', out); created = bool(m) and m.group(1) == 'ok' and rc == 0
        res['create_ok'] = created; res['create_s'] = round(cpu, 2)
        base = lab.baseline_names()
        files = [f for f in os.listdir(d) if f.endswith('.metal')]; new = [f for f in files if f not in base] or files
        if not created or not new: res['ok'] = False; res['err'] = 'create failed' if not created else 'no MSL'; return res
        p = os.path.join(d, max(new, key=lambda x: os.path.getsize(os.path.join(d, x)))); text = open(p).read()
        res.update(lab.msl_stats(text)); res['fp'] = fingerprint(text)
        cc = med_compile(p, tmp); res['cc'] = cc; res['ok'] = cc['ok']
        if not cc['ok']: res['err'] = cc['err']
        if baseline:
            os.makedirs(BMSL, exist_ok=True)
            with gzip.open(os.path.join(BMSL, key + '.gz'), 'wt', compresslevel=3) as f: f.write(text)
        else:
            bp = os.path.join(BMSL, key + '.gz')
            if os.path.exists(bp):
                bt = gzip.open(bp, 'rt').read(); res['same_text'] = bt == text
                if bt != text: res['fp_base'] = fingerprint(bt)
    except Exception as e:
        res['ok'] = False; res['err'] = 'exc ' + repr(e)[:150]
    finally: shutil.rmtree(tmp, ignore_errors=True)
    return res

def run(a):
    install = os.path.abspath(a.install)
    icd_src = os.path.join(install, 'share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json'); lib = os.path.join(install, 'lib/libvulkan_kosmickrisp.dylib')
    assert os.path.exists(icd_src) and os.path.exists(lib), 'not a KK install dir (need share/vulkan/icd.d/*.json and lib/libvulkan_kosmickrisp.dylib)'
    os.makedirs(os.path.join(L, 'tmp'), exist_ok=True)
    icd = tempfile.mkdtemp(prefix='icd', dir=os.path.join(L, 'tmp')) + '/icd.json'
    j = json.load(open(icd_src)); j['ICD']['library_path'] = lib; json.dump(j, open(icd, 'w')); lab.KKICD = icd
    man = {m[0]: m for m in (l.rstrip('\n').split('\t') for l in open(os.path.join(L, 'manifest.tsv')))}
    ids = open(IDS).read().split()
    jobs = [(i, man[i][1], man[i][2], install, a.baseline, icd) for i in ids]
    jobs.sort(key=lambda x: -os.path.getsize(os.path.join(lab.SPV, x[0] + '.spv')))
    t0 = time.time(); res = []
    with mp.Pool(a.j) as pool:
        for n, r in enumerate(pool.imap_unordered(job, jobs, chunksize=1)):
            res.append(r)
            if n % 40 == 0: print(n, 'of', len(jobs), '%.0fs' % (time.time() - t0), flush=True)
    out = {r['id']: r for r in res}
    json.dump(dict(install=install, wall=round(time.time() - t0), res=out), open(os.path.join(L, 'gate_base.json' if a.baseline else a.out), 'w'))
    if a.baseline: print('baseline written', len(out), 'modules from', install, '%.0fs' % (time.time() - t0)); return
    report(out, install, time.time() - t0)

def tot(rs, k): return sum(r.get(k, 0) for r in rs)
def report(out, install, wall):
    b = json.load(open(BASE)); B = b['res']
    ids = [i for i in out if 'cal' not in out[i]]
    # machine-noise factor: median compile-CPU ratio over modules whose MSL text is byte-identical to baseline (pure noise); fallback: 3 small calibration recompiles
    same_r = [out[i]['cc']['cpu'] / B[i]['cc']['cpu'] for i in ids if out[i].get('same_text') and out[i].get('ok') and B[i].get('ok') and B[i]['cc']['cpu'] > 1.0]
    if len(same_r) >= 15: cal_n, how = statistics.median(same_r), '%d byte-identical modules' % len(same_r)
    else: cal_n, how = statistics.median([out[i]['cal'] / B[i]['cal'] for i in CAL if i in out and i in B and B[i].get('cal')] or [1.0]), 'calibration recompiles (few identical modules; small, noisy)'
    print('GATE %s vs baseline %s   wall %.0fs   machine-noise factor %.2f from %s; CPU below is divided by it' % (install, b['install'], wall, cal_n, how))
    fails = [i for i in ids if not out[i].get('ok')]; bfails = [i for i in ids if i in B and not B[i].get('ok')]
    fixed = sorted(set(bfails) - set(fails)); newf = sorted(set(fails) - set(bfails))
    print('FAILURES: now %d, baseline %d; FIXED %d; NEW REGRESSIONS %d %s' % (len(fails), len(bfails), len(fixed), len(newf), newf[:10]))
    for i in newf[:10]: print('   REGRESSION', i, out[i].get('err'))
    both = [i for i in ids if out[i].get('ok') and i in B and B[i].get('ok')]
    print('compared modules (ok in both): %d' % len(both))
    groups = [('all', both)] + [(g, [i for i in both if out[i]['stage'] == g]) for g in ('vs', 'ps', 'cs')]
    print('%-6s %4s | %9s %9s %7s | %9s %9s %7s | %9s %9s %7s | %7s %7s' % ('grp', 'n', 'MSL MB', 'base', 'ratio', 'stmt k', 'base', 'ratio', 'cpu s', 'base', 'ratio', 'med cpu', 'med stm'))
    for g, s in groups:
        if not s: continue
        n = lambda k, o: sum(o[i][k] if k in ('bytes', 'stmt') else o[i]['cc']['cpu'] for i in s)
        nb, ob, ns, os_, nc, oc = n('bytes', out), n('bytes', B), n('stmt', out), n('stmt', B), n('cpu', out) / cal_n, n('cpu', B)
        mc = statistics.median(out[i]['cc']['cpu'] / cal_n / max(B[i]['cc']['cpu'], 0.05) for i in s); ms = statistics.median(out[i]['stmt'] / max(B[i]['stmt'], 1) for i in s)
        print('%-6s %4d | %9.1f %9.1f %7.3f | %9.1f %9.1f %7.3f | %9.1f %9.1f %7.3f | %7.3f %7.3f' % (g, len(s), nb / 1e6, ob / 1e6, nb / ob, ns / 1e3, os_ / 1e3, ns / os_, nc, oc, nc / oc, mc, ms))
    worst = sorted(both, key=lambda i: -(out[i]['cc']['cpu'] / cal_n) / max(B[i]['cc']['cpu'], 0.05))[:5]
    print('top-5 CPU regressions (ratio): ' + ', '.join('%s %.2f' % (i, out[i]['cc']['cpu'] / cal_n / max(B[i]['cc']['cpu'], .05)) for i in worst))
    best = sorted(both, key=lambda i: (out[i]['cc']['cpu'] / cal_n) / max(B[i]['cc']['cpu'], 0.05))[:5]
    print('top-5 CPU wins        (ratio): ' + ', '.join('%s %.2f' % (i, out[i]['cc']['cpu'] / cal_n / max(B[i]['cc']['cpu'], .05)) for i in best))
    same = [i for i in both if out[i].get('same_text')]; diff = [i for i in both if not out[i].get('same_text')]
    bad = []
    for i in diff:
        fb, fn = out[i].get('fp_base') or B[i].get('fp'), out[i]['fp']
        d = [k for k in fn if fb and fn[k] != fb.get(k)]
        if d: bad.append((i, d))
    print('MSL TEXT: %d identical to baseline, %d changed; SANITY (entry signature + barrier/atomic/sample/read/write/gather/discard/simd/loop/return counts): %d changed modules differ in a fingerprint field' % (len(same), len(diff), len(bad)))
    for i, d in bad[:12]: print('   FINGERPRINT', i, out[i]['stage'], {k: (B[i]['fp'][k], out[i]['fp'][k]) for k in d})
    print('(fingerprint diffs are expected only for loop/return/sample-count changes from intended transforms; barrier/atomic/write/sig diffs = suspect semantic change)')
    verdict = 'PASS' if not newf else 'FAIL (new compile failures)'
    if bad and any(k in ('barrier', 'atomic', 'write', 'sig', 'discard') for _, d in bad for k in d): verdict += ' / REVIEW (semantic fingerprint differs)'
    print('VERDICT:', verdict)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd'); ap.add_argument('install', nargs='?'); ap.add_argument('--baseline', action='store_true')
    ap.add_argument('-j', type=int, default=3); ap.add_argument('--out', default='gate_out.json'); a = ap.parse_args()
    if a.cmd == 'report':
        o = json.load(open(os.path.join(L, a.install or 'gate_out.json'))); report(o['res'], o['install'], o['wall'])
    else: select() if a.cmd == 'select' else run(a)

# re-print a stored report:  python3 -I gate.py report gate_out.json
