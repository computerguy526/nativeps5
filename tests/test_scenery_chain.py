#!/usr/bin/env python3
"""The scenery-keys CHAIN, end to end, with no game and no GPU: a fake "title" (a small python child) stands in for Kyty and does what Kyty does on a
key press (writes KYTY_SCENE_STATE_FILE atomically in Kyty's exact text format); the real `execute()` starts the real scenery.Watcher, which repoints
the real scenery.Live links over a synthetic app0. Each layer had its own tests; nothing tested the chain, and the chain was broken once (v1.4.4:
execute() never started the watcher). Also the contract with Kyty: the same golden texts and env names are pinned in Kyty tests/SceneSkipTests.cpp
(TestContractWithLauncher), so a format change on either side fails a test on that side. SYNTHETIC data only."""
import importlib.util, json, os, re, sys, subprocess, tempfile, threading, time, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


L = load("nps5_chain_live", "tests/test_scenery_live.py")
TR = load("nps5_chain_run", "tests/test_run.py")
S, run = L.S, L.run
SC = load("nps5_scenerycheck", "scripts/nps5lib/scenerycheck.py")

# ---- golden texts: IDENTICAL strings are pinned in Kyty tests/SceneSkipTests.cpp (kGoldenState / kGoldenTable / kGoldenEnv)
GOLDEN_STATE = "seq=2\npreset=performance\nflowers=0\ngrass=0\nmoss=0\nother=0\n"
GOLDEN_TABLE = ("# nps5 scenery table: read by Kyty (KYTY_SCENE_SKIP_FILE)\n"
                "preset\tbalanced\tflowers=50,grass=50,moss=100,other=100\n"
                "preset\tperformance\tflowers=0,grass=0,moss=0,other=0\n"
                "start\tflowers=0,grass=0,moss=0,other=0\n"
                "skip\tlv\tflowers\taaaaaaaaaaaaaaaa\t-\n"
                "level\tlv\tflowers=both,grass=entry\n")
GOLDEN_ENV = ("KYTY_SCENE_KEYS", "KYTY_SCENE_SKIP_FILE", "KYTY_SCENE_STATE_FILE")

# the fake title: what Kyty's WriteState does on a press (tmp file + rename; text = Controller::StateText), then waits for the launcher to react
CHILD = r'''
import json, os, sys, time
state, link, out = os.environ["KYTY_SCENE_STATE_FILE"], sys.argv[1], sys.argv[2]
CATS = ("flowers", "grass", "moss", "other")
def state_text(seq, preset, mask):          # == Controller::StateText in Kyty's sceneSkip.h
    return "seq=%d\npreset=%s\n" % (seq, preset) + "".join("%s=%d\n" % (c, mask[i]) for i, c in enumerate(CATS))
def press(seq, preset, mask):
    with open(state + ".tmp", "wb") as f: f.write(state_text(seq, preset, mask).encode())
    os.rename(state + ".tmp", state)
def wait_for(pred, secs=4.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if pred(): return round(time.time() - t0, 2)
        time.sleep(0.02)
    return None
res = dict(stale_state_at_start=os.path.exists(state), link_at_start=os.path.realpath(link))
press(1, "custom", (0, 100, 100, 100))                                # key 6: flowers off
res["off_s"] = wait_for(lambda: "cats-flowers" in os.path.realpath(link)); res["off_target"] = os.path.realpath(link)
press(2, "full", (100, 100, 100, 100))                                # key 6 again: flowers back on
res["on_s"] = wait_for(lambda: "cats-" not in os.path.realpath(link)); res["on_target"] = os.path.realpath(link)
json.dump(res, open(out, "w"))
'''


class FakeFootprint:
    def __init__(self, pid): pass
    def start(self): return self
    def peak(self): return 0


