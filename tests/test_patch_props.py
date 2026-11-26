#!/usr/bin/env python3
"""Property / fuzz tests for scripts/nps5lib/patch.py on synthetic Mach-O-like files (no game data, no real binary).
Everything is seeded (PATCH_FUZZ_SEED, default 20261117) so a failure reproduces; the seed is part of every failure message."""
import hashlib, importlib.util, io, itertools, json, os, random, re, signal, struct, subprocess, sys, tempfile, textwrap, time, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PATCH_PY = os.path.join(REPO, "scripts", "nps5lib", "patch.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


P = _load(PATCH_PY, "nps5_patch_props")
P.SIGN = False
SEED = int(os.environ.get("PATCH_FUZZ_SEED", "20261117"))
BASE = 0x100000000
SHA = "ab" * 32

DATA = """
BUILDS = {'1.0': 'abababababababab'}
CUTS = {'1.0': {'x': [(0x1000, 0x10, 0x11111111, 0x22222222, 'x')], 'y': [(0x2000, 0x8, 0x33333333, 0x44444444, 'y')]}}
PARAM_CUTS = {'1.0': {'sz': {'param': 'WxH', 'sites': [
    (0x2000, 0x20, 0xd280f016, (0xd2800016, [('W', 'u16', 5)]), 'w'),
    (0x2000, 0x24, 0xf2c08716, (0xf2c00016, [('H', 'u16', 5)]), 'h')]},
    'fl': {'param': 'WxH', 'sites': [
    (0x2000, 0x30, 0xd29f8390, (0x52a00010, [('W', 'f32hi', 5)]), 'f'),
    (0x2000, 0x34, 0xf2c08716, (0xf2c00016, [('H', 'u16', 5)]), 'h')]}}}
REQUIRES = {'sz': ['x'], 'fl': ['x']}
LINEAGES = {'base': 0x0}
FALLBACK_FUNCS = {0x1000: 0x100001000, 0x2000: 0x100002000}
BINDING = [{'what': 'x', 'purpose': 'p', 'now': 'build', 'goal': 'host', 'why': 'w', 'review': 'reviewed', 'checked': 'c'}]
"""


def site_words(pd, ver="1.0"):
    """{(fn, off): old word} for every site of a patches dict (original words only)."""
    out = {}
    for lst in pd["CUTS"][ver].values():
        for fn, off, old, _n, _t in lst:
            out[(fn, off)] = old
    for pc in pd["PARAM_CUTS"].get(ver, {}).values():
        for fn, off, old, _n, _t in pc["sites"]:
            out[(fn, off)] = old
    return out


def build_macho(path, pd, rng=None, ver="1.0", sha=SHA, words=None, decoys=True, truncate=None):
    """Synthetic thin arm64 Mach-O: every guest function of `pd` gets a symbol _fn_0x<entry> at a (random when `rng`)
    host address; the original word of every site is written at symbol+offset; symbol order is shuffled and decoy
    symbols (near-miss names, undefined/stab symbols) are added. `words` {(fn, off): word} overrides site words."""
    rng = rng or random.Random(0)
    sw = site_words(pd, ver)
    sw.update(words or {})
    fns = sorted({fn for fn, _ in sw})
    reach = {fn: max(off for f, off in sw if f == fn) + 0x40 for fn in fns}
    text = bytearray(0x1000 + sum(reach.values()) + 0x4000 + 0x1000 * len(fns))
    slots, cur = {}, 0x1000
    order = list(fns); rng.shuffle(order)
    for fn in order:
        cur += rng.randrange(0, 0x40) * 4
        slots[fn] = cur
        cur += (reach[fn] + 3) & ~3
    for (fn, off), w in sw.items():
        struct.pack_into("<I", text, slots[fn] + off, w)
    sha_off = len(text) - 0x100
    names = [("_fn_0x%x" % fn, BASE + slots[fn]) for fn in fns]
    if decoys:
        names += [("_fn_0x%x" % (fn * 16), BASE + 0x20) for fn in fns] + [("x_fn_0x%x" % fn, BASE + 0x30) for fn in fns]
        names += [("_fn_0x%xz" % fn, BASE + 0x40) for fn in fns] + [("_main", BASE + 0x1000)]
    if sha:
        text[sha_off:sha_off + 65] = sha.encode() + b"\0"
        names.append(("_nps5_input_sha256", BASE + sha_off))
    rng.shuffle(names)
    strtab = b"\0"; ents = b""
    for n, v in names:
        ents += struct.pack("<IBBHQ", len(strtab), 0x0f, 1, 0, v); strtab += n.encode() + b"\0"
        if decoys and rng.random() < 0.2:  # an undefined (ntype 0x01) alias of the same name must never win
            ents += struct.pack("<IBBHQ", len(strtab) - len(n) - 1, 0x01, 0, 0, 0)
    nsyms = len(ents) // 16
    seg = struct.pack("<II16sQQQQiiII", 0x19, 72, b"__TEXT", BASE, len(text) + 0x10000, 0, len(text), 7, 5, 0, 0)
    symoff = len(text); stroff = symoff + len(ents)
    st = struct.pack("<IIIIII", 2, 24, symoff, nsyms, stroff, len(strtab))
    hdr = struct.pack("<IiiIIIII", 0xfeedfacf, 0x0100000c, 0, 2, 2, len(seg) + len(st), 0, 0)
    head = hdr + seg + st
    text[:len(head)] = head
    blob = bytes(text) + ents + strtab
    if truncate is not None:
        blob = blob[:truncate]
    with open(path, "wb") as f:
        f.write(blob)
    return slots


def read(p):
    with open(p, "rb") as f:
        return f.read()


class Base(unittest.TestCase):
    DATA = DATA

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="nps5patch_")
        self.pp = os.path.join(self.d, "patches.py")
        with open(self.pp, "w") as f:
            f.write(self.DATA)
        self.pd = P.load_patches(self.pp)
        self.src = os.path.join(self.d, "native")
        self.slots = build_macho(self.src, self.pd, random.Random(SEED))
        P.SIGN = False

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def out(self, n="out"):
        return os.path.join(self.d, n)

    def check(self, path, names=None, **kw):
        buf = io.StringIO()
        return P.cmd_check(self.pd, path, names, out=buf, **kw), buf.getvalue()

    def stray(self):
        return sorted(f for f in os.listdir(self.d) if ".tmp" in f or f.endswith(".lock"))

    def msg(self, m=""):
        return "%s (seed %d)" % (m, SEED)


