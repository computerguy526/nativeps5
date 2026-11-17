#!/usr/bin/env python3
"""nps5 cache: the pipeline list and the compiled caches of one title (bringup-design.md, section 5). Standard library only.

  nps5 cache record  <id> [--scene NAME ...] [--secs N] [--dry-run]   one first visit per scene into an EMPTY cache arm
  nps5 cache merge   <id> [--from DIR ...] [--rebuild] [--write]      export one list per recording, merge with the committed list
  nps5 cache verify-list <id> [--list FILE] [--shaders JOURNAL ...]   no game code in the list; writes <list>.verified
  nps5 cache prepare <id> [--secs N] [--dry-run]                      USERS RUN THIS ONCE: fresh cache + fresh Metal cache id, builds
                                                                      every listed pipeline in one start that exits before the game runs
  nps5 cache check   <id> [--scene NAME ...] [--secs N] [--dry-run]   first-visit runs on a clone of the prepared cache: live compiles, stalls
  nps5 cache status  <id>                                              fresh | partial | prepared | stale (exit 0 only when prepared)

Identity is derived, never typed. stack key = 8 hex of sha256(list sha256, Kyty commit, KosmicKrisp commit, ps5recomp commit,
NPS5_HW_CACHE_KEY of hwpresets); cache dir = <scratch>/cache/<id>/<gpu>-<stack key>/; Metal cache id =
com.nativeps5.<gpu>.<id>.<stack key>. A new list, a new KK / Kyty / ps5recomp commit or another GPU family / OS build gives a new
key, so a stale cache is never reused silently. Compiled caches are never committed; only games/<id>/pipelines.kyplst and
its .verified are. The runs go through `nps5 run` (title queue, memory limit, one GPU process); nothing here starts a game itself.
KK_LIBCACHE_DIR (KK's library cache) is set to <cache dir>/kk-libcache; after `prepare`, $NPS5_KK_LIBCACHE_BUILD <dir> <jobs> (KK's
kk_libcache_build.sh) turns the collected MSL into metallibs when that script exists.
Tool: $NPS5_KYPLST_TOOL, else <kyty>/build-arm64/kyty_pipeline_list. Exit codes: 0 ok, 1 failed / stale, 2 usage."""
import hashlib, os, re, shlex, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run as runmod  # noqa: E402

STALL_MS = 250.0
KEY_FIELDS = ("list", "kyty", "kk", "ps5recomp", "hw")


class CacheError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg); self.code = code


# ---------------------------------------------------------------- identity (pure)
def stack_key(list_sha, kyty, kk, recomp, hw):
    """8 hex of the stack identity. Every input is a string; a missing commit is spelled 'unknown' by the caller."""
    s = "\n".join("%s=%s" % (k, v) for k, v in zip(KEY_FIELDS, (list_sha, kyty, kk, recomp, hw)))
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def metal_id(gpu, tid, key):
    return "com.nativeps5.%s.%s.%s" % (gpu, tid, key)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class Ctx:
    """Everything one invocation needs. Built by context(); tests build it with explicit commits."""
    def __init__(self, tid, environ, p, gpu, list_path, commits, hw, scratch, titles):
        self.tid, self.environ, self.p, self.gpu, self.list_path = tid, environ, p, gpu, list_path
        self.commits, self.hw, self.scratch, self.titles = commits, hw, scratch, titles
        self.list_sha = file_sha(list_path) if os.path.isfile(list_path) else "none"
        self.key = stack_key(self.list_sha, commits["kyty"], commits["kk"], commits["ps5recomp"], hw)
        self.root = os.path.join(scratch, "cache", tid)
        self.dir = os.path.join(self.root, "%s-%s" % (gpu, self.key))
        self.metal = metal_id(gpu, tid, self.key)

    def identity(self):
        return dict(list=self.list_sha, kyty=self.commits["kyty"], kk=self.commits["kk"], ps5recomp=self.commits["ps5recomp"], hw=self.hw)


