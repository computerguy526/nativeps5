#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/metal.py (Metal shader generation from a keys-only list) and its use by `nps5 cache prepare`
/ `nps5 cache metal` and the launch words. A synthetic programs.bin and a fake kyty_msl_dump; no title, no GPU, no game data."""
import hashlib, io, os, shutil, struct, sys, tempfile, unittest, warnings
warnings.simplefilter("ignore", ResourceWarning)
from contextlib import redirect_stdout, redirect_stderr
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib"))
sys.path.insert(0, HERE)
import metal  # noqa: E402
import cache  # noqa: E402
import test_cache  # noqa: E402

HDR = "// kk-mslinject v1 local_size 64 1 1 entry main_entrypoint math safe\n"
# fake kyty_msl_dump: text = header + "perm N" (+ the env switch it must never see); perm 3 is incomplete, perm 4 fails
DUMP = """#!/bin/bash
while [ $# -gt 0 ]; do case $1 in --perm) p=$2; shift;; --out) o=$2; shift;; --mode) m=$2; shift;; esac; shift; done
[ "$p" = 4 ] && exit 3
[ "$p" = 3 ] && { echo "// complete=0 unsupported: X" >&2; printf "$HDR" > $o; exit 0; }
printf "$HDRperm %s %s%s\\n" "$p" "${KYTY_MSL_LDS_FENCE:-}" "${m:+ mode=$m}" > $o
echo "// complete=1 unsupported:" >&2
"""


def rec(kind, payload):
    return struct.pack("<IIQQ", metal.KPRC, kind, len(payload), 0) + payload   # 24-byte record header


def perm_rec(digest, spec, cursor=0):
    return rec(2, digest + struct.pack("<I", cursor) + struct.pack("<I", len(spec)) + spec + b"\0" * 8)


def programs(path, perms):
    """perms: [(digest16, spec)]; a source record (kind 1) first, which does not count as a permutation."""
    d = b"\0" * 16 + struct.pack("<Q", 3) + b"abc" + b"\0" * 8 + rec(1, b"\x11" * 40)
    for dg, sp in perms:
        d += perm_rec(dg, sp)
    open(path, "wb").write(d)


def b_text(p):
    return (HDR + "perm %d \n" % p).encode()


def row(dg, sp, kk, text, ev="exact"):
    return "%s\t%s\t%s\t%s\t64x1x1\t%s" % (dg.hex(), metal.spec_key(sp), kk, hashlib.sha256(text).hexdigest(), ev)


DG = [bytes([i]) * 16 for i in range(6)]
SP = [b"spec%d" % i for i in range(6)]


def keys_list(rows):
    return "# test\nformat\tnps5-metal-keys\t1\ntitle\tPPSA00001\t01.001.000\nemitter\tabc\n" + "\n".join(rows) + "\n"


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dump = os.path.join(self.tmp, "kyty_msl_dump"); open(self.dump, "w").write(DUMP.replace("$HDR", HDR.replace("\n", "\\n")))
        os.chmod(self.dump, 0o755)
        self.env = dict(PATH=os.environ["PATH"], HOME=self.tmp, NPS5_MSL_TOOL=self.dump, KYTY_MSL_LDS_FENCE="0")
        self.prog = os.path.join(self.tmp, "X.programs.bin")
        programs(self.prog, list(zip(DG, SP)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_list(self, rows):
        p = os.path.join(self.tmp, "metal-shaders.tsv"); open(p, "w").write(keys_list(rows)); return p

    def gen(self, rows, **kw):
        out = io.StringIO()
        st = metal.generate(self.write_list(rows), self.prog, os.path.join(self.tmp, "c", "metal-shaders"), self.env, out=out, **kw)
        return st, out.getvalue()

    # ---- list
    def test_parse_list_rejects_malformed_rows_and_duplicates(self):
        ok = row(DG[0], SP[0], "a" * 16, b"x")
        meta, rows = metal.parse_list(keys_list([ok]))
        self.assertEqual((meta["format"], len(rows), rows[0]["local"]), (("nps5-metal-keys", "1"), 1, "64x1x1"))
        for bad in [ok.replace("\texact", "\tEXACT"), ok[:-40], ok + "\textra", "deadbeef\t" + ok]:
            with self.assertRaises(metal.MetalError):
                metal.parse_list(keys_list([bad]))
        with self.assertRaises(metal.MetalError):
            metal.parse_list(keys_list([ok, row(DG[1], SP[1], "a" * 16, b"y")]))
        self.assertEqual(len(metal.parse_list(keys_list([ok, row(DG[1], SP[1], "a" * 16, b"x")]))[1]), 2)   # shared kernel, same text
        with self.assertRaises(metal.MetalError):
            metal.parse_list("title\tX\n" + ok + "\n")                       # no format line

    def test_read_perms_indexes_permutations_not_source_records(self):
        p = metal.read_perms(self.prog)
        self.assertEqual(p[(DG[2].hex(), metal.spec_key(SP[2]))], 2)
        self.assertEqual(len(p), 6)
        open(self.prog, "ab").write(b"\x01\x02")                             # torn tail: ignored
        self.assertEqual(len(metal.read_perms(self.prog)), 6)

    # ---- generation
    def test_generate_accepts_only_the_verified_text(self):
        rows = [row(DG[0], SP[0], "0" * 16, b_text(0)), row(DG[1], SP[1], "1" * 16, b"other text"),
                row(DG[3], SP[3], "3" * 16, b_text(3)), row(DG[4], SP[4], "4" * 16, b_text(4)),
                row(b"\xee" * 16, SP[0], "9" * 16, b_text(0)), row(DG[5], SP[5], "5" * 16, b_text(5))]
        st, out = self.gen(rows, seen={"0" * 16, "f" * 16})
        d = os.path.join(self.tmp, "c", "metal-shaders")
        self.assertEqual((st["listed"], st["written"], st["seen"]), (6, 2, 1))
        self.assertEqual(sorted(f for f in os.listdir(d) if f.endswith(".metal")), ["0" * 16 + ".metal", "5" * 16 + ".metal"])
        self.assertEqual(open(os.path.join(d, "0" * 16 + ".metal"), "rb").read(), b_text(0))   # KYTY_* env did not reach the tool
        self.assertEqual(st["reasons"], {"text differs from the verified kernel": 1, "incomplete translation": 1, "dump failed rc=3": 1,
                                         "not in programs.bin": 1})
        self.assertEqual(len(open(os.path.join(d, "skipped.tsv")).read().splitlines()), 4)
        self.assertFalse([f for f in os.listdir(d) if f.endswith(".part")])
        self.assertIn("Metal shaders: 6/6 (100%)", out)
        self.assertEqual(metal.index_rows(d), (st["list_sha"], 2))

    def test_regeneration_reuses_unchanged_text_without_running_the_tool(self):
        rows = [row(DG[0], SP[0], "0" * 16, b_text(0)), row(DG[2], SP[2], "2" * 16, b_text(2))]
        st, _ = self.gen(rows)
        self.assertEqual((st["written"], st["reused"]), (2, 0))
        calls = []
        def dump(tool, programs, perm, out, env):
            calls.append(perm); return metal.run_dump(tool, programs, perm, out, env)
        st, out = self.gen(rows + [row(DG[5], SP[5], "5" * 16, b_text(5))], dump=dump)
        self.assertEqual(len(calls), 1)                                          # only the new key was emitted
        self.assertEqual((st["written"], st["reused"]), (3, 2))
        self.assertIn("2 unchanged from an earlier prepare", metal.summary(st))
        d = os.path.join(self.tmp, "c", "metal-shaders")
        open(os.path.join(d, "0" * 16 + ".metal"), "ab").write(b"tampered")      # a changed file is never trusted: regenerated
        calls.clear()
        st, _ = self.gen(rows, dump=dump)
        self.assertEqual((len(calls), st["reused"]), (1, 1))
        self.assertEqual(open(os.path.join(d, "0" * 16 + ".metal"), "rb").read(), b_text(0))

    def test_two_permutations_of_one_kernel(self):
        st, _ = self.gen([row(DG[0], SP[0], "0" * 16, b_text(0)), row(DG[0], SP[0], "0" * 16, b_text(0)), row(DG[2], SP[2], "2" * 16, b_text(2))])
        self.assertEqual((st["listed"], st["written"]), (2, 2))
        self.assertEqual(metal.index_rows(os.path.join(self.tmp, "c", "metal-shaders"))[1], 2)

    def test_generate_replaces_the_old_dir_and_needs_tool_and_programs(self):
        self.gen([row(DG[0], SP[0], "0" * 16, b_text(0))])
        st, _ = self.gen([row(DG[2], SP[2], "2" * 16, b_text(2))])
        d = os.path.join(self.tmp, "c", "metal-shaders")
        self.assertEqual(sorted(f for f in os.listdir(d) if f.endswith(".metal")), ["2" * 16 + ".metal"])
        self.assertFalse([f for f in os.listdir(os.path.dirname(d)) if f.startswith(".metal-new")])
        self.env["NPS5_MSL_TOOL"] = os.path.join(self.tmp, "nope")
        with self.assertRaises(metal.MetalError):
            self.gen([row(DG[0], SP[0], "0" * 16, b_text(0))])
        self.env["NPS5_MSL_TOOL"] = self.dump; os.remove(self.prog)
        with self.assertRaises(metal.MetalError):
            self.gen([row(DG[0], SP[0], "0" * 16, b_text(0))])

    def test_launch_words_follow_list_and_switch(self):
        lp = self.write_list([row(DG[0], SP[0], "0" * 16, b_text(0))])
        cdir = os.path.join(self.tmp, "c")
        self.assertEqual(metal.launch_words(cdir, lp, self.env), ([], None))     # nothing generated yet
        metal.generate(lp, self.prog, os.path.join(cdir, "metal-shaders"), self.env, out=io.StringIO())
        self.assertEqual(metal.launch_words(cdir, lp, self.env)[0], [("KK_MSL_OVERRIDE_DIR", os.path.join(cdir, "metal-shaders"))])
        self.assertEqual(metal.launch_words(cdir, lp, dict(self.env, NPS5_METAL_SHADERS="0")), ([], None))
        open(lp, "a").write("# edited\n")
        w, warn = metal.launch_words(cdir, lp, self.env)
        self.assertEqual(w, []); self.assertIn("another metal-shaders.tsv", warn)

    def test_log_parsers(self):
        log = ("[kk-mslinject] seen 00000000000000aa len=5\n[kk-mslinject] seen 00000000000000bb len=5\n"
               "[kk-mslinject] override 00000000000000aa local_size 64 1 1 (KK 32 1 1) 3.0 ms\n"
               "[kk-mslinject] override 00000000000000bb FAILED to compile: KK kernel kept\n")
        self.assertEqual(metal.seen_keys(log), {"00000000000000aa", "00000000000000bb"})
        self.assertEqual(metal.override_counts(log), (1, 1))
        self.assertTrue(metal.enabled({})); self.assertFalse(metal.enabled({"NPS5_METAL_SHADERS": "off"}))

    def test_canon_switch_and_launch_word(self):                 # v1.2.2 oskey: default off, the word only with the switch
        self.assertFalse(metal.canon_enabled({})); self.assertFalse(metal.canon_enabled({"NPS5_METAL_CANON": "0"}))
        self.assertTrue(metal.canon_enabled({"NPS5_METAL_CANON": "1"})); self.assertTrue(metal.canon_enabled({"NPS5_METAL_CANON": " On "}))
        lp = self.write_list([row(DG[0], SP[0], "0" * 16, b_text(0))])
        cdir = os.path.join(self.tmp, "c")
        metal.generate(lp, self.prog, os.path.join(cdir, "metal-shaders"), self.env, out=io.StringIO())
        d = os.path.join(cdir, "metal-shaders")
        self.assertEqual(metal.launch_words(cdir, lp, self.env)[0], [("KK_MSL_OVERRIDE_DIR", d)])
        self.assertEqual(metal.launch_words(cdir, lp, dict(self.env, NPS5_METAL_CANON="1"))[0],
                         [("KK_MSL_OVERRIDE_DIR", d), ("KK_MSL_CANON_KEY", "1")])

    def test_canon_log_lines_are_parsed(self):
        # a macOS 27 / M6 Mac: its own key differs, the canonical key (the lists' key) is on the same line
        log = ("[kk-mslinject] seen 00000000000000a1 len=9 compute canon=00000000000000c1\n"
               "[kk-mslinject] seen 00000000000000b1 len=7 vertex canon=00000000000000c2\n"
               "[kk-mslinject] seen 00000000000000b2 len=8 fragment canon=00000000000000c3\n"
               "[kk-mslinject] seen 00000000000000d1 len=4 compute\n")                      # no canonical key (switch off for it)
        self.assertEqual(metal.seen_keys(log), {"00000000000000a1", "00000000000000b1", "00000000000000b2", "00000000000000d1",
                                                "00000000000000c1", "00000000000000c2", "00000000000000c3"})
        self.assertEqual(metal.seen_canon_aliases(log), {"00000000000000c1": "00000000000000a1", "00000000000000c2": "00000000000000b1",
                                                         "00000000000000c3": "00000000000000b2"})
        self.assertEqual(metal.seen_vertex_lens(log), {"00000000000000b1": 7})              # the real key, as before
        self.assertEqual(metal.seen_canon_aliases("[kk-mslinject] seen 00000000000000a1 len=9 compute\n"), {})


GDUMP = """#!/bin/bash
# fake kyty_msl_dump for graphics: the fragment text is first (declares its reads), the vertex text needs --link-ps and the reads + KK outputs + layout words
while [ $# -gt 0 ]; do case $1 in --perm) p=$2; shift;; --out) o=$2; shift;; --link-ps) lp=$2; shift;; --mode) m=$2; shift;; esac; shift; done
[ "$p" = 4 ] && exit 3
if [ -n "$lp" ]; then
  [ "$KYTY_MSL_KK_PS_READS" = "1:15" ] || { echo "no ps reads" >&2; exit 5; }
  printf "// kk-mslinject v1 stage vertex entry main0 math safe\\nvs %s outs=%s set0=%s va=%s cm=%s plain=%s%s\\n" "$p" "$KYTY_MSL_KK_VS_OUTS" "$KYTY_MSL_KK_SET0" "$KYTY_MSL_KK_VA" "$KYTY_MSL_KK_COLOR_MASK" "${KYTY_MSL_MIPSTATS_PLAIN:-0}" "${m:+ mode=$m}" > $o
else
  printf "// kk-mslinject v1 stage fragment entry main0 math safe\\n// kk-bda ps_reads 1:15\\nps %s plain=%s%s%s\\n" "$p" "${KYTY_MSL_MIPSTATS_PLAIN:-0}" "${KYTY_MSL_KK_BLEND:+ blend=$KYTY_MSL_KK_BLEND}" "${m:+ mode=$m}" > $o
fi
echo "// complete=1 unsupported:" >&2
"""
KKVS = "struct VertexOut {\n    float4 position [[position]];\n    float4 vary_00 [[user(vary_00)]];\n};\n"


def gfx_text(vs, ps, outs="0:4", set0="0:0,58:16", va="0:0:109", cm="1", plain="1", blend=""):
    v = "// kk-mslinject v1 stage vertex entry main0 math safe\nvs %d outs=%s set0=%s va=%s cm=%s plain=%s\n" % (vs, outs, set0, va, cm, plain)
    f = "// kk-mslinject v1 stage fragment entry main0 math safe\n// kk-bda ps_reads 1:15\nps %d plain=%s%s\n" % (ps, plain, " blend=" + blend if blend else "")
    return v.encode(), f.encode()


def gfx_row(vs, ps, kk_vs, kk_fs, texts, variant="twin", set0="0:0,58:16", va="0:0:109", cm="1", ev="exact"):
    v, f = texts
    return "\t".join(["gfx", DG[vs].hex(), metal.spec_key(SP[vs]), DG[ps].hex(), metal.spec_key(SP[ps]), kk_vs, kk_fs,
                      hashlib.sha256(v).hexdigest(), hashlib.sha256(f).hexdigest(), variant, set0, va, cm, ev])


def gfx_list(rows):
    return "# test\nformat\tnps5-metal-gfx-pairs\t1\ntitle\tPPSA00001\t01.001.000\nemitter\tabc\n" + "\n".join(rows) + "\n"


class Gfx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dump = os.path.join(self.tmp, "kyty_msl_dump"); open(self.dump, "w").write(GDUMP); os.chmod(self.dump, 0o755)
        self.env = dict(PATH=os.environ["PATH"], HOME=self.tmp, NPS5_MSL_TOOL=self.dump, KYTY_MSL_KK_SET0="evil")
        self.prog = os.path.join(self.tmp, "X.programs.bin")
        # 6 digests, each of them twice as digest16*2 = 32 hex in the list: the rows use DG[i]*2, programs.bin carries 16-byte digests
        programs(self.prog, [(DG[i] * 1, SP[i]) for i in range(6)])
        self.kkdir = os.path.join(self.tmp, "kk", "msl"); os.makedirs(self.kkdir)
        self.kk_vs = metal.blake3_64(KKVS.encode())
        open(os.path.join(self.kkdir, "opaque-cache-name.metal"), "w").write(KKVS)
        open(os.path.join(self.kkdir, "other.metal"), "w").write(KKVS + "// another\n")
        self.out = os.path.join(self.tmp, "c", "metal-shaders")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lst(self, rows):
        p = os.path.join(self.tmp, "metal-gfx-pairs.tsv"); open(p, "w").write(gfx_list(rows)); return p

    def gen(self, rows, **kw):
        return metal.generate_gfx(self.lst(rows), self.prog, self.kkdir, self.out, self.env, out=io.StringIO(), **kw)

    def test_find_kk_vertex_hashes_each_file_once_for_all_keys(self):
        other = KKVS + "// second vertex\n"
        k2 = metal.blake3_64(other.encode())
        open(os.path.join(self.kkdir, "second.metal"), "w").write(other)
        got = metal.find_kk_vertex(self.kkdir, {self.kk_vs, k2, "f" * 16})
        self.assertEqual(sorted(got), sorted([self.kk_vs, k2]))
        sized = metal.find_kk_vertex(self.kkdir, {self.kk_vs, k2}, {self.kk_vs: len(KKVS), k2: len(other)})
        self.assertEqual(sorted(sized), sorted([self.kk_vs, k2]))
        self.assertEqual(metal.find_kk_vertex(self.kkdir, {k2}, {k2: 5}), {})           # a wrong logged length excludes the file

    def test_find_kk_vertex_by_the_canonical_key(self):
        # the list names the canonical key; this Mac's own vertex text hashes to another key (the log line's alias)
        own = KKVS + "// macOS 27: no NaN guard\n"
        open(os.path.join(self.kkdir, "own.metal"), "w").write(own)
        k_own = metal.blake3_64(own.encode()); canon = "c" * 16
        self.assertEqual(metal.find_kk_vertex(self.kkdir, {canon}), {})                       # no alias: not found (v1.2.1 behaviour)
        got = metal.find_kk_vertex(self.kkdir, {canon}, {k_own: len(own)}, {canon: k_own})
        self.assertEqual(list(got), [canon]); self.assertEqual(open(got[canon]).read(), own)
        got = metal.find_kk_vertex(self.kkdir, {canon, self.kk_vs}, None, {canon: k_own})     # keys without an alias are looked up as before
        self.assertEqual(sorted(got), sorted([canon, self.kk_vs]))

    def test_blake3_matches_the_reference(self):
        self.assertEqual(metal.blake3_64(b""), "af1349b9f5f9a1a6")
        b3 = shutil.which("b3sum")
        if b3:
            for n in (1, 64, 65, 1024, 1025, 2049, 5000, 70000):
                d = bytes(i * 7 % 251 for i in range(n))
                ref = __import__("subprocess").run([b3, "--no-names"], input=d, capture_output=True).stdout.decode()[:16]
                self.assertEqual(metal.blake3_64(d), ref, n)

    def test_pair_is_generated_linked_and_verified(self):
        t = gfx_text(1, 2)
        st = self.gen([gfx_row(1, 2, self.kk_vs, "f" * 16, t)])
        self.assertEqual((st["listed"], st["written"], st["skipped"]), (1, 1, 0), st)
        pair = "%s_%s" % (self.kk_vs, "f" * 16)
        self.assertEqual(open(os.path.join(self.out, pair + ".vs.metal"), "rb").read(), t[0])
        self.assertEqual(open(os.path.join(self.out, pair + ".fs.metal"), "rb").read(), t[1])
        sha, n = metal.gfx_index_rows(self.out)
        self.assertEqual(n, 1)
        self.assertEqual(sha, hashlib.sha256(open(os.path.join(self.tmp, "metal-gfx-pairs.tsv"), "rb").read()).hexdigest())

    def test_any_mismatch_skips_the_whole_pair(self):
        t = gfx_text(1, 2)
        bad = gfx_text(1, 2, cm="3")                          # the list says another layout: our text differs from the verified one
        st = self.gen([gfx_row(1, 2, self.kk_vs, "f" * 16, bad), gfx_row(1, 3, self.kk_vs, "e" * 16, t),
                       gfx_row(1, 2, "9" * 16, "d" * 16, t), gfx_row(4, 2, self.kk_vs, "c" * 16, t)])
        self.assertEqual(st["written"], 0, st)
        self.assertEqual(sorted(st["reasons"]), ["KosmicKrisp vertex text not in the library cache", "text differs from the verified pair",
                                                 "vertex translation failed"])
        self.assertEqual([f for f in os.listdir(self.out) if f.endswith(".metal")], [])

    def test_second_prepare_without_kk_texts_keeps_the_pairs(self):
        t = gfx_text(1, 2)
        rows = [gfx_row(1, 2, self.kk_vs, "f" * 16, t)]
        self.assertEqual(self.gen(rows)["written"], 1)
        shutil.rmtree(self.kkdir); os.makedirs(self.kkdir)    # a later prepare loads metallibs: KosmicKrisp writes no MSL text
        st = self.gen(rows)
        self.assertEqual((st["written"], st["skipped"]), (1, 0), st)
        pair = "%s_%s" % (self.kk_vs, "f" * 16)
        self.assertEqual(open(os.path.join(self.out, pair + ".vs.metal"), "rb").read(), t[0])

    def test_row_is_checked_with_the_list_numbers_and_the_variant(self):
        t = gfx_text(1, 2, outs="0:4", set0="0:0", va="1:0:103,2:0:109", cm="3", plain="0")
        st = self.gen([gfx_row(1, 2, self.kk_vs, "f" * 16, t, variant="mipstats", set0="0:0", va="1:0:103,2:0:109", cm="3")])
        self.assertEqual(st["written"], 1, st)

    def test_regeneration_drops_pairs_of_another_list(self):
        t = gfx_text(1, 2)
        self.gen([gfx_row(1, 2, self.kk_vs, "f" * 16, t)])
        st = self.gen([gfx_row(1, 2, self.kk_vs, "a" * 16, t)])
        self.assertEqual(sorted(f for f in os.listdir(self.out) if f.endswith(".metal")),
                         ["%s_%s.fs.metal" % (self.kk_vs, "a" * 16), "%s_%s.vs.metal" % (self.kk_vs, "a" * 16)])
        self.assertEqual(st["written"], 1)

    def test_list_rules(self):
        t = gfx_text(1, 2)
        r = gfx_row(1, 2, self.kk_vs, "f" * 16, t)
        with self.assertRaises(metal.MetalError):
            metal.parse_gfx_list(gfx_list([r.replace("\ttwin\t", "\tgreen\t")]))
        with self.assertRaises(metal.MetalError):
            metal.parse_gfx_list(gfx_list([r + "\textra"]))
        with self.assertRaises(metal.MetalError):                          # one pair, two different texts
            metal.parse_gfx_list(gfx_list([r, r.replace(hashlib.sha256(t[0]).hexdigest(), "0" * 64)]))
        with self.assertRaises(metal.MetalError):
            metal.parse_gfx_list("format\tnps5-metal-keys\t1\n" + r + "\n")

    def test_launch_words_need_both_current_lists(self):
        t = gfx_text(1, 2)
        lp = os.path.join(self.tmp, "metal-shaders.tsv"); open(lp, "w").write("")
        self.gen([gfx_row(1, 2, self.kk_vs, "f" * 16, t)])
        w, warn = metal.launch_words(os.path.join(self.tmp, "c"), lp, self.env)
        self.assertEqual((w, warn), ([("KK_MSL_OVERRIDE_DIR", self.out)], None))
        open(os.path.join(self.tmp, "metal-gfx-pairs.tsv"), "a").write("# changed\n")
        w, warn = metal.launch_words(os.path.join(self.tmp, "c"), lp, self.env)
        self.assertEqual(w, []); self.assertIn("metal-gfx-pairs.tsv", warn)

    def test_the_numbers_of_the_real_list_parse(self):
        p = os.path.join(HERE, "..", "games", "PPSA21564", metal.GFX_LIST_NAME)
        if os.path.isfile(p):
            meta, rows = metal.parse_gfx_list(open(p).read())     # v1.1 ships it header-only (no verified pairs yet)
            self.assertEqual(meta["title"][0], "PPSA21564")
            self.assertTrue(all(len(r["b_vs"]) == 64 and len(r["b_fs"]) == 64 for r in rows))


class Prepare(unittest.TestCase):
    kktext = "src"
    """`nps5 cache prepare` with a keys list: pass 1 logs keys, generation runs between the passes, pass 2 compiles the kernels
    synchronously, the dir survives pass 2's fresh cache, prepared.tsv and launch words carry them; NPS5_METAL_SHADERS=0 = v1.0."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.env = test_cache.make(self.tmp)
        dump = os.path.join(self.tmp, "kyty_msl_dump"); open(dump, "w").write(DUMP.replace("$HDR", HDR.replace("\n", "\\n"))); os.chmod(dump, 0o755)
        self.env["NPS5_MSL_TOOL"] = dump
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        open(os.path.join(self.tmp, "games", "PPSA00001", "metal-shaders.tsv"), "w").write(
            keys_list([row(DG[0], SP[0], "0" * 16, b_text(0)), row(DG[1], SP[1], "1" * 16, b_text(1))]))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prepare(self, extra=None):
        env = dict(self.env, **(extra or {}))
        c = cache.context("PPSA00001", dict(env)); os.makedirs(c.dir)
        lcd = os.path.join(c.dir, "kk-libcache"); runs = []
        rd = os.path.join(env["NPS5_SCRATCH"], "runs", "PPSA00001", "cache-prepare")

        def runner(cmd, env=None, capture=False):
            if capture: return 0, "  shaders 1\n  permutations 2, pipelines 3\n"
            if cmd[0] == "bash":
                os.makedirs(lcd + "/lib", exist_ok=True); open(lcd + "/lib/k1.metallib", "w").write("m"); return 0
            words = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--env"]
            runs.append(dict(w.split("=", 1) for w in words))
            os.makedirs(rd, exist_ok=True)
            if len(runs) == 1:
                os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/k1.metal", "w").write(self.kktext)
                programs(os.path.join(c.dir, "PPSA00001.programs.bin"), list(zip(DG, SP)))
                open(os.path.join(rd, "run.err"), "w").write("[kk-mslinject] seen %s len=1\n" % ("0" * 16))
            elif len(runs) == 2:
                runs[-1]["_dir_ok"] = os.path.isfile(os.path.join(c.dir, "metal-shaders", "0" * 16 + ".metal"))
                open(os.path.join(rd, "run.err"), "w").write("[kk-mslinject] override %s local_size 64 1 1 (KK 64 1 1) 2.0 ms\n" % ("0" * 16))
            return 0
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cache.main(["prepare", "PPSA00001"], dict(env), runner)
        return rc, c, runs, out.getvalue()

    def test_prepare_generates_between_passes(self):
        rc, c, runs, out = self.prepare()
        self.assertEqual(rc, 0, out)
        self.assertEqual(runs[0].get("KK_MSL_OVERRIDE_LOG"), "1")
        self.assertNotIn("KK_MSL_OVERRIDE_DIR", runs[0])
        self.assertEqual((runs[1].get("KK_MSL_OVERRIDE_ASYNC"), runs[1]["_dir_ok"]), ("0", True))
        self.assertEqual(runs[1]["KK_MSL_OVERRIDE_DIR"], os.path.join(c.dir, "metal-shaders"))
        self.assertEqual(runs[2].get("KK_MSL_OVERRIDE_DIR"), os.path.join(c.dir, "metal-shaders"))   # warm start: launch words
        self.assertNotIn("KK_MSL_OVERRIDE_ASYNC", runs[2])
        kv = cache.read_kv(os.path.join(c.dir, "prepared.tsv"))
        self.assertEqual((kv["metal_shaders"], kv["metal_seen"], kv["metal_active"], kv["metal_failed"]), ("2/2", "1", "1", "0"))
        self.assertIn("Metal shaders: 2 of 2 verified kernels generated", out)

    def test_prepare_opt_out_is_v10(self):
        rc, c, runs, out = self.prepare({"NPS5_METAL_SHADERS": "0"})
        self.assertEqual(rc, 0, out)
        self.assertFalse(any(k.startswith("KK_MSL_OVERRIDE") for r in runs for k in r))
        self.assertFalse(os.path.exists(os.path.join(c.dir, "metal-shaders")))
        self.assertNotIn("metal_shaders", cache.read_kv(os.path.join(c.dir, "prepared.tsv")))

    def test_prepare_without_tool_still_prepares(self):
        rc, c, runs, out = self.prepare({"NPS5_MSL_TOOL": os.path.join(self.tmp, "none")})
        self.assertEqual(rc, 0, out)
        self.assertIn("Metal shaders: skipped", out)
        self.assertFalse(any("KK_MSL_OVERRIDE_DIR" in r for r in runs))
        self.assertEqual(cache.read_kv(os.path.join(c.dir, "prepared.tsv"))["metal_shaders"], "0/0")

    def test_header_only_gfx_list_is_no_graphics_step(self):
        # v1.1 ships games/<id>/metal-gfx-pairs.tsv without pair rows: compute as before, no graphics step and no gfx fields
        open(os.path.join(self.tmp, "games", "PPSA00001", "metal-gfx-pairs.tsv"), "w").write(gfx_list([]))
        rc, c, runs, out = self.prepare()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Metal graphics", out)
        self.assertIn("Metal shaders: 2 of 2 verified kernels generated", out)
        self.assertEqual(cache.read_kv(os.path.join(c.dir, "prepared.tsv"))["metal_gfx"], "0/0")
        self.assertFalse(os.path.exists(os.path.join(c.dir, "metal-shaders", "index-gfx.tsv")))


class GfxPrepare(Prepare):
    """The same flow with a graphics pairs list only: the vertex text comes from the pass-1 library cache, the pair is generated between
    the passes, pass 2 compiles it (log line 'gfx override'), prepared.tsv records it and the launch words point at the dir."""
    kktext = KKVS

    def setUp(self):
        Prepare.setUp(self)
        gd = os.path.join(self.tmp, "games", "PPSA00001")
        os.remove(os.path.join(gd, "metal-shaders.tsv"))
        dump = os.path.join(self.tmp, "kyty_msl_dump"); open(dump, "w").write(GDUMP); os.chmod(dump, 0o755)
        self.kk_vs = metal.blake3_64(KKVS.encode())
        open(os.path.join(gd, "metal-gfx-pairs.tsv"), "w").write(gfx_list([gfx_row(0, 1, self.kk_vs, "f" * 16, gfx_text(0, 1))]))

    def test_prepare_generates_pairs_between_passes(self):
        rc, c, runs, out = self.prepare()
        self.assertEqual(rc, 0, out)
        self.assertEqual(runs[0].get("KK_MSL_OVERRIDE_LOG"), "1")
        self.assertEqual(runs[1]["KK_MSL_OVERRIDE_DIR"], os.path.join(c.dir, "metal-shaders"))
        self.assertEqual(runs[2].get("KK_MSL_OVERRIDE_DIR"), os.path.join(c.dir, "metal-shaders"))
        self.assertTrue(os.path.isfile(os.path.join(c.dir, "metal-shaders", "%s_%s.vs.metal" % (self.kk_vs, "f" * 16))))
        kv = cache.read_kv(os.path.join(c.dir, "prepared.tsv"))
        self.assertEqual((kv["metal_gfx"], kv["metal_shaders"]), ("1/1", "0/0"))
        self.assertIn("Metal graphics: 1 of 1 verified vertex+fragment pairs generated", out)

    def test_second_prepare_keeps_pairs_without_kk_texts(self):
        rc, c, runs, out = self.prepare()
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(c.root + "." + metal.KK_VS_KEEP, self.kk_vs + ".metal")))
        shutil.rmtree(c.dir)
        self.kktext = "// metallibs loaded: KosmicKrisp wrote no vertex text\n"
        rc, c, runs, out = self.prepare()
        self.assertEqual(rc, 0, out)
        kv = cache.read_kv(os.path.join(c.dir, "prepared.tsv"))
        self.assertEqual(kv["metal_gfx"], "1/1", out)

    test_prepare_generates_between_passes = None
    test_prepare_opt_out_is_v10 = None
    test_prepare_without_tool_still_prepares = None
    test_header_only_gfx_list_is_no_graphics_step = None



