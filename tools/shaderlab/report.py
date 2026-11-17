#!/usr/bin/env python3
"""report.py [results.jsonl] : coverage, failure classes, per-stage deltas, top-20 SPIRV-Cross-better shaders -> stdout (markdown pieces)"""
import json, sys, collections, statistics as st, os
LAB = os.path.dirname(os.path.abspath(__file__))
f = sys.argv[1] if len(sys.argv) > 1 else 'results.jsonl'
rows = [json.loads(l) for l in open(os.path.join(LAB, f))]
for r in rows:
    if not r.get('capv2'): r['caps'] = [{'f64': 'i64', 'i64': 'f64'}.get(c, c) for c in r.get('caps', [])]
man = [l.rstrip('\n').split('\t') for l in open(os.path.join(LAB, 'manifest.tsv'))]
ext = [l.split() for l in open(os.path.join(LAB, 'extract.txt'))]
S = lambda x: sorted(x)
med = lambda v: st.median(v) if v else float('nan')
out = []
P = out.append
P('## coverage (unique modules)')
bystage = collections.defaultdict(list)
for r in rows: bystage[r['stage']].append(r)
mc = collections.Counter(m[2] for m in man)
P('| stage | unique SPIR-V | processed | spirv-val ok | KK MSL | KK Metal-compile ok | KK compiled (measured) | SC MSL | SC Metal-compile ok |')
P('|---|---|---|---|---|---|---|---|---|')
tot = collections.Counter()
for s in ('vs', 'ps', 'cs'):
    R = bystage[s]
    v = sum(r.get('val') == 'ok' for r in R); k = sum(r['kk'].get('ok', False) for r in R)
    kc = sum(bool(r['kk'].get('cc', {}).get('ok')) for r in R); kcm = sum('cpu' in r['kk'].get('cc', {}) for r in R)
    sx = sum(r['sc'].get('ok', False) for r in R); sc = sum(bool(r['sc'].get('cc', {}).get('ok')) for r in R)
    P(f'| {s} | {mc[s]} | {len(R)} | {v} | {k} | {kc} | {kcm} | {sx} | {sc} |')
    for n, x in zip(('u', 'p', 'v', 'k', 'kc', 'kcm', 'sx', 'sc'), (mc[s], len(R), v, k, kc, kcm, sx, sc)): tot[n] += x
P(f"| total | {tot['u']} | {tot['p']} | {tot['v']} | {tot['k']} | {tot['kc']} | {tot['kcm']} | {tot['sx']} | {tot['sc']} |")
P('')
P('## failure classes')
for path in ('kk', 'sc'):
    c = collections.Counter()
    for r in rows:
        d = r[path]
        if d.get('err'): c[(d.get('stage_fail', 'create'), d['err'], r['stage'])] += 1
        elif d.get('cc', {}).get('err'): c[('metal', d['cc']['err'], r['stage'])] += 1
    P(f'### {path}')
    for (sf, e, s), n in c.most_common(25): P(f'- {n} x {s} [{sf}] {e}')
vc = collections.Counter((r['val'], r['stage']) for r in rows if r.get('val') != 'ok')
P('### spirv-val'); [P(f'- {n} x {s} {e}') for (e, s), n in vc.most_common(10)]
P('')
both = [r for r in rows if r['kk'].get('cc', {}).get('ok') and r['sc'].get('cc', {}).get('ok') and 'cpu' in r['kk']['cc']]
P(f'## deltas on {len(both)} modules both paths compiled (measured)')
P('| stage | n | KK MSL MB | SC MSL MB | KK stmt k | SC stmt k | KK xcrun CPU s | SC xcrun CPU s | median CPU ratio KK/SC | median stmt ratio | median bytes ratio |')
P('|---|---|---|---|---|---|---|---|---|---|---|')
for s in ('vs', 'ps', 'cs', 'all'):
    B = [r for r in both if s == 'all' or r['stage'] == s]
    if not B: continue
    k = lambda key, p: sum(r[p].get(key, 0) for r in B)
    kc = sum(r['kk']['cc']['cpu'] for r in B); sc = sum(r['sc']['cc']['cpu'] for r in B)
    cr = med([r['kk']['cc']['cpu'] / max(r['sc']['cc']['cpu'], .01) for r in B if r['kk']['cc']['cpu'] > .1])
    sr = med([r['kk']['stmt'] / max(r['sc']['stmt'], 1) for r in B]); br = med([r['kk']['bytes'] / max(r['sc']['bytes'], 1) for r in B])
    P(f"| {s} | {len(B)} | {k('bytes','kk')/1e6:.1f} | {k('bytes','sc')/1e6:.1f} | {k('stmt','kk')/1e3:.0f} | {k('stmt','sc')/1e3:.0f} | {kc:.0f} | {sc:.0f} | {cr:.1f} | {sr:.1f} | {br:.1f} |")
P('')
P('## what KK emits more of (sum over the same modules)')
P('| token | KK | SC |'); P('|---|---|---|')
for key in ('decl', 'isnan', 'coherent', 'ulong', 'ifs', 'loops', 'barrier'):
    P(f"| {key} | {sum(r['kk'].get(key,0) for r in both)} | {sum(r['sc'].get(key,0) for r in both)} |")
P('')
P('## top 20: SPIRV-Cross MSL much better (KK minus SC xcrun CPU s, measured)')
P('| # | id | hash | stage | words | KK stmt | SC stmt | KK bytes | SC bytes | KK s | SC s | KK/SC | KK isnan | KK decl | KK coherent |'); P('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
top = sorted(both, key=lambda r: r['sc']['cc']['cpu'] - r['kk']['cc']['cpu'])[:20]
for i, r in enumerate(top, 1):
    k, s = r['kk'], r['sc']
    P(f"| {i} | {r['id']} | {r['hash']} | {r['stage']} | {r['words']} | {k['stmt']} | {s['stmt']} | {k['bytes']} | {s['bytes']} | {k['cc']['cpu']:.1f} | {s['cc']['cpu']:.2f} | {k['cc']['cpu']/max(s['cc']['cpu'],.01):.0f} | {k['isnan']} | {k['decl']} | {k['coherent']} |")
print('\n'.join(out))