def hw_key(environ):
    k = environ.get("NPS5_HW_CACHE_KEY")
    if k:
        return k
    import hw as hwmod
    h = hwmod.detect(environ)
    return hwmod.cache_key(h) if h else "unknown"


def stack_commits(environ):
    e = environ
    def pick(var, *dirs):
        if e.get(var):
            return e[var]
        for d in dirs:
            c = runmod.commit_of(d) if d else None
            if c:
                return c
        return "unknown"
    return dict(kyty=pick("NPS5_STACK_KYTY", e.get("NPS5_KYTY_DIR")), kk=pick("NPS5_STACK_KK", e.get("NPS5_KK_DIR"), e.get("NPS5_KK_WORK")),
                ps5recomp=pick("NPS5_STACK_RECOMP", e.get("NPS5_RECOMP_DIR")))


def context(tid, environ, commits=None, hw=None):
    environ = runmod.machine_env(environ)
    g = runmod._game()
    try:
        p = g.Profile(*g.find_profile(tid, environ))
    except g.Err as e:
        raise CacheError(str(e), 2)
    kind = (p.one("cache", "") or "").split()
    if not any(w == "kind=kyplst" for w in kind):
        raise CacheError("games/%s/game.tsv has no `cache kind=kyplst` row: this title has no pipeline-list cache" % tid, 2)
    tpath = os.path.join(environ.get("NPS5_PUBLIC_DIR", REPO), "titles", tid + ".env")
    titles = runmod.parse_titles_env(tpath, environ)
    gpu = titles.get("GPU") or p.one("gpu", "kyty")
    return Ctx(tid, environ, p, gpu, os.path.join(p.dir, "pipelines.kyplst"), commits or stack_commits(environ),
               hw or hw_key(environ), environ["NPS5_SCRATCH"], titles)


def cache_opts(p):
    d = {}
    for w in (p.one("cache", "") or "").split()[1:]:
        k, _, v = w.partition("=")
        d[k] = v
    return d


# ---------------------------------------------------------------- environment words of a cache arm
def cache_env(tid, cdir, mid, with_libcache=True):
    """The words that point one run at cache dir `cdir` (file names are <title id>.*, as the Kyty runner always used)."""
    e = [("KYTY_PROGRAM_CACHE_PATH", "%s/%s.programs.bin" % (cdir, tid)), ("KYTY_PIPELINE_JOURNAL_PATH", "%s/%s.pipelines.journal" % (cdir, tid)),
         ("KYTY_PIPELINE_KNOWN_PATH", "%s/%s.pipelines.known" % (cdir, tid)), ("KYTY_PIPELINE_CACHE_PATH", "%s/%s.bin" % (cdir, tid)),
         ("KYTY_SHADER_PRECOMPILE_PATH", "%s/%s.shaders.journal" % (cdir, tid)), ("KK_PIPELINE_ARCHIVE_DIR", "%s/kk-archive" % cdir),
         ("KK_METAL_CACHE_ID", mid)]
    if with_libcache:
        e.append(("KK_LIBCACHE_DIR", "%s/kk-libcache" % cdir))
    return e


def prepare_words(ctx, app0):
    o = cache_opts(ctx.p)
    return [("KYTY_PIPELINE_LIST", ctx.list_path), ("KYTY_PIPELINE_LIST_PREPARE", "1"),
            ("KYTY_PIPELINE_LIST_APP_VER", o.get("app_ver", "")), ("KYTY_PIPELINE_LIST_EXE", os.path.join(app0, "eboot.bin")),
            ("KYTY_PIPELINE_LIST_CACHE_LIMIT_MB", o.get("limit_mb", "448")), ("KYTY_PIPELINE_STATS", "1"), ("KYTY_PROGRESS_TITLE_LOG", "1")]