class Chain(L.Base):
    def setUp(self):
        L.Base.setUp(self)
        self.bin = os.path.join(self.d, "native"); open(self.bin, "w").write("bin")
        with open(os.path.join(self.d, "titles", "PPSA00001.env"), "w") as f: f.write('APP0="%s"\nBIN=%s\n' % (self.app0, self.bin))
        self.saved = (run.preflight, run.verify_binary, run.components, run.shutil.which)
        run.preflight = lambda plan: ([], [])
        run.verify_binary = lambda g, p, sel, environ, patchmod=None: dict(build="01.001", build_id="0123456789abcdef", sidecar=None, patches_applied=[], notes=[])
        run.components = lambda plan: dict(public=None, ps5recomp="r", anyps5="a", kk="k", kyty="y")
        run.shutil.which = lambda n: "/usr/bin/true"
        self.env.update(NPS5_WATCH="0", NPS5_ALLOW_MISSING_HOST_LIBS="1", NPS5_LOADED_CHECK="0")
        self.child = os.path.join(self.d, "child.py"); open(self.child, "w").write(CHILD)
        self.result = os.path.join(self.d, "child.json")

    def tearDown(self):
        run.preflight, run.verify_binary, run.components, run.shutil.which = self.saved

    def execute(self, argv=("--scenery-keys", "on"), child=True):
        t, opts = run.parse_args(["PPSA00001", "--tag", "t1"] + list(argv))
        plan = run.compose(t, opts, dict(self.env)); self.plan_ = plan
        link = plan.live[0].link_path("lv", "level.lvx") if plan.live else os.path.join(self.d, "none")
        self.live = plan.live[0] if plan.live else None; self.state = plan.live[1] if plan.live else None
        def popen(cmd, env=None, **kw):                          # the "title": a python child with the env nps5 composed
            return subprocess.Popen([sys.executable, "-I", self.child, link, self.result], env=dict(env, PATH=os.environ.get("PATH", "")), **kw)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = run.execute(plan, opts, plan.environ, queue_factory=TR.FakeQueue, popen=popen, hasher=lambda p: "abcd", footprint=FakeFootprint)
        return rc

    def n(self):
        with open(self.live.link_path("lv", "level.lvx"), "rb") as f: return self.count(f.read())

    def test_key_press_in_the_title_repoints_the_live_links_and_back(self):
        t0 = time.time(); self.assertEqual(self.execute(), 0)
        res = json.load(open(self.result))
        own = os.path.realpath(os.path.join(self.app0, S.LEVEL_REL % "lv"))
        self.assertEqual(res["link_at_start"], own)                                   # starts on the player's own full level
        self.assertIsNotNone(res["off_s"], "press 6 never repointed the links: " + res["off_target"])
        self.assertLess(res["off_s"], 2.0)
        self.assertIn("cats-flowers", res["off_target"])
        self.assertIsNotNone(res["on_s"], "second press never repointed the links back")
        self.assertLess(res["on_s"], 2.0); self.assertEqual(res["on_target"], own)

    def test_masked_variant_has_the_flowers_removed_while_the_press_is_live(self):
        # the child leaves the flowers off and exits: what the links pointed at when the title stopped is still a real masked file
        self.execute(); res = json.load(open(self.result))
        self.assertEqual(self.n(), 1 + 20)                                            # last press = all on again
        self.live.repoint({"flowers": 0})
        self.assertEqual(self.n(), 1 + 10)

    def test_stale_state_file_of_an_earlier_run_is_dropped_at_start(self):
        t, opts = run.parse_args(["PPSA00001", "--tag", "t1", "--scenery-keys", "on"])
        st = run.compose(t, opts, dict(self.env)).live[1]
        os.makedirs(os.path.dirname(st), exist_ok=True); open(st, "w").write("seq=9\nflowers=0\ngrass=0\n")
        self.execute(); res = json.load(open(self.result))
        self.assertFalse(res["stale_state_at_start"])
        self.assertEqual(res["link_at_start"], os.path.realpath(os.path.join(self.app0, S.LEVEL_REL % "lv")))   # the old press was not applied

    def test_watcher_is_stopped_when_the_child_exits(self):
        self.execute()
        time.sleep(0.8)
        self.assertEqual([t for t in threading.enumerate() if t.name == "scenery-watch" and t.is_alive()], [])
        before = os.path.realpath(self.live.link_path("lv", "level.lvx"))
        with open(self.state, "w") as f: f.write("seq=77\nflowers=0\ngrass=0\n")
        time.sleep(1.3)                                                                # > two watcher intervals
        self.assertEqual(os.path.realpath(self.live.link_path("lv", "level.lvx")), before)

    def test_no_keys_no_watcher_thread(self):
        t, opts = run.parse_args(["PPSA00001", "--tag", "t1"])
        self.assertIsNone(run.compose(t, opts, dict(self.env)).live)


