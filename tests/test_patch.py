#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/patch.py on a synthetic Mach-O-like file (no real binary read)."""
import importlib.util, io, json, os, struct, sys, tempfile, unittest, contextlib
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("nps5_patch", os.path.join(HERE, "..", "scripts", "nps5lib", "patch.py"))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

SHA = "ab" * 32
BASE = 0x100000000
FN_A, FN_B = 0x1000, 0x2000
HOST_A, HOST_B = 0x1000, 0x2000      # function host offsets in the text
DATA = """
BUILDS = {'1.0': 'abababababababab'}
CUTS = {'1.0': {'x': [(0x1000, 0x10, 0x11111111, 0x22222222, 'x')], 'y': [(0x2000, 0x8, 0x33333333, 0x44444444, 'y')]}}
PARAM_CUTS = {'1.0': {'sz': {'param': 'WxH', 'sites': [
    (0x2000, 0x20, 0xd280f016, (0xd2800016, [('W', 'u16', 5)]), 'w'),
    (0x2000, 0x24, 0xf2c08716, (0xf2c00016, [('H', 'u16', 5)]), 'h')]}}}
REQUIRES = {'sz': ['x']}
LINEAGES = {'base': 0x0, 'l2': 0x100}
FALLBACK_FUNCS = {0x1000: 0x100001000, 0x2000: 0x100002000}
BINDING = [{'what': 'x', 'purpose': 'p', 'now': 'build', 'goal': 'host', 'why': 'w', 'review': 'reviewed', 'checked': 'c',
            'setting': {'name': 'xs', 'state': 'pending'}},
           {'what': 'y', 'purpose': 'p', 'now': 'build', 'goal': 'build', 'why': 'w', 'review': 'unreviewed', 'checked': 'c'},
           {'what': 'sz', 'purpose': 'p', 'now': 'build', 'goal': 'build', 'why': 'w', 'review': 'unreviewed', 'checked': 'c'}]
"""