def run_cmd(tid, tag, scene, secs, words, cls="func"):
    cmd = [sys.executable, os.path.join(REPO, "scripts", "nps5"), "run", tid, "--class", cls, "--tag", tag, "--secs", str(secs)]
    if scene:
        cmd += ["--scene", scene]
    for k, v in words:
        cmd += ["--env", "%s=%s" % (k, v)]
    return cmd


def inside(path, root):
    r = os.path.realpath(root)
    return os.path.realpath(path).startswith(r + os.sep)


def rmtree_inside(path, root):
    if not inside(path, root):
        raise CacheError("refusing to delete %s: not inside %s" % (path, root))
    shutil.rmtree(path, ignore_errors=True)


def os_metal_dir(mid):
    d = subprocess.run(["getconf", "DARWIN_USER_CACHE_DIR"], capture_output=True, text=True).stdout.strip()
    return os.path.join(d, mid) if d else None


def tool_path(ctx):
    t = ctx.environ.get("NPS5_KYPLST_TOOL") or os.path.join(ctx.environ.get("NPS5_KYTY_DIR", ""), "build-arm64", "kyty_pipeline_list")
    if not (os.path.isfile(t) and os.access(t, os.X_OK)):
        raise CacheError("no pipeline-list tool at %s (build the kyty fork's kyty_pipeline_list, or set NPS5_KYPLST_TOOL)" % t, 2)
    return t


def scene_names(ctx, wanted):
    rows = [r[0] for r in ctx.p.scenes() if r and "*" not in r[0]]
    if wanted:
        g = runmod._game()
        for w in wanted:
            if g.scene_lookup(ctx.p.scenes(), w) is None:
                raise CacheError("scene %s is not defined in games/%s/scenes.tsv" % (w, ctx.tid), 2)
        return wanted
    return rows


def app0_of(ctx, dry=False):
    a = ctx.titles.get("APP0")
    if not a and dry:
        return "<APP0 from titles/%s.env>" % ctx.tid
    if not a:
        raise CacheError("no APP0 in titles/%s.env" % ctx.tid, 2)
    return a


