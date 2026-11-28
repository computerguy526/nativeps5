#!/usr/bin/env python3
"""cachezip: shrink a title's compiled shader caches on disk with APFS transparent compression (LZFSE). Standard library only.

  cachezip.py [--dry-run] [--min-age S] [--min-size B] [--no-verify] <dir>...
  nps5 cache prepare calls compress_dir() on the prepared cache dir unless NPS5_CACHE_COMPRESS=0.

Why: the caches are text and tables that compress well (measured on a 5.9 GB ASTRO cache: 5.9 -> 2.3 GB; MSL / shader texts 7-8x,
KosmicKrisp pipeline archive 1.7x, program cache 4x, Vulkan driver cache 1.2x). Compression is transparent: the game reads the
same bytes, content and mtime are unchanged. Files above 2 GiB cannot be compressed by APFS and are skipped.

Safe by construction:
  - a file that any process has open (lsof, includes mmaps) is skipped; so is a file changed less than --min-age seconds ago
    (default 120), so a cache a game is still writing is never touched. Do not run it while a game uses the dir anyway.
  - journals / known sets / last_scene files are skipped by name: the game rewrites them every run, which would drop the
    compression again (harmless, pointless). Everything else that is rewritten (program cache, driver cache) is only
    recompressed on the next prepare.
  - already-compressed files are skipped (idempotent). Files under --min-size (default 64 KiB) are skipped.
  - after compressing, the sha256 of the bytes read back is compared with the sha256 taken before; on a mismatch the
    file is restored decompressed and the run exits non-zero. --no-verify skips this (afsctool still verifies itself).
  - tools: /opt/homebrew/bin/afsctool or `afsctool` on PATH (-c -T LZFSE -s 10); otherwise `ditto --hfsCompression`
    (files over 512 MiB are skipped by ditto and stay as they are). Not macOS / no tool: does nothing, exit 0.
Env (library entry point only): NPS5_CACHE_COMPRESS=1 enables / 0 disables after prepare; unset = ask on a TTY (default yes), else DEFAULT_ON (True), no prompt. Exit codes: 0 ok, 1 verify failure, 2 usage."""
import hashlib, os, re, shutil, stat, subprocess, sys, tempfile

# Default for `nps5 cache prepare`: ON since the 2026-11-28 warm-start A/B-A on box 3 (ASTRO + SAROS, plain vs compressed cache,
# work/research/cachecomp-2026-11-27.md "exec: default"). NPS5_CACHE_COMPRESS=0 forces it off, =1 forces it on. Fallbacks: any
# failure (no lsof, no tool, game running, verify mismatch -> file restored) leaves the cache as it was and never fails a prepare.
DEFAULT_ON = True
UF_COMPRESSED = 0x20
MAX_SIZE = 2 * 1024 ** 3 - 1
DITTO_MAX = 512 * 1024 ** 2          # ditto --hfsCompression skips bigger files; afsctool handles up to MAX_SIZE
GAME_RE = re.compile(r"(^|/)(ue2)?boot/native_|/ue2boot/native_out\d+|(^|/)native(_[A-Za-z0-9_.-]+)?$|(^|/)kyty")
SKIP_SUFFIX = (".journal", ".known", ".last_scene", ".lock", ".tmp", ".pid")


def is_compressed(path):
    try:
        return bool(getattr(os.lstat(path), "st_flags", 0) & UF_COMPRESSED)
    except OSError:
        return False