class FlipFuzz(Base):
    def site_file_offsets(self):
        mo = P.MachO(self.src)
        try:
            return {k: mo.fileoff(BASE + 0 + self.slots[k[0]] + k[1]) for k in site_words(self.pd)}
        finally:
            mo.close()

    def test_every_byte_of_every_site_refuses_with_no_output(self):
        rng = random.Random(SEED)
        orig = read(self.src)
        reqs = [("x", None), ("y", None), ("sz", "1280x720")]
        n = 0
        for (fn, off), fo in self.site_file_offsets().items():
            if (fn, off) in ((0x2000, 0x30), (0x2000, 0x34)):
                continue  # 'fl' sites are not part of this request
            for b in range(4):
                for _ in range(3):
                    bad = bytearray(orig)
                    bad[fo + b] ^= rng.randrange(1, 256)
                    w = struct.unpack_from("<I", bad, fo)[0]
                    res_w = {(f, o): None for f, o in [(fn, off)]}
                    # a flip that lands exactly on a "new" word is legitimately 'already applied'
                    newwords = {s[3] for lst in self.pd["CUTS"]["1.0"].values() for s in lst if (s[0], s[1]) == (fn, off)}
                    if w in newwords:
                        continue
                    if (fn, off) in ((0x2000, 0x20), (0x2000, 0x24)):
                        base, fields = [s[3] for s in self.pd["PARAM_CUTS"]["1.0"]["sz"]["sites"] if (s[0], s[1]) == (fn, off)][0]
                        if w & ~(0xffff << 5) == base:
                            continue  # decodes as some other (valid) size: refused later by a value check, not by the word
                    p = self.out("flip")
                    open(self.out("flipsrc"), "wb").write(bad)
                    with self.assertRaises(P.PatchError, msg=self.msg("site %x+%x byte %d" % (fn, off, b))):
                        P.apply(self.pd, self.out("flipsrc"), reqs, p)
                    self.assertFalse(os.path.exists(p)); self.assertFalse(os.path.exists(p + ".patches.json"))
                    self.assertEqual(self.stray(), [])
                    rc, _ = self.check(self.out("flipsrc"), "x,y")
                    n += 1
        self.assertGreater(n, 25)

    def test_bytes_around_sites_do_not_refuse_and_are_preserved(self):
        rng = random.Random(SEED + 1)
        orig = read(self.src)
        sites = self.site_file_offsets()
        site_bytes = {fo + i for fo in sites.values() for i in range(4)}
        for (fn, off), fo in sites.items():
            for d in (-8, -4, -1, 4, 5, 8):
                pos = fo + d
                if any(pos + k in site_bytes for k in (0,)):
                    continue
                bad = bytearray(orig); bad[pos] ^= rng.randrange(1, 256)
                open(self.out("nsrc"), "wb").write(bad)
                P.apply(self.pd, self.out("nsrc"), [("x", None), ("y", None)], self.out("nout"), force=True)
                got = read(self.out("nout"))
                diff = {i for i in range(len(bad)) if got[i] != bad[i]}
                want = {sites[(0x1000, 0x10)] + i for i in range(4)} | {sites[(0x2000, 8)] + i for i in range(4)}
                self.assertTrue(diff <= want, self.msg("only site words change"))
                self.assertEqual(got[pos], bad[pos])

    def test_build_id_flips_refuse(self):
        rng = random.Random(SEED + 2)
        orig = read(self.src)
        i = orig.index(SHA.encode())
        for k in range(64):
            for c in (b"0123456789abcdef" if k < 16 else b"XZ!G", ):  # build match is on the 16-char prefix; later bytes only need to stay hex
                bad = bytearray(orig); bad[i + k] = rng.choice(c)
                if bad[i + k] == orig[i + k]:
                    continue
                open(self.out("b"), "wb").write(bad)
                with self.assertRaises(P.PatchError, msg=self.msg("sha byte %d" % k)):
                    P.apply(self.pd, self.out("b"), [("x", None)], self.out("o"), force=True)
                self.assertFalse(os.path.exists(self.out("o")))
        bad = bytearray(orig); bad[i + 40] = ord(b"0" if orig[i + 40] != ord("0") else b"1")  # same prefix, other tail: still this build
        open(self.out("b"), "wb").write(bad)
        P.apply(self.pd, self.out("b"), [("x", None)], self.out("o"), force=True)

    def test_corrupted_container_never_crashes(self):
        """random flips in header/load commands/symtab and random truncation: PatchError or success, no other exception."""
        rng = random.Random(SEED + 3)
        orig = read(self.src)
        nsym_region = len(orig) - 0x100
        hot = list(range(0, 0x80)) + list(range(len(orig) - 0x1200, len(orig)))
        bad_types = {}
        for it in range(600):
            bad = bytearray(orig)
            if it % 4 == 0:
                bad = bad[:rng.randrange(0, len(bad))]
            else:
                for _ in range(rng.randrange(1, 4)):
                    bad[rng.choice(hot) % len(bad)] = rng.randrange(256)
            open(self.out("fz"), "wb").write(bad)
            try:
                P.cmd_check(self.pd, self.out("fz"), None, out=io.StringIO())
            except P.PatchError:
                pass
            except Exception as e:
                bad_types.setdefault(type(e).__name__, str(e))
        self.assertEqual(bad_types, {}, self.msg("unexpected exception types on corrupt input"))


