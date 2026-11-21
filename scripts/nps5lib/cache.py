#!/usr/bin/env python3
"""nps5 cache: the pipeline list and the compiled caches of one title (games/README.md). Standard library only.

  nps5 cache record  <id> [--scene NAME ...] [--secs N] [--dry-run]   one first visit per scene into an EMPTY cache arm
  nps5 cache merge   <id> [--from DIR ...] [--rebuild] [--write]      export one list per recording, merge with the committed list
  nps5 cache share   <id> [--from DIR ...] [--pr]                      SHARE YOUR LIST: your journals -> games/<id>/contrib/<date>-<hash>.kyplst
                                                                      (keys only, verified); prints how many pipelines are new and how to send it
  nps5 cache merge   <id> --contrib FILE... [--write]                  MAINTAINER: fold verified contributions into the shipped list
  nps5 cache verify-list <id> [--list FILE] [--shaders JOURNAL ...]   no game code in the list; writes <list>.verified
  nps5 cache prepare <id> [--secs N] [--single-pass] [--no-warm] [--dry-run]
                                                                      USERS RUN THIS ONCE: fresh cache + fresh Metal cache id, builds
                                                                      every listed pipeline in a start that exits before the game runs;
                                                                      two starts with KK's library cache (see below), --single-pass = one;
                                                                      then two short warm starts (window helper pipelines), --no-warm = none
  nps5 cache metal   <id> [--dry-run] [--reproduce]                   (re)generate our Metal kernels for the listed verified keys from this
                                                                      cache's programs.bin (prepare does it between its passes; metal.py);
                                                                      --reproduce: every row of every GPU class, prints the record line
  nps5 cache check   <id> [--scene NAME ...] [--secs N] [--dry-run]   first-visit runs on a clone of the prepared cache: live compiles, stalls
  nps5 cache status  <id>                                              fresh | partial | prepared | stale (exit 0 only when prepared)

Identity is derived, never typed. stack key = 8 hex of sha256(list sha256, Kyty commit, KosmicKrisp commit, ps5recomp commit,
NPS5_HW_CACHE_KEY of hwpresets). A commit is the checkout's HEAD, with +<hash of the uncommitted changes> when its tracked files
differ (run.py commit_of); KosmicKrisp is read from $NPS5_KK_DIR, else $NPS5_KK_WORK or its mesa/ (the public layout, where the patch
series is applied on the pinned commit without committing). cache dir = <scratch>/cache/<id>/<gpu>-<stack key>/; Metal cache id =
com.nativeps5.<gpu>.<id>.<stack key>. A new list, a new KK / Kyty / ps5recomp commit or another GPU family / OS build gives a new
key, so a stale cache is never reused silently. Compiled caches are never committed; only games/<id>/pipelines.kyplst and
its .verified are. The runs go through `nps5 run` (title queue, memory limit, one GPU process); nothing here starts a game itself.
KK_LIBCACHE_DIR (KK's library cache) is set to <cache dir>/kk-libcache. `prepare` is TWO-PASS: pass 1 collects the MSL of every shader,
$NPS5_KK_LIBCACHE_BUILD (default scripts/kk-libcache-build.sh) <dir> <jobs> builds it into metallibs, pass 2 starts again from an empty
cache and a fresh Metal cache id with the metallibs in place. (KK's Metal archive keys a pipeline on its library: a pipeline built from
a source-compiled library never hits when the same shader is later loaded from a metallib, so only pass 2's archive is kept.)
Tool: $NPS5_KYPLST_TOOL, else <kyty>/build-arm64/kyty_pipeline_list. Exit codes: 0 ok, 1 failed / stale, 2 usage."""
import hashlib, importlib.util, os, re, shlex, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run as runmod  # noqa: E402
import metal as metalmod  # noqa: E402

