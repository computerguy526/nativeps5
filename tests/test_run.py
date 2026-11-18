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
    def test_shader_cache_words_never_raise(self):
        # the shader cache is an optimisation: an unknown title, a missing list or a broken profile gives a warning or nothing, never an error
        out = run.shader_cache_words("PPSA99999", dict(os.environ, NPS5_SCRATCH=tempfile.gettempdir()), "/nonexistent/app0")
        self.assertTrue(all(k is None for k, _, _ in out))
        self.assertTrue(out == [] or "shader cache not used" in out[0][2])

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
        # `nps5 cache prepare` runs (tag cache-*, class func) ask for a 1800 s ceiling; any other tag / class / larger value is still capped
        self.assertEqual(self.plan(["--tag", "cache-prepare", "--secs", "1800"], dict(NPS5_RUN_REASON="nps5 cache")).secs, 1800)
        self.refused(["--tag", "cache-prepare", "--secs", "1801"], env=dict(NPS5_RUN_REASON="x")); self.refused(["--tag", "mytag", "--secs", "1800"], env=dict(NPS5_RUN_REASON="x"))
        self.assertEqual(self.plan(["--secs", "120"], dict(NPS5_RUN_REASON="cold cache")).secs, 120)
        self.refused(["--scene", "sl"])                     # scene says 170 s: needs a reason
        self.refused(["--scene", "nope"], 2); self.refused(["--class", "fast"], 2); self.refused(["--gpu", "x"], 2)
        self.refused(["--arm", "bad"], 2); self.refused(["--env", "NOEQUALS"], 2); self.refused(["--secs", "x"], 2)

    def test_play_session(self):
        # the README's "how to play" step: a long interactive run is refused without --play, allowed with it
        self.assertIn("--play", self.refused(["--secs", "3600"], env=dict(NPS5_RUN_REASON="playing")))
        p = self.plan(["--play"])
        self.assertTrue(p.play); self.assertEqual(p.secs, run.PLAY_CAP_S); self.assertIsNone(p.until)
        self.assertNotIn("NPS5_AUDIO_NULL", p.env)                                     # sound on
        self.assertEqual(p.env["PS4_NO_DIALOGS"], "1"); self.assertEqual(p.env["NPS5_TITLE_RUN"], "1")
        self.assertEqual(self.plan(["--play", "--secs", "3600"]).secs, 3600)
        self.assertIsNone(self.plan(["--play", "--scene", "scn"]).until)               # the scene's target does not end a session
        self.refused(["--play", "--secs", "7201"]); self.refused(["--play", "--secs", "0"], 2)
        self.refused(["--play", "--until", "marker:m"], 2); self.refused(["--play", "--class", "perf"], 2)
        # every other rule stays
        self.refused(["--play", "--env", "BAD_WORD=1"]); self.refused(["--play", "--env", "NPS5_SELF_MEM_LIMIT_MB=18945"])
        self.refused(["--play", "--env", "APS5_PROFILE_GPU=1"])
        # without --play nothing changed
        q = self.plan([]); self.assertFalse(q.play); self.assertEqual(q.env["NPS5_AUDIO_NULL"], "1")
        self.assertIsNone(run.watch_run(p, type("P", (), {"poll": lambda s: None, "pid": 1})(), "/dev/null", {}))

    # ---- review findings
    def test_zero_or_negative_seconds_never_reach_the_timeout_tool(self):
        self.refused(["--secs", "0"], 2); self.refused(["--secs", "-5"], 2)          # gtimeout 0 means no limit at all
        open(os.path.join(self.d, "games", "PPSA00001", "scenes.tsv"), "a").write("z0\t-\t-\t-\t0\t-\td\nsm\t-\t-\t-\t45/120\t-\td\n")
        self.refused(["--scene", "z0"], 2)
        self.assertEqual(self.plan(["--scene", "sm"]).secs, 45)                        # <s>/<max>: the scene's seconds, not the default

    def test_names_are_not_paths(self):
        for bad in ("../x", "a/b", "..", ".hidden", "/abs", ""):
            if bad: self.refused(["--tag", bad], 2)
            self.refused(["--save", bad or "a b"], 2)
        self.refused(["--workdir", "../../x"], 2); self.refused(["--arm", "a:save=../y"], 2)
        p = self.plan(["--tag", "cache-rec-level:w1", "--workdir", "arm1"])
        self.assertTrue(p.run_dir.endswith("/runs/PPSA00001/cache-rec-level:w1")); self.assertTrue(p.workdir.endswith("/work/PPSA00001/arm1"))

    def test_layout_failure_is_a_refusal_not_a_traceback(self):
        repo, run.REPO = run.REPO, self.d                    # no scripts/nps5-env.sh there: nothing resolves the layout
        try:
            with self.assertRaises(run.Refuse) as c:
                run.machine_env(dict(PATH=os.environ.get("PATH", "")))
        finally:
            run.REPO = repo
        self.assertEqual(c.exception.code, 2)

    def test_gpu_user_is_decided_by_the_executable_not_by_a_command_line(self):
        comm = ["100 /usr/bin/python3", "50 /bin/bash",
                "201 /ws/nps5-scratch/build/PPSA00001/native_kyty6", "202 /ws/b/native", "200 /opt/homebrew/bin/gtimeout",
                "300 /bin/bash", "301 /bin/bash", "302 /bin/sh", "303 /usr/bin/python3", "304 /usr/bin/grep", "305 /usr/bin/vim",
                "400 /t/agc_frame_replay", "401 /usr/bin/python3", "402 /t/kk_spv_gfx", "403 /Applications/My App.app/Contents/MacOS/My App"]
        args = ["100 python3 scripts/nps5 run PPSA00001 --bin /s/native_x", "50 /bin/bash bench.sh /s/native_a",
                "201 /ws/nps5-scratch/build/PPSA00001/native_kyty6 -guest", "202 /ws/b/native", "200 gtimeout 90 /ws/nps5-scratch/build/PPSA00001/native_kyty6",
                "300 /bin/bash /w/run_sandboxed.sh /s/native_kyty6 90", "301 bash /w/run_astro.sh --bin /s/native_kyty6", "302 sh -c sleep 5; /s/native_x",
                "303 python3 -I /r/scripts/nps5 run PPSA00002 --bin /s/native_y", "304 grep -r native_ /s/native_dir", "305 vim /s/native_notes.txt",
                "400 /t/agc_frame_replay cap4", "401 python3 -u /t/shaderlab/harness.py --gpu", "402 /t/kk_spv_gfx a.spv", "403 /Applications/My App.app/Contents/MacOS/My App /s/native_x"]
        # waiting wrappers, runners, a timeout wrapper, tools that only NAME a title binary: none of them holds the GPU
        self.assertEqual(run.gpu_users(comm, args, mine={100, 50}), ["201 native_kyty6", "202 native", "400 agc_frame_replay", "401 harness.py", "402 kk_spv_gfx"])
        waiting = (["%d /bin/bash" % p for p in range(500, 520)], ["%d /bin/bash /w/run_sandboxed.sh /s/native_kyty6 90" % p for p in range(500, 520)])
        self.assertEqual(run.gpu_users(*waiting), [])                                   # 20 queued wrappers: the run may start
        class P: cls = "func"
        warn, busy = run.preflight(P, power=lambda: "AC", others=lambda: 0, procs=lambda: run.gpu_users(*waiting))
        self.assertEqual(busy, [])
        warn, busy = run.preflight(P, power=lambda: "AC", others=lambda: 0, procs=lambda: run.gpu_users(comm, args, {100, 50}))
        self.assertIn("201 native_kyty6", busy[0]); self.assertIn("402 kk_spv_gfx", busy[0])      # the refusal names every pid
        self.assertIn(os.getpid(), run._ancestors())
        self.assertIsInstance(run.gpu_processes(), list)                                # the real ps call parses

    def test_component_identity_names_changed_sources(self):
        import subprocess
        d = os.path.join(self.d, "co"); os.makedirs(os.path.join(d, "sub"))
        def g(*a, data=None): return subprocess.run(("git", "-C", d) + a, input=data, capture_output=True, check=True).stdout.decode().strip()
        g("init", "-q")
        g("fast-import", "--quiet", data=b"commit refs/heads/main\ncommitter T <t@example.invalid> 1 +0000\ndata 1\nc\nM 100644 inline f.c\ndata 6\nint a;\nM 100644 inline sub/g.c\ndata 6\nint b;\n\n")
        g("symbolic-ref", "HEAD", "refs/heads/main"); g("reset", "-q", "--hard")
        head = g("rev-parse", "--short=10", "HEAD")
        self.assertEqual(run.commit_of(d), head)
        self.assertIsNone(run.commit_of(os.path.join(d, "sub")))            # a folder inside a checkout is not that component
        self.assertIsNone(run.commit_of(os.path.join(self.d, "nope")))
        open(os.path.join(d, "f.c"), "w").write("int a = 1;\n")
        one = run.commit_of(d); self.assertRegex(one, "^%s\\+[0-9a-f]{8}$" % head)
        open(os.path.join(d, "f.c"), "w").write("int a = 2;\n")
        self.assertNotEqual(run.commit_of(d), one)                          # another change, another identity
        # the public layout: <NPS5_KK_WORK>/mesa is the checkout, with the series applied and not committed
        work = os.path.join(self.d, "kkwork"); os.makedirs(work); os.rename(d, os.path.join(work, "mesa"))
        self.assertRegex(run.kk_commit(work), "^%s\\+[0-9a-f]{8}$" % head)
        self.assertIsNone(run.kk_commit(os.path.join(self.d, "s"), None))

    def test_peak_footprint_sampler(self):
        vals = {7: [5 << 20, 900 << 20, 100 << 20], 8: [3000 << 20]}
        f = run.Footprint(7, children=lambda pid: [8], reader=lambda pid: vals[pid].pop(0) if vals[pid] else None)
        f.sample(); f.sample(); f.sample()
        self.assertEqual(f.peak(), 3000)
        self.assertIsNone(run.Footprint(7, children=lambda pid: [], reader=lambda pid: None).start().peak())
        me = run.Footprint(os.getpid()); me.sample()                         # the real call, on this process
        self.assertGreater(me.peak() or 0, 1)

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


    def test_kyty_until_dumps_frames_for_the_watcher(self):
        # the Kyty back end prints no [pstat] line: with a target the watcher needs frame dumps in <run>/frames
        self.titles("APP0=/dump\nBIN=/b/native\nGPU=kyty\n")
        p = self.plan(["--until", "first-present+10"])
        self.assertEqual(p.env["KYTY_DUMP_FRAMES"], "150"); self.assertEqual(p.layers["KYTY_DUMP_FRAMES"], "watch")
        self.assertEqual(p.env["KYTY_DUMP_DIR"], os.path.join(p.run_dir, "frames"))
        self.assertEqual(self.plan(["--until", "first-present", "--env", "KYTY_DUMP_FRAMES=7"]).env["KYTY_DUMP_FRAMES"], "7")
        self.assertNotIn("KYTY_DUMP_FRAMES", self.plan([]).env)

    def test_bad_until_refused(self):
        self.titles("APP0=/dump\nBIN=/b/native\n")
        self.assertIn("--until", self.refused(["--until", "marker:nope"], 2))
        self.assertIn("--until", self.refused(["--until", "pic,black:x"], 2))

    def test_judge_from_profile(self):
        self.titles("APP0=/dump\nBIN=/b/native\n")
        p = self.plan(["--scene", "scn", "--until", "marker:m+5,stall:20"])
        j = run._watch().judge_for(p)
        self.assertEqual((j.blank_fail, j.stall_fail, j.until["kind"], j.until["arg"], j.until["n"]), (33.0, 20.0, "log", "READY", 5.0))