class Contract(L.Base):
    def test_env_names_nps5_sets_are_the_golden_names(self):
        pl = self.plan(["--scenery-keys", "on"])
        self.assertEqual(sorted(k for k in pl.env if k.startswith("KYTY_SCENE")), sorted(GOLDEN_ENV))
        self.assertEqual(pl.env["KYTY_SCENE_KEYS"], "1")

    def kyty_source(self):
        for d in (os.environ.get("NPS5_KYTY_SRC"), os.path.join(REPO, "..", "v144-kyty"), os.path.join(REPO, "..", "kyty")):
            if d:
                p = os.path.join(d, "src", "graphics", "host_gpu", "renderer", "sceneSkip.cpp")
                if os.path.isfile(p): return open(p).read()
        return None

    def test_kyty_reads_exactly_the_golden_env_names(self):
        src = self.kyty_source()
        if src is None: self.skipTest("Kyty source not beside this checkout (set NPS5_KYTY_SRC)")
        read = set(re.findall(r'getenv\("(KYTY_SCENE_\w+)"\)|EnvOn\("(KYTY_SCENE_\w+)"\)', src))
        names = {a or b for a, b in read} - {"KYTY_SCENE_KEYS_SCRIPT"}                # the script is a Kyty-only test hook, never set by nps5
        self.assertEqual(names, set(GOLDEN_ENV))

    def test_state_text_kyty_writes_parses(self):
        r = S.parse_state(GOLDEN_STATE)
        self.assertEqual((r["seq"], r["preset"], r["mask"]), (2, "performance", dict(flowers=0, grass=0, moss=0, other=0)))
        # the mask_text spelling the table uses is the same one the state uses
        self.assertEqual(S.mask_text(r["mask"]), "flowers=0,grass=0,moss=0,other=0")

    def test_skip_table_writer_output_is_the_golden_text(self):
        pr = lambda lv, cat: S.parse_row([lv, cat, "src=" + "0" * 64, "class=bg", "drop=" + L.KEY_F])
        sk = S.parse_skip(["lv", "flowers", "vs=" + "a" * 16, "ps=-"])
        p = os.path.join(self.d, "t.tsv")
        S.write_skip_table(p, [("balanced", dict(flowers=50, grass=50)), ("performance", dict(flowers=0, grass=0, moss=0, other=0))],
                           dict(flowers=0, grass=0, moss=0, other=0), [sk], [pr("lv", "flowers"), pr("lv", "grass")])
        self.assertEqual(open(p).read(), GOLDEN_TABLE)

    def test_check_gate_runs_the_chain(self):
        lst = open(os.path.join(REPO, ".nps5-check-tests")).read()
        self.assertIn("tests/test_scenery_chain.py", lst); self.assertIn("tests/test_scenery_live.py", lst)


class IngameCheck(unittest.TestCase):
    """scripts/nps5lib/scenerycheck.py: the verdict of the automated in-game run (synthetic logs)."""
    ERR = ("[scenery] key script: 1 presses, 0 bad entries\n[scenery] key script: press 3 at 3.0 s\n"
           "[scenery] seq 1: flowers OFF (instant: 2 shader pairs in lv), grass on, moss on, other on\n[ps4-vfs] overlay open data/levels/lv/level.lvx\n")

    def test_pass_fail(self):
        ok = SC.check(self.ERR, "nps5 run: scenery: seq 1 staged (2 files, 0.1 s)\n")
        self.assertEqual([r[0] for r in ok], ["PASS", "PASS", "INFO"])
        self.assertIn("1 overlay open", ok[2][2])
        bad = SC.check(self.ERR, "nothing\n"); self.assertEqual(bad[1][0], "FAIL")
        self.assertEqual(SC.check("", "")[0][0], "FAIL")


