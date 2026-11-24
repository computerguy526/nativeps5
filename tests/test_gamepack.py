#!/usr/bin/env python3
"""Tests for scripts/gamepack.py on a synthetic file: pack -> verify -> unpack round trip, --keep, and --dry-run.
No game data: the files are random bytes in a temp dir."""
import contextlib, hashlib, importlib.util, io, os, sys, tempfile, unittest
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gamepack", os.path.join(HERE, "..", "scripts", "gamepack.py"))
gp = importlib.util.module_from_spec(spec); spec.loader.exec_module(gp)

SIZE = 300_017
SEG = 65_537
COMMON = ["--no-game-check", "--margin", "0"]


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sys.argv = ["gamepack"] + list(argv)
        try:
            rc = gp.main()
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue()


def pack_args(path, *extra):
    return ["pack", path, "--seg", str(SEG), "--min-size", "0", "--whole-max", "0", "--ratio-skip", "2"] + COMMON + list(extra)


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmp.name, "Big-ps5.ucas")
        self.data = os.urandom(SIZE)
        with open(self.p, "wb") as f: f.write(self.data)
        self.sd = self.p + ".nps5seg"

    def tearDown(self): self.tmp.cleanup()

    def test_pack_verify_unpack(self):
        rc, _ = run(*pack_args(self.p))
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.p))  # the original goes only after the verify inside pack passed
        self.assertTrue(os.path.isfile(os.path.join(self.sd, "manifest.json")))
        self.assertEqual(len([n for n in os.listdir(self.sd) if n != "manifest.json"]), -(-SIZE // SEG))
        self.assertEqual(run("verify", self.p, *COMMON)[0], 0)
        self.assertEqual(run("unpack", self.p, *COMMON)[0], 0)
        with open(self.p, "rb") as f: self.assertEqual(hashlib.sha256(f.read()).digest(), hashlib.sha256(self.data).digest())
        self.assertFalse(os.path.exists(self.sd))

    def test_keep_leaves_original(self):
        self.assertEqual(run(*pack_args(self.p, "--keep"))[0], 0)
        self.assertTrue(os.path.exists(self.p))
        self.assertTrue(os.path.isdir(self.sd))
        with open(self.p, "rb") as f: self.assertEqual(f.read(), self.data)

    def test_dry_run_changes_nothing(self):
        self.assertEqual(run(*pack_args(self.p, "--dry-run"))[0], 0)
        self.assertTrue(os.path.exists(self.p))
        self.assertFalse(os.path.exists(self.sd))

    def test_verify_detects_corruption(self):
        run(*pack_args(self.p))
        seg = os.path.join(self.sd, "0001")
        with open(seg, "r+b") as f: f.write(b"\xff\xfe\xfd")
        self.assertNotEqual(run("verify", self.p, *COMMON)[0], 0)

    def test_refuses_while_game_runs(self):
        orig = gp.game_running
        gp.game_running = lambda: ["1 /x/native_demo"]
        try:
            rc, _ = run("pack", self.p, "--min-size", "0")
        finally:
            gp.game_running = orig
        self.assertNotEqual(rc, 0)
        self.assertTrue(os.path.exists(self.p))

    def test_game_pattern(self):
        for ok in ("/opt/x/native_demo", "native", "/a/ue2boot/native_out3"):
            self.assertTrue(gp.GAME_RE.search(ok), ok)
        self.assertFalse(gp.GAME_RE.search("/usr/bin/python3"))

    def test_help(self):
        self.assertIn("--keep", gp.__doc__)
        self.assertIn("NPS5_VFS_SEGFILES=0", gp.__doc__)


if __name__ == "__main__":
    unittest.main()