def on_disk(path):
    try:
        return os.lstat(path).st_blocks * 512
    except OSError:
        return 0


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def open_files(root):
    """Set of paths under root that some process has open (lsof +D). None when lsof is unusable: callers then compress nothing."""
    if not shutil.which("lsof"):
        return None
    try:
        p = subprocess.run(["lsof", "-F", "n", "+D", root], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode not in (0, 1):
        return None
    return {os.path.realpath(l[1:]) for l in p.stdout.splitlines() if l.startswith("n/")}


def candidates(root, min_age=120, min_size=64 * 1024, now=None, open_set=frozenset()):
    """(path, reason) for every regular file under root; reason None = compress it, else why it is skipped."""
    import time
    now = time.time() if now is None else now
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != ".git"]
        for n in sorted(fn):
            p = os.path.join(dp, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if n.endswith(SKIP_SUFFIX):
                r = "rewritten every run"
            elif st.st_size < min_size:
                r = "small"
            elif st.st_size > MAX_SIZE:
                r = "over 2 GiB (APFS limit)"
            elif getattr(st, "st_flags", 0) & UF_COMPRESSED:
                r = "already compressed"
            elif st.st_mode & 0o111:
                r = "executable"
            elif now - st.st_mtime < min_age:
                r = "modified recently"
            elif os.path.realpath(p) in open_set:
                r = "in use"
            else:
                r = None
            out.append((p, r))
    return out


def _tool():
    for c in ("/opt/homebrew/bin/afsctool", "/usr/local/bin/afsctool", shutil.which("afsctool")):
        if c and os.path.exists(c):
            return ("afsctool", c)
    if sys.platform == "darwin" and os.path.exists("/usr/bin/ditto"):
        return ("ditto", "/usr/bin/ditto")
    return None


def _compress_one(tool, path):
    kind, exe = tool
    if kind == "afsctool":
        r = subprocess.run(["nice", "-n", "10", exe, "-c", "-T", "LZFSE", "-s", "10", path], capture_output=True)
        return r.returncode == 0
    if os.lstat(path).st_size > 512 * 1024 ** 2:
        return False
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".cachezip-", dir=d); os.close(fd)
    try:
        if subprocess.run(["nice", "-n", "10", exe, "--hfsCompression", path, tmp], capture_output=True).returncode != 0:
            return False
        if not is_compressed(tmp) or on_disk(tmp) >= on_disk(path) * 0.9:
            return False
        st = os.lstat(path)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns)); os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, path); tmp = None
        return True
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _size(path):
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def _over(res, path):
    res["over_limit_files"] += 1
    res["over_limit_bytes"] += _size(path)


def compress_dir(root, dry_run=False, min_age=120, min_size=64 * 1024, verify=True, log=print, compress=None, open_set=None):
    """Compress the eligible files under root. Returns dict(files, saved, before, after, skipped{reason: n}, failed[list]).
    `compress(path) -> bool` and `open_set` are injection points for tests."""
    res = dict(files=0, before=0, after=0, saved=0, skipped={}, failed=[], over_limit_files=0, over_limit_bytes=0)
    if not os.path.isdir(root):
        return res
    if open_set is None:
        open_set = open_files(root)
        if open_set is None:
            log("cachezip: lsof unusable, compressing nothing"); return res
    tool_kind = None
    if compress is None:
        tool = _tool()
        if tool is None:
            log("cachezip: no compression tool (needs macOS APFS), nothing to do"); return res
        tool_kind = tool[0]
        compress = lambda p: _compress_one(tool, p)
    ditto_only = tool_kind == "ditto"
    for p, why in candidates(root, min_age, min_size, open_set=open_set):
        if why:
            res["skipped"][why] = res["skipped"].get(why, 0) + 1
            if why.startswith("over 2 GiB"):
                _over(res, p)
            continue
        if ditto_only and _size(p) > DITTO_MAX:      # eligible but ditto cannot do it: counted as over the limit
            res["skipped"]["over 512 MiB (ditto limit)"] = res["skipped"].get("over 512 MiB (ditto limit)", 0) + 1
            _over(res, p)
            continue
        b = on_disk(p)
        if dry_run:
            res["files"] += 1; res["before"] += b
            continue
        h0 = sha(p) if verify else None
        st0 = os.lstat(p)
        if not compress(p):
            res["skipped"]["no gain"] = res["skipped"].get("no gain", 0) + 1
            continue
        if verify and (sha(p) != h0 or os.lstat(p).st_size != st0.st_size):
            res["failed"].append(p)
            log("cachezip: VERIFY FAILED %s, restoring" % p)
            try:
                subprocess.run(["afsctool", "-d", p], capture_output=True)
            except OSError:
                pass
            continue
        a = on_disk(p)
        res["files"] += 1; res["before"] += b; res["after"] += a
    res["saved"] = res["before"] - res["after"]
    return res


