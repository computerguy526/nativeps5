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


def pad(img):
    """int3 padding up to the next 16-byte boundary (at least two bytes), as the compiler aligns functions."""
    img += b"\xcc" * 2
    while len(img) % 16: img += b"\xcc"


def nps5img(regions, nsegments=3, flags=5):
    """A guest_image.bin built exactly as emitter.EncodeGuestImage / recomp_runtime.c load_guest_image lay it out:
    magic[8], u32 nregions, u32 nsegments, regions {u64 vaddr, u64 size, u64 file offset}, segments {u64 vaddr, u64 memsz, u32 pf, u32 0},
    then the region bytes. regions = [(vaddr, bytes)]. -> (image, [file offset of each region's bytes])."""
    hdr = 16 + 24 * len(regions) + 24 * nsegments
    out = bytearray(loc.MAGIC + struct.pack("<II", len(regions), nsegments))
    offs, off = [], hdr
    for v, data in regions:
        out += struct.pack("<QQQ", v, len(data), off); offs.append(off); off += len(data)
    for i in range(nsegments):
        out += struct.pack("<QQII", 0x400000 + i * 0x1000, 0x1000 + i, flags + i, 0)
    for _, data in regions:
        out += data
    return bytes(out), offs


def image(both=True, second_func=False, callers=2):
    """-> (bytes, decoder offset, lea offset). Layout: pad, data strings, int3 pad, decoder, int3 pad, callers."""
    img = bytearray(b"\x00" * 0x100)
    s1 = len(img); img += S1; s2 = len(img); img += S2
    img += b"\xcc" * 2
    while len(img) % 16: img += b"\xcc"
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
        pad(img); other = len(img); img += PROLOGUE; img += lea(len(img), s1); img += b"\xc3"; img += b"\xcc" * 8
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

    def test_region_table_shift_is_applied(self):
        # real NPS5IMG layout (nregions=1, nsegments=3): vaddr = region vaddr + position in the region
        data, dec, lea_off, _ = image()
        img, offs = nps5img([(0x400000, data)])
        rc, out, _ = self.run_main([self.build(img)])
        self.assertEqual(rc, 0, out)
        want = 0x400000 + dec
        self.assertIn("decoder=0x%x " % want, out); self.assertIn("callers=2", out)
        self.assertIn("fp=%016x" % loc.fnv1a64(img[offs[0] + dec:offs[0] + dec + 64]), out)

    def test_segment_table_entries_are_not_regions(self):
        # the segment table {addr, memsz, pf flags, 0} follows the regions: it must not be read as more regions
        data, *_ = image()
        img, offs = nps5img([(0x400000, data)], nsegments=5)
        regs = loc.regions(img)
        self.assertEqual(regs, [(0x400000, len(data), offs[0])])
        img2, _ = nps5img([(0x400000, data), (0x900000, b"\0" * 64)], nsegments=4)
        self.assertEqual(len(loc.regions(img2)), 2)

    def test_two_regions_map_by_containing_region(self):
        data, dec, _, _ = image()
        img, _ = nps5img([(0x400000, b"\0" * 0x2000), (0x900000, data)])
        rc, out, _ = self.run_main([self.build(img)])
        self.assertEqual(rc, 0, out); self.assertIn("decoder=0x%x " % (0x900000 + dec), out)

    def test_malformed_headers_are_exit_2(self):
        data, *_ = image()
        good, _ = nps5img([(0x400000, data)])
        for bad in (loc.MAGIC + struct.pack("<II", 0, 0) + b"\0" * 64,                      # no regions
                    loc.MAGIC + struct.pack("<II", 5000, 0) + b"\0" * 64,                   # implausible count
                    good[:40],                                                                # tables run past the file
                    loc.MAGIC[:8] + struct.pack("<II", 1, 0) + struct.pack("<QQQ", 0x400000, 10 ** 9, 40) + b"\0" * 64):   # region outside the file
            self.assertEqual(self.run_main([self.build(bad)])[0], 2, bad[:20])

    def test_header_and_flat_images_agree_on_fp(self):
        # the same code with and without the NPS5IMG header: same fingerprint and caller count, vaddr differs only by the mapping
        data, dec, _, _ = image()
        img, _ = nps5img([(0x400000, data)])
        a = loc.locate(data)[0]; b = loc.locate(img)[0]
        self.assertEqual((a["fp"], a["callers"], a["lea"]), (b["fp"], b["callers"], b["lea"]))
        self.assertEqual((a["vaddr"], b["vaddr"]), (dec, 0x400000 + dec))

    def test_cc_inside_an_immediate_is_not_padding(self):
        # `mov eax, 0xCCCCCCCD` (divide by 10) puts CD CC CC CC inside the function before the lea: not a function start
        img = bytearray(b"\x00" * 0x40)
        s1 = len(img); img += S1; s2 = len(img); img += S2
        pad(img); dec = len(img)
        img += PROLOGUE + b"\xb8\xcd\xcc\xcc\xcc" + b"\x90" * 3
        img += lea(len(img), s1); img += lea(len(img), s2); img += b"\xc3"
        pad(img); at = len(img); img += call(at, dec) + b"\xc3"
        found = loc.locate(bytes(img))
        self.assertEqual([d["vaddr"] for d in found if d["both"]], [dec])

    def test_function_at_the_start_of_the_region_and_no_caller_is_refused(self):
        # a function at offset 0 of its region (no padding before it); with callers=0 it is refused, with one caller it is the decoder
        img = bytearray(PROLOGUE)
        l1 = len(img); img += b"\x00" * 7; l2 = len(img); img += b"\x00" * 7; img += b"\xc3"
        pad(img); t1 = len(img); img += S1; t2 = len(img); img += S2
        img[l1:l1 + 7] = lea(l1, t1); img[l2:l2 + 7] = lea(l2, t2)
        data = bytes(img) + b"\0" * 64
        rc, out, err = self.run_main([self.build(nps5img([(0x400000, data)])[0])])
        self.assertEqual(rc, 1); self.assertIn("direct caller", err); self.assertNotIn("ue5\t", out)
        img2 = bytearray(data); pad(img2); c = len(img2); img2 += call(c, 0) + b"\xc3" + b"\0" * 64
        rc, out, _ = self.run_main([self.build(nps5img([(0x400000, bytes(img2))])[0])])
        self.assertEqual(rc, 0, out); self.assertIn("decoder=0x400000 ", out)

    def test_head_must_hold_64_bytes_inside_the_region(self):
        data, dec, _, _ = image(callers=0)
        short = data[:dec + 40]                                    # the decoder's head is cut by the end of its region
        d2 = bytearray(short); pad(d2)
        img, _ = nps5img([(0x400000, bytes(short)), (0x500000, b"\x90" * 200)])
        rc, out, err = self.run_main([self.build(img), "--all"])
        self.assertNotIn("decoder=0x%x" % (0x400000 + dec), out); self.assertIn("fewer than 64 bytes", err)

    def test_scan_does_not_cross_regions(self):
        # padding in the PREVIOUS region must not give the function a start: a region that begins unaligned is skipped
        data, dec, _, _ = image(callers=1)
        cut = dec + 1                                               # the region starts one byte into the decoder (no padding, unaligned vaddr)
        img, _ = nps5img([(0x400000, b"\xcc" * 32), (0x600001, data[cut:])])
        self.assertEqual([d for d in loc.locate(img) if d["both"]], [])

    def test_decoy_xrefs_and_prologue_variants(self):
        # decoy: a function that mentions only S1 and another only S2 are not the decoder; a second real decoder with another
        # prologue is told apart by --prologue; one string repeated in data (two copies) still resolves
        img = bytearray(b"\0" * 0x80)
        s1 = len(img); img += S1; s1b = len(img); img += S1; s2 = len(img); img += S2; img += b"\xcc" * 8
        def func(prol, *targets):
            nonlocal img
            pad(img); f = len(img); img += prol
            for t in targets:
                img += lea(len(img), t)
            img += b"\xc3" + b"\xcc" * 8
            return f
        decoy1 = func(PROLOGUE, s1); decoy2 = func(PROLOGUE, s2)
        alt = bytes.fromhex("4883ec28") + b"\x90" * 12
        d1 = func(PROLOGUE, s1b, s2); d2 = func(alt, s1, s2)
        for tgt in (d1, d2, d1, d2):                     # a decoder is called: give each one direct callers
            pad(img); img += call(len(img), tgt) + b"\xc3"
        found = loc.locate(bytes(img))
        both = sorted(d["vaddr"] for d in found if d["both"])
        self.assertEqual(both, [d1, d2]); self.assertNotIn(decoy1, both); self.assertNotIn(decoy2, both)
        b = self.build(bytes(img))
        self.assertEqual(self.run_main([b])[0], 1)                                            # ambiguous without a filter
        rc, out, _ = self.run_main([b, "--prologue", alt.hex()]); self.assertEqual(rc, 0); self.assertIn("decoder=0x%x " % d2, out)
        rc, out, _ = self.run_main([b, "--prologue", PROLOGUE[:6].hex()]); self.assertEqual(rc, 0); self.assertIn("decoder=0x%x " % d1, out)

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
 export) [ -n "$STUB_FAIL_EXPORT" ] && { echo "export: cannot read the shader journal"; exit 1; }
   while [ $# -gt 0 ]; do [ "$1" = --out ] && out=$2; shift; done; echo "list" > "$out"; echo "exported: 7 kept as runtime code";;
 merge) [ -n "$STUB_FAIL_MERGE" ] && { echo "merge: another title or version"; exit 2; }
   shift; while [ $# -gt 0 ]; do case $1 in --out) out=$2; shift 2;; --allow-partial|--producer) shift;; *) cat "$1" >> "$out.in"; shift;; esac; done; cat "$out.in" > "$out"; rm "$out.in";;
 check) echo "check: ok"; echo "runtime: 6 resolved, ${STUB_PENDING:-1} pending"; [ -n "$STUB_MISMATCH" ] && exit 2; [ -n "$STUB_FAIL_CHECK" ] && exit 1; exit 0;;
 info) echo "  title PPSA00009, version 1.0, 1 things"; echo "  shaders 3 (vertex 1, pixel 1, compute 1); permutations 4"; echo "  permutations 4, pipelines 5";;
 drop) while [ $# -gt 0 ]; do case $1 in --list) l=$2; shift 2;; --out) o=$2; shift 2;; --dropped) d=$2; shift 2;; --hashes) h=$2; shift 2;; *) shift;; esac; done
   cp "$l" "$o"; printf '# hash\\tkind\\tcode\\treason\\n0x1\\tcompute\\truntime\\tx\\n' > "$d"; echo "drop: $(grep -c 0x "$h") hashes named, 1 shaders dropped, 1 permutations, 0 of 5 pipelines; wrote $o";;
 hashes) printf '0x00000000000000a1\tcompute\n0x00000000000000b2\tpixel\n';;
 verify) [ -n "$STUB_FAIL_VERIFY" ] && { echo "  section 3: 100 bytes, 2 code windows"; echo "verify: FAILED, the list contains shader code"; exit 1; }
   [ -n "$STUB_ODD_VERIFY" ] && { echo "verify: something unexpected"; exit 0; }
   # the real tool: the result line, then (when the list has <runtime> shaders) a LAST line with the coverage
   echo "  file: 100 bytes, 0 code windows"; echo "verify: no shader code in the list"
   echo "verify: ${STUB_UNCOVERED:-0} runtime shaders not covered by a --shaders journal (of 12)"; [ "${STUB_UNCOVERED:-0}" != 0 ] && exit 1; exit 0;;
