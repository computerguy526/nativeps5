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
kk-libcache/msl, written by pass 1; a copy is kept in <cache root>.kk-vs-keep because a later prepare loads metallibs and
KosmicKrisp then writes no text), and keeps BOTH files only when both sha256 equal the list's. They go to
<cache dir>/metal-shaders/<kk_vs>_<kk_fs>.vs.metal / .fs.metal; KosmicKrisp swaps a pipeline only when its two keys match a pair.

Per GPU class (v1.2.1, keys list format 2 / pairs list format 3): a row may name an emitter MODE (the switches kyty_msl_dump applies
for that row only, `--mode`; `base` = the plain text every earlier release shipped) and a GPU CLASS (`*` = every Mac; `g16` = Apple GPU
generation 16 = M4 family, `g17` = M5 family, `g18` = M6 family, optionally `@os26` = only on macOS 26). Several rows may name one KosmicKrisp key for
different classes. Per key this module takes the most specific row that matches THIS Mac (generation + OS > generation > OS > `*`),
falls back to the next matching row when this Mac does not reproduce its text, and to KosmicKrisp's own shader when none is left.
Mode `kk` = "KosmicKrisp's shader on this class" (a variant that was measured slower there). Rows are only listed for a class
where the text was verified and measured faster on that class (verdict and B/A in the row). Lists of format 1 / 2 have no class
column: every row is `*` + `base`, i.e. exactly the v1.2 behaviour.

Switches: NPS5_METAL_SHADERS=0 turns the feature off (no generation, no KK_MSL_OVERRIDE_DIR at launch).
          NPS5_METAL_CANON (default ON since v1.2.2, oskey; =0 turns it off) = KK_MSL_CANON_KEY=1 in prepare and launch: KosmicKrisp's MSL text, so its
          override key, differs between macOS 26 and 27 and between GPU families (workaround sets, texture lowering). With the switch
          KosmicKrisp also keys every shader by the text a macOS 26 / Apple GPU family 9 Mac writes (the Mac the lists' keys come
          from) and looks a replacement up under that key when none exists under this Mac's own key; the keys in the lists are those
          canonical keys. Prepare then finds the Mac's own vertex text through the `canon=` field of KosmicKrisp's log line.
          NPS5_METAL_CLASS=<g16[@os26]> overrides the detected GPU class, =none uses only the `*` rows: honoured ONLY with
          NPS5_DEV=1 (developer/tests; the summary says "forced ... this Mac is ..."), ignored with a note otherwise.
          NPS5_MSL_TOOL = the kyty_msl_dump binary (default <NPS5_KYTY_DIR>/build-arm64/kyty_msl_dump).
Generated files never leave NPS5_SCRATCH (git-ignored); never commit or upload them."""
import hashlib, os, re, shutil, struct, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

FORMAT = ("nps5-metal-keys", "1")
KEYS_FORMATS = ("1", "2")  # 2 (v1.2.1) adds mode, class, rule, verdict and B/A after the evidence column
LIST_NAME = "metal-shaders.tsv"
OUT_NAME = "metal-shaders"
GFX_LIST_NAME = "metal-gfx-pairs.tsv"
GFX_INDEX = "index-gfx.tsv"
GFX_FORMAT = ("nps5-metal-gfx-pairs", "2")
GFX_FORMATS = ("1", "2", "3")   # 2 (v1.1.1) adds the blend column before the evidence: KosmicKrisp blends in the fragment shader;
                                # 3 (v1.2.1) adds mode, class, rule, verdict and B/A after the evidence column (as keys format 2)
GFX_COLS = ("vs", "vs_spec", "ps", "ps_spec", "kk_vs", "kk_fs", "b_vs", "b_fs", "variant", "set0", "va", "cmask")
_GFX_HEAD = (r"^gfx\t([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{16})\t([0-9a-f]{16})\t([0-9a-f]{64})\t([0-9a-f]{64})"
             r"\t(mipstats|twin|mesh|mesh-twin)\t([0-9:,]*)\t([0-9:,]*)\t(\d+)")
# format 2: a spec field may carry the record's push-data cursor, <spec key>:<cursor> (see read_perms)
_GFX_HEAD2 = _GFX_HEAD.replace(r"\t([0-9a-f]{16})\t([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{16})", r"\t([0-9a-f]{16}(?::\d{1,4})?)\t([0-9a-f]{32}|-)\t([0-9a-f]{16}(?::\d{1,4})?|-)\t([0-9a-f]{16})", 1)
# format 2/3, VS-only pipeline (no fragment shader, e.g. depth-only shadow passes): ps and ps_spec are '-'. KosmicKrisp then adds its own
# empty fragment shader (KK_EMPTY_FS, keyed kk_fs); the pair is the emitter's vertex text + that fragment text verbatim.
KK_EMPTY_FS = ("// Generated by Mesa compiler\n#include <metal_stdlib>\nusing namespace metal;\nstruct FragmentIn {\n};\nstruct FragmentOut {\n};\n"
               "struct Buffer {\n    uint64_t contents[1];\n};\nstruct SamplerTable {\n    sampler handles[4096];\n};\n"
               "fragment FragmentOut main_entrypoint(\n    FragmentIn in [[stage_in]],\n    constant Buffer &buf0 [[buffer(0)]],\n"
               "    constant SamplerTable &sampler_table [[buffer(1)]]\n)\n{\n    FragmentOut out = {};\n    return out;\n}\n")
KK_EMPTY_FS_HEADER = "// kk-mslinject v1 stage fragment entry main_entrypoint math fast\n"   # KK's own library options: math=2
GFX_ROW_RE = re.compile(_GFX_HEAD + r"\t([a-z0-9_+,.-]*)$")                        # format 1
_BLEND_RT = r"(?:\d{1,3}:){8}[fusix]:\d"                                        # en:sc:dc:co:sa:da:ao:wm:class:components
GFX_ROW2_RE = re.compile(_GFX_HEAD2 + r"\t((?:%s(?:;%s){0,7})?)\t([a-z0-9_+,.-]*)$" % (_BLEND_RT, _BLEND_RT))       # format 2: ... cmask blend evidence
ROW_RE = re.compile(r"^([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{16})\t([0-9a-f]{64})\t(\d+x\d+x\d+)\t([a-z0-9_,]*)$")
# per-class columns (keys format 2, pairs format 3): mode, class, rule, verdict, B/A. Hashes and short words only.
MODE_PAT = r"(?:base|kk|[a-z][a-z0-9_]{0,15}(?:=[0-9a-z]{1,8})?(?:\+[a-z][a-z0-9_]{0,15}(?:=[0-9a-z]{1,8})?){0,7})"
CLASS_PAT = r"(?:\*|g\d{2}[a-z]?(?:@os\d{2})?|@os\d{2})"
RULE_PAT = r"[a-z0-9][a-z0-9:=_.,-]{0,47}"
VERDICT_PAT = r"(?:faster|shipped|slower)"
BA_PAT = r"(?:-|\d\.\d{1,4})"
CLASS_COLS = ("mode", "cls", "rule", "verdict", "ba")
_CLASS_TAIL = r"\t(%s)\t(%s)\t(%s)\t(%s)\t(%s)$" % (MODE_PAT, CLASS_PAT, RULE_PAT, VERDICT_PAT, BA_PAT)
ROW2_RE = re.compile(r"^([0-9a-f]{32})\t([0-9a-f]{16})\t([0-9a-f]{16})\t([0-9a-f]{64}|-)\t(\d+x\d+x\d+)\t([a-z0-9_,.+-]*)" + _CLASS_TAIL)
CLASS_DEFAULTS = dict(mode="base", cls="*", rule="exact", verdict="shipped", ba="-")
GFX_ROW3_RE = re.compile(_GFX_HEAD2.replace(r"\t([0-9a-f]{64})\t([0-9a-f]{64})", r"\t([0-9a-f]{64}|-)\t([0-9a-f]{64}|-)", 1)
                         + r"\t((?:%s(?:;%s){0,7})?)\t([a-z0-9_+,.-]*)" % (_BLEND_RT, _BLEND_RT) + _CLASS_TAIL)       # format 3: ... evidence mode class rule verdict ba
HEADER = "// kk-mslinject v1 local_size "
KPRC = 0x4352504B
SEEN_RE = re.compile(r"\[kk-mslinject\] seen ([0-9a-f]{16})")
OVERRIDE_RE = re.compile(r"\[kk-mslinject\] override ([0-9a-f]{16}) (local_size|FAILED)")


class MetalError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg); self.code = code


def canon_enabled(environ):
    """Default ON (v1.2.2); NPS5_METAL_CANON=0 (off/no/false) turns it off: KosmicKrisp looks replacements up under the canonical
    (macOS 26 / GPU family 9) key as well as this Mac's own. A no-op on a Mac whose text is the canonical one."""
    return (environ.get("NPS5_METAL_CANON") or "1").strip().lower() not in ("0", "off", "no", "false")


