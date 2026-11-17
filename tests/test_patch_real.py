#!/usr/bin/env python3
"""LOCAL real-binary tests for `nps5 patch` (needs-dump). Auto-skipped unless the translated PPSA21564 binaries exist.

  NPS5_PATCH_REAL=1 python3 -I tests/test_patch_real.py

Where: NPS5_PATCH_BOOT (directory holding native_kyty6, native_kyty6_dr, ...; default ../../ppsa21564/boot relative to
this repository, the layout of the maintainer's checkout), NPS5_PATCH_OTHER_NATIVE (a binary of ANOTHER title, optional),
NPS5_PATCH_SCRATCH (scratch directory for the ~680 MB clones; default a fresh temp dir). The boot directory is only ever
READ: every produced copy goes to the scratch directory (cp -c clone, deleted right after it is compared). Needs ~1 GB
of free disk in the worst case, normally a few MB (APFS clones share blocks).

Golden files: the already-existing boot/native_kyty6_dr, _drnt, _drm, _dr720, _dr900 (made earlier by hand) must be
reproduced byte for byte (everything before the code signature) from the unpatched native_kyty6."""
import importlib.util, os, shutil, struct, subprocess, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("nps5_patch_real", os.path.join(REPO, "scripts", "nps5lib", "patch.py"))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

BOOT = os.environ.get("NPS5_PATCH_BOOT") or os.path.normpath(os.path.join(REPO, "..", "..", "ppsa21564", "boot"))
OTHER = os.environ.get("NPS5_PATCH_OTHER_NATIVE") or os.path.normpath(os.path.join(REPO, "..", "..", "ppsa30552", "boot", "fast", "native_fast"))
SRC = os.path.join(BOOT, "native_kyty6")
GOLD = [  # (golden file, request)
    ("native_kyty6_dr", "dr"), ("native_kyty6_drnt", "dr,nt"), ("native_kyty6_drm", "dr,mode"),
    ("native_kyty6_dr720", "dr,lowres=1280x720"), ("native_kyty6_dr900", "dr,lowres=1600x900"),
]
PB_GOLD = [("native_kyty6_pb", "native_kyty6_pb_dr", "dr")]   # (unpatched, golden, request): the pb link needs --lineage? checked below
PROFILE = os.path.join(REPO, "games", "PPSA21564", "patches.py")


def needs_dump():
    if os.environ.get("NPS5_PATCH_REAL") != "1":
        return "set NPS5_PATCH_REAL=1 to run the real-binary tier (needs-dump)"
    if not os.path.isfile(SRC) or not os.path.isfile(PROFILE):
        return "needs-dump: %s not present" % SRC
    return None


def sign_offset(path):
    mo = P.MachO(path)
    try:
        return mo.sig[0] if mo.sig else os.path.getsize(path)
    finally:
        mo.close()


def same_before_signature(a, b, chunk=1 << 24):
    """None when identical up to LC_CODE_SIGNATURE's data (the ad hoc signature itself differs per identifier/time),
    else the first differing offset."""
    n = min(sign_offset(a), sign_offset(b))
    if sign_offset(a) != sign_offset(b):
        return "signature offsets differ: %d vs %d" % (sign_offset(a), sign_offset(b))
    with open(a, "rb") as fa, open(b, "rb") as fb:
        pos = 0
        while pos < n:
            ca, cb = fa.read(min(chunk, n - pos)), fb.read(min(chunk, n - pos))
            if ca != cb:
                i = next(i for i in range(min(len(ca), len(cb))) if ca[i] != cb[i])
                return "first difference at file offset 0x%x" % (pos + i)
            pos += len(ca)
    return None


