#!/usr/bin/env python3
"""xray.py: per-layer view of one shader (offline only, no GPU). See README.md. Use via xray.sh."""
import os, re, sys, json, glob, gzip, shutil, subprocess, tempfile, statistics, collections, argparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
W = os.path.join(ROOT, 'work')
XR = os.path.join(W, 'xray')
MSL_DIRS = [os.path.join(W, 'mslover/dump_h'), os.path.join(W, 'mslover/dump')]
SPV_DIR = os.path.join(W, 'shaderlab/spv')
KKSPV = os.path.join(W, 'kscan/bin/kk_spv_gfx')
HASH_RE = re.compile(r'^[0-9a-f]{16}(\.v[0-9]+)?$')


class XErr(Exception):
    pass


def run(cmd, env=None, timeout=900, cwd=None):
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, cwd=cwd, errors='replace')
    except FileNotFoundError:
        raise XErr('tool not found: %s' % cmd[0])
    except subprocess.TimeoutExpired:
        raise XErr('timeout after %ds: %s' % (timeout, ' '.join(cmd[:4])))
    return p.returncode, p.stdout, p.stderr


def b3(path_or_bytes):
    if isinstance(path_or_bytes, bytes):
        p = subprocess.run(['b3sum', '--no-names'], input=path_or_bytes, capture_output=True)
    else:
        p = subprocess.run(['b3sum', '--no-names', path_or_bytes], capture_output=True)
    if p.returncode != 0:
        raise XErr('b3sum failed')
    return p.stdout.decode().strip()[:16]


def need_tools():
    for t in ('b3sum', 'spirv-dis'):
        if not shutil.which(t):
            raise XErr('missing tool: %s (brew install %s)' % (t, 'b3sum' if t == 'b3sum' else 'spirv-tools'))
    rc, out, _ = run(['xcrun', '-sdk', 'macosx', '-f', 'metal'])
    if rc != 0 or not out.strip():
        raise XErr('missing tool: xcrun metal (Xcode Metal toolchain: xcodebuild -downloadComponent MetalToolchain)')
    return out.strip()


# ------------------------------------------------------------------ layer 4: MSL stats
# KK emits global loads as  tN = *(coherent device T*)(addr);  (coherent optional)
LOAD_RE = re.compile(r'\*\s*\(\s*(?:coherent\s+)?(?:volatile\s+)?device\s+(?:const\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\*\s*\)')
TYPE_BYTES = {'char': 1, 'uchar': 1, 'bool': 1, 'short': 2, 'ushort': 2, 'half': 2, 'int': 4, 'uint': 4, 'float': 4,
              'long': 8, 'ulong': 8}


def type_info(t):
    t = t.replace('packed_', '')
    m = re.match(r'^([a-z]+?)([234]?)$', t)
    if not m or m.group(1) not in TYPE_BYTES:
        return None
    n = int(m.group(2)) if m.group(2) else 1
    return n, TYPE_BYTES[m.group(1)]


def msl_stats(text):
    loads = collections.Counter()
    for m in LOAD_RE.finditer(text):
        ti = type_info(m.group(1))
        if ti:
            n, b = ti
            loads['%s%d' % ('v' if n > 1 else 's', n * b)] += 1   # e.g. s4 = scalar 4B, v16 = vector 16B
    scalar = sum(v for k, v in loads.items() if k[0] == 's')
    vec = sum(v for k, v in loads.items() if k[0] == 'v')
    st = dict(lines=text.count('\n') + 1, bytes=len(text.encode('latin-1', 'replace')),
              gloads=scalar + vec, gload_scalar=scalar, gload_vec=vec,
              gload_widths=dict(sorted(loads.items())),
              coherent=len(re.findall(r'\bcoherent\b', text)),
              loops=len(re.findall(r'\b(?:for|while)\s*\(', text)),
              atomics=len(re.findall(r'\batomic_[a-z_]+', text)),
              barriers=len(re.findall(r'threadgroup_barrier', text)),
              tex=len(re.findall(r'\.(?:sample|read|write|gather|sample_compare)\s*\(', text)),
              simd=len(re.findall(r'\bsimd_[a-z_]+', text)))
    return st


