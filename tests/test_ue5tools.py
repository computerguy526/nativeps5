#!/usr/bin/env python3
"""Tests for tools/ue5/ue5_locate_decoder.py (synthetic guest image: strings, lea, int3 padding, call sites) and scripts/harvest_ue5.sh
(a stub kyty_pipeline_list that records its arguments). Synthetic data only: no game bytes, no title run."""
import io, os, shutil, struct, subprocess, sys, tempfile, unittest, warnings
from contextlib import redirect_stderr, redirect_stdout
warnings.simplefilter("ignore", ResourceWarning)
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "ue5"))
import ue5_locate_decoder as loc  # noqa: E402

PROLOGUE = bytes.fromhex("554889e5415741564155415453") + b"\x90" * 20     # synthetic: push rbp; mov rbp,rsp; pushes; nops
S1, S2 = loc.STRINGS


def lea(at, target, reg=0x3d, rex=0x48):
    """rip-relative `lea reg,[rip+disp]` placed at image offset `at` that points at `target`."""
    return bytes([rex, 0x8d, reg]) + struct.pack("<i", target - (at + 7))


def call(at, target):
    return b"\xe8" + struct.pack("<i", target - (at + 5))


def image(both=True, second_func=False, callers=2):
    """-> (bytes, decoder offset, lea offset). Layout: pad, data strings, int3 pad, decoder, int3 pad, callers."""
    img = bytearray(b"\x00" * 0x100)
    s1 = len(img); img += S1; s2 = len(img); img += S2
    img += b"\xcc" * 16
    dec = len(img)
    body = bytearray(PROLOGUE)
    lea_off = dec + len(body)
    body += lea(lea_off, s1)
    if both:
        body += lea(dec + len(body), s2)
    body += b"\xc3"
    img += body
    img += b"\xcc" * 15
    other = None
    if second_func:                         # a second function that mentions only the first string
        other = len(img); img += PROLOGUE; img += lea(len(img), s1); img += b"\xc3"; img += b"\xcc" * 8
    for _ in range(callers):
        at = len(img); img += call(at, dec); img += b"\xc3" + b"\xcc" * 8
    return bytes(img), dec, lea_off - dec, other