@unittest.skipIf(needs_dump(), needs_dump() or "")
class Real(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pd = P.load_patches(PROFILE)
        cls.scratch = os.environ.get("NPS5_PATCH_SCRATCH") or tempfile.mkdtemp(prefix="nps5patch_real_")
        os.makedirs(cls.scratch, exist_ok=True)
        cls.own = not os.environ.get("NPS5_PATCH_SCRATCH")
        P.SIGN = True
        assert os.path.realpath(cls.scratch) != os.path.realpath(BOOT)
        assert not os.path.realpath(cls.scratch).startswith(os.path.realpath(BOOT) + os.sep), "scratch must not be inside boot"

    @classmethod
    def tearDownClass(cls):
        for f in os.listdir(cls.scratch):
            if f.startswith("native") or f.endswith(".json") or f.endswith(".lock"):
                os.remove(os.path.join(cls.scratch, f))
        if cls.own:
            shutil.rmtree(cls.scratch, ignore_errors=True)

    def produce(self, src, name, request):
        """apply to a scratch copy NAMED like the golden (the ad hoc identifier is the file name); returns its path."""
        out = os.path.join(self.scratch, name)
        P.apply(self.pd, src, P.parse_request(request), out, force=True, tid="PPSA21564")
        return out

    def drop(self, out):
        for p in (out, out + ".patches.json"):
            if os.path.exists(p):
                os.remove(p)

    def test_golden_equivalence(self):
        src_mtime = os.stat(SRC).st_mtime_ns
        done = 0
        for gold, req in GOLD:
            g = os.path.join(BOOT, gold)
            if not os.path.isfile(g):
                continue
            out = self.produce(SRC, gold, req)
            try:
                self.assertIsNone(same_before_signature(out, g), "%s (%s) differs from the existing golden" % (gold, req))
                r = subprocess.run(["codesign", "-v", out], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, "codesign -v %s: %s" % (gold, r.stderr))
                # and the golden itself reads back as applied
                import io
                buf = io.StringIO(); self.assertEqual(P.cmd_check(self.pd, out, req.replace("=1280x720", "").replace("=1600x900", ""), out=buf), 0)
                self.assertNotIn("MISMATCH", buf.getvalue())
            finally:
                self.drop(out)
            done += 1
        self.assertEqual(os.stat(SRC).st_mtime_ns, src_mtime, "source untouched")
        if not done:
            self.skipTest("no golden files present")

    def test_golden_pb_link_needs_lineage_or_symbols(self):
        for plain, gold, req in PB_GOLD:
            s, g = os.path.join(BOOT, plain), os.path.join(BOOT, gold)
            if not (os.path.isfile(s) and os.path.isfile(g)):
                continue
            out = self.produce(s, gold, req)
            try:
                self.assertIsNone(same_before_signature(out, g), gold)
                self.assertEqual(subprocess.run(["codesign", "-v", out], capture_output=True).returncode, 0)
            finally:
                self.drop(out)

    def test_every_native_checks_without_crashing(self):
        """check-only sweep over every native* of the boot directory: PatchError (refused) or a report, never another
        exception. The canonical unpatched links must report every patch applicable; a variant that was hand-modified
        (e.g. native_kyty6_set) legitimately reports MISMATCH, which is the tool doing its job."""
        import io
        clean = []
        for f in sorted(os.listdir(BOOT)):
            p = os.path.join(BOOT, f)
            if not f.startswith("native") or not os.path.isfile(p) or os.path.getsize(p) < 1 << 20:
                continue
            buf = io.StringIO()
            try:
                rc = P.cmd_check(self.pd, p, None, out=buf)
            except P.PatchError:
                continue   # not this build / no symbols: refused
            if f in ("native_kyty6", "native_kyty5"):
                self.assertEqual(rc, 0, "%s: %s" % (f, buf.getvalue()))
                self.assertEqual(buf.getvalue().count("applicable"), 5, buf.getvalue())
                clean.append(f)
        self.assertIn("native_kyty6", clean)

    def test_other_title_binary_refused(self):
        if not os.path.isfile(OTHER):
            self.skipTest("no binary of another title at %s" % OTHER)
        out = os.path.join(self.scratch, "native_other")
        with self.assertRaises(P.PatchError) as cm:
            P.apply(self.pd, OTHER, P.parse_request("dr,nt"), out, tid="PPSA21564")
        self.assertIn("not one of this profile's builds", str(cm.exception))
        self.assertFalse(os.path.exists(out)); self.assertFalse(os.path.exists(out + ".patches.json"))
        self.assertEqual(P.cmd_check.__name__, "cmd_check")
        with self.assertRaises(P.PatchError):
            P.cmd_check(self.pd, OTHER, None)

    def test_corrupted_site_word_refused(self):
        """a clone of the real unpatched binary with ONE site word changed refuses, for every site of dr/nt/lowres."""
        clone = os.path.join(self.scratch, "native_corrupt")
        if os.path.exists(clone):
            os.remove(clone)
        if subprocess.run(["cp", "-c", SRC, clone]).returncode != 0:
            self.skipTest("cp -c (APFS clone) unavailable: not copying 680 MB for a one-word test")
        try:
            mo = P.MachO(clone)
            try:
                ver, _sha = "01.007", None
                reqs = [("dr", None), ("nt", None), ("lowres", "1280x720")]
                res = P.resolve(self.pd, mo, ver, reqs)
                offs = [(mo.fileoff(s.vm), s.old) for _, _, l in res for s in l]
            finally:
                mo.close()
            for fo, old in offs[:4] + offs[-2:]:
                with open(clone, "r+b") as f:
                    f.seek(fo); f.write(struct.pack("<I", old ^ 0x00010000))
                out = os.path.join(self.scratch, "native_corrupt_out")
                with self.assertRaises(P.PatchError, msg="site at file offset 0x%x" % fo) as cm:
                    P.apply(self.pd, clone, reqs, out, tid="PPSA21564")
                self.assertIn("refusing, nothing patched", str(cm.exception))
                self.assertFalse(os.path.exists(out)); self.assertFalse(os.path.exists(out + ".patches.json"))
                import io
                buf = io.StringIO(); rc = P.cmd_check(self.pd, clone, "dr,nt,lowres", out=buf)
                # --check without a size reads a param site holding any size as "already patched": partial, not applicable
                self.assertTrue("MISMATCH" in buf.getvalue() or "partial" in buf.getvalue(), buf.getvalue())
                with open(clone, "r+b") as f:
                    f.seek(fo); f.write(struct.pack("<I", old))
            # restored: applies again
            out = os.path.join(self.scratch, "native_corrupt_out")
            P.apply(self.pd, clone, [("dr", None)], out, tid="PPSA21564"); self.drop(out)
        finally:
            if os.path.exists(clone):
                os.remove(clone)

    def test_patch_twice_is_stable_and_signed(self):
        a = self.produce(SRC, "native_kyty6_drnt", "dr,nt")
        b = os.path.join(self.scratch, "native_kyty6_drnt2")
        try:
            P.apply(self.pd, a, P.parse_request("dr,nt"), b, force=True)
            self.assertIsNone(same_before_signature(a, b), "second run on a patched binary changes nothing")
            self.assertEqual(subprocess.run(["codesign", "-v", b], capture_output=True).returncode, 0)
        finally:
            self.drop(a); self.drop(b)

    def test_launch_smoke_skipped(self):
        """No flag or environment variable makes the translated binary print its build info and exit before the guest
        starts (checked: strings of the binary show none), and game runs are not allowed from the test tier."""
        self.skipTest("no exit-before-guest mode exists; launch smoke deliberately not run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
