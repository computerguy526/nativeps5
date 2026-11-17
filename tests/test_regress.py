#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/regress.py: the green / red rule on fake run.json + frames + dumps, baselines, game status, and
the validated.tsv rule (written only when green, only for a non-provisional profile with a known class). No title is run."""
import io, json, os, shutil, sys, tempfile, unittest, warnings
warnings.simplefilter("ignore", ResourceWarning)
from contextlib import redirect_stdout, redirect_stderr
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evfix  # noqa: E402,F401
import bmp  # noqa: E402
import regress  # noqa: E402
import run as runmod  # noqa: E402

GAME = "title_id\tPPSA00001\nname\tFixture\nbuild\t01.001\teboot=0123456789abcdef\npackage\t01.001\ngpu\tkyty\ncanonical_lineage\tlin1\nmarker\tm\tlog:READY\n"
SCENES = "boot\t-\t-\tmarker:m\t90\tnew\tb\nmap\t-\t-\tmarker:m\t90\tnew\tm\nlvl\t-x\t-\tmarker:m\t90\tnew\tl\n"
SMOKE = "# c\nboot\tboot\tquick,full\tmarker:m+5@90\t18944\t1500\tboot\nmap\tmap\tquick,full\tmarker:m+5@90\t18944\t1500\tmap\nlvl\tlvl\tfull\tmarker:m+5@180\t18944\t2500\tlevel\n"
ROUTE = dict(name="boot", scene="boot", modes=["quick", "full"], until="marker:m+5", secs=90, mem_max=18944, stall_max=1500, description="")
GOOD_PIC = dict(ok=True, source="pstat", detail="")
COMP = dict(public="p1", ps5recomp="ccccccc333", anyps5=None, kk="bbbbbbb222", kyty="aaaaaaa111")


def rj(verdict="DONE_UNTIL", rc=0, comps=None, cls="m4pro-24g", peak=None, lineage="lin1", patches=("a", "b")):
    d = dict(schema=1, title="PPSA00001", tag="t", **{"class": "func"}, gpu="kyty", scene="boot", until="u", secs=90, arm=None, build=dict(version="01.001", build_id="x"),
             lineage=lineage, patches=list(patches), binary=dict(path="/b", sha256_16="x"), components=comps or dict(COMP),
             env={}, machine=dict(model="Mac16,8", class_=None, mem_gb=24), command=["x"], timings=dict(start=None, end=None, wall_s=60.0), rc=rc, verdict=verdict, logs={}, warnings=[])
    d["machine"]["class"] = cls
    if peak is not None: d["timings"]["peak_footprint_mib"] = peak
    return d


def frames(per_ms=16.0, n=2000, spike=None):
    ms = [per_ms] * n
    if spike: ms[n - 10] = spike
    return regress.fr.parse_perf_csv(evfix.perf_csv(ms), sum(ms) / 1000)


class RuleTests(unittest.TestCase):
    def j(self, rjson=None, f=None, err="", pic=GOOD_PIC, route=ROUTE):
        return regress.judge_route(route, rjson if rjson is not False else None, f if f is not None else frames(), err, pic)

    def test_green(self):
        r = self.j(rj()); self.assertEqual((r["status"], r["reasons"]), ("green", [])); self.assertIn("p95", r["metrics"])

    def test_done_fixed_is_green_too(self):
        self.assertEqual(self.j(rj("DONE_FIXED"))["status"], "green")

    def test_red_verdicts(self):
        for v, rc in (("CRASH", 139), ("FAIL_RC3", 3), ("EARLY_BLACK", 1), ("EARLY_STALL", 1), ("DUMP_MODIFIED", 99)):
            r = self.j(rj(v, rc)); self.assertEqual(r["status"], "red", v); self.assertIn("verdict", r["reasons"][0])

    def test_timeout_means_reach_not_met(self):
        r = self.j(rj("TIMEOUT", 124)); self.assertEqual(r["status"], "red"); self.assertIn("not reached", r["reasons"][0])

    def test_rc86_is_memory_not_crash(self):
        r = self.j(rj("FAIL_RC86", 86)); self.assertEqual(r["status"], "red"); self.assertIn("memory", r["reasons"][0]); self.assertNotIn("crash", r["reasons"][0])

    def test_red_line_in_log(self):
        r = self.j(rj(), err="foo\n[RED] mem-limit hit\n"); self.assertEqual(r["status"], "red"); self.assertIn("mem-limit", r["reasons"][0])

    def test_peak_memory_limit(self):
        self.assertEqual(self.j(rj(peak=18000))["status"], "green")
        r = self.j(rj(peak=19000)); self.assertEqual(r["status"], "red"); self.assertIn("peak footprint", r["reasons"][0])
        self.assertEqual(self.j(rj())["metrics"].get("peak_mib"), None)             # a run that recorded none is not failed for it

    def test_stall_limit(self):
        self.assertEqual(self.j(rj(), frames(spike=1400.0))["status"], "green")
        r = self.j(rj(), frames(spike=1900.0)); self.assertEqual(r["status"], "red"); self.assertIn("largest frame", r["reasons"][0])

    def test_black_picture_is_red(self):
        r = self.j(rj(), pic=dict(ok=False, source="pstat", detail="black=1.00")); self.assertEqual(r["status"], "red"); self.assertIn("picture", r["reasons"][0])

    def test_missing_or_bad_run_json_is_an_error_not_red(self):
        self.assertEqual(self.j(False)["status"], "error")
        d = rj(); del d["env"]; self.assertEqual(self.j(d)["status"], "error")
        self.assertEqual(self.j(rj("RUNNING", None))["status"], "error")

    def test_no_frames_still_judged_on_verdict(self):
        r = regress.judge_route(ROUTE, rj(), None, "", GOOD_PIC); self.assertEqual(r["status"], "green"); self.assertNotIn("p95", r["metrics"])

    def test_every_reason_is_listed(self):
        r = self.j(rj("CRASH", 139, peak=99999), err="[RED] x\n", pic=dict(ok=False, source="none", detail="n")); self.assertGreaterEqual(len(r["reasons"]), 4)


class PictureTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); os.makedirs(os.path.join(self.d, "frames"))
    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)
    def dump(self, name, f):
        open(os.path.join(self.d, "frames", name), "wb").write(bmp.make_bmp(16, 16, f))

    def test_drawn_dumps_pass(self):
        self.dump("frame_100.bmp", lambda x, y: ((x * 16) % 256,) * 3); self.dump("frame_200.bmp", lambda x, y: ((x * 16 + y) % 256,) * 3)
        r = regress.picture_check(self.d); self.assertTrue(r["ok"], r); self.assertEqual(r["source"], "dumps")

    def test_black_dump_fails(self):
        self.dump("frame_100.bmp", lambda x, y: (0, 0, 0)); r = regress.picture_check(self.d)
        self.assertFalse(r["ok"]); self.assertIn("black or flat", r["detail"])

    def test_flat_colour_fails(self):
        self.dump("frame_100.bmp", lambda x, y: (120, 120, 120)); self.assertFalse(regress.picture_check(self.d)["ok"])

    def test_last_dump_decides(self):
        self.dump("frame_100.bmp", lambda x, y: (0, 0, 0)); self.dump("frame_200.bmp", lambda x, y: ((x * 16) % 256,) * 3)
        self.assertTrue(regress.picture_check(self.d)["ok"])

    def test_pstat_fallback_and_no_evidence(self):
        ok = "[pstat] wall=10.0 presents=100 luma=80.0 black=0.01 sd=30.0 diff=2.0\n"
        bad = "[pstat] wall=10.0 presents=100 luma=1.0 black=0.99 sd=0.0 diff=0.0\n"
        self.assertTrue(regress.picture_check(self.d, ok)["ok"]); self.assertFalse(regress.picture_check(self.d, bad)["ok"])
        r = regress.picture_check(self.d, ""); self.assertFalse(r["ok"]); self.assertEqual(r["source"], "none")


class StatusTests(unittest.TestCase):
    G = lambda self, n="boot", p95=16.5: dict(name=n, status="green", reasons=[], metrics=dict(p95=p95))
    R = lambda self: dict(name="boot", status="red", reasons=["x"], metrics={})
    S = [("game.py check", "ok", "")]

    def test_pass_changed_regression_error(self):
        b = {"boot": dict(p95=16.5)}
        self.assertEqual(regress.game_status(self.S, [self.G()], b, 10, 1)[0], "PASS")
        self.assertEqual(regress.game_status(self.S, [self.G(p95=16.5 * 1.05)], b, 10, 1)[0], "CHANGED")
        self.assertEqual(regress.game_status(self.S, [self.G(p95=16.5 * 0.9)], b, 10, 1)[0], "CHANGED")        # faster also moved
        self.assertEqual(regress.game_status(self.S, [self.G(p95=16.5 * 1.2)], b, 10, 1)[0], "REGRESSION")
        self.assertEqual(regress.game_status(self.S, [self.R()], b, 10, 1)[0], "REGRESSION")
        self.assertEqual(regress.game_status(self.S, [dict(name="boot", status="error", reasons=["no run.json"], metrics={})], b, 10, 1)[0], "ERROR")
        self.assertEqual(regress.game_status([("x", "fail", "bad")], [self.G()], b, 10, 1)[0], "ERROR")
        self.assertEqual(regress.game_status(self.S, [self.G()], b, 10, 2)[0], "ERROR")                        # fewer routes ran than needed
        self.assertEqual(regress.game_status(self.S, [self.G()], {}, 10, 1)[0], "PASS")                       # no baseline

    def test_may_record_rule(self):
        g = [self.G()]
        self.assertIsNone(regress.may_record(False, "m4pro-24g", "PASS", g, 1))
        self.assertIsNone(regress.may_record(False, "m4pro-24g", "CHANGED", g, 1))
        self.assertIn("provisional", regress.may_record(True, "m4pro-24g", "PASS", g, 1))
        self.assertIn("class", regress.may_record(False, None, "PASS", g, 1))
        for s in ("REGRESSION", "ERROR"): self.assertIn(s, regress.may_record(False, "m4pro-24g", s, g, 1))
        self.assertIn("every route", regress.may_record(False, "m4pro-24g", "PASS", [self.G(), self.R()], 2))
        self.assertIn("every route", regress.may_record(False, "m4pro-24g", "PASS", g, 2))


class ValidatedTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "validated.tsv")
    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_row_format_and_class(self):
        row = regress.validated_row("quick", [rj(), rj()], "m4pro-24g", "26.6.2", "2026-11-17")
        self.assertEqual(row, "quick\tkyty=aaaaaaa111 kk=bbbbbbb222 ps5recomp=ccccccc333 lineage=lin1+a+b date=2026-11-17 class=m4pro-24g machine=Mac16,8 macos=26.6.2")

    def test_components_must_agree(self):
        other = dict(COMP, kk="zzz")
        with self.assertRaises(regress.RegressError): regress.validated_row("quick", [rj(), rj(comps=other)], "c", "1", "d")
        with self.assertRaises(regress.RegressError): regress.validated_row("quick", [rj(comps=dict(COMP, kyty=None))], "c", "1", "d")

    def test_update_replaces_same_mode_and_class_keeps_the_rest(self):
        open(self.p, "w").write("# header\nfull\tkyty=1 class=m4pro-24g date=old\nfull\tkyty=2 class=m1-16g date=other\nquick\tkyty=3 class=m4pro-24g date=q\n")
        regress.update_validated(self.p, "full\tkyty=9 class=m4pro-24g date=new")
        lines = open(self.p).read().splitlines()
        self.assertEqual(lines, ["# header", "full\tkyty=9 class=m4pro-24g date=new", "full\tkyty=2 class=m1-16g date=other", "quick\tkyty=3 class=m4pro-24g date=q"])
        regress.update_validated(self.p, "full\tkyty=7 class=m3-18g date=n3")
        self.assertEqual(len(open(self.p).read().splitlines()), 5)

    def test_update_creates_the_file(self):
        regress.update_validated(self.p, "quick\tkyty=1 class=c date=d"); self.assertEqual(open(self.p).read(), "quick\tkyty=1 class=c date=d\n")


class GameFlowTests(unittest.TestCase):
    """regress_game end to end with a fake `nps5 run` that writes run directories."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        gd = os.path.join(self.tmp, "games", "PPSA00001"); os.makedirs(gd); self.gd = gd
        for n, c in (("game.tsv", GAME), ("scenes.tsv", SCENES), ("smoke.tsv", SMOKE), ("validated.tsv", "# header\n")):
            with open(os.path.join(gd, n), "w") as f: f.write(c)
        self.env = dict(NPS5_GAMES_DIR=os.path.join(self.tmp, "games"), NPS5_GAMES_FIRST="1", NPS5_SCRATCH=self.tmp, NPS5_SELF_MEM_LIMIT_MB="18944",
                        NPS5_PUBLIC_DIR=self.tmp, NPS5_KK_ICD="x", PATH=os.environ["PATH"])
        self.g = runmod._game(); self.orig = self.g.find_profile
        self.provisional = False
        self.g.find_profile = lambda tid, environ=None: (self.gd, self.provisional)
        self._old = runmod._game; runmod._game = lambda: self.g
        self.scenario = {}
    def tearDown(self):
        runmod._game = self._old; shutil.rmtree(self.tmp, ignore_errors=True)

    def caller(self, cmd, env=None):
        tag = cmd[cmd.index("--tag") + 1]; name = tag.split("-")[1]
        sc = self.scenario.get(name, {})
        if sc.get("norun"): return 78
        d = os.path.join(self.tmp, "runs", "PPSA00001", tag); os.makedirs(os.path.join(d, "frames"))
        json.dump(rj(sc.get("verdict", "DONE_UNTIL"), sc.get("rc", 0), cls=sc.get("cls", "m4pro-24g")), open(d + "/run.json", "w"))
        open(d + "/run.err", "w").write(sc.get("err", "[pstat] wall=9.0 presents=100 luma=70.0 black=0.02 sd=30.0 diff=1.0\n"))
        open(d + "/frames/perf_frames.csv", "w").write(evfix.perf_csv([sc.get("ms", 16.0)] * 2000))
        return 0

    def go(self, cmd="check", quick=True, record=False, statics=None, **kw):
        o = dict(cmd=cmd, games=None, quick=quick, record=record, baseline="latest", out=None, tolerance=10.0, dry=False); o.update(kw)
        return regress.regress_game("PPSA00001", o, dict(self.env), self.caller, statics or (lambda t, e: [("game.py check", "ok", "")]), macos="26.6.2")

    def validated(self):
        return open(os.path.join(self.gd, "validated.tsv")).read()

    def test_quick_selects_two_routes_full_three(self):
        self.go(); self.assertEqual(len(os.listdir(os.path.join(self.tmp, "runs", "PPSA00001"))), 2)
        self.assertEqual(len(regress.select_routes(regress.read_smoke(os.path.join(self.gd, "smoke.tsv")), False)), 3)

    def test_green_run_records_class_row(self):
        res = self.go(record=True)
        self.assertEqual(res["status"], "PASS"); self.assertIsNotNone(res["recorded"])
        v = self.validated(); self.assertIn("quick\tkyty=aaaaaaa111", v); self.assertIn("class=m4pro-24g", v); self.assertIn("macos=26.6.2", v)

    def test_no_record_flag_no_row(self):
        self.go(record=False); self.assertEqual(self.validated(), "# header\n")

    def test_red_route_leaves_validated_untouched(self):
        self.scenario["map"] = dict(verdict="CRASH", rc=139)
        res = self.go(record=True)
        self.assertEqual(res["status"], "REGRESSION"); self.assertEqual(self.validated(), "# header\n"); self.assertIn("NOT recorded", regress.render(res))
        self.assertIn("REGRESSION", res["record_refused"])

    def test_black_picture_is_red_and_not_recorded(self):
        self.scenario["boot"] = dict(err="[pstat] wall=9.0 presents=100 luma=0.0 black=1.00 sd=0.0 diff=0.0\n")
        res = self.go(record=True); self.assertEqual(res["status"], "REGRESSION"); self.assertEqual(self.validated(), "# header\n")

    def test_run_that_never_started_is_error(self):
        self.scenario["boot"] = dict(norun=True)
        res = self.go(record=True); self.assertEqual(res["status"], "ERROR"); self.assertEqual(self.validated(), "# header\n")

    def test_provisional_profile_never_recorded(self):
        self.provisional = True
        res = self.go(record=True); self.assertEqual(res["status"], "PASS"); self.assertEqual(self.validated(), "# header\n"); self.assertIn("provisional", res["record_refused"])

    def test_unknown_class_never_recorded(self):
        self.scenario["boot"] = self.scenario["map"] = dict(cls=None)
        res = self.go(record=True); self.assertIn("class", res["record_refused"]); self.assertEqual(self.validated(), "# header\n")

    def test_static_failure_runs_nothing(self):
        res = self.go(record=True, statics=lambda t, e: [("pipeline list verified", "fail", "x")])
        self.assertEqual(res["status"], "ERROR"); self.assertFalse(os.path.exists(os.path.join(self.tmp, "runs"))); self.assertEqual(self.validated(), "# header\n")

    def test_baseline_then_check_detects_regression(self):
        res = self.go("baseline"); self.assertTrue(res["baseline_saved"])
        self.assertEqual(self.go()["status"], "PASS")
        self.scenario["boot"] = dict(ms=25.0)
        res = self.go(); self.assertEqual(res["status"], "REGRESSION"); self.assertIn("tolerance", " ".join(res["notes"]))

    def test_red_baseline_is_not_stored(self):
        self.scenario["boot"] = dict(verdict="CRASH", rc=139)
        res = self.go("baseline"); self.assertFalse(res["baseline_saved"])
        self.assertFalse(os.path.isdir(regress.baseline_dir(self.env, "PPSA00001", "latest", "quick")))

    def test_full_mode_records_its_own_row(self):
        res = self.go(quick=False, record=True); self.assertEqual(res["status"], "PASS")
        self.assertIn("full\tkyty=", self.validated()); self.assertEqual(len(res["routes"]), 3)

    def test_dry_run_runs_nothing(self):
        res = self.go(dry=True); self.assertEqual(res["status"], "DRY"); self.assertFalse(os.path.exists(os.path.join(self.tmp, "runs")))

    def test_skip_env(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = regress.main(["check", "--games", "PPSA00001", "--quick"], dict(self.env, NPS5_REGRESS_SKIP="PPSA00001"), self.caller)
        self.assertEqual(rc, 2)

    def test_main_exit_codes_and_list(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = regress.main(["check", "--games", "PPSA00001", "--quick"], dict(self.env), self.caller, lambda t, e: [("game.py check", "ok", "")])
            self.scenario["boot"] = dict(verdict="CRASH", rc=139)
            rc2 = regress.main(["check", "--games", "PPSA00001", "--quick"], dict(self.env), self.caller, lambda t, e: [("game.py check", "ok", "")])
        self.assertEqual((rc, rc2), (0, 1)); self.assertIn("PPSA00001 quick: PASS", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out): regress.main(["list"], dict(self.env))
        self.assertIn("2 quick", out.getvalue())

    def test_usage(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(regress.main([], {}), 2); self.assertEqual(regress.main(["frob"], dict(self.env)), 2)


class SmokeFileTests(unittest.TestCase):
    def test_read_smoke_and_bad_rows(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "s.tsv")
        try:
            open(p, "w").write(SMOKE); r = regress.read_smoke(p)
            self.assertEqual((r[0]["until"], r[0]["secs"], r[2]["modes"]), ("marker:m+5", 90, ["full"]))
            self.assertEqual([x["name"] for x in regress.select_routes(r, True)], ["boot", "map"])
            open(p, "w").write("a\tb\tmaybe\tx@1\t1\t1\n")
            with self.assertRaises(regress.RegressError): regress.read_smoke(p)
            open(p, "w").write("a\tb\tquick\tx\t1\t1\n")
            with self.assertRaises(regress.RegressError): regress.read_smoke(p)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_astro_smoke_file_parses_and_has_a_quick_pair(self):
        r = regress.read_smoke(os.path.join(HERE, "..", "games", "PPSA21564", "smoke.tsv"))
        self.assertEqual([x["name"] for x in regress.select_routes(r, True)], ["title", "worldmap"])
        self.assertEqual(len(regress.select_routes(r, False)), 3)


if __name__ == "__main__":
    unittest.main()