def enabled(environ):
    return (environ.get("NPS5_METAL_SHADERS") or "1").strip() not in ("0", "off", "no", "false")


# ---------------------------------------------------------------- the keys list (pure)
def _row_rules(r, where):
    """Per-class row consistency: mode kk carries no text and verdict slower; a text row never says slower."""
    texts = [r[k] for k in ("b", "b_vs", "b_fs") if k in r]
    if (r["mode"] == "kk") != all(t == "-" for t in texts) or any(t == "-" for t in texts) and r["mode"] != "kk":
        raise MetalError("%s: mode kk (KosmicKrisp's shader on this class) and only it has no text hash" % where, 2)
    if (r["mode"] == "kk") != (r["verdict"] == "slower"):
        raise MetalError("%s: verdict slower goes with mode kk only" % where, 2)


def parse_list(text):
    """-> (meta dict, rows). Rows: dict(program, spec, kk, b, local, evidence, mode, cls, rule, verdict, ba). Raises MetalError on any
    malformed line, so a corrupted list never generates anything. Format 1 rows are mode base, class * (every Mac)."""
    meta, rows, seen = {}, [], {}
    fmt = next((ln.split("\t") for ln in text.splitlines() if ln.startswith("format\t")), [])
    v2 = len(fmt) > 2 and fmt[1] == FORMAT[0] and fmt[2] == "2"
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if f[0] in ("format", "title", "emitter"):
            meta[f[0]] = tuple(f[1:])
            continue
        m = (ROW2_RE if v2 else ROW_RE).match(line)
        if not m:
            raise MetalError("%s line %d: not a key row (32 hex, 16 hex, 16 hex, 64 hex, XxYxZ, evidence%s)"
                             % (LIST_NAME, n, ", mode, class, rule, verdict, B/A" if v2 else ""), 2)
        g = m.groups()
        r = dict(zip(("program", "spec", "kk", "b", "local", "evidence"), g[:6]), **(dict(zip(CLASS_COLS, g[6:])) if v2 else CLASS_DEFAULTS))
        _row_rules(r, "%s line %d" % (LIST_NAME, n))
        # two permutations may share one kernel, never with different texts for one class
        if seen.setdefault((r["kk"], r["cls"], r["mode"] == "kk"), (r["b"], r["mode"])) != (r["b"], r["mode"]):
            raise MetalError("%s line %d: kk_msl %s listed twice for class %s with different texts" % (LIST_NAME, n, r["kk"], r["cls"]), 2)
        rows.append(r)
    if meta.get("format", ())[:1] != FORMAT[:1] or meta.get("format", ("", ""))[1:2] not in [(x,) for x in KEYS_FORMATS]:
        raise MetalError("%s: missing or unknown `format` line (want %s %s)" % (LIST_NAME, FORMAT[0], " or ".join(KEYS_FORMATS)), 2)
    return meta, rows


# ---------------------------------------------------------------- GPU class of this Mac, and the row choice per key (pure)
_IOCLASS_RE = re.compile(r'"IOClass" = "AGXAccelerator(G\d{2})([A-Z]?)"')
_ARCH_RE = re.compile(r"^(?:applegpu_)?(g\d{2})([a-z]?)$")
CLASS_SPEC_RE = re.compile(r"^(?:(g\d{2})([a-z]?))?(?:@os(\d{2}))?$")


def _run_out(argv, timeout=10):
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_device(spec):
    """'g16', 'g16s@os26', 'applegpu_g18g', 'none' -> dict(gen, var, os) or None (none / unparsable)."""
    spec = (spec or "").strip().lower()
    a, _, o = spec.partition("@")
    m = _ARCH_RE.match(a) if a else None
    if spec in ("", "none", "off") or (a and not m) or (o and not re.match(r"^os\d{2}$", o)):
        return None
    return dict(gen=m.group(1) if m else "", var=m.group(2) if m else "", os=o[2:] if o else "")


