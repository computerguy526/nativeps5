#!/usr/bin/env python3
"""nps5 Metal shaders: our own Metal translation (Kyty's MSL back end, "kk-bda" convention) for the verified compute kernels of a
title, GENERATED ON THIS MAC from the player's own dump. Standard library only.

  nps5 cache metal <id> [--dry-run]      (re)generate <cache dir>/metal-shaders/ from games/<id>/metal-shaders.tsv
                                         (`nps5 cache prepare` does this between its two passes)

Nothing game-derived is shipped: games/<id>/metal-shaders.tsv holds KEYS ONLY (hashes) of the kernels that were verified
(differential GPU test against KosmicKrisp's own kernel, benchmark, play test). For every key this module finds the permutation
in the prepared cache's <id>.programs.bin (guest code + specialization, written by Kyty from your dump), runs
`kyty_msl_dump --kk-bda --no-comments` for it and keeps the text only when its sha256 equals the list's `b_msl` hash, i.e.
when this Mac produced byte for byte the kernel that was verified. The result is <cache dir>/metal-shaders/<kk_msl>.metal, the
file name KosmicKrisp's override hook (KK_MSL_OVERRIDE_DIR) looks up for a compute kernel whose own MSL hashes to <kk_msl>.
Every failure ends at KosmicKrisp's own kernel: a skipped row is simply not written, a key KK never sees is never looked up, and
KK keeps its own pipeline when a replacement fails to compile.

Switches: NPS5_METAL_SHADERS=0 turns the feature off (no generation, no KK_MSL_OVERRIDE_DIR at launch).
          NPS5_MSL_TOOL = the kyty_msl_dump binary (default <NPS5_KYTY_DIR>/build-arm64/kyty_msl_dump).
Generated files never leave NPS5_SCRATCH (git-ignored); never commit or upload them."""
import hashlib, os, re, shutil, struct, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

FORMAT = ("nps5-metal-keys", "1")
LIST_NAME = "metal-shaders.tsv"
OUT_NAME = "metal-shaders"
ROW_RE = re.compile(r"^([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{16})\t([0-9a-f]{64})\t(\d+x\d+x\d+)\t([a-z0-9_,]*)$")
HEADER = "// kk-mslinject v1 local_size "
KPRC = 0x4352504B
SEEN_RE = re.compile(r"\[kk-mslinject\] seen ([0-9a-f]{16})")
OVERRIDE_RE = re.compile(r"\[kk-mslinject\] override ([0-9a-f]{16}) (local_size|FAILED)")


class MetalError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg); self.code = code


def enabled(environ):
    return (environ.get("NPS5_METAL_SHADERS") or "1").strip() not in ("0", "off", "no", "false")