class ReentryCheck(unittest.TestCase):
    """scenerycheck.check_reentry on synthetic logs: boot level, press, leave planet, re-enter."""
    HEAD = "[scenery] scene lv: 0 shader pairs active (F+ G+ M+ O+ (full))\n"
    PRESS = "[scenery] seq 1: flowers OFF (instant: 0 shader pairs in lv), grass on, moss on, other on\n"
    def S(self, name, f="F-"): return "[scenery] scene %s: 0 shader pairs active (%s G+ M+ O+ (custom))\n" % (name, f)

    def test_pass(self):
        r = SC.check_reentry(self.HEAD + self.PRESS + self.S("worldmap") + self.S("worldmap") + self.S("lv"), "lv")
        self.assertEqual([x[0] for x in r], ["PASS", "PASS", "PASS", "INFO"])
        self.assertIn("boot entry: F+", r[2][2]); self.assertIn("not logged", r[3][2])

    def test_fail_never_left(self):
        r = SC.check_reentry(self.HEAD + self.PRESS + self.S("lv"), "lv")
        self.assertEqual([x[0] for x in r][:3], ["PASS", "FAIL", "FAIL"])

    def test_fail_reentry_with_flowers_on_or_no_press(self):
        r = SC.check_reentry(self.HEAD + self.PRESS + self.S("worldmap") + self.S("lv", "F+"), "lv")
        self.assertEqual(r[2][0], "FAIL")
        self.assertEqual(SC.check_reentry(self.HEAD + self.S("worldmap") + self.S("lv"), "lv")[0][0], "FAIL")

    def test_overlay_open_line_reported(self):
        r = SC.check_reentry(self.HEAD + self.PRESS + self.S("worldmap") + self.S("lv") + "[ps4-vfs] overlay open cats-flowers/level.lvx\n", "lv")
        self.assertIn("1 overlay open", r[3][2])

    # --- deterministic version: overlay open lines + Kyty draw summaries ---
    LOG = HEAD + PRESS + "[scenery] scene worldmap: 0 shader pairs active (F- G+ M+ O+ (custom))\n"
    def OP(self, rel, tgt): return "[ps4-vfs] overlay open %s -> %s\n" % (rel, tgt)
    def err(self, tgt):
        return self.LOG + self.S("lv") + self.OP("Content/Levels/lv/a.lvx", tgt)
    GOOD = "/s/scenery/PPSA21564/cats-flowers/Content/Levels/lv/a.lvx"

    def test_variant_pass_and_fail(self):
        r = SC.check_reentry(self.err(self.GOOD), "lv", strict=True)
        self.assertEqual((r[3][0], r[3][1]), ("PASS", "variant")); self.assertIn("cats-flowers", r[3][2])
        self.assertEqual(SC.check_reentry(self.err("/s/scenery/x/cats-flowers+grass/p/a"), "lv", strict=True)[3][0], "PASS")
        self.assertEqual(SC.check_reentry(self.err("/g/PPSA21564/app0/Content/Levels/lv/a.lvx"), "lv", strict=True)[3][0], "FAIL")   # the player's own file
        self.assertEqual(SC.check_reentry(self.err("/s/cats-flowers~50/p/a"), "lv", strict=True)[3][0], "FAIL")                    # thinned, not off
        self.assertEqual(SC.check_reentry(self.err("/s/cats-grass/p/a"), "lv", strict=True)[3][0], "FAIL")
        self.assertEqual(SC.check_reentry(self.LOG + self.S("lv"), "lv", strict=True)[3][0], "FAIL")                               # no open logged
        # an open BEFORE the re-entry does not count
        early = self.HEAD + self.OP("Content/Levels/lv/a.lvx", self.GOOD) + self.PRESS + "[scenery] scene worldmap: 0 shader pairs active (F- G+ M+ O+ (custom))\n" + self.S("lv")
        self.assertEqual(SC.check_reentry(early, "lv", strict=True)[3][0], "FAIL")

    @staticmethod
    def SUM(label, flips, pairs):
        d = sum(c for _, _, c in pairs)
        return ("# kyty draw summary v1\n# dump 1 why x flip 9 window_flips %d window_ms 6000 draws %d pairs %d label %s\n" % (flips, d, len(pairs), label)
                + "".join("D %016x %016x %d\n" % (v, p, c) for v, p, c in pairs))

    def sums(self, before, after):
        return [SC.parse_draws(self.SUM("scene:lv#1", 100, before)), SC.parse_draws(self.SUM("scene:lv#2", 200, after))]

    def test_draws_pass_fail(self):
        common = [(1, 2, 5000), (3, 4, 900)]
        flowers = [(10, 20, 1500), (11, 20, 600)]                       # 21 per flip before
        ok = SC.check_draws(self.sums(common + flowers, [(1, 2, 10100), (3, 4, 1800)]), "lv")
        self.assertEqual(ok[0], "PASS"); self.assertIn("21.0", ok[1]); self.assertIn("-> 0.0", ok[1])
        st = SC.check_draws(self.sums(common + flowers, common + flowers), "lv")[0]           # nothing vanished
        self.assertEqual(st, "FAIL")
        self.assertEqual(SC.check_draws(self.sums(common + flowers, common + [(10, 20, 400)]), "lv", flower_pairs={("%016x" % 10, "%016x" % 20), ("%016x" % 11, "%016x" % 20)})[0], "FAIL")  # 2/flip left of 21 = 9.5 %
        self.assertEqual(SC.check_draws(self.sums(common, common), "lv")[0], "FAIL")           # no flowers before at all
        self.assertEqual(SC.check_draws(self.sums(common + flowers, common)[:1], "lv")[0], "FAIL")   # one entry only

    def test_draws_given_pairs_and_cli(self):
        import tempfile, subprocess
        d = tempfile.mkdtemp()
        for i, (lab, fl, pr) in enumerate([("scene:lv#1", 100, [(1, 2, 5000), (10, 20, 2100)]), ("scene:lv#2", 200, [(1, 2, 10000)])]):
            open(os.path.join(d, "t.%d.sc%d_lv.draws.txt" % (i, i + 1)), "w").write(self.SUM(lab, fl, pr))
        open(os.path.join(d, "junk.draws.txt"), "w").write("not a summary\n")
        e = os.path.join(d, "run.err"); open(e, "w").write(self.err(self.GOOD))
        fp = os.path.join(d, "pairs.txt"); open(fp, "w").write("# flowers\n000000000000000a 0000000000000014\n")
        for extra in ([], ["--flower-pairs", fp]):
            p = subprocess.run([sys.executable, "-I", os.path.join(REPO, "scripts", "nps5lib", "scenerycheck.py"), "--reentry", e, "--scene", "lv"] + extra + ["--draws", d], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("PASS variant", p.stdout); self.assertIn("PASS draws", p.stdout); self.assertIn("21.0 ", p.stdout)
        bad = SC.check_reentry(self.err("/g/app0/a"), "lv", draws=SC.load_draws([d]))
        self.assertEqual([x[0] for x in bad[3:]], ["FAIL", "PASS"])

    def test_cli(self):
        import tempfile, subprocess
        with tempfile.NamedTemporaryFile("w", suffix=".err", delete=False) as f:
            f.write(self.HEAD + self.PRESS + self.S("worldmap") + self.S("lv"))
        p = subprocess.run([sys.executable, "-I", os.path.join(REPO, "scripts", "nps5lib", "scenerycheck.py"), "--reentry", f.name, "--scene", "lv"], capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertIn("PASS reentry", p.stdout)


if __name__ == "__main__":
    unittest.main()