def device_class(environ=None, run=_run_out):
    """-> dict(gen 'g16', var '' or the Metal architecture letter, os '26', model) of this Mac, or None (unknown / NPS5_METAL_CLASS=none:
    only the `*` rows apply). The GPU generation comes from the IORegistry (AGXAcceleratorG16X = generation 16: M4 family; G18G: M6),
    the same number as MTLDevice.architecture.name (applegpu_g16s). No Metal device is opened. The OS major from sw_vers' product
    version (sysctl kern.osproductversion). NPS5_GPU_ARCH=applegpu_g16s adds the architecture letter (rows like g16s).
    NPS5_METAL_CLASS=<class>|none forces a class ONLY with NPS5_DEV=1 (tests, the developer); otherwise it is ignored and the
    dict carries a `note` saying so (printed in the summary)."""
    environ = os.environ if environ is None else environ
    o = environ.get("NPS5_METAL_CLASS")
    if o is not None:
        here = device_class({k: v for k, v in environ.items() if k != "NPS5_METAL_CLASS"}, run)
        if (environ.get("NPS5_DEV") or "").strip() not in ("1", "yes", "on", "true"):
            # review M3: a user install never takes another GPU's texts (verified and timed only there) from a stray variable
            return dict(here, note="NPS5_METAL_CLASS ignored (developer switch: needs NPS5_DEV=1)") if here else None
        d = parse_device(o)
        return dict(d, model="NPS5_METAL_CLASS", note="forced by NPS5_METAL_CLASS; this Mac is %s" % class_name(here)) if d else None
    m = _IOCLASS_RE.search(run(["/usr/sbin/ioreg", "-r", "-c", "AGXAccelerator", "-d", "1"]))
    if not m:
        return None
    mm = re.search(r'"model" = "([^"]{1,40})"', run(["/usr/sbin/ioreg", "-r", "-c", "AGXAccelerator", "-d", "1"]))
    osv = run(["/usr/sbin/sysctl", "-n", "kern.osproductversion"]).strip()
    am = _ARCH_RE.match((environ.get("NPS5_GPU_ARCH") or "").strip().lower())
    var = am.group(2) if am and am.group(1) == m.group(1).lower() else ""
    return dict(gen=m.group(1).lower(), var=var, os=osv.split(".")[0] if re.match(r"^\d{2}\b", osv) else "", model=mm.group(1) if mm else "")


def class_name(dev):
    """dict -> 'g16@os26' (the string written to the index and printed), 'none' for None."""
    if not dev:
        return "none"
    return "%s%s%s" % (dev["gen"], dev["var"], "@os" + dev["os"] if dev["os"] else "")


def class_score(spec, dev):
    """How specifically a row's class spec matches this Mac: None = not at all, 0 = `*`, else generation 4 + letter 2 + OS 1."""
    if spec == "*":
        return 0
    m = CLASS_SPEC_RE.match(spec)
    if not m or not dev:
        return None
    gen, var, osm = m.groups()
    if gen and gen != dev["gen"] or var and var != dev["var"] or osm and osm != dev["os"]:
        return None
    return (4 if gen else 0) + (2 if var else 0) + (1 if osm else 0)


def choose(rows, dev, key):
    """-> {key: [option, ...]} where an option is the list of rows (permutations) of one (key, class, text-or-kk) and the options are
    ordered best first: most specific matching class; within one class the text row before the class's kk row (review L8: a class
    text that does not reproduce here falls back to KosmicKrisp when the `*` text was measured slower on this class, not to `*`);
    then list order. Rows of a class that does not match this Mac are left out; a key with no matching row is absent (KK)."""
    opts = {}
    for i, r in enumerate(rows):
        sc = class_score(r["cls"], dev)
        if sc is None:
            continue
        kk = r["mode"] == "kk"
        o = opts.setdefault(key(r), {}).setdefault((r["cls"], kk), [sc, kk, i, []])
        o[3].append(r)
    return {k: [o[3] for o in sorted(v.values(), key=lambda o: (-o[0], o[1], o[2]))] for k, v in opts.items()}


def has_classes(rows):
    return any(r["cls"] != "*" or r["mode"] != "base" for r in rows)


def spec_key(spec):
    return hashlib.sha256(spec).hexdigest()[:16]


def read_perms(path, cursors=False):
    """Kyty's program cache (<id>.programs.bin): -> {(program digest hex, spec key): permutation index}. The index counts every
    record that is not a source record (kind 1), as kyty_msl_dump --perm does. The first record of a key wins.
    cursors=True also keys (digest, spec key, push-data cursor): records of one program and specialization can differ only in the cursor (u32
    at record offset 16, where the stage's user data starts in the push block), which changes a graphics stage's text."""
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
                    k = (d[p:p + 16].hex(), spec_key(d[p + 24:p + 24 + sl]))
                    out.setdefault(k, idx)
                    if cursors:
                        out.setdefault(k + (struct.unpack_from("<I", d, p + 16)[0],), idx)
            idx += 1
        off = p + size
    return out


CANON_RE = re.compile(r"\[kk-mslinject\] seen ([0-9a-f]{16}) len=\d+ (compute|vertex|fragment) canon=([0-9a-f]{16})")


def seen_keys(text):
    """Keys KosmicKrisp logged as seen: its own, plus (KK_MSL_CANON_KEY) the canonical key of each."""
    return set(SEEN_RE.findall(text)) | {c for _, _, c in CANON_RE.findall(text)}


def key_match_line(log, kernels, pairs):
    """-> one summary line: how many listed compute keys / (vs, fs) pairs KosmicKrisp produced on this Mac, by its own key or (with
    KK_MSL_CANON_KEY) by the canonical key. kernels = {kk key}, pairs = {(kk_vs, kk_fs)}; '' when the log has no `seen` lines."""
    own = set(SEEN_RE.findall(log))
    anyk = own | {c for _, _, c in CANON_RE.findall(log)}
    if not anyk:
        return ""
    k_all, k_own = len(kernels & anyk), len(kernels & own)
    p_all = sum(1 for v, f in pairs if v in anyk and f in anyk)
    p_own = sum(1 for v, f in pairs if v in own and f in own)
    s = "%d of %d listed kernels and %d of %d listed pairs match on this Mac" % (k_all, len(kernels), p_all, len(pairs))
    if k_all + p_all:
        s += " (%d + %d by this Mac's own key, the rest by the canonical key)" % (k_own, p_own)
    else:
        s += ": the replacements are inactive here (KosmicKrisp's text differs from the one the lists were made for)"
    return s


def seen_canon_aliases(text):
    """-> {canonical key: this Mac's own key} of the shaders KosmicKrisp logged with KK_MSL_CANON_KEY=1 (vertex and fragment)."""
    return {c: r for r, _, c in CANON_RE.findall(text)}


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


def mode_args(mode):
    """kyty_msl_dump arguments of a row's emitter mode: none for `base` (the v1.2 command line, byte for byte)."""
    return [] if mode in ("base", "", None) else ["--mode", mode]