esac
"""


SPV_STUB = r"""#!/bin/bash
# fake kyty_spv_dump --probe: records its arguments, writes the rows $STUB_PROBE_ROWS to --out (an unprobed.txt and a log too)
echo "SPV $@" >> "$STUB_LOG"
[ "$1" = --probe ] || exit 9
while [ $# -gt 0 ]; do case $1 in --out) o=$2; shift 2;; --log) l=$2; shift 2;; --unprobed) u=$2; shift 2;; --hashes) cp "$2" "$STUB_HASHES_COPY"; shift 2;; *) shift;; esac; done
printf "$STUB_PROBE_ROWS" > "$o"; : > "$l"; : > "$u"
[ -n "$STUB_PROBE_FAIL" ] && exit 1
echo "probe: 2 programs probed (3 permutations), $(grep -c 0x "$o") dropped (1 refused/crashed/timeout, 0 ray tracing), 0 listed programs not in any cache"
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
        for kind in ("shaders", "pipelines"):       # a static journal (not played) is skipped
            open(os.path.join(self.dirs[0], "PPSA00009.static.%s.journal" % kind), "w").write("s")
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
        self.assertEqual(len(exports), 2); self.assertFalse([l for l in exports if ".static." in l]); self.assertTrue(all("--runtime-code" in l for l in exports))
        self.assertTrue(any(l.startswith("merge") and "--allow-partial" in l for l in log))
        self.assertTrue(all("--game %s" % self.game in l for l in exports))      # --game is required by export and check
        self.assertTrue(any(l.startswith("check") and "--game %s" % self.game in l for l in log)); self.assertTrue(any(l.startswith("verify") for l in log))
        self.assertIn("skipped cache-empty", out); self.assertIn("runtime: 6 resolved, 1 pending", out)
        o = self.out_dir()
        for f in ("pipelines.kyplst", "pipelines.kyplst.verified", "counts.txt"):
            self.assertTrue(os.path.isfile(os.path.join(o, f)), f)
        self.assertIn("runtime: 6 resolved", open(os.path.join(o, "counts.txt")).read())
        self.assertIn("not adopted", out)
        self.assertFalse(os.path.exists(os.path.join(self.games, "PPSA00009", "pipelines.kyplst")))

    def test_real_two_line_verify_output_is_accepted_and_recorded(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, self.dirs[0])
        self.assertEqual(rc, 0, out)
        rec = open(os.path.join(self.out_dir(), "pipelines.kyplst.verified")).read()
        self.assertIn("result=verify: no shader code in the list\n", rec); self.assertIn("runtime=0 runtime shaders not covered", rec)

    def test_uncovered_runtime_shaders_adopt_nothing(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0], env=dict(STUB_UNCOVERED="3"))
        self.assertEqual(rc, 1); self.assertIn("verify FAILED", out)
        self.assertEqual(os.listdir(os.path.join(self.games, "PPSA00009")), [])

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

    def test_drop_hashes_removes_programs_before_check_and_records_them(self):
        h = os.path.join(self.tmp, "h.txt"); open(h, "w").write("0x5dcf90f23ef02c81\tSPIR-V validation failed\n0xb1b98482d67c466b\tBVH\n")
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--drop-hashes", h, self.dirs[0])
        self.assertEqual(rc, 0, out)
        log = open(self.log).read().splitlines()
        i_drop = [i for i, l in enumerate(log) if l.startswith("drop")]; i_chk = [i for i, l in enumerate(log) if l.startswith("check")]
        self.assertTrue(i_drop and i_chk and i_drop[0] < i_chk[0]); self.assertIn("--hashes " + h, log[i_drop[0]])
        self.assertIn("drop: 2 hashes named, 1 shaders dropped", out)
        self.assertIn("dropped: drop: 2 hashes named", open(os.path.join(self.out_dir(), "counts.txt")).read())
        self.assertTrue(os.path.isfile(os.path.join(self.out_dir(), "dropped.tsv")))
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--drop-hashes", os.path.join(self.tmp, "nope"), self.dirs[0])
        self.assertEqual(rc, 2); self.assertIn("is not a file", out)
        rc, out = self.run_h("PPSA00009", "--game", self.game, self.dirs[0])             # without the option: no drop call, no stale dropped.tsv
        self.assertEqual(rc, 0); self.assertFalse(os.path.exists(os.path.join(self.out_dir(), "dropped.tsv")))

    def probe_env(self, rows="0x00000000000000a1\\trefused: EXIT SPIR-V validation failed\\n"):
        spv = os.path.join(self.tmp, "spv.sh"); open(spv, "w").write(SPV_STUB); os.chmod(spv, 0o755)
        for d in self.dirs: open(os.path.join(d, "PPSA00009.programs.bin"), "w").write("p")
        self.hashes_copy = os.path.join(self.tmp, "hashes.copy")
        return dict(NPS5_SPVDUMP_TOOL=spv, STUB_PROBE_ROWS=rows, STUB_HASHES_COPY=self.hashes_copy)

    def test_probe_drops_refused_programs_before_check(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--probe", "--probe-jobs", "6", "--probe-timeout", "30", *self.dirs, env=self.probe_env())
        self.assertEqual(rc, 0, out)
        log = open(self.log).read().splitlines()
        spv = [l for l in log if l.startswith("SPV")][0]
        for d in self.dirs: self.assertIn("--programs %s/PPSA00009.programs.bin" % d, spv)
        self.assertIn("--jobs 6", spv); self.assertIn("--timeout 30", spv); self.assertNotIn("--rt", spv)       # no game.tsv: ray tracing stays
        self.assertEqual(open(self.hashes_copy).read().count("0x"), 2)                                           # the list's hashes (tool `hashes`)
        i_drop = [i for i, l in enumerate(log) if l.startswith("drop")]; i_chk = [i for i, l in enumerate(log) if l.startswith("check")]
        self.assertTrue(i_drop and i_drop[0] < i_chk[0]); self.assertIn("--hashes %s/drop.hashes" % self.out_dir(), log[i_drop[0]])
        self.assertIn("probe: 2 programs probed", out); self.assertIn("probe: 2 programs probed", open(os.path.join(self.out_dir(), "counts.txt")).read())
        self.assertIn("0x00000000000000a1", open(os.path.join(self.out_dir(), "probe.tsv")).read())

    def test_probe_rt_follows_the_title_profile(self):
        env = self.probe_env(); tsv = os.path.join(self.games, "PPSA00009", "game.tsv")
        open(tsv, "w").write("env\tNPS5_UECVAR_SET=r.Nanite.X=0,r.RayTracing.Enable=0,g.Y=1\tgpu=kyty\tnote\n")
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--probe", self.dirs[0], env=env)[0], 0)
        self.assertIn("--rt", [l for l in open(self.log).read().splitlines() if l.startswith("SPV")][-1])
        os.remove(self.log)
        open(tsv, "w").write("env\tNPS5_UECVAR_SET=r.RayTracing.Enable=1\tgpu=kyty\tnote\n")      # a title that runs ray tracing: no --rt
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--probe", self.dirs[0], env=env)[0], 0)
        self.assertNotIn("--rt", open(self.log).read().split("SPV")[1])
        os.remove(self.log)
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--probe", "--probe-rt", "on", self.dirs[0], env=env)[0], 0)
        self.assertIn("--rt", open(self.log).read().split("SPV")[1])
        os.remove(self.log); open(tsv, "w").write("env\tNPS5_UECVAR_SET=r.RayTracing.Enable=0\tgpu=kyty\tnote\n")
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--probe", "--probe-rt", "off", self.dirs[0], env=env)[0], 0)
        self.assertNotIn("--rt", open(self.log).read().split("SPV")[1])

    def test_probe_errors_and_other_titles_unaffected(self):
        env = self.probe_env()
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--probe", self.dirs[0], env=dict(env, STUB_PROBE_FAIL="1"))
        self.assertEqual(rc, 1); self.assertIn("probe FAILED", out)
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--probe", self.dirs[0], env=dict(env, NPS5_SPVDUMP_TOOL=os.path.join(self.tmp, "none")))
        self.assertEqual(rc, 2); self.assertIn("no probe tool", out)
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--probe", self.empty, self.dirs[0], env=env)
        self.assertEqual(rc, 0, out)
        os.remove(self.log)
        for d in self.dirs: os.remove(os.path.join(d, "PPSA00009.programs.bin"))
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--probe", self.dirs[0], env=env); self.assertEqual(rc, 1); self.assertIn("no PPSA00009*.programs.bin", out)
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--probe-rt", "maybe", self.dirs[0])[0], 2)
        # without --probe nothing of the probe runs (the ASTRO path): no hashes call, no probe files, no drop
        os.remove(self.log)
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, self.dirs[0], env=env)[0], 0)
        log = open(self.log).read(); self.assertNotIn("SPV", log); self.assertFalse([l for l in log.splitlines() if l.startswith(("hashes", "drop"))])
        self.assertFalse(os.path.exists(os.path.join(self.out_dir(), "probe.tsv")))

    def test_probe_and_drop_hashes_combine(self):
        h = os.path.join(self.tmp, "h.txt"); open(h, "w").write("0x00000000000000b2\tBVH\n")
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--probe", "--drop-hashes", h, self.dirs[0], env=self.probe_env())
        self.assertEqual(rc, 0, out)
        both = open(os.path.join(self.out_dir(), "drop.hashes")).read()
        self.assertIn("0x00000000000000a1", both); self.assertIn("0x00000000000000b2", both)

    def test_failed_check_and_verify_adopt_nothing(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", "--code-store", "/x/s", "--require-runtime", "100", self.dirs[0], env=dict(STUB_FAIL_CHECK="1"))
        self.assertEqual(rc, 1); self.assertIn("check FAILED", out)
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0], env=dict(STUB_FAIL_VERIFY="1"))
        self.assertEqual(rc, 1); self.assertIn("verify FAILED", out)
        self.assertEqual(os.listdir(os.path.join(self.games, "PPSA00009")), [])

    def test_check_exit_codes_gate_the_harvest(self):
        # 1 = --require-runtime failed, 2 = title/version mismatch: both stop the harvest and adopt nothing
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0], env=dict(STUB_MISMATCH="1"))
        self.assertEqual(rc, 1); self.assertIn("check FAILED", out)
        self.assertEqual(os.listdir(os.path.join(self.games, "PPSA00009")), [])
        rc, out = self.run_h("PPSA00009", "--game", self.game, self.dirs[0], env=dict(STUB_PENDING="0"))
        self.assertEqual(rc, 0); self.assertIn("0 pending", out)

    def test_code_store_goes_to_export_too(self):
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--code-store", "/x/s.codestore", self.dirs[0])[0], 0)
        ex = [l for l in open(self.log).read().splitlines() if l.startswith("export")][0]
        self.assertIn("--code-store /x/s.codestore", ex)

    def test_usage_errors(self):
        for args in ([], ["notanid", "--game", self.game, self.dirs[0]], ["PPSA00009", "--game", self.game],
                     ["PPSA00009", self.dirs[0]], ["PPSA00009", "--game", self.game, "--bogus", self.dirs[0]],
                     ["PPSA00009", "--game", self.game, "--require-runtime", "101", self.dirs[0]]):
            self.assertEqual(self.run_h(*args)[0], 2, args)

    def test_no_usable_journals_fails(self):
        rc, out = self.run_h("PPSA00009", "--game", self.game, self.empty)
        self.assertEqual(rc, 1); self.assertIn("no usable journals", out)


