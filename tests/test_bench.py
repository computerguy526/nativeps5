#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/bench.py: bench.tsv, window maths on stored perf_frames.csv + run.err logs, ABBA plans, the quiet
gate, the arm summary and the CSV / markdown / json outputs. No title is run."""
import io, json, os, shutil, sys, tempfile, time, unittest, warnings
warnings.simplefilter("ignore", ResourceWarning)
from contextlib import redirect_stdout, redirect_stderr
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evfix  # noqa: E402,F401
import bench  # noqa: E402
import frames as fr  # noqa: E402
import run as runmod  # noqa: E402

GAME = "title_id\tPPSA00001\nname\tFixture\nbuild\t01.001\teboot=0123456789abcdef\npackage\t01.001\ngpu\tkyty\ncanonical_lineage\tlin1\nscene_open_key\tlevels/\nmarker\tworldmap\topen:levels/worldmap/\n"
SCENES = "worldmap\t-\t-\tmarker:worldmap\t90\tnew\tw\nlvl\t-x\t-\tscene:lvl\t180\tnew\tl\n"
BENCH = "# c\nwm\tworldmap\tmarker:worldmap+15\tperf\tworld map\nlv\tlvl\tpresent:lvl+100\tperf\tlevel\nsec\tworldmap\t10s..30s\tperf\tseconds\n"
ERR = "x\n[scene-open] worldmap t=5.0\n[scene-open] lvl t=40.0\n[kk-archive] stats libraries 0 (0 slow, 0 ms), pipelines archive-hit 9 (1 ms) compiled 3 (0 slow, 0 ms)\n"


def make_run(root, tid, tag, ms_a, ms_b, verdict="DONE_FIXED", err=ERR, total=60.0):
    """60 s run: frames of ms_a for the first 30 s then ms_b (frame times drive the clock)."""
    d = os.path.join(root, "runs", tid, tag); os.makedirs(os.path.join(d, "frames"))
    t, ms = 0.0, []
    for per, until in ((ms_a, 30.0), (ms_b, total)):
        while t < until:
            ms.append(per); t += per / 1000.0
    open(d + "/frames/perf_frames.csv", "w").write(evfix.perf_csv(ms))
    open(d + "/run.err", "w").write(err)
    json.dump(dict(timings=dict(wall_s=total), verdict=verdict, rc=0), open(d + "/run.json", "w"))
    return d


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        gd = os.path.join(self.tmp, "games", "PPSA00001"); os.makedirs(gd)
        for n, c in (("game.tsv", GAME), ("scenes.tsv", SCENES), ("bench.tsv", BENCH)): open(os.path.join(gd, n), "w").write(c)
        self.env = dict(NPS5_GAMES_DIR=os.path.join(self.tmp, "games"), NPS5_GAMES_FIRST="1", NPS5_SCRATCH=self.tmp, NPS5_SELF_MEM_LIMIT_MB="18944",
                        NPS5_PUBLIC_DIR=self.tmp, NPS5_KK_ICD="x", NPS5_RUNS_ROOT=os.path.join(self.tmp, "runs"), PATH=os.environ["PATH"])
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def main(self, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = bench.main(argv, dict(self.env), **kw)
        return rc, out.getvalue(), err.getvalue()

    def test_read_routes(self):
        r = bench.read_routes(os.path.join(self.tmp, "games", "PPSA00001", "bench.tsv"))
        self.assertEqual(list(r), ["wm", "lv", "sec"]); self.assertEqual(r["lv"]["window"], "present:lvl+100")

    def test_scene_opens(self):
        self.assertEqual(bench.scene_opens(ERR), {"worldmap": 5.0, "lvl": 40.0})
        self.assertEqual(bench.scene_opens("[level-open] a t=1.5\n[scene-open] a t=9\n"), {"a": 1.5})

    def test_marker_window_starts_after_the_marker(self):
        f = fr.parse_perf_csv(evfix.perf_csv([10.0] * 6000), 60.0)
        a, b = bench.window_bounds("marker:worldmap+15", f, 60.0, {"worldmap": 5.0}, {"worldmap": "open:levels/worldmap/"})
        self.assertEqual((a, b), (20.0, 59.0))

    def test_present_window_counts_presents_after_the_open(self):
        f = fr.parse_perf_csv(evfix.perf_csv([10.0] * 6000), 60.0)
        a, b = bench.window_bounds("present:lvl+100", f, 60.0, {"lvl": 40.0})
        self.assertAlmostEqual(a, 41.0, delta=0.02); self.assertEqual(b, 59.0)
        with self.assertRaises(fr.FramesError): bench.window_bounds("present:lvl+99999", f, 60.0, {"lvl": 40.0})
        with self.assertRaises(fr.FramesError): bench.window_bounds("present:other+1", f, 60.0, {"lvl": 40.0})

    def test_seconds_window_is_after_the_first_present(self):
        f = fr.parse_perf_csv(evfix.perf_csv([10.0] * 6000), 60.0)
        a, b = bench.window_bounds("10s..30s", f, 60.0, {})
        self.assertAlmostEqual(a, f.t[0] + 10); self.assertAlmostEqual(b, f.t[0] + 30)
        with self.assertRaises(bench.BenchError): bench.window_bounds("garbage", f, 60.0, {})

    def test_marker_log_and_unknown(self):
        self.assertEqual(bench.marker_time("log:READY", {}, "foo\nREADY t=12.5\n"), 12.5)
        with self.assertRaises(fr.FramesError): bench.marker_time("log:READY", {}, "READY\n")
        with self.assertRaises(fr.FramesError): bench.marker_time("open:nothing/", {"a": 1.0}, "")

    def test_measure_uses_only_the_window(self):
        d = make_run(self.tmp, "PPSA00001", "r1", 50.0, 10.0)                  # slow 50 ms before 30 s, fast 10 ms after
        m = bench.measure(d, "marker:worldmap+15", {"worldmap": "open:levels/worldmap/"})   # window 20..59: 10 s at 50 ms + the rest at 10 ms
        self.assertEqual(m["compiles"], 3); self.assertEqual(m["verdict"], "DONE_FIXED")
        m2 = bench.measure(d, "35s..55s")
        self.assertAlmostEqual(m2["p50"], 10.0, places=1); self.assertAlmostEqual(m2["p99"], 10.0, places=1); self.assertAlmostEqual(m2["fps"], 100.0, places=0)
        m3 = bench.measure(d, "1s..25s")
        self.assertAlmostEqual(m3["p50"], 50.0, places=1)

    def test_summarize_arms_verdicts_need_two_runs_per_arm(self):
        mk = lambda p50: dict(run="x", n=100, p50=p50, p95=p50 + 1, p99=p50 + 2, fps=1000 / p50, noisy=False)
        one = bench.summarize_arms([("a", [mk(20.0)]), ("b", [mk(10.0)])])
        self.assertEqual(one["pairs"][0]["verdict"], "insufficient")
        two = bench.summarize_arms([("a", [mk(20.0), mk(20.2), mk(19.9), mk(20.1), mk(20.0)]), ("b", [mk(10.0), mk(10.1), mk(9.9), mk(10.0), mk(10.2)])])
        self.assertEqual([p["verdict"] for p in two["pairs"] if p["metric"] == "p50"], ["better"])
        self.assertEqual([p["verdict"] for p in two["pairs"] if p["metric"] == "fps"], ["better"])

    def test_quiet_gate(self):
        ok = bench.quiet_gate(lambda: "AC", lambda: 3.0, lambda: [], lambda: False)
        self.assertEqual(ok, (True, []))
        ok, why = bench.quiet_gate(lambda: "battery", lambda: 80.0, lambda: ["123 native_x"], lambda: True)
        self.assertFalse(ok); self.assertEqual(len(why), 4)
        self.assertEqual(bench.quiet_gate(lambda: "AC", lambda: None, lambda: [], None)[0], True)    # unreadable GPU use is not a reason

    def test_wait_quiet_gives_up_after_the_wait(self):
        t = [0.0]
        ok, why = bench.wait_quiet(lambda: (False, ["noisy"]), 50, sleep=lambda s: t.__setitem__(0, t[0] + s), now=lambda: t[0])
        self.assertFalse(ok); self.assertGreaterEqual(t[0], 50)
        self.assertEqual(bench.wait_quiet(lambda: (True, []), 50, sleep=lambda s: 1 / 0, now=lambda: 0)[0], True)

    def test_start_hold_and_bad_counts(self):
        d = tempfile.mkdtemp()
        self.assertFalse(bench.start_hold(d))
        open(os.path.join(d, "start.hold"), "w").close()
        self.assertTrue(bench.start_hold(d)); self.assertFalse(bench.start_hold(d, now=lambda: time.time() + 60))
        ok, why = bench.quiet_gate(power=lambda: "AC", others=lambda: 0, procs=lambda: [], hold=lambda: bench.start_hold(d))
        self.assertFalse(ok); self.assertIn("start hold", why[0])
        for bad in (["X", "--route", "r", "--runs", "0"], ["X", "--route", "r", "--runs", "-2"], ["X", "--quiet-wait", "-1"], ["X", "--min-effect", "nan"]):
            with self.assertRaises(bench.BenchError, msg=str(bad)): bench.parse(bad)

    def test_dry_run_plans_abba(self):
        rc, out, _ = self.main(["PPSA00001", "--route", "wm", "--arm", "a:X=1", "--arm", "b:X=2", "--dry-run"])
        self.assertEqual(rc, 0)
        labels = [l[1] for l in out.splitlines() if l.startswith("[")]
        self.assertEqual(labels, ["a", "b", "b", "a"])
        self.assertIn("--class perf", out); self.assertIn("--scene worldmap", out); self.assertIn("--arm a:X=1", out)

    def test_single_arm_default_and_three_arms(self):
        rc, out, _ = self.main(["PPSA00001", "--route", "wm", "--runs", "2", "--dry-run"])
        self.assertEqual([l[1] for l in out.splitlines() if l.startswith("[")], ["b", "b"]); self.assertNotIn("--arm", out)
        rc, out, _ = self.main(["PPSA00001", "--route", "wm", "--arm", "a:A=1", "--arm", "b:A=2", "--arm", "c:A=3", "--runs", "1", "--dry-run"])
        self.assertEqual([l[1] for l in out.splitlines() if l.startswith("[")], ["a", "b", "c"])

    def test_refusals(self):
        self.assertEqual(self.main(["PPSA00001"])[0], 2)
        self.assertEqual(self.main(["PPSA00001", "--route", "nope"])[0], 2)
        self.assertEqual(self.main(["PPSA00001", "--route", "wm", "--arm", "a:X=1", "--arm", "a:X=2", "--dry-run"])[0], 2)

    def test_list(self):
        rc, out, _ = self.main(["PPSA00001", "--list"])
        self.assertIn("present:lvl+100", out)

    def test_full_run_with_fake_runs_writes_outputs_and_marks_noisy(self):
        started = []
        def caller(cmd, env=None):
            tag = cmd[cmd.index("--tag") + 1]; started.append(tag)
            arm = cmd[cmd.index("--arm") + 1].split(":")[0]
            make_run(self.tmp, "PPSA00001", tag, 20.0 if arm == "b" else 10.0, 10.0)
            return 0
        out_dir = os.path.join(self.tmp, "out")
        rc, out, err = self.main(["PPSA00001", "--route", "sec", "--arm", "a:X=1", "--arm", "b:X=2", "--runs", "2", "--out", out_dir], caller=caller, gate=lambda: (False, ["not on AC power"]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(started), 4)
        for f in ("results.json", "results.md", "runs.csv", "arms.csv"): self.assertTrue(os.path.isfile(os.path.join(out_dir, f)), f)
        res = json.load(open(os.path.join(out_dir, "results.json")))
        self.assertEqual([a["label"] for a in res["arms"]], ["a", "b"])
        self.assertIn("NOISY", open(os.path.join(out_dir, "results.md")).read())
        rows = open(os.path.join(out_dir, "runs.csv")).read().splitlines()
        self.assertEqual(len(rows), 5); self.assertTrue(rows[1].endswith(",1"))        # noisy flag
        self.assertAlmostEqual(res["arms"][1]["p50_mean"], 20.0, places=1)

    def test_require_quiet_refuses_before_any_run(self):
        rc, _, err = self.main(["PPSA00001", "--route", "wm", "--require-quiet"], caller=lambda *a, **k: 1 / 0, gate=lambda: (False, ["other GPU use 90 ms/s"]))
        self.assertEqual(rc, 75); self.assertIn("not quiet", err)

    def test_failed_run_stops(self):
        rc, _, err = self.main(["PPSA00001", "--route", "wm"], caller=lambda cmd, env=None: 78, gate=lambda: (True, []))
        self.assertEqual(rc, 1); self.assertIn("exited 78", err)

    def test_from_runs_needs_no_title_run(self):
        for i, ms in enumerate((10.0, 10.1)): make_run(self.tmp, "PPSA00001", "a%d" % i, 10.0, ms)
        for i, ms in enumerate((20.0, 20.1)): make_run(self.tmp, "PPSA00001", "b%d" % i, 10.0, ms)
        out_dir = os.path.join(self.tmp, "o2")
        rc, out, _ = self.main(["PPSA00001", "--route", "sec", "--from-runs", "--arm", "a:a0,a1", "--arm", "b:b0,b1", "--out", out_dir], caller=lambda *a, **k: 1 / 0)
        self.assertEqual(rc, 0); self.assertIn("| a |", out)


if __name__ == "__main__":
    unittest.main()
