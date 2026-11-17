#!/usr/bin/env python3
"""prof.py: compute-shader profile.  For every CS that both translators produced MSL for: runtime Metal compile of the KK MSL and of the SPIRV-Cross MSL
(harness.m: pipeline limits maxTotalThreadsPerThreadgroup / threadgroup memory / compile ms), and, for kernels without textures/samplers/BDA,
a dispatch of dummy buffers timed with MTLCommandBuffer GPUStartTime/GPUEndTime only.  Each kernel runs in its own process with a timeout; KK and
SPIRV-Cross runs are interleaved (ABAB).  Output prof.jsonl (resumable).
  prof.py [--limit N] [--groups 512] [--dispatch 1] [--reps 5]"""
import os, sys, json, gzip, tempfile, argparse, subprocess, time
LAB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LAB); import lab

def msl_file(sub, h, d):
    p = os.path.join(d, sub + '.metal')
    open(p, 'w').write(gzip.open(os.path.join(LAB, 'msl', sub, h + '.metal.gz'), 'rt').read()); return p

def hs(path, entry, local, groups, reps, mode, table):
    rc, out, err, cpu, rss, wall = lab.run([os.path.join(LAB, 'harness'), path, entry, '%d,%d,%d' % tuple(local), str(groups), str(reps), mode, str(table)], timeout=120)
    try: return json.loads(out.strip().split('\n')[-1])
    except Exception: return dict(ok=False, stage='harness', err=(err or out)[-200:], rc=rc)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--limit', type=int, default=0); ap.add_argument('--groups', type=int, default=512)
    ap.add_argument('--dispatch', type=int, default=1); ap.add_argument('--reps', type=int, default=5); ap.add_argument('--ids', default='')
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(os.path.join(LAB, 'results.jsonl'))]
    cs = [r for r in rows if r['stage'] == 'cs' and r['kk'].get('ok') and r['sc'].get('ok')]
    if a.ids: cs = [r for r in cs if r['id'] in a.ids.split(',')]
    cs.sort(key=lambda r: r['words'])
    if a.limit: cs = cs[::max(1, len(cs) // a.limit)][:a.limit]
    out = os.path.join(LAB, 'prof.jsonl'); done = set()
    if os.path.exists(out): done = {json.loads(l)['id'] for l in open(out)}
    os.makedirs(os.path.join(LAB, 'tmp'), exist_ok=True)
    with open(out, 'a') as o:
        for r in cs:
            if r['id'] in done: continue
            d = tempfile.mkdtemp(dir=os.path.join(LAB, 'tmp'))
            try:
                kk = msl_file('kk', r['hash'], d); sc = msl_file('sc', r['hash'], d); ent = 'vk_' + r['hash']
                row = dict(id=r['id'], hash=r['hash'], words=r['words'], local=r['local'])
                row['kk_info'] = hs(kk, 'main_entrypoint', r['local'], 1, 1, 'info', 1)
                row['sc_info'] = hs(sc, ent, r['local'], 1, 1, 'info', 0)
                tex = r['sc']['slots']['texture'] + r['sc']['slots']['sampler']
                bda = 'physbuf' in r['caps'] or 'i64atom' in r['caps']
                row['dispatchable'] = bool(a.dispatch and not tex and not bda and row['kk_info'].get('ok') and row['sc_info'].get('ok'))
                if not row['dispatchable']: row['why_not'] = 'textures' if tex else 'bda' if bda else 'compile'
                if row['dispatchable']:
                    for mode in ('pat', 'zero'):
                        res = {'kk': [], 'sc': []}
                        for rnd in range(2):   # ABAB
                            res['kk'].append(hs(kk, 'main_entrypoint', r['local'], a.groups, a.reps, mode, 1))
                            res['sc'].append(hs(sc, ent, r['local'], a.groups, a.reps, mode, 0))
                        row['run_' + mode] = res
                        if any('fault' in x or not x.get('ok') for v in res.values() for x in v): break   # do not retry a faulting kernel with another data mode
            finally:
                import shutil; shutil.rmtree(d, ignore_errors=True)
            o.write(json.dumps(row) + '\n'); o.flush()
main()
