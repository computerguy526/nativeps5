#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/run.py: env layering, refusals, arm parsing, queue, run.json schema. No title is run."""
import importlib.util, os, sys, tempfile, unittest, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("nps5run", os.path.join(HERE, "..", "scripts", "nps5lib", "run.py"))
run = importlib.util.module_from_spec(spec); spec.loader.exec_module(run)

GAME = """title_id\tPPSA00001
name\tFixture
build\t01.001\teboot=0123456789abcdef
package\t01.001
gpu\tkyty
canonical_lineage\tlin1
patches_default\ta b
env\tA_ALL=1
env\tA_KYTY=1\tgpu=kyty
env\tA_PERF=1\tclass=perf
env\tLAYER=profile
env_forbidden\tBAD_WORD=1\twhy
marker\tm\tlog:READY
blank_fail_s\t33
"""
SCENES = "scn\t-guest -x\tpad/pad.vpad\tmarker:m\t60\tsv\td\nsl\t-\t-\t-\t170\t-\td\n"


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        gd = os.path.join(self.d, "games", "PPSA00001"); os.makedirs(os.path.join(gd, "pad"))
        open(os.path.join(gd, "game.tsv"), "w").write(GAME)
        open(os.path.join(gd, "scenes.tsv"), "w").write(SCENES)
        open(os.path.join(gd, "pad", "pad.vpad"), "w").write("0:ly=1\n")
        os.makedirs(os.path.join(self.d, "titles"))
        self.env = dict(NPS5_GAMES_DIR=os.path.join(self.d, "games"), NPS5_SCRATCH=os.path.join(self.d, "s"), NPS5_PUBLIC_DIR=self.d,
                        NPS5_SELF_MEM_LIMIT_MB="18944", NPS5_KK_ICD="/kk/icd.json", NPS5_ANYPS5_BUILD="/any/build-arm64",
                        NPS5_KYTY_DIR="/kyty", NPS5_BREW_PREFIX="/brew")

    def titles(self, text):
        open(os.path.join(self.d, "titles", "PPSA00001.env"), "w").write(text)

    def plan(self, argv, env=None):
        tid, opts = run.parse_args(["PPSA00001"] + argv)
        return run.compose(tid, opts, dict(self.env, **(env or {})))

    def refused(self, argv, code=78, env=None):
        with self.assertRaises(run.Refuse) as c:
            self.plan(argv, env)
        self.assertEqual(c.exception.code, code, str(c.exception)); return str(c.exception)

    # ---- layering
    def test_layer_order(self):
        self.titles('APP0=/dump\nBIN=/b/native\nENV="LAYER=titles T_ONLY=1"\n')
        p = self.plan(["--scene", "scn", "--arm", "x:LAYER=arm,A_ALL=9", "--env", "A_ALL=10"])
        self.assertEqual(p.env["LAYER"], "arm"); self.assertEqual(p.layers["LAYER"], "arm:x")
        self.assertEqual(p.env["A_ALL"], "10"); self.assertEqual(p.layers["A_ALL"], "--env")
        self.assertEqual(p.env["T_ONLY"], "1"); self.assertEqual(p.layers["T_ONLY"], "titles")
        self.assertEqual(p.env["NPS5_VPAD_SCRIPT"], "@" + os.path.join(self.d, "games", "PPSA00001", "pad", "pad.vpad"))
        self.assertEqual(p.env["NPS5_TITLE_RUN"], "1"); self.assertEqual(p.env["NPS5_APP0_DIR"], "/dump")
        self.assertEqual(p.args, ["-guest", "-x"]); self.assertEqual(p.secs, 60); self.assertEqual(p.until, "marker:m")

    def test_titles_beats_profile_and_class(self):
        self.titles('ENV="LAYER=titles"\n')
        p = self.plan([]); self.assertEqual(p.env["LAYER"], "titles")
        self.assertNotIn("A_PERF", p.env); self.assertIn("A_PERF", self.plan(["--class", "perf"]).env)
        self.assertEqual(p.env["NPS5_RUN_CLASS"], "func")

    def test_titles_file_is_not_executed(self):
        self.titles('BIN=$(touch %s/pwned)\n' % self.d)
        self.refused([], 2); self.assertFalse(os.path.exists(os.path.join(self.d, "pwned")))
        self.titles("RM=1\n"); self.refused([], 2)

    def test_arm_pieces(self):
        p = self.plan(["--arm", "b:libs=/L,kk=/K,kyty=/Y,bin=/B/native,workdir=/W,save=s1,X=1"])
        self.assertEqual(p.env["DYLD_LIBRARY_PATH"], "/L/core/libs/libs/unpatched")
        self.assertEqual(p.env["VK_DRIVER_FILES"], "/K/install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json")
        self.assertEqual(p.env["NPS5_KYTY_GPU_LIB"], "/Y/build-arm64/kyty_gpu.dylib")
        self.assertEqual(p.sel["path"], "/B/native"); self.assertEqual(p.workdir, "/W"); self.assertEqual(p.env["HOME"], "/W/home")
        self.assertTrue(p.env["NPS5_USERDATA_DIR"].endswith("userdata/PPSA00001/s1")); self.assertEqual(p.env["X"], "1")

    # ---- binary selection
    def test_binary_selection(self):
        p = self.plan([]); self.assertEqual(p.sel["source"], "cache"); self.assertEqual(p.sel["patches"], "a,b")
        self.assertTrue(p.sel["path"].endswith("native-cache/PPSA00001/lin1+a+b")); self.assertEqual(p.sel["state"], "missing")
        os.makedirs(os.path.join(self.d, "src")); open(os.path.join(self.d, "src", "native_lin1"), "w").write("x")
        self.titles('BIN_DIR=%s/src\n' % self.d); self.assertEqual(self.plan([]).sel["state"], "build")
        self.titles('BIN=/x/native\n'); self.assertEqual(self.plan([]).sel["path"], "/x/native")
        self.assertEqual(self.plan(["--bin", "/y"]).sel["path"], "/y")
        self.assertEqual(self.plan(["--patch", "a"]).sel["patches"], "a")

    # ---- refusals (exit 78 unless noted)
    def test_refusals(self):
        self.assertIn("env_forbidden", self.refused(["--env", "BAD_WORD=1"]))
        self.refused(["--arm", "x:BAD_WORD=1"])
        self.refused(["--env", "NPS5_SELF_MEM_LIMIT_MB=18945"])
        self.refused(["--env", "NPS5_SELF_MEM_LIMIT_MB=20000"], env=dict(NPS5_SELF_MEM_LIMIT_MB="12000"))   # above this Mac's own max
        self.refused(["--env", "KYTY_EOP_TIMESTAMPS=gpu"]); self.refused(["--env", "APS5_PROFILE_GPU=1"])
        self.refused(["--secs", "200"]); self.refused(["--secs", "120"])
        self.assertEqual(self.plan(["--secs", "120"], dict(NPS5_RUN_REASON="cold cache")).secs, 120)
        self.refused(["--scene", "sl"])                     # scene says 170 s: needs a reason
        self.refused(["--scene", "nope"], 2); self.refused(["--class", "fast"], 2); self.refused(["--gpu", "x"], 2)
        self.refused(["--arm", "bad"], 2); self.refused(["--env", "NOEQUALS"], 2); self.refused(["--secs", "x"], 2)

    def test_profiling_ban_off_needs_flag_and_short_run(self):
        open(os.path.join(self.d, "games", "PPSA00001", "game.tsv"), "a").write("gpu_profile_ban\toff\n")
        self.refused(["--env", "APS5_PROFILE_PASSES=1", "--secs", "30"])
        self.refused(["--env", "APS5_PROFILE_PASSES=1", "--secs", "60"], env=dict(NPS5_ALLOW_GPU_PROFILE="1"))
        self.plan(["--env", "APS5_PROFILE_PASSES=1", "--secs", "30"], dict(NPS5_ALLOW_GPU_PROFILE="1"))

    # ---- arm syntax
    def test_parse_arm(self):
        a = run.parse_arm("A:K=V,bin=/b,lib=/l,Z=")
        self.assertEqual((a["label"], a["env"], a["pieces"]), ("A", [("K", "V"), ("Z", "")], {"bin": "/b", "libs": "/l"}))
        self.assertEqual(run.parse_arm("K=V")["label"], None); self.assertEqual(run.parse_arm(None)["env"], [])
        for bad in ("A:K", "A:bin=/a,bin=/b", "A:1x=2", "nothing"):
            with self.assertRaises(run.Refuse, msg=bad): run.parse_arm(bad)

    # ---- queue
    def test_queue_fifo_and_func_passes_held_perf(self):
        g = os.path.join(self.d, "guard"); os.makedirs(g)
        t = [1000.0]; slept = []
        def mk(cls, pid): return run.Queue(g, cls, "t", pid=pid, now=lambda: t[0], sleep=lambda s: slept.append(s), load=lambda: 0, pressure=lambda: 1, swap_mb=lambda: 0, polls=3)
        me = os.getpid()
        qa = mk("perf", me); qa.take_ticket(); t[0] += 1
        qb = mk("func", me); qb.take_ticket()
        self.assertEqual(qb._first(), os.path.basename(qa.ticket))      # FIFO
        open(os.path.join(g, "start.hold"), "w").write("load")
        os.utime(os.path.join(g, "start.hold"), (t[0], t[0]))
        self.assertEqual(qb._first(), os.path.basename(qb.ticket))      # func skips the perf ticket while the hold is fresh
        os.remove(qa.ticket); os.remove(os.path.join(g, "start.hold"))
        self.assertTrue(qb.acquire(log=lambda m: None)); self.assertEqual(open(os.path.join(g, "title.lock", "pid")).read().strip(), str(me))
        qc = mk("func", me); self.assertFalse(qc.acquire(log=lambda m: None))   # lock held by a live pid
        qb.release(); self.assertFalse(os.path.exists(os.path.join(g, "title.lock")))

    def test_queue_stale_lock_and_dead_ticket(self):
        g = os.path.join(self.d, "guard2"); os.makedirs(os.path.join(g, "title.lock")); os.makedirs(os.path.join(g, "title.queue"))
        open(os.path.join(g, "title.lock", "pid"), "w").write("999999\n")
        open(os.path.join(g, "title.queue", "000000000001-x-999998"), "w").write("perf\n")
        q = run.Queue(g, "func", "t", sleep=lambda s: None, load=lambda: 0, pressure=lambda: 1, swap_mb=lambda: 0, polls=3)
        self.assertTrue(q.acquire(log=lambda m: None)); q.release()
        self.assertEqual(os.listdir(os.path.join(g, "title.queue")), [])

    def test_start_hold_perf_always_func_on_load(self):
        g = os.path.join(self.d, "guard3"); os.makedirs(g); open(os.path.join(g, "start.hold"), "w").write("x")
        mk = lambda c, load: run.Queue(g, c, load=lambda: load, pressure=lambda: 1, swap_mb=lambda: 0)
        self.assertTrue(mk("perf", 0)._held()); self.assertFalse(mk("func", 5)._held()); self.assertTrue(mk("func", 30)._held())

    # ---- run.json
    def test_run_json_schema(self):
        self.titles('APP0=/dump\nBIN=/b/native\n')
        p = self.plan(["--scene", "scn", "--arm", "x:K=1"])
        rj = run.new_run_json(p, dict(build="01.001", build_id="0123456789abcdef", sidecar=None, patches_applied=["a"]), "abcd", run.components(p))
        self.assertEqual(run.validate_run_json(rj), [])
        json.dumps(rj)
        self.assertEqual(rj["env"]["K"], "1"); self.assertEqual(rj["arm"]["label"], "x"); self.assertEqual(rj["class"], "func")
        self.assertEqual(rj["binary"]["sha256_16"], "abcd"); self.assertEqual(rj["patches"], ["a", "b"])
        del rj["timings"]; rj["class"] = "x"
        self.assertTrue(any("timings" in x for x in run.validate_run_json(rj))); self.assertTrue(any("class" in x for x in run.validate_run_json(rj)))

    def test_dry_run_text(self):
        self.titles('APP0=/dump\nBIN=/b/native\n')
        s = run.dry_run_text(self.plan(["--scene", "scn"]))
        self.assertIn("  A_ALL=1\n", s); self.assertIn("command: ", s); self.assertIn("/b/native -guest -x", s)


    def test_bad_until_refused(self):
        self.titles("APP0=/dump\nBIN=/b/native\n")
        self.assertIn("--until", self.refused(["--until", "marker:nope"], 2))
        self.assertIn("--until", self.refused(["--until", "pic,black:x"], 2))

    def test_judge_from_profile(self):
        self.titles("APP0=/dump\nBIN=/b/native\n")
        p = self.plan(["--scene", "scn", "--until", "marker:m+5,stall:20"])
        j = run._watch().judge_for(p)
        self.assertEqual((j.blank_fail, j.stall_fail, j.until["kind"], j.until["arg"], j.until["n"]), (33.0, 20.0, "log", "READY", 5.0))


if __name__ == "__main__":
    unittest.main()
