#!/usr/bin/env python3
"""Tests for ab.py and report.py: run ordering, from-runs analysis with verdicts, one-run refusal, loading-phase handling,
the evidence bundle (scrubbing, sections) and that the bundle passes scripts/check.sh."""
import contextlib, io, json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evfix  # noqa: E402
import ab  # noqa: E402
import bmp  # noqa: E402
import report  # noqa: E402

HOME = "/Us" + "ers/someone"     # built in two parts so the repo check does not flag the fixture itself
REPO = os.path.abspath(os.path.join(evfix.HERE, ".."))


def steady(base, seed, n=1500, loading=True):
    """Wall times: optional 10 s loading phase of 500 ms frames, then n frames around `base` ms."""
    return ([500.0] * 20 if loading else []) + evfix.jitter(n, base, seed)


def call(mod, args, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = mod.main(args, env or {})
    return rc, out.getvalue(), err.getvalue()


class Sequence(unittest.TestCase):
    def test_abba(self):
        self.assertEqual(ab.sequence(2, 2, "ABBA"), [0, 1, 1, 0])
        self.assertEqual(ab.sequence(2, 3, "ABBA"), [0, 1, 1, 0, 0, 1])
        self.assertEqual(ab.sequence(2, 1, "AB"), [0, 1])
        for s in ("AAAA", "ABC", "", "A"):
            with self.assertRaises(ValueError):
                ab.sequence(2, 2, s)
        for reps in (1, 2, 3, 5):
            seq = ab.sequence(2, reps, "ABBA")
            self.assertEqual((seq.count(0), seq.count(1)), (reps, reps))

    def test_dry_run_plan(self):
        rc, out, _ = call(ab, ["T", "--arm", "A:X=1", "--arm", "B:X=2", "--reps", "2", "--prefix", "p", "--scene", "hoover", "--dry-run"])
        self.assertEqual(rc, 0)
        lines = out.strip().splitlines()
        self.assertEqual([l[:3] for l in lines], ["[A]", "[B]", "[B]", "[A]"])
        self.assertIn("--tag p-A1", lines[0])
        self.assertIn("--tag p-B2", lines[2])
        self.assertIn("--tag p-A2", lines[3])
        self.assertIn("--arm A:X=1", lines[0])
        self.assertIn("--scene hoover", lines[0])
        self.assertIn("--class perf", lines[0])

    def test_usage_errors(self):
        self.assertEqual(call(ab, [])[0], 2)
        self.assertEqual(call(ab, ["T", "--arm", "A:x"])[0], 2)                       # needs two arms
        self.assertEqual(call(ab, ["T", "--arm", "A:x", "--arm", "nolabel"])[0], 2)
        self.assertEqual(call(ab, ["T", "--arm", "A:x", "--arm", "B:y", "--reps", "0", "--dry-run"])[0], 2)
        self.assertEqual(call(ab, ["T", "--arm", "A:x", "--arm", "B:y", "--order", "AAC", "--dry-run"])[0], 2)
        self.assertEqual(call(ab, ["T", "--arm", "A:x", "--arm", "B:y", "--reps", "x"])[0], 2)
        self.assertEqual(call(ab, ["T", "--arm", "A:x", "--arm", "B:y", "--bogus"])[0], 2)


class FromRuns(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.td.name, "runs")
        self.env = {"NPS5_RUNS_ROOT": self.root}

    def tearDown(self):
        self.td.cleanup()

    def mk(self, tag, base, seed, **kw):
        return evfix.make_run(self.root, "T", tag, steady(base, seed, **kw))

    def test_clear_win_with_loading_phases(self):
        for i in range(3):
            self.mk(f"a{i}", 20.0, i)
            self.mk(f"b{i}", 16.0, 10 + i)
        rc, out, _ = call(ab, ["T", "--from-runs", "--arm", "A:a0,a1,a2", "--arm", "B:b0,b1,b2"], self.env)
        self.assertEqual(rc, 0)
        self.assertIn("p50 B vs A: -4.", out)
        self.assertIn("BETTER", out)
        self.assertIn("fps B vs A: +", out)
        res = json.loads(call(ab, ["T", "--from-runs", "--arm", "A:a0,a1,a2", "--arm", "B:b0,b1,b2", "--json"], self.env)[1])
        self.assertEqual(res["compare"]["p50"]["verdict"], "better")
        r = res["arms"][0]["runs"][0]
        self.assertAlmostEqual(r["p50"], 20.0, delta=0.4)         # the 10 s of 500 ms loading frames did not leak in
        self.assertAlmostEqual(r["fps"], 50.0, delta=1.0)

    def test_regression_is_worse(self):
        for i in range(3):
            self.mk(f"a{i}", 16.0, i)
            self.mk(f"b{i}", 20.0, 10 + i)
        res = json.loads(call(ab, ["T", "--from-runs", "--arm", "A:a0,a1,a2", "--arm", "B:b0,b1,b2", "--json"], self.env)[1])
        self.assertEqual(res["compare"]["p50"]["verdict"], "worse")
        self.assertEqual(res["compare"]["fps"]["verdict"], "worse")

    def test_same_config_is_no_difference(self):
        for i in range(3):
            self.mk(f"a{i}", 20.0, i)
            self.mk(f"b{i}", 20.0, 10 + i)
        res = json.loads(call(ab, ["T", "--from-runs", "--arm", "A:a0,a1,a2", "--arm", "B:b0,b1,b2", "--json"], self.env)[1])
        self.assertEqual(res["compare"]["p50"]["verdict"], "no-difference")

    def test_one_run_per_arm_never_a_winner(self):
        self.mk("a0", 20.0, 1)
        self.mk("b0", 10.0, 2)                                    # twice as fast, still no verdict from one run
        rc, out, _ = call(ab, ["T", "--from-runs", "--arm", "A:a0", "--arm", "B:b0"], self.env)
        self.assertEqual(rc, 0)
        self.assertIn("insufficient", out)
        self.assertNotIn("BETTER", out)
        self.assertNotIn("WORSE", out)

    def test_missing_and_unusable_runs_are_listed_and_not_counted(self):
        self.mk("a0", 20.0, 1)
        self.mk("a1", 20.0, 2)
        d = self.mk("b0", 20.0, 3)
        os.remove(os.path.join(d, "frames", "perf_frames.csv"))
        rc, out, _ = call(ab, ["T", "--from-runs", "--arm", "A:a0,a1", "--arm", "B:b0,nope"], self.env)
        self.assertEqual(rc, 1)                                   # arm B has no usable run
        self.assertEqual(out.count("skipped"), 2)
        self.assertIn("insufficient", out)

    def test_run_dir_path_accepted(self):
        d1, d2 = self.mk("a0", 20.0, 1), self.mk("b0", 20.0, 2)
        rc, out, _ = call(ab, ["T", "--from-runs", "--arm", f"A:{d1}", "--arm", f"B:{d2}"], {})
        self.assertEqual(rc, 0)
        self.assertIn("arm A: 1 run", out)


class Report(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.td.name, "runs")
        self.env = {"NPS5_RUNS_ROOT": self.root}
        bad = "[guard] watchdog fired at " + HOME + "/secret/path/file.dat after 90s\n[run_sandboxed] title run capped 180s -> 90s\nplain line\n"
        envA = {"APS5_X": "1", "NPS5_RUNS_DIR": HOME + "/scratch/runs"}
        envB = {"APS5_X": "2", "APS5_NEW": "on", "NPS5_RUNS_DIR": HOME + "/scratch/runs"}
        pic = lambda v: {"frame_100.bmp": bmp.make_bmp(8, 8, lambda x, y: (v, v, v)), "frame_200.bmp": bmp.make_bmp(8, 8, lambda x, y: (v, v, v))}  # noqa: E731
        for i in range(2):
            evfix.make_run(self.root, "T", f"a{i}", steady(20.0, i), env=envA, comps={"public": "abc1234", "kk": "def5678"}, logs={"run.err": bad}, bmps=pic(100))
            evfix.make_run(self.root, "T", f"b{i}", steady(15.0, 5 + i), arm="fast", env=envB, comps={"public": "abc1234", "kk": "0123456"}, bmps=pic(120))
        self.x = []
        for name, v in (("x1.csv", "10"), ("x2.csv", "8")):
            p = os.path.join(self.td.name, name)
            evfix.write(p, f"input,id,agx_instr,agx_gpr,error\nabcdef0123456789,s1,{v},4,\n")
            self.x.append(p)

    def tearDown(self):
        self.td.cleanup()

    def test_bundle(self):
        out = os.path.join(self.td.name, "out")
        rc, _, _ = call(report, ["T/a0", "T/a1", "T/b0", "T/b1", "--out", out, "--xray", *self.x], self.env)
        self.assertEqual(rc, 0)
        with open(os.path.join(out, "report.md")) as f:
            md = f.read()
        for want in ("## Runs", "## Components", "## Environment differences vs `a0`", "## Frame times", "## Pictures", "## Guard, watchdog and cap lines",
                     "## Shader deltas", "`APS5_X` 1 -> 2", "`APS5_NEW` (unset) -> on", "agx_instr 10 -> 8", "**better**", "![A | B | difference](sheet.png)",
                     "title run capped", "watchdog fired", "| fast | 2 |"):
            self.assertIn(want, md)
        self.assertNotIn(HOME[:6], md)                              # no absolute paths anywhere
        self.assertNotIn("someone", md)
        self.assertIn("<path>", md)
        self.assertTrue(os.path.getsize(os.path.join(out, "sheet.png")) > 100)
        self.assertIn("mean abs diff 20.000", md)

    def test_single_run_has_no_ci_and_no_verdict(self):
        rc, out, _ = call(report, ["T/a0"], self.env)
        self.assertEqual(rc, 0)
        self.assertIn("one run: no CI", out)
        self.assertNotIn("**better**", out)

    def test_bundle_passes_the_repo_check(self):
        out = os.path.join(self.td.name, "out")
        call(report, ["T/a0", "T/a1", "T/b0", "T/b1", "--out", out], self.env)
        repo = os.path.join(self.td.name, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        with open(os.path.join(out, "report.md")) as f:
            evfix.write(os.path.join(repo, "report.md"), f.read())
        subprocess.run(["git", "add", "report.md"], cwd=repo, check=True)
        p = subprocess.run([os.path.join(REPO, "scripts", "check.sh")], cwd=repo, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_errors(self):
        self.assertEqual(call(report, [], self.env)[0], 2)
        self.assertEqual(call(report, ["T/missing"], self.env)[0], 1)
        self.assertEqual(call(report, ["T/a0", "--bogus"], self.env)[0], 2)
        self.assertEqual(call(report, ["T/a0", "--xray", "x"], self.env)[0], 2)

    def test_scrub(self):
        self.assertEqual(report.scrub("see " + HOME + "/y.log and ~/z, ok"), "see <path> and <path>, ok")
        self.assertEqual(report.scrub("ratio a/b 3/4 stays"), "ratio a/b 3/4 stays")
        self.assertLessEqual(len(report.scrub("x" * 500)), 220)


if __name__ == "__main__":
    unittest.main()