def gfx2_row(vs_spec, ps, kk_vs, kk_fs, texts, blend, variant="twin", vs_dg=1):
    v, f = texts
    return "\t".join(["gfx", DG[vs_dg].hex(), vs_spec, DG[ps].hex(), metal.spec_key(SP[ps]), kk_vs, kk_fs, hashlib.sha256(v).hexdigest(),
                      hashlib.sha256(f).hexdigest(), variant, "0:0,58:16", "0:0:109", "1", blend, "fuzz2+mutant+play2"])


class Gfx2(unittest.TestCase):
    """v1.1.1 list format 2: the blend column (KosmicKrisp's in-shader blend) and the record's push-data cursor in a spec field."""
    setUp, tearDown, gen = Gfx.setUp, Gfx.tearDown, Gfx.gen

    def lst(self, rows, emitter="abc"):
        p = os.path.join(self.tmp, "metal-gfx-pairs.tsv")
        open(p, "w").write("# test\nformat\tnps5-metal-gfx-pairs\t2\ntitle\tPPSA00001\t01.001.000\nemitter\t%s\tv1.1.1\n" % emitter + "\n".join(rows) + "\n")
        return p

    def test_blend_column_reaches_the_emitter(self):
        bl = "1:1:6:0:1:0:0:7:f:4;0:0:0:0:0:0:0:15:u:2"
        st = self.gen([gfx2_row(metal.spec_key(SP[1]), 2, self.kk_vs, "f" * 16, gfx_text(1, 2, blend=bl), bl)])
        self.assertEqual(st["written"], 1, st)
        st = self.gen([gfx2_row(metal.spec_key(SP[1]), 2, self.kk_vs, "f" * 16, gfx_text(1, 2, blend=bl), "")])   # blend lost: text differs
        self.assertEqual((st["written"], list(st["reasons"])), (0, ["text differs from the verified pair"]), st)
        for bad in ("1:1:6;", "1:1:6:0:1:0:0:7:F:4", "evil text"):
            with self.assertRaises(metal.MetalError):
                metal.parse_gfx_list(open(self.lst([gfx2_row(metal.spec_key(SP[1]), 2, self.kk_vs, "f" * 16, gfx_text(1, 2), bad)])).read())

    def test_cursor_selects_the_exact_record(self):
        # one program + specialization in three records that differ only in the push-data cursor (perms 6, 7, 8)
        d = open(self.prog, "rb").read()
        open(self.prog, "wb").write(d + perm_rec(DG[1], b"curspec", 5) + perm_rec(DG[1], b"curspec", 21) + perm_rec(DG[1], b"curspec", 18))
        sk = metal.spec_key(b"curspec")
        self.assertEqual(metal.read_perms(self.prog)[(DG[1].hex(), sk)], 6)                   # without a cursor: the first record
        self.assertEqual(metal.read_perms(self.prog, cursors=True)[(DG[1].hex(), sk, 18)], 8)
        self.assertEqual(metal.perm_key("aa", sk + ":18"), ("aa", sk, 18))
        st = self.gen([gfx2_row(sk + ":18", 2, self.kk_vs, "f" * 16, gfx_text(8, 2), "")])
        self.assertEqual(st["written"], 1, st)
        st = self.gen([gfx2_row(sk + ":21", 2, self.kk_vs, "f" * 16, gfx_text(8, 2), "")])   # verified with record 8, listed as 7
        self.assertEqual(st["written"], 0, st)

    def test_v12_list_counts_rows_as_v12_did(self):                         # review M2: "2 of 2", 2 index rows, list order
        bl = ""
        t = gfx_text(1, 2)
        r1 = gfx2_row(metal.spec_key(SP[1]), 2, self.kk_vs, "f" * 16, t, bl)
        st = self.gen([r1, r1.replace("fuzz2+mutant+play2", "autoval")])
        self.assertIn("2 of 2 verified vertex+fragment pairs generated", metal.gfx_summary(st))
        self.assertNotIn("GPU class", metal.gfx_summary(st))
        self.assertEqual(metal.gfx_index_rows(self.out)[1], 2)

    def test_format_1_lists_still_parse_and_unknown_formats_fail(self):
        meta, rows = metal.parse_gfx_list(gfx_list([gfx_row(1, 2, self.kk_vs, "f" * 16, gfx_text(1, 2))]))
        self.assertEqual((meta["format"], rows[0]["blend"]), (("nps5-metal-gfx-pairs", "1"), ""))
        with self.assertRaises(metal.MetalError):
            metal.parse_gfx_list(gfx_list([]).replace("pairs\t1", "pairs\t4"))

    def test_emitter_mismatch_is_reported(self):
        st = dict(reasons={"text differs from the verified pair": 2}, emitter=("a" * 40, "v1.1.1"))
        self.assertIn("your kyty_msl_dump is from Kyty bbbbbbbbbbbb", metal.emitter_problem(st, "b" * 40))
        self.assertIn("another compiler", metal.emitter_problem(st, "a" * 40))
        self.assertIsNone(metal.emitter_problem(dict(st, reasons={"not in programs.bin": 1}), "b" * 40))
        st = self.gen([gfx2_row(metal.spec_key(SP[1]), 2, self.kk_vs, "f" * 16, gfx_text(1, 2, cm="3"), "")])
        self.assertEqual(st["emitter"], ("abc", "v1.1.1"))
        self.assertIn("differ from the verified texts", metal.emitter_problem(st, "def"))