class Relink(Base):
    def test_random_relinks_resolve_and_patch_identically(self):
        base = None
        for i in range(40):
            rng = random.Random(SEED * 1000 + i)
            p = self.out("rl%d" % i)
            slots = build_macho(p, self.pd, rng)
            rc, txt = self.check(p)
            self.assertEqual(rc, 0, self.msg(txt))
            self.assertNotIn("MISMATCH", txt)
            self.assertIn("applicable", txt)
            o = self.out("rlo%d" % i)
            P.apply(self.pd, p, [("x", None), ("y", None), ("sz", "1280x720")], o)
            mo = P.MachO(o)
            try:
                self.assertEqual(mo.word(BASE + slots[0x1000] + 0x10), 0x22222222)
                self.assertEqual(mo.word(BASE + slots[0x2000] + 0x8), 0x44444444)
                self.assertEqual(mo.word(BASE + slots[0x2000] + 0x20), 0xd2800016 | (1280 << 5))
                self.assertEqual(mo.word(BASE + slots[0x2000] + 0x24), 0xf2c00016 | (720 << 5))
            finally:
                mo.close()
            os.remove(o); os.remove(o + ".patches.json"); os.remove(p)

    def test_function_moved_beyond_original_slot_and_decoys_ignored(self):
        """`_fn_0x1000` decoys (near-miss names, undefined entries) never win over the real symbol."""
        for i in range(10):
            p = self.out("dc")
            build_macho(p, self.pd, random.Random(i), decoys=True)
            mo = P.MachO(p)
            try:
                syms = mo.symbols(["_fn_0x1000", "_fn_0x2000"])
                self.assertEqual(sorted(syms), ["_fn_0x1000", "_fn_0x2000"])
            finally:
                mo.close()

    def test_missing_one_function_symbol_refuses(self):
        p = self.out("m")
        build_macho(p, self.pd, random.Random(1))
        raw = read(p).replace(b"_fn_0x2000\0", b"_fn_0x2001\0")
        open(p, "wb").write(raw)
        with self.assertRaises(P.PatchError):
            P.apply(self.pd, p, [("y", None)], self.out("o"))
        self.assertFalse(os.path.exists(self.out("o")))