def run_dump(tool, programs, perm, out, env, mode="base"):
    r = subprocess.run([tool, "--programs", programs, "--perm", str(perm), "--kk-bda", "--no-comments"] + mode_args(mode) + ["--out", out],
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


def generate(list_path, programs, out_dir, environ, jobs=4, seen=None, dump=run_dump, label="", out=None, dev=False):
    """Write out_dir (atomically replaced) with <kk>.metal per accepted key + index.tsv + skipped.tsv. Per key the best row for this
    Mac's GPU class (choose(); dev=False detects it, dev=None = class unknown: `*` rows only) whose text this Mac reproduces.
    -> dict(listed, written, skipped, seen (None if unknown), reasons {reason: count}, cls, kk_class (keys left to KosmicKrisp by a
    kk row), fallback (keys that took a less specific row))."""
    out = out or sys.stdout
    with open(list_path) as f:
        text = f.read()
    meta, rows = parse_list(text)
    if not has_classes(rows):                           # a v1.2 list (no class columns): the v1.2 code path, verbatim (review M2)
        return dict(_generate_v12(list_path, programs, out_dir, environ, jobs, seen, dump, label, out), cls=None)
    list_sha = hashlib.sha256(text.encode()).hexdigest()
    tool = tool_path(environ)
    if not tool:
        raise MetalError("no kyty_msl_dump (build it with scripts/build.sh, or set NPS5_MSL_TOOL)")
    if not os.path.isfile(programs):
        raise MetalError("no %s (prepare the cache first)" % programs)
    classes = True
    if dev is False:
        dev = device_class(environ) if classes else None
    keys = choose(rows, dev, lambda r: r["kk"])
    perms = read_perms(programs)
    env = clean_env(environ)
    parent = os.path.dirname(out_dir)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".metal-new-", dir=parent)
    prog = Progress(len(keys), label, out)
    reused = [0]

    def try_row(i, r):
        idx = perms.get((r["program"], r["spec"]))
        if idx is None:
            return "not in programs.bin"
        dst = os.path.join(tmp, r["kk"] + ".metal")
        old = os.path.join(out_dir, r["kk"] + ".metal")      # prepfast: text already generated for this key (sha256 = the verified one)
        if os.path.isfile(old) and file_sha(old) == r["b"]:
            try:
                os.link(old, dst)
            except FileExistsError:
                pass
            except OSError:
                shutil.copyfile(old, dst)
            reused[0] += 1
            return None
        part = "%s.%d.part" % (dst, i)
        try:
            rc, err = dump(tool, programs, idx, part, env) if r["mode"] == "base" else dump(tool, programs, idx, part, env, mode=r["mode"])
        except (OSError, subprocess.SubprocessError) as e:
            return "dump failed: %s" % type(e).__name__
        m = re.search(r"// complete=(\d)", err or "")
        if rc != 0 or not os.path.isfile(part):
            return "dump failed rc=%s" % rc
        if m and m.group(1) != "1":
            return "incomplete translation"
        with open(part, "rb") as f:
            head = f.read(len(HEADER))
        if head != HEADER.encode():
            return "no kk-mslinject header"
        if file_sha(part) != r["b"]:
            return "text differs from the verified kernel"
        os.replace(part, dst)
        return None

    def one(ik):
        i, k = ik
        tried = []
        try:
            for opt in keys[k]:
                if opt[0]["mode"] == "kk":                  # measured slower on this class: KosmicKrisp's own kernel
                    return k, None, "kk", tried
                why = None
                for j, r in enumerate(opt):                 # permutations of one kernel: any one reproduces it
                    why = try_row(i * 64 + j, r)
                    if why is None:
                        return k, r, None, tried
                tried.append((opt[0], why))
            return k, None, tried[0][1] if tried else "no row", tried
        finally:
            prog.step()

    try:
        with ThreadPoolExecutor(max(1, jobs)) as ex:
            res = list(ex.map(one, enumerate(keys)))
        for fn in os.listdir(tmp):
            if fn.endswith(".part"):
                os.remove(os.path.join(tmp, fn))
        ok = [r for _, r, why, _ in res if r is not None]
        bad = [(keys[k][0][0], why) for k, r, why, _ in res if r is None and why != "kk"]
        kkc = [k for k, r, why, _ in res if why == "kk"]
        fb = [k for k, r, why, tried in res if r is not None and tried]
        with open(os.path.join(tmp, "index.tsv"), "w") as f:
            f.write("# written by nps5 cache metal; LOCAL ONLY (generated from your dump). list_sha=%s\n" % list_sha)
            f.write("list_sha\t%s\n" % list_sha)
            if classes:
                f.write("class\t%s\n" % class_name(dev))
            for r in ok:
                f.write("%s\t%s\t%s\t%s%s\n" % (r["kk"], r["program"], r["spec"], r["local"], "\t%s\t%s" % (r["mode"], r["cls"]) if classes else ""))
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
    return dict(listed=len({r["kk"] for r in rows}), written=len(ok), skipped=len(bad), seen=nseen, reasons=reasons, list_sha=list_sha, reused=reused[0],
                emitter=meta.get("emitter", ()), cls=class_name(dev) if classes else None, kk_class=len(kkc), fallback=len(fb),
                cls_note=(dev or {}).get("note"),
                other_class=len({r["kk"] for r in rows}) - len(keys))


# ---- v1.2 code paths, verbatim from v1.2-dev 7b538c4 (lists without class columns must generate and index byte-identically)
def _generate_v12(list_path, programs, out_dir, environ, jobs=4, seen=None, dump=run_dump, label="", out=None):
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
    reused = [0]

    def one(ir):
        i, r = ir
        try:
            idx = perms.get((r["program"], r["spec"]))
            if idx is None:
                return r, "not in programs.bin"
            dst = os.path.join(tmp, r["kk"] + ".metal")
            old = os.path.join(out_dir, r["kk"] + ".metal")      # prepfast: text already generated for this key (sha256 = the verified one)
            if os.path.isfile(old) and file_sha(old) == r["b"]:
                try:
                    os.link(old, dst)
                except FileExistsError:                  # a second permutation of the same kernel
                    pass
                except OSError:
                    shutil.copyfile(old, dst)
                reused[0] += 1
                return r, None
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
    return dict(listed=len({r["kk"] for r in rows}), written=len(ok), skipped=len(bad), seen=nseen, reasons=reasons, list_sha=list_sha, reused=reused[0],
                emitter=meta.get("emitter", ()))


def summary(st):
    s = "Metal shaders: %d of %d verified kernels generated" % (st["written"], st["listed"])
    if st["reasons"]:
        s += "; skipped: " + ", ".join("%s %d" % (k, v) for k, v in sorted(st["reasons"].items()))
    if st.get("seen") is not None:
        s += "; KosmicKrisp built %d of the listed kernels in pass 1" % st["seen"]
    if st.get("reused"):
        s += "; %d unchanged from an earlier prepare" % st["reused"]
    return s + class_summary(st)