# ---------------------------------------------------------------- v1.2.1: rows per GPU class + emitter mode (keys format 2, pairs format 3)
def b_text_m(p, mode=""):
    return (HDR + "perm %d %s\n" % (p, " mode=" + mode if mode else "")).encode()


def crow(dg, sp, kk, text, mode="base", cls="*", rule="exact", verdict="faster", ba="0.900", ev="autoval"):
    b = hashlib.sha256(text).hexdigest() if text is not None else "-"
    return "%s\t%s\t%s\t%s\t64x1x1\t%s\t%s\t%s\t%s\t%s\t%s" % (dg.hex(), metal.spec_key(sp), kk, b, ev, mode, cls, rule, verdict, ba)


def keys_list2(rows):
    return "# test\nformat\tnps5-metal-keys\t2\ntitle\tPPSA00001\t01.001.000\nemitter\tabc\tv1.2.1\n" + "\n".join(rows) + "\n"


IOREG_M4 = '+-o AGXAcceleratorG16X  <class AGXAcceleratorG16X>\n    {\n      "model" = "Apple M4 Pro"\n      "IOClass" = "AGXAcceleratorG16X"\n      "gpu-core-count" = 20\n    }\n'
IOREG_M6 = IOREG_M4.replace("G16X", "G18G").replace("M4 Pro", "M6")