# ------------------------------------------------------------------ layer 5: AIR/LLVM stats
LL_LOAD = re.compile(r'=\s*load\s+(?:atomic\s+)?(?:volatile\s+)?(<\s*\d+\s*x\s*[a-z0-9]+\s*>|[a-z0-9]+)\s*,\s*[^,]*addrspace\((\d+)\)\*')
LL_STORE = re.compile(r'\bstore\s+(?:atomic\s+)?(?:volatile\s+)?(<\s*\d+\s*x\s*[a-z0-9]+\s*>|[a-z0-9]+)\s+[^,]+,\s*[^,]*addrspace\((\d+)\)\*')
LL_BITS = {'i8': 1, 'i16': 2, 'half': 2, 'i32': 4, 'float': 4, 'i64': 8, 'double': 8}


def ll_width(t):
    t = t.replace(' ', '')
    m = re.match(r'^<(\d+)x([a-z0-9]+)>$', t)
    if m:
        return 'v', int(m.group(1)) * LL_BITS.get(m.group(2), 0)
    return 's', LL_BITS.get(t, 0)


AIR_T = {'f32': ('f', 4), 'i32': ('i', 4), 'u32': ('i', 4), 'f16': ('f', 2), 'i16': ('i', 2), 'u16': ('i', 2), 'i8': ('i', 1), 'u8': ('i', 1),
         'i64': ('i', 8), 'u64': ('i', 8)}


def coh_width(t):
    m = re.match(r'^v(\d)(.+)$', t)
    n, base = (int(m.group(1)), m.group(2)) if m else (1, t)
    b = AIR_T.get(base, ('?', 0))[1]
    return '%s%d' % ('v' if n > 1 else 's', n * b)


def ll_stats(text):
    spaces = {'1': 'device', '2': 'constant', '3': 'threadgroup'}
    loads = collections.defaultdict(collections.Counter)
    stores = collections.defaultdict(collections.Counter)
    for rx, d in ((LL_LOAD, loads), (LL_STORE, stores)):
        for m in rx.finditer(text):
            k, b = ll_width(m.group(1))
            d[spaces.get(m.group(2), 'as' + m.group(2))]['%s%d' % (k, b)] += 1
    dev = collections.Counter(loads.get('device', collections.Counter()))
    coh = collections.Counter()
    for m in re.finditer(r'\bcall\s+[^@\n]*@air\.load\.device_coherent\.([a-z0-9]+)\.', text):
        coh[coh_width(m.group(1))] += 1
    for k, v in coh.items():
        dev[k] += v     # coherent loads are opaque air.* calls: LLVM cannot merge them
    scal = sum(v for k, v in dev.items() if k[0] == 's')
    vec = sum(v for k, v in dev.items() if k[0] == 'v')
    calls = collections.Counter(re.findall(r'\bcall\s+[^@\n]*@([A-Za-z0-9_.$]+)', text))
    st = dict(lines=text.count('\n') + 1,
              dload_total=scal + vec, dload_scalar=scal, dload_vec=vec,
              dload_widths=dict(sorted(dev.items())), dload_coherent=sum(coh.values()),
              dstore_widths=dict(sorted(stores.get('device', {}).items())),
              loop_br=len(re.findall(r'!llvm\.loop\b', text)) - len(re.findall(r'^!\d+ = distinct !\{!\d+, ', text, re.M)) if False else len(re.findall(r',\s*!llvm\.loop\s+!\d+', text)),
              calls=sum(calls.values()), calls_distinct=len(calls),
              top_calls=dict(calls.most_common(8)),
              atomics=len(re.findall(r'\batomicrmw\b|\bcmpxchg\b', text)) + sum(v for k, v in calls.items() if 'atomic' in k),
              phis=len(re.findall(r'=\s*phi\b', text)),
              allocas=len(re.findall(r'=\s*alloca\b', text)))
    return st