class ProbeHashes(unittest.TestCase):
    def run_p(self, *args):
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "ue5_probe_hashes.sh")] + list(args), capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def test_validation_failures_and_rt_kernels(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, True)
        a = os.path.join(d, "run.out"); b = os.path.join(d, "run.err")
        open(a, "w").write("noise\nKYTY_RT_SOFTWARE: CS shader 0x5dcf90f23ef02c81 has 2 BVH intersection instruction(s), first at pc=0x00000368.\n"
                           "SPIR-V validation failed: hash=0x5dcf90f23ef02c81 stage=4 reason=writable FLAT/GLOBAL addresses require GPU ownership tracking\n")
        open(b, "w").write("KYTY_RT_STUB: CS shader 0x91eca6e9b0627e97 has 1 BVH intersection instruction(s), first at pc=0x0000044c; every ray misses.\n"
                           "KYTY_RT_STUB: CS shader 0x0123456789abcdef has 0 BVH intersection instruction(s)\n")
        rc, out, err = self.run_p(a, b)
        self.assertEqual(rc, 0, err); self.assertEqual([l.split("\t")[0] for l in out.splitlines()], ["0x5dcf90f23ef02c81"])    # failures only
        rc, out, err = self.run_p("--rt", a, b)
        self.assertEqual(rc, 0, err); self.assertEqual([l.split("\t")[0] for l in out.splitlines()], ["0x5dcf90f23ef02c81", "0x91eca6e9b0627e97"])
        self.assertEqual(self.run_p(os.path.join(d, "missing"))[0], 2); self.assertEqual(self.run_p()[0], 2)