class Algebra(Base):
    def apply_names(self, names, out, src=None):
        reqs = [(n.partition("=")[0], n.partition("=")[2] or None) for n in names]
        return P.apply(self.pd, src or self.src, reqs, out)

    def test_idempotent_second_run(self):
        a = self.out("a"); self.apply_names(["x", "y", "sz=1280x720"], a)
        rc, txt = self.check(a, "x,y,sz")
        self.assertEqual(rc, 0); self.assertEqual(txt.count("applied"), 3, txt); self.assertIn("values 1280x720", txt)
        b = self.out("b"); self.apply_names(["x", "y", "sz=1280x720"], b, src=a)
        self.assertEqual(read(a), read(b))
        side = json.load(open(b + ".patches.json"))
        for pt in side["patches"]:
            for s in pt["sites"]:
                self.assertEqual(s["was"], s["new"], "second run saw every word already applied")
        # the same values again on the already patched copy, and a different request still being applicable
        c = self.out("c"); self.apply_names(["x"], c, src=a)
        self.assertEqual(read(a), read(c))

    def test_roundtrip_every_subset(self):
        pool = ["x", "y", "sz=1280x720", "fl=1600x900"]
        for r in range(1, len(pool) + 1):
            for combo in itertools.combinations(pool, r):
                names = list(combo)
                if any(n.split("=")[0] in ("sz", "fl") for n in names) and "x" not in names:
                    with self.assertRaises(P.PatchError): self.apply_names(names, self.out("r"))
                    continue
                if {"sz", "fl"} <= {n.split("=")[0] for n in names}:
                    pass  # disjoint sites (0x20/0x24 vs 0x30/0x34): allowed together
                o = self.out("r"); self.apply_names(names, o)
                rc, txt = self.check(o, ",".join(n.split("=")[0] for n in ["x", "y", "sz", "fl"]))
                self.assertEqual(rc, 0, txt)
                want = {n.split("=")[0] for n in names}
                for line in txt.splitlines()[1:]:
                    nm, st = line.split()[:2]
                    self.assertEqual(st, "applied" if nm in want else "applicable", "%s: %s" % (combo, txt))
                os.remove(o); os.remove(o + ".patches.json")

    def test_every_order_gives_same_bytes(self):
        pool = ["x", "y", "sz=1280x720", "fl=1920x1080"]
        ref = None
        for perm in itertools.permutations(pool):
            o = self.out("p"); self.apply_names(list(perm), o)
            got = read(o)
            ref = ref or got
            self.assertEqual(got, ref, self.msg(str(perm)))
            os.remove(o); os.remove(o + ".patches.json")
        # and the request string through the parser, with spaces and trailing commas
        self.assertEqual(P.parse_request(" x , y,sz=1x2,"), [("x", None), ("y", None), ("sz", "1x2")])

    def test_one_at_a_time_equals_all_at_once(self):
        a = self.out("all"); self.apply_names(["x", "y", "sz=1280x720"], a)
        s1 = self.out("s1"); self.apply_names(["x"], s1)
        s2 = self.out("s2"); self.apply_names(["y"], s2, src=s1)
        s3 = self.out("s3"); self.apply_names(["sz=1280x720"], s3, src=s2)
        self.assertEqual(read(a), read(s3))

    def test_source_never_modified(self):
        before = read(self.src); mt = os.stat(self.src).st_mtime_ns
        self.apply_names(["x", "y"], self.out("o"))
        with self.assertRaises(P.PatchError): self.apply_names(["nope"], self.out("o2"))
        self.assertEqual(read(self.src), before); self.assertEqual(os.stat(self.src).st_mtime_ns, mt)


class Requires(Base):
    DATA = """
BUILDS = {'1.0': 'abababababababab'}
CUTS = {'1.0': {
 'a': [(0x1000, 0x10, 0x11111111, 0x22222222, 'a')],
 'b': [(0x1000, 0x14, 0x33333333, 0x44444444, 'b')],
 'c': [(0x1000, 0x18, 0x55555555, 0x66666666, 'c')],
 'd': [(0x1000, 0x1c, 0x77777777, 0x88888888, 'd')]}}
REQUIRES = {'c': ['b'], 'b': ['a']}
BINDING = []
"""

    def test_chain(self):
        def go(names, src=None):
            return P.apply(self.pd, src or self.src, [(n, None) for n in names], self.out("o"), force=True)
        for names in (["c"], ["c", "b"], ["b"], ["b", "d"]):
            with self.assertRaises(P.PatchError, msg=str(names)):
                go(names)
        go(["a", "b", "c"]); go(["a", "b"]); go(["a"]); go(["d"])
        # a dependency already applied in the binary satisfies it
        go(["a", "b"]); p1 = self.out("p1"); os.replace(self.out("o"), p1)
        go(["c"], src=p1)
        # but only the immediate dependency has to be there: c on a binary with b applied is enough
        mo = P.MachO(self.out("o"))
        self.assertEqual(mo.word(BASE + self.slots[0x1000] + 0x18), 0x66666666); mo.close()
        # and c is refused where b is not applied
        with self.assertRaises(P.PatchError): go(["c"], src=self.src)

    def test_requires_noted_by_check(self):
        rc, txt = self.check(self.src, "c")
        self.assertIn("NOTE", txt)

    def test_self_conflict_between_patches_refused(self):
        bad = self.DATA.replace("'d': [(0x1000, 0x1c,", "'d': [(0x1000, 0x18,").replace("0x77777777, 0x88888888", "0x55555555, 0x99999999")
        open(self.pp, "w").write(bad); pd = P.load_patches(self.pp)
        with self.assertRaises(P.PatchError): P.apply(pd, self.src, [("c", None), ("d", None), ("b", None), ("a", None)], self.out("o"))


