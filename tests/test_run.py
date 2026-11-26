#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/run.py: env layering, refusals, arm parsing, queue, run.json schema. No title is run."""
import importlib.util, os, sys, tempfile, unittest, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("nps5run", os.path.join(HERE, "..", "scripts", "nps5lib", "run.py"))
run = importlib.util.module_from_spec(spec); spec.loader.exec_module(run)
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib"))

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

    # ---- host libraries (LIBS): build tree or flat dir; a missing dir is refused at launch
    def test_libs_flat_dir_is_used_when_it_holds_the_host_library(self):
        flat = os.path.join(self.d, "flat"); os.makedirs(flat); open(os.path.join(flat, "libnps5_gpu.dylib"), "w").write("x")
        self.titles('LIBS="%s"\n' % flat)
        self.assertEqual(self.plan([]).env["DYLD_LIBRARY_PATH"], flat)

    def test_libs_build_tree_layout_is_kept(self):
        tree = os.path.join(self.d, "b", "core/libs/libs/unpatched"); os.makedirs(tree); open(os.path.join(tree, "libnps5_gpu.dylib"), "w").write("x")
        self.titles('LIBS="%s"\n' % os.path.join(self.d, "b"))
        self.assertEqual(self.plan([]).env["DYLD_LIBRARY_PATH"], tree)
        self.assertEqual(run.host_lib_dir("/nowhere/at/all"), "/nowhere/at/all/core/libs/libs/unpatched")

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

    def test_fast_math_deny_list_from_profile(self):
        # v1.1.2: games/<id>/fast-math-deny.txt becomes KYTY_FAST_MATH_DENY on the kyty back end; a later layer overrides it
        self.assertNotIn("KYTY_FAST_MATH_DENY", self.plan([]).env)
        deny = os.path.join(self.d, "games", "PPSA00001", "fast-math-deny.txt"); open(deny, "w").write("# hashes\n0123456789abcdef\n")
        p = self.plan([])
        if p.env.get("NPS5_GPU") == "kyty":
            self.assertEqual(p.env["KYTY_FAST_MATH_DENY"], deny); self.assertEqual(p.layers["KYTY_FAST_MATH_DENY"], "profile")
            self.assertEqual(self.plan(["--env", "KYTY_FAST_MATH_DENY="]).env["KYTY_FAST_MATH_DENY"], "")
        else:
            self.assertNotIn("KYTY_FAST_MATH_DENY", p.env)

    def test_deny_list_problem(self):
        f = run.deny_list_problem
        good = os.path.join(self.d, "deny.txt"); open(good, "w").write("# c\n0123456789abcdef\n")
        empty = os.path.join(self.d, "empty.txt"); open(empty, "w").write("# only a comment\n")
        low = {"KYTY_GPU_ACCURACY": "low"}
        self.assertEqual(f({}), ""); self.assertEqual(f(dict(low)), "")                                 # not set: nothing to check here
        self.assertEqual(f({"KYTY_FAST_MATH_DENY": "/nope"}), "")                                       # accuracy high: the list is moot
        self.assertEqual(f(dict(low, KYTY_FAST_MATH_DENY=good)), "")
        for bad in ("/no/such/file", empty, "", self.d):
            self.assertTrue(f(dict(low, KYTY_FAST_MATH_DENY=bad)), bad)
        self.assertEqual(f(dict(low, KYTY_FAST_MATH_DENY="/no/such", KYTY_FAST_MATH_DENY_OPTIONAL="1")), "")
        self.assertTrue(f({"KYTY_GPU_ACCURACY": "normal", "KYTY_FAST_MATH_DENY": "/no/such"}))
        self.assertTrue(f({"KYTY_FAST_MATH": "2", "KYTY_FAST_MATH_DENY": "/no/such"}))
        self.assertEqual(f({"KYTY_FAST_MATH": "0", "KYTY_GPU_ACCURACY": "high", "KYTY_FAST_MATH_DENY": "/no/such"}), "")

    def test_deny_path_missing_refuses_at_plan(self):
        # the shipped composition with a deny path that does not exist (box 3) is refused, dry run included (same check_final)
        with self.assertRaises(run.Refuse) as c:
            self.plan(["--env", "KYTY_GPU_ACCURACY=low", "--env", "KYTY_FAST_MATH_DENY=/no/such/deny.txt"])
        self.assertIn("not a readable file", str(c.exception))

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

    def test_env_required(self):
        # v1.4 review M18: a title that needs a user-made file (SAROS: the Unreal cvar list) refuses loudly instead of running without it
        open(os.path.join(self.d, "games", "PPSA00001", "game.tsv"), "a").write("env_required\tNEED_LIST\tmade from your own eboot\n")
        self.assertIn("NEED_LIST must be set", self.refused([]))
        self.assertIn("made from your own eboot", self.refused(["--env", "NEED_LIST="]))
        self.assertIn("is not a file", self.refused(["--env", "NEED_LIST=/nonexistent/x.list"]))
        f = os.path.join(self.d, "x.list"); open(f, "w").close()
        self.assertEqual(self.plan(["--env", "NEED_LIST=" + f]).env["NEED_LIST"], f)
        self.titles('ENV="NEED_LIST=%s"\n' % f); self.assertEqual(self.plan([]).env["NEED_LIST"], f)
        # v14rev2: "~" and relative paths are checked too (and handed to the game as absolute paths)
        import unittest.mock
        home = unittest.mock.patch.dict(os.environ, {"HOME": self.d}); home.start(); self.addCleanup(home.stop)
        self.assertIn("is not a file", self.refused(["--env", "NEED_LIST=~/definitely/not/here.list"]))
        self.assertIn("is not a file", self.refused(["--env", "NEED_LIST=nope.list"]))
        self.assertIn("is not a file", self.refused(["--env", "NEED_LIST=" + self.d]))  # a directory is not a file
        self.titles("")
        open(os.path.join(self.d, "home.list"), "w").close()
        self.assertEqual(self.plan(["--env", "NEED_LIST=~/home.list"]).env["NEED_LIST"], os.path.join(self.d, "home.list"))
        old = os.getcwd(); os.chdir(self.d)
        try:
            self.assertEqual(self.plan(["--env", "NEED_LIST=x.list"]).env["NEED_LIST"], os.path.abspath("x.list"))
        finally:
            os.chdir(old)

    # ---- refusals (exit 78 unless noted)
    def test_refusals(self):
        self.assertIn("env_forbidden", self.refused(["--env", "BAD_WORD=1"]))
        self.refused(["--arm", "x:BAD_WORD=1"])
        self.refused(["--env", "NPS5_SELF_MEM_LIMIT_MB=18945"])
        self.refused(["--env", "NPS5_SELF_MEM_LIMIT_MB=20000"], env=dict(NPS5_SELF_MEM_LIMIT_MB="12000"))   # above this Mac's own max
        self.refused(["--env", "KYTY_EOP_TIMESTAMPS=gpu"]); self.refused(["--env", "APS5_PROFILE_GPU=1"])
        self.refused(["--secs", "200"]); self.refused(["--secs", "120"])
        # `nps5 cache prepare` runs (tag cache-*, class func) ask for a 1800 s ceiling; any other tag / class / larger value is still capped
        prep = ["--env", "KYTY_PIPELINE_LIST_PREPARE=1"]
        self.assertEqual(self.plan(["--tag", "cache-prepare", "--secs", "1800"] + prep, dict(NPS5_RUN_REASON="nps5 cache")).secs, 1800)
        self.refused(["--tag", "cache-prepare", "--secs", "1801"] + prep, env=dict(NPS5_RUN_REASON="x")); self.refused(["--tag", "mytag", "--secs", "1800"] + prep, env=dict(NPS5_RUN_REASON="x"))
        # only a prepare pass (the game never starts): a cache-* tag on a run that plays, or a prepare tag without the prepare word, stays capped
        self.refused(["--tag", "cache-prepare", "--secs", "1800"], env=dict(NPS5_RUN_REASON="x"))
        self.refused(["--tag", "cache-rec-level", "--secs", "1800"], env=dict(NPS5_RUN_REASON="x"))
        self.refused(["--tag", "cache-x", "--secs", "1800"] + prep, env=dict(NPS5_RUN_REASON="x"))
        self.refused(["--tag", "cache-prepare", "--class", "perf", "--secs", "1800"] + prep, env=dict(NPS5_RUN_REASON="x"))
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
        for bad in ("A:lib=/x/kyty_gpu.dylib", "A:libs=/x/kyty_gpu.dylib"):      # lib=/libs= is the AnyPS5 folder, not the Kyty dylib (kyty=)
            with self.assertRaises(run.Refuse, msg=bad): run.parse_arm(bad)
        self.assertEqual(run.parse_arm("A:kyty=/x/kyty_gpu.dylib")["pieces"], {"kyty": "/x/kyty_gpu.dylib"})

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


