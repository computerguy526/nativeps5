#!/usr/bin/env python3
"""shaderlab driver (offline; no title run, no GPU counters).  See research/shaderlab.md.
  lab.py manifest                      extract.txt + spv/ -> manifest.tsv (unique modules, stage, size)
  lab.py baseline                      KK internal MSL names (device-init noise) -> baseline.txt
  lab.py run [-j N] [--limit N] [--ids a,b] [--stage cs,ps] [--kkenv K=V,..] [--out results.jsonl]
                                       per module: spirv-val, KK MSL + Metal compile, SPIRV-Cross MSL + Metal compile
Inputs/outputs live in work/shaderlab (spv/, msl/kk|sc/*.metal.gz, results.jsonl).  Nothing here is committed.
"""
import os, sys, re, json, gzip, time, shutil, struct, subprocess, tempfile, argparse, collections, multiprocessing as mp

LAB = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(LAB, '..', '..'))
SPV = os.path.join(LAB, 'spv')
KKSPV = os.path.join(ROOT, 'work/kscan/bin/kk_spv_gfx')
KKICD = os.path.join(ROOT, 'work/s3/kk-bary/install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json')
NXT = os.path.join(ROOT, 'repos/nxnative/dm/gfx/tools')
SPIRV_CROSS = os.path.join(NXT, 'spirv_cross_bin/spirv-cross')
MACMIN = re.search(r'MACOS_MIN := (\S+)', open(os.path.join(ROOT, 'repos/nxnative/dm/mac/min_os.mk')).read()).group(1)
METAL = subprocess.run(['xcrun', '-sdk', 'macosx', '-f', 'metal'], capture_output=True, text=True).stdout.strip()
SDKP = subprocess.run(['xcrun', '-sdk', 'macosx', '--show-sdk-path'], capture_output=True, text=True).stdout.strip()
# NXNative's Mac compile line (dmsh_chain.py mac_metal): -O1 -mmacosx-version-min -fpreserve-invariance -fwrapv
MTL_FLAGS = ['-isysroot', SDKP, '-c', '-O1', f'-mmacosx-version-min={MACMIN}', '-fpreserve-invariance', '-fwrapv']
# NXNative's spirv-cross line for SPIR-V shaders (vk_shaders.py)
SC_FLAGS = ['--msl', '--msl-version', '20300', '--msl-decoration-binding', '--msl-texture-1d-as-2d']
STAGE_NAME = {0: 'vs', 4: 'ps', 5: 'cs', 5267: 'mesh', 5268: 'task', 5364: 'task', 5365: 'mesh', 2: 'hs', 3: 'ds', 1: 'hs'}
KK_CC_WORDS = 60000
SC_STAGE = {'vs': 'vert', 'ps': 'frag', 'cs': 'comp'}

