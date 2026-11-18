#!/usr/bin/env python3
"""nps5 Metal shaders: our own Metal translation (Kyty's MSL back end, "kk-bda" convention) for the verified compute kernels AND
the verified graphics (vertex + fragment) pairs of a title, GENERATED ON THIS MAC from the player's own dump. Standard library only.

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

Graphics: games/<id>/metal-gfx-pairs.tsv lists verified (vertex, fragment) PAIRS the same way (hashes of the programs, of
KosmicKrisp's two MSL texts and of ours, plus the numeric layout words the emitter needs). For each pair this module generates the
fragment text, then the vertex text linked to the fragment's reads and to KosmicKrisp's own vertex text (read from this cache's
kk-libcache/msl, written by pass 1), and keeps BOTH files only when both sha256 equal the list's. They go to
<cache dir>/metal-shaders/<kk_vs>_<kk_fs>.vs.metal / .fs.metal; KosmicKrisp swaps a pipeline only when its two keys match a pair.

Switches: NPS5_METAL_SHADERS=0 turns the feature off (no generation, no KK_MSL_OVERRIDE_DIR at launch).
          NPS5_MSL_TOOL = the kyty_msl_dump binary (default <NPS5_KYTY_DIR>/build-arm64/kyty_msl_dump).
Generated files never leave NPS5_SCRATCH (git-ignored); never commit or upload them."""
import hashlib, os, re, shutil, struct, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

FORMAT = ("nps5-metal-keys", "1")
LIST_NAME = "metal-shaders.tsv"
OUT_NAME = "metal-shaders"
GFX_LIST_NAME = "metal-gfx-pairs.tsv"
GFX_INDEX = "index-gfx.tsv"
GFX_FORMAT = ("nps5-metal-gfx-pairs", "1")
GFX_ROW_RE = re.compile(r"^gfx\t([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{16})\t([0-9a-f]{16})\t([0-9a-f]{64})\t([0-9a-f]{64})"
                        r"\t(mipstats|twin)\t([0-9:,]*)\t([0-9:,]*)\t(\d+)\t([a-z0-9_+,.-]*)$")
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


GFX_OVERRIDE_RE = re.compile(r"\[kk-mslinject\] gfx override ([0-9a-f]{16}/[0-9a-f]{16}) (FAILED|[0-9.]+ ms)")


