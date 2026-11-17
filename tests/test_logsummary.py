#!/usr/bin/env python3
"""Tests for scripts/nps5lib/logsummary.py: levels, rate limiting, the SUMMARY block, the CLI. Synthetic logs only."""
import importlib.util, io, os, sys, tempfile, unittest, contextlib, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("logsummary", os.path.join(HERE, "..", "scripts", "nps5lib", "logsummary.py"))
ls = importlib.util.module_from_spec(spec); spec.loader.exec_module(ls)

LOG = ["[boot] start"] + ["[progress-title] %d ms: x, frame: %d, fps: %d — compiling shaders 1/9 + live 3" % (i, i, 20 + i % 5) for i in range(100)] + \
      ["[framerate] frame 5 measured 40.0 fps (ema 40.0) block now"] + ["[kk-archive] stats (periodic): libraries %d pending 1" % i for i in range(300)] + \
      ["[kyty-seam] W slow page", "[x] RED self memory limit: exiting cleanly (exit 3)", "[psN-recomp] WARN: unimplemented", "[g] assert failed: x", "[d] D chatty", "[end] done"]


class Levels(unittest.TestCase):
    def test_level_of(self):
        self.assertEqual(ls.level_of("[a] E broke"), "E"); self.assertEqual(ls.level_of("[a] W hmm"), "W")
        self.assertEqual(ls.level_of("[a] D x"), "D"); self.assertEqual(ls.level_of("[a] plain"), "I")
        self.assertEqual(ls.level_of("[a] FATAL: boom"), "E"); self.assertEqual(ls.level_of("[a] WARN: x"), "W")
        self.assertEqual(ls.subsystem_of("[kk-archive] stats"), "kk-archive"); self.assertIsNone(ls.subsystem_of("no tag"))

    def test_filter_level(self):
        out = ls.filter_level(LOG, "W")
        self.assertIn("[kyty-seam] W slow page", out); self.assertIn("[g] assert failed: x", out)
        self.assertNotIn("[d] D chatty", out); self.assertNotIn("[end] done", out)
        self.assertIn("[d] D chatty", ls.filter_level(LOG, "D"))


class Rate(unittest.TestCase):
    def test_first_every_and_summary(self):
        out = ls.rate_limit(["[kk-archive] stats (periodic): libraries %d" % i for i in range(300)], 3, 50)
        shown = [l for l in out if l.startswith("[kk-archive]")]
        self.assertEqual(len(shown), 3 + 6)                       # first 3, then lines 50,100,...,300
        self.assertTrue(any(l.startswith("(x300 repeated, 9 shown)") for l in out))

    def test_warnings_never_cut(self):
        out = ls.rate_limit(["[a] W same"] * 100 + ["[b] ERROR same"] * 100, 1, 0)
        self.assertEqual(len([l for l in out if "same" in l and not l.startswith("(x")]), 200)


class Summary(unittest.TestCase):
    def test_block(self):
        s = ls.summary("\n".join(LOG), dict(verdict="DONE_UNTIL", timings=dict(wall_s=12.5), env={"NPS5_X": "1", "SECRET_TOKEN": "z"}, **{"class": "func"}))
        for want in ("==== SUMMARY ====", "DONE_UNTIL (12.5s wall)", "fps       p50", "3 live shader compile", "crashes    ", "settings  NPS5_X=1"):
            self.assertIn(want, s)
        self.assertNotIn("SECRET_TOKEN", s)
        self.assertIn("assert failed", s)
        self.assertIn("self memory limit", s)                     # a guard line

    def test_empty_log(self):
        s = ls.summary("")
        self.assertIn("no fps lines", s); self.assertIn("crashes    none", s)

    def test_write_run_summary(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "run.err"), "w").write("\n".join(LOG))
            t = ls.write_run_summary(d, {"verdict": "CRASH"})
            self.assertEqual(open(os.path.join(d, "run.summary.txt")).read(), t)
            self.assertIn("CRASH", t)
            self.assertIn("no fps", ls.write_run_summary(tempfile.mkdtemp(), {}))   # missing log: no exception


class Cli(unittest.TestCase):
    def run_cli(self, *a):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = ls.main(list(a), {})
        return rc, buf.getvalue()

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.err"); open(p, "w").write("\n".join(LOG))
            rc, o = self.run_cli(p, "--summary"); self.assertEqual(rc, 0); self.assertTrue(o.startswith("==== SUMMARY"))
            rc, o = self.run_cli(p, "--rate", "2,100"); self.assertEqual(rc, 0); self.assertIn("(x300 repeated", o)
            rc, o = self.run_cli(p, "--level", "W", "--rate", "1,0"); self.assertNotIn("chatty", o)
            self.assertEqual(self.run_cli()[0], 2); self.assertEqual(self.run_cli(os.path.join(d, "nope"))[0], 1)


if __name__ == "__main__":
    unittest.main()