def class_summary(st):
    """'' for a list without classes (the v1.2 summary, unchanged)."""
    if not st.get("cls"):
        return ""
    s = "; GPU class %s" % st["cls"] + (" (%s)" % st["cls_note"] if st.get("cls_note") else "")
    for k, what in (("other_class", "listed for other GPUs only"), ("kk_class", "KosmicKrisp's on this GPU (measured slower here)"),
                    ("fallback", "took a less specific row")):
        if st.get(k):
            s += ", %d %s" % (st[k], what)
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
    """-> (meta dict, rows). Row: dict(vs, vs_spec, ps, ps_spec, kk_vs, kk_fs, b_vs, b_fs, variant, set0, va, cmask, blend, evidence)
    + mode, cls, rule, verdict, ba (format 3; base / * / exact / shipped / - before). (blend is '' in a format-1 list). Raises MetalError on a malformed line or on one (kk_vs, kk_fs) pair listed with different texts."""
    meta, rows, seen = {}, [], {}
    fmt = next((ln.split("\t") for ln in text.splitlines() if ln.startswith("format\t")), [])
    if fmt[1:2] != [GFX_FORMAT[0]] or len(fmt) < 3 or fmt[2] not in GFX_FORMATS:
        raise MetalError("%s: missing or unknown `format` line (want %s %s)" % (GFX_LIST_NAME, GFX_FORMAT[0], " or ".join(GFX_FORMATS)), 2)
    v2, v3 = fmt[2] in ("2", "3"), fmt[2] == "3"
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if f[0] in ("format", "title", "emitter"):
            meta[f[0]] = tuple(f[1:])
            continue
        m = (GFX_ROW3_RE if v3 else GFX_ROW2_RE if v2 else GFX_ROW_RE).match(line)
        if not m:
            raise MetalError("%s line %d: not a pair row" % (GFX_LIST_NAME, n), 2)
        g = m.groups()
        if v3:
            r = dict(zip(GFX_COLS, g[:12]), blend=g[12], evidence=g[13], **dict(zip(CLASS_COLS, g[14:])))
        else:
            r = dict(zip(GFX_COLS, g[:12]), blend=g[12] if v2 else "", evidence=g[-1], **CLASS_DEFAULTS)
        _row_rules(r, "%s line %d" % (GFX_LIST_NAME, n))
        k = (r["kk_vs"], r["kk_fs"], r["cls"], r["mode"] == "kk")
        if seen.setdefault(k, (r["b_vs"], r["b_fs"], r["mode"])) != (r["b_vs"], r["b_fs"], r["mode"]):
            raise MetalError("%s line %d: pair %s_%s listed twice for class %s with different texts" % (GFX_LIST_NAME, n, r["kk_vs"], r["kk_fs"], r["cls"]), 2)
        rows.append(r)
    return meta, rows


SEEN_LEN_RE = re.compile(r"\[kk-mslinject\] seen ([0-9a-f]{16}) len=(\d+) vertex")


def seen_vertex_lens(text):
    """-> {key: byte length} of the vertex shaders KosmicKrisp logged in a run (KK_MSL_OVERRIDE_LOG=1)."""
    return {k: int(n) for k, n in SEEN_LEN_RE.findall(text)}


def find_kk_vertex(kk_msl_dir, wanted, lens=None, alias=None):
    """-> {key: path}: the files of KosmicKrisp's library cache (named by its own cache key) whose text hashes to a wanted override key.
    A logged length narrows the candidates to files of that size; without a log every vertex text is hashed (BLAKE3 in pure Python:
    slow, a minute or two on a big cache)."""
    if alias:                                          # canonical key -> this Mac's key: search by the latter, answer by the former
        back = {alias.get(k, k): k for k in wanted}
        found = find_kk_vertex(kk_msl_dir, set(back), {alias.get(k, k): n for k, n in (lens or {}).items()} if lens else lens)
        return {back[k]: v for k, v in found.items()}
    lens = lens or {}
    try:
        files = [(fn, os.path.getsize(os.path.join(kk_msl_dir, fn))) for fn in os.listdir(kk_msl_dir) if fn.endswith(".metal")]
    except OSError:
        return {}
    b3sum = shutil.which("b3sum")
    found = {}
    wanted = set(wanted)
    # prepfast: ONE pass over the files, each text hashed at most once (was: every wanted key re-read and re-hashed every candidate,
    # O(keys x files) full-file reads: 150 pairs x 1,583 files of up to 5 MB each; 5x the shader set = 25x the work)
    sizes = {lens[k] for k in wanted if lens.get(k) is not None}
    unsized = any(lens.get(k) is None for k in wanted)
    for fn, size in sorted(files):
        if len(found) == len(wanted):
            break
        path = os.path.join(kk_msl_dir, fn)
        try:
            with open(path, "rb") as f:
                if size not in sizes:                  # a file of a logged length is always a candidate; else only vertex texts
                    if not unsized or b"struct VertexOut" not in f.read(4096):
                        continue
                    f.seek(0)
                data = f.read()
        except OSError:
            continue
        if b3sum:
            h = subprocess.run([b3sum, "--no-names"], input=data, capture_output=True).stdout.decode()[:16]
        else:
            h = blake3_64(data)
        if h in wanted and h not in found:
            found[h] = path
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
    if r["variant"] in ("twin", "mesh-twin"):
        e["KYTY_MSL_MIPSTATS_PLAIN"] = "1"
    if is_mesh(r):                                     # meshpath: the vertex half is Kyty's mesh program ([[mesh]] text)
        e["KYTY_MSL_MESH"] = "1"
    if r.get("blend"):                                 # KosmicKrisp's in-shader blend + write mask per colour attachment (format 2)
        e["KYTY_MSL_KK_BLEND"] = r["blend"]
    return e


MESH_ROWS_ENV = "NPS5_MESH_ROWS"


def is_mesh(r):
    """meshpath: a mesh row (variant 'mesh' / 'mesh-twin'): a KosmicKrisp mesh pipeline, replacement files <ms>_<fs>.ms.metal + .fs.metal
    (KosmicKrisp needs KK_MSL_OVERRIDE_MESH=1, patch 0029; mode token meshspill also needs patch 0030). On by default since v1.2.3;
    NPS5_MESH_ROWS=0 skips them."""
    return r.get("variant", "").startswith("mesh")


def first_ext(r):
    return ".ms.metal" if is_mesh(r) else ".vs.metal"


def perm_key(digest, spec):
    """A row's (digest, spec[:cursor]) -> the read_perms key."""
    s, _, c = spec.partition(":")
    return (digest, s, int(c)) if c else (digest, s)