def macho(path, syms=True, sha=SHA, shift=0, words=None):
    """one __TEXT segment (vm 0x100000000, file 0), text at file 0x1000.., symtab + strtab + sha string at 0x8000."""
    text = bytearray(0x4000)
    words = words or {}
    base_words = {FN_A + 0x10: 0x11111111, FN_B + 0x8: 0x33333333, FN_B + 0x20: 0xd280f016, FN_B + 0x24: 0xf2c08716}
    base_words.update(words)
    for off, w in base_words.items():
        struct.pack_into("<I", text, off + shift, w)
    names = [("_fn_0x1000", BASE + FN_A + shift), ("_fn_0x2000", BASE + FN_B + shift)] if syms else []
    sha_off = 0x3f00
    if sha:
        text[sha_off:sha_off + 65] = sha.encode() + b"\0"
        names.append(("_nps5_input_sha256", BASE + sha_off))
    strtab = b"\0"; ents = b""
    for n, v in names:
        ents += struct.pack("<IBBHQ", len(strtab), 0x0f, 1, 0, v); strtab += n.encode() + b"\0"
    symoff = 0x4000; stroff = symoff + len(ents)
    seg = struct.pack("<II16sQQQQiiII", 0x19, 72, b"__TEXT", BASE, 0x10000, 0, 0x4000, 7, 5, 0, 0)
    st = struct.pack("<IIIIII", 2, 24, symoff, len(names), stroff, len(strtab))
    hdr = struct.pack("<IiiIIIII", 0xfeedfacf, 0x0100000c, 0, 2, 2, len(seg) + len(st), 0, 0)
    blob = bytearray(hdr + seg + st); blob += bytes(0x1000 - len(blob))
    blob = bytes(blob) + bytes(text[0x1000:]) + ents + strtab
    open(path, "wb").write(blob)


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.pp = os.path.join(self.d, "patches.py"); open(self.pp, "w").write(DATA)
        self.pd = P.load_patches(self.pp)
        self.bin = os.path.join(self.d, "native")
        macho(self.bin)
        P.SIGN = False

    def check(self, path=None, names=None, **kw):
        out = io.StringIO()
        rc = P.cmd_check(self.pd, path or self.bin, names, out=out, **kw)
        return rc, out.getvalue()

    def test_symbol_anchor_survives_relink(self):
        moved = os.path.join(self.d, "moved"); macho(moved, shift=0x40)
        rc, out = self.check(moved); self.assertEqual(rc, 0); self.assertIn("applicable", out)

    def test_data_only(self):
        bad = os.path.join(self.d, "bad.py"); open(bad, "w").write("def f(): pass\n")
        with self.assertRaises(P.PatchError): P.load_patches(bad)
        open(bad, "w").write("BUILDS = {}\nCUTS = {}\nBINDING = [1+1]\n")
        with self.assertRaises(P.PatchError): P.load_patches(bad)

    def test_wrong_build_refused(self):
        other = os.path.join(self.d, "o"); macho(other, sha="cd" * 32)
        with self.assertRaises(P.PatchError): self.check(other)

    def test_no_build_symbol_refused(self):
        other = os.path.join(self.d, "o"); macho(other, sha=None)
        with self.assertRaises(P.PatchError): self.check(other)

    def test_missing_symbol_refused(self):
        other = os.path.join(self.d, "o"); macho(other, syms=False)
        with self.assertRaises(P.PatchError): self.check(other)
        rc, out = self.check(other, anchor="lineage", lineage="base", build="1.0") if False else (0, "")

    def test_lineage_fallback(self):
        other = os.path.join(self.d, "o"); macho(other, syms=False, shift=0x100)
        rc, out = self.check(other, lineage="l2", build="1.0") if False else (None, None)
        mo, ver, _ = P.open_binary(self.pd, other)
        res = P.resolve(self.pd, mo, ver, [("x", None)], "l2")
        self.assertEqual(res[0][2][0].anchor, "lineage:l2")
        self.assertEqual(P.judge(mo, res, self.pd)[0]["state"], "applicable")
        with self.assertRaises(P.PatchError): P.resolve(self.pd, mo, ver, [("x", None)], None)
        mo.close()

    def test_mismatch_reports(self):
        other = os.path.join(self.d, "o"); macho(other, words={FN_A + 0x10: 0xdeadbeef})
        rc, out = self.check(other); self.assertEqual(rc, 1); self.assertIn("MISMATCH", out)

    def test_apply_copy_and_sidecar(self):
        src_bytes = open(self.bin, "rb").read()
        out = os.path.join(self.d, "copy")
        info = P.apply(self.pd, self.bin, [("x", None), ("y", None)], out)
        self.assertEqual(open(self.bin, "rb").read(), src_bytes)             # source untouched
        mo = P.MachO(out); self.assertEqual(mo.word(BASE + FN_A + 0x10), 0x22222222); self.assertEqual(mo.word(BASE + FN_B + 8), 0x44444444); mo.close()
        side = json.load(open(out + ".patches.json"))
        self.assertEqual(side["build"], "1.0"); self.assertEqual(side["build_id"], SHA); self.assertEqual(side["anchor_mode"], "symbol")
        rc, txt = self.check(out); self.assertIn("applied", txt)
        # idempotent on an already patched binary
        out2 = os.path.join(self.d, "copy2"); P.apply(self.pd, out, [("x", None)], out2)

    def test_fail_closed_no_partial(self):
        bad = os.path.join(self.d, "bad"); macho(bad, words={FN_B + 8: 0x99999999})
        out = os.path.join(self.d, "copy")
        with self.assertRaises(P.PatchError): P.apply(self.pd, bad, [("x", None), ("y", None)], out)
        self.assertFalse(os.path.exists(out)); self.assertFalse(os.path.exists(out + ".patches.json"))

    def test_refuses_source_as_output_and_existing(self):
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.bin, [("x", None)], self.bin)
        out = os.path.join(self.d, "copy"); open(out, "w").write("x")
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.bin, [("x", None)], out)

    def test_param_patch_and_requires(self):
        out = os.path.join(self.d, "c")
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.bin, [("sz", "1280x720")], out)   # needs x
        P.apply(self.pd, self.bin, [("x", None), ("sz", "1280x720")], out)
        rc, txt = self.check(out); self.assertIn("values 1280x720", txt)
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.bin, [("x", None), ("sz", "bad")], os.path.join(self.d, "c2"))
        with self.assertRaises(P.PatchError): P.apply(self.pd, out, [("x", None), ("sz", "640x360")], os.path.join(self.d, "c3"))  # other values
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.bin, [("x", None), ("sz", None)], os.path.join(self.d, "c4"))

    def test_float_param_rules(self):
        self.assertEqual(P._field("f32hi", 1280, 5, "t"), (0x44a00000 >> 16) << 5)
        with self.assertRaises(P.PatchError): P._field("f32hi", 1281, 5, "t")
        with self.assertRaises(P.PatchError): P._field("u16", 70000, 5, "t")

    def test_overlap_and_unknown(self):
        with self.assertRaises(P.PatchError): P.parse_request("x,x")
        with self.assertRaises(P.PatchError): P.apply(self.pd, self.bin, [("nope", None)], os.path.join(self.d, "c"))

    def test_list_and_docs(self):
        out = io.StringIO(); self.assertEqual(P.cmd_list(self.pd, "T", out), 0)
        self.assertIn("setting: xs (pending)", out.getvalue())
        md = P.docs_text([("PPSA00001", self.pd)]); self.assertIn("| `x` |", md); self.assertIn("`xs` (pending)", md)


if __name__ == "__main__":
    unittest.main()