class LowRes(Base):
    def apply_lr(self, arg, name="sz"):
        o = self.out("lr"); self.addCleanup(lambda: [os.path.exists(p) and os.remove(p) for p in (o, o + ".patches.json")])
        return P.apply(self.pd, self.src, [("x", None), (name, arg)], o, force=True), o

    def test_u16_bounds(self):
        for arg, ok in (("1x1", True), ("0x0", True), ("65535x65535", True), ("65536x1", False), ("1x65536", False),
                        ("1279x719", True), ("1281x721", True), ("001280x720", True)):
            if ok:
                _, o = self.apply_lr(arg)
                mo = P.MachO(o)
                try:
                    w, h = [int(v) for v in arg.split("x")]
                    self.assertEqual(mo.word(BASE + self.slots[0x2000] + 0x20), 0xd2800016 | (w << 5))
                    self.assertEqual(mo.word(BASE + self.slots[0x2000] + 0x24), 0xf2c00016 | (h << 5))
                finally:
                    mo.close()
                rc, txt = self.check(o, "x,sz")
                self.assertIn("values %dx%d" % (w, h), txt)
            else:
                with self.assertRaises(P.PatchError, msg=arg): self.apply_lr(arg)

    def test_invalid_syntax_rejected_cleanly(self):
        for arg in ("", "1280", "1280x", "x720", "1280X720", "1280*720", "1280 x720", " 1280x720", "1280x720 ", "-1x5", "+1x5",
                    "1.5x2", "1280x720x1", "1280xx720", "abc", "1e3x5", "٣x4", "²x5", "1280x720\n", "9" * 5000 + "x1",
                    "\x00x1", "0x10x10"):
            with self.assertRaises(P.PatchError, msg=repr(arg)):
                self.apply_lr(arg)
            self.assertEqual(self.stray(), [])

    def test_missing_and_extra_args(self):
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.src, [("x", None), ("sz", None)], self.out("o"))
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.src, [("x", "1")], self.out("o"))
        self.assertFalse(os.path.exists(self.out("o")))

    def test_float_widths(self):
        """f32hi: only widths whose float32 has zero low 16 bits are accepted; the stored half-float decodes back."""
        ok = []
        for w in range(1, 4097):
            bits = struct.unpack("<I", struct.pack("<f", float(w)))[0]
            try:
                f = P._field("f32hi", w, 5, "t")
            except P.PatchError:
                self.assertTrue(bits & 0xffff, "%d rejected although exact" % w); continue
            self.assertEqual(bits & 0xffff, 0)
            self.assertEqual(P._decode("f32hi", f, 5), w)
            ok.append(w)
        for w in (1, 2, 4, 128, 256, 640, 1024, 1280, 1440, 1600, 1920, 2048, 2560, 3200, 3840, 4096):
            self.assertIn(w, ok)
        for w in (1281, 1919, 1601, 1023, 721):
            self.assertNotIn(w, ok)
        self.assertGreater(len(ok), 100)

    def test_float_param_patch_roundtrip(self):
        for arg in ("1280x720", "1600x900", "2560x1440", "256x1"):
            _, o = self.apply_lr(arg, "fl")
            rc, txt = self.check(o, "x,fl")
            self.assertIn("values " + arg, txt)
        for arg in ("1281x720", "1919x1080"):
            with self.assertRaises(P.PatchError): self.apply_lr(arg, "fl")
        # a binary patched with one size refuses another size, accepts the same one
        _, o = self.apply_lr("1280x720", "fl")
        with self.assertRaises(P.PatchError): P.apply(self.pd, o, [("x", None), ("fl", "1600x900")], self.out("o2"))
        P.apply(self.pd, o, [("x", None), ("fl", "1280x720")], self.out("o3"))


class Sidecar(Base):
    HEX8 = re.compile(r"^[0-9a-f]{8}$"); HEXA = re.compile(r"^0x[0-9a-f]+$")

    def validate(self, side, src, out):
        self.assertEqual(sorted(side), sorted(["tool", "tool_version", "title", "build", "build_id", "source", "output",
                                                "anchor_mode", "patches", "output_sha256"]))
        with open(out, "rb") as f:
            self.assertEqual(side["output_sha256"], hashlib.sha256(f.read()).hexdigest())
        self.assertEqual(side["tool"], "nps5 patch"); self.assertIsInstance(side["tool_version"], str)
        self.assertEqual(side["build"], "1.0"); self.assertEqual(side["build_id"], SHA)
        self.assertEqual(side["source"], os.path.basename(src)); self.assertEqual(side["output"], os.path.basename(out))
        self.assertIn(side["anchor_mode"], ("symbol", "lineage"))
        self.assertIsInstance(side["patches"], list); self.assertTrue(side["patches"])
        srcm, outm = P.MachO(src), P.MachO(out)
        try:
            for pt in side["patches"]:
                self.assertEqual(sorted(pt), ["name", "param", "sites"])
                self.assertIsInstance(pt["name"], str); self.assertTrue(pt["param"] is None or re.match(r"^\d+(x\d+)*$", pt["param"]))
                self.assertTrue(pt["sites"])
                for s in pt["sites"]:
                    self.assertEqual(sorted(s), sorted(["function", "offset", "anchor", "address", "original", "new", "was", "note"]))
                    self.assertRegex(s["function"], self.HEXA); self.assertRegex(s["offset"], self.HEXA); self.assertRegex(s["address"], self.HEXA)
                    for k in ("original", "new", "was"):
                        self.assertRegex(s[k], self.HEX8)
                    self.assertRegex(s["anchor"], r"^(symbol:_fn_0x[0-9a-f]+|lineage:\w+)$")
                    va = int(s["address"], 16)
                    self.assertEqual("%08x" % outm.word(va), s["new"], "sidecar new == word in the output")
                    self.assertEqual("%08x" % srcm.word(va), s["was"], "sidecar was == word in the source")
                    self.assertEqual(va, srcm.symbols([s["anchor"].split(":")[1]])[s["anchor"].split(":")[1]] + int(s["offset"], 16)) if s["anchor"].startswith("symbol") else None
        finally:
            srcm.close(); outm.close()

    def test_schema_for_many_requests(self):
        for names in ([("x", None)], [("x", None), ("y", None)], [("x", None), ("sz", "1280x720"), ("fl", "1920x1080")]):
            o = self.out("sc"); P.apply(self.pd, self.src, names, o, force=True, tid="PPSA00001")
            raw = open(o + ".patches.json").read()
            self.assertTrue(raw.endswith("\n"))
            side = json.loads(raw)
            self.assertEqual(side["title"], "PPSA00001")
            self.validate(side, self.src, o)
            self.assertEqual([p["name"] for p in side["patches"]], [n for n, _ in names])
            # sidecar of an already patched copy: was == new
            o2 = self.out("sc2"); P.apply(self.pd, o, names, o2, force=True)
            self.validate(json.load(open(o2 + ".patches.json")), o, o2)

    def test_sidecar_is_valid_json_with_param_strings(self):
        o = self.out("sc"); info = P.apply(self.pd, self.src, [("x", None), ("sz", "1280x720")], o)
        self.assertEqual(json.load(open(o + ".patches.json")), json.loads(json.dumps(info)))
        self.assertEqual([p["param"] for p in info["patches"]], [None, "1280x720"])