class Locate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.tmp, True)

    def build(self, data, name="guest_image.bin"):
        d = os.path.join(self.tmp, "build"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f: f.write(data)
        return d

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = loc.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_fnv1a64_known_values(self):
        self.assertEqual(loc.fnv1a64(b""), 0xcbf29ce484222325)
        self.assertEqual(loc.fnv1a64(b"a"), 0xaf63dc4c8601ec8c)

    def test_finds_decoder_vaddr_fp_and_callers(self):
        data, dec, lea_off, _ = image()
        rc, out, _ = self.run_main([self.build(data)])
        self.assertEqual(rc, 0)
        fp = "%016x" % loc.fnv1a64(data[dec:dec + 64])
        self.assertIn("decoder=0x%x lea=+0x%x callers=2 fp=%s" % (dec, lea_off, fp), out)
        self.assertIn("ue5\tdecoder=0x%x\tfp=%s\tabi=oodlelz14" % (dec, fp), out)

    def test_row_is_accepted_by_the_profile_schema(self):
        sys.path.insert(0, os.path.join(REPO, "games"))
        import game
        data, dec, _, _ = image()
        rc, out, _ = self.run_main([self.build(data)])
        row = [l for l in out.splitlines() if l.startswith("ue5\t")][0].split("\t")[1:]
        self.assertIsNone(game.v_ue5(row, "-"))

    def test_base_file_path_and_json(self):
        data, dec, _, _ = image()
        p = os.path.join(self.build(data), "guest_image.bin")
        rc, out, _ = self.run_main([p, "--base", "0x400000", "--json"])
        self.assertEqual(rc, 0); self.assertIn('"vaddr": %d' % (0x400000 + dec), out)

    def test_one_string_only_is_not_the_decoder(self):
        data, *_ = image(both=False)
        rc, out, err = self.run_main([self.build(data)])
        self.assertEqual(rc, 1); self.assertIn("no function references both", err); self.assertNotIn("ue5\t", out)
        rc, out, _ = self.run_main([self.build(data), "--all"])            # --all lists it, marked
        self.assertEqual(rc, 0); self.assertIn("one string only", out)

    def test_other_function_with_one_string_does_not_make_it_ambiguous(self):
        data, dec, _, other = image(second_func=True)
        rc, out, _ = self.run_main([self.build(data)])
        self.assertEqual(rc, 0); self.assertIn("decoder=0x%x" % dec, out); self.assertNotIn("0x%x " % other, out)
        rc, out, _ = self.run_main([self.build(data), "--all"])
        self.assertIn("decoder=0x%x" % other, out)

    def test_prologue_filter(self):
        data, *_ = image()
        b = self.build(data)
        self.assertEqual(self.run_main([b, "--prologue", PROLOGUE[:8].hex()])[0], 0)
        self.assertEqual(self.run_main([b, "--prologue", "deadbeef"])[0], 1)

    def test_no_strings_bad_args_and_unreadable(self):
        self.assertEqual(self.run_main([self.build(b"\x00" * 4096)])[0], 1)
        self.assertEqual(self.run_main([])[0], 2)
        self.assertEqual(self.run_main(["--bogus"])[0], 2)
        self.assertEqual(self.run_main([os.path.join(self.tmp, "missing")])[0], 2)
        self.build(b"", "empty.bin")
        self.assertEqual(self.run_main([os.path.join(self.tmp, "build", "empty.bin")])[0], 2)

    def test_output_holds_no_image_bytes(self):
        data, *_ = image()
        _, out, err = self.run_main([self.build(data)])
        for chunk in (PROLOGUE[:8], S1, S2):
            self.assertNotIn(chunk.hex(), out + err); self.assertNotIn(chunk.decode("latin1"), out + err)

    def test_truncated_tail_does_not_crash(self):
        data, *_ = image()
        self.assertIn(self.run_main([self.build(data + b"\x48\x8d\x3d\x01")])[0], (0, 1))
        self.assertIn(self.run_main([self.build(data + b"\xe8\x01")])[0], (0, 1))


STUB = r"""#!/bin/bash
echo "$@" >> "$STUB_LOG"
case $1 in
 export) while [ $# -gt 0 ]; do [ "$1" = --out ] && out=$2; shift; done; echo "list" > "$out"; echo "exported: 7 kept as runtime code";;
 merge) shift; while [ $# -gt 0 ]; do case $1 in --out) out=$2; shift 2;; --allow-partial|--producer) shift;; *) cat "$1" >> "$out.in"; shift;; esac; done; cat "$out.in" > "$out"; rm "$out.in";;
 check) echo "check: ok"; echo "runtime: 6 resolved, ${STUB_PENDING:-1} pending"; [ -n "$STUB_FAIL_CHECK" ] && exit 1; exit 0;;
 info) echo "  title PPSA00009, version 1.0, 1 things"; echo "  shaders 3 (vertex 1, pixel 1, compute 1); permutations 4"; echo "  permutations 4, pipelines 5";;
 verify) [ -n "$STUB_FAIL_VERIFY" ] && { echo "verify: FOUND code"; exit 1; }; echo "verify: no shader code in the list";;
esac
"""


class Harvest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        self.tool = os.path.join(t, "tool.sh"); open(self.tool, "w").write(STUB); os.chmod(self.tool, 0o755)
        self.log = os.path.join(t, "stub.log"); self.game = os.path.join(t, "app0"); os.makedirs(self.game)
        self.games = os.path.join(t, "games"); os.makedirs(os.path.join(self.games, "PPSA00009"))
        self.dirs = []
        for n in ("a", "b"):
            d = os.path.join(t, "cache-" + n); os.makedirs(d); self.dirs.append(d)
            for kind in ("shaders", "pipelines"):
                open(os.path.join(d, "PPSA00009.%s.journal" % kind), "w").write(n)
        self.empty = os.path.join(t, "cache-empty"); os.makedirs(self.empty)
        self.env = dict(os.environ, NPS5_KYPLST_TOOL=self.tool, NPS5_SCRATCH=os.path.join(t, "scratch"), NPS5_GAMES_DIR=self.games,
                        NPS5_HW_CACHE_KEY="g16-25G83", STUB_LOG=self.log)

    def run_h(self, *args, env=None):
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "harvest_ue5.sh")] + list(args), env=dict(self.env, **(env or {})),
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def out_dir(self): return os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00009", "harvest-ue5")

    def test_happy_path_passes_the_runtime_words_and_writes_files(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, *self.dirs, self.empty)
        self.assertEqual(rc, 0, out)
        log = open(self.log).read().splitlines()
        exports = [l for l in log if l.startswith("export")]
        self.assertEqual(len(exports), 2); self.assertTrue(all("--runtime-code" in l for l in exports))
        self.assertTrue(any(l.startswith("merge") and "--allow-partial" in l for l in log))
        self.assertTrue(any(l.startswith("check") for l in log)); self.assertTrue(any(l.startswith("verify") for l in log))
        self.assertIn("skipped cache-empty", out); self.assertIn("runtime: 6 resolved, 1 pending", out)
        o = self.out_dir()
        for f in ("pipelines.kyplst", "pipelines.kyplst.verified", "counts.txt"):
            self.assertTrue(os.path.isfile(os.path.join(o, f)), f)
        self.assertIn("runtime: 6 resolved", open(os.path.join(o, "counts.txt")).read())
        self.assertIn("not adopted", out)
        self.assertFalse(os.path.exists(os.path.join(self.games, "PPSA00009", "pipelines.kyplst")))

    def test_write_adopts_list_and_verified(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0])
        self.assertEqual(rc, 0, out)
        g = os.path.join(self.games, "PPSA00009")
        self.assertEqual(open(os.path.join(g, "pipelines.kyplst")).read(), open(os.path.join(self.out_dir(), "pipelines.kyplst")).read())
        self.assertIn("sha256=", open(os.path.join(g, "pipelines.kyplst.verified")).read())

    def test_code_store_and_require_runtime_pass_through(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--code-store", "/x/s.codestore", "--require-runtime", "100", self.dirs[0])
        self.assertEqual(rc, 0, out)
        chk = [l for l in open(self.log).read().splitlines() if l.startswith("check")][0]
        self.assertIn("--code-store /x/s.codestore", chk); self.assertIn("--require-runtime 100", chk)

    def test_failed_check_and_verify_adopt_nothing(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", "--require-runtime", "100", self.dirs[0], env=dict(STUB_FAIL_CHECK="1"))
        self.assertEqual(rc, 1); self.assertIn("check FAILED", out)
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0], env=dict(STUB_FAIL_VERIFY="1"))
        self.assertEqual(rc, 1); self.assertIn("verify FAILED", out)
        self.assertEqual(os.listdir(os.path.join(self.games, "PPSA00009")), [])

    def test_usage_errors(self):
        for args in ([], ["notanid", "--game", self.game, self.dirs[0]], ["PPSA00009", "--game", self.game],
                     ["PPSA00009", self.dirs[0]], ["PPSA00009", "--game", self.game, "--bogus", self.dirs[0]],
                     ["PPSA00009", "--game", self.game, "--require-runtime", "101", self.dirs[0]]):
            self.assertEqual(self.run_h(*args)[0], 2, args)

    def test_no_usable_journals_fails(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, self.empty)
        self.assertEqual(rc, 1); self.assertIn("no usable journals", out)


if __name__ == "__main__":
    unittest.main()