class CacheKinds(unittest.TestCase):
    """uewarmC: `cache kind=journal` gives a stable per-player dir and no list words; kyplst and none plans are unchanged."""
    BASE = GAME.replace("env\tLAYER=profile\n", "env\tLAYER=profile\n")

    def mk(self, row):
        d = tempfile.mkdtemp()
        gd = os.path.join(d, "games", "PPSA00001"); os.makedirs(os.path.join(gd, "pad"))
        open(os.path.join(gd, "game.tsv"), "w").write(self.BASE + (row + "\n" if row else "")
                                                       + "env\tASTROHYBRID_CACHE_TITLE_ID=PPSA00001\tgpu=kyty\twhy\n")
        open(os.path.join(gd, "scenes.tsv"), "w").write(SCENES)
        open(os.path.join(gd, "pad", "pad.vpad"), "w").write("0:ly=1\n")
        os.makedirs(os.path.join(d, "titles")); os.makedirs(os.path.join(d, "app0"))
        open(os.path.join(d, "titles", "PPSA00001.env"), "w").write("APP0=%s\nBIN=/b/native\n" % os.path.join(d, "app0"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        env = dict(NPS5_GAMES_DIR=os.path.join(d, "games"), NPS5_SCRATCH=os.path.join(d, "s"), NPS5_PUBLIC_DIR=d, NPS5_SELF_MEM_LIMIT_MB="18944",
                   NPS5_KK_ICD="/kk/icd.json", NPS5_ANYPS5_BUILD="/any/build-arm64", NPS5_KYTY_DIR="/kyty", NPS5_BREW_PREFIX="/brew",
                   NPS5_HW_CACHE_KEY="g16-25G83", NPS5_STACK_KYTY="aaaa111", NPS5_STACK_KK="bbbb222", NPS5_STACK_RECOMP="cccc333")
        return d, env

    def plan(self, row, extra=None, **kw):
        d, env = self.mk(row); env.update(extra or {})
        tid, opts = run.parse_args(["PPSA00001"] + kw.get("argv", []))
        return d, env, run.compose(tid, opts, env)

    CACHE_KEYS = ("KYTY_PROGRAM_CACHE_PATH", "KYTY_PIPELINE_JOURNAL_PATH", "KYTY_PIPELINE_KNOWN_PATH", "KYTY_PIPELINE_CACHE_PATH",
                  "KYTY_SHADER_PRECOMPILE_PATH", "KK_PIPELINE_ARCHIVE_DIR", "KK_METAL_CACHE_ID")
    LIST_KEYS = ("KYTY_PIPELINE_REPLAY_ALL", "KYTY_HELPER_PREWARM", "KK_LIBCACHE_DIR")

    def test_journal_plan_has_the_cache_words_under_the_player_dir(self):
        d, env, p = self.plan("cache\tkind=journal")
        pdir = os.path.join(env["NPS5_SCRATCH"], "cache", "PPSA00001", "kyty-player")
        for k in self.CACHE_KEYS:
            self.assertIn(k, p.env, k); self.assertEqual(p.layers[k], "cache")
        for k in self.CACHE_KEYS[:-1]:
            self.assertTrue(p.env[k].startswith(pdir + "/") or p.env[k] == pdir + "/kk-archive" or p.env[k].startswith(pdir), (k, p.env[k]))
        self.assertEqual(p.env["KK_METAL_CACHE_ID"], "com.nativeps5.kyty.PPSA00001.g16-25G83")
        self.assertEqual(p.env["KYTY_PIPELINE_LIST"], "0")
        self.assertEqual(p.env["ASTROHYBRID_CACHE_TITLE_ID"], "PPSA00001")
        for k in self.LIST_KEYS:
            self.assertNotIn(k, p.env, k)
        self.assertTrue(os.path.isdir(pdir))
        self.assertTrue([w for w in p.warnings if w.startswith("first session")])
        # the second plan: the dir exists and holds files -> no first-session note
        open(os.path.join(pdir, "x"), "w").close()
        tid, opts = run.parse_args(["PPSA00001"]); p2 = run.compose(tid, opts, env)
        self.assertFalse([w for w in p2.warnings if w.startswith("first session")]); self.assertEqual(p2.env["KYTY_PROGRAM_CACHE_PATH"], p.env["KYTY_PROGRAM_CACHE_PATH"])

    def test_journal_plan_with_the_cache_off_has_none_of_it(self):
        d, env, p = self.plan("cache\tkind=journal", dict(NPS5_RUN_SHADER_CACHE="0"))
        for k in self.CACHE_KEYS + self.LIST_KEYS + ("KYTY_PIPELINE_LIST",):
            self.assertNotIn(k, p.env, k)

    def test_kyplst_without_a_list_and_none_plans_are_unchanged(self):
        # kyplst: no pipelines.kyplst in the fixture -> no cache words at all (as before); none: nothing
        for row in ("cache\tkind=kyplst", "cache\tkind=none", ""):
            d, env, p = self.plan(row)
            self.assertEqual([k for k, l in p.layers.items() if l == "cache"], [], row)
            self.assertNotIn("KYTY_PIPELINE_LIST", p.env, row)
            self.assertFalse(os.path.exists(os.path.join(env["NPS5_SCRATCH"], "cache")), row)

    def test_kyplst_plan_matches_the_formula_of_the_list_path(self):
        # a kyplst fixture with a list: the cache layer is exactly cache_env (+ libcache only when the dir has it) + launch_words, as before
        d, env = self.mk("cache\tkind=kyplst")
        open(os.path.join(d, "games", "PPSA00001", "pipelines.kyplst"), "wb").write(b"list")
        tid, opts = run.parse_args(["PPSA00001"]); p = run.compose(tid, opts, env)
        import cache as cm
        cx = cm.context("PPSA00001", env)
        want = dict(cm.cache_env("PPSA00001", cx.dir, cx.metal, with_libcache=False, environ=env)); want.update(dict(cm.launch_words(cx, os.path.join(d, "app0"))))
        got = {k: v for k, v in p.env.items() if p.layers.get(k) == "cache"}
        self.assertEqual(got, want)
        self.assertTrue(cx.dir.endswith("kyty-" + cx.key)); self.assertNotIn("player", cx.dir)
        self.assertEqual(got["KYTY_PIPELINE_LIST"], os.path.join(d, "games", "PPSA00001", "pipelines.kyplst"))      # the list path, never the journal "0"

    UE5ROWS = "cache\tkind=kyplst\tdir=player\tcode=ue5\tapp_ver=01.005.002\tlimit_mb=448\nue5\tdecoder=0x926510\tfp=1f82e44a65371db3\tabi=oodlelz14"
    UE5_KEYS = ("KYTY_PIPELINE_LIST_CODE_STORE", "KYTY_UE5_DECODER", "KYTY_UE5_DECODER_FP", "KYTY_UE5_DECODER_ABI", "KYTY_LAUNCH_WAIT_BEFORE_ENTRY")

    def test_ue5_plan_has_the_code_words_and_no_eboot_reader(self):
        d, env = self.mk(self.UE5ROWS)
        open(os.path.join(d, "games", "PPSA00001", "pipelines.kyplst"), "wb").write(b"list")
        tid, opts = run.parse_args(["PPSA00001"]); p = run.compose(tid, opts, env)
        pdir = os.path.join(env["NPS5_SCRATCH"], "cache", "PPSA00001", "kyty-player")
        self.assertEqual(p.env["KYTY_PIPELINE_LIST_CODE_STORE"], pdir + "/PPSA00001.codestore")
        self.assertEqual((p.env["KYTY_UE5_DECODER"], p.env["KYTY_UE5_DECODER_FP"], p.env["KYTY_UE5_DECODER_ABI"]), ("0x926510", "1f82e44a65371db3", "oodlelz14"))
        for k in self.UE5_KEYS:
            self.assertEqual(p.layers[k], "cache", k)
        for k in ("KYTY_PIPELINE_LIST", "KYTY_PIPELINE_LIST_APP_VER", "KYTY_PIPELINE_REPLAY_ALL", "KYTY_HELPER_PREWARM"):
            self.assertIn(k, p.env, k)
        self.assertEqual(p.env["KYTY_PIPELINE_LIST_APP_VER"], "01.005.002")
        self.assertNotIn("KYTY_PIPELINE_LIST_EXE", p.env)
        self.assertEqual(p.env["KYTY_LAUNCH_WAIT_BEFORE_ENTRY"], "1")
        self.assertTrue(p.env["KYTY_PROGRAM_CACHE_PATH"].startswith(pdir + "/"))           # the player dir = the uewarmC dir
        self.assertFalse([w for w in p.warnings if "not prepared" in w])                   # nothing to prepare ahead
        # with the cache off none of it is set
        d2, env2 = self.mk(self.UE5ROWS); env2["NPS5_RUN_SHADER_CACHE"] = "0"
        p2 = run.compose(*run.parse_args(["PPSA00001"]), env2)
        for k in self.UE5_KEYS + ("KYTY_PIPELINE_LIST",):
            self.assertNotIn(k, p2.env)

    def test_ue5_without_a_list_and_plain_kyplst_have_no_ue5_words(self):
        # no list yet: the journal cache of v1.4 (list-less words, KYTY_PIPELINE_LIST=0), none of the ue5 words; a plain kyplst title never gets one
        d, env = self.mk(self.UE5ROWS)
        p = run.compose(*run.parse_args(["PPSA00001"]), env)
        self.assertEqual(p.env["KYTY_PIPELINE_LIST"], "0")
        for k in self.UE5_KEYS + ("KYTY_PIPELINE_REPLAY_ALL", "KYTY_PIPELINE_LIST_EXE"):
            self.assertNotIn(k, p.env, k)
        d, env = self.mk("cache\tkind=kyplst")
        open(os.path.join(d, "games", "PPSA00001", "pipelines.kyplst"), "wb").write(b"list")
        p = run.compose(*run.parse_args(["PPSA00001"]), env)
        self.assertIn("KYTY_PIPELINE_LIST_EXE", p.env)
        for k in self.UE5_KEYS:
            self.assertNotIn(k, p.env)

    def test_astro_profile_cache_plan_is_unchanged(self):
        # the shipped ASTRO profile (kind=kyplst with its list): same dir as the stack-key formula, same words, no journal word
        root = os.path.abspath(os.path.join(HERE, ".."))
        sc = tempfile.mkdtemp(); self.addCleanup(__import__("shutil").rmtree, sc, True)
        app0 = os.path.join(sc, "app0"); os.makedirs(app0)
        env = dict(NPS5_GAMES_DIR=os.path.join(root, "games"), NPS5_PUBLIC_DIR=sc, NPS5_SCRATCH=sc, NPS5_SELF_MEM_LIMIT_MB="18944", NPS5_KK_ICD="/kk/icd.json",
                   NPS5_ANYPS5_BUILD="/any/build-arm64", NPS5_KYTY_DIR="/kyty", NPS5_BREW_PREFIX="/brew", NPS5_HW_CACHE_KEY="g16-25G83",
                   NPS5_STACK_KYTY="aaaa111", NPS5_STACK_KK="bbbb222", NPS5_STACK_RECOMP="cccc333", NPS5_RUN_REASON="cache plan test")
        os.makedirs(os.path.join(sc, "titles")); open(os.path.join(sc, "titles", "PPSA21564.env"), "w").write("APP0=%s\nBIN=/b/native\n" % app0)
        out = run.shader_cache_words("PPSA21564", env, app0, adopt=False)   # a dry plan: the stable dir is only named, not created
        import cache as cm
        cx = cm.context("PPSA21564", env)
        self.assertEqual(cx.kind, "kyplst"); self.assertTrue(cx.stable_dir)   # v1.4.2 cachekey: the profile row NPS5_CACHE_STABLE_DIR=1 names the dir by hardware class
        self.assertEqual(cx.dir, os.path.join(sc, "cache", "PPSA21564", cm.stable_name("kyty", cx.hw)))
        got = [(k, v) for k, v, _ in out if k]
        want = cm.cache_env("PPSA21564", cx.dir, cx.metal, with_libcache=False, environ=env) + cm.launch_words(cx, app0)
        self.assertEqual(got, want)
        self.assertNotIn(("KYTY_PIPELINE_LIST", "0"), got)
        self.assertFalse(os.path.exists(cx.dir))                                  # a kyplst plan creates no dir


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


class RehearseGuards(unittest.TestCase):
    """Release-rehearsal guards (2026-11-25): an explicit cache= arm warns loudly; guest_image.bin is found where the runtime looks."""
    titles = T.titles
    setUp = T.setUp
    plan = T.plan

    def test_cache_arm_warns_loudly(self):
        self.titles("APP0=/dump\nBIN=/b/native\n")
        p = self.plan(["--arm", "x:cache=cache-x"])
        w = [x for x in p.warnings if x.startswith("WARNING") and "cache=cache-x" in x]
        self.assertEqual(len(w), 1)
        self.assertIn("SKIPPED", w[0]); self.assertIn("KYTY_PIPELINE_LIST", w[0]); self.assertIn("not the player path", w[0])
        self.assertEqual(p.layers["NPS5_CACHE_DIR"], "arm")

    def test_no_cache_arm_no_warning(self):
        self.titles("APP0=/dump\nBIN=/b/native\n")
        self.assertFalse([x for x in self.plan(["--arm", "x:LAYER=1"]).warnings if "EXPLICIT cache" in x])

    def _app(self, where):
        root = tempfile.mkdtemp()
        exe = os.path.join(root, "T.app", "Contents", "MacOS"); os.makedirs(exe)
        open(os.path.join(exe, "nps5-title"), "w").write("b")
        if where:
            os.makedirs(os.path.join(root, "T.app", "Contents", where), exist_ok=True)
            open(os.path.join(root, "T.app", "Contents", where, "guest_image.bin"), "w").write("i")
        return os.path.join(exe, "nps5-title")

    def test_app_bundle_layout_is_found(self):
        for where in ("Resources", "SharedSupport"):      # `nps5 build` assembles Contents/Resources/guest_image.bin (pkg/pack/bundle.go)
            b = self._app(where)
            self.assertEqual(run.guest_image_plan(dict(path=b))[:2], (True, None), where)

    def test_bin_beside_image_is_found(self):
        d = tempfile.mkdtemp(); open(os.path.join(d, "native"), "w").write("b"); open(os.path.join(d, "guest_image.bin"), "w").write("i")
        self.assertEqual(run.guest_image_plan(dict(path=os.path.join(d, "native")))[:2], (True, None))

    def test_symlinked_bin_resolves_like_the_runtime(self):
        b = self._app("Resources"); d = tempfile.mkdtemp(); ln = os.path.join(d, "title"); os.symlink(b, ln)
        self.assertTrue(run.guest_image_plan(dict(path=ln))[0])

    def test_bin_beside_out_dir_is_linked_not_found(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "out")); open(os.path.join(d, "native"), "w").write("b")
        open(os.path.join(d, "out", "guest_image.bin"), "w").write("i")
        found, src, note = run.guest_image_plan(dict(path=os.path.join(d, "native")))
        self.assertFalse(found); self.assertEqual(os.path.realpath(src), os.path.realpath(os.path.join(d, "out", "guest_image.bin"))); self.assertIn("linked", note)

    def test_patched_copy_takes_the_image_of_its_source(self):
        s = tempfile.mkdtemp(); c = tempfile.mkdtemp()
        open(os.path.join(s, "native_one"), "w").write("b"); open(os.path.join(s, "guest_image.bin"), "w").write("i"); open(os.path.join(c, "one+dr"), "w").write("b")
        found, src, note = run.guest_image_plan(dict(path=os.path.join(c, "one+dr"), src=os.path.join(s, "native_one")))
        self.assertFalse(found); self.assertEqual(os.path.realpath(src), os.path.realpath(os.path.join(s, "guest_image.bin")))

    def test_no_image_anywhere_is_a_loud_warning(self):
        d = tempfile.mkdtemp(); open(os.path.join(d, "native"), "w").write("b")
        found, src, note = run.guest_image_plan(dict(path=os.path.join(d, "native")))
        self.assertFalse(found); self.assertIsNone(src); self.assertTrue(note.startswith("WARNING")); self.assertIn("Failed to open guest image", note)

    def test_missing_binary_is_not_this_checks_business(self):
        self.assertEqual(run.guest_image_plan(dict(path="/nonexistent/native"))[:2], (True, None))


class LoadedCheck(unittest.TestCase):
    class P: gpu = "kyty"; env = {"NPS5_KYTY_GPU_LIB": "/rc/kyty_gpu.dylib", "DYLD_LIBRARY_PATH": "/new/flat"}
    def test_rpath_fallback_is_reported(self):
        txt = "n 1 u REG 1,1 1 1 /rc/kyty_gpu.dylib\nn 1 u REG 1,1 1 2 /old/build-arm64/core/libs/libs/unpatched/libnps5_gpu.dylib\n"
        bad = run.verify_loaded(self.P, lambda: txt, sleep=lambda s: None, tries=2)
        self.assertEqual(len(bad), 1); self.assertIn("libnps5_gpu.dylib loaded from", bad[0])
    def test_planned_libraries_pass(self):
        txt = "n 1 u REG 1,1 1 1 /rc/kyty_gpu.dylib\nn 1 u REG 1,1 1 2 /new/flat/libnps5_gpu.dylib\n"
        self.assertEqual(run.verify_loaded(self.P, lambda: txt, sleep=lambda s: None, tries=2), [])
    def test_never_mapped_is_not_a_mismatch(self):
        self.assertEqual(run.verify_loaded(self.P, lambda: "", sleep=lambda s: None, tries=2), [])


class MissingHostLibs(unittest.TestCase):
    def test_missing_dyld_dir_is_refused(self):
        T.setUp(self); self.app0 = os.path.join(self.d, "app0"); os.makedirs(self.app0)
        T.titles(self, "APP0=%s\n" % self.app0)
        tid, opts = run.parse_args(["PPSA00001"])
        plan = run.compose(tid, opts, dict(self.env))
        self.assertFalse(os.path.isdir(plan.env["DYLD_LIBRARY_PATH"]))
        saved = run.preflight; run.preflight = lambda plan: ([], [])
        try:
            self.assertEqual(run.execute(plan, opts, plan.environ), 2)
        finally:
            run.preflight = saved


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
        self.env["NPS5_WATCH"] = "0"; self.env["NPS5_ALLOW_MISSING_HOST_LIBS"] = "1"   # fake host path

    def tearDown(self):
        run.preflight, run.verify_binary, run.components, run.shutil.which = self.saved

    def go(self, argv, proc):
        tid, opts = run.parse_args(["PPSA00001", "--tag", "t1"] + argv)
        plan = run.compose(tid, opts, dict(self.env)); self.plan_ = plan
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run.execute(plan, opts, plan.environ, queue_factory=FakeQueue, popen=lambda *a, **k: proc, hasher=lambda p: "abcd")

    def rj(self): return json.load(open(os.path.join(self.plan_.run_dir, "run.json")))

    def test_guest_image_beside_out_is_linked_into_frames(self):
        os.makedirs(os.path.join(self.d, "out")); open(os.path.join(self.d, "out", "guest_image.bin"), "w").write("i")
        self.assertEqual(self.go([], FakeProc(124)), 0)
        ln = os.path.join(self.plan_.run_dir, "frames", "guest_image.bin")
        self.assertTrue(os.path.islink(ln)); self.assertTrue(os.path.isfile(ln))

    def test_patched_copy_in_native_cache_gets_the_image_of_the_build_dir(self):
        # BIN_DIR route (no explicit BIN): the run uses a patched COPY in native-cache; the image stays next to the build's native_<lineage>
        bd = os.path.join(self.d, "build"); os.makedirs(bd)
        open(os.path.join(bd, "native_lin1"), "w").write("bin"); open(os.path.join(bd, "guest_image.bin"), "w").write("img")
        self.titles("APP0=%s\nBIN_DIR=%s\n" % (self.app0, bd))
        def fake_build(sel, p, environ, runner=None):
            os.makedirs(os.path.dirname(sel["path"]), exist_ok=True); open(sel["path"], "w").write("patched")
        saved = run.build_patched; run.build_patched = fake_build
        try:
            rc = self.go([], FakeProc(124))
        finally:
            run.build_patched = saved
        self.assertEqual(rc, 0)
        self.assertEqual(self.plan_.sel["source"], "cache")
        self.assertIn("native-cache", self.plan_.sel["path"])
        ln = os.path.join(self.plan_.run_dir, "frames", "guest_image.bin")
        self.assertTrue(os.path.isfile(ln))
        self.assertEqual(os.path.realpath(ln), os.path.realpath(os.path.join(bd, "guest_image.bin")))

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


class ShippedGames(unittest.TestCase):
    """Every shipped title's profile + preset must compose through check_final (v1.4 review B1: SAROS and Cronos were refused with
    rc 78 because the preset's KYTY_EOP_TIMESTAMPS=gpu met their env_forbidden row with no override). No title is run."""

    def test_every_shipped_title_composes(self):
        root = os.path.abspath(os.path.join(HERE, ".."))
        games = os.path.join(root, "games")
        ids = sorted(t for t in os.listdir(games) if os.path.isfile(os.path.join(games, t, "game.tsv")))
        self.assertTrue(ids)
        env = dict(NPS5_GAMES_DIR=games, NPS5_PUBLIC_DIR=root, NPS5_SCRATCH=tempfile.mkdtemp(), NPS5_SELF_MEM_LIMIT_MB="18944", NPS5_RUN_REASON="shipped-title compose test",
                   NPS5_KK_ICD="/kk/icd.json", NPS5_ANYPS5_BUILD="/any/build-arm64", NPS5_KYTY_DIR="/kyty", NPS5_BREW_PREFIX="/brew")
        lst = os.path.join(env["NPS5_SCRATCH"], "cvars.list"); open(lst, "w").close()    # stands for the user's own NPS5_UECVAR_LIST
        for t in ids:
            tid, opts = run.parse_args([t, "--bin", "/x/native", "--env", "NPS5_UECVAR_LIST=" + lst])
            try:
                plan = run.compose(tid, opts, dict(env))
            except run.Refuse as e:
                self.fail("%s is refused by check_final (rc %s): %s" % (t, e.code, e))
            self.assertNotEqual(plan.env.get("KYTY_EOP_TIMESTAMPS"), "gpu", t)


if __name__ == "__main__":
    unittest.main()
