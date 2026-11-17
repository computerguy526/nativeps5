#!/usr/bin/python3
"""gpucache_prune.py - safe pruner for per-workdir GPU cache version folders.

Run: python3 -I tools-host/gpucache_prune.py [--apply]     (default = dry run, read-only)

How the folders are keyed (AnyPS5 ShaderCacheVersion.cmake / ShaderDiskCache / GpuCacheStore):
  <workdir>/home/Library/Application Support/nativeps5/<TITLE>/gpu-cache/<HASH>/
  <HASH> = the ShaderDiskCache "SourceVersion": a 16-hex hash of the shader-recompiler SOURCE tree
  (core/shader/recompiler), baked into libSceAgcDriver by ShaderCacheVersion.cmake (generated
  ShaderCacheVersion.cpp). Inside the folder: recompiled SPIR-V <hash>.bin, vk-pipelines-<vendor>-<dev>-
  <driver>-<UUID>.bin, kk-archive-...-<macOS build>.mtl4a, journal. KK build / macOS / GPU are keyed
  inside (file names, gpucache.sqlite v_mesa/v_macos/v_gpu), NOT in the folder name.
  pipeline.stamp "recompiler=<HASH>" = the folder the last run used (current); gpucache.sqlite meta
  stage.recompiler is the same. A lib build maps to its hash via
  wt/<name>/build-arm64/.../shader_recompiler_agc_driver_generated/ShaderCacheVersion.cpp.

Rules: never the current folder (stamp or sqlite); never a canonical-build folder in a never-delete
workdir; keep newest N (--keep-extra, default 1) otherwise-prunable folders per workdir; skip workdirs
whose path appears in a process (args/env) or that have open files.

Sizes: "alloc" = sum of st_blocks (APFS clones counted in full = UPPER BOUND). "est.freed" is
clone-aware: a file keyed (version, relpath, size) that survives in any KEPT folder of any workdir is
assumed to share blocks (cp -c) and frees nothing; if all holders are pruned it frees its size.
"""
import argparse, os, re, shutil, subprocess, time, glob, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEX16 = re.compile(r'^[0-9a-f]{16}$')

# never-delete workdirs (work/briefs/common-2026-11-15.md) + Astro canonical workdir
NEVER_DELETE_WORKDIRS = {'w_ic', 'w_int4', 'w_int6', 'w_int7', 'w_sf', 'w_up1', 'w_astro'}
# lib builds (wt/<name>) used by any canonical config (env.sh, env_int4.sh, run.sh, run_astro.sh, never-delete list)
CANON_BUILDS = {
    'ppsa30552': ['anyps5-dark4', 'anyps5-int6', 'anyps5-int4', 'anyps5-int5', 'anyps5-up1', 'anyps5-poison'],
    'ppsa21564': ['anyps5-astro-int2', 'anyps5-astro2', 'anyps5-astrobase'],
}
WORKDIR_GLOBS = ['work/ppsa21564/boot/w_*', 'work/ppsa30552/boot/fast/w_*']


def build_versions():
    m = collections.defaultdict(list)
    pat = 'wt/*/build-arm64/core/libs/prx/libSceAgcDriver/shader_recompiler_agc_driver_generated/ShaderCacheVersion.cpp'
    for f in glob.glob(os.path.join(ROOT, pat)):
        try:
            txt = open(f, errors='replace').read()
        except OSError:
            continue
        h = re.search(r'SourceVersion\s*=\s*0x([0-9a-f]{16})', txt)
        if h:
            m[h.group(1)].append(f.split('/wt/')[1].split('/')[0])
    return m


def read_stamp(cache):
    cur = None
    try:
        for line in open(os.path.join(cache, 'pipeline.stamp'), errors='replace'):
            if line.startswith('recompiler='):
                cur = line.strip().split('=', 1)[1]
    except OSError:
        pass
    return cur


def read_sqlite_recompiler(cache):
    p = os.path.join(cache, 'gpucache.sqlite')
    if not os.path.exists(p):
        return None
    try:
        import sqlite3
        c = sqlite3.connect('file:%s?mode=ro&immutable=1' % p, uri=True)
        r = c.execute("select value from meta where key='stage.recompiler'").fetchone()
        c.close()
        return r[0] if r else None
    except Exception:
        return None