def gfx_override_counts(text):
    """-> (compiled, failed) graphics override pairs in a run log (distinct pairs)."""
    ok, bad = set(), set()
    for k, what in GFX_OVERRIDE_RE.findall(text):
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
    def __init__(self, total, label, out=None, every=5.0, now=time.time, what="Metal shaders"):
        self.total, self.label, self.out, self.every, self.now, self.what = total, label, out or sys.stdout, every, now, what
        self.done, self.last, self.lock = 0, 0.0, threading.Lock()

    def step(self):
        with self.lock:
            self.done += 1
            t = self.now()
            if self.done == self.total or t - self.last >= self.every:
                self.last = t
                print("%s%s: %d/%d (%d%%)" % (self.label, self.what, self.done, self.total, 100 * self.done // max(1, self.total)),
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


# ---------------------------------------------------------------- BLAKE3 (KosmicKrisp's override key = BLAKE3-64 of its MSL text)
_B3_IV = (0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A, 0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19)
_B3_PERM = (2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8)
_M32 = 0xFFFFFFFF


def _b3_compress(cv, m, counter, blen, flags):
    v = list(cv) + list(_B3_IV[:4]) + [counter & _M32, counter >> 32, blen, flags]
    m = list(m)

    def g(a, b, c, d, x, y):
        v[a] = (v[a] + v[b] + x) & _M32
        t = v[d] ^ v[a]; v[d] = ((t >> 16) | (t << 16)) & _M32
        v[c] = (v[c] + v[d]) & _M32
        t = v[b] ^ v[c]; v[b] = ((t >> 12) | (t << 20)) & _M32
        v[a] = (v[a] + v[b] + y) & _M32
        t = v[d] ^ v[a]; v[d] = ((t >> 8) | (t << 24)) & _M32
        v[c] = (v[c] + v[d]) & _M32
        t = v[b] ^ v[c]; v[b] = ((t >> 7) | (t << 25)) & _M32
    for r in range(7):
        g(0, 4, 8, 12, m[0], m[1]); g(1, 5, 9, 13, m[2], m[3]); g(2, 6, 10, 14, m[4], m[5]); g(3, 7, 11, 15, m[6], m[7])
        g(0, 5, 10, 15, m[8], m[9]); g(1, 6, 11, 12, m[10], m[11]); g(2, 7, 8, 13, m[12], m[13]); g(3, 4, 9, 14, m[14], m[15])
        if r < 6:
            m = [m[i] for i in _B3_PERM]
    return [v[i] ^ v[i + 8] for i in range(8)] + [v[i + 8] ^ cv[i] for i in range(8)]


def blake3_64(data):
    """First 8 bytes (16 hex digits) of the BLAKE3 hash of data: the key KosmicKrisp files a replacement under."""
    def block_words(b):
        b = b + b"\0" * (64 - len(b))
        return struct.unpack("<16I", b)

    def chunk_cv(chunk, counter, root):
        cv = _B3_IV
        nb = max(1, (len(chunk) + 63) // 64)
        for i in range(nb):
            blk = chunk[i * 64:(i + 1) * 64]
            fl = (1 if i == 0 else 0) | (2 if i == nb - 1 else 0) | (8 if root and i == nb - 1 else 0)
            out = _b3_compress(cv, block_words(blk), counter, len(blk), fl)
            cv = out[:8]
        return out if root else cv

    n = max(1, (len(data) + 1023) // 1024)
    if n == 1:
        out = chunk_cv(data, 0, True)
        return struct.pack("<16I", *out)[:8].hex()
    stack = []
    for i in range(n):
        cv = chunk_cv(data[i * 1024:(i + 1) * 1024], i, False)
        t = i + 1
        while t % 2 == 0 and i < n - 1:                 # merge completed subtrees (the last chunk is folded below)
            left = stack.pop()
            cv = _b3_compress(_B3_IV, tuple(left) + tuple(cv), 0, 64, 4)[:8]
            t //= 2
        stack.append(cv)
    cv = stack.pop()
    while stack:
        left = stack.pop()
        out = _b3_compress(_B3_IV, tuple(left) + tuple(cv), 0, 64, 4 | (8 if not stack else 0))
        cv = out[:8]
    return struct.pack("<16I", *out)[:8].hex()


# ---------------------------------------------------------------- graphics pairs (vertex + fragment)
def parse_gfx_list(text):
    """-> (meta dict, rows). Row: dict(vs, vs_spec, ps, ps_spec, kk_vs, kk_fs, b_vs, b_fs, variant, set0, va, cmask, evidence).
    Raises MetalError on a malformed line or on one (kk_vs, kk_fs) pair listed with different texts."""
    meta, rows, seen = {}, [], {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if f[0] in ("format", "title", "emitter"):
            meta[f[0]] = tuple(f[1:])
            continue
        m = GFX_ROW_RE.match(line)
        if not m:
            raise MetalError("%s line %d: not a pair row" % (GFX_LIST_NAME, n), 2)
        r = dict(zip(("vs", "vs_spec", "ps", "ps_spec", "kk_vs", "kk_fs", "b_vs", "b_fs", "variant", "set0", "va", "cmask", "evidence"), m.groups()))
        if seen.setdefault((r["kk_vs"], r["kk_fs"]), (r["b_vs"], r["b_fs"])) != (r["b_vs"], r["b_fs"]):
            raise MetalError("%s line %d: pair %s_%s listed twice with different texts" % (GFX_LIST_NAME, n, r["kk_vs"], r["kk_fs"]), 2)
        rows.append(r)
    if meta.get("format", ())[:2] != GFX_FORMAT:
        raise MetalError("%s: missing or unknown `format` line (want %s)" % (GFX_LIST_NAME, " ".join(GFX_FORMAT)), 2)
    return meta, rows


SEEN_LEN_RE = re.compile(r"\[kk-mslinject\] seen ([0-9a-f]{16}) len=(\d+) vertex")


def seen_vertex_lens(text):
    """-> {key: byte length} of the vertex shaders KosmicKrisp logged in a run (KK_MSL_OVERRIDE_LOG=1)."""
    return {k: int(n) for k, n in SEEN_LEN_RE.findall(text)}


def find_kk_vertex(kk_msl_dir, wanted, lens=None):
    """-> {key: path}: the files of KosmicKrisp's library cache (named by its own cache key) whose text hashes to a wanted override key.
    A logged length narrows the candidates to files of that size; without a log every vertex text is hashed (BLAKE3 in pure Python:
    slow, a minute or two on a big cache)."""
    lens = lens or {}
    try:
        files = [(fn, os.path.getsize(os.path.join(kk_msl_dir, fn))) for fn in os.listdir(kk_msl_dir) if fn.endswith(".metal")]
    except OSError:
        return {}
    b3sum = shutil.which("b3sum")
    found = {}
    for key in wanted:
        want = lens.get(key)
        for fn, size in sorted(files):
            if key in found or (want is not None and size != want):
                continue
            path = os.path.join(kk_msl_dir, fn)
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if want is None and b"struct VertexOut" not in data[:4096]:
                continue
            if b3sum:
                h = subprocess.run([b3sum, "--no-names"], input=data, capture_output=True).stdout.decode()[:16]
            else:
                h = blake3_64(data)
            if h == key:
                found[key] = path
    return found


def run_dump_gfx(tool, programs, perm, out, env, extra=()):
    r = subprocess.run([tool, "--programs", programs, "--perm", str(perm), "--kk-bda"] + list(extra) + ["--out", out],
                       capture_output=True, text=True, errors="replace", env=env, timeout=120)
    return r.returncode, r.stderr


def kk_vs_outs(text):
    """Parameter locations (and component counts) KosmicKrisp's vertex shader writes: -> 'loc:n,...'."""
    i = text.find("struct VertexOut")
    vo = text[i:] if i >= 0 else ""
    vo = vo[:vo.find("};")]
    outs = {}
    for _ty, n, loc in re.findall(r"(float|uint|int|half)(\d?) vary_(\d+) \[\[user", vo):
        outs[int(loc)] = int(n or 1)
    return ",".join("%d:%d" % (k, v) for k, v in sorted(outs.items()))


def kk_slot_set(t):
    """Vertex attribute slots KK's vertex shader pulls (root + 136 + 8k, clamp read at root + 392 + 4k from the same base)."""
    by = {}
    for b, o in re.findall(r"= (\w+) \+ ulong\((\d+)u\)", t):
        by.setdefault(b, set()).add(int(o))
    return max(({o for o in v if 136 <= o < 392 and (o - 136) % 8 == 0 and 392 + (o - 136) // 2 in v} for v in by.values()), key=len, default=set())


def slot_types(t):
    """Element type each vertex attribute slot is fetched as: {offset: type}."""
    L = t.splitlines()
    out = {}
    for i, l in enumerate(L):
        m = re.search(r"(?:ulong\(|kk_rt \+ )(\d+)u\)", l)
        if not m or not (136 <= int(m.group(1)) < 392 and (int(m.group(1)) - 136) % 8 == 0) or int(m.group(1)) in out:
            continue
        var = "kk_vb"
        if "kk_vb" not in l:
            mv = re.match(r"\s*(\w+) = ", l)
            a1 = mv and mv.group(1)
            nx = [re.match(r"\s*(\w+) = \*\(constant ulong\*\)\((\w+)\);", x) for x in L[i + 1:i + 4]]
            nx = [x for x in nx if x and x.group(2) == a1]
            if not nx:
                continue
            var = nx[0].group(1)
        for l2 in L[i + 1:i + 40]:
            if "*)%s)" % var in l2:
                ty = [x for x in re.findall(r"\(constant (\w+)\*\)", l2) if x != "ulong"]
                if ty:
                    out[int(m.group(1))] = ty[0]
                break
    return out


def gfx_env(base, r, vs_outs):
    """The emitter's layout words for one pair (the numbers come from the keys list, the vertex outputs from KK's own text)."""
    e = dict(base)
    e["KYTY_MSL_KK_SET0"] = r["set0"]
    e["KYTY_MSL_KK_VA"] = r["va"]
    e["KYTY_MSL_KK_COLOR_MASK"] = r["cmask"]
    e["KYTY_MSL_KK_VS_OUTS"] = vs_outs
    if r["variant"] == "twin":
        e["KYTY_MSL_MIPSTATS_PLAIN"] = "1"
    return e


def gen_pair(r, tool, programs, perms, kk_vs_path, tmp, i, env, dump=run_dump_gfx):
    """-> (None, (vs_part, fs_part)) when both texts equal the verified ones, else (reason, None)."""
    vi, pi = perms.get((r["vs"], r["vs_spec"])), perms.get((r["ps"], r["ps_spec"]))
    if vi is None or pi is None:
        return "not in programs.bin", None
    try:
        with open(kk_vs_path or os.devnull, errors="replace") as f:
            kt = f.read()
    except OSError:
        kt = ""
    if not kt:
        return "KosmicKrisp vertex text not in the library cache", None
    e = gfx_env(env, r, kk_vs_outs(kt))
    ps_out, vs_out = os.path.join(tmp, "%d.fs.part" % i), os.path.join(tmp, "%d.vs.part" % i)
    try:
        rc, err = dump(tool, programs, pi, ps_out, e)
        m = re.search(r"// complete=(\d)", err or "")
        if rc != 0 or not os.path.isfile(ps_out) or (m and m.group(1) != "1"):
            return "fragment translation failed", None
        with open(ps_out, errors="replace") as f:
            pt = f.read()
        mr = re.search(r"^// kk-bda ps_reads(.*)$", pt, re.M)
        if mr:
            e["KYTY_MSL_KK_PS_READS"] = ",".join(mr.group(1).split())
        rc, err = dump(tool, programs, vi, vs_out, e, ("--link-ps", str(pi)))
        m = re.search(r"// complete=(\d)", err or "")
        if rc != 0 or not os.path.isfile(vs_out) or (m and m.group(1) != "1"):
            return "vertex translation failed", None
    except (OSError, subprocess.SubprocessError) as ex:
        return "dump failed: %s" % type(ex).__name__, None
    with open(vs_out, errors="replace") as f:
        vt = f.read()
    if len(kk_slot_set(kt)) != vt.count("const ulong kk_vb") or slot_types(kt) != slot_types(vt):
        return "vertex attribute layout differs from KosmicKrisp's", None
    if not vt.startswith("// kk-mslinject v1 stage vertex") or not pt.startswith("// kk-mslinject v1 stage fragment"):
        return "no kk-mslinject header", None
    if file_sha(vs_out) != r["b_vs"] or file_sha(ps_out) != r["b_fs"]:
        return "text differs from the verified pair", None
    return None, (vs_out, ps_out)


def generate_gfx(list_path, programs, kk_msl_dir, out_dir, environ, jobs=4, dump=run_dump_gfx, label="", out=None, lens=None):
    """Add <kk_vs>_<kk_fs>.vs.metal/.fs.metal to out_dir (created if needed; call after generate(), which replaces the directory)
    plus index-gfx.tsv/skipped-gfx.tsv. -> dict(listed, written, skipped, reasons, list_sha)."""
    out = out or sys.stdout
    with open(list_path) as f:
        text = f.read()
    _meta, rows = parse_gfx_list(text)
    list_sha = hashlib.sha256(text.encode()).hexdigest()
    tool = tool_path(environ)
    if not tool:
        raise MetalError("no kyty_msl_dump (build it with scripts/build.sh, or set NPS5_MSL_TOOL)")
    if not os.path.isfile(programs):
        raise MetalError("no %s (prepare the cache first)" % programs)
    perms = read_perms(programs)
    env = clean_env(environ)
    kkv = find_kk_vertex(kk_msl_dir, {r["kk_vs"] for r in rows}, lens)
    os.makedirs(out_dir, exist_ok=True)
    for fn in os.listdir(out_dir):                     # a pair set of another list is never left behind
        if fn.endswith((".vs.metal", ".fs.metal")) or fn in (GFX_INDEX, "skipped-gfx.tsv"):
            os.remove(os.path.join(out_dir, fn))
    tmp = tempfile.mkdtemp(prefix=".metal-gfx-", dir=os.path.dirname(out_dir) or ".")
    prog = Progress(len(rows), label, out, what="Metal graphics")

    def one(ir):
        i, r = ir
        try:
            return r, gen_pair(r, tool, programs, perms, kkv.get(r["kk_vs"]), tmp, i, env, dump)
        finally:
            prog.step()

    try:
        with ThreadPoolExecutor(max(1, jobs)) as ex:
            res = list(ex.map(one, enumerate(rows)))
        ok, bad = [], []
        for r, (why, files) in res:
            if why:
                bad.append((r, why))
                continue
            pair = "%s_%s" % (r["kk_vs"], r["kk_fs"])
            os.replace(files[0], os.path.join(out_dir, pair + ".vs.metal"))
            os.replace(files[1], os.path.join(out_dir, pair + ".fs.metal"))
            ok.append(r)
        with open(os.path.join(out_dir, GFX_INDEX), "w") as f:
            f.write("# written by nps5 cache metal; LOCAL ONLY. list_sha=%s\n" % list_sha)
            f.write("list_sha\t%s\n" % list_sha)
            for r in ok:
                f.write("%s\t%s\t%s\t%s\n" % (r["kk_vs"], r["kk_fs"], r["vs"], r["ps"]))
        with open(os.path.join(out_dir, "skipped-gfx.tsv"), "w") as f:
            for r, why in bad:
                f.write("%s\t%s\t%s\n" % (r["kk_vs"], r["kk_fs"], why))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    reasons = {}
    for _, why in bad:
        reasons[why] = reasons.get(why, 0) + 1
    return dict(listed=len(rows), written=len(ok), skipped=len(bad), reasons=reasons, list_sha=list_sha)


def gfx_summary(st):
    s = "Metal graphics: %d of %d verified vertex+fragment pairs generated" % (st["written"], st["listed"])
    if st["reasons"]:
        s += "; skipped: " + ", ".join("%s %d" % (k, v) for k, v in sorted(st["reasons"].items()))
    return s


def gfx_index_rows(out_dir):
    """-> (list_sha, number of pairs) of the generated graphics set, (None, 0) when absent / torn."""
    try:
        with open(os.path.join(out_dir, GFX_INDEX)) as f:
            lines = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return None, 0
    sha = next((l[1] for l in lines if l[0] == "list_sha" and len(l) > 1), None)
    n = sum(1 for l in lines if l[0] != "list_sha" and len(l) >= 4
            and os.path.isfile(os.path.join(out_dir, "%s_%s.vs.metal" % (l[0], l[1]))) and os.path.isfile(os.path.join(out_dir, "%s_%s.fs.metal" % (l[0], l[1]))))
    return sha, n


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
    """-> ([(key, value)], warning or None): KK_MSL_OVERRIDE_DIR for a normal start when this cache has generated kernels / pairs for
    the current lists and the feature is on. The graphics list sits next to the compute list (games/<id>/metal-gfx-pairs.tsv)."""
    if not enabled(environ):
        return [], None
    d = os.path.join(cache_dir, OUT_NAME)
    sha, n = index_rows(d)
    gsha, gn = gfx_index_rows(d)
    if not n and not gn:
        return [], None

    def current(path):
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None
    if n and sha != current(list_path):
        return [], "the Metal shaders of this cache were made for another %s: not used; `nps5 cache metal <id>` regenerates them" % LIST_NAME
    if gn and gsha != current(os.path.join(os.path.dirname(list_path), GFX_LIST_NAME)):
        return [], "the Metal graphics pairs of this cache were made for another %s: not used; `nps5 cache metal <id>` regenerates them" % GFX_LIST_NAME
    return [("KK_MSL_OVERRIDE_DIR", d)], None
