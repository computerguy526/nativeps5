#!/usr/bin/env python3
"""Tests for analyze.py (pass-size stats, CpGaps, commit-feedback GPU accounting, hot shaders, ioreg parsing, CLI) on synthetic logs."""
import contextlib, io, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evfix  # noqa: E402
import analyze as an  # noqa: E402


def att(size, draws):
    return f"CpPass: attachments {size} fmt=bgra draws/frame={draws}"


class PassSizes(unittest.TestCase):
    def test_weighted_shares(self):
        lines = [att("1920x1080", 30), att("960x540", 10), "noise", att("1920x1080", 0)]
        s = an.pass_sizes(lines)
        self.assertAlmostEqual(s["1920x1080"], 75.0)
        self.assertAlmostEqual(s["960x540"], 25.0)
        self.assertEqual(list(s)[0], "1920x1080")

    def test_tail_fraction(self):
        lines = [att("1920x1080", 10)] * 6 + [att("960x540", 10)] * 2          # last 25 % = the two 960x540 lines
        self.assertEqual(an.pass_sizes(lines, .25), {"960x540": 100.0})
        self.assertEqual(an.pass_sizes([]), {})

    def test_blocks(self):
        lines = ["CpPass: frame 1", att("100x100", 3), att("50x50", 1), "CpPass: frame 2", att("50x50", 2), "CpPass: frame 3"]
        b = an.pass_blocks(lines)
        self.assertEqual(len(b), 2)                              # the empty third block is dropped
        self.assertAlmostEqual(b[0]["100x100"], 75.0)
        self.assertEqual(b[1], {"50x50": 100.0})


