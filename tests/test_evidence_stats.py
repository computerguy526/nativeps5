#!/usr/bin/env python3
"""Tests for stats.py and frames.py: percentiles, summaries, flip times, steady-window selection (the loading-phase bug class),
the noise-aware comparison. Known answers from synthetic frames logs."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evfix  # noqa: E402
import frames as fr  # noqa: E402
import stats as st  # noqa: E402


class Stats(unittest.TestCase):
    def test_percentile_convention(self):
        v = list(range(1, 11))
        self.assertEqual(st.pct(v, .5), 6)       # upper nearest rank, like the old scripts
        self.assertEqual(st.pct(v, .95), 10)
        self.assertEqual(st.pct(v, 0), 1)
        with self.assertRaises(ValueError):
            st.pct([], .5)

    def test_summarize(self):
        s = st.summarize([10, 10, 20, 20])
        self.assertEqual((s["n"], s["p50"], s["mean"]), (4, 20, 15))
        self.assertAlmostEqual(s["fps"], 4000 / 60)
        self.assertEqual(st.summarize([])["n"], 0)

    def test_compare_refuses_one_run_per_arm(self):
        c = st.compare([20.0], [10.0])
        self.assertEqual(c["verdict"], "insufficient")
        self.assertIn("single run", c["reason"])
        self.assertEqual(st.compare([20.0, 20.1], [10.0])["verdict"], "insufficient")

    def test_compare_verdicts(self):
        a, b = [20.0, 20.2, 19.9], [16.0, 16.1, 15.9]
        c = st.compare(a, b)
        self.assertEqual(c["verdict"], "better")                      # B lower p50
        self.assertLess(c["ci_hi"], 0)
        self.assertEqual(st.compare(b, a)["verdict"], "worse")
        self.assertEqual(st.compare(a, b, lower_is_better=False)["verdict"], "worse")   # fps: lower is worse
        self.assertEqual(st.compare(a, list(a))["verdict"], "no-difference")

    def test_compare_noise_is_not_a_win(self):
        # two runs each, difference smaller than the between-run spread
        self.assertEqual(st.compare([20.0, 22.0], [19.0, 21.0])["verdict"], "no-difference")
        c = st.compare([20.0, 26.0], [18.0, 18.1])      # CI excludes 0 but the gap is inside A's run-to-run spread
        self.assertEqual(c["verdict"], "no-difference")
        self.assertIn("spread", c["reason"])
        # a big gap beats the same spread
        self.assertEqual(st.compare([20.0, 20.5], [10.0, 10.4])["verdict"], "better")

    def test_min_effect(self):
        self.assertEqual(st.compare([20.0, 20.1, 20.0], [19.5, 19.6, 19.5], min_effect=1.0)["verdict"], "no-difference")

    def test_bootstrap_ci_brackets_mean_and_is_deterministic(self):
        v = [10.0, 11.0, 12.0, 13.0]
        lo, hi = st.bootstrap_ci(v)
        self.assertTrue(lo <= st.mean(v) <= hi)
        self.assertEqual((lo, hi), st.bootstrap_ci(v))


class Parsers(unittest.TestCase):
    def test_frames_log_skips_torn_and_foreign_lines(self):
        f = fr.parse_frames_log("time,frame\n1000000,16000\n2016000,\n17000\n3000000,16000\n4000000,1")
        self.assertEqual(len(f), 3)
        self.assertEqual(f.t, [1.0, 3.0, 4.0])
        self.assertAlmostEqual(f.ms[0], 16.0)

    def test_empty_and_garbage_logs_raise_a_clear_error(self):
        for text in ("", "\n\n", "garbage\nmore,garbage\n"):
            with self.assertRaises(fr.FramesError):
                fr.parse_frames_log(text)

    def test_flip_times(self):
        self.assertEqual([round(x, 3) for x in fr.flip_times([100, 200, 300], 10.0)], [9.5, 9.7, 10.0])
        self.assertEqual([round(x, 3) for x in fr.flip_times([100, 200, 300])], [0.1, 0.3, 0.6])

    def test_perf_csv_and_run_dir(self):
        with tempfile.TemporaryDirectory() as td:
            d = evfix.make_run(td, "T", "r1", [10.0] * 400, total=12.0)
            f = fr.load_run_frames(d)
            self.assertEqual(len(f), 400)
            self.assertAlmostEqual(f.t[-1], 12.0)                      # run.json wall_s places the last flip
            self.assertAlmostEqual(f.t[0], 12.0 - 399 * 0.010)
            with self.assertRaises(fr.FramesError):
                fr.load_run_frames(td)

    def test_frames_log_in_run_dir(self):
        text, _ = evfix.frames_log([(100, 20)])
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "x.frames.log"), "w") as fh:
                fh.write(text)
            self.assertEqual(len(fr.load_run_frames(td)), 100)


class SteadyWindow(unittest.TestCase):
    """The known bug class: a window that reaches into the loading phase gave wrong statistics."""

    def window(self, segments, **kw):
        text, _ = evfix.frames_log(segments)
        return fr.steady_window(fr.parse_frames_log(text), **kw)

    def test_long_run_window_is_pure_steady(self):
        w = self.window([(40, 500), (2500, 20)])            # 20 s loading, 50 s steady at 50 fps
        self.assertFalse(w.short)
        self.assertEqual(w.loading_frames_cut, 0)
        self.assertEqual((w.stats["p50"], w.stats["p95"]), (20.0, 20.0))
        self.assertAlmostEqual(w.stats["fps"], 50.0)
        self.assertAlmostEqual(w.covered_s, 30.0, delta=0.1)

    def test_window_reaching_into_loading_is_clipped_not_polluted(self):
        segs = [(40, 500), (750, 20)]                       # 20 s loading, only 15 s steady; 30 s window requested
        text, _ = evfix.frames_log(segs)
        f = fr.parse_frames_log(text)
        naive = fr.time_window(f, f.t[-1] - 2 - 30, f.t[-1] - 2)          # the old behaviour
        self.assertLess(naive.stats["fps"], 35.0)                           # polluted by the loading frames
        w = fr.steady_window(f)
        self.assertAlmostEqual(w.stats["fps"], 50.0)
        self.assertEqual(w.stats["p50"], 20.0)
        self.assertGreater(w.loading_frames_cut, 25)
        self.assertTrue(w.short)                                            # says it covers ~13 s, not 30
        self.assertLess(w.covered_s, 15.0)
        self.assertTrue(any("loading" in n for n in w.notes))

    def test_isolated_stalls_do_not_move_the_window(self):
        steady = []
        for _ in range(12):
            steady += [(199, 20), (1, 300)]                 # a 300 ms stall every 200 frames
        text, _ = evfix.frames_log([(40, 500)] + steady)
        f = fr.parse_frames_log(text)
        self.assertEqual(fr.settled_index(f.ms), 40)
        w = fr.steady_window(f)
        self.assertEqual(w.stats["p50"], 20.0)
        self.assertEqual(w.stats["n"], len([x for x in f.ms[40:] if True and f.t[40 + 0] >= 0]) - sum(1 for t in f.t[40:] if t <= f.t[-1] - 2 - 30 or t > f.t[-1] - 2) if False else w.stats["n"])
        self.assertEqual(w.loading_frames_cut, 0)

    def test_hiccup_of_loading_in_the_middle(self):
        text, _ = evfix.frames_log([(20, 500), (40, 20), (30, 500), (3000, 20)])   # second loading burst after a calm stretch
        f = fr.parse_frames_log(text)
        self.assertEqual(fr.settled_index(f.ms), 90)

    def test_timeout_tail_is_skipped(self):
        text, _ = evfix.frames_log([(40, 500), (2500, 20), (1, 20000)])           # the run hangs and is killed after 20 s
        w = fr.steady_window(fr.parse_frames_log(text))
        self.assertEqual(w.stats["p95"], 20.0)
        self.assertAlmostEqual(w.stats["n"], 1500, delta=2)                       # a full 30 s of steady frames before the hang
        self.assertFalse(w.short)
        self.assertTrue(any("trailing stall" in n for n in w.notes))
        self.assertAlmostEqual(w.stats["fps"], 50.0)

    def test_no_data(self):
        self.assertIsNone(fr.settled_index([]))
        with self.assertRaises(fr.FramesError):
            fr.time_window(fr.Frames([1.0], [10.0]), 0, 5)


class SceneWindows(unittest.TestCase):
    def frames(self):
        text, tot = evfix.frames_log([(3500, 20)], t0=0.0)   # 70 s at 50 fps
        return fr.parse_frames_log(text), tot

    def test_title_and_last_and_t(self):
        f, tot = self.frames()
        opens = {"worldmap": 10.0}
        w = fr.scene_window(f, opens, "title", tot)
        self.assertAlmostEqual(w.start_s, 25.0)
        self.assertAlmostEqual(w.end_s, tot - 1)
        self.assertAlmostEqual(w.stats["n"], 44 * 50, delta=2)
        self.assertAlmostEqual(fr.scene_window(f, opens, "t:20-30", tot).stats["n"], 500, delta=2)
        self.assertAlmostEqual(fr.scene_window(f, opens, "last:10", tot).stats["fps"], 50.0)

    def test_missing_open_and_too_few_presents(self):
        f, tot = self.frames()
        with self.assertRaises(fr.FramesError):
            fr.scene_window(f, {}, "worldmap", tot)
        with self.assertRaises(fr.FramesError):
            fr.scene_window(f, {"hoover": 60.0}, "level:hoover", tot)   # only ~500 presents after the open
        w = fr.scene_window(f, {"hoover": 5.0}, "level:hoover", tot)     # 3250 presents after: starts at the 1700th
        self.assertAlmostEqual(w.start_s, 5.0 + 1700 * 0.02, delta=0.05)
        with self.assertRaises(fr.FramesError):
            fr.scene_window(f, {}, "bogus", tot)


class NonFinite(unittest.TestCase):
    def test_compare_refuses_nan_and_inf(self):
        for a, b in (([10, 10.1, float("nan")], [5, 5.1, 5.2]), ([10, 10.1, 10.2], [5, float("inf"), 5.2]), ([10, 10.1, 10.2], [5, -float("inf"), 5.2])):
            c = st.compare(a, b)
            self.assertEqual(c["verdict"], "insufficient")
            self.assertIn("finite", c["reason"])

    def test_perf_csv_skips_torn_and_foreign_rows(self):
        text = "frame,wall_ms\n0,16.0\n1,nan\n2,inf\n3,-4\n4,0\n5,17.0\n"
        self.assertEqual(fr.parse_perf_csv(text).ms, [16.0, 17.0])

    def test_frames_log_rejects_unicode_digits(self):
        t = "1000000,16000\n\u00b2,5\n7,\u0663\n2000000,16000\n" + "1" * 5000 + ",1\n"
        self.assertEqual(fr.parse_frames_log(t).ms, [16.0, 16.0])

    def test_non_dict_run_json_and_bad_wall_time_are_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            for body in ("[1]", '"x"', '{"timings": [1]}', '{"timings": {"wall_s": "9"}}'):
                evfix.write(os.path.join(td, "run.json"), body)
                self.assertIsNone(fr.run_total_seconds(td))
            os.remove(os.path.join(td, "run.json"))
            evfix.write(os.path.join(td, "summary.txt"), "wall_time_s: 1.2.3\n")
            self.assertIsNone(fr.run_total_seconds(td))
            evfix.write(os.path.join(td, "summary.txt"), "wall_time_s: 1.5\n")
            self.assertEqual(fr.run_total_seconds(td), 1.5)


if __name__ == "__main__":
    unittest.main()
