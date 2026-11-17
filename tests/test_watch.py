#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/watch.py (the run watcher): synthetic log streams, no title is run.
Ported from the private smartwatch_test.py (recorded-log replays dropped; the same shapes are synthetic here)."""
import importlib.util, os, struct, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("nps5watch", os.path.join(HERE, "..", "scripts", "nps5lib", "watch.py"))
W = importlib.util.module_from_spec(spec); spec.loader.exec_module(W)
Judge = W.Judge
MARK = {"worldmap": "open:levels/worldmap/", "ready": "log:LEVELREADY", "p100": "present:100"}


def pstat(presents, luma, black, sd, diff=1.0):
    return "[pstat] wall=1.0 presents=%d luma=%.2f black=%.3f sd=%.2f diff=%.2f" % (presents, luma, black, sd, diff)


def series(t0, t1, luma, black, sd, presents0=1, step=2, rate=30):
    return [(t, pstat(presents0 + int((t - t0) * rate), luma, black, sd)) for t in range(int(t0), int(t1), step)]


def play(j, events, cpu=0.0, horizon=400, open_at=None, open_text=""):
    ev = sorted(events, key=lambda e: e[0]); i = 0
    for age in range(horizon):
        while i < len(ev) and ev[i][0] <= age:
            j.feed_line(age, ev[i][1]); i += 1
        j.feed_cpu(age, cpu)
        if age == open_at: j.feed_open_files(age, open_text)
        v = j.decide(age)
        if v: return age, v[0]
    return horizon, "NONE"


def compiling(a, b, total=4000):
    return [(t, "[prebuild] Compiling shaders  %d / %d (%d s)" % (t * 20, total, t)) for t in range(a, b, 2)]


class T(unittest.TestCase):
    def ok(self, got, verdict, lo, hi):
        age, v = got
        self.assertEqual(v, verdict, got); self.assertTrue(lo <= age <= hi, got)

    # ---- ported cases
    def test_pic_window(self):
        self.ok(play(Judge(90, "pic+20"), series(21, 200, 90, .1, 40)), W.DONE, 41, 45)

    def test_marker_never_seen_times_out(self):
        self.ok(play(Judge(90, "scene:hoover_beach+30"), series(21, 200, 90, .1, 40)), W.TIMEOUT, 90, 91)

    def test_log_marker_window(self):
        self.ok(play(Judge(90, "log:LEVELREADY+15"), series(21, 200, 90, .1, 40) + [(60, "x LEVELREADY y")]), W.DONE, 75, 78)

    def test_black_from_first_present(self):
        pre = compiling(20, 60) + [(60, "[prebuild] 800 / 800 pipelines in 40 s before the first frame")]
        self.ok(play(Judge(200, "pic+20"), pre + series(61, 300, 5.7, .90, 4.3)), W.BLACK, 101, 105)
        self.ok(play(Judge(90, "pic+20"), pre + series(61, 200, 5.7, .90, 4.3)), W.BLACK, 90, 91)   # black at the cap

    def test_cold_compile_extended_then_picture(self):
        cold = compiling(10, 150) + [(150, "[prebuild] 3000 / 3000 pipelines in 140 s before the first frame")]
        self.ok(play(Judge(90, "pic+10", max_s=180), cold + series(151, 300, 80, .2, 30)), W.DONE, 160, 165)

    def test_compile_frozen_before_cap_stalls(self):
        self.ok(play(Judge(90, "pic+10"), compiling(10, 42)), W.STALL, 85, 87)

    def test_compile_frozen_at_cap_times_out(self):
        self.ok(play(Judge(90, "pic+10"), compiling(10, 72)), W.TIMEOUT, 90, 91)

    def test_compile_never_ends_hits_ceiling(self):
        self.ok(play(Judge(90, "pic+10", max_s=180), compiling(10, 400, 9000)), W.TIMEOUT, 180, 181)

    def test_presents_stop(self):
        self.ok(play(Judge(120, "log:NEVER+30"), series(30, 60, 80, .2, 30)), W.STALL, 103, 104)

    def test_picture_then_black(self):
        mid = series(30, 60, 80, .2, 30) + series(60, 200, 5.7, .90, 4.3, presents0=1000)
        self.ok(play(Judge(120, "log:NEVER+30"), mid), W.BLACK, 98, 102)

    def test_black_fade_survives(self):
        fade = series(30, 50, 80, .2, 30) + series(50, 70, 0, 1.0, 0, presents0=700) + series(70, 200, 90, .1, 40, presents0=1400)
        self.ok(play(Judge(120, "pic+60"), fade), W.DONE, 90, 91)

    def test_no_target_fixed(self):
        self.ok(play(Judge(60, None), series(30, 200, 80, .2, 30)), W.FIXED, 60, 61)

    def test_busy_boot_not_stall(self):
        self.ok(play(Judge(90, "pic+10"), series(70, 200, 80, .2, 30), cpu=1.5), W.DONE, 80, 84)

    def test_dead_boot_stalls(self):
        self.ok(play(Judge(90, "pic+10"), [(1, "x")]), W.STALL, 46, 47)

    def test_open_file_marker_via_lsof(self):
        j = Judge(120, "marker:worldmap+20", markers=MARK)
        self.ok(play(j, series(46, 300, 80, .2, 30), cpu=1., open_at=100, open_text=".../prein/levels/worldmap/cinematics/a.anim"), W.DONE, 120, 121)
        j = Judge(120, "marker:worldmap+20", markers=MARK)
        self.ok(play(j, series(46, 300, 80, .2, 30), cpu=1., open_at=100, open_text="nothing"), W.TIMEOUT, 120, 121)

    # ---- scene-open lines, incl. the legacy alias
    def test_scene_open_and_alias(self):
        for tag in ("scene-open", "level-open"):
            j = Judge(120, "scene:hoover_beach+20")
            self.ok(play(j, series(46, 300, 80, .2, 30) + [(60, "[%s] hoover_beach t=59.9" % tag)], cpu=1.5), W.DONE, 79, 81)

    def test_scene_open_other_scene_ignored(self):
        self.ok(play(Judge(90, "scene:hoover_beach+20"), series(46, 300, 80, .2, 30) + [(60, "[scene-open] worldmap t=59.9")], cpu=1.5), W.TIMEOUT, 90, 91)

    def test_scene_open_before_picture_waits(self):
        self.ok(play(Judge(120, "scene:hoover_beach+10"), series(80, 300, 80, .2, 30) + [(60, "[scene-open] hoover_beach")], cpu=1.5), W.DONE, 90, 95)

    def test_open_marker_matched_by_scene_open_key(self):
        j = Judge(120, "marker:worldmap+10", markers=MARK, scene_key="levels/")
        self.ok(play(j, series(46, 300, 80, .2, 30) + [(95, "[scene-open] worldmap t=94.8")], cpu=1.5), W.DONE, 105, 108)
        j = Judge(120, "marker:worldmap+10", markers=MARK, scene_key="other/")   # another key: line does not mark levels/worldmap/
        self.ok(play(j, series(46, 300, 80, .2, 30) + [(95, "[scene-open] worldmap t=94.8")], cpu=1.5), W.TIMEOUT, 120, 121)

    def test_legacy_spellings(self):
        self.assertEqual(W.parse_until("worldmap+5")["arg"], "worldmap")
        self.assertEqual(W.parse_until("level:x+5")["kind"], "scene")

    # ---- new until forms
    def test_time_and_frames(self):
        self.ok(play(Judge(120, "time:30"), series(5, 200, 80, .2, 30)), W.DONE, 30, 31)
        self.ok(play(Judge(120, "frames:300"), series(5, 200, 80, .2, 30)), W.DONE, 14, 17)

    def test_present_marker(self):
        self.ok(play(Judge(120, "marker:p100+5", markers=MARK), series(5, 200, 80, .2, 30)), W.DONE, 13, 16)

    def test_first_present(self):
        self.ok(play(Judge(120, "first-present+3"), series(20, 200, 5.7, .9, 4)), W.DONE, 23, 24)

    def test_black_modifier_overrides(self):
        self.ok(play(Judge(120, "pic+10,black:15"), series(20, 200, 5.7, .9, 4.3)), W.BLACK, 35, 37)

    def test_stall_modifier_overrides(self):
        self.ok(play(Judge(120, "log:NEVER+5,stall:10"), series(5, 30, 80, .2, 30)), W.STALL, 37, 41)

    def test_bad_specs(self):
        for bad in ("nope", "marker:unknown+3", "pic,black:x", "log:(+3", "time:x"):
            with self.assertRaises(W.UntilError, msg=bad): W.parse_until(bad, MARK)
        self.assertIsNone(W.parse_until("-")); self.assertIsNone(W.parse_until(None))

    def test_profile_scenes_parse(self):
        for s in ("marker:worldmap", "scene:hoover_beach", "pic+5", "log:a.b+3", "open:levels/x/+3,black:30"):
            W.parse_until(s, MARK)


class Child:
    """A fake Popen: runs until `ends_at` fake seconds, or is terminated."""
    def __init__(self, clock, ends_at=10 ** 9):
        self.clock, self.ends_at, self.rc, self.terminated, self.killed = clock, ends_at, None, False, False
    @property
    def returncode(self): return self.rc
    def poll(self):
        if self.rc is None and self.clock[0] >= self.ends_at: self.rc = 0
        return self.rc
    def terminate(self): self.terminated = True; self.rc = 143
    def kill(self): self.killed = True; self.rc = -9
    def wait(self): return self.rc


class Src:
    def __init__(self, clock, events): self.clock, self.ev, self.i = clock, sorted(events, key=lambda e: e[0]), 0
    def lines(self):
        out = []
        while self.i < len(self.ev) and self.ev[self.i][0] <= self.clock[0]:
            out.append(self.ev[self.i][1]); self.i += 1
        return out
    def presents(self): return None
    def picture(self): return None


class Follow(unittest.TestCase):
    def drive(self, j, events, ends_at=10 ** 9):
        clock = [0.0]
        def sleep(s): clock[0] += s
        pr = Child(clock, ends_at)
        r = W.follow(pr, j, Src(clock, events), now=lambda: clock[0], sleep=sleep)
        return r, pr, clock[0]

    def test_stops_child_on_target(self):
        (v, detail, rc), pr, t = self.drive(Judge(120, "pic+10"), series(5, 300, 80, .2, 30))
        self.assertEqual(v, W.DONE); self.assertTrue(pr.terminated); self.assertEqual(rc, 143); self.assertTrue(15 <= t <= 20, t)

    def test_black_stops_child(self):
        (v, _, _), pr, t = self.drive(Judge(120, "pic+10"), series(5, 300, 5.7, .9, 4.3))
        self.assertEqual(v, W.BLACK); self.assertTrue(pr.terminated)

    def test_child_ends_alone(self):
        (v, _, rc), pr, t = self.drive(Judge(120, "pic+100"), series(5, 300, 80, .2, 30), ends_at=30)
        self.assertIsNone(v); self.assertFalse(pr.terminated); self.assertEqual(rc, 0)


class Files(unittest.TestCase):
    def bmp(self, path, v, w=8, h=8):
        row = bytes([v, v, v]) * w
        pad = b"\0" * ((4 - len(row) % 4) % 4)
        data = (row + pad) * h
        hdr = b"BM" + struct.pack("<IHHI", 54 + len(data), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(data), 0, 0, 0, 0)
        open(path, "wb").write(hdr + data)

    def test_bmp_stats_black_and_picture(self):
        d = tempfile.mkdtemp()
        self.bmp(os.path.join(d, "a.bmp"), 0); self.bmp(os.path.join(d, "b.bmp"), 200)
        l, b = W.bmp_stats(os.path.join(d, "a.bmp")); self.assertEqual((l, b), (0.0, 1.0))
        l, b = W.bmp_stats(os.path.join(d, "b.bmp")); self.assertTrue(l > 150 and b == 0.0)
        open(os.path.join(d, "bad.bmp"), "wb").write(b"BM123")
        self.assertIsNone(W.bmp_stats(os.path.join(d, "bad.bmp")))

    def test_sources_incremental(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "frames"))
        err = os.path.join(d, "run.err"); open(err, "w").write("one\ntwo")
        s = W.Sources(err, d)
        self.assertEqual(s.lines(), ["one"])
        open(err, "a").write("\nthree\n")
        self.assertEqual(s.lines(), ["two", "three"]); self.assertEqual(s.lines(), [])
        open(os.path.join(d, "frames", "perf_frames.csv"), "w").write("frame,wall_ms\n0,16.6\n1,16.7\n2,16.5\n")
        self.assertEqual(s.presents(), 3)
        self.bmp(os.path.join(d, "frames", "f1.bmp"), 0)
        self.assertEqual(s.picture(), (0.0, 1.0)); self.assertIsNone(s.picture())

    def test_black_dumps_drive_early_black(self):
        j = Judge(120, "pic+10", blank_fail=20)
        for t in range(0, 40, 2):
            j.feed_present(t, t + 1); j.feed_picture(t, 0.0, 1.0)
            j.feed_cpu(t, 1.0)
        v = j.decide(40)
        self.assertEqual(v[0], W.BLACK)



class TornLines(unittest.TestCase):
    def test_torn_number_does_not_stop_the_watcher(self):
        j = W.Judge(30)
        for line in ("[pstat] wall=1 presents=1 luma=. black=0 sd=0 diff=0", "[pstat] wall=1.2.3 presents=1 luma=70 black=0 sd=0 diff=0",
                     "[scene-open] map t=1.2.3", "[scene-open] map t=."):
            j.feed_line(1.0, line)                       # raised ValueError before: the watcher died and the title ran on unwatched
        j.feed_line(2.0, "[pstat] wall=2.0 presents=5 luma=70.0 black=0.02 sd=30.0 diff=1.0")
        self.assertIsNotNone(j.t_pic)


if __name__ == "__main__":
    unittest.main()
