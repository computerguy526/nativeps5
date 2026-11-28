#!/usr/bin/env python3
"""Tests of scripts/nps5lib/indreg.py (synthetic run.err text and synthetic save dirs; no game data)."""
import importlib.util, json, os, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("indreg", os.path.join(HERE, "..", "scripts", "nps5lib", "indreg.py"))
ir = importlib.util.module_from_spec(spec); spec.loader.exec_module(ir)

ENTER = "[scenery] scene lvl_x: 0 shader pairs active (full)\n"
FRONT = "[indirect] draw SKIPPED (no base): op=index data_offset=0x0 initiator=0x0 submit=1 base=0x0 last_set_base(type=0 addr=0x0 n=2) skipped=1\n"
BACK = "[indirect] draw index SKIPPED (args in null page): args=0x20 bytes=0x14 skipped=1\n"
CRASH = "\nFATAL: Signal 10 at host address 0x10000000030 (no guest context)\nSignal 10 on host thread 1 'kyty:Thread_Gpu'\n"
DONE = {"verdict": "DONE_UNTIL"}


class T(unittest.TestCase):
    def test_fixed_run_passes_and_counts_the_skips(self):
        ok, p, info = ir.judge("boot\n" + ENTER + FRONT + BACK + "[framerate] ...\n", DONE, "lvl_x")
        self.assertTrue(ok, p); self.assertEqual((info["front_skips"], info["back_skips"]), (1, 1))

    def test_the_incident_is_named_indcrash(self):
        ok, p, _ = ir.judge(ENTER + FRONT + CRASH, {"verdict": "FAIL_RC1"}, "lvl_x")
        self.assertFalse(ok); self.assertTrue(any(x.startswith("indcrash class") for x in p), p)

    def test_other_fatal_is_a_plain_fatal(self):
        ok, p, _ = ir.judge(ENTER + CRASH, DONE, "lvl_x")
        self.assertFalse(ok); self.assertTrue(any(x.startswith("FATAL:") for x in p), p)

    def test_scene_must_be_entered_and_held(self):
        ok, p, _ = ir.judge("[scenery] scene worldmap: x\n", DONE, "lvl_x")
        self.assertFalse(ok); self.assertIn("scene lvl_x never entered", p)
        ok, p, _ = ir.judge(ENTER, {"verdict": "TIMEOUT"}, "lvl_x")
        self.assertFalse(ok)
        self.assertTrue(ir.entered("[scene-open] lvl_x t=3\n", "lvl_x"))
        self.assertFalse(ir.entered("[scenery] scene lvl_x2: y\n", "lvl_x"))

    def test_check_reads_a_run_dir(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "run.err"), "w").write(ENTER)
            ok, p, _ = ir.check(d, "lvl_x"); self.assertFalse(ok); self.assertTrue(p[0].startswith("no run.json"))
            json.dump(DONE, open(os.path.join(d, "run.json"), "w"))
            self.assertTrue(ir.check(d, "lvl_x")[0])
            self.assertEqual(ir.main(["check", d, "--scene", "lvl_x"]), 0)

    def test_prepare_copies_a_backup_and_refuses_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            live = os.path.join(d, "userdata"); fx = os.path.join(d, "userdata.bak-MASTER-1")
            for root in (live, fx):
                os.makedirs(os.path.join(root, "savedata", "u")); open(os.path.join(root, "savedata", "u", "m.dat"), "w").write("synthetic")
            dest = os.path.join(d, "scratch", "userdata", "T", "indreg_1")
            self.assertEqual(ir.prepare(fx, dest), [])
            self.assertEqual(open(os.path.join(dest, "savedata", "u", "m.dat")).read(), "synthetic")
            self.assertTrue(os.path.exists(os.path.join(fx, "savedata", "u", "m.dat")))   # fixture untouched
            self.assertIn("exists", ir.prepare(fx, dest)[0])
            self.assertIn("live save", ir.prepare(live, dest + "b")[0])
            self.assertIn("inside the fixture", ir.prepare(fx, os.path.join(fx, "x"))[0])
            os.makedirs(os.path.join(d, "repo", ".git"))
            self.assertIn("git checkout", ir.prepare(fx, os.path.join(d, "repo", "saves", "s"))[0])


if __name__ == "__main__":
    unittest.main()