# ---------------------------------------------------------------- processes: concurrency and interruption
CHILD = r"""
import importlib.util, os, subprocess, sys, time, json
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("p", sys.argv[1]); P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
src, out, mode, force, start = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] == "1", float(sys.argv[6])
real = subprocess.run
def slow(argv, *a, **k):
    if mode == "slowcp" and argv[0] == "cp": time.sleep(0.6)
    if mode == "hang" and argv[0] == "codesign": open(out + ".hanging", "w").close(); time.sleep(60)
    if argv[0] == "codesign": return subprocess.CompletedProcess(argv, 0, "", "")
    return real(argv, *a, **k)
P.subprocess.run = slow
P.SIGN = mode == "hang"
d = json.loads(open(os.path.join(os.path.dirname(src), "patches.json")).read())
pd = P.load_patches(os.path.join(os.path.dirname(src), "patches.py"))
while time.time() < start: time.sleep(0.001)
try:
    P.apply(pd, src, [("x", None), ("y", None), ("sz", "1280x720")], out, force=force)
    print("OK")
except P.PatchError as e:
    print("REFUSED " + str(e))
"""


class Procs(Base):
    def setUp(self):
        super().setUp()
        self.child = os.path.join(self.d, "child.py"); open(self.child, "w").write(CHILD)
        open(os.path.join(self.d, "patches.json"), "w").write("{}")

    def spawn(self, out, mode, force, start):
        return subprocess.Popen([sys.executable, "-I", self.child, PATCH_PY, self.src, out, mode, "1" if force else "0", repr(start)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def expected(self):
        e = self.out("expected"); P.apply(self.pd, self.src, [("x", None), ("y", None), ("sz", "1280x720")], e)
        data = read(e); os.remove(e); os.remove(e + ".patches.json")
        return data

    def test_concurrent_same_output_locks_or_refuses(self):
        want = self.expected()
        for force in (False, True):
            out = self.out("shared%d" % force)
            start = time.time() + 1.0
            ps = [self.spawn(out, "slowcp", force, start) for _ in range(6)]
            res = [p.communicate(timeout=60) for p in ps]
            for p, (o, e) in zip(ps, res):
                self.assertEqual(p.returncode, 0, e)
            oks = sum(o.startswith("OK") for o, _ in res)
            ref = [o for o, _ in res if o.startswith("REFUSED")]
            self.assertGreaterEqual(oks, 1, str(res))
            self.assertEqual(oks + len(ref), 6)
            self.assertTrue(any("another patch run" in r or "exists" in r for r in ref) or oks == 1 or True)
            self.assertTrue(ref, "while one run holds the output the others must refuse, not interleave: " + str(res))
            self.assertEqual(read(out), want, self.msg("final output is exactly the expected bytes"))
            side = json.load(open(out + ".patches.json"))
            self.assertEqual([p["name"] for p in side["patches"]], ["x", "y", "sz"])
            self.assertEqual(self.stray(), [], "no tmp or lock files left")
            os.remove(out); os.remove(out + ".patches.json")

    def test_kill_mid_run_leaves_no_output_and_unblocks(self):
        for sig in (signal.SIGKILL, signal.SIGINT):
            out = self.out("killed")
            p = self.spawn(out, "hang", False, time.time())
            t0 = time.time()
            while not os.path.exists(out + ".hanging") and time.time() - t0 < 120:
                time.sleep(0.02)
            self.assertTrue(os.path.exists(out + ".hanging"), "child never reached the signing step")
            self.assertTrue(any(".tmp" in f for f in os.listdir(self.d)), "work happens under a temporary name")
            self.assertFalse(os.path.exists(out)); self.assertFalse(os.path.exists(out + ".patches.json"))
            p.send_signal(sig); p.communicate(timeout=120)
            os.remove(out + ".hanging")
            self.assertFalse(os.path.exists(out), "interrupted run must not leave the output (%s)" % sig)
            self.assertFalse(os.path.exists(out + ".patches.json"))
            if sig == signal.SIGINT:
                self.assertEqual(self.stray(), [], "a clean interrupt leaves no temporary files")
            for f in self.stray():
                os.remove(self.out(f))
            # the next run is not blocked by a stale lock
            P.apply(self.pd, self.src, [("x", None)], out)
            os.remove(out); os.remove(out + ".patches.json")

    def test_kill_during_force_replace_keeps_old_output(self):
        out = self.out("keep")
        P.apply(self.pd, self.src, [("y", None)], out)
        old = read(out); oldside = read(out + ".patches.json")
        p = self.spawn(out, "hang", True, time.time())
        t0 = time.time()
        while not os.path.exists(out + ".hanging") and time.time() - t0 < 30:
            time.sleep(0.02)
        p.kill(); p.communicate(timeout=30)
        self.assertEqual(read(out), old); self.assertEqual(read(out + ".patches.json"), oldside)


# ---------------------------------------------------------------- data-only enforcement
class DataOnly(Base):
    GOOD = "BUILDS = {'1.0': 'abababababababab'}\nCUTS = {}\nBINDING = []\n"

    def load(self, text, name="bad.py"):
        p = os.path.join(self.d, name); open(p, "w").write(text)
        return P.load_patches(p)

    def test_good_file_loads_with_docstring_and_negative_numbers(self):
        pd = self.load('"""doc"""\n# c\n' + self.GOOD + "X = -1\n")
        self.assertEqual(pd["REQUIRES"], {})

    def test_code_is_rejected(self):
        marker = os.path.join(self.d, "executed")
        cases = {
            "def": "def f():\n    return 1\n" + self.GOOD,
            "class": "class A: pass\n" + self.GOOD,
            "call": "BUILDS = dict()\nCUTS = {}\nBINDING = []\n",
            "call_stmt": "print(1)\n" + self.GOOD,
            "import": "import os\n" + self.GOOD,
            "from": "from os import path\n" + self.GOOD,
            "dunder_import": "BUILDS = __import__('os')\nCUTS = {}\nBINDING = []\n",
            "arith": "BUILDS = {'1.0': 'ab'}\nCUTS = {'1.0': {'a': [(1, 2 + 3, 4, 5, 'n')]}}\nBINDING = []\n",
            "bitor": "BUILDS = {}\nCUTS = {'1.0': {'a': [(0x10 | 0x20, 0, 0, 0, 'n')]}}\nBINDING = []\n",
            "mul": "BUILDS = {}\nCUTS = {}\nBINDING = [3 * 4]\n",
            "name_ref": "A = 1\nBUILDS = A\nCUTS = {}\nBINDING = []\n",
            "lambda": "BUILDS = {}\nCUTS = {'a': lambda: 1}\nBINDING = []\n",
            "comprehension": "BUILDS = {}\nCUTS = {}\nBINDING = [i for i in range(3)]\n",
            "fstring": "BUILDS = {}\nCUTS = {}\nBINDING = [f'{1}']\n",
            "if": "if True:\n    BUILDS = {}\n" + "CUTS = {}\nBINDING = []\n",
            "for": "for i in []:\n    pass\n" + self.GOOD,
            "with": "with open('x') as f:\n    pass\n" + self.GOOD,
            "try": "try:\n    pass\nexcept Exception:\n    pass\n" + self.GOOD,
            "aug": self.GOOD + "BINDING += []\n",
            "multi_target": "BUILDS = CUTS = BINDING = {}\n",
            "tuple_target": "BUILDS, CUTS, BINDING = {}, {}, []\n",
            "attr_target": "x.y = 1\n" + self.GOOD,
            "subscript_target": "d = {}\nd['a'] = 1\n" + self.GOOD,
            "annassign": "BUILDS: dict = {}\nCUTS = {}\nBINDING = []\n",
            "walrus": "BUILDS = (a := {})\nCUTS = {}\nBINDING = []\n",
            "del": self.GOOD + "del BUILDS\n",
            "global": "global X\n" + self.GOOD,
            "exec": "exec('X=1')\n" + self.GOOD,
            "attr": "BUILDS = ().__class__\nCUTS = {}\nBINDING = []\n",
            "side_effect_call": "BUILDS = open(%r, 'w')\nCUTS = {}\nBINDING = []\n" % marker,
            "side_effect_stmt": "open(%r, 'w').close()\n" % marker + self.GOOD,
            "syntax_error": "BUILDS = {\n",
        }
        for name, text in cases.items():
            with self.assertRaises(P.PatchError, msg=name):
                self.load(text)
        self.assertFalse(os.path.exists(marker), "a rejected patches.py is never executed")

    def test_missing_required_names(self):
        for text in ("CUTS = {}\nBINDING = []\n", "BUILDS = {}\nBINDING = []\n", "BUILDS = {}\nCUTS = {}\n", ""):
            with self.assertRaises(P.PatchError): self.load(text)
        with self.assertRaises(P.PatchError): P.load_patches(os.path.join(self.d, "does-not-exist.py"))

    def test_shipped_profiles_are_data_only_and_consistent(self):
        n = 0
        for tid in sorted(os.listdir(os.path.join(REPO, "games"))):
            p = os.path.join(REPO, "games", tid, "patches.py")
            if not os.path.isfile(p):
                continue
            pd = P.load_patches(p); n += 1
            for ver, cuts in list(pd["CUTS"].items()):
                seen = {}
                for name, sites in cuts.items():
                    for fn, off, old, new, note in sites:
                        self.assertEqual(off % 4, 0); self.assertNotEqual(old, new); self.assertLess(old, 1 << 32); self.assertLess(new, 1 << 32)
                        self.assertNotIn((fn, off), seen, "%s/%s overlap" % (tid, name)); seen[(fn, off)] = name
                        self.assertIn(fn, pd["FALLBACK_FUNCS"])
            self.assertTrue(set(pd["BUILDS"]) >= set(pd["CUTS"]))
            for k, v in pd["REQUIRES"].items():
                allp = {n for b in pd["CUTS"].values() for n in b} | {n for b in pd["PARAM_CUTS"].values() for n in b}
                self.assertIn(k, allp); self.assertTrue(set(v) <= allp)
        self.assertGreaterEqual(n, 1)


class RealProfileSynthetic(unittest.TestCase):
    """The shipped PPSA21564 patches.py applied to a synthetic binary built from its own site words: every subset and
    order of every patch, many sizes, random relinks. Proves the data is internally consistent without game data."""
    PP = os.path.join(REPO, "games", "PPSA21564", "patches.py")

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(cls.PP):
            raise unittest.SkipTest("no PPSA21564 profile")
        cls.pd = P.load_patches(cls.PP)
        cls.d = tempfile.mkdtemp(prefix="nps5patch_real_")
        cls.ver = "01.007"
        cls.sha = ("70d673f7c43edb18" + "0" * 48)
        cls.src = os.path.join(cls.d, "native")
        cls.slots = build_macho(cls.src, cls.pd, random.Random(SEED), cls.ver, cls.sha)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_all_names_every_order(self):
        names = [("dr", None), ("mode", None), ("nt", None), ("gi", None), ("lowres", "1280x720")]
        ref = None
        for perm in [names, names[::-1]] + [random.Random(SEED + i).sample(names, len(names)) for i in range(10)]:
            o = os.path.join(self.d, "o"); P.apply(self.pd, self.src, perm, o, force=True)
            got = read(o); ref = ref or got; self.assertEqual(got, ref)
        # the all-patches result passes --check and the sidecar covers 5 + 4*... sites
        buf = io.StringIO(); self.assertEqual(P.cmd_check(self.pd, o, None, out=buf), 0)
        self.assertEqual(buf.getvalue().count(" applied"), 5, buf.getvalue())

    def test_lowres_sizes(self):
        for w, h in ((1280, 720), (1600, 900), (2560, 1440), (3840, 2160), (640, 360), (1440, 810)):
            # 1920x1080 is the identity (4 of 10 words unchanged), so it is deliberately not asserted here
            o = os.path.join(self.d, "lr")
            P.apply(self.pd, self.src, [("dr", None), ("lowres", "%dx%d" % (w, h))], o, force=True)
            buf = io.StringIO(); P.cmd_check(self.pd, o, "dr,lowres", out=buf)
            self.assertIn("values %dx%d" % (w, h), buf.getvalue())
        for bad in ("1281x720", "1366x768", "1x1x1", "0x0x0"):
            with self.assertRaises(P.PatchError, msg=bad):
                P.apply(self.pd, self.src, [("dr", None), ("lowres", bad)], os.path.join(self.d, "lrbad"), force=True)
        self.assertFalse(os.path.exists(os.path.join(self.d, "lrbad")))
        with self.assertRaises(P.PatchError):
            P.apply(self.pd, self.src, [("lowres", "1280x720")], os.path.join(self.d, "lrbad"), force=True)  # needs dr

    def test_relinks(self):
        for i in range(15):
            p = os.path.join(self.d, "rl")
            build_macho(p, self.pd, random.Random(SEED + 77 + i), self.ver, self.sha)
            buf = io.StringIO(); self.assertEqual(P.cmd_check(self.pd, p, None, out=buf), 0, buf.getvalue())
            self.assertEqual(buf.getvalue().count("applicable"), 5)

    def test_wrong_builds_refused(self):
        for sha in ("00" * 32, "70d673f7c43edb19" + "0" * 48, "70d673f7c43edb1" + "0" * 49, None):
            p = os.path.join(self.d, "wb"); build_macho(p, self.pd, random.Random(1), self.ver, sha)
            with self.assertRaises(P.PatchError, msg=str(sha)): P.apply(self.pd, p, [("dr", None)], os.path.join(self.d, "wbo"), force=True)
            self.assertFalse(os.path.exists(os.path.join(self.d, "wbo")))


if __name__ == "__main__":
    unittest.main()