def fake_run(ioreg, osv):
    def run(argv, timeout=10):
        return ioreg if "ioreg" in argv[0] else osv + "\n" if "sysctl" in argv[0] else ""
    return run


class Classes(unittest.TestCase):
    """GPU class detection, the choice per key (most specific matching class, fallback, kk rows) and per-class generation."""
    setUp, tearDown = T.setUp, T.tearDown

    def gen2(self, rows, cls, **kw):
        p = os.path.join(self.tmp, "metal-shaders.tsv"); open(p, "w").write(keys_list2(rows))
        env = dict(self.env, NPS5_METAL_CLASS=cls, NPS5_DEV="1")
        st = metal.generate(p, self.prog, os.path.join(self.tmp, "c", "metal-shaders"), env, out=io.StringIO(), **kw)
        d = os.path.join(self.tmp, "c", "metal-shaders")
        return st, {f[:-6]: open(os.path.join(d, f), "rb").read() for f in os.listdir(d) if f.endswith(".metal")}, p, env

    def test_device_class_from_ioreg_and_os(self):
        m4 = metal.device_class({}, run=fake_run(IOREG_M4, "26.6.2"))
        self.assertEqual((m4["gen"], m4["var"], m4["os"], m4["model"]), ("g16", "", "26", "Apple M4 Pro"))
        self.assertEqual(metal.class_name(m4), "g16@os26")
        m6 = metal.device_class({"NPS5_GPU_ARCH": "applegpu_g18g"}, run=fake_run(IOREG_M6, "27.2.1"))
        self.assertEqual(metal.class_name(m6), "g18g@os27")
        self.assertEqual(metal.class_name(metal.device_class({"NPS5_GPU_ARCH": "applegpu_g16s"}, run=fake_run(IOREG_M6, "27.2"))), "g18@os27")  # another gen's letter is ignored
        self.assertIsNone(metal.device_class({}, run=fake_run("", "26.0")))          # no Apple GPU (VM): `*` rows only
        self.assertIsNone(metal.device_class({"NPS5_METAL_CLASS": "none", "NPS5_DEV": "1"}, run=fake_run(IOREG_M4, "26.6")))
        forced = metal.device_class({"NPS5_METAL_CLASS": "g18@os27", "NPS5_DEV": "1"}, run=fake_run(IOREG_M4, "26.6"))
        self.assertEqual((metal.class_name(forced), forced["note"]), ("g18@os27", "forced by NPS5_METAL_CLASS; this Mac is g16@os26"))
        # review M3: without the developer switch a stray NPS5_METAL_CLASS is ignored, and the summary says so
        stray = metal.device_class({"NPS5_METAL_CLASS": "g18@os27"}, run=fake_run(IOREG_M4, "26.6"))
        self.assertEqual(metal.class_name(stray), "g16@os26"); self.assertIn("ignored", stray["note"])
        self.assertEqual(metal.class_name(metal.parse_device("applegpu_g16s")), "g16s")
        for bad in ("m4", "g1", "g16@27", "applegpu"):
            self.assertIsNone(metal.parse_device(bad), bad)

    def test_class_score(self):
        dev = metal.parse_device("g16s@os26")
        self.assertEqual([metal.class_score(c, dev) for c in ("*", "g16", "g16s", "g16@os26", "g16s@os26", "@os26", "g18", "g16g", "@os27")],
                         [0, 4, 6, 5, 7, 1, None, None, None])
        self.assertEqual([metal.class_score(c, None) for c in ("*", "g16")], [0, None])
        nolet = metal.parse_device("g16@os26")
        self.assertIsNone(metal.class_score("g16s", nolet))                         # a letter row needs the letter known

    def test_choose_orders_options_most_specific_first(self):
        rows = [dict(kk="a", cls="*", mode="base"), dict(kk="a", cls="g16", mode="kk"), dict(kk="a", cls="g16", mode="fix6"),
                dict(kk="a", cls="g18", mode="base"), dict(kk="a", cls="g16@os26", mode="base"), dict(kk="b", cls="g18", mode="base")]
        got = metal.choose(rows, metal.parse_device("g16@os26"), lambda r: r["kk"])
        self.assertEqual([(o[0]["cls"], o[0]["mode"]) for o in got["a"]], [("g16@os26", "base"), ("g16", "fix6"), ("g16", "kk"), ("*", "base")])
        self.assertNotIn("b", got)
        self.assertEqual([o[0]["cls"] for o in metal.choose(rows, None, lambda r: r["kk"])["a"]], ["*"])

    def test_parse_rules(self):
        ok = crow(DG[0], SP[0], "a" * 16, b"x", mode="fix6+vsperf", cls="g16")
        self.assertEqual(metal.parse_list(keys_list2([ok]))[1][0]["mode"], "fix6+vsperf")
        for bad in (crow(DG[0], SP[0], "a" * 16, b"x", mode="KK"), crow(DG[0], SP[0], "a" * 16, b"x", cls="m4"),
                    crow(DG[0], SP[0], "a" * 16, b"x", mode="kk"),                       # kk with a text
                    crow(DG[0], SP[0], "a" * 16, None, mode="base"),                     # a text row without its hash
                    crow(DG[0], SP[0], "a" * 16, None, mode="kk", verdict="faster"),     # kk is for measured slower
                    crow(DG[0], SP[0], "a" * 16, b"x", ba="fast"), ok + "\textra", ok.replace("\tg16\t", "\tg16;rm\t")):
            with self.assertRaises(metal.MetalError, msg=bad):
                metal.parse_list(keys_list2([bad]))
        with self.assertRaises(metal.MetalError):                                    # one key + class, two texts
            metal.parse_list(keys_list2([ok, crow(DG[1], SP[1], "a" * 16, b"y", mode="fix6+vsperf", cls="g16")]))
        self.assertEqual(len(metal.parse_list(keys_list2([ok, crow(DG[1], SP[1], "a" * 16, b"y", cls="g18")]))[1]), 2)
        self.assertEqual(metal.parse_list(keys_list([row(DG[0], SP[0], "a" * 16, b"x")]))[1][0]["cls"], "*")   # format 1 = every Mac

    def mock_rows(self):
        """the mock table: key 0 has an M4 text (mode m4fast) and an M6 text (mode m6fast), key 1 only a `*` text,
        key 2 is faster on M6 only (KosmicKrisp on the M4: no row matches), key 3 `*` text but measured slower on M4 (kk row)."""
        return [crow(DG[0], SP[0], "0" * 16, b_text_m(0, "m4fast"), mode="m4fast", cls="g16", ba="0.880"),
                crow(DG[0], SP[0], "0" * 16, b_text_m(0, "m6fast"), mode="m6fast", cls="g18", ba="0.910"),
                crow(DG[1], SP[1], "1" * 16, b_text_m(1), cls="*", verdict="shipped", ba="-"),
                crow(DG[2], SP[2], "2" * 16, b_text_m(2, "xf=7"), mode="xf=7", cls="g18", ba="0.930"),
                crow(DG[5], SP[5], "5" * 16, b_text_m(5), cls="*", verdict="shipped"),
                crow(DG[5], SP[5], "5" * 16, None, mode="kk", cls="g16", verdict="slower", ba="1.176")]

    def test_m4_and_m6_get_different_texts(self):
        st4, f4, _, _ = self.gen2(self.mock_rows(), "g16@os26")
        st6, f6, _, _ = self.gen2(self.mock_rows(), "g18@os27")
        self.assertEqual(f4, {"0" * 16: b_text_m(0, "m4fast"), "1" * 16: b_text_m(1)})
        self.assertEqual(f6, {"0" * 16: b_text_m(0, "m6fast"), "1" * 16: b_text_m(1), "2" * 16: b_text_m(2, "xf=7"), "5" * 16: b_text_m(5)})
        self.assertEqual((st4["cls"], st4["kk_class"], st4["other_class"], st4["written"]), ("g16@os26", 1, 1, 2))
        self.assertRegex(metal.summary(st4), r"GPU class g16@os26 \(forced by NPS5_METAL_CLASS; this Mac is [^)]*\), 1 listed for other GPUs only, 1 KosmicKrisp's on this GPU")
        stn, fn, _, _ = self.gen2(self.mock_rows(), "none")                          # unknown GPU: the `*` rows only
        self.assertEqual(sorted(fn), ["1" * 16, "5" * 16])

    def test_fallback_to_the_class_agnostic_text_then_kk(self):
        rows = [crow(DG[0], SP[0], "0" * 16, b"not what this Mac emits", mode="m4fast", cls="g16"),
                crow(DG[0], SP[0], "0" * 16, b_text_m(0), cls="*", verdict="shipped"),
                crow(DG[2], SP[2], "2" * 16, b"wrong", mode="m4fast", cls="g16")]
        st, f, _, _ = self.gen2(rows, "g16@os26")
        self.assertEqual(f, {"0" * 16: b_text_m(0)})
        self.assertEqual((st["fallback"], st["reasons"]), (1, {"text differs from the verified kernel": 1}))

    def test_failed_class_text_falls_back_to_kk_when_star_is_slower_there(self):   # review L8
        rows = [crow(DG[0], SP[0], "0" * 16, b"not what this Mac emits", mode="m4fast", cls="g16"),
                crow(DG[0], SP[0], "0" * 16, None, mode="kk", cls="g16", verdict="slower", ba="1.100"),
                crow(DG[0], SP[0], "0" * 16, b_text_m(0), cls="*", verdict="shipped")]
        st, f, _, _ = self.gen2(rows, "g16@os26")
        self.assertEqual((f, st["kk_class"]), ({}, 1))
        st, f, _, _ = self.gen2(rows, "g18@os27")                                 # other class: the `*` text
        self.assertEqual(f, {"0" * 16: b_text_m(0)})

    def test_index_records_the_class_and_launch_checks_it(self):
        st, _, lp, env = self.gen2(self.mock_rows(), "g16@os26")
        cdir = os.path.join(self.tmp, "c")
        self.assertEqual(metal.index_class(os.path.join(cdir, "metal-shaders"), "index.tsv"), "g16@os26")
        self.assertEqual(metal.launch_words(cdir, lp, env)[0], [("KK_MSL_OVERRIDE_DIR", os.path.join(cdir, "metal-shaders"))])
        w, warn = metal.launch_words(cdir, lp, dict(env, NPS5_METAL_CLASS="g16@os27"))   # macOS update: regenerate
        self.assertIn("forced", metal.summary(st))
        self.assertEqual(w, []); self.assertIn("this Mac is g16@os27", warn)
        self.assertEqual(metal.index_rows(os.path.join(cdir, "metal-shaders"))[1], 2)

    def test_v12_list_writes_the_v12_index(self):
        p = os.path.join(self.tmp, "metal-shaders.tsv"); open(p, "w").write(keys_list([row(DG[0], SP[0], "0" * 16, b_text(0))]))
        st = metal.generate(p, self.prog, os.path.join(self.tmp, "c", "metal-shaders"), dict(self.env, NPS5_METAL_CLASS="g16"), out=io.StringIO())
        idx = open(os.path.join(self.tmp, "c", "metal-shaders", "index.tsv")).read()
        self.assertNotIn("class", idx); self.assertIsNone(st["cls"]); self.assertNotIn("GPU class", metal.summary(st))

    def test_reproduce_checks_every_class(self):
        p = os.path.join(self.tmp, "metal-shaders.tsv"); open(p, "w").write(keys_list2(self.mock_rows()))
        work = os.path.join(self.tmp, "w"); os.makedirs(work)
        res = metal.reproduce(p, None, self.prog, None, self.env, work, out=io.StringIO())
        self.assertEqual((res["rows"], res["reproduced"], res["failed"]), (5, 5, []))
        self.assertEqual(metal.reproduce_record(res)[0], "reproduced=5 of 5")
        open(p, "w").write(keys_list2(self.mock_rows() + [crow(DG[3], SP[3], "3" * 16, b_text_m(3, "m6fast"), mode="m6fast", cls="g18")]))
        res = metal.reproduce(p, None, self.prog, None, self.env, work, out=io.StringIO())
        self.assertEqual((res["rows"], res["reproduced"]), (6, 5))                   # perm 3 is an incomplete translation
        self.assertEqual(res["failed"], [("cs", "3" * 16, "g18", "incomplete translation")])

    def test_reproduce_a_v12_list(self):                                       # no class columns: every row is `*`
        p = os.path.join(self.tmp, "metal-shaders.tsv")
        open(p, "w").write(keys_list([row(DG[0], SP[0], "0" * 16, b_text(0)), row(DG[1], SP[1], "1" * 16, b_text(1))]))
        work = os.path.join(self.tmp, "w"); os.makedirs(work)
        res = metal.reproduce(p, None, self.prog, None, self.env, work, out=io.StringIO())
        self.assertEqual((res["rows"], res["reproduced"], res["per_class"]), (2, 2, {("cs", "*"): [2, 2]}))


