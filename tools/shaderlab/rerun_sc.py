#!/usr/bin/env python3
"""re-run only the SPIRV-Cross path for modules whose old SC result failed -> sc_fix.jsonl (resumable)"""
import sys, os, json, shutil, tempfile, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab
def job(j):
    sid, key, stage = j
    tmp = tempfile.mkdtemp(prefix='scx', dir=lab.LAB + '/tmp')
    try:
        r = lab.sc_path(lab.SPV + '/' + sid + '.spv', stage, key, tmp)
    except Exception as e: r = dict(ok=False, err='exc ' + repr(e)[:100])
    finally: shutil.rmtree(tmp, ignore_errors=True)
    return dict(id=sid, stage=stage, sc=r)
if __name__ == '__main__':
    man = {l.split('\t')[0]: l.rstrip('\n').split('\t') for l in open(lab.LAB + '/manifest.tsv')}
    out = lab.LAB + '/sc_fix.jsonl'; done = set()
    if os.path.exists(out): done = {json.loads(l)['id'] for l in open(out)}
    jobs = []
    for l in open(lab.LAB + '/results.jsonl'):
        r = json.loads(l); s = r.get('sc')
        if s is None or r['id'] in done: continue
        if not s.get('ok') or not s['cc']['ok']: jobs.append((r['id'], r['hash'], r['stage']))
    print('jobs', len(jobs), flush=True)
    with mp.Pool(int(sys.argv[1]) if len(sys.argv) > 1 else 2) as p, open(out, 'a') as o:
        for r in p.imap_unordered(job, jobs): o.write(json.dumps(r) + '\n'); o.flush()