class HarvestGates(unittest.TestCase):
    """More of the harvest's gates (stub tool; the real tool's exit codes: check 1 = --require-runtime failed, 2 = another title or build)."""
    setUp, run_h, out_dir = Harvest.setUp, Harvest.run_h, Harvest.out_dir

    def calls(self): return open(self.log).read().splitlines()

    def test_export_and_merge_failures_stop_the_harvest_and_adopt_nothing(self):
        for env, msg in ((dict(STUB_FAIL_EXPORT="1"), "FAILED"), (dict(STUB_FAIL_MERGE="1"), "merge FAILED")):
            rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0], env=env)
            self.assertEqual(rc, 1, out); self.assertIn(msg, out)
            self.assertEqual(os.listdir(os.path.join(self.games, "PPSA00009")), [])
            self.assertFalse(os.path.exists(os.path.join(self.out_dir(), "pipelines.kyplst.verified")))

    def test_steps_run_in_order_and_only_the_wanted_ones(self):
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, self.dirs[0])[0], 0)
        order = [l.split()[0] for l in self.calls()]
        self.assertEqual(order, ["export", "merge", "check", "info", "info", "verify"])             # no drop without --drop-hashes; info for counts, then the record
        chk = [l for l in self.calls() if l.startswith("check")][0]
        self.assertNotIn("--require-runtime", chk); self.assertNotIn("--code-store", chk)         # report-only by default

    def test_require_runtime_values_and_its_code_store_rule(self):
        for pct, rc in (("0", 0), ("1", 2), ("100", 2), ("101", 2), ("-1", 2), ("abc", 2), ("5%", 2), ("", 2)):
            self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--require-runtime", pct, self.dirs[0])[0], rc, pct)     # without --code-store: usage error
        for pct in ("1", "50", "100"):
            self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--code-store", "/x/s", "--require-runtime", pct, self.dirs[0])[0], 0, pct)
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--require-runtime", "50", self.dirs[0])
        self.assertIn("needs --code-store", out)
        for opt in ("--game", "--out", "--code-store", "--require-runtime", "--drop-hashes"):       # a missing or empty value is a usage error (2), not the shell's abort
            self.assertEqual(self.run_h("PPSA00009", self.dirs[0], opt)[0], 2, opt)
            self.assertEqual(self.run_h("PPSA00009", opt, "", self.dirs[0])[0], 2, opt)

    def test_check_failure_of_either_kind_is_a_harvest_failure_with_no_record(self):
        for env in (dict(STUB_FAIL_CHECK="1"), dict(STUB_MISMATCH="1")):                           # tool exit 1 (below --require-runtime) and exit 2 (other build)
            rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0], env=env)
            self.assertEqual(rc, 1, out); self.assertIn("check FAILED", out)
            self.assertNotIn("verify", [l.split()[0] for l in self.calls()])                       # the proof is never attempted on a list that failed its check
            self.assertFalse(os.path.exists(os.path.join(self.out_dir(), "pipelines.kyplst.verified")))
            os.remove(self.log)

    def test_verify_gets_the_game_and_every_played_journal_and_no_static_one(self):
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, *self.dirs)[0], 0)
        v = [l for l in self.calls() if l.startswith("verify")][0]
        self.assertIn("--game %s" % self.game, v)
        self.assertEqual(v.count("--shaders"), 2); self.assertNotIn(".static.", v)
        for d in self.dirs: self.assertIn(os.path.join(d, "PPSA00009.shaders.journal"), v)

    def test_verify_finding_code_adopts_nothing_and_keeps_the_old_pair(self):
        g = os.path.join(self.games, "PPSA00009")
        open(os.path.join(g, "pipelines.kyplst"), "w").write("OLD"); open(os.path.join(g, "pipelines.kyplst.verified"), "w").write("OLDREC")
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0], env=dict(STUB_FAIL_VERIFY="1"))
        self.assertEqual(rc, 1); self.assertIn("verify FAILED", out); self.assertIn("do not commit", out)
        self.assertEqual(open(os.path.join(g, "pipelines.kyplst")).read(), "OLD"); self.assertEqual(open(os.path.join(g, "pipelines.kyplst.verified")).read(), "OLDREC")
        self.assertEqual(sorted(os.listdir(g)), ["pipelines.kyplst", "pipelines.kyplst.verified"])      # no .new, no .bak

    def test_write_replaces_the_pair_and_keeps_the_old_one_as_bak(self):
        g = os.path.join(self.games, "PPSA00009")
        open(os.path.join(g, "pipelines.kyplst"), "w").write("OLD"); open(os.path.join(g, "pipelines.kyplst.verified"), "w").write("OLDREC")
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0])[0], 0)
        self.assertEqual(open(os.path.join(g, "pipelines.kyplst.bak")).read(), "OLD"); self.assertEqual(open(os.path.join(g, "pipelines.kyplst.verified.bak")).read(), "OLDREC")
        self.assertNotEqual(open(os.path.join(g, "pipelines.kyplst")).read(), "OLD"); self.assertIn("sha256=", open(os.path.join(g, "pipelines.kyplst.verified")).read())
        self.assertFalse([f for f in os.listdir(g) if f.endswith(".new")])

    def test_write_into_a_missing_title_folder_fails_after_everything_passed(self):
        shutil.rmtree(os.path.join(self.games, "PPSA00009"))
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0])
        self.assertEqual(rc, 1); self.assertIn("no ", out)

    def test_a_rerun_clears_the_outputs_of_the_last_one(self):
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, self.dirs[0], env=dict(STUB_PENDING="3"))[0], 0)
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, "--drop-hashes", self.log, self.dirs[0])[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.out_dir(), "dropped.tsv")))
        rc, out = self.run_h("PPSA00009", "--game", self.game, self.empty)                          # nothing usable: the old list / record are gone, not left to be adopted
        self.assertEqual(rc, 1)
        for f in ("pipelines.kyplst", "pipelines.kyplst.verified", "counts.txt", "dropped.tsv"):
            self.assertFalse(os.path.exists(os.path.join(self.out_dir(), f)), f)

    def test_unpaired_and_static_only_journals_are_skipped_by_name(self):
        d = os.path.join(self.tmp, "cache-odd"); os.makedirs(d)
        open(os.path.join(d, "PPSA00009.session_x.shaders.journal"), "w").write("s")                # no matching pipelines journal
        for kind in ("shaders", "pipelines"): open(os.path.join(d, "PPSA00009.static.%s.journal" % kind), "w").write("s")
        open(os.path.join(d, "PPSA00001.shaders.journal"), "w").write("other title")
        rc, out = self.run_h("PPSA00009", "--game", self.game, d)
        self.assertEqual(rc, 1); self.assertIn("skipped cache-odd", out); self.assertIn("no usable journals", out)
        s = os.path.join(d, "PPSA00009.session_x.pipelines.journal"); open(s, "w").write("p")
        rc, out = self.run_h("PPSA00009", "--game", self.game, d)
        self.assertEqual(rc, 0, out); self.assertEqual(len([l for l in self.calls() if l.startswith("export")]), 1)

    def test_game_tool_and_title_arguments(self):
        f = os.path.join(self.tmp, "afile"); open(f, "w").write("x")
        self.assertEqual(self.run_h("PPSA00009", "--game", f, self.dirs[0])[0], 2)                  # --game must be a folder
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, self.dirs[0], env=dict(NPS5_KYPLST_TOOL=os.path.join(self.tmp, "none")))[0], 2)
        os.chmod(self.tool, 0o644)
        self.assertEqual(self.run_h("PPSA00009", "--game", self.game, self.dirs[0])[0], 2)         # a tool that is not executable
        for bad in ("ppsa00009", "PPSA0009", "PPSA000099", "../PPSA00009"):
            self.assertEqual(self.run_h(bad, "--game", self.game, self.dirs[0])[0], 2, bad)
        self.assertEqual(self.run_h("--help")[0], 0)

    def test_nothing_of_the_game_is_printed(self):
        open(os.path.join(self.game, "eboot.bin"), "wb").write(b"GAMEBYTES-MARKER")
        rc, out = self.run_h("PPSA00009", "--game", self.game, "--write", self.dirs[0]); self.assertEqual(rc, 0, out)
        self.assertNotIn("GAMEBYTES-MARKER", out)
        for root, _d, fs in os.walk(self.tmp):
            for f in fs:
                if os.path.join(root, f) != os.path.join(self.game, "eboot.bin"):
                    self.assertNotIn(b"GAMEBYTES-MARKER", open(os.path.join(root, f), "rb").read(), f)