# ------------------------------------------------------------------ resolve input
def resolve(arg):
    """-> dict(kind='spv'|'msl', path, id, ...)"""
    if os.path.isfile(arg):
        with open(arg, 'rb') as f:
            head = f.read(4)
        if arg.endswith('.spv') or head == b'\x03\x02\x23\x07':
            if head != b'\x03\x02\x23\x07':
                raise XErr('bad input: %s is not SPIR-V (magic 0x07230203 missing)' % arg)
            return dict(kind='spv', path=os.path.abspath(arg), id=b3(arg))
        if arg.endswith('.metal'):
            return dict(kind='msl', path=os.path.abspath(arg), id=b3(arg))
        raise XErr('bad input: %s is neither .spv nor .metal' % arg)
    if HASH_RE.match(arg):
        for d in MSL_DIRS:
            p = os.path.join(d, arg + '.metal')
            if os.path.isfile(p):
                return dict(kind='msl', path=p, id=arg.split('.')[0] if '.' not in arg else arg, hash_arg=arg)
        raise XErr('bad input: no MSL dump for hash %s in %s' % (arg, ', '.join(os.path.relpath(d, ROOT) for d in MSL_DIRS)))
    p = os.path.join(SPV_DIR, arg + '.spv')
    if re.match(r'^s\d+[.\w]*$', arg) and os.path.isfile(p):
        return dict(kind='spv', path=p, id=b3(p), lab_id=arg)
    raise XErr('bad input: %r is not a file, a 16-hex MSL hash (mslover dump), or a shaderlab id' % arg)


def kk_install(arg):
    cands = [arg] if arg else [os.environ.get('XRAY_KK', ''), os.path.join(W, 's3/kk-xray'), os.path.join(W, 's3/kk-vec')]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json')):
            return c
    raise XErr('no KosmicKrisp install found (tried %s); set XRAY_KK or --kk' % ', '.join(c for c in cands if c))


def kk_has_nir_hook(kkdir):
    lib = os.path.join(kkdir, 'install-arm64/lib/libvulkan_kosmickrisp.dylib')
    try:
        return b'KK_NIR_DUMP_DIR' in open(lib, 'rb').read()
    except OSError:
        return False


def kk_collect(spv, kkdir, opt, tmp, tag):
    if not os.path.isfile(KKSPV):
        raise XErr('missing tool: %s (kscan harness; build per work/kscan/tools)' % os.path.relpath(KKSPV, ROOT))
    coll = os.path.join(tmp, 'msl_' + tag); nird = os.path.join(tmp, 'nir_' + tag)
    os.makedirs(coll); os.makedirs(nird)
    env = dict(os.environ, DYLD_LIBRARY_PATH='/opt/homebrew/lib', kk_fake_depth_bounds='true',
               VK_DRIVER_FILES=os.path.join(kkdir, 'install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json'),
               KK_MSL_COLLECT_DIR=coll, KK_NIR_DUMP_DIR=nird,
               MESA_SHADER_CACHE_DISABLE='true', MESA_GLSL_CACHE_DISABLE='true')
    if opt is not None:
        env['KK_MSL_OPT'] = str(opt)
    rc, out, err = run([KKSPV, spv], env=env, timeout=600)
    m = re.search(r'create (ok|FAILED) ([\d.]+) ms', out)
    if rc != 0 or not m or m.group(1) != 'ok':
        raise XErr('KK pipeline creation failed for %s (rc=%d): %s' % (spv, rc, (out + err).strip()[-300:]))
    files = {f: os.path.join(coll, f) for f in os.listdir(coll) if f.endswith('.metal')}
    return files, nird


NIR_BASE = set()


def spv_to_msl(spv, kkdir, opt, tmp):
    """KK writes its internal device-init shaders and a trivial partner stage next to the shader under test:
    subtract the MSL files KK writes for three trivial modules (base/{cs,vs,ps}.spv), then take the largest new one."""
    base = set(); nbase = set()
    for b in ('cs', 'vs', 'ps'):
        files, bn = kk_collect(os.path.join(XR, 'base', b + '.spv'), kkdir, opt, tmp, 'base_' + b)
        base |= {b3(p) for p in files.values()}
        nbase |= {b3(os.path.join(bn, f)) for f in os.listdir(bn) if f.endswith('3_to_msl.nir')}
    files, nird = kk_collect(spv, kkdir, opt, tmp, 'tgt')
    if not files:
        raise XErr('KK produced no MSL for %s' % spv)
    new = {f: p for f, p in files.items() if b3(p) not in base}
    pool = new or files
    best = max(pool, key=lambda f: os.path.getsize(pool[f]))
    NIR_BASE.clear(); NIR_BASE.update(nbase)
    return pool[best], len(new), nird