def gen_pair(r, tool, programs, perms, kk_vs_path, tmp, i, env, dump=run_dump_gfx):
    """-> (None, (vs_part, fs_part)) when both texts equal the verified ones, else (reason, None)."""
    if r.get("mode", "base") != "base":                 # the row's emitter mode reaches both stages (and only through argv)
        inner = dump

        def dump(tool, programs, perm, out, env, extra=()):
            return inner(tool, programs, perm, out, env, tuple(mode_args(r["mode"])) + tuple(extra))
    vs_only = r["ps"] == "-"
    mesh = is_mesh(r)
    if mesh and os.environ.get(MESH_ROWS_ENV) == "0":
        return "mesh rows off (%s=0)" % MESH_ROWS_ENV, None
    vi = perms.get(perm_key(r["vs"], r["vs_spec"]))
    pi = None if vs_only else perms.get(perm_key(r["ps"], r["ps_spec"]))
    if vi is None or (pi is None and not vs_only):
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
        if vs_only:                                    # KosmicKrisp's own empty fragment shader; the vertex shader writes no varyings
            if blake3_64(KK_EMPTY_FS.encode()) != r["kk_fs"]:
                return "KosmicKrisp fragment shader is not its empty one", None
            pt = KK_EMPTY_FS_HEADER + KK_EMPTY_FS
            with open(ps_out, "w") as f:
                f.write(pt)
            e["KYTY_MSL_KK_PS_READS"] = ""
        else:
            rc, err = dump(tool, programs, pi, ps_out, e)
            m = re.search(r"// complete=(\d)", err or "")
            if rc != 0 or not os.path.isfile(ps_out) or (m and m.group(1) != "1"):
                return "fragment translation failed", None
            with open(ps_out, errors="replace") as f:
                pt = f.read()
            mr = re.search(r"^// kk-bda ps_reads(.*)$", pt, re.M)
            if mr:
                e["KYTY_MSL_KK_PS_READS"] = ",".join(mr.group(1).split())
        rc, err = dump(tool, programs, vi, vs_out, e, () if vs_only or mesh else ("--link-ps", str(pi)))
        m = re.search(r"// complete=(\d)", err or "")
        if rc != 0 or not os.path.isfile(vs_out) or (m and m.group(1) != "1"):
            return "vertex translation failed", None
    except (OSError, subprocess.SubprocessError) as ex:
        return "dump failed: %s" % type(ex).__name__, None
    with open(vs_out, errors="replace") as f:
        vt = f.read()
    if not mesh and (len(kk_slot_set(kt)) != vt.count("const ulong kk_vb") or slot_types(kt) != slot_types(vt)):
        return "vertex attribute layout differs from KosmicKrisp's", None
    if not vt.startswith("// kk-mslinject v1 stage mesh" if mesh else "// kk-mslinject v1 stage vertex") or not pt.startswith("// kk-mslinject v1 stage fragment"):
        return "no kk-mslinject header", None
    if file_sha(vs_out) != r["b_vs"] or file_sha(ps_out) != r["b_fs"]:
        return "text differs from the verified pair", None
    return None, (vs_out, ps_out)


KK_VS_KEEP = "kk-vs-keep"


def generate_gfx(list_path, programs, kk_msl_dir, out_dir, environ, jobs=4, dump=run_dump_gfx, label="", out=None, lens=None, keep_dir=None, dev=False, alias=None):
    """Add <kk_vs>_<kk_fs>.vs.metal/.fs.metal to out_dir (created if needed; call after generate(), which replaces the directory)
    plus index-gfx.tsv/skipped-gfx.tsv. -> dict(listed, written, skipped, reasons, list_sha)."""
    out = out or sys.stdout
    with open(list_path) as f:
        text = f.read()
    meta, rows = parse_gfx_list(text)
    if not has_classes(rows):                           # a v1.2 list: the v1.2 code path, verbatim (review M2)
        return dict(_generate_gfx_v12(list_path, programs, kk_msl_dir, out_dir, environ, jobs, dump, label, out, lens, keep_dir), cls=None)
    list_sha = hashlib.sha256(text.encode()).hexdigest()
    tool = tool_path(environ)
    if not tool:
        raise MetalError("no kyty_msl_dump (build it with scripts/build.sh, or set NPS5_MSL_TOOL)")
    if not os.path.isfile(programs):
        raise MetalError("no %s (prepare the cache first)" % programs)
    perms = read_perms(programs, cursors=True)
    env = clean_env(environ)
    classes = has_classes(rows)
    if dev is False:
        dev = device_class(environ) if classes else None
    # KosmicKrisp writes its vertex MSL to kk-libcache/msl only when it compiles a shader; a later prepare LOADS the metallibs, so
    # the texts are gone and every pair would be dropped. Keep a copy of each needed vertex text in keep_dir (prepare: next to the
    # metallib store, it outlives the cache dir; default <out_dir>/../kk-vs-keep) as <kk key>.metal. The key is KosmicKrisp's
    # BLAKE3 of the text, so the store is content-addressed; the sha256 check of our texts still decides.
    keep = keep_dir or os.path.join(os.path.dirname(os.path.abspath(out_dir)), KK_VS_KEEP)
    wanted = {r["kk_vs"] for r in rows if r["mode"] != "kk"}
    kkv = {k: os.path.join(keep, k + ".metal") for k in wanted if os.path.isfile(os.path.join(keep, k + ".metal"))}
    found = find_kk_vertex(kk_msl_dir, wanted - set(kkv), lens, alias) if wanted - set(kkv) else {}
    if found:
        os.makedirs(keep, exist_ok=True)
        for k, src in found.items():
            dst = os.path.join(keep, k + ".metal")
            shutil.copyfile(src, dst + ".tmp")
            os.replace(dst + ".tmp", dst)
            kkv[k] = dst
    os.makedirs(out_dir, exist_ok=True)
    for fn in os.listdir(out_dir):                     # a pair set of another list is never left behind
        if fn.endswith((".vs.metal", ".ms.metal", ".fs.metal")) or fn in (GFX_INDEX, "skipped-gfx.tsv"):
            os.remove(os.path.join(out_dir, fn))
    tmp = tempfile.mkdtemp(prefix=".metal-gfx-", dir=os.path.dirname(out_dir) or ".")
    pairs = choose(rows, dev, lambda r: (r["kk_vs"], r["kk_fs"]))
    prog = Progress(len(pairs), label, out, what="Metal graphics")

    def one(ik):
        i, k = ik
        tried = []
        try:
            for opt in pairs[k]:
                if opt[0]["mode"] == "kk":
                    return k, None, "kk", None, tried
                why = None
                for j, r in enumerate(opt):
                    why, files = gen_pair(r, tool, programs, perms, kkv.get(r["kk_vs"]), tmp, i * 64 + j, env, dump)
                    if not why:
                        return k, r, None, files, tried
                tried.append((opt[0], why))
            return k, None, tried[0][1] if tried else "no row", None, tried
        finally:
            prog.step()

    try:
        with ThreadPoolExecutor(max(1, jobs)) as ex:
            res = list(ex.map(one, enumerate(pairs)))
        ok, bad, kkc, fb = [], [], 0, 0
        for k, r, why, files, tried in res:
            if why == "kk":
                kkc += 1
                continue
            if why:
                bad.append((pairs[k][0][0], why))
                continue
            fb += 1 if tried else 0
            pair = "%s_%s" % (r["kk_vs"], r["kk_fs"])
            os.replace(files[0], os.path.join(out_dir, pair + first_ext(r)))
            os.replace(files[1], os.path.join(out_dir, pair + ".fs.metal"))
            ok.append(r)
        with open(os.path.join(out_dir, GFX_INDEX), "w") as f:
            f.write("# written by nps5 cache metal; LOCAL ONLY. list_sha=%s\n" % list_sha)
            f.write("list_sha\t%s\n" % list_sha)
            if classes:
                f.write("class\t%s\n" % class_name(dev))
            for r in ok:
                f.write("%s\t%s\t%s\t%s%s\n" % (r["kk_vs"], r["kk_fs"], r["vs"], r["ps"], "\t%s\t%s" % (r["mode"], r["cls"]) if classes else ""))
        with open(os.path.join(out_dir, "skipped-gfx.tsv"), "w") as f:
            for r, why in bad:
                f.write("%s\t%s\t%s\n" % (r["kk_vs"], r["kk_fs"], why))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    reasons = {}
    for _, why in bad:
        reasons[why] = reasons.get(why, 0) + 1
    nlisted = len({(r["kk_vs"], r["kk_fs"]) for r in rows})
    return dict(listed=len(rows) if not classes else nlisted, written=len(ok), skipped=len(bad), reasons=reasons, list_sha=list_sha,
                emitter=meta.get("emitter", ()), cls=class_name(dev) if classes else None, kk_class=kkc, fallback=fb, other_class=nlisted - len(pairs),
                cls_note=(dev or {}).get("note"))