def scan_folder(path):
    tot = 0; files = {}; newest = os.lstat(path).st_mtime; n = 0
    for dp, dn, fn in os.walk(path):
        for f in fn:
            fp = os.path.join(dp, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            a = st.st_blocks * 512
            tot += a; n += 1
            files[(os.path.relpath(fp, path), st.st_size)] = a
            newest = max(newest, st.st_mtime)
    return tot, files, newest, n


def running_workdirs(rels):
    busy = {}
    try:
        out = subprocess.run(['ps', 'eww', '-A', '-o', 'pid=,command='], capture_output=True, text=True).stdout
    except Exception:
        out = ''
    for w in rels:
        key = os.path.join(ROOT, w)
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            exe = os.path.basename(parts[1].split(' ', 1)[0])
            # only real consumers; shells/agents merely mentioning the path are not users of the cache
            if not (exe.startswith(('native', 'nps5', 'agc_frame_replay', 'run_sandboxed')) or exe.endswith('.sh') and 'run' in exe or exe.startswith('gtimeout')):
                continue
            if (key + '/') in line or (key + ' ') in line or line.endswith(key):
                busy[w] = line.split(None, 1)[0]
                break
    return busy


def has_open_files(cache):
    try:
        r = subprocess.run(['lsof', '-t', '+D', cache], capture_output=True, text=True, timeout=90)
        return bool(set(r.stdout.split()) - {str(os.getpid())})
    except Exception:
        return False


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return '%d B' % n if u == 'B' else '%.2f %s' % (n, u)
        n /= 1024.0


def other_stale():
    res = []
    cands = [os.path.join(ROOT, 'work/s3/shader_cache')]
    cands += glob.glob(os.path.join(ROOT, 'wt/*/build-arm64/shader_cache'))
    cands += glob.glob(os.path.join(ROOT, 'wt/*/build-arm64/**/shader_cache'), recursive=True)
    cands += glob.glob(os.path.join(ROOT, 'work/**/shader_cache'), recursive=True)
    for c in sorted(set(cands)):
        if os.path.isdir(c):
            try:
                o = subprocess.run(['du', '-sk', c], capture_output=True, text=True).stdout.split()[0]
                res.append((int(o) * 1024, os.path.relpath(c, ROOT)))
            except Exception:
                pass
    return res


def main():
    ap = argparse.ArgumentParser(description='Prune stale GPU cache version folders (dry run by default).')
    ap.add_argument('--apply', action='store_true', help='delete PRUNE folders')
    ap.add_argument('--keep-extra', type=int, default=1, help='newest N otherwise-prunable folders to keep per workdir')
    ap.add_argument('--no-lsof', action='store_true', help='skip open-files check')
    a = ap.parse_args()

    bv = build_versions()
    canon = {t: {h for h, names in bv.items() if any(n in b for n in names)} for t, b in CANON_BUILDS.items()}

    wds = []
    for g in WORKDIR_GLOBS:
        wds += sorted(glob.glob(os.path.join(ROOT, g)))
    rels = [os.path.relpath(w, ROOT) for w in wds]
    busy = running_workdirs(rels)

    plan = []
    for w, rel in zip(wds, rels):
        title = rel.split('/')[1]
        for cache in glob.glob(os.path.join(w, 'home/Library/Application Support/nativeps5/*/gpu-cache')):
            cur = read_stamp(cache); sq = read_sqlite_recompiler(cache)
            wbase = os.path.basename(w)
            never = wbase in NEVER_DELETE_WORKDIRS
            skip = rel in busy or (not a.no_lsof and has_open_files(cache))
            rows = []
            for d in sorted(os.listdir(cache)):
                p = os.path.join(cache, d)
                if not HEX16.match(d) or not os.path.isdir(p) or os.path.islink(p):
                    continue
                alloc, files, newest, n = scan_folder(p)
                rows.append(dict(wd=rel, title=title, ver=d, path=p, alloc=alloc, files=files, mtime=newest, n=n,
                                 cur=cur, sq=sq, never=never, skip=skip, builds=bv.get(d, []),
                                 canon=d in canon.get(title, set())))
            cand = []
            for r in rows:
                if r['skip']:
                    r['verdict'] = 'SKIP'; r['why'] = 'workdir busy (pid/open files %s)' % busy.get(rel, '')
                elif r['ver'] == r['cur']:
                    r['verdict'] = 'KEEP'; r['why'] = 'current (pipeline.stamp)'
                elif r['ver'] == r['sq']:
                    r['verdict'] = 'KEEP'; r['why'] = 'current (sqlite stage.recompiler)'
                elif r['cur'] is None:
                    r['verdict'] = 'KEEP'; r['why'] = 'no readable stamp'
                elif r['never'] and r['canon']:
                    r['verdict'] = 'KEEP'; r['why'] = 'canonical build in never-delete workdir'
                else:
                    cand.append(r)
            cand.sort(key=lambda r: -r['mtime'])
            for i, r in enumerate(cand):
                if i < a.keep_extra:
                    r['verdict'] = 'KEEP'; r['why'] = 'safety margin (newest %d extra)' % a.keep_extra
                else:
                    r['verdict'] = 'PRUNE'
                    r['why'] = ('build %s, not canonical' % ','.join(r['builds'][:3])) if r['builds'] else 'no known build has this hash'
            plan += rows

    holders = collections.defaultdict(lambda: [0, 0])
    for r in plan:
        for k in r['files']:
            holders[(r['ver'],) + k][0 if r['verdict'] != 'PRUNE' else 1] += 1
    for r in plan:
        est = 0
        if r['verdict'] == 'PRUNE':
            for k, al in r['files'].items():
                h = holders[(r['ver'],) + k]
                if h[0] == 0:
                    est += al / max(h[1], 1)   # all holders pruned: shared blocks freed once
        r['est'] = est

    out = ['gpucache_prune %s  mode=%s keep-extra=%d' % (time.strftime('%F %T'), 'APPLY' if a.apply else 'DRY-RUN', a.keep_extra)]
    for t, s in canon.items():
        out.append('canonical hashes %s: %s' % (t, ', '.join(sorted(s))))
    out.append('')
    hdr = '%-30s %-16s %9s %6s %-11s %-5s %9s  %s' % ('workdir', 'version', 'alloc', 'files', 'mtime', 'verd', 'est.freed', 'why')
    out += [hdr, '-' * len(hdr)]
    per = collections.OrderedDict()
    tot_alloc = tot_est = 0
    for r in plan:
        out.append('%-30s %-16s %9s %6d %-11s %-5s %9s  %s%s' % (
            r['wd'].replace('work/', '').replace('/boot', '').replace('/fast', ''), r['ver'], human(r['alloc']), r['n'],
            time.strftime('%m-%d %H:%M', time.localtime(r['mtime'])), r['verdict'],
            human(r['est']) if r['verdict'] == 'PRUNE' else '-', r['why'],
            ' [builds: %s]' % ','.join(r['builds'][:3]) if r['builds'] and r['verdict'] != 'PRUNE' else ''))
        d = per.setdefault(r['wd'], [0, 0, 0, 0.0, 0.0])
        d[{'KEEP': 0, 'PRUNE': 1, 'SKIP': 2}[r['verdict']]] += 1
        if r['verdict'] == 'PRUNE':
            d[3] += r['alloc']; d[4] += r['est']; tot_alloc += r['alloc']; tot_est += r['est']
    out += ['', 'Per workdir (keep/prune/skip, du upper bound, clone-aware estimate):']
    for w, d in per.items():
        out.append('  %-30s keep %d prune %d skip %d  du<= %9s  est %9s' % (w.replace('work/', ''), d[0], d[1], d[2], human(d[3]), human(d[4])))
    out += ['', 'TOTAL PRUNE: %d folders, alloc UPPER BOUND (clones double-counted) %s, clone-aware estimate %s'
            % (sum(1 for r in plan if r['verdict'] == 'PRUNE'), human(tot_alloc), human(tot_est)),
            'Estimate assumes identical (version,relpath,size) files across workdirs share APFS blocks; confirm with df after a real run.',
            '', 'Other big stale cache-like dirs (report only):']
    for sz, p in other_stale():
        out.append('  %9s  %s' % (human(sz), p))
    print('\n'.join(out))

    if a.apply:
        busy2 = running_workdirs(rels)
        for r in plan:
            if r['verdict'] != 'PRUNE':
                continue
            p = r['path']
            if (r['wd'] in busy2 or os.path.islink(p) or not HEX16.match(os.path.basename(p))
                    or not os.path.realpath(p).startswith(os.path.join(ROOT, 'work') + '/')):
                print('refuse', p); continue
            print('rm', p); shutil.rmtree(p)


if __name__ == '__main__':
    main()