# ---------------------------------------------------------------- the keys list (pure)
def parse_list(text):
    """-> (meta dict, rows). Rows: dict(program, spec, kk, b, local, evidence). Raises MetalError on any malformed line, so a
    corrupted list never generates anything."""
    meta, rows, seen = {}, [], {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if f[0] in ("format", "title", "emitter"):
            meta[f[0]] = tuple(f[1:])
            continue
        m = ROW_RE.match(line)
        if not m:
            raise MetalError("%s line %d: not a key row (32 hex, 16 hex, 16 hex, 64 hex, XxYxZ, evidence)" % (LIST_NAME, n), 2)
        r = dict(zip(("program", "spec", "kk", "b", "local", "evidence"), m.groups()))
        if seen.setdefault(r["kk"], r["b"]) != r["b"]:       # two permutations may share one kernel, never with different texts
            raise MetalError("%s line %d: kk_msl %s listed twice with different texts" % (LIST_NAME, n, r["kk"]), 2)
        rows.append(r)
    if meta.get("format", ())[:2] != FORMAT:
        raise MetalError("%s: missing or unknown `format` line (want %s)" % (LIST_NAME, " ".join(FORMAT)), 2)
    return meta, rows


def spec_key(spec):
    return hashlib.sha256(spec).hexdigest()[:16]


def read_perms(path):
    """Kyty's program cache (<id>.programs.bin): -> {(program digest hex, spec key): permutation index}. The index counts every
    record that is not a source record (kind 1), as kyty_msl_dump --perm does. The first record of a key wins."""
    with open(path, "rb") as f:
        d = f.read()
    out = {}
    if len(d) < 32:
        return out
    off = 16
    n = struct.unpack_from("<Q", d, off)[0]
    off += 8 + n + 8
    idx = 0
    while off + 24 <= len(d):
        magic, kind, size = struct.unpack_from("<IIQ", d, off)
        if magic != KPRC:
            break
        p = off + 24
        if p + size > len(d):
            break
        if kind != 1:
            if size >= 24:
                sl = struct.unpack_from("<I", d, p + 20)[0]
                if p + 24 + sl <= p + size:
                    out.setdefault((d[p:p + 16].hex(), spec_key(d[p + 24:p + 24 + sl])), idx)
            idx += 1
        off = p + size
    return out


def seen_keys(text):
    return set(SEEN_RE.findall(text))


def override_counts(text):
    """-> (compiled, failed) override kernels in a run log (distinct keys)."""
    ok, bad = set(), set()
    for k, what in OVERRIDE_RE.findall(text):
        (bad if what == "FAILED" else ok).add(k)
    return len(ok), len(bad - ok)


def clean_env(environ):
    """The generator's environment: no KYTY_* / KK_* switch may change the emitted text (the list's hashes were made without any)."""
    keep = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
    return {k: v for k, v in environ.items() if k in keep}


# ---------------------------------------------------------------- generation
def tool_path(environ):
    t = environ.get("NPS5_MSL_TOOL") or os.path.join(environ.get("NPS5_KYTY_DIR", ""), "build-arm64", "kyty_msl_dump")
    return t if os.path.isfile(t) and os.access(t, os.X_OK) else None


def run_dump(tool, programs, perm, out, env):
    r = subprocess.run([tool, "--programs", programs, "--perm", str(perm), "--kk-bda", "--no-comments", "--out", out],
                       capture_output=True, text=True, errors="replace", env=env, timeout=120)
    return r.returncode, r.stderr


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class Progress:
    """Terminal progress for the generation step (no window is open then): 'Metal shaders: N/T (P%)' at most every `every` s."""
    def __init__(self, total, label, out=None, every=5.0, now=time.time):
        self.total, self.label, self.out, self.every, self.now = total, label, out or sys.stdout, every, now
        self.done, self.last, self.lock = 0, 0.0, threading.Lock()

    def step(self):
        with self.lock:
            self.done += 1
            t = self.now()
            if self.done == self.total or t - self.last >= self.every:
                self.last = t
                print("%sMetal shaders: %d/%d (%d%%)" % (self.label, self.done, self.total, 100 * self.done // max(1, self.total)),
                      file=self.out, flush=True)


def generate(list_path, programs, out_dir, environ, jobs=4, seen=None, dump=run_dump, label="", out=None):
    """Write out_dir (atomically replaced) with <kk>.metal per accepted row + index.tsv + skipped.tsv.
    -> dict(listed, written, skipped, seen (None if unknown), reasons {reason: count})."""
    out = out or sys.stdout
    with open(list_path) as f:
        text = f.read()
    meta, rows = parse_list(text)
    list_sha = hashlib.sha256(text.encode()).hexdigest()
    tool = tool_path(environ)
    if not tool:
        raise MetalError("no kyty_msl_dump (build it with scripts/build.sh, or set NPS5_MSL_TOOL)")
    if not os.path.isfile(programs):
        raise MetalError("no %s (prepare the cache first)" % programs)
    perms = read_perms(programs)
    env = clean_env(environ)
    parent = os.path.dirname(out_dir)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".metal-new-", dir=parent)
    prog = Progress(len(rows), label, out)

    def one(ir):
        i, r = ir
        try:
            idx = perms.get((r["program"], r["spec"]))
            if idx is None:
                return r, "not in programs.bin"
            dst = os.path.join(tmp, r["kk"] + ".metal")
            part = "%s.%d.part" % (dst, i)
            try:
                rc, err = dump(tool, programs, idx, part, env)
            except (OSError, subprocess.SubprocessError) as e:
                return r, "dump failed: %s" % type(e).__name__
            m = re.search(r"// complete=(\d)", err or "")
            if rc != 0 or not os.path.isfile(part):
                return r, "dump failed rc=%s" % rc
            if m and m.group(1) != "1":
                return r, "incomplete translation"
            with open(part, "rb") as f:
                head = f.read(len(HEADER))
            if head != HEADER.encode():
                return r, "no kk-mslinject header"
            if file_sha(part) != r["b"]:
                return r, "text differs from the verified kernel"
            os.replace(part, dst)
            return r, None
        finally:
            prog.step()

    try:
        with ThreadPoolExecutor(max(1, jobs)) as ex:
            res = list(ex.map(one, enumerate(rows)))
        for fn in os.listdir(tmp):
            if fn.endswith(".part"):
                os.remove(os.path.join(tmp, fn))
        ok = list({r["kk"]: r for r, why in res if why is None}.values())   # one index row per kernel
        bad = [(r, why) for r, why in res if why is not None]
        with open(os.path.join(tmp, "index.tsv"), "w") as f:
            f.write("# written by nps5 cache metal; LOCAL ONLY (generated from your dump). list_sha=%s\n" % list_sha)
            f.write("list_sha\t%s\n" % list_sha)
            for r in ok:
                f.write("%s\t%s\t%s\t%s\n" % (r["kk"], r["program"], r["spec"], r["local"]))
        with open(os.path.join(tmp, "skipped.tsv"), "w") as f:
            for r, why in bad:
                f.write("%s\t%s\t%s\t%s\n" % (r["kk"], r["program"], r["spec"], why))
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        os.replace(tmp, out_dir)
    finally:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
    reasons = {}
    for _, why in bad:
        reasons[why] = reasons.get(why, 0) + 1
    nseen = None if seen is None else len({r["kk"] for r in rows} & set(seen))
    return dict(listed=len({r["kk"] for r in rows}), written=len(ok), skipped=len(bad), seen=nseen, reasons=reasons, list_sha=list_sha)


def summary(st):
    s = "Metal shaders: %d of %d verified kernels generated" % (st["written"], st["listed"])
    if st["reasons"]:
        s += "; skipped: " + ", ".join("%s %d" % (k, v) for k, v in sorted(st["reasons"].items()))
    if st.get("seen") is not None:
        s += "; KosmicKrisp built %d of the listed kernels in pass 1" % st["seen"]
    return s


# ---------------------------------------------------------------- launch words
def index_rows(out_dir):
    """-> (list_sha, number of kernels) of a generated dir, (None, 0) when absent / torn."""
    try:
        with open(os.path.join(out_dir, "index.tsv")) as f:
            lines = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return None, 0
    sha = next((l[1] for l in lines if l[0] == "list_sha" and len(l) > 1), None)
    n = sum(1 for l in lines if l[0] != "list_sha" and len(l) >= 4 and os.path.isfile(os.path.join(out_dir, l[0] + ".metal")))
    return sha, n


def launch_words(cache_dir, list_path, environ):
    """-> ([(key, value)], warning or None): KK_MSL_OVERRIDE_DIR for a normal start when this cache has generated kernels for the
    current list and the feature is on."""
    if not enabled(environ):
        return [], None
    d = os.path.join(cache_dir, OUT_NAME)
    sha, n = index_rows(d)
    if not n:
        return [], None
    try:
        with open(list_path, "rb") as f:
            cur = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        cur = None
    if sha != cur:
        return [], "the Metal shaders of this cache were made for another %s: not used; `nps5 cache metal <id>` regenerates them" % LIST_NAME
    return [("KK_MSL_OVERRIDE_DIR", d)], None