# ---------------------------------------------------------------- status (pure on files)
def read_kv(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, _, v = line.rstrip("\n").partition("=")
                    out[k] = v
    except OSError:
        return None
    return out


def status(ctx):
    """-> (state, lines). fresh: no cache for this key and none older. partial: dir without prepared.tsv. prepared: prepared.tsv with
    this key. stale: only caches of other keys exist (fields that differ are named)."""
    lines = ["title %s gpu %s: stack key %s" % (ctx.tid, ctx.gpu, ctx.key),
             "  list %s kyty %s kk %s ps5recomp %s hw %s" % (ctx.list_sha[:12], ctx.commits["kyty"], ctx.commits["kk"], ctx.commits["ps5recomp"], ctx.hw)]
    unk = [k for k in ("kyty", "kk", "ps5recomp") if ctx.commits[k] == "unknown"]
    if unk:
        lines.append("  warning: no commit known for %s (set NPS5_KYTY_DIR / NPS5_KK_DIR / NPS5_RECOMP_DIR to git checkouts): the key does not track them" % ", ".join(unk))
    if ctx.list_sha == "none":
        return "fresh", lines + ["  no games/%s/pipelines.kyplst: nothing to prepare" % ctx.tid]
    prep = read_kv(os.path.join(ctx.dir, "prepared.tsv"))
    if prep is not None and prep.get("key") == ctx.key and prep.get("rc") in (None, "0"):
        return "prepared", lines + ["  prepared %s (%s pipelines): %s" % (prep.get("date", "?"), prep.get("pipelines", "?"), ctx.dir)]
    if os.path.isdir(ctx.dir):
        return "partial", lines + ["  %s exists without a good prepared.tsv: run nps5 cache prepare %s" % (ctx.dir, ctx.tid)]
    others = []
    if os.path.isdir(ctx.root):
        for d in sorted(os.listdir(ctx.root)):
            pr = read_kv(os.path.join(ctx.root, d, "prepared.tsv"))
            if pr and pr.get("key"):
                diff = [k for k in KEY_FIELDS if pr.get(k) != ctx.identity()[k]]
                others.append((d, pr.get("date", "?"), diff))
    if others:
        d, date, diff = others[-1]
        return "stale", lines + ["  STALE: the cache %s (prepared %s) was built for another stack; changed: %s" % (d, date, ", ".join(diff) or "?"),
                                 "  first visits will stutter: nps5 cache prepare %s" % ctx.tid]
    return "fresh", lines + ["  no prepared cache: first visits will stutter: nps5 cache prepare %s" % ctx.tid]


# ---------------------------------------------------------------- list counts and review block
def parse_info(text):
    """kyty_pipeline_list info -> dict(shaders, permutations, pipelines) (ints) from its count lines."""
    d = {}
    for k in ("shaders", "permutations", "pipelines"):
        m = re.search(r"\b%s (\d+)" % k, text)
        if m:
            d[k] = int(m.group(1))
    return d


def count_diff(before, after):
    return ", ".join("%s %d%s" % (k, after.get(k, 0), "" if k not in before else " (%+d)" % (after.get(k, 0) - before[k])) for k in ("shaders", "permutations", "pipelines"))


# ---------------------------------------------------------------- check results (pure)
def parse_check(err_text, frame_ms):
    """-> dict(libraries, slow_libraries, archive_hits, compiled, slow_pipelines, stalls, stall_s, worst_ms, red). From one run's err log
    ([kk-archive] stats line, last one wins) and its per-frame times in ms."""
    d = dict(libraries=None, archive_hits=None, compiled=None, slow_pipelines=None)
    m = re.findall(r"\[kk-archive\] stats[^\n]*libraries (\d+) \((\d+) slow, (\d+) ms\), pipelines archive-hit (\d+) \((\d+) ms\) compiled (\d+) \((\d+) slow, (\d+) ms\)", err_text)
    if m:
        a = m[-1]
        d.update(libraries=int(a[0]), slow_libraries=int(a[1]), archive_hits=int(a[3]), compiled=int(a[5]), slow_pipelines=int(a[6]))
    big = [x for x in frame_ms if x > STALL_MS]
    d.update(stalls=len(big), stall_s=round(sum(big) / 1000.0, 2), worst_ms=round(max(frame_ms), 1) if frame_ms else None,
             red=sorted(set(re.findall(r"\[RED\] ?(\S+)", err_text)))[:5])
    return d


def check_row(scene, rc, res):
    f = lambda v: "-" if v is None else str(v)
    return "\t".join([scene, f(rc), f(res["compiled"]), f(res["libraries"]), f(res["stalls"]), f(res["stall_s"]), f(res["worst_ms"]), ",".join(res["red"]) or "-"])


# ---------------------------------------------------------------- subcommands
def _call(cmd, ctx, runner):
    return runner(cmd, env=dict(ctx.environ, NPS5_RUN_REASON=ctx.environ.get("NPS5_RUN_REASON") or "nps5 cache (%s)" % cmd[cmd.index("--tag") + 1]))


def cmd_record(ctx, o, runner):
    rc_all = 0
    for s in scene_names(ctx, o["scene"]):
        d = os.path.join(ctx.root, "rec", s)
        if os.path.isfile(os.path.join(d, ctx.tid + ".pipelines.journal")):
            print("record %s: exists, skipped (rm -r %s to redo)" % (s, d)); continue
        words = cache_env(ctx.tid, d, metal_id(ctx.gpu, ctx.tid, "rec"), with_libcache=False)
        cmd = run_cmd(ctx.tid, "cache-rec-" + s, s, o["secs"] or 180, words)
        if o["dry"]:
            print("[dry-run] mkdir -p %s && %s" % (d, " ".join(shlex.quote(c) for c in cmd))); continue
        rmtree_inside(d, ctx.root); os.makedirs(d)
        rc = _call(cmd, ctx, runner)
        for junk in ("kk-archive", ctx.tid + ".bin", ctx.tid + ".programs.bin"):   # keep only the journals: the recording
            p = os.path.join(d, junk)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else (os.path.exists(p) and os.remove(p))
        print("record %s: rc=%s" % (s, rc)); rc_all = rc_all or (1 if rc else 0)
    return rc_all


def recordings(ctx, extra):
    out = []
    for base in [os.path.join(ctx.root, "rec")] if not extra else []:
        if os.path.isdir(base):
            out += [os.path.join(base, d) for d in sorted(os.listdir(base))]
    out += list(extra)
    return [d for d in out if os.path.isfile(os.path.join(d, ctx.tid + ".pipelines.journal")) and os.path.isfile(os.path.join(d, ctx.tid + ".shaders.journal"))]


def cmd_merge(ctx, o, runner):
    tool, app0 = tool_path(ctx), app0_of(ctx)
    recs = recordings(ctx, o["from"])
    if not recs:
        raise CacheError("no recordings (run `nps5 cache record %s`, or give --from DIR holding %s.{shaders,pipelines}.journal)" % (ctx.tid, ctx.tid))
    out_dir = os.path.join(ctx.root, "lists"); os.makedirs(out_dir, exist_ok=True)
    lists = [] if (o["rebuild"] or not os.path.isfile(ctx.list_path)) else [ctx.list_path]
    for d in recs:
        n = os.path.basename(d.rstrip("/"))
        out = os.path.join(out_dir, n + ".kyplst")
        r = runner([tool, "export", "--out", out, "--game", app0, "--shaders", os.path.join(d, ctx.tid + ".shaders.journal"),
                    "--pipelines", os.path.join(d, ctx.tid + ".pipelines.journal"), "--producer", "nps5-cache-" + n], env=ctx.environ, capture=True)
        if r[0] != 0:
            print("export %s FAILED:\n%s" % (n, r[1])); continue
        lists.append(out); print("export %s: ok" % n)
    cand = os.path.join(ctx.root, "merged.kyplst")
    r = runner([tool, "merge", "--out", cand] + lists, env=ctx.environ, capture=True)
    if r[0] != 0:
        raise CacheError("merge failed:\n%s" % r[1])
    new = parse_info(runner([tool, "info", cand], env=ctx.environ, capture=True)[1])
    old = parse_info(runner([tool, "info", ctx.list_path], env=ctx.environ, capture=True)[1]) if os.path.isfile(ctx.list_path) else {}
    print("merged %d list(s)%s -> %s\n  %s" % (len(lists), " (rebuild: committed list not included)" if o["rebuild"] else "", cand, count_diff(old, new)))
    if o["write"]:
        shutil.copyfile(cand, ctx.list_path)
        try: os.remove(ctx.list_path + ".verified")
        except OSError: pass
        print("wrote %s; its .verified is removed: run `nps5 cache verify-list %s` before committing" % (ctx.list_path, ctx.tid))
    else:
        print("candidate only; `nps5 cache verify-list %s --list %s` then --write (or copy it) to adopt it" % (ctx.tid, cand))
    return 0


def cmd_verify_list(ctx, o, runner):
    lst = o["list"] or ctx.list_path
    if not os.path.isfile(lst):
        raise CacheError("no list %s" % lst, 2)
    tool = tool_path(ctx)
    before = parse_info(runner([tool, "info", ctx.list_path], env=ctx.environ, capture=True)[1]) if os.path.isfile(ctx.list_path) and lst != ctx.list_path else {}
    r = runner([tool, "check", lst, "--game", app0_of(ctx)], env=ctx.environ, capture=True)
    print(r[1].strip().splitlines()[-1] if r[1].strip() else "check: (no output)")
    if r[0] != 0:
        print("verify-list: `check` FAILED; do not commit this list"); return 1
    script = os.path.join(REPO, "scripts", "verify-pipeline-list.sh")
    cmd = ["bash", script, lst, app0_of(ctx)] + sum([["%s" % j] for j in o["shaders"]], [])
    rc = runner(cmd, env=dict(ctx.environ, NPS5_KYPLST_TOOL=tool))
    if rc != 0:
        return 1
    after = parse_info(runner([tool, "info", lst], env=ctx.environ, capture=True)[1])
    print("review block for the PR: %s" % count_diff(before, after))
    return 0


def cmd_prepare(ctx, o, runner):
    app0 = app0_of(ctx, o["dry"])
    if not os.path.isfile(ctx.list_path):
        raise CacheError("no %s" % ctx.list_path, 2)
    words = cache_env(ctx.tid, ctx.dir, ctx.metal) + prepare_words(ctx, app0)
    cmd = run_cmd(ctx.tid, "cache-prepare", None, o["secs"] or 180, words)
    if o["dry"]:
        print("[dry-run] stack key %s\n  cache dir %s\n  metal id  %s\n  would remove %s and %s, then:\n  %s" % (
            ctx.key, ctx.dir, ctx.metal, ctx.dir, os_metal_dir(ctx.metal) or "<OS metal cache>", " ".join(shlex.quote(c) for c in cmd)))
        return 0
    rmtree_inside(ctx.dir, ctx.root); os.makedirs(ctx.dir)
    om = os_metal_dir(ctx.metal)
    if om and os.path.isdir(om):
        shutil.rmtree(om, ignore_errors=True)
    t0 = time.time()
    rc = _call(cmd, ctx, runner)
    lc = ctx.environ.get("NPS5_KK_LIBCACHE_BUILD")
    if rc == 0 and lc and os.path.isfile(lc) and os.path.isdir(os.path.join(ctx.dir, "kk-libcache", "msl")):
        runner(["bash", lc, os.path.join(ctx.dir, "kk-libcache"), str(ctx.environ.get("NPS5_BUILD_JOBS", "4"))], env=ctx.environ)
    info = {}
    try:
        info = parse_info(runner([tool_path(ctx), "info", ctx.list_path], env=ctx.environ, capture=True)[1])
    except CacheError:
        pass
    with open(os.path.join(ctx.dir, "prepared.tsv"), "w") as f:
        f.write("# written by nps5 cache prepare; read by nps5 cache status (a key mismatch means stale)\n")
        for k, v in [("key", ctx.key), ("gpu", ctx.gpu), ("metal_id", ctx.metal), ("date", time.strftime("%Y-%m-%d", time.gmtime())),
                     ("rc", rc), ("wall_s", int(time.time() - t0)), ("pipelines", info.get("pipelines", "?"))] + list(ctx.identity().items()):
            f.write("%s=%s\n" % (k, v))
    print("prepare: rc=%s, %s" % (rc, ctx.dir))
    return 0 if rc == 0 else 1


def clone_dir(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    r = subprocess.run(["cp", "-c", "-R", src, dst], capture_output=True)       # APFS clone: instant, no extra disk
    if r.returncode != 0:
        subprocess.run(["cp", "-R", src, dst], check=True)


def cmd_check(ctx, o, runner):
    st, lines = status(ctx)
    if st != "prepared" and not o["dry"]:
        raise CacheError("\n".join(lines) + "\ncache check needs a prepared cache for this stack: nps5 cache prepare %s" % ctx.tid)
    rows = ["scene\trc\tcompiled\tlibraries\tstalls>%dms\tstall_s\tworst_ms\tred" % STALL_MS]
    out_dir = os.path.join(ctx.root, "check-%s" % ctx.key); os.makedirs(out_dir, exist_ok=True) if not o["dry"] else None
    for s in scene_names(ctx, o["scene"]):
        d = os.path.join(ctx.root, "verify", ctx.key, s); mid = ctx.metal + ".v." + s
        words = cache_env(ctx.tid, d, mid) + [("KYTY_PROGRESS_TITLE_LOG", "1"), ("KYTY_PIPELINE_STATS", "1")]
        tag = "cache-check-" + s
        cmd = run_cmd(ctx.tid, tag, s, o["secs"] or 180, words)
        if o["dry"]:
            print("[dry-run] clone %s -> %s; %s" % (ctx.dir, d, " ".join(shlex.quote(c) for c in cmd))); continue
        clone_dir(ctx.dir, d)
        om = os_metal_dir(ctx.metal)
        if om and os.path.isdir(om):
            clone_dir(om, os_metal_dir(mid))
        rc = _call(cmd, ctx, runner)
        rd = os.path.join(ctx.scratch, "runs", ctx.tid, tag)
        try:
            import frames as fr
            ms = fr.load_run_frames(rd).ms
        except Exception:
            ms = []
        try:
            err = open(os.path.join(rd, "run.err"), errors="replace").read()
        except OSError:
            err = ""
        rows.append(check_row(s, rc, parse_check(err, ms)))
        rmtree_inside(d, ctx.root)
        if om: shutil.rmtree(os_metal_dir(mid), ignore_errors=True)
    if not o["dry"]:
        with open(os.path.join(out_dir, "check.tsv"), "w") as f:
            f.write("\n".join(rows) + "\n")
        print("\n".join(rows))
    return 0


def cmd_status(ctx, o, runner):
    st, lines = status(ctx)
    print("\n".join(lines)); print("state: %s" % st)
    return 0 if st == "prepared" else 1


def default_runner(cmd, env=None, capture=False):
    if capture:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, errors="replace")
        return r.returncode, r.stdout + r.stderr
    return subprocess.call(cmd, env=env)


def parse(argv):
    o = dict(scene=[], secs=None, dry=False, rebuild=False, write=False, list=None, shaders=[], **{"from": []})
    pos, i = [], 0
    VAL = {"--scene": "scene", "--from": "from", "--shaders": "shaders", "--secs": "secs", "--list": "list"}
    while i < len(argv):
        a = argv[i]
        if a in VAL:
            if i + 1 >= len(argv):
                raise CacheError("%s needs a value" % a, 2)
            k = VAL[a]; v = argv[i + 1]
            if isinstance(o[k], list): o[k].append(v)
            else: o[k] = v
            i += 1
        elif a in ("--dry-run", "--rebuild", "--write"):
            o[{"--dry-run": "dry"}.get(a, a[2:])] = True
        elif a.startswith("--"):
            raise CacheError("unknown option %s" % a, 2)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 2:
        raise CacheError("usage: nps5 cache record|merge|verify-list|prepare|check|status <id> [options]; see --help", 2)
    if o["secs"] is not None and not re.fullmatch(r"\d+", o["secs"]):
        raise CacheError("--secs must be a number", 2)
    return pos[0], pos[1], o


CMDS = {"record": cmd_record, "merge": cmd_merge, "verify-list": cmd_verify_list, "prepare": cmd_prepare, "check": cmd_check, "status": cmd_status}


def main(argv, environ=None, runner=default_runner, ctx=None):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0 if argv else 2
    try:
        cmd, tid, o = parse(argv)
        if cmd not in CMDS:
            raise CacheError("unknown cache command %s" % cmd, 2)
        ctx = ctx or context(tid, dict(os.environ if environ is None else environ))
        return CMDS[cmd](ctx, o, runner)
    except CacheError as e:
        print("nps5 cache: %s" % e, file=sys.stderr); return e.code
    except runmod.Refuse as e:
        print("nps5 cache: %s" % e, file=sys.stderr); return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