def gfx3_row(vs, ps, kk_vs, kk_fs, texts, mode="base", cls="*", verdict="faster", ba="0.900"):
    r = gfx2_row(metal.spec_key(SP[vs]), ps, kk_vs, kk_fs, texts if texts else (b"", b""), "", vs_dg=vs).split("\t")
    if texts is None:
        r[7] = r[8] = "-"
    return "\t".join(r + [mode, cls, "exact", verdict, ba])


class GfxClasses(unittest.TestCase):
    setUp, tearDown, gen = Gfx.setUp, Gfx.tearDown, Gfx.gen

    def lst(self, rows):
        p = os.path.join(self.tmp, "metal-gfx-pairs.tsv")
        open(p, "w").write("# test\nformat\tnps5-metal-gfx-pairs\t3\ntitle\tPPSA00001\t01.001.000\nemitter\tabc\tv1.2.1\n" + "\n".join(rows) + "\n")
        return p

    def texts(self, vs, ps, mode=""):
        v, f = gfx_text(vs, ps)
        sfx = (" mode=" + mode).encode() if mode else b""
        return v[:-1] + sfx + b"\n", f[:-1] + sfx + b"\n"

    def rows(self):
        return [gfx3_row(1, 2, self.kk_vs, "f" * 16, self.texts(1, 2, "fix6+vsperf"), mode="fix6+vsperf", cls="g16"),
                gfx3_row(1, 2, self.kk_vs, "f" * 16, self.texts(1, 2, "fix6+xf=7"), mode="fix6+xf=7", cls="g18"),
                gfx3_row(1, 3, self.kk_vs, "e" * 16, self.texts(1, 3), cls="*", verdict="shipped", ba="-"),
                gfx3_row(1, 3, self.kk_vs, "e" * 16, None, mode="kk", cls="g18", verdict="slower", ba="1.050")]

    def files(self):
        return {f: open(os.path.join(self.out, f), "rb").read() for f in sorted(os.listdir(self.out)) if f.endswith(".metal")}

    def test_pairs_per_class(self):
        self.env.update(NPS5_METAL_CLASS="g16@os26", NPS5_DEV="1")
        st = self.gen(self.rows())
        f4 = self.files()
        self.assertEqual((st["written"], st["cls"]), (2, "g16@os26"), st)
        self.assertEqual(f4["%s_%s.vs.metal" % (self.kk_vs, "f" * 16)], self.texts(1, 2, "fix6+vsperf")[0])
        self.env["NPS5_METAL_CLASS"] = "g18@os27"
        st = self.gen(self.rows())
        f6 = self.files()
        self.assertEqual((st["written"], st["kk_class"]), (1, 1), st)
        self.assertEqual(list(f6), ["%s_%s.fs.metal" % (self.kk_vs, "f" * 16), "%s_%s.vs.metal" % (self.kk_vs, "f" * 16)])
        self.assertEqual(f6["%s_%s.fs.metal" % (self.kk_vs, "f" * 16)], self.texts(1, 2, "fix6+xf=7")[1])
        self.assertIn("class\tg18@os27", open(os.path.join(self.out, metal.GFX_INDEX)).read())

    def test_reproduce_pairs(self):
        work = os.path.join(self.tmp, "w"); os.makedirs(work)
        res = metal.reproduce(None, self.lst(self.rows()), self.prog, self.kkdir, self.env, work, out=io.StringIO())
        self.assertEqual((res["rows"], res["reproduced"]), (3, 3), res)


if __name__ == "__main__":
    unittest.main(verbosity=1)
