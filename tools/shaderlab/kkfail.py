#!/usr/bin/env python3
"""kkfail.py: classify KK create failures whose Metal compile was skipped (xcrun on the saved MSL), writes kkfail.jsonl"""
import os, sys, json, gzip, tempfile, shutil
LAB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LAB); import lab
os.makedirs(os.path.join(LAB, 'tmp'), exist_ok=True)
out = open(os.path.join(LAB, 'kkfail.jsonl'), 'w')
for l in open(os.path.join(LAB, 'results.jsonl')):
    r = json.loads(l); k = r['kk']
    if not (k.get('stage_fail') == 'create'): continue
    d = tempfile.mkdtemp(dir=os.path.join(LAB, 'tmp'))
    try:
        p = os.path.join(d, 'k.metal'); open(p, 'w').write(gzip.open(os.path.join(LAB, 'msl/kk', r['hash'] + '.metal.gz'), 'rt').read())
        cc = lab.metal_compile(p, d) if k.get('cc', {}).get('skipped') or 'cc' not in k else k['cc']
        out.write(json.dumps(dict(id=r['id'], hash=r['hash'], stage=r['stage'], err=cc.get('err'), ok=cc.get('ok'))) + '\n')
    except Exception as e:
        out.write(json.dumps(dict(id=r['id'], hash=r['hash'], stage=r['stage'], err='nomsl ' + repr(e)[:80])) + '\n')
    finally: shutil.rmtree(d, ignore_errors=True)