class VerifyScript(unittest.TestCase):
    """scripts/verify-pipeline-list.sh (the no-game-code record that scripts/check.sh requires) against a stub tool. Synthetic data only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tool = os.path.join(self.tmp, "tool.sh"); open(self.tool, "w").write(STUB); os.chmod(self.tool, 0o755)
        self.log = os.path.join(self.tmp, "stub.log"); self.game = os.path.join(self.tmp, "app0"); os.makedirs(self.game)
        self.lst = os.path.join(self.tmp, "pipelines.kyplst"); open(self.lst, "w").write("LISTBYTES")
        self.env = dict(os.environ, NPS5_KYPLST_TOOL=self.tool, STUB_LOG=self.log, NPS5_SCRATCH=os.path.join(self.tmp, "scratch"))

    def run_v(self, *args, env=None):
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "verify-pipeline-list.sh")] + list(args), env=dict(self.env, **(env or {})), capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def rec(self):
        return dict(l.split("=", 1) for l in open(self.lst + ".verified").read().splitlines() if "=" in l and not l.startswith("#"))

    def test_record_holds_the_hash_of_the_list_the_result_and_the_counts(self):
        import hashlib
        rc, out = self.run_v(self.lst, self.game); self.assertEqual(rc, 0, out)
        r = self.rec()
        self.assertEqual(r["sha256"], hashlib.sha256(b"LISTBYTES").hexdigest()); self.assertEqual(r["result"], "verify: no shader code in the list")
        self.assertEqual(r["title"], "PPSA00009 1.0"); self.assertIn("shaders 3", r["counts"]); self.assertIn("pipelines 5", r["counts"])
        self.assertEqual(r["runtime"], "0 runtime shaders not covered by a --shaders journal (of 12)")      # the coverage line of a list with runtime shaders
        self.assertNotIn(self.game, open(self.lst + ".verified").read())                                    # no path of the player's machine in the record

    def test_every_journal_is_passed_as_shaders_and_the_game_as_game(self):
        j1, j2 = os.path.join(self.tmp, "a.shaders.journal"), os.path.join(self.tmp, "b.shaders.journal")
        self.assertEqual(self.run_v(self.lst, self.game, j1, j2)[0], 0)
        v = [l for l in open(self.log).read().splitlines() if l.startswith("verify")][0]
        self.assertIn("--game %s" % self.game, v); self.assertIn("--shaders %s --shaders %s" % (j1, j2), v)

    def test_found_code_exits_1_and_writes_no_record(self):
        rc, out = self.run_v(self.lst, self.game, env=dict(STUB_FAIL_VERIFY="1"))
        self.assertEqual(rc, 1); self.assertIn("FAILED", out); self.assertFalse(os.path.exists(self.lst + ".verified"))

    def test_found_code_leaves_an_older_record_alone_so_the_list_is_still_refused_by_the_check(self):
        open(self.lst + ".verified", "w").write("sha256=OLD\nresult=verify: no shader code in the list\n")
        self.assertEqual(self.run_v(self.lst, self.game, env=dict(STUB_FAIL_VERIFY="1"))[0], 1)
        self.assertIn("sha256=OLD", open(self.lst + ".verified").read())

    def test_uncovered_runtime_shaders_and_an_unexpected_result_line_exit_1(self):
        rc, out = self.run_v(self.lst, self.game, env=dict(STUB_UNCOVERED="2")); self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.lst + ".verified"))
        rc, out = self.run_v(self.lst, self.game, env=dict(STUB_ODD_VERIFY="1")); self.assertEqual(rc, 1); self.assertIn("unexpected", out)
        self.assertFalse(os.path.exists(self.lst + ".verified"))

    def test_usage_errors_are_exit_2(self):
        self.assertEqual(self.run_v(self.lst)[0], 2); self.assertEqual(self.run_v()[0], 2)
        self.assertEqual(self.run_v(os.path.join(self.tmp, "nolist"), self.game)[0], 2)
        self.assertEqual(self.run_v(self.lst, self.lst)[0], 2)                                            # the game must be a folder
        self.assertEqual(self.run_v(self.lst, self.game, env=dict(NPS5_KYPLST_TOOL=os.path.join(self.tmp, "none")))[0], 2)


if __name__ == "__main__":
    unittest.main()