class EnvScript(unittest.TestCase):
    """scripts/nps5-env.sh reading nps5.conf (sourced by every script and by machine_env)."""
    def env_of(self, conf):
        import subprocess
        d = tempfile.mkdtemp(); c = os.path.join(d, "nps5.conf"); open(c, "w").write(conf)
        sh = os.path.join(HERE, "..", "scripts", "nps5-env.sh")
        r = subprocess.run(["bash", "-c", '. "$1" && env', "_", sh], capture_output=True, text=True, env=dict(PATH=os.environ["PATH"], NPS5_CONF=c, NPS5_HW="m4pro-24g"))
        return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l), r.stderr, d

    def test_values_may_use_the_root_whatever_the_line_order(self):
        e, _, _ = self.env_of("NPS5_SCRATCH=$NPS5_ROOT/scratch\nNPS5_KK_WORK=$NPS5_ROOT/build/kk        # Mesa checkout\nNPS5_ROOT=/ws root\n")
        self.assertEqual(e["NPS5_ROOT"], "/ws root"); self.assertEqual(e["NPS5_SCRATCH"], "/ws root/scratch")     # was /scratch: expanded before the root was known
        self.assertEqual(e["NPS5_KK_WORK"], "/ws root/build/kk")                                                   # no trailing blanks from the comment
        self.assertEqual(e["NPS5_RECOMP_DIR"], "/ws root/ps5recomp")

    def test_a_name_is_never_code(self):
        e, err, d = self.env_of("NPS5_X;touch %s/pwned=1\nFOO=1\n" % tempfile.gettempdir())
        self.assertFalse(os.path.exists(os.path.join(tempfile.gettempdir(), "pwned"))); self.assertIn("ignoring", err); self.assertNotIn("FOO", e)