# v1.2 graphics path, verbatim from v1.2-dev 7b538c4 (see _generate_v12)
def _generate_gfx_v12(list_path, programs, kk_msl_dir, out_dir, environ, jobs=4, dump=run_dump_gfx, label="", out=None, lens=None, keep_dir=None):
    """Add <kk_vs>_<kk_fs>.vs.metal/.fs.metal to out_dir (created if needed; call after generate(), which replaces the directory)
    plus index-gfx.tsv/skipped-gfx.tsv. -> dict(listed, written, skipped, reasons, list_sha)."""
    out = out or sys.stdout
    with open(list_path) as f:
        text = f.read()
    meta, rows = parse_gfx_list(text)
    list_sha = hashlib.sha256(text.encode()).hexdigest()
    tool = tool_path(environ)
    if not tool:
        raise MetalError("no kyty_msl_dump (build it with scripts/build.sh, or set NPS5_MSL_TOOL)")
    if not os.path.isfile(programs):
        raise MetalError("no %s (prepare the cache first)" % programs)
    perms = read_perms(programs, cursors=True)
    env = clean_env(environ)
    # KosmicKrisp writes its vertex MSL to kk-libcache/msl only when it compiles a shader; a later prepare LOADS the metallibs, so
    # the texts are gone and every pair would be dropped. Keep a copy of each needed vertex text in keep_dir (prepare: next to the
    # metallib store, it outlives the cache dir; default <out_dir>/../kk-vs-keep) as <kk key>.metal. The key is KosmicKrisp's
    # BLAKE3 of the text, so the store is content-addressed; the sha256 check of our texts still decides.
    keep = keep_dir or os.path.join(os.path.dirname(os.path.abspath(out_dir)), KK_VS_KEEP)
    wanted = {r["kk_vs"] for r in rows}
    kkv = {k: os.path.join(keep, k + ".metal") for k in wanted if os.path.isfile(os.path.join(keep, k + ".metal"))}
    found = find_kk_vertex(kk_msl_dir, wanted - set(kkv), lens) if wanted - set(kkv) else {}
    if found:
        os.makedirs(keep, exist_ok=True)
        for k, src in found.items():
            dst = os.path.join(keep, k + ".metal")
            shutil.copyfile(src, dst + ".tmp")
            os.replace(dst + ".tmp", dst)
            kkv[k] = dst
    os.makedirs(out_dir, exist_ok=True)
    for fn in os.listdir(out_dir):                     # a pair set of another list is never left behind
        if fn.endswith((".vs.metal", ".ms.metal", ".fs.metal")) or fn in (GFX_INDEX, "skipped-gfx.tsv"):
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
            os.replace(files[0], os.path.join(out_dir, pair + first_ext(r)))
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
    return dict(listed=len(rows), written=len(ok), skipped=len(bad), reasons=reasons, list_sha=list_sha, emitter=meta.get("emitter", ()))


TEXT_DIFFERS = ("text differs from the verified kernel", "text differs from the verified pair")


def emitter_problem(st, kyty_commit):
    """-> a message when a list's texts were not reproduced (sha256 mismatch) and why, else None. The list's `emitter` line names
    the Kyty commit (and release tag) its hashes were made with; components.lock pins the same commit. A different Kyty is the usual
    cause: rebuild the pinned one."""
    n = sum(v for k, v in (st or {}).get("reasons", {}).items() if k in TEXT_DIFFERS)
    if not n:
        return None
    em = tuple(st.get("emitter") or ())
    want = em[0] if em else ""
    tag = " (%s)" % em[1] if len(em) > 1 else ""
    have = kyty_commit if kyty_commit and kyty_commit != "unknown" else ""
    if want and have and not (have.startswith(want) or want.startswith(have)):
        return ("%d shader(s) differ from the verified texts: the list was made with Kyty %s%s, your kyty_msl_dump is from Kyty %s. "
                "Use the pinned components (scripts/fetch-components.sh, then scripts/build.sh kyty)" % (n, want[:12], tag, have[:12]))
    if want and have:
        return ("%d shader(s) differ from the verified texts although Kyty is the listed %s%s (another compiler or macOS SDK?): "
                "please report it with `nps5 bugreport`" % (n, want[:12], tag))
    return "%d shader(s) differ from the verified texts (Kyty commit unknown: is NPS5_KYTY_DIR the pinned checkout?)" % n


def gfx_summary(st):
    s = "Metal graphics: %d of %d verified vertex+fragment pairs generated" % (st["written"], st["listed"])
    if st["reasons"]:
        s += "; skipped: " + ", ".join("%s %d" % (k, v) for k, v in sorted(st["reasons"].items()))
    return s + class_summary(st)


