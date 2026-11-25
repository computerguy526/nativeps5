#!/usr/bin/env python3
"""Tests of scripts/nps5lib/harvestall.py: the shipped list must cover what programs.bin / journals hold (not only journals). Synthetic data only."""
import os, struct, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib"))
import harvestall as H  # noqa: E402

SPEC0 = struct.pack("<II", 0, 0)                                   # no buffers, no images
IMG21 = bytes(32) + b"\0\0"                                       # one int21 image record
SPEC21 = struct.pack("<II", 0, 1) + IMG21
SPEC_CUR = struct.pack("<II", 0, 1) + bytes(32) + b"\0\0\0\0" + bytes(4) + b"\0"
STAT = (0, 64 | (0xc0 << 8), 32, 1, 0, 0, 0, 128, 1, 1, 1, 1, 0, 0)   # 14 compute key words


def ident(stage=H.COMPUTE, h=0x11, code=3, statics=STAT, wave=64, ub=0, plain=0, ud=16):
    return (stage, h, ud, code, tuple(statics), wave, ub, plain)


def key(i):
    k = struct.pack("<IIQIII", 1, i[0], i[1], i[2], i[3], len(i[4])) + struct.pack("<%dI" % len(i[4]), *i[4])
    k += struct.pack("<IIB", i[5], i[6], i[7]) + struct.pack("<I", i[3]) + struct.pack("<%dI" % i[3], *range(i[3])) + struct.pack("<I", 0)
    return k


def rec(kind, payload):
    return struct.pack("<IIQQ", H.KPRC_MAGIC, kind, len(payload), 0) + payload


def programs_file(path, sources, perms):
    """sources [(digest byte, ident)], perms [(digest byte, cursor, spec)]"""
    b = bytearray(16) + struct.pack("<Q", 3) + b"hdr" + struct.pack("<Q", 0)
    for dg, i in sources:
        k = key(i)
        b += rec(1, bytes([dg]) * 16 + struct.pack("<I", len(k)) + k + b"\0" + struct.pack("<I", 0))
    for dg, cur, spec in perms:
        b += rec(2, bytes([dg]) * 16 + struct.pack("<II", cur, len(spec)) + spec + bytes(12))
    open(path, "wb").write(bytes(b))


class T(unittest.TestCase):
    def test_spec_upgrade(self):
        self.assertTrue(H.reads_current(SPEC0) and H.reads_current(SPEC_CUR))
        self.assertFalse(H.reads_current(SPEC21))
        self.assertEqual(H.upgrade_spec(SPEC21), SPEC_CUR)
        self.assertEqual(H.upgrade_spec(SPEC0), SPEC0)
        self.assertIsNone(H.upgrade_spec(b"\xff\xff\xff\xff"))
        self.assertIsNone(H.upgrade_spec(struct.pack("<II", 0, 1) + bytes(5)))

    def test_programs_read_and_upgrade(self):
        a, b = ident(h=1), ident(h=2)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.programs.bin")
            programs_file(p, [(1, a), (2, b)], [(1, 0, SPEC0), (2, 4, SPEC21), (9, 0, SPEC0), (2, 5, b"\x01")])
            idents, perms, bad = H.programs_view(p)
            self.assertEqual(idents, {a, b})
            self.assertEqual(perms, {(a, 0, SPEC0), (b, 4, SPEC_CUR)})   # orphan perm (digest 9) ignored, int21 spec upgraded
            self.assertEqual(bad, 1)                                    # unreadable spec counted

    def test_coverage_finds_programs_only_sources(self):
        # the v1.4 miss: a compute source in programs.bin that no journal recorded
        a, b = ident(h=1), ident(h=2)
        lst = H.ListIndex([a], [(a, 0, SPEC0)])
        ms, mp = H.coverage(lst, [("pb:x", {a, b}, {(a, 0, SPEC0), (b, 0, SPEC0), (a, 7, SPEC0)})])
        self.assertEqual(set(ms), {b})
        self.assertEqual(set(mp), {(b, 0, SPEC0), (a, 7, SPEC0)})
        ms, mp = H.coverage(lst, [("pb:x", {a}, {(a, 0, SPEC0)})])
        self.assertEqual((ms, mp), ({}, {}))

    def test_gate_guest_level(self):
        a = ident(h=0xaa)
        lst = H.ListIndex([a, ident(stage=H.PIXEL, h=0xbb, statics=())], [])
        guests = H.parse_guests("format\tnps5-metal-guests\t1\ncs\t%s\t%016x\nfs\t%s\t%016x\n" % ("1" * 16, 0xaa, "2" * 16, 0xbb))
        self.assertEqual(guests, {("cs", 0xaa), ("fs", 0xbb)})
        self.assertEqual(H.override_gate(lst, guests, set()), [])
        self.assertEqual(len(H.override_gate(lst, {("cs", 0xbb)}, set())), 1)      # a pixel shader is not a cs override
        self.assertEqual(len(H.override_gate(lst, guests, {0xcc})), 1)             # deny hash absent
        self.assertEqual(len(H.override_gate(lst, {("vs", 0xaa)}, set())), 1)      # compute is not a vertex shader

    def test_gate_program_variants(self):
        # an override guest hash with ANOTHER static variant that only programs.bin knows (the 38 of v1.4)
        a = ident(h=0xaa)
        variant = ident(h=0xaa, statics=(0, 64 | (0xc0 << 8), 32, 1, 0, 0, 0, 256, 1, 1, 1, 1, 0, 0))
        other = ident(h=0xdd, statics=STAT)
        lst = H.ListIndex([a], [(a, 0, SPEC0)])
        views = [("pb:x", {a, variant, other}, {(a, 0, SPEC0), (a, 3, SPEC0), (variant, 0, SPEC0)})]
        errs = H.override_gate(lst, {("cs", 0xaa)}, set(), views)
        self.assertEqual(len(errs), 2)                                              # variant source + the (a, 3) permutation; `other` is no override
        self.assertTrue(any("source" in e for e in errs) and any("permutation" in e for e in errs))
        full = H.ListIndex([a, variant], [(a, 0, SPEC0), (a, 3, SPEC0), (variant, 0, SPEC0)])
        self.assertEqual(H.override_gate(full, {("cs", 0xaa)}, set(), views), [])

    def test_shipped_list_reads(self):
        p = os.path.join(HERE, "..", "games", "PPSA21564", "pipelines.kyplst")
        if not os.path.isfile(p):
            self.skipTest("no shipped list")
        import shutil
        if shutil.which("zstd") is None:
            self.skipTest("no zstd")
        lst = H.read_list(p)
        self.assertGreater(len(lst.shaders), 1000)
        self.assertGreater(len(lst.perms), 1400)
        self.assertEqual(H.read_list.__name__, "read_list")

    def test_bad_list(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x")
            open(p, "wb").write(b"nope" * 8)
            with self.assertRaises(H.FormatError):
                H.read_list(p)


if __name__ == "__main__":
    unittest.main()