class FakeProc:
    def __init__(self, rc=0, boom=None): self.returncode, self.boom, self.terminated, self.alive = rc, boom, False, True
    def poll(self): return None if self.alive else self.returncode
    def wait(self, timeout=None):
        if self.boom: b, self.boom = self.boom, None; raise b
        self.alive = False; return self.returncode
    def terminate(self): self.terminated = True; self.alive = False
    def kill(self): self.alive = False


class FakeQueue:
    def __init__(self, *a, **k): self.released = False; FakeQueue.last = self
    def acquire(self): return True
    def release(self): self.released = True


class Exec(unittest.TestCase):
    """execute() with a fake child: run.json states, clean-up on failure, the binary's patch record."""
    titles = T.titles

    def setUp(self):
        T.setUp(self)
        self.app0 = os.path.join(self.d, "app0"); os.makedirs(self.app0)
        self.bin = os.path.join(self.d, "native"); open(self.bin, "w").write("bin")
        self.titles("APP0=%s\nBIN=%s\n" % (self.app0, self.bin))
        self.saved = (run.preflight, run.verify_binary, run.components, run.shutil.which)
        run.preflight = lambda plan: ([], [])
        run.verify_binary = lambda g, p, sel, environ, patchmod=None: dict(build="01.001", build_id="0123456789abcdef", sidecar=None, patches_applied=[], notes=[])
        run.components = lambda plan: dict(public=None, ps5recomp="r", anyps5="a", kk="k", kyty="y")
        run.shutil.which = lambda n: "/usr/bin/true"
        self.env["NPS5_WATCH"] = "0"

    def tearDown(self):
        run.preflight, run.verify_binary, run.components, run.shutil.which = self.saved

    def go(self, argv, proc):
        tid, opts = run.parse_args(["PPSA00001", "--tag", "t1"] + argv)
        plan = run.compose(tid, opts, dict(self.env)); self.plan_ = plan
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run.execute(plan, opts, plan.environ, queue_factory=FakeQueue, popen=lambda *a, **k: proc, hasher=lambda p: "abcd")

    def rj(self): return json.load(open(os.path.join(self.plan_.run_dir, "run.json")))

    def test_fixed_run_and_schema(self):
        self.assertEqual(self.go([], FakeProc(124)), 0)
        rj = self.rj(); self.assertEqual(rj["verdict"], "DONE_FIXED"); self.assertEqual(run.validate_run_json(rj), [])
        self.assertIn("peak_footprint_mib", rj["timings"]); self.assertTrue(FakeQueue.last.released)

    def test_target_not_seen_is_never_done(self):
        self.assertEqual(self.go(["--until", "marker:m"], FakeProc(124)), 0); self.assertEqual(self.rj()["verdict"], "TIMEOUT")
        self.assertEqual(self.go(["--until", "marker:m"], FakeProc(0)), 1)                 # the title quit by itself before the target
        self.assertEqual(self.rj()["verdict"], "ENDED_EARLY")

    def test_reused_tag_sets_the_earlier_run_aside(self):
        self.go([], FakeProc(124))
        open(os.path.join(self.plan_.run_dir, "frames", "old_frame.txt"), "w").write("from run 1")
        self.go([], FakeProc(124))
        self.assertEqual(os.listdir(os.path.join(self.plan_.run_dir, "frames")), [])        # nothing of run 1 is read as run 2
        prev = [n for n in os.listdir(os.path.dirname(self.plan_.run_dir)) if n.startswith("t1.prev-")]
        self.assertEqual(len(prev), 1)
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(self.plan_.run_dir), prev[0], "frames", "old_frame.txt")))

    def test_runner_failure_stops_the_child_and_closes_run_json(self):
        pr = FakeProc(0, boom=RuntimeError("watcher broke"))
        with self.assertRaises(RuntimeError):
            self.go([], pr)
        self.assertTrue(pr.terminated); self.assertEqual(self.rj()["verdict"], "ERROR"); self.assertTrue(FakeQueue.last.released)
        pr = FakeProc(0, boom=KeyboardInterrupt())
        self.assertEqual(run.run_interruptible(lambda: self.go([], pr)), 130)
        self.assertTrue(pr.terminated); self.assertEqual(self.rj()["verdict"], "INTERRUPTED")

    def test_sigterm_is_an_interrupt(self):
        import signal
        def body():
            os.kill(os.getpid(), signal.SIGTERM); import time as t; t.sleep(5); return 0
        self.assertEqual(run.run_interruptible(body), 130)
        self.assertNotEqual(signal.getsignal(signal.SIGTERM).__class__.__name__, "function")   # handler restored

    def test_sidecar_must_match_the_bytes_and_cache_copies_need_one(self):
        import hashlib
        _, verify, _, _ = self.saved
        class PM:
            PatchError = Exception
            class MachO:
                def __init__(s, path): pass
                def build_id(s): return "0123456789abcdef" + "0" * 48
                def close(s): pass
        tid, opts = run.parse_args(["PPSA00001"]); plan = run.compose(tid, opts, dict(self.env))
        sel = dict(plan.sel, patches="a")
        good = dict(build_id="0123456789abcdef" + "0" * 48, patches=[dict(name="a")], output_sha256=hashlib.sha256(b"bin").hexdigest())
        json.dump(good, open(self.bin + ".patches.json", "w"))
        self.assertEqual(verify(None, plan.p, sel, self.env, PM)["patches_applied"], ["a"])
        open(self.bin, "w").write("relinked")                                                  # same name, other bytes, old record
        with self.assertRaises(run.Refuse) as c: verify(None, plan.p, sel, self.env, PM)
        self.assertIn("other bytes", str(c.exception))
        open(self.bin + ".patches.json", "w").write("{not json")
        with self.assertRaises(run.Refuse): verify(None, plan.p, sel, self.env, PM)
        open(self.bin + ".patches.json", "w").write('{"patches": [3]}')
        with self.assertRaises(run.Refuse): verify(None, plan.p, sel, self.env, PM)
        os.remove(self.bin + ".patches.json")
        with self.assertRaises(run.Refuse) as c: verify(None, plan.p, dict(sel, source="cache"), self.env, PM)
        self.assertIn("no .patches.json", str(c.exception))

    def test_help_anywhere(self):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["PPSA00001", "--help"], dict(self.env)), 0)


if __name__ == "__main__":
    unittest.main()