def nir_for(nird, msl_text):
    """join NIR dumps of the shader under test: the sequence number whose 3_to_msl is the largest"""
    groups = collections.defaultdict(list)
    for f in sorted(os.listdir(nird)):
        m = re.match(r'^(\d+)_([A-Za-z]+)_(.*)\.nir$', f)
        if m:
            groups[m.group(1)].append(f)
    if not groups:
        return None
    def fin(g):
        fs = [f for f in groups[g] if f.endswith('3_to_msl.nir')]
        if not fs or b3(os.path.join(nird, fs[0])) in NIR_BASE:
            return -1
        return os.path.getsize(os.path.join(nird, fs[0]))
    best = max(groups, key=fin)
    parts = []
    for f in groups[best]:
        parts.append('######## %s\n' % f + open(os.path.join(nird, f), errors='replace').read())
    return ''.join(parts)


def cost_lines(h8):
    """layer 7: commits logs + knock-out tables"""
    out = []
    pref = h8[:16]
    for p in sorted(glob.glob(os.path.join(W, 'mslover/msl_*.commits.log'))):
        ms = []
        with open(p, errors='replace') as f:
            for line in f:
                if pref in line and not line.startswith('#'):
                    parts = line.split()
                    try:
                        ms.append(float(parts[1]))
                    except (IndexError, ValueError):
                        pass
        if ms:
            out.append('commit_log %s: %d commits contain it, commit GPU ms mean %.2f median %.2f min %.2f max %.2f (whole commit, not the shader alone)' % (
                os.path.basename(p), len(ms), statistics.mean(ms), statistics.median(ms), min(ms), max(ms)))
    ko = os.path.join(W, 'mslover/ko_hashes.txt')
    if os.path.isfile(ko):
        for line in open(ko):
            if pref in line:
                out.append('ko_hashes.txt: ' + line.strip() + '   (columns as written by mslover: stage hash ...)')
    for p in (os.path.join(ROOT, 'research/msl-override.md'),):
        if os.path.isfile(p):
            for line in open(p, errors='replace'):
                if pref[:8] in line:
                    out.append('msl-override.md: ' + line.strip()[:240])
    return out


def isa_lookup(keys):
    d = os.environ.get('XRAY_ISA_DIR', os.path.join(XR, 'isa'))
    for k in keys:
        for p in glob.glob(os.path.join(d, '*%s*.rdna2.txt' % k)) + glob.glob(os.path.join(d, '*%s*.isa.txt' % k)):
            return p
    return None


def load_index():
    """MSL b3 -> shaderlab id (best effort; built by --build-index)"""
    p = os.path.join(XR, 'idx/kkmsl_b3.tsv')
    d = {}
    if os.path.isfile(p):
        for line in open(p):
            a = line.split()
            if len(a) == 2:
                d[a[0]] = a[1]
    return d


def build_index():
    os.makedirs(os.path.join(XR, 'idx'), exist_ok=True)
    rows = []
    for f in sorted(glob.glob(os.path.join(W, 'shaderlab/msl/kk/*.metal.gz'))):
        data = gzip.open(f).read()
        rows.append('%s %s' % (b3(data), os.path.basename(f)[:-9]))
    open(os.path.join(XR, 'idx/kkmsl_b3.tsv'), 'w').write('\n'.join(rows) + '\n')
    return len(rows)


def write(path, text):
    with open(path, 'w') as f:
        f.write(text)


def fmt_stats(d):
    return '\n'.join('%s: %s' % (k, json.dumps(v) if isinstance(v, dict) else v) for k, v in d.items()) + '\n'