def gfx_index_rows(out_dir):
    """-> (list_sha, number of pairs) of the generated graphics set, (None, 0) when absent / torn."""
    try:
        with open(os.path.join(out_dir, GFX_INDEX)) as f:
            lines = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return None, 0
    sha = next((l[1] for l in lines if l[0] == "list_sha" and len(l) > 1), None)
    n = sum(1 for l in lines if l[0] not in ("list_sha", "class") and len(l) >= 4
            and any(os.path.isfile(os.path.join(out_dir, "%s_%s%s" % (l[0], l[1], x))) for x in (".vs.metal", ".ms.metal"))
            and os.path.isfile(os.path.join(out_dir, "%s_%s.fs.metal" % (l[0], l[1]))))
    return sha, n


# ---------------------------------------------------------------- reproducibility of a per-class list (every row, every class)
def _class_sublist(text, cls, gfx, classes=True):
    """The list with only the rows of one class spec, rewritten to class `*` (so they generate on any Mac). Header lines kept.
    A list without class columns (keys 1, pairs 1/2) is all `*`: returned as it is."""
    if not classes:
        return text if text.endswith("\n") else text + "\n"
    out = []
    for line in text.splitlines():
        f = line.split("\t")
        is_row = (f[0] == "gfx") if gfx else (len(f) > 5 and len(f[0]) == 32 and not line.startswith("#"))
        if not is_row:
            out.append(line)
        elif f[-4] == cls and f[-5] != "kk":
            f[-4] = "*"
            out.append("\t".join(f))
    return "\n".join(out) + "\n"


def reproduce(list_path, gfx_path, programs, kk_msl_dir, environ, work, jobs=4, keep_dir=None, out=None):
    """Regenerate EVERY text row of the lists (all classes, not only this Mac's) and compare with the listed sha256: the proof that the
    public generator reproduces the list. -> dict(rows, reproduced, per_class {cls: (ok, n)}, failed [(kind, key, cls, why)])."""
    out = out or sys.stdout
    res = dict(rows=0, reproduced=0, per_class={}, failed=[])
    for path, gfx in ((list_path, False), (gfx_path, True)):
        if not path or not os.path.isfile(path):
            continue
        with open(path) as f:
            text = f.read()
        meta, rows = (parse_gfx_list if gfx else parse_list)(text)
        classes = meta.get("format", ("", ""))[1:2] == (("3",) if gfx else ("2",))
        for cls in sorted({r["cls"] for r in rows if r["mode"] != "kk"}):
            sub = [r for r in rows if r["cls"] == cls and r["mode"] != "kk"]
            tag = "%s-%s" % ("gfx" if gfx else "cs", re.sub(r"[^a-z0-9]", "_", cls))
            lp = os.path.join(work, tag + ".tsv")
            with open(lp, "w") as f:
                f.write(_class_sublist(text, cls, gfx, classes))
            od = os.path.join(work, tag)
            if gfx:
                st = generate_gfx(lp, programs, kk_msl_dir, od, environ, jobs=jobs, out=out, keep_dir=keep_dir, dev=None)
                done = {(l[0], l[1]) for l in _index_lines(os.path.join(od, GFX_INDEX))}
                why = {(l[0], l[1]): l[2] for l in _index_lines(os.path.join(od, "skipped-gfx.tsv"))}
                for r in sub:
                    k = (r["kk_vs"], r["kk_fs"])
                    ok = k in done
                    res["rows"] += 1; res["reproduced"] += ok
                    pc = res["per_class"].setdefault(("gfx", cls), [0, 0]); pc[0] += ok; pc[1] += 1
                    if not ok:
                        res["failed"].append(("gfx", "%s_%s" % k, cls, why.get(k, "?")))
            else:
                st = generate(lp, programs, od, environ, jobs=jobs, out=out, dev=None)
                done = {l[0] for l in _index_lines(os.path.join(od, "index.tsv"))}
                why = {l[0]: l[3] for l in _index_lines(os.path.join(od, "skipped.tsv")) if len(l) > 3}
                for r in sub:
                    ok = r["kk"] in done
                    res["rows"] += 1; res["reproduced"] += ok
                    pc = res["per_class"].setdefault(("cs", cls), [0, 0]); pc[0] += ok; pc[1] += 1
                    if not ok:
                        res["failed"].append(("cs", r["kk"], cls, why.get(r["kk"], "?")))
    return res


def _index_lines(path):
    try:
        with open(path) as f:
            return [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith(("#", "list_sha\t", "class\t"))]
    except OSError:
        return []


def reproduce_record(res):
    """The record lines for <list>.verified (scripts/nps5_check.py requires `reproduced=N of N` for a per-class list)."""
    lines = ["reproduced=%d of %d" % (res["reproduced"], res["rows"])]
    for (kind, cls), (ok, n) in sorted(res["per_class"].items()):
        lines.append("class %s %s: %d of %d" % (kind, cls, ok, n))
    return lines


# ---------------------------------------------------------------- launch words
def index_rows(out_dir):
    """-> (list_sha, number of kernels) of a generated dir, (None, 0) when absent / torn."""
    try:
        with open(os.path.join(out_dir, "index.tsv")) as f:
            lines = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return None, 0
    sha = next((l[1] for l in lines if l[0] == "list_sha" and len(l) > 1), None)
    n = sum(1 for l in lines if l[0] not in ("list_sha", "class") and len(l) >= 4 and os.path.isfile(os.path.join(out_dir, l[0] + ".metal")))
    return sha, n


def mesh_words(d):
    """meshpath: KK_MSL_OVERRIDE_MESH=1 (patch 0029) when the generated set holds mesh pairs (<ms>_<fs>.ms.metal), else nothing."""
    try:
        return [("KK_MSL_OVERRIDE_MESH", "1")] if any(f.endswith(".ms.metal") for f in os.listdir(d)) else []
    except OSError:
        return []


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
    made = {c for c in (index_class(d, "index.tsv") if n else None, index_class(d, GFX_INDEX) if gn else None) if c}
    if made:                                           # per-class lists: the texts were chosen for one GPU class (and macOS major)
        here = class_name(device_class(environ))
        if made != {here}:
            return [], ("the Metal shaders of this cache were chosen for GPU class %s, this Mac is %s (another Mac or a macOS update): "
                        "not used; `nps5 cache metal <id>` regenerates them" % ("/".join(sorted(made)), here))
    return [("KK_MSL_OVERRIDE_DIR", d)] + ([("KK_MSL_CANON_KEY", "1")] if canon_enabled(environ) else []) + mesh_words(d), None


def index_class(out_dir, name):
    """-> the `class` line of a generated index (None for a list without classes, i.e. every v1.2 index)."""
    try:
        with open(os.path.join(out_dir, name)) as f:
            for l in f:
                if l.startswith("class\t"):
                    return l.rstrip("\n").split("\t")[1]
    except OSError:
        pass
    return None