STALL_MS = 250.0
PREPARE_SECS = 1800
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
    def pick(var, *dirs, of=runmod.commit_of):
        if e.get(var):
            return e[var]
        for d in dirs:
            c = of(d) if d else None
            if c:
                return c
        return "unknown"
    return dict(kyty=pick("NPS5_STACK_KYTY", e.get("NPS5_KYTY_DIR")),
                kk=pick("NPS5_STACK_KK", e.get("NPS5_KK_DIR"), e.get("NPS5_KK_WORK"), of=runmod.kk_commit),
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
    if gpu not in ("kyty", "anyps5"):                 # it becomes part of folder names that are deleted and recreated
        raise CacheError("GPU must be kyty or anyps5 (titles/%s.env)" % tid, 2)
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


def compile_jobs(environ):
    """Job count of the metallib build: $NPS5_COMPILE_THREADS, else $NPS5_BUILD_JOBS, else the logical cores minus 4 (at least 2), so the
    Mac stays usable while it compiles; Kyty sizes its prepare / launch-phase workers by the same rule and runs them at utility QoS."""
    for k in ("NPS5_COMPILE_THREADS", "NPS5_BUILD_JOBS"):
        if (environ.get(k) or "").isdigit() and int(environ[k]) > 0:
            return int(environ[k])
    return max(2, (os.cpu_count() or 8) - 4)


def prepare_words(ctx, app0):
    o = cache_opts(ctx.p)
    return [("KYTY_PIPELINE_LIST", ctx.list_path), ("KYTY_PIPELINE_LIST_PREPARE", "1"),
            ("KYTY_PIPELINE_LIST_APP_VER", o.get("app_ver", "")), ("KYTY_PIPELINE_LIST_EXE", os.path.join(app0, "eboot.bin")),
            ("KYTY_PIPELINE_LIST_CACHE_LIMIT_MB", o.get("limit_mb", "448")), ("KYTY_PIPELINE_STATS", "1"), ("KYTY_PROGRESS_TITLE_LOG", "1"),
            ("KYTY_HELPER_PREWARM", "1"),   # the renderer's own tile/detile pipelines are in no list: built (and archived) by the prepare too
            # v1.1.2: the profile defers KosmicKrisp's archive flush to exit for play; a prepare waits one flush period before it ends
            # (Kyty, pipelineCache.cpp), so it keeps KosmicKrisp's own 32 pipelines / 10 s.
            ("KK_PIPELINE_ARCHIVE_FLUSH_N", "32"), ("KK_PIPELINE_ARCHIVE_FLUSH_S", "10")]


def launch_words(ctx, app0):
    """The launch phase of a normal run on a prepared cache: every pipeline of the list (and of the player's own journal) is created
    again before the game starts, behind the loading card (KYTY_PIPELINE_REPLAY_ALL), together with the renderer's helper pipelines
    (KYTY_HELPER_PREWARM). On a prepared cache each one is a cache hit (about 3 s for 1,500 pipelines); whatever the caches lost since
    compiles there, not at its first draw."""
    o = cache_opts(ctx.p)
    return [("KYTY_PIPELINE_LIST", ctx.list_path), ("KYTY_PIPELINE_LIST_APP_VER", o.get("app_ver", "")),
            ("KYTY_PIPELINE_LIST_EXE", os.path.join(app0, "eboot.bin")), ("KYTY_PIPELINE_REPLAY_ALL", "1"), ("KYTY_HELPER_PREWARM", "1")] + \
        metalmod.launch_words(ctx.dir, os.path.join(ctx.p.dir, metalmod.LIST_NAME), ctx.environ)[0]


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
        lines.append("  warning: no commit known for %s (set NPS5_KYTY_DIR / NPS5_KK_DIR / NPS5_RECOMP_DIR to git checkouts, or NPS5_STACK_KYTY / "
                     "NPS5_STACK_KK / NPS5_STACK_RECOMP to an identity): the key does not track them, so a cache built by another build of them "
                     "would be reused" % ", ".join(unk))
    if ctx.list_sha == "none":
        return "fresh", lines + ["  no games/%s/pipelines.kyplst: nothing to prepare" % ctx.tid]
    prep = read_kv(os.path.join(ctx.dir, "prepared.tsv"))
    if prep is not None and prep.get("key") == ctx.key and prep.get("rc") == "0":      # no rc line = a torn or foreign file, not a pass
        ms = metalmod.index_rows(os.path.join(ctx.dir, metalmod.OUT_NAME))[1]
        gs = metalmod.gfx_index_rows(os.path.join(ctx.dir, metalmod.OUT_NAME))[1]
        mline = ["  Metal shaders: %d generated kernels, %d graphics pairs%s" % (ms, gs, "" if metalmod.enabled(ctx.environ) else " (off: NPS5_METAL_SHADERS=0)")] if ms or gs or prep.get("metal_shaders") else []
        return "prepared", lines + ["  prepared %s (%s pipelines): %s" % (prep.get("date", "?"), prep.get("pipelines", "?"), ctx.dir)] + mline
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
    """kyty_pipeline_list info -> dict(shaders, permutations, pipelines, vertex, pixel, compute) (ints) and title / version (strings)."""
    d = {}
    for k in ("shaders", "permutations", "pipelines", "vertex", "pixel", "compute"):
        m = re.search(r"\b%s (\d+)" % k, text)
        if m:
            d[k] = int(m.group(1))
    m = re.search(r"title (\S+), version ([^,\s]+)", text)
    if m:
        d["title"], d["version"] = m.group(1), m.group(2)
    return d


def parse_scene_tags(text):
    """kyty_pipeline_list info -> {scene: pipelines tagged with it} ('  level <name>: <n> pipelines' lines). A recording made by a
    runtime that names the current scene (nps5_vfs_current_scene) tags each pipeline with the scene it was first needed in; export keeps
    the tag and merge unions them. `shader_prepare=scene` needs them: a start whose scene has no tagged pipeline prepares everything."""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^\s*level (\S+): (\d+) pipelines?\s*$", text, re.M)}


def scene_tag_line(tags):
    if not tags:
        return "scene tags: none (shader_prepare=scene prepares everything; record with the current runtime to get them)"
    return "scene tags: %d pipeline tag(s) in %d scene(s): %s" % (sum(tags.values()), len(tags), ", ".join("%s %d" % kv for kv in sorted(tags.items())))


def stage_summary(d):
    """'shaders 832 (vertex 479, pixel 230, compute 123), permutations 878, pipelines 1544' (the .verified record's wording)."""
    return "shaders %d (vertex %d, pixel %d, compute %d), permutations %d, pipelines %d" % tuple(
        d.get(k, 0) for k in ("shaders", "vertex", "pixel", "compute", "permutations", "pipelines"))


def new_counts(before, after):
    return {k: after.get(k, 0) - before.get(k, 0) for k in ("shaders", "vertex", "pixel", "compute", "permutations", "pipelines")}


PRIVATE = []      # paths of this invocation that must never be printed (the game folder), set by cmd_share


def scrub(text, environ=None):
    """Tool output / errors may carry the user's paths: home and scratch are replaced before anything is printed."""
    e = environ or os.environ
    for v, w in [(x, "<game>") for x in PRIVATE] + [(e.get("NPS5_SCRATCH"), "<scratch>"), (REPO, "<repo>"), (e.get("HOME"), "~"), (os.path.expanduser("~"), "~")]:
        if v and len(v) > 1:
            text = text.replace(v, w)
    return re.sub(r"/Users/[^/\s]+", "~", text)


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
    if o["contrib"]:
        return cmd_merge_contrib(ctx, o, runner)
    tool, app0 = tool_path(ctx), app0_of(ctx)
    recs = recordings(ctx, o["from"])
    if not recs:
        raise CacheError("no recordings (run `nps5 cache record %s`, or give --from DIR holding %s.{shaders,pipelines}.journal)" % (ctx.tid, ctx.tid))
    out_dir = os.path.join(ctx.root, "lists"); os.makedirs(out_dir, exist_ok=True)
    lists = [] if (o["rebuild"] or not os.path.isfile(ctx.list_path)) else [ctx.list_path]
    failed = []
    for d in recs:
        n = os.path.basename(d.rstrip("/"))
        out = os.path.join(out_dir, n + ".kyplst")
        r = runner([tool, "export", "--out", out, "--game", app0, "--shaders", os.path.join(d, ctx.tid + ".shaders.journal"),
                    "--pipelines", os.path.join(d, ctx.tid + ".pipelines.journal"), "--producer", "nps5-cache-" + n], env=ctx.environ, capture=True)
        if r[0] != 0:
            print("export %s FAILED:\n%s" % (n, r[1])); failed.append(n); continue
        lists.append(out); print("export %s: ok" % n)
    cand = os.path.join(ctx.root, "merged.kyplst")
    r = runner([tool, "merge", "--out", cand] + lists, env=ctx.environ, capture=True)
    if r[0] != 0:
        raise CacheError("merge failed:\n%s" % r[1])
    cand_info = runner([tool, "info", cand], env=ctx.environ, capture=True)[1]
    new = parse_info(cand_info)
    old = parse_info(runner([tool, "info", ctx.list_path], env=ctx.environ, capture=True)[1]) if os.path.isfile(ctx.list_path) else {}
    print("merged %d list(s)%s -> %s\n  %s\n  %s" % (len(lists), " (rebuild: committed list not included)" if o["rebuild"] else "", cand, count_diff(old, new),
                                                   scene_tag_line(parse_scene_tags(cand_info))))
    if failed:
        # a list merged from fewer recordings than were given is smaller than intended, silently so with --rebuild
        print("merge: %d recording(s) could not be exported (%s): the candidate is incomplete and was NOT adopted" % (len(failed), ", ".join(failed)))
        return 1
    if o["write"]:
        shutil.copyfile(cand, ctx.list_path + ".tmp"); os.replace(ctx.list_path + ".tmp", ctx.list_path)
        try: os.remove(ctx.list_path + ".verified")
        except OSError: pass
        print("wrote %s; its .verified is removed: run `nps5 cache verify-list %s` before committing" % (ctx.list_path, ctx.tid))
    else:
        print("candidate only; `nps5 cache verify-list %s --list %s` then --write (or copy it) to adopt it" % (ctx.tid, cand))
    return 0


# ---------------------------------------------------------------- share (users) and contrib merge (maintainers)
def kyplst_rules(path_rel, data, sidecar):
    """scripts/nps5_check.py's rules for a list (single zstd frame, size cap, no payload, .verified for these bytes): -> problems."""
    spec = importlib.util.spec_from_file_location("nps5_check_for_share", os.path.join(HERE, "..", "nps5_check.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.check_kyplst(path_rel, data, sidecar)


def journal_dirs(ctx, extra):
    """Directories that hold this title's shader + pipeline journals: --from dirs, else rec/*, the cache dirs of every stack key and
    their verify clones under <scratch>/cache/<id>/."""
    cands = list(extra)
    if not extra and os.path.isdir(ctx.root):
        for dp, dn, _ in os.walk(ctx.root):
            if os.path.relpath(dp, ctx.root).count(os.sep) >= 3:
                dn[:] = []; continue
            cands.append(dp)
    seen, out = set(), []
    for d in cands:
        r = os.path.realpath(d)
        if r in seen or not (os.path.isfile(os.path.join(d, ctx.tid + ".pipelines.journal")) and os.path.isfile(os.path.join(d, ctx.tid + ".shaders.journal"))):
            continue
        seen.add(r); out.append(d)
    return out


def machine_class(environ):
    try:
        import hw as hwmod
        h = hwmod.detect(environ)
        return (h.get("class") or h.get("chip_key") or "unknown") if h else "unknown"
    except Exception:
        return "unknown"


def same_version(a, b, what_a, what_b):
    """A list is for ONE title and ONE app version: other versions have other shaders (their own list)."""
    if a.get("title") != b.get("title") or a.get("version") != b.get("version"):
        raise CacheError("%s is for %s %s but %s is for %s %s: lists are per game version, never mixed (a new version gets its own list)" % (
            what_a, a.get("title"), a.get("version"), what_b, b.get("title"), b.get("version")), 2)


def pr_texts(tid, name, ver, new, cls, commit):
    title = "Shader list: %s %s, +%d pipelines" % (tid, ver, new["pipelines"])
    body = ("%s\n\n- title / version: %s %s\n- nps5 commit: %s\n- machine class: %s\n- new vs the shipped list: %s\n\n"
            "- [x] The list holds keys and hashes only: no game code, no game data (`nps5 cache share` verified it against my own copy of the game)\n\n"
            "AI assistance: <yes / no; if yes which agent, and a person read this>\n" % (title, tid, ver, commit, cls, stage_summary(new)))
    return title, body


def cmd_share(ctx, o, runner):
    tool = tool_path(ctx)
    dirs = journal_dirs(ctx, o["from"])
    if not dirs:
        raise CacheError("no journals for %s found (play the title with `nps5 run`, or point --from at a folder holding %s.shaders.journal and %s.pipelines.journal)" % (ctx.tid, ctx.tid, ctx.tid))
    app0 = app0_of(ctx); PRIVATE[:] = [app0, os.path.dirname(app0), os.path.dirname(os.path.dirname(os.path.dirname(ctx.list_path)))]
    work = os.path.join(ctx.root, "share"); rmtree_inside(work, ctx.root) if os.path.isdir(work) else None; os.makedirs(work)
    commit = (runmod.commit_of(REPO) or "unknown")[:12]
    cls = machine_class(ctx.environ)
    producer = "nps5-share %s %s" % (commit, cls)
    exp = []
    for i, d in enumerate(dirs):
        out = os.path.join(work, "j%d.kyplst" % i)
        rc, text = runner([tool, "export", "--out", out, "--game", app0, "--shaders", os.path.join(d, ctx.tid + ".shaders.journal"),
                           "--pipelines", os.path.join(d, ctx.tid + ".pipelines.journal"), "--producer", producer], env=ctx.environ, capture=True)
        if rc != 0:
            print("share: could not export journal set %d: %s" % (i + 1, scrub(text.strip().splitlines()[-1] if text.strip() else "?", ctx.environ))); continue
        exp.append(out)
    if not exp:
        raise CacheError("none of the %d journal set(s) could be exported against your game copy" % len(dirs))
    mine = os.path.join(work, "mine.kyplst")
    rc, text = runner([tool, "merge", "--out", mine, "--producer", producer] + exp, env=ctx.environ, capture=True)
    if rc != 0:
        raise CacheError("merge of your journals failed: %s" % scrub(text.strip().splitlines()[-1] if text.strip() else "?", ctx.environ))
    mi = parse_info(runner([tool, "info", mine], env=ctx.environ, capture=True)[1])
    shipped = os.path.isfile(ctx.list_path)
    si = parse_info(runner([tool, "info", ctx.list_path], env=ctx.environ, capture=True)[1]) if shipped else {}
    if shipped:
        same_version(mi, si, "your journals", "the shipped list")
    union = os.path.join(work, "union.kyplst")
    if shipped:
        rc, text = runner([tool, "merge", "--out", union, ctx.list_path, mine], env=ctx.environ, capture=True)
        if rc != 0:
            raise CacheError("could not compare with the shipped list: %s" % scrub(text.strip().splitlines()[-1] if text.strip() else "?", ctx.environ))
        ui = parse_info(runner([tool, "info", union], env=ctx.environ, capture=True)[1])
    else:
        ui = mi
    new = new_counts(si, ui)
    print("title %s %s, %d journal set(s) from your machine" % (mi.get("title"), mi.get("version"), len(dirs)))
    print("your list:    %s" % stage_summary(mi))
    print("shipped list: %s" % (stage_summary(si) if shipped else "none"))
    print("NEW:          %s" % stage_summary(new))
    if new["pipelines"] <= 0 and new["permutations"] <= 0 and new["shaders"] <= 0:
        print("Thanks! Your journals hold nothing the shipped list does not already have: there is nothing to share.")
        return 0
    # The contribution: the user's own list. The tool has no `subtract`, so entries already shipped stay in it; the maintainer merge dedupes.
    h = file_sha(mine)[:8]
    cdir = os.path.join(os.path.dirname(ctx.list_path), "contrib"); os.makedirs(cdir, exist_ok=True)
    name = "%s-%s.kyplst" % (time.strftime("%Y-%m-%d", time.gmtime()), h)
    dest = os.path.join(cdir, name)
    shutil.copyfile(mine, dest)
    rel = "games/%s/contrib/%s" % (ctx.tid, name)
    print("wrote %s (keys and hashes of %d pipelines, %d of them new)" % (rel, mi.get("pipelines", 0), new["pipelines"]))
    vo = dict(o, quiet=True, list=dest, shaders=[os.path.join(d, ctx.tid + ".shaders.journal") for d in dirs])
    if cmd_verify_list(ctx, vo, runner) != 0:
        os.remove(dest); print("share: the list did NOT pass; it was deleted. Nothing to send. Please open an issue with the message above."); return 1
    with open(dest, "rb") as f: data = f.read()
    try:
        with open(dest + ".verified", "rb") as f: side = f.read()
    except OSError:
        side = b""
    bad = kyplst_rules(rel, data, side)
    if bad:
        for f in (dest, dest + ".verified"):
            try: os.remove(f)
            except OSError: pass
        print("share: the repository's list rules reject it; it was deleted:\n  " + "\n  ".join(bad)); return 1
    print("share: passed the no-game-code proof and the list rules (one zstd frame, size cap, no payload).")
    ver = mi.get("version")
    title, body = pr_texts(ctx.tid, name, ver, new, cls, commit)
    print("\nNEXT STEPS. Send these two files (both small, keys only):\n  %s\n  %s.verified\n" % (rel, rel))
    print("With git:\n  gh repo fork --remote --remote-name fork   # once\n  git switch -c share-%s-%s\n  git add %s %s.verified\n"
          "  git commit -m %s\n  git push -u fork HEAD\n  gh pr create --title %s --body-file <(printf '%%s' \"$BODY\")" % (ctx.tid, h, rel, rel, shlex.quote(title), shlex.quote(title)))
    print("\nWithout git: open a 'Shader list' issue (New issue -> Shader list) and attach both files. PR / issue text:\n---\n%s---" % body)
    if o.get("pr"):
        return open_pr(ctx, o, rel, h, title, body, runner)
    print("(`nps5 cache share %s --pr` does the git / gh steps for you after asking.)" % ctx.tid)
    return 0


def open_pr(ctx, o, rel, h, title, body, runner):
    if runner(["gh", "auth", "status"], env=ctx.environ, capture=True)[0] != 0:
        print("share --pr: gh is not logged in (`gh auth login`); use the steps above."); return 1
    steps = ["gh repo fork --remote --remote-name fork   (skipped if the fork exists)", "git switch -c share-%s-%s" % (ctx.tid, h),
             "git add %s %s.verified; git commit" % (rel, rel), "git push -u fork share-%s-%s   <- publishes to YOUR fork on GitHub" % (ctx.tid, h),
             "gh pr create   <- opens a public pull request"]
    print("\n--pr will run, in %s:\n  %s" % (REPO, "\n  ".join(steps)))
    try:
        ans = (o.get("confirm") or input)("Push and open the pull request? [y/N] ")
    except EOFError:                                       # no terminal to ask: the answer is no
        ans = ""
    if ans.strip().lower() not in ("y", "yes"):
        print("Nothing was pushed or created."); return 0
    git = lambda *a: runner(["git", "-C", REPO] + list(a), env=ctx.environ, capture=True)
    runner(["gh", "repo", "fork", "--remote", "--remote-name", "fork"], env=ctx.environ, capture=True)
    br = "share-%s-%s" % (ctx.tid, h)
    # the commit names its two files: whatever else is staged in the user's clone is not committed and not published
    for cmd in (["switch", "-c", br], ["add", rel, rel + ".verified"], ["commit", "-m", title, "--", rel, rel + ".verified"], ["push", "-u", "fork", br]):
        rc, text = git(*cmd)
        if rc != 0:
            print("share --pr: git %s failed: %s" % (cmd[0], scrub(text.strip()[-300:], ctx.environ))); return 1
    rc, text = runner(["gh", "pr", "create", "--title", title, "--body", body], env=ctx.environ, capture=True)
    print(scrub(text.strip(), ctx.environ)); return 0 if rc == 0 else 1


def cmd_merge_contrib(ctx, o, runner):
    tool = tool_path(ctx)
    if not os.path.isfile(ctx.list_path):
        raise CacheError("no shipped list to fold into: %s" % ctx.list_path, 2)
    si = parse_info(runner([tool, "info", ctx.list_path], env=ctx.environ, capture=True)[1])
    prov = os.path.join(os.path.dirname(ctx.list_path), "pipelines.contrib.tsv")
    done = set()
    if os.path.isfile(prov):
        done = {l.split("\t")[1] for l in open(prov) if l.strip() and not l.startswith("#") and "\t" in l}
    good = []
    for c in o["contrib"]:
        if c.endswith(".verified") or not os.path.isfile(c):
            continue
        rel = "games/%s/contrib/%s" % (ctx.tid, os.path.basename(c))
        data = open(c, "rb").read()
        try: side = open(c + ".verified", "rb").read()
        except OSError: side = b""
        bad = kyplst_rules(rel, data, side)
        if bad:
            raise CacheError("%s refused: %s" % (rel, "; ".join(bad)), 2)
        ci = parse_info(runner([tool, "info", c], env=ctx.environ, capture=True)[1])
        same_version(ci, si, rel, "the shipped list")
        good.append((c, ci, hashlib.sha256(data).hexdigest()[:8]))
    if not good:
        raise CacheError("no contribution files given (--contrib games/%s/contrib/*.kyplst)" % ctx.tid, 2)
    out = os.path.join(ctx.root, "merged-contrib.kyplst"); os.makedirs(ctx.root, exist_ok=True)
    rc, text = runner([tool, "merge", "--out", out, ctx.list_path] + [g[0] for g in good], env=ctx.environ, capture=True)
    if rc != 0:
        raise CacheError("merge failed: %s" % scrub(text.strip()[-300:], ctx.environ))
    ui = parse_info(runner([tool, "info", out], env=ctx.environ, capture=True)[1])
    print("folded %d contribution(s) into %s %s\n  before: %s\n  after:  %s\n  gained: %s" % (
        len(good), ui.get("title"), ui.get("version"), stage_summary(si), stage_summary(ui), stage_summary(new_counts(si, ui))))
    if not o["write"]:
        print("candidate only (%s); add --write to adopt it" % out); return 0
    rows = [(c, sha) for c, _, sha in good if sha not in done]
    shutil.copyfile(out, ctx.list_path + ".tmp"); os.replace(ctx.list_path + ".tmp", ctx.list_path)
    try: os.remove(ctx.list_path + ".verified")
    except OSError: pass
    new_file = not os.path.isfile(prov)
    with open(prov, "a") as f:
        if new_file:
            f.write("# provenance of pipelines.kyplst: one row per folded contribution (date, sha256 prefix, its own counts). No names.\n")
        for c, sha in rows:
            ci = next(g[1] for g in good if g[0] == c)
            f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d", time.gmtime()), sha, stage_summary(ci)))
    print("wrote %s; provenance: %d contribution(s) recorded in total (%d new rows); its .verified is removed: run `nps5 cache verify-list %s`, then git rm the folded contrib files" % (
        ctx.list_path, len(done) + len(rows), len(rows), ctx.tid))
    return 0


def cmd_verify_list(ctx, o, runner):
    lst = o["list"] or ctx.list_path
    if not os.path.isfile(lst):
        raise CacheError("no list %s" % lst, 2)
    tool = tool_path(ctx)
    before = parse_info(runner([tool, "info", ctx.list_path], env=ctx.environ, capture=True)[1]) if os.path.isfile(ctx.list_path) and lst != ctx.list_path else {}
    r = runner([tool, "check", lst, "--game", app0_of(ctx)], env=ctx.environ, capture=True)
    print(scrub(r[1].strip().splitlines()[-1], ctx.environ) if r[1].strip() else "check: (no output)")
    if r[0] != 0:
        print("verify-list: `check` FAILED; do not commit this list"); return 1
    script = os.path.join(REPO, "scripts", "verify-pipeline-list.sh")
    cmd = ["bash", script, lst, app0_of(ctx)] + sum([["%s" % j] for j in o["shaders"]], [])
    if o.get("quiet"):                                     # share: nothing but scrubbed text reaches the terminal
        rc, text = runner(cmd, env=dict(ctx.environ, NPS5_KYPLST_TOOL=tool), capture=True)
        print(scrub(text.strip(), ctx.environ))
    else:
        rc = runner(cmd, env=dict(ctx.environ, NPS5_KYPLST_TOOL=tool))
    if rc != 0:
        return 1
    after = parse_info(runner([tool, "info", lst], env=ctx.environ, capture=True)[1])
    if o.get("quiet"):
        return 0
    print("review block for the PR: %s" % count_diff(before, after))
    return 0


PROGRESS_RE = re.compile(r"^(?:\[[^\]]*\]\s*)?((?:Shader precompile|Pipeline replay|Prepare shaders|Helper prewarm): .*)$", re.M)


def progress_line(text):
    """The newest progress line of a prepare's log ('Pipeline replay: 512/1544 (33%): ...'), shortened for a terminal; '' when none."""
    m = PROGRESS_RE.findall(text)
    return m[-1].strip()[:110] if m else ""


class ProgressTail(threading.Thread):
    """While a prepare pass runs, print its newest progress line to the terminal every few seconds (the window shows the same count on
    the loading card). Reads the tail of run.out / run.err of the run folder; never fails the prepare."""
    def __init__(self, run_dir, label, every=5.0, out=None):
        super().__init__(daemon=True)
        self.run_dir, self.label, self.every, self.out, self.stop, self.last = run_dir, label, every, out or sys.stdout, threading.Event(), ""

    def poll(self, since=0.0):
        best = ""
        for n in ("run.out", "run.err"):
            try:
                p = os.path.join(self.run_dir, n)
                if os.stat(p).st_mtime < since:
                    continue                        # the previous pass's log
                with open(p, "rb") as f:
                    f.seek(max(0, os.path.getsize(p) - 65536))
                    line = progress_line(f.read().decode(errors="replace"))
                if line and (not best or line.startswith("Prepare shaders: done")):
                    best = line
            except OSError:
                pass
        return best

    def run(self):
        t0 = time.time()
        while not self.stop.wait(self.every):
            line = self.poll(t0 - 2)
            if line and line != self.last:
                self.last = line
                print("prepare: %s: %s" % (self.label, line), file=self.out, flush=True)

    def done(self):
        self.stop.set()


def cmd_prepare(ctx, o, runner):
    app0 = app0_of(ctx, o["dry"])
    if not os.path.isfile(ctx.list_path):
        raise CacheError("no %s" % ctx.list_path, 2)
    words = cache_env(ctx.tid, ctx.dir, ctx.metal) + prepare_words(ctx, app0)
    mlist = metal_list(ctx) or metal_gfx_list(ctx)
    if mlist:
        words.append(("KK_MSL_OVERRIDE_LOG", "1"))     # pass 1: KK names every compute kernel it builds (how many listed keys it sees)
        if metalmod.canon_enabled(ctx.environ):
            words.append(("KK_MSL_CANON_KEY", "1"))    # ... and the canonical (macOS 26 / GPU family 9) key of each: the lists' keys
    # A prepare exits by itself when the list is built; --secs is only its ceiling. 180 s (the old default) cut a first prepare of
    # ~1,600 pipelines on a busy Mac (no until target: the watcher ends a run at its secs), so the default is 30 min.
    cmd = run_cmd(ctx.tid, "cache-prepare", None, o["secs"] or PREPARE_SECS, words)
    if o["dry"]:
        print("[dry-run] stack key %s\n  cache dir %s\n  metal id  %s\n  would remove %s and %s, then:\n  %s" % (
            ctx.key, ctx.dir, ctx.metal, ctx.dir, os_metal_dir(ctx.metal) or "<OS metal cache>", " ".join(shlex.quote(c) for c in cmd)))
        return 0
    om = os_metal_dir(ctx.metal)
    lcdir = os.path.join(ctx.dir, "kk-libcache")

    def fresh_dir(keep_libcache=False):
        keep = ctx.dir + ".libkeep"
        mdir, mkeep = os.path.join(ctx.dir, metalmod.OUT_NAME), ctx.dir + ".metalkeep"
        if keep_libcache and os.path.isdir(lcdir):
            shutil.rmtree(keep, ignore_errors=True); os.replace(lcdir, keep)
        if keep_libcache and os.path.isdir(mdir):
            shutil.rmtree(mkeep, ignore_errors=True); os.replace(mdir, mkeep)
        rmtree_inside(ctx.dir, ctx.root); os.makedirs(ctx.dir)
        if os.path.isdir(keep):
            os.replace(keep, lcdir)
        if os.path.isdir(mkeep):
            os.replace(mkeep, mdir)
        if om and os.path.isdir(om):
            shutil.rmtree(om, ignore_errors=True)

    def pending_libs():
        """Collected MSL keys without a metallib yet = what the builder will compile."""
        try:
            have = {f[:-9] for f in os.listdir(os.path.join(lcdir, "lib")) if f.endswith(".metallib")}
        except OSError:
            have = set()
        try:
            return len([f for f in os.listdir(os.path.join(lcdir, "msl")) if f.endswith(".metal") and f[:-6] not in have])
        except OSError:
            return 0

    def build_libs():
        """KK's library cache: build the collected MSL into metallibs; returns the number of metallibs now present."""
        lc = libcache_builder(ctx)
        if lc and os.path.isdir(os.path.join(lcdir, "msl")):
            if runner(["bash", lc, lcdir, str(compile_jobs(ctx.environ))], env=benv) != 0:
                print("prepare: warning: %s failed; the library cache is not (fully) built (pipelines are still prepared)" % lc)
        try:
            return len([f for f in os.listdir(os.path.join(lcdir, "lib")) if f.endswith(".metallib")])
        except OSError:
            return 0

    # prepfast: a content-addressed metallib store next to the cache root (key = hash of MSL + KK build + macOS build + GPU, so a hit
    # is always valid). Pass 1 starts with every stored metallib linked into kk-libcache/lib: shaders seen before are LOADED instead of
    # compiled from source (a re-prepare after the list grew, or a second cache dir of the same stack, compiles only the new keys), and
    # the builder clones what it builds back into the store. NPS5_METALLIB_STORE= (empty) turns it off, a path moves it.
    store = ctx.environ["NPS5_METALLIB_STORE"] if "NPS5_METALLIB_STORE" in ctx.environ else ctx.root + ".metallib-store"
    benv = dict(ctx.environ, NPS5_METALLIB_STORE=store)

    def seed_libs():
        n = 0
        if store and os.path.isdir(store):
            ldir = os.path.join(lcdir, "lib")
            os.makedirs(ldir, exist_ok=True)
            for fn in os.listdir(store):
                if fn.endswith(".metallib") and not os.path.exists(os.path.join(ldir, fn)):
                    try:
                        os.link(os.path.join(store, fn), os.path.join(ldir, fn))
                    except OSError:
                        shutil.copyfile(os.path.join(store, fn), os.path.join(ldir, fn))
                    n += 1
        return n

    t0 = time.time()
    # Two passes. KK keys a compiled pipeline in its Metal archive on the library it was built from, and a library compiled from
    # source never matches the same shader loaded from an offline-built metallib. Pass 1 collects the MSL (source libraries) and the
    # builder makes the metallibs; pass 2 starts a fresh cache and a fresh Metal cache id WITH the metallibs in place, so its archive
    # is the one later runs hit. Only pass 2's archive is kept. No builder, or nothing collected = a single pass, as before.
    rd = os.path.join(ctx.scratch, "runs", ctx.tid, "cache-prepare")

    def one_pass(label, cmd=cmd):
        # The window shows the loading card ("PREPARING SHADERS X / T") for the whole pass; the terminal gets the same count.
        print("prepare: %s: building the shader list; a window with the progress card is open, the game does not start" % label, flush=True)
        tail = ProgressTail(rd, label); tail.start()
        try:
            return _call(cmd, ctx, runner)
        finally:
            tail.done()

    fresh_dir()
    nseed = seed_libs()
    if nseed:
        print("prepare: %d metallibs from earlier prepares are reused (only new shaders are compiled)" % nseed, flush=True)
    rc = one_pass("pass 1 of 2" if not o.get("single") else "pass 1")
    if rc == 0:
        print("prepare: building Metal libraries from the collected shaders (no window; this takes a few minutes on a first prepare)", flush=True)
    newlibs = pending_libs() if rc == 0 else 0           # MSL KK had to compile from source in pass 1 (no metallib for its key yet)
    nlib = build_libs() if rc == 0 else 0
    mst = metal_step(ctx, runner, rd) if rc == 0 and mlist else None
    passes = 1
    # prepfast: when every shader of pass 1 was LOADED from a metallib (seeded from the store: nothing new to build), pass 1's archive
    # is already the one later runs hit, so a fresh pass 2 would redo ~1,600 identical pipeline compiles. Keep the directory then: with
    # no generated kernels pass 2 is skipped, with them it only adds the kernel pipelines (everything else is an archive hit).
    # NPS5_PREPARE_KEEP_PASS1=0 restores the always-fresh pass 2.
    keep1 = rc == 0 and nlib > 0 and newlibs == 0 and not o.get("single") and ctx.environ.get("NPS5_PREPARE_KEEP_PASS1") != "0"
    if keep1 and not (mst and mst.get("written")):
        print("prepare: pass 1 loaded all %d shaders from metallibs (nothing new to compile): its cache is final, pass 2 skipped" % nlib)
    elif rc == 0 and nlib > 0 and not o.get("single"):
        if keep1:
            print("prepare: pass 1 loaded all %d shaders from metallibs; pass 2 only adds the generated kernels to the same cache" % nlib)
        else:
            print("prepare: pass 1 done (%d metallibs); pass 2 re-prepares with them" % nlib)
            fresh_dir(keep_libcache=True)
        cmd2 = cmd
        if mst and mst.get("written"):
            # pass 2 compiles every generated kernel once, synchronously, under the progress card; KK collects its MSL, so the
            # library build below makes its metallib and normal starts only load it (asynchronously)
            mw = [("KK_MSL_OVERRIDE_DIR", os.path.join(ctx.dir, metalmod.OUT_NAME)), ("KK_MSL_OVERRIDE_ASYNC", "0")]
            if metalmod.canon_enabled(ctx.environ):
                mw.append(("KK_MSL_CANON_KEY", "1"))
            cmd2 = run_cmd(ctx.tid, "cache-prepare", None, o["secs"] or PREPARE_SECS, words + mw)
        rc = one_pass("pass 2 of 2", cmd2); passes = 2
        if rc == 0:
            nlib = build_libs()
            if mst and mst.get("written"):
                mst["active"], mst["failed"] = metalmod.override_counts(run_logs(rd))
                print("prepare: Metal shaders: %d compiled, %d failed to compile (KosmicKrisp's own kernel is kept for those)" % (mst["active"], mst["failed"]))
                if mst.get("gfx_written"):
                    mst["gfx_active"], mst["gfx_failed"] = metalmod.gfx_override_counts(run_logs(rd))
                    print("prepare: Metal graphics: %d pairs compiled, %d failed to compile (KosmicKrisp's own pair is kept for those)" % (mst["gfx_active"], mst["gfx_failed"]))
    warm = 0
    if rc == 0 and (passes == 2 or keep1) and not o.get("nowarm"):
        warm = warm_starts(ctx, app0, o, runner, build_libs)
    info = {}
    try:
        info = parse_info(runner([tool_path(ctx), "info", ctx.list_path], env=ctx.environ, capture=True)[1])
    except CacheError:
        pass
    pt = os.path.join(ctx.dir, "prepared.tsv")
    with open(pt + ".tmp", "w") as f:
        f.write("# written by nps5 cache prepare; read by nps5 cache status (a key mismatch means stale)\n")
        for k, v in [("key", ctx.key), ("gpu", ctx.gpu), ("metal_id", ctx.metal), ("date", time.strftime("%Y-%m-%d", time.gmtime())),
                     ("rc", rc), ("wall_s", int(time.time() - t0)), ("pipelines", info.get("pipelines", "?")), ("passes", passes), ("metallibs", nlib), ("warm", warm)] + metal_fields(mst) + list(ctx.identity().items()):
            f.write("%s=%s\n" % (k, v))
    os.replace(pt + ".tmp", pt)            # whole or absent: status never reads a half-written record
    print("prepare: rc=%s, %s" % (rc, ctx.dir))
    if rc == 0 and mst and mst.get("mismatch"):
        # v1.1.1: the shipped lists are tied to the pinned Kyty (their `emitter` line); a mismatch is a component problem, not a
        # broken cache (the skipped shaders simply stay KosmicKrisp's), but it must not pass silently
        print("prepare: ERROR: component mismatch: %s" % mst["mismatch"])
        print("prepare: the cache works (KosmicKrisp's own shaders replace the skipped ones); exit code 3 = components differ from the release")
        return 3
    return 0 if rc == 0 else 1


def metal_list(ctx):
    """games/<id>/metal-shaders.tsv when this title has one and NPS5_METAL_SHADERS is not 0, else None."""
    p = os.path.join(ctx.p.dir, metalmod.LIST_NAME)
    return p if os.path.isfile(p) and metalmod.enabled(ctx.environ) else None


def metal_gfx_list(ctx):
    """games/<id>/metal-gfx-pairs.tsv (verified vertex+fragment pairs) under the same switch, else None. A list without pair rows
    (header only: no verified pairs for this title yet) is None too, so a prepare does not run an empty graphics step."""
    p = os.path.join(ctx.p.dir, metalmod.GFX_LIST_NAME)
    if not (os.path.isfile(p) and metalmod.enabled(ctx.environ)):
        return None
    try:
        with open(p) as f:
            if not metalmod.parse_gfx_list(f.read())[1]:
                return None
    except (metalmod.MetalError, OSError):
        pass                                            # a malformed list is reported by the graphics step
    return p


def run_logs(rd):
    t = ""
    for n in ("run.err", "run.out"):
        try:
            with open(os.path.join(rd, n), "rb") as f:
                t += f.read().decode(errors="replace")
        except OSError:
            pass
    return t


def metal_step(ctx, runner, rd=None, label="prepare: "):
    """Generate <cache dir>/metal-shaders (compute kernels, then the verified vertex+fragment pairs) from the keys lists, this cache's
    programs.bin and KosmicKrisp's collected MSL (metal.py). Never fails a prepare: any problem means KosmicKrisp's own shaders, as in
    v1.0. -> stats dict (written = kernels + pairs written) or None."""
    mlist, glist = metal_list(ctx), metal_gfx_list(ctx)
    if not mlist and not glist:
        return None
    print("%sgenerating our Metal shaders from your dump (CPU only, no window)" % label, flush=True)
    seen = metalmod.seen_keys(run_logs(rd)) if rd else None
    prog = os.path.join(ctx.dir, "%s.programs.bin" % ctx.tid)
    odir = os.path.join(ctx.dir, metalmod.OUT_NAME)
    st = dict(listed=0, written=0, skipped=0, seen=None, reasons={}, gfx_listed=0, gfx_written=0)
    try:
        if mlist:
            st = dict(st, **metalmod.generate(mlist, prog, odir, ctx.environ, jobs=compile_jobs(ctx.environ), seen=seen or None, label=label))
            print(label + metalmod.summary(st), flush=True)
            why = metalmod.emitter_problem(st, ctx.commits.get("kyty"))
            if why:
                print("%sERROR: Metal shaders: %s" % (label, why), flush=True)
                st["mismatch"] = why
            if st.get("seen") == 0 and st["written"]:
                print("%swarning: KosmicKrisp built none of the listed kernels here (another KosmicKrisp / macOS MSL): nothing will be swapped" % label)
    except (metalmod.MetalError, OSError) as e:
        print("%sMetal shaders: skipped (%s); KosmicKrisp's own kernels are used" % (label, e), flush=True)
        st["error"] = str(e)
    try:
        if glist:
            g = metalmod.generate_gfx(glist, prog, os.path.join(ctx.dir, "kk-libcache", "msl"), odir, ctx.environ, jobs=compile_jobs(ctx.environ), label=label,
                                      lens=metalmod.seen_vertex_lens(run_logs(rd)) if rd else None,
                                      alias=metalmod.seen_canon_aliases(run_logs(rd)) if rd else None,
                                      keep_dir=ctx.root + "." + metalmod.KK_VS_KEEP)   # outlives the cache dir (a new prepare starts empty)
            st["gfx_listed"], st["gfx_written"] = g["listed"], g["written"]
            st["written"] += g["written"]
            print(label + metalmod.gfx_summary(g), flush=True)
            why = metalmod.emitter_problem(g, ctx.commits.get("kyty"))
            if why:
                print("%sERROR: Metal graphics: %s" % (label, why), flush=True)
                st["mismatch"] = (st.get("mismatch", "") + " / " + why).strip(" /")
    except (metalmod.MetalError, OSError) as e:
        print("%sMetal graphics: skipped (%s); KosmicKrisp's own shaders are used" % (label, e), flush=True)
        st["error"] = str(e)
    if rd:
        try:
            ks = {r["kk"] for r in metalmod.parse_list(open(mlist).read())[1]} if mlist else set()
            ps = {(r["kk_vs"], r["kk_fs"]) for r in metalmod.parse_gfx_list(open(glist).read())[1]} if glist else set()
            line = metalmod.key_match_line(run_logs(rd), ks, ps)
            if line:
                print("%sMetal shaders: %s" % (label, line), flush=True)
                st["match"] = line
        except (metalmod.MetalError, OSError):
            pass
    return st


def metal_fields(st):
    if not st:
        return []
    return [("metal_shaders", "%d/%d" % (st["written"] - st.get("gfx_written", 0), st["listed"])), ("metal_seen", "" if st.get("seen") is None else st["seen"]),
            ("metal_active", st.get("active", "")), ("metal_failed", st.get("failed", "")),
            ("metal_gfx", "%d/%d" % (st.get("gfx_written", 0), st.get("gfx_listed", 0))), ("metal_gfx_active", st.get("gfx_active", "")),
            ("metal_gfx_failed", st.get("gfx_failed", "")), ("metal_mismatch", "1" if st.get("mismatch") else "")]


def cmd_metal(ctx, o, runner):
    if not metal_list(ctx) and not metal_gfx_list(ctx):
        print("no games/%s/%s or %s, or NPS5_METAL_SHADERS=0: nothing to generate" % (ctx.tid, metalmod.LIST_NAME, metalmod.GFX_LIST_NAME)); return 0
    if o["dry"]:
        print("[dry-run] would generate %s from %s and %s/%s.programs.bin" % (os.path.join(ctx.dir, metalmod.OUT_NAME), metal_list(ctx), ctx.dir, ctx.tid))
        return 0
    if o.get("reproduce"):
        return metal_reproduce(ctx)
    st = metal_step(ctx, runner, None, label="")
    return 0 if st and not st.get("error") and not st.get("mismatch") else 1


def metal_reproduce(ctx):
    """`nps5 cache metal <id> --reproduce`: regenerate every row of both lists for EVERY GPU class (not only this Mac's) from this
    cache and print the `reproduced=N of N` record line the per-class lists' .verified records need. Writes nothing into the cache."""
    import tempfile
    prog = os.path.join(ctx.dir, "%s.programs.bin" % ctx.tid)
    lp, gp = (os.path.join(ctx.p.dir, n) for n in (metalmod.LIST_NAME, metalmod.GFX_LIST_NAME))
    work = tempfile.mkdtemp(prefix="metal-repro-", dir=ctx.root)
    try:
        res = metalmod.reproduce(lp, gp, prog, os.path.join(ctx.dir, "kk-libcache", "msl"), ctx.environ, work, jobs=compile_jobs(ctx.environ),
                                 keep_dir=ctx.root + "." + metalmod.KK_VS_KEEP)
    except (metalmod.MetalError, OSError) as e:
        print("reproduce: %s" % e); return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for kind, key, cls, why in res["failed"]:
        print("NOT reproduced: %s %s class %s: %s" % (kind, key, cls, why))
    for l in metalmod.reproduce_record(res):
        print(l)
    return 0 if res["rows"] and res["reproduced"] == res["rows"] else 1


def warm_starts(ctx, app0, o, runner, build_libs):
    """Two short normal starts after the two passes (--no-warm skips them). The renderer's presentation / overlay blits and a few
    kernels exist only once a real start opens its window, so no list holds them: start 1 compiles them from source and KK collects
    their MSL, the library build turns that into metallibs, start 2 builds them from the metallibs so the Metal archive holds them
    under the identity every later start uses. Without them the first two starts of the player compile 5-6 of these (in the
    window-open half second, never in game). A failed warm start is only a warning: the prepared list is complete either way.
    -> number of warm starts that ended rc 0."""
    scenes = scene_names(ctx, [])
    scene = scenes[0] if scenes else None
    words = cache_env(ctx.tid, ctx.dir, ctx.metal) + launch_words(ctx, app0)
    ok = 0
    for i in (1, 2):
        print("prepare: warm start %d of 2: a short normal start builds the window's own helper pipelines (%s)" % (i, scene or "title"), flush=True)
        rc = _call(run_cmd(ctx.tid, "cache-warm-%d" % i, scene, o["secs"] or 120, words), ctx, runner)
        if rc != 0:
            print("prepare: warning: warm start %d ended rc=%s; the first normal starts may compile a few helper pipelines" % (i, rc), flush=True)
            break
        ok += 1
        if i == 1:
            build_libs()
    return ok


def libcache_builder(ctx):
    """The script that builds <KK_LIBCACHE_DIR>/msl into /lib: $NPS5_KK_LIBCACHE_BUILD, else scripts/kk-libcache-build.sh of this repo."""
    lc = ctx.environ.get("NPS5_KK_LIBCACHE_BUILD") or os.path.join(REPO, "scripts", "kk-libcache-build.sh")
    return lc if os.path.isfile(lc) else None


def clone_dir(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    r = subprocess.run(["cp", "-c", "-R", src, dst], capture_output=True)       # APFS clone: instant, no extra disk
    if r.returncode != 0:
        subprocess.run(["cp", "-R", src, dst], check=True)


def cmd_check(ctx, o, runner):
    st, lines = status(ctx)
    app0 = app0_of(ctx, o["dry"])
    if st != "prepared" and not o["dry"]:
        raise CacheError("\n".join(lines) + "\ncache check needs a prepared cache for this stack: nps5 cache prepare %s" % ctx.tid)
    rows = ["scene\trc\tcompiled\tlibraries\tstalls>%dms\tstall_s\tworst_ms\tred" % STALL_MS]
    out_dir = os.path.join(ctx.root, "check-%s" % ctx.key); os.makedirs(out_dir, exist_ok=True) if not o["dry"] else None
    bad = 0
    for s in scene_names(ctx, o["scene"]):
        d = os.path.join(ctx.root, "verify", ctx.key, s); mid = ctx.metal + ".v." + s
        words = cache_env(ctx.tid, d, mid) + launch_words(ctx, app0) + [("KYTY_PROGRESS_TITLE_LOG", "1"), ("KYTY_PIPELINE_STATS", "1")]
        tag = "cache-check-" + s
        cmd = run_cmd(ctx.tid, tag, s, o["secs"] or 180, words)
        if o["dry"]:
            print("[dry-run] clone %s -> %s; %s" % (ctx.dir, d, " ".join(shlex.quote(c) for c in cmd))); continue
        clone_dir(ctx.dir, d)
        om = os_metal_dir(ctx.metal)
        if om and os.path.isdir(om):
            clone_dir(om, os_metal_dir(mid))
        rd = os.path.join(ctx.scratch, "runs", ctx.tid, tag)
        t_start = time.time()
        rc = _call(cmd, ctx, runner)
        ms, err = [], ""
        # only this run's files: when the run was refused or never started, the folder still holds the previous check
        fresh = os.path.isfile(os.path.join(rd, "run.json")) and os.stat(os.path.join(rd, "run.json")).st_mtime >= t_start - 2
        if fresh:
            try:
                import frames as fr
                ms = fr.load_run_frames(rd).ms
            except Exception:
                ms = []
            try:
                with open(os.path.join(rd, "run.err"), errors="replace") as f: err = f.read()
            except OSError:
                err = ""
        bad += 1 if (rc != 0 or not fresh) else 0
        rows.append(check_row(s, rc if fresh or rc != 0 else "norun", parse_check(err, ms)))
        rmtree_inside(d, ctx.root)
        if om: shutil.rmtree(os_metal_dir(mid), ignore_errors=True)
    if not o["dry"]:
        with open(os.path.join(out_dir, "check.tsv"), "w") as f:
            f.write("\n".join(rows) + "\n")
        print("\n".join(rows))
        if bad:
            print("cache check: %d scene run(s) failed or did not start; their rows are not a measurement" % bad)
    return 1 if bad else 0


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
    o = dict(scene=[], secs=None, dry=False, rebuild=False, write=False, single=False, nowarm=False, list=None, shaders=[], contrib=[], pr=False, confirm=None, reproduce=False, **{"from": []})
    pos, i = [], 0
    VAL = {"--scene": "scene", "--from": "from", "--shaders": "shaders", "--secs": "secs", "--list": "list"}
    while i < len(argv):
        a = argv[i]
        if a == "--contrib":                       # takes every following non-option word (a shell glob)
            while i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                o["contrib"].append(argv[i + 1]); i += 1
        elif a in VAL:
            if i + 1 >= len(argv):
                raise CacheError("%s needs a value" % a, 2)
            k = VAL[a]; v = argv[i + 1]
            if isinstance(o[k], list): o[k].append(v)
            else: o[k] = v
            i += 1
        elif a in ("--dry-run", "--rebuild", "--write", "--single-pass", "--pr", "--no-warm", "--reproduce"):
            o[{"--dry-run": "dry", "--single-pass": "single", "--no-warm": "nowarm"}.get(a, a[2:])] = True
        elif a.startswith("--"):
            raise CacheError("unknown option %s" % a, 2)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 2:
        raise CacheError("usage: nps5 cache record|merge|share|verify-list|prepare|metal|check|status <id> [options]; see --help", 2)
    if o["secs"] is not None and not re.fullmatch(r"\d+", o["secs"]):
        raise CacheError("--secs must be a number", 2)
    return pos[0], pos[1], o


CMDS = {"metal": cmd_metal, "record": cmd_record, "merge": cmd_merge, "share": cmd_share, "verify-list": cmd_verify_list, "prepare": cmd_prepare, "check": cmd_check, "status": cmd_status}


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
    except OSError as e:
        print("nps5 cache: %s" % e, file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