def xray(arg, outroot, kkarg, opt, skip_air=False, quiet=False):
    metal = need_tools()
    inp = resolve(arg)
    tmp = tempfile.mkdtemp(prefix='xray_')
    try:
        kk = None; notes = []
        spv = inp['path'] if inp['kind'] == 'spv' else None
        if inp['kind'] == 'spv':
            kk = kk_install(kkarg)
            msl_path, nmsl, nird = spv_to_msl(spv, kk, opt, tmp)
            msl_text = open(msl_path, errors='replace').read()
            msl_hash = b3(msl_path)
            ident = '%s_o%s' % (inp['id'], opt if opt is not None else 'def')
        else:
            msl_path = inp['path']; msl_text = open(msl_path, errors='replace').read()
            msl_hash = b3(msl_path); nird = None; nmsl = 1
            ident = inp.get('hash_arg') or msl_hash
            idx = load_index()
            if msl_hash in idx:
                cand = os.path.join(SPV_DIR, idx[msl_hash] + '.spv')
                if os.path.isfile(cand):
                    spv = cand
        od = os.path.join(outroot, ident)
        if os.path.isdir(od):
            for f in os.listdir(od):
                p = os.path.join(od, f)
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        os.makedirs(od, exist_ok=True)
        S = collections.OrderedDict()
        S['id'] = ident; S['input'] = inp['kind']; S['msl_b3'] = msl_hash

        # layer 1 ISA
        isa = isa_lookup([ident, msl_hash] + ([os.path.basename(spv)[:-4]] if spv else []))
        if isa:
            shutil.copy(isa, os.path.join(od, '1_isa.txt')); S['L1'] = 'ok'
        else:
            write(os.path.join(od, '1_isa.txt'), 'N/A: no Kyty RDNA2 dump for this shader in %s.\n'
                  'Produce one: run Kyty with ASTROHYBRID_SHADER_DUMP=<guest shader hash hex> KYTY_PROGRAM_CACHE=0 '
                  '(writes <stage>_<hash>.rdna2.txt), copy it to work/xray/isa/ with the SPIR-V/MSL id in the name.\n' %
                  os.path.relpath(os.environ.get('XRAY_ISA_DIR', os.path.join(XR, 'isa')), ROOT))
            S['L1'] = 'na'
        # layer 2 SPIR-V
        if spv:
            rc, out, err = run(['spirv-dis', '--no-color', spv])
            if rc != 0 or not out.strip():
                raise XErr('spirv-dis failed on %s: %s' % (spv, err.strip()[-200:]))
            write(os.path.join(od, '2_spirv.txt'), out)
            S['L2'] = 'ok'; S['spv_ins'] = sum(1 for l in out.splitlines() if re.match(r'^\s+(%\d+ = )?Op', l))
            S['spv'] = os.path.basename(spv)
        else:
            write(os.path.join(od, '2_spirv.txt'), 'N/A: input is MSL and no SPIR-V is linked to it (run `xray.sh --build-index` to link MSL dumps '
                  'that match shaderlab KK MSL byte-for-byte; most mslover dumps come from a different KK build).\n')
            S['L2'] = 'na'
        # layer 3 NIR
        if nird is not None and kk_has_nir_hook(kk):
            nir = nir_for(nird, msl_text)
            if nir:
                write(os.path.join(od, '3_nir.txt'), nir); S['L3'] = 'ok'
                S['nir_loads_global'] = len(re.findall(r'load_global', nir.split('3_to_msl')[-1]))
            else:
                write(os.path.join(od, '3_nir.txt'), 'N/A: KK NIR hook present but wrote nothing.\n'); S['L3'] = 'na'
        else:
            write(os.path.join(od, '3_nir.txt'), 'N/A: %s.\nNIR needs the KK_NIR_DUMP_DIR hook (work/s3/kk-xray) and an SPIR-V input.\n' %
                  ('input is MSL' if nird is None else 'this KK build (%s) has no KK_NIR_DUMP_DIR hook; use XRAY_KK=work/s3/kk-xray' % os.path.relpath(kk, ROOT)))
            S['L3'] = 'na'
        # layer 4 MSL
        shutil.copy(msl_path, os.path.join(od, '4_msl.metal'))
        ms = msl_stats(msl_text)
        write(os.path.join(od, '4_msl_stats.txt'), fmt_stats(ms))
        S['L4'] = 'ok'
        for k in ('lines', 'gloads', 'gload_scalar', 'gload_vec', 'coherent', 'loops', 'atomics'):
            S['msl_' + k] = ms[k]
        # layer 5 AIR
        if skip_air:
            S['L5'] = 'skipped'
        else:
            ll = os.path.join(tmp, 'o.ll')
            sdk = run(['xcrun', '-sdk', 'macosx', '--show-sdk-path'])[1].strip()
            mm = re.search(r'MACOS_MIN := (\S+)', open(os.path.join(ROOT, 'repos/nxnative/dm/mac/min_os.mk')).read()) \
                if os.path.isfile(os.path.join(ROOT, 'repos/nxnative/dm/mac/min_os.mk')) else None
            macmin = os.environ.get('XRAY_MACMIN') or (mm.group(1) if mm else '15.0')
            cmd = ['xcrun', '-sdk', 'macosx', 'metal', '-S', '-emit-llvm', '-isysroot', sdk, '-O1', '-mmacosx-version-min=' + macmin,
                   '-fpreserve-invariance', '-fwrapv', '4_msl.metal', '-o', ll]
            shutil.copy(os.path.join(od, '4_msl.metal'), os.path.join(tmp, '4_msl.metal'))
            rc, out, err = run(cmd, cwd=tmp, timeout=int(os.environ.get('XRAY_METAL_TIMEOUT', '1500')))
            if rc != 0 or not os.path.isfile(ll):
                write(os.path.join(od, '5_air.ll'), 'ERROR: xcrun metal failed rc=%d\n%s\n' % (rc, err[-2000:]))
                write(os.path.join(od, '5_air_stats.txt'), 'ERROR\n')
                S['L5'] = 'FAIL'
            else:
                lltext = open(ll, errors='replace').read().replace(os.path.realpath(tmp) + '/', '').replace(tmp + '/', '')
                write(os.path.join(od, '5_air.ll'), lltext)   # tmp path scrubbed: runs are byte-identical
                st = ll_stats(lltext)
                write(os.path.join(od, '5_air_stats.txt'), fmt_stats(st))
                S['L5'] = 'ok'
                for k in ('dload_total', 'dload_scalar', 'dload_vec', 'loop_br', 'calls', 'atomics'):
                    S['air_' + k] = st[k]
        # layer 6 hook
        hook = os.environ.get('XRAY_AGXDIS') or os.path.join(W, 'agxdis/agxdis.sh')
        a6 = os.path.join(od, '6_agx'); os.makedirs(a6, exist_ok=True)
        if os.path.isfile(hook) and os.access(hook, os.X_OK):
            jf = os.path.join(a6, 'agx_stats.json')
            rc, out, err = run([hook, os.path.join(od, '4_msl.metal'), '--json', jf], timeout=int(os.environ.get('XRAY_AGX_TIMEOUT', '1800')))
            write(os.path.join(a6, 'agx.txt'), out)
            write(os.path.join(a6, 'agxdis.log'), err)
            S['L6'] = 'ok' if rc == 0 else 'FAIL(rc=%d)' % rc
            if rc == 0 and os.path.isfile(jf):
                try:
                    fns = json.load(open(jf))
                    if isinstance(fns, dict):
                        fns = [fns]
                    vec = sum(c for f in fns for k, c in (f.get('dload_w') or {}).items() if not k.endswith('x1'))
                    S['agx_instr'] = sum(f.get('instr', 0) for f in fns)
                    S['agx_gpr'] = max([f.get('gpr', 0) for f in fns] or [0])
                    S['agx_spill'] = sum((f.get('stack_ld', 0) + f.get('stack_st', 0)) for f in fns)
                    S['agx_dload'] = sum(f.get('dload', 0) for f in fns)
                    S['agx_dload_vec'] = vec
                except (ValueError, TypeError, AttributeError) as e:
                    S['L6'] = 'FAIL(bad json: %s)' % e
        else:
            write(os.path.join(a6, 'README.txt'), 'N/A: work/agxdis/agxdis.sh not present (layer 6 is owned by agent agxdis). '
                  'xray.sh calls it as: agxdis.sh <4_msl.metal> <this dir>.\n')
            S['L6'] = 'na'
        # layer 7 cost
        cl = cost_lines(ident if HASH_RE.match(ident) else msl_hash)
        write(os.path.join(od, '7_cost.txt'), ('\n'.join(cl) + '\n') if cl else 'N/A: no commit-log / knock-out data for this hash (mslover/gprof2 logs).\n')
        S['L7'] = 'ok' if cl else 'na'
        write(os.path.join(od, 'summary.txt'), ' '.join('%s=%s' % kv for kv in S.items()) + '\n')
        return od, S
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(prog='xray.sh')
    ap.add_argument('input', nargs='?')
    ap.add_argument('--out', default=os.path.join(XR, 'out'))
    ap.add_argument('--kk'); ap.add_argument('--opt', type=int)
    ap.add_argument('--no-air', action='store_true')
    ap.add_argument('--build-index', action='store_true')
    a = ap.parse_args()
    try:
        if a.build_index:
            need_tools(); print('indexed', build_index()); return 0
        if not a.input:
            raise XErr('usage: xray.sh <msl hash | .spv | .metal | shaderlab id> [--opt N] [--kk DIR] [--out DIR] [--no-air]')
        od, S = xray(a.input, a.out, a.kk, a.opt, skip_air=a.no_air)
        print(open(os.path.join(od, 'summary.txt')).read().strip())
        print('-> ' + od)
        return 1 if any(str(v).startswith('FAIL') for v in S.values()) else 0
    except XErr as e:
        print('xray: ERROR: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