def report(res, dry_run):
    gb = lambda n: "%.2f GB" % (n / 1e9)
    sk = ", ".join("%s %d" % kv for kv in sorted(res["skipped"].items())) or "none"
    if res.get("over_limit_files"):
        sk += "; %d file(s), %s, left uncompressed because they are over the size limit" % (res["over_limit_files"], gb(res["over_limit_bytes"]))
    if dry_run:
        return "cachezip dry-run: %d files (%s on disk) would be considered; skipped: %s" % (res["files"], gb(res["before"]), sk)
    return "cachezip: %d files compressed, %s -> %s (saved %s); skipped: %s%s" % (
        res["files"], gb(res["before"]), gb(res["after"]), gb(res["saved"]), sk, "; FAILED %d" % len(res["failed"]) if res["failed"] else "")


def game_running():
    """Lines of `ps` that look like a running game; ['ps failed: ...'] when it cannot be told (callers then refuse)."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, check=True).stdout
    except Exception as e:
        return ["ps failed: %s" % e]
    me = os.getpid()
    hits = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) != me and GAME_RE.search(parts[1]):
            hits.append(line.strip())
    return hits


def answer_file(environ):
    d = environ.get("NPS5_DATA_STATE_DIR") or os.path.join(environ.get("HOME") or os.path.expanduser("~"), "Library", "Application Support", "nativeps5", "data")
    return os.path.join(d, "game-compress-answer")


def game_files_answer(environ):
    """True / False when the user already said yes / no to compressing the game files (NPS5_GAME_COMPRESS=1/0, else the answer
    file `nps5 data compress --apply` writes), None when never asked."""
    v = environ.get("NPS5_GAME_COMPRESS")
    if v in ("1", "0"):
        return v == "1"
    try:
        with open(answer_file(environ)) as fh:
            t = fh.read().strip()
    except OSError:
        return None
    return {"yes": True, "no": False}.get(t)


def record_game_answer(environ, yes):
    try:
        f = answer_file(environ); os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "w") as fh:
            fh.write("yes\n" if yes else "no\n")
    except OSError:
        pass


def want_compress(environ=None, interactive=None, ask=input, log=print):
    """Whether `nps5 cache prepare` should compress the finished cache. Order: NPS5_CACHE_COMPRESS=1/0 always wins; no TTY on
    stdin (or interactive=False) -> DEFAULT_ON, no prompt; TTY -> ask in the style of the other nps5 prompts, default yes when
    the user said yes to compressing the game files (else DEFAULT_ON). EOF / Ctrl-C at the prompt = the default."""
    environ = os.environ if environ is None else environ
    v = environ.get("NPS5_CACHE_COMPRESS")
    if v in ("1", "0"):
        return v == "1"
    if interactive is None:
        try:
            interactive = sys.stdin.isatty()
        except (AttributeError, ValueError):
            interactive = False
    default = DEFAULT_ON or game_files_answer(environ) is True   # DEFAULT_ON True: the prompt defaults to yes
    if not interactive:
        return DEFAULT_ON
    try:
        ans = ask("Compress the shader cache too (about 2.5x smaller, same bytes for the game)? [%s] " % ("Y/n" if default else "y/N")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        log(""); return default
    return default if not ans else ans[0] == "y"


def maybe_compress(root, environ=None, log=print, interactive=None, ask=input, running=None):
    """Entry point for `nps5 cache prepare` (the prepare run has exited, so min age is 0; lsof still guards): honours
    NPS5_CACHE_COMPRESS, never raises."""
    environ = os.environ if environ is None else environ
    try:
        if not want_compress(environ, interactive, ask, log):
            return None
        g = (running or game_running)()
        if g:
            log("cachezip: not compressing, a game is running (%s)" % g[0]); return None
        res = compress_dir(root, min_age=int(environ.get("NPS5_CACHE_COMPRESS_MIN_AGE", "0")), log=log)
        log(report(res, False))
        return res
    except Exception as e:  # compression is an optimisation, never fail a prepare over it
        log("cachezip: skipped (%s)" % e)
        return None


def main(argv):
    dry, verify, age, size, dirs = False, True, 120, 64 * 1024, []
    it = iter(argv)
    for a in it:
        if a == "--dry-run": dry = True
        elif a == "--no-verify": verify = False
        elif a == "--min-age": age = int(next(it))
        elif a == "--min-size": size = int(next(it))
        elif a.startswith("-"):
            print(__doc__); return 2
        else: dirs.append(a)
    if not dirs:
        print(__doc__); return 2
    bad = 0
    for d in dirs:
        res = compress_dir(d, dry_run=dry, min_age=age, min_size=size, verify=verify)
        print(report(res, dry)); bad += len(res["failed"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
