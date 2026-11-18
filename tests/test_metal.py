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
while [ $# -gt 0 ]; do case $1 in --perm) p=$2; shift;; --out) o=$2; shift;; esac; shift; done
[ "$p" = 4 ] && exit 3
[ "$p" = 3 ] && { echo "// complete=0 unsupported: X" >&2; printf "$HDR" > $o; exit 0; }
printf "$HDRperm %s %s\\n" "$p" "${KYTY_MSL_LDS_FENCE:-}" > $o
echo "// complete=1 unsupported:" >&2
"""


def rec(kind, payload):
    return struct.pack("<IIQQ", metal.KPRC, kind, len(payload), 0) + payload   # 24-byte record header


def perm_rec(digest, spec):
    return rec(2, digest + b"\0" * 4 + struct.pack("<I", len(spec)) + spec + b"\0" * 8)


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


class Prepare(unittest.TestCase):
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
                os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/k1.metal", "w").write("src")
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