class Gaps(unittest.TestCase):
    A = "CpGaps 10s: frames 600 wall 16.7 ms/frame | commit 12.0 (150/f) | gap 4.7 = wait 3.0 (60%) other 1.7 (30%)"
    B = "CpGaps 10s: frames 10 wall 20.0 ms/frame | commit 5.0 (80 draws/f) | gap 15.0 = sync 15.0 (90%)"

    def test_both_log_formats(self):
        g = an.cpgaps([self.A, "unrelated", self.B])
        self.assertEqual([x["frames"] for x in g], [600, 10])
        self.assertEqual((g[0]["draws"], g[1]["draws"]), (150, 80))
        self.assertAlmostEqual(g[0]["busy_pct"], 100 * 12.0 / 16.7)
        self.assertEqual(g[0]["parts"], {"wait": 3.0, "other": 1.7})

    def test_cli_filters_short_windows(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "o.log")
            evfix.write(p, self.A + "\n" + self.B + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(an.main(["gaps", p]), 0)
            self.assertIn("frames  600", out.getvalue())
            self.assertNotIn("frames   10", out.getvalue())


class Commits(unittest.TestCase):
    def stats(self, n=100, **kw):
        c = an.load_commits(evfix.commit_log(n))
        return an.commit_stats(c, last=1.0, skip_end=0.1, **kw)

    def test_known_busy_and_idle(self):
        s = self.stats()
        self.assertTrue(s["ok"])
        self.assertAlmostEqual(s["fps"], 50.0, places=3)
        self.assertAlmostEqual(s["busy_ms"], 8.0, places=3)      # union of [0,5] and [3,8] ms = 8 ms per 20 ms frame
        self.assertAlmostEqual(s["span_ms"], 10.0, places=3)     # spans are counted twice where they overlap
        self.assertAlmostEqual(s["idle_ms"], 12.0, places=3)
        self.assertAlmostEqual(s["busy_pct"], 40.0, places=3)
        self.assertAlmostEqual(s["commits_per_frame"], 2.0)
        self.assertAlmostEqual(s["passes_per_frame"], 1.0)
        self.assertAlmostEqual(s["interval_p50"], 20.0, places=3)

    def test_exclusive_time_sums_to_busy_and_classes(self):
        s = self.stats()
        self.assertAlmostEqual(sum(c["ms"] for c in s["classes"]), s["busy_ms"], places=6)
        by = {c["cls"]: c for c in s["classes"]}
        self.assertAlmostEqual(by["1920x1080"]["ms"], 5.0, places=3)         # first commit: its whole interval
        self.assertAlmostEqual(by["PRESENT-only"]["ms"], 3.0, places=3)      # PRESENT commit: only the 3 ms it adds
        sz = s["sizes"][0]
        self.assertEqual((sz["w"], sz["h"]), (1920, 1080))
        self.assertAlmostEqual(sz["binds"], 3.0)
        self.assertAlmostEqual(sz["passes"], 1.0)

    def test_idle_gaps(self):
        s = self.stats()
        self.assertAlmostEqual(s["idle_gap_ms"], 12.0, delta=0.5)
        self.assertAlmostEqual(s["big_gaps_per_frame"], 1.0, delta=0.05)

    def test_too_few_frames_and_empty(self):
        self.assertFalse(self.stats(n=2)["ok"])
        self.assertFalse(an.commit_stats([])["ok"])

    def test_bad_input_is_skipped_not_fatal(self):
        c = an.load_commits("garbage line\n5.0 4.0 x\n0 1 y\n1.0 2.0 PRESENT\nonly\n")
        self.assertEqual(len(c), 1)
        p = an.parse_tokens(["Rgarbage", "Pfs+vs*2", "C12/34", "Cabcdef0123456789*4", "PRESENT"])
        self.assertEqual([x[0] for x in p], ["R", "C"])
        self.assertEqual(p[0][3]["fs+vs"], 2)               # a pipeline bind before any pass opens a pseudo pass
        self.assertEqual((p[1][1], p[1][2], p[1][3]), ("abcdef0123456789", "C12/34", 4))

    def test_gz_log(self):
        import gzip
        with tempfile.TemporaryDirectory() as td:
            with gzip.open(os.path.join(td, "t.commits.log.gz"), "wt") as f:
                f.write(evfix.commit_log(10))
            self.assertEqual(len(an.read_commits(os.path.join(td, "t.commits.log"))), 20)


class Hot(unittest.TestCase):
    def test_ranking(self):
        h1, h2 = "a" * 16, "b" * 16
        lines = [f"{t}.0 {m} x C{h1}" for t, m in zip(range(0, 10), [1] * 10)] + [f"{t}.0 {m} x P{h1}+{h2}" for t, m in zip(range(10, 20), [2] * 10)]
        r = an.hot_shaders("\n".join(lines) + "\n", from_frac=0.5, top=5)       # second half: ten commits of 2 ms over 9 s
        self.assertAlmostEqual(r["span"], 9.0)
        self.assertEqual({h for _, h, _ in r["rows"]}, {h1, h2})
        self.assertAlmostEqual(r["rows"][0][0], 20 / 9)
        self.assertEqual(r["rows"][0][2], 10)

    def test_single_row_does_not_divide_by_zero(self):
        self.assertEqual(an.hot_shaders("1.0 1 x C" + "a" * 16 + "\n")["rows"], [])


IOREG = '''+-o AGXDeviceUserClient  <class AGXDeviceUserClient>
  {
    "IOUserClientCreator" = "pid 100, native_title"
    "AppUsage" = ({"accumulatedGPUTime"=5000000000,"x"=1},{"accumulatedGPUTime"=1000000000})
  }
+-o AGXDeviceUserClient  <class AGXDeviceUserClient>
  {
    "IOUserClientCreator" = "pid 200, WindowServer"
    "AppUsage" = ({"accumulatedGPUTime"=2000000000})
  }
+-o AGXDeviceUserClient  <class AGXDeviceUserClient>
  {
    "IOUserClientCreator" = "pid 300, Safari"
    "AppUsage" = ({"accumulatedGPUTime"=1000000000})
  }
+-o AGXDeviceUserClient  <class AGXDeviceUserClient>
  {
    "IOUserClientCreator" = "pid 400, nothing"
  }
'''


class Gpu(unittest.TestCase):
    def test_parse_and_rates(self):
        a = an.parse_ioreg(IOREG)
        self.assertEqual(a[(100, "native_title")], 6_000_000_000)
        self.assertEqual(len(a), 3)
        b = {k: v + (500_000_000 if k[0] == 100 else 100_000_000 if k[0] == 200 else 50_000_000) for k, v in a.items()}
        r = an.gpu_rates(a, b, 1.0)
        self.assertEqual(r["title_pid"], 100)
        self.assertAlmostEqual(r["title"], 500.0)
        self.assertAlmostEqual(r["windowserver"], 100.0)
        self.assertAlmostEqual(r["others"], 50.0)
        self.assertEqual(an.gpu_rates({}, {}, 1.0)["title"], 0.0)


class Cli(unittest.TestCase):
    def run_cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = an.main(args)
        return rc, out.getvalue(), err.getvalue()

    def test_frames_steady_and_errors(self):
        text, _ = evfix.frames_log([(40, 500), (750, 20)])
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.frames.log")
            evfix.write(p, text)
            rc, out, _ = self.run_cli(["frames", p])
            self.assertEqual(rc, 0)
            self.assertIn("p50 20.0", out)
            self.assertIn("50.0 fps", out)
            self.assertIn("SHORT", out)
            rc, out, _ = self.run_cli(["frames", p, "--json", "--window", "last:5"])
            self.assertEqual(rc, 0)
            self.assertIn('"p50": 20.0', out)
            e = os.path.join(td, "empty.frames.log")
            evfix.write(e, "")
            self.assertEqual(self.run_cli(["frames", e])[0], 1)            # unusable input
            self.assertEqual(self.run_cli(["frames", os.path.join(td, "nope.log")])[0], 1)
        self.assertEqual(self.run_cli(["frames"])[0], 2)                    # usage
        self.assertEqual(self.run_cli(["frames", "x", "--bogus"])[0], 2)
        self.assertEqual(self.run_cli(["frames", "x", "--last"])[0], 2)
        self.assertEqual(self.run_cli(["nosuch", "x"])[0], 2)
        self.assertEqual(self.run_cli([])[0], 2)
        self.assertEqual(self.run_cli(["--help"])[0], 0)

    def test_commits_cli(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "c.log")
            evfix.write(p, evfix.commit_log(100))
            rc, out, _ = self.run_cli(["commits", p, "--last", "1", "--skip-end", "0.1"])
            self.assertEqual(rc, 0)
            self.assertIn("GPU busy 8.0 ms/frame = 40 %", out)


class Hardening(unittest.TestCase):
    def run_cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = an.main(args)
        return rc, out.getvalue(), err.getvalue()

    def test_malformed_window_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.frames.log")
            evfix.write(p, evfix.frames_log([(400, 20.0)])[0])
            for w in ("last:abc", "t:5", "t:a-b", "last:", "last:nan", "bogus"):
                rc, _, err = self.run_cli(["frames", p, "--window", w])
                self.assertEqual(rc, 2, w)
                self.assertIn("--window", err)
            self.assertEqual(self.run_cli(["frames", p, "--window", "last:5"])[0], 0)

    def test_commit_stats_all_frames_ending_together(self):
        log = "".join("%.6f %.6f PRESENT\n" % (100.0 + i * 0.001, 101.0) for i in range(5))       # every commit ends at 101.0
        s = an.commit_stats(an.load_commits(log), last=10.0, skip_end=0.0)
        self.assertFalse(s["ok"])
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "c.log")
            evfix.write(p, log)
            self.assertEqual(self.run_cli(["commits", p, "--last", "10", "--skip-end", "0"])[0], 1)


if __name__ == "__main__":
    unittest.main()