# ---------------------------------------------------------------- SPIR-V helpers
def read_words(path):
    d = open(path, 'rb').read()
    return list(struct.unpack('<%dI' % (len(d) // 4), d))

def parse(words):
    """entry model, localsize, capabilities, decorations"""
    i, model, local, caps, dec = 5, None, None, set(), {}
    while i < len(words):
        op, n = words[i] & 0xffff, words[i] >> 16
        if n == 0: break
        if op == 15 and model is None: model = words[i + 1]
        elif op == 17: caps.add(words[i + 1])
        elif op == 16 and words[i + 2] == 17: local = tuple(words[i + 3:i + 6])
        elif op == 71 and n >= 4:
            v = dec.setdefault(words[i + 1], {})
            if words[i + 2] == 33: v['binding'], v['binding_at'] = words[i + 3], i + 3
            elif words[i + 2] == 34: v['set'] = words[i + 3]
        i += n
    return model, local, caps, dec

CAPN = {10: 'f64', 11: 'i64', 22: 'i16', 9: 'f16', 61: 'ballot', 63: 'subgrp_arith', 64: 'subgrp_vote', 65: 'subgrp_shuffle', 66: 'subgrp_shuffle_rel',
        67: 'subgrp_clustered', 68: 'subgrp_quad', 5345: 'bary', 5347: 'physbuf', 5301: 'int64atom', 4437: 'physbuf2', 4445: 'physbuf3'}

# ---------------------------------------------------------------- MSL stats (msl_stats.py)
DECL = re.compile(r'^\s+[A-Za-z0-9_]+ t\d+;$')
REG = re.compile(r'^\s+[A-Za-z0-9_]+ r\d+ = [A-Za-z0-9_]+\(0\);$')
def msl_stats(text):
    decl = stmt = loops = ifs = barrier = nan = coh = ulong = 0
    for l in text.split('\n'):
        if DECL.match(l) or REG.match(l): decl += 1; continue
        s = l.strip()
        if s.startswith('for (') or s.startswith('while ('): loops += 1
        if s.startswith('if ('): ifs += 1
        if s.endswith(';'): stmt += 1
        if 'threadgroup_barrier' in s: barrier += 1
        if 'isnan' in s: nan += 1
        if 'coherent' in s: coh += 1
        if 'ulong' in s: ulong += 1
    return dict(bytes=len(text), stmt=stmt, decl=decl, loops=loops, ifs=ifs, barrier=barrier, isnan=nan, coherent=coh, ulong=ulong)

# ---------------------------------------------------------------- process helpers
def wait_timeout(p, timeout):
    t = time.time()
    while True:
        r = os.wait4(p.pid, os.WNOHANG)
        if r[0]: return r
        if time.time() - t > timeout: raise TimeoutError
        time.sleep(0.02)

def run(cmd, env=None, timeout=600):
    """returns (rc, stdout, stderr, cpu_s (user+sys incl. waited children), rss_mb, wall_s)"""
    with tempfile.TemporaryFile() as o, tempfile.TemporaryFile() as e:
        t = time.time()
        p = subprocess.Popen(cmd, stdout=o, stderr=e, env=env, preexec_fn=lambda: os.nice(5))
        try:
            _, st, ru = wait_timeout(p, timeout)
        except TimeoutError:
            p.kill(); os.wait4(p.pid, 0)
            return 124, '', 'timeout', timeout, 0, timeout
        wall = time.time() - t
        o.seek(0); e.seek(0)
        rc = os.waitstatus_to_exitcode(st)
        return rc, o.read().decode('utf8', 'replace'), e.read().decode('utf8', 'replace'), ru.ru_utime + ru.ru_stime, ru.ru_maxrss / 1e6, wall

def err_class(s):
    """normalise an error message into a failure class"""
    s = s.strip()
    for l in s.split('\n'):
        l = l.strip()
        if re.search(r'error|fail|unsupported|not |timeout|cannot|unknown', l, re.I):
            l = re.sub(r'^.*?(error:|ERROR:)\s*', '', l)
            l = re.sub(r"'[^']*'", "'X'", l); l = re.sub(r'\b\d+\b', 'N', l); l = re.sub(r'/\S+', 'PATH', l)
            return l[:140]
    return s.split('\n')[0][:140] if s else ''

def kk_env(extra):
    env = dict(os.environ, DYLD_LIBRARY_PATH='/opt/homebrew/lib', VK_DRIVER_FILES=KKICD, kk_fake_depth_bounds='true')
    for kv in extra.split(';') if extra else []:
        k, v = kv.split('=', 1); env[k] = v
    return env

def baseline_names():
    p = os.path.join(LAB, 'baseline.txt')
    return set(open(p).read().split()) if os.path.exists(p) else set()

def metal_compile(msl_path, tmp):
    air = os.path.join(tmp, 'o.air')
    rc, _, err, cpu, rss, wall = run([METAL] + MTL_FLAGS + [msl_path, '-o', air], timeout=900)
    ok = rc == 0
    sz = os.path.getsize(air) if ok and os.path.exists(air) else 0
    if os.path.exists(air): os.remove(air)
    return dict(ok=ok, cpu=round(cpu, 3), rss=round(rss), air=sz, err='' if ok else err_class(err))

def save_gz(text, sub, key, kkenv=''):
    d = os.path.join(LAB, 'msl', sub + (('_' + re.sub(r'\W', '', kkenv)) if kkenv else '')); os.makedirs(d, exist_ok=True)
    with gzip.open(os.path.join(d, key + '.metal.gz'), 'wt', compresslevel=3) as f: f.write(text)

# ---------------------------------------------------------------- SPIRV-Cross path (NXNative offline chain)
def remap_bindings(words, dec, refl):
    """vk_shaders.py: renumber descriptor bindings per kind (buffer / texture / sampler slots from 0 in binding order)"""
    KINDS = {'ubos': 'u', 'ssbos': 'b', 'separate_images': 't', 'images': 't', 'separate_samplers': 's', 'textures': 'c'}
    CLS = {'u': 'buffer', 'b': 'buffer', 't': 'texture', 's': 'sampler', 'c': 'texture'}
    by_pair, count = {}, {}
    for k, kind in KINDS.items():
        for r in refl.get(k, []):
            pair = (r.get('set', 0), r['binding']); by_pair[pair] = kind
            dims = r.get('array') or [1]
            count[pair] = dims[0] if len(dims) == 1 and dims[0] else 1
    slots, nxt = {}, {'buffer': 0, 'texture': 0, 'sampler': 0}
    nvar = collections.Counter((d.get('set', 0), d['binding']) for d in dec.values() if 'binding_at' in d)
    for vid, d in sorted(dec.items(), key=lambda t: (t[1].get('set', 0), t[1].get('binding', 0))):
        if 'binding_at' not in d: continue
        pair = (d.get('set', 0), d['binding']); kind = by_pair.get(pair)
        if not kind: continue
        cnt0 = count[pair]
        if nvar[pair] > 1 and cnt0 > 1: pair = pair + (vid,)   # descriptor ARRAYS aliasing one binding (e.g. u32 + u8 views of one SSBO array): SPIRV-Cross cannot alias arrays -> own slot range per var; the host binds the same buffers to both ranges
        if pair in slots: n = slots[pair]
        else:
            cls, k = CLS[kind], cnt0; n = nxt[cls]; nxt[cls] += k
            if kind == 'c': nxt['sampler'] = max(nxt['sampler'], n + k)
            slots[pair] = n
        words[d['binding_at']] = n
    return nxt, bool(refl.get('push_constants'))

def bary_prepass(words):
    """Metal barycentric_coord must be a plain float3, not interpolant<>; GLSL.std.450 InterpolateAtCentroid/Sample/Offset on a BaryCoord(NoPersp)KHR input
    make SPIRV-Cross emit interpolant<> (invalid MSL).  Rewrite them in place to OpLoad of the input (centroid == center without MSAA; sample/offset are approximated by the
    pixel-centre value) padded with OpNop, so word offsets/ids stay stable.  Returns number of rewrites."""
    i, bary, n_rw, setid = 5, set(), 0, None
    while i < len(words):
        op, n = words[i] & 0xffff, words[i] >> 16
        if n == 0: break
        if op == 71 and n >= 4 and words[i + 2] == 11 and words[i + 3] in (5286, 5287): bary.add(words[i + 1])
        elif op == 11 and n >= 3:   # OpExtInstImport
            nm = struct.pack('<%dI' % (n - 2), *words[i + 2:i + n]).split(b'\0')[0]
            if nm == b'GLSL.std.450': setid = words[i + 1]
        elif op == 12 and setid is not None and n >= 6 and words[i + 3] == setid and words[i + 4] in (76, 77, 78) and words[i + 5] in bary:
            words[i:i + 4] = [61 | (4 << 16), words[i + 1], words[i + 2], words[i + 5]]   # OpLoad %type %result %ptr
            for k in range(i + 4, i + n): words[k] = 1 << 16                              # OpNop
            n_rw += 1
        i += n
    return n_rw

def sc_path(path, stage, key, tmp):
    r = dict(ok=False)
    words = read_words(path); model, local, caps, dec = parse(words)
    sc_stage = SC_STAGE.get(stage)
    if not sc_stage:
        r['err'] = 'stage %s not supported by the spirv-cross MSL path' % stage; r['stage_fail'] = 'stage'; return r
    rc, out, err, cpu, rss, wall = run([SPIRV_CROSS, path, '--reflect'], timeout=300)
    if rc:
        r['err'] = 'reflect: ' + err_class(err or out); r['stage_fail'] = 'reflect'; return r
    try: refl = json.loads(out)
    except Exception: r['err'] = 'reflect: bad json'; r['stage_fail'] = 'reflect'; return r
    nxt, pc = remap_bindings(words, dec, refl)
    if stage == 'ps': r['bary_rw'] = bary_prepass(words)
    r['slots'] = dict(nxt, push=int(pc))
    spv = os.path.join(tmp, 'sc.spv'); open(spv, 'wb').write(struct.pack('<%dI' % len(words), *words))
    msl = os.path.join(tmp, 'sc.metal')
    cmd = [SPIRV_CROSS, spv] + SC_FLAGS + ['--rename-entry-point', 'main', 'vk_' + key, sc_stage, '--output', msl]
    if stage == 'vs': cmd.append('--flip-vert-y')
    rc, out, err, cpu, rss, wall = run(cmd, timeout=300)
    if rc and 'MSL 3.1' in (err + out):   # storage-image-array atomics emulation needs MSL 3.1
        cmd[cmd.index('20300')] = '30100'; r['msl31'] = True
        rc, out, err, cpu, rss, wall = run(cmd, timeout=300)
    r['xlate_s'] = round(cpu, 3)
    if rc or not os.path.exists(msl):
        r['err'] = 'xlate: ' + err_class(err or out); r['stage_fail'] = 'xlate'; return r
    text = open(msl).read()
    if pc:   # vk_shaders.py: the push-constant block gets the buffer slot after the descriptor buffers (spirv-cross puts it at buffer(0))
        try:
            tname = refl['types'][refl['push_constants'][0]['type']]['name']
            m = re.search(r'\bvk_' + key + r'\((.*?)\)\s*\{', text, re.S)
            a = re.search(r'constant\s+' + re.escape(tname) + r'&\s+\w+\s+\[\[buffer\((\d+)\)\]\]', m.group(1))
            s0, s1 = m.start(1) + a.start(1), m.start(1) + a.end(1)
            text = text[:s0] + str(nxt['buffer']) + text[s1:]
        except Exception: r['pc_move_failed'] = True
    text = re.sub(r'\bfast::(min|max|clamp)\(', r'precise::\1(', text); open(msl, 'w').write(text)   # vk_shaders.py PRECISE
    r.update(msl_stats(text)); r['ok'] = True
    if nxt['buffer'] + int(pc) > 16 or nxt['texture'] > 31 or nxt['sampler'] > 16:
        r['slot_overflow'] = True   # NXNative's red(): resource slots exceeded (vk_shaders.py limits 16 buffers / 31 textures / 16 samplers)
    r['cc'] = metal_compile(msl, tmp)
    save_gz(text, 'sc', key)
    return r

# ---------------------------------------------------------------- KK path
def kk_path(path, stage, key, tmp, extra_env, base, do_cc=True):
    r = dict(ok=False)
    d = os.path.join(tmp, 'kkmsl'); os.makedirs(d)
    env = kk_env(extra_env); env['KK_MSL_COLLECT_DIR'] = d
    rc, out, err, cpu, rss, wall = run([KKSPV, path], env=env, timeout=600)
    m = re.search(r'create (ok|FAILED) ([\d.]+) ms', out)
    r['create_ms'] = float(m.group(2)) if m else None
    r['create_cpu'] = round(cpu, 3); r['create_rss'] = round(rss)
    files = [f for f in os.listdir(d) if f.endswith('.metal')]
    new = [f for f in files if f not in base] or files
    created = m is not None and m.group(1) == 'ok' and rc == 0
    if not created:
        r['err'] = 'create: ' + err_class(out + '\n' + err); r['stage_fail'] = 'create'
    if not new:
        if created: r['err'] = 'no MSL collected'; r['stage_fail'] = 'create'
        return r
    f = max(new, key=lambda x: os.path.getsize(os.path.join(d, x)))
    p = os.path.join(d, f); text = open(p).read()
    r.update(msl_stats(text)); r['nmsl'] = len(new)
    r['ok'] = created
    if do_cc: r['cc'] = metal_compile(p, tmp)
    else: r['cc'] = dict(skipped=True)
    save_gz(text, 'kk', key, extra_env)
    return r

# ---------------------------------------------------------------- driver
def process(job):
    sid, key, stage, kkenv, only_kk = job
    path = os.path.join(SPV, sid + '.spv')
    tmp = tempfile.mkdtemp(prefix='lab', dir=os.path.join(LAB, 'tmp'))
    res = dict(id=sid, hash=key, stage=stage)
    try:
        words = read_words(path); model, local, caps, dec = parse(words)
        res['words'] = len(words); res['local'] = local; res['capv2'] = 1
        res['caps'] = sorted(CAPN[c] for c in caps if c in CAPN)
        if not only_kk:
            rc, out, err, cpu, rss, wall = run(['spirv-val', '--target-env', 'vulkan1.3', path], timeout=300)
            res['val'] = 'ok' if rc == 0 else err_class(err or out)
        # tier: the xcrun compile of KK's MSL costs up to 22 s per large PS (SPIRV-Cross's MSL 0.4 s): above KK_CC_WORDS words only every 4th graphics shader is compiled (by hash)
        do_cc = stage == 'cs' or len(words) <= KK_CC_WORDS or int(key[:4], 16) % 4 == 0 or bool(kkenv)
        res['kk'] = kk_path(path, stage, key, tmp, kkenv, baseline_names(), do_cc)
        if not only_kk: res['sc'] = sc_path(path, stage, key, tmp)
    except Exception as e:
        res['exc'] = repr(e)[:200]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res

def cmd_manifest(a):
    seen, rows = {}, []
    for l in open(os.path.join(LAB, 'extract.txt')):
        f = l.split()
        if f[1] == 'crash': rows.append((f[0], '-', 'crash', 0)); continue
        h = f[1].split(':')[2]
        if h in seen: continue
        seen[h] = f[0]
        p = os.path.join(SPV, f[0] + '_0.spv')
        if not os.path.exists(p): continue
        model = parse(read_words(p))[0]
        rows.append((f[0] + '_0', h, STAGE_NAME.get(model, 'other%s' % model), os.path.getsize(p) // 4))
    with open(os.path.join(LAB, 'manifest.tsv'), 'w') as o:
        for r in rows: o.write('\t'.join(map(str, r)) + '\n')
    print(collections.Counter(r[2] for r in rows))

def cmd_baseline(a):
    """three tiny modules (one per stage): record every MSL file KK writes (internal meta shaders + trivial partner stages)"""
    os.makedirs(os.path.join(LAB, 'tmp'), exist_ok=True)
    man = [l.rstrip('\n').split('\t') for l in open(os.path.join(LAB, 'manifest.tsv'))]
    names = set()
    for stage in ('cs', 'vs', 'ps'):
        c = sorted((int(m[3]), m[0]) for m in man if m[2] == stage and int(m[3]) > 0)[0]
        d = tempfile.mkdtemp(dir=os.path.join(LAB, 'tmp')); env = kk_env(''); env['KK_MSL_COLLECT_DIR'] = d
        run([KKSPV, os.path.join(SPV, c[1] + '.spv')], env=env)
        names |= {f for f in os.listdir(d) if f.endswith('.metal')}; shutil.rmtree(d)
        print(stage, c)
    open(os.path.join(LAB, 'baseline.txt'), 'w').write('\n'.join(sorted(names)) + '\n')
    print(len(names), 'baseline files')

def cmd_run(a):
    os.makedirs(os.path.join(LAB, 'tmp'), exist_ok=True)
    man = [l.rstrip('\n').split('\t') for l in open(os.path.join(LAB, 'manifest.tsv'))]
    man = [m for m in man if m[2] != 'crash']
    if a.ids: want = set(a.ids.split(',')); man = [m for m in man if m[0] in want]
    if a.stage: man = [m for m in man if m[2] in a.stage.split(',')]
    out = os.path.join(LAB, a.out)
    done = set()
    if os.path.exists(out):
        for l in open(out):
            try: done.add(json.loads(l)['id'])
            except Exception: pass
    jobs = [(m[0], m[1], m[2], a.kkenv, bool(a.kkenv)) for m in man if m[0] not in done]
    if a.order == 'small': jobs.sort(key=lambda j: os.path.getsize(os.path.join(SPV, j[0] + '.spv')))
    else: jobs.sort(key=lambda j: -os.path.getsize(os.path.join(SPV, j[0] + '.spv')))   # big first: best load balance
    if a.limit:
        step = max(1, len(jobs) // a.limit); jobs = jobs[::step][:a.limit]   # spread sample
    print('jobs', len(jobs), 'done', len(done), flush=True)
    with mp.Pool(a.j) as pool, open(out, 'a') as o:
        t = time.time()
        for n, r in enumerate(pool.imap_unordered(process, jobs, chunksize=1)):
            o.write(json.dumps(r) + '\n'); o.flush()
            if n % 50 == 0: print(n, 'of', len(jobs), '%.0fs' % (time.time() - t), flush=True)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('cmd')
    ap.add_argument('-j', type=int, default=4); ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--ids', default=''); ap.add_argument('--stage', default=''); ap.add_argument('--kkenv', default='')
    ap.add_argument('--out', default='results.jsonl'); ap.add_argument('--order', default='')
    a = ap.parse_args()
    {'manifest': cmd_manifest, 'baseline': cmd_baseline, 'run': cmd_run}[a.cmd](a)

if __name__ == '__main__': main()
