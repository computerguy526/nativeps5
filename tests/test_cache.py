#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/cache.py: stack key / Metal id derivation, status on fresh / partial / prepared / stale dirs,
list merge and verify-list through a fake pipeline-list tool, prepare / record / check as dry runs and with a fake runner. No title is run."""
import contextlib, time, io, os, shutil, sys, tempfile, unittest, warnings
warnings.simplefilter("ignore", ResourceWarning)
from contextlib import redirect_stdout, redirect_stderr
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib"))
import cache  # noqa: E402
import run as runmod  # noqa: E402

GAME = "title_id\tPPSA00001\nname\tFixture\nbuild\t01.001\teboot=0123456789abcdef\npackage\t01.001\ngpu\tkyty\ncanonical_lineage\tlin1\ncache\tkind=kyplst\tlimit_mb=448\tapp_ver=01.001.000\n"
SCENES = "boot\t-\t-\t-\t60\tnew\tboot\nlvl1\t-x\t-\t-\t60\tnew\tlevel\nlevel:*\t-lvl {name}\t-\t-\t60\tnew\ttemplate\n"
COMMITS = dict(kyty="aaaa111", kk="bbbb222", ps5recomp="cccc333")
# a fake kyty_pipeline_list: info prints counts from the file's first line, merge concatenates, export copies, check / verify succeed
TOOL = """#!/bin/bash
case $1 in
 info) echo "  title X, version 1, 2 things"; echo "  shaders $(head -1 $2 | cut -d' ' -f1); permutations $(head -1 $2 | cut -d' ' -f2), pipelines $(head -1 $2 | cut -d' ' -f3)"; echo "  permutations $(head -1 $2 | cut -d' ' -f2), pipelines $(head -1 $2 | cut -d' ' -f3)";;
 export) while [ $# -gt 0 ]; do [ "$1" = --out ] && out=$2; shift; done; echo "3 4 5" > $out;;
 merge) shift; [ "$1" = --out ] && out=$2 && shift 2; cat "$@" | sort -u | tail -1 > $out;;
 check) echo "check: ok";;
 verify) echo "verify: no shader code in the list";;
esac
"""


def make(tmp, list_bytes=b"1 2 3\n"):
    gd = os.path.join(tmp, "games", "PPSA00001"); os.makedirs(gd)
    open(os.path.join(gd, "game.tsv"), "w").write(GAME); open(os.path.join(gd, "scenes.tsv"), "w").write(SCENES)
    if list_bytes is not None: open(os.path.join(gd, "pipelines.kyplst"), "wb").write(list_bytes)
    tool = os.path.join(tmp, "tool.sh"); open(tool, "w").write(TOOL); os.chmod(tool, 0o755)
    os.makedirs(os.path.join(tmp, "app0"))
    os.makedirs(os.path.join(tmp, "titles"))
    open(os.path.join(tmp, "titles", "PPSA00001.env"), "w").write("APP0=%s\n" % os.path.join(tmp, "app0"))
    return dict(NPS5_GAMES_DIR=os.path.join(tmp, "games"), NPS5_GAMES_FIRST="1", NPS5_SCRATCH=os.path.join(tmp, "scratch"), NPS5_SELF_MEM_LIMIT_MB="18944",
                NPS5_PUBLIC_DIR=tmp, NPS5_KK_ICD="x", NPS5_HW_CACHE_KEY="g16-25G83", NPS5_KYPLST_TOOL=tool,
                NPS5_STACK_KYTY=COMMITS["kyty"], NPS5_STACK_KK=COMMITS["kk"], NPS5_STACK_RECOMP=COMMITS["ps5recomp"], PATH=os.environ["PATH"])


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.env = make(self.tmp)
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, **kw):
        return cache.context("PPSA00001", dict(self.env), **kw)

    def run_main(self, argv, runner=None, ctx=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cache.main(argv, dict(self.env), runner or cache.default_runner, ctx)
        return rc, out.getvalue(), err.getvalue()

    # ---- identity
    def test_replay_limit_follows_the_driver_cache_cap(self):
        # v14prep M12: the replay limit is never above 7/8 of the cap that is passed to Kyty with it, in the prepare and the launch words
        self.assertEqual(cache.replay_limit_mb({"limit_mb": "896"}), "896")
        self.assertEqual(cache.replay_limit_mb({"limit_mb": "4000"}), str(cache.DRIVER_CACHE_CAP_MB // 8 * 7))
        self.assertEqual(cache.replay_limit_mb({"limit_mb": "x"}), "896")
        self.assertEqual(cache.replay_limit_mb({}), "896")
        cx = self.ctx()
        for words in (cache.prepare_words(cx, "/app0"), cache.launch_words(cx, "/app0")):
            w = dict(words)
            self.assertEqual(w["KYTY_DRIVER_CACHE_MAX_MB"], str(cache.DRIVER_CACHE_CAP_MB))
            self.assertLessEqual(int(w["KYTY_PIPELINE_LIST_CACHE_LIMIT_MB"]), cache.DRIVER_CACHE_CAP_MB // 8 * 7)

    def test_user_set_larger_cap_is_kept(self):
        # round 2: the words never lower a user's own KYTY_DRIVER_CACHE_MAX_MB; the replay limit follows the cap that is passed
        self.assertEqual(cache.driver_cap_mb({}), 1024)
        self.assertEqual(cache.driver_cap_mb({"KYTY_DRIVER_CACHE_MAX_MB": "2048"}), 2048)
        self.assertEqual(cache.driver_cap_mb({"KYTY_DRIVER_CACHE_MAX_MB": "512"}), 1024)
        self.assertEqual(cache.driver_cap_mb({"KYTY_DRIVER_CACHE_MAX_MB": "99999"}), 3584)
        self.assertEqual(cache.driver_cap_mb({"KYTY_DRIVER_CACHE_MAX_MB": "x"}), 1024)
        self.assertEqual(cache.replay_limit_mb({"limit_mb": "1500"}, 2048), "1500")
        self.assertEqual(cache.replay_limit_mb({"limit_mb": "1500"}, 1024), "896")
        env = dict(self.env, KYTY_DRIVER_CACHE_MAX_MB="2048")
        cx = cache.context("PPSA00001", env)
        self.assertEqual(dict(cache.prepare_words(cx, "/app0"))["KYTY_DRIVER_CACHE_MAX_MB"], "2048")

    def test_progress_line(self):
        log = "x\nShader precompile: 89/893 (9%): 89 compiled\n[12:00:01] Pipeline replay: 512/1544 (33%): 500 built, 12 known, 0 failed, 21.0 s\nother\n"
        self.assertEqual(cache.progress_line(log), "Pipeline replay: 512/1544 (33%): 500 built, 12 known, 0 failed, 21.0 s")
        self.assertEqual(cache.progress_line("nothing here\n"), "")
        self.assertLessEqual(len(cache.progress_line("Prepare shaders: " + "x" * 400)), 110)

    def test_progress_tail_prints_new_lines_once(self):
        import io
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "run.out"), "w") as f: f.write("Pipeline replay: 10/100 (10%): 10 built\n")
        buf = io.StringIO(); t = cache.ProgressTail(d, "pass 1 of 2", every=0.05, out=buf); t.start()
        time.sleep(0.3)
        with open(os.path.join(d, "run.out"), "a") as f: f.write("Pipeline replay: 100/100 (100%): 100 built\n")
        time.sleep(0.3); t.done(); t.join(2)
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines, ["prepare: pass 1 of 2: Pipeline replay: 10/100 (10%): 10 built", "prepare: pass 1 of 2: Pipeline replay: 100/100 (100%): 100 built"])
        self.assertEqual(cache.ProgressTail(os.path.join(d, "none"), "x").poll(), "")

    def test_key_changes_with_every_input(self):
        base = cache.stack_key("l", "k", "kk", "r", "hw")
        self.assertEqual(base, cache.stack_key("l", "k", "kk", "r", "hw"))
        self.assertEqual(len(base), 8)
        for args in (("L", "k", "kk", "r", "hw"), ("l", "K", "kk", "r", "hw"), ("l", "k", "KK", "r", "hw"), ("l", "k", "kk", "R", "hw"), ("l", "k", "kk", "r", "HW")):
            self.assertNotEqual(base, cache.stack_key(*args), args)

    def test_key_follows_the_list_bytes_and_the_hw_key(self):
        a = self.ctx()
        open(a.list_path, "ab").write(b"more\n")
        b = self.ctx()
        self.assertNotEqual(a.key, b.key)
        self.env["NPS5_HW_CACHE_KEY"] = "g17-26A1"
        self.assertNotEqual(b.key, self.ctx().key)
        c = self.ctx()
        self.assertEqual(c.metal, "com.nativeps5.kyty.PPSA00001." + c.hw)        # per GPU family / OS build, not the stack key
        self.assertNotIn(c.key, c.metal)
        self.assertTrue(c.dir.endswith("cache/PPSA00001/kyty-" + c.key))

    def test_no_kyplst_row_is_refused(self):
        open(os.path.join(self.tmp, "games", "PPSA00001", "game.tsv"), "a").write("")
        s = open(os.path.join(self.tmp, "games", "PPSA00001", "game.tsv")).read().replace("cache\tkind=kyplst", "cache\tkind=none")
        open(os.path.join(self.tmp, "games", "PPSA00001", "game.tsv"), "w").write(s)
        rc, _, err = self.run_main(["status", "PPSA00001"])
        self.assertEqual(rc, 2); self.assertIn("no `cache kind=kyplst`", err)

    def set_cache_row(self, row):
        gp = os.path.join(self.tmp, "games", "PPSA00001", "game.tsv")
        txt = open(gp).read().replace("cache\tkind=kyplst\tlimit_mb=448\tapp_ver=01.001.000", row)
        open(gp, "w").write(txt)

    def test_journal_kind_gets_one_stable_player_dir(self):
        # uewarmC: `cache kind=journal` = no list, one dir per player, the same for any stack commit
        self.set_cache_row("cache\tkind=journal")
        both = ("kyplst", "journal")
        a = cache.context("PPSA00001", dict(self.env), kinds=both)
        b = cache.context("PPSA00001", dict(self.env), commits=dict(kyty="zzzz999", kk="yyyy888", ps5recomp="xxxx777"), kinds=both)
        self.assertEqual(a.kind, "journal")
        self.assertEqual(a.dir, os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "kyty-player")); self.assertEqual(a.dir, b.dir)
        self.assertEqual(cache.journal_words(a), [("KYTY_PIPELINE_LIST", "0")])

    def test_journal_kind_is_refused_by_the_list_commands(self):
        self.set_cache_row("cache\tkind=journal")
        with self.assertRaises(cache.CacheError) as c:
            self.ctx()
        self.assertEqual(c.exception.code, 2); self.assertIn("no `cache kind=kyplst`", str(c.exception))
        rc, _, err = self.run_main(["status", "PPSA00001"])
        self.assertEqual(rc, 2); self.assertIn("no `cache kind=kyplst`", err)

    def test_kyplst_dir_player_word_and_plain_kyplst_unchanged(self):
        plain = self.ctx()
        self.assertTrue(plain.dir.endswith("cache/PPSA00001/kyty-" + plain.key)); self.assertEqual(plain.kind, "kyplst")
        self.set_cache_row("cache\tkind=kyplst\tdir=player\tlimit_mb=448")
        pl = self.ctx()
        self.assertEqual(pl.dir, os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "kyty-player"))
        self.assertEqual(pl.key, plain.key)                                   # the key itself is still computed (list identity)

    # ---- T6: stable cache dir (cachekey)
    def stable_env(self, **kw):
        e = dict(self.env); e["NPS5_CACHE_STABLE_DIR"] = "1"; e.update(kw); return e

    def sctx(self, env=None, **kw):
        return cache.context("PPSA00001", env or self.stable_env(), **kw)

    def mkdir_old(self, name, files=("PPSA00001.programs.bin",), hw=None, age=0, tid="PPSA00001", gpu_root=None):
        d = os.path.join(self.env["NPS5_SCRATCH"], "cache", tid, name); os.makedirs(os.path.join(d, "kk-archive", "stamp1"))
        for f in files:
            open(os.path.join(d, f), "w").write("x" * 10)
        open(os.path.join(d, "kk-archive", "stamp1", "seg-0.mtl4a"), "w").write("seg")
        if hw: open(os.path.join(d, "prepared.tsv"), "w").write("key=aaaa\nrc=0\nhw=%s\nkyty=oldkyty\n" % hw)
        t = time.time() - age
        for dp, dn, fn in os.walk(d):
            for n in dn + fn: os.utime(os.path.join(dp, n), (t, t))
        os.utime(d, (t, t))
        return d

    def test_t6_stable_dir_name_ignores_commits_and_follows_hw(self):
        a = self.sctx()
        b = self.sctx(commits=dict(kyty="zzzz999", kk="yyyy888", ps5recomp="xxxx777"))
        open(a.list_path, "ab").write(b"new list\n")
        c = self.sctx()
        self.assertTrue(a.stable_dir)
        self.assertEqual(a.dir, b.dir); self.assertEqual(a.dir, c.dir)
        self.assertEqual(a.dir, os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", cache.stable_name("kyty", "g16-25G83")))
        self.assertTrue(os.path.basename(a.dir).startswith("kyty-stable-")); self.assertTrue(cache.STABLE_RE.match(os.path.basename(a.dir)))
        e = self.stable_env(NPS5_HW_CACHE_KEY="g17-26A1")
        d = self.sctx(env=e)
        self.assertNotEqual(a.dir, d.dir)
        self.assertNotEqual(a.key, b.key)                             # the real stack key is still computed (prepared.tsv, STACK.txt)
        self.assertEqual(a.metal, b.metal)

    def test_t6_gate_off_is_the_old_key_bit_for_bit(self):
        plain = self.ctx()
        self.assertFalse(plain.stable_dir)
        self.assertTrue(plain.dir.endswith("cache/PPSA00001/kyty-" + plain.key))
        off = self.sctx(env=self.stable_env(NPS5_CACHE_STABLE_DIR="0"))
        self.assertEqual((off.dir, off.key), (plain.dir, plain.key))
        self.assertEqual(self.sctx(env=self.stable_env(NPS5_CACHE_STABLE_DIR="")).dir, plain.dir)     # unset-like = off without a profile row
        # unrelated switches that mention nothing: never the gate
        self.assertEqual(cache.context("PPSA00001", dict(self.env, NPS5_CACHE_COMPRESS="1")).dir, plain.dir)

    def test_t6_profile_env_row_turns_it_on_and_env_zero_beats_it(self):
        gp = os.path.join(self.tmp, "games", "PPSA00001", "game.tsv")
        open(gp, "a").write("env\tNPS5_CACHE_STABLE_DIR=1\tgpu=kyty\tcachekey\n")
        on = cache.context("PPSA00001", dict(self.env))
        self.assertTrue(on.stable_dir)
        off = cache.context("PPSA00001", dict(self.env, NPS5_CACHE_STABLE_DIR="0"))
        self.assertFalse(off.stable_dir); self.assertTrue(off.dir.endswith("kyty-" + off.key))

    def test_t6_player_dir_rows_keep_their_name(self):
        self.set_cache_row("cache\tkind=kyplst\tdir=player\tlimit_mb=448")
        self.assertEqual(self.sctx().dir, os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "kyty-player"))

    def test_t6_adoption_picks_newest_compatible_and_keeps_the_source(self):
        old = self.mkdir_old("kyty-11111111", hw="g16-25G83", age=5000)
        new = self.mkdir_old("kyty-22222222", hw="g16-25G83", age=100)
        other_hw = self.mkdir_old("kyty-33333333", hw="g17-26A1", age=1)          # newest, but built for another hardware key
        empty = os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "kyty-44444444"); os.makedirs(empty)
        player = self.mkdir_old("kyty-player", age=1)                              # not a stack-keyed dir
        cx = self.sctx()
        self.assertEqual([os.path.basename(d) for _, d in cache.sibling_candidates(cx)], ["kyty-22222222", "kyty-11111111"])
        before = cache.tree_manifest(new)
        msgs = []
        st, src = cache.adopt_sibling(cx, msgs.append)
        self.assertEqual((st, src), ("adopted", "kyty-22222222"))
        self.assertEqual(cache.tree_manifest(cx.dir), before)                      # verified clone
        self.assertEqual(cache.tree_manifest(new), before)                          # source untouched
        for d in (old, other_hw, empty, player): self.assertTrue(os.path.isdir(d))
        self.assertFalse(os.path.lexists(cx.dir + ".adopting"))
        # a second call never touches the existing stable dir
        open(os.path.join(cx.dir, "mine"), "w").write("1")
        self.assertEqual(cache.adopt_sibling(cx), ("have", None)); self.assertTrue(os.path.isfile(os.path.join(cx.dir, "mine")))

    # ---- osadopt 2026-11-27: OS update keeps the player's journals; Metal rows regenerate at start
    def mk_os_dir(self, hw, age=0):
        d = self.mkdir_old(cache.stable_name("kyty", hw), files=("PPSA00001.programs.bin", "PPSA00001.pipelines.journal",
                                                                  "PPSA00001.shaders.journal", "PPSA00001.bin"), age=age)
        for sub, n in (("msl", "t.msl"), ("lib", "t.metallib")):
            os.makedirs(os.path.join(d, "kk-libcache", sub), exist_ok=True); open(os.path.join(d, "kk-libcache", sub, n), "w").write("m")
        open(os.path.join(d, cache.STACK_FILE), "w").write("hw=%s\n" % hw)
        t = time.time() - age; os.utime(d, (t, t))
        return d

    def test_osadopt_os_update_clones_inputs_and_drops_os_compiled_stores(self):
        old = self.mk_os_dir("g16-24A10", age=100)
        other_fam = self.mk_os_dir("g17-26A1", age=1)                               # newer, but another GPU family
        snap, snap2 = cache.tree_manifest(old), cache.tree_manifest(other_fam)
        cx = self.sctx()                                                           # this Mac: g16-25G83
        self.assertEqual([d for _, d in cache.os_update_candidates(cx)], [old])
        msgs = []
        self.assertEqual(cache.adopt_sibling(cx, msgs.append), ("adopted", os.path.basename(old)))
        have = cache.tree_manifest(cx.dir)
        for f in ("PPSA00001.programs.bin", "PPSA00001.pipelines.journal", "PPSA00001.shaders.journal", "kk-libcache/msl/t.msl"):
            self.assertIn(f, have)
        for f in ("PPSA00001.bin", "kk-archive", "kk-libcache/lib/t.metallib"):
            self.assertNotIn(f, have)
        self.assertEqual(have.get("kk-libcache/lib"), "d")                         # empty lib/ kept: KK_LIBCACHE_DIR still passed
        self.assertEqual((cache.tree_manifest(old), cache.tree_manifest(other_fam)), (snap, snap2))   # sources untouched
        self.assertTrue(any("OS update" in m and "g16-24A10" in m for m in msgs))
        self.assertFalse(os.path.lexists(cx.dir + ".adopting"))

    def test_osadopt_same_hw_sibling_wins_and_other_family_or_unrecorded_never_adopted(self):
        self.mk_os_dir("g16-24A10", age=1)
        sib = self.mkdir_old("kyty-12121212", hw="g16-25G83", age=500)
        self.assertEqual(cache.adopt_sibling(self.sctx()), ("adopted", "kyty-12121212"))
        self.assertIn("kk-archive", cache.tree_manifest(self.sctx().dir))             # same OS build: nothing dropped
        root = os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001")
        for n in os.listdir(root): shutil.rmtree(os.path.join(root, n))
        self.mk_os_dir("g17-26A1")
        u = self.mk_os_dir("g16-24A10"); os.remove(os.path.join(u, cache.STACK_FILE))  # says nothing about its hw: not an OS-update source
        self.assertEqual(cache.os_update_candidates(self.sctx()), [])
        self.assertEqual(cache.adopt_sibling(self.sctx()), ("empty", None))

    def test_osadopt_metal_autogen_runs_only_when_rows_are_missing(self):
        cx = self.sctx(); os.makedirs(cx.dir); open(os.path.join(cx.dir, "PPSA00001.programs.bin"), "w").write("p")
        open(os.path.join(cx.p.dir, cache.metalmod.LIST_NAME), "w").write("# keys\n")
        calls = []
        def ok(c, runner, rd, label=""):
            calls.append(label); os.makedirs(os.path.join(c.dir, "metal-shaders")); open(os.path.join(c.dir, "metal-shaders", "k.metal"), "w").write("m")
            return dict(written=3)
        n = cache.metal_autogen(cx, step=ok)
        self.assertIn("3 of our", n); self.assertEqual(calls, ["start: "])
        self.assertTrue(os.path.isfile(os.path.join(cx.dir, "metal-shaders", cache.PROGRAMS_STAMP)))
        self.assertIsNone(cache.metal_autogen(cx, step=ok)); self.assertEqual(len(calls), 1)       # rows present: nothing to do
        shutil.rmtree(os.path.join(cx.dir, "metal-shaders"))
        def bad(c, runner, rd, label=""):
            os.makedirs(os.path.join(c.dir, "metal-shaders")); return dict(written=2, mismatch="emitter differs")
        n = cache.metal_autogen(cx, step=bad)
        self.assertIn("emitter differs", n); self.assertIn("regenerates", n)
        self.assertFalse(os.path.exists(os.path.join(cx.dir, "metal-shaders")))   # a mismatched set is never left to load
        def boom(*a, **k): raise RuntimeError("tool missing")
        self.assertIn("tool missing", cache.metal_autogen(cx, step=boom))
        for off in ("NPS5_METAL_AUTOGEN", "NPS5_METAL_SHADERS"):
            self.assertIsNone(cache.metal_autogen(self.sctx(env=self.stable_env(**{off: "0"})), step=ok))
        os.remove(os.path.join(cx.dir, "PPSA00001.programs.bin"))
        self.assertIsNone(cache.metal_autogen(cx, step=ok))                       # no programs.bin: nothing to generate from

    def test_osadopt_metal_autogen_replaces_a_half_written_dir_and_keeps_a_finished_one(self):
        cx = self.sctx(); os.makedirs(cx.dir); open(os.path.join(cx.dir, "PPSA00001.programs.bin"), "w").write("p")
        open(os.path.join(cx.p.dir, cache.metalmod.LIST_NAME), "w").write("# keys\n")
        out = os.path.join(cx.dir, "metal-shaders"); os.makedirs(out); open(os.path.join(out, "half.metal"), "w").write("torn")   # no programs stamp
        calls = []
        def ok(c, runner, rd, label=""):
            calls.append(1); os.makedirs(os.path.join(c.dir, "metal-shaders")); open(os.path.join(c.dir, "metal-shaders", "k.metal"), "w").write("m")
            return dict(written=1)
        self.assertIn("1 of our", cache.metal_autogen(cx, step=ok)); self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.isfile(os.path.join(out + ".partial", "half.metal")))          # kept for diagnosis, not loaded
        self.assertFalse(os.path.exists(os.path.join(out, "half.metal")))
        self.assertIsNone(cache.metal_autogen(cx, step=ok)); self.assertEqual(len(calls), 1)    # stamped = finished: left alone

    # ---- osadopt2 2026-11-27: prepared record after an OS adoption; failed-autogen marker
    def test_osadopt2_cross_os_adoption_rewrites_prepared_record_honestly(self):
        old = self.mk_os_dir("g16-24A10", age=100)
        open(os.path.join(old, "prepared.tsv"), "w").write("key=aaaa\nrc=0\nhw=g16-24A10\nkyty=k1\npipelines=12\ndate=2026-11-20\n")
        cx = self.sctx()
        self.assertEqual(cache.adopt_sibling(cx)[0], "adopted")
        kv = cache.read_kv(os.path.join(cx.dir, "prepared.tsv"))
        self.assertEqual((kv["hw"], kv["os_stores"], kv["os_from"], kv["pipelines"]), (cx.hw, "pending", "g16-24A10", "12"))
        self.assertNotIn("os_stores", cache.read_kv(os.path.join(old, "prepared.tsv")))          # source untouched
        self.assertFalse(os.path.exists(os.path.join(cx.dir, "PPSA00001.bin")))                  # nothing claimed as built
        lines = cache.status(cx)[1]
        self.assertEqual(cache.status(cx)[0], "prepared"); self.assertTrue(any("OS stores pending" in l for l in lines))
        self.assertFalse(cache.clear_os_pending(cx))                                              # driver cache not rebuilt yet: stays pending
        open(os.path.join(cx.dir, "PPSA00001.bin"), "w").write("rebuilt")
        self.assertTrue(cache.clear_os_pending(cx)); self.assertNotIn("os_stores", cache.read_kv(os.path.join(cx.dir, "prepared.tsv")))

    def test_osadopt2_unprepared_or_same_os_adoption_leaves_prepared_record_alone(self):
        self.mk_os_dir("g16-24A10", age=100)                                                      # STACK.txt only: never prepared
        cx = self.sctx(); self.assertEqual(cache.adopt_sibling(cx)[0], "adopted")
        self.assertFalse(os.path.exists(os.path.join(cx.dir, "prepared.tsv")))
        shutil.rmtree(cx.dir)
        sib = self.mkdir_old("kyty-12121212", hw="g16-25G83", age=500)
        self.assertEqual(cache.adopt_sibling(self.sctx())[0], "adopted")
        self.assertNotIn("os_stores", cache.read_kv(os.path.join(self.sctx().dir, "prepared.tsv")))

    def test_t6_adoption_takes_dirs_that_do_not_say_their_hw_and_skips_empty_ones(self):
        self.mkdir_old("kyty-aaaaaaaa")                                             # a player-filled dir: no prepared.tsv
        self.assertEqual(cache.adopt_sibling(self.sctx())[0], "adopted")
        s2 = os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001"); shutil.rmtree(os.path.join(s2, cache.stable_name("kyty", "g16-25G83")))
        shutil.rmtree(os.path.join(s2, "kyty-aaaaaaaa")); os.makedirs(os.path.join(s2, "kyty-bbbbbbbb"))
        self.assertEqual(cache.adopt_sibling(self.sctx()), ("empty", None))
        self.assertFalse(os.path.exists(self.sctx().dir))

    def test_t6_never_touches_other_titles_or_gpus(self):
        other_title = self.mkdir_old("kyty-55555555", hw="g16-25G83", tid="PPSA99999")
        other_gpu = self.mkdir_old("anyps5-66666666", hw="g16-25G83")
        snap = (cache.tree_manifest(other_title), cache.tree_manifest(other_gpu))
        cx = self.sctx()
        self.assertEqual(cache.sibling_candidates(cx), [])
        self.assertEqual(cache.adopt_sibling(cx), ("empty", None))
        self.assertEqual(cache.prepare_stable_dir(cx), [])
        self.assertEqual((cache.tree_manifest(other_title), cache.tree_manifest(other_gpu)), snap)
        self.assertTrue(os.path.isdir(cx.dir))                                      # created empty, with STACK.txt only
        self.assertEqual(sorted(os.listdir(cx.dir)), [cache.STACK_FILE])

    def test_t6_failed_clone_leaves_nothing_and_the_source_alone(self):
        src = self.mkdir_old("kyty-77777777", hw="g16-25G83")
        cx = self.sctx(); snap = cache.tree_manifest(src)
        real = cache.subprocess.run
        class R: returncode = 1; stderr = b"no clone"
        cache.subprocess.run = lambda *a, **k: R()
        try:
            self.assertEqual(cache.adopt_sibling(cx), ("failed", "kyty-77777777"))
        finally:
            cache.subprocess.run = real
        self.assertFalse(os.path.exists(cx.dir)); self.assertFalse(os.path.lexists(cx.dir + ".adopting")); self.assertEqual(cache.tree_manifest(src), snap)
        # a clone that differs from the source (verify catches it)
        def bad(cmd, **k):
            real(["cp", "-R", cmd[3], cmd[4]], capture_output=True); os.remove(os.path.join(cmd[4], "PPSA00001.programs.bin"))
            class Ok: returncode = 0; stderr = b""
            return Ok()
        cache.subprocess.run = bad
        try:
            self.assertEqual(cache.adopt_sibling(cx)[0], "failed")
        finally:
            cache.subprocess.run = real
        self.assertFalse(os.path.exists(cx.dir)); self.assertFalse(os.path.lexists(cx.dir + ".adopting"))

    def test_t6_stack_txt_records_the_stack_and_history(self):
        cx = self.sctx(); os.makedirs(cx.dir)
        self.assertIsNone(cache.write_stack(cx))
        kv = cache.read_kv(os.path.join(cx.dir, cache.STACK_FILE))
        self.assertEqual((kv["kyty"], kv["kk"], kv["ps5recomp"], kv["hw"]), ("aaaa111", "bbbb222", "cccc333", "g16-25G83"))
        cx2 = self.sctx(commits=dict(kyty="zzzz999", kk="bbbb222", ps5recomp="cccc333"))
        self.assertEqual(cache.write_stack(cx2), "aaaa111")                          # the Kyty commit the dir saw before
        cache.write_stack(cx2)                                                       # same stack again: no new history line
        text = open(os.path.join(cx.dir, cache.STACK_FILE)).read()
        self.assertEqual(text.count("history\t"), 2); self.assertIn("kyty=zzzz999", text)

    def test_t6_metal_rows_are_set_aside_when_kyty_moved_and_kept_when_not(self):
        cx = self.sctx(); os.makedirs(os.path.join(cx.dir, "metal-shaders")); open(os.path.join(cx.dir, "metal-shaders", "a.metal"), "w").write("m")
        self.assertEqual(cache.prepare_stable_dir(cx), [])                           # first stamp, not adopted: left alone
        self.assertTrue(os.path.isdir(os.path.join(cx.dir, "metal-shaders")))
        self.assertEqual(cache.prepare_stable_dir(cx), [])                           # same Kyty: kept
        self.assertTrue(os.path.isdir(os.path.join(cx.dir, "metal-shaders")))
        cx2 = self.sctx(commits=dict(kyty="zzzz999", kk="bbbb222", ps5recomp="cccc333"))
        notes = cache.prepare_stable_dir(cx2)
        self.assertEqual(len(notes), 1); self.assertIn("nps5 cache metal PPSA00001", notes[0])
        self.assertFalse(os.path.isdir(os.path.join(cx.dir, "metal-shaders"))); self.assertTrue(os.path.isfile(os.path.join(cx.dir, "metal-shaders.aside", "a.metal")))

    def test_t6_adopted_dir_sets_metal_rows_aside_unless_the_same_kyty(self):
        src = self.mkdir_old("kyty-88888888", hw="g16-25G83"); os.makedirs(os.path.join(src, "metal-shaders")); open(os.path.join(src, "metal-shaders", "a.metal"), "w").write("m")
        cx = self.sctx()
        notes = cache.prepare_stable_dir(cx)                                          # prepared.tsv says kyty=oldkyty, now aaaa111
        self.assertTrue(any("regenerates" in n for n in notes)); self.assertTrue(any("clone of kyty-88888888" in n for n in notes))
        self.assertTrue(os.path.isdir(os.path.join(src, "metal-shaders")))             # the old dir keeps its rows
        src2 = self.mkdir_old("kyty-99999999", hw="g16-25G83", age=-50); shutil.rmtree(cx.dir); os.makedirs(os.path.join(src2, "metal-shaders"))
        open(os.path.join(src2, "prepared.tsv"), "w").write("key=bbbb\nrc=0\nhw=g16-25G83\nkyty=aaaa111\n")
        notes = cache.prepare_stable_dir(cx)
        self.assertFalse(any("regenerates" in n for n in notes)); self.assertTrue(os.path.isdir(os.path.join(cx.dir, "metal-shaders")))

    def test_t6_status_of_a_stable_dir_ignores_the_moved_stack(self):
        cx = self.sctx(); os.makedirs(cx.dir)
        with open(os.path.join(cx.dir, "prepared.tsv"), "w") as f:
            f.write("key=oldkey\nrc=0\npipelines=1260\nhw=g16-25G83\nkyty=oldkyty\nkk=bbbb222\nps5recomp=cccc333\n")
        st, lines = cache.status(cx)
        self.assertEqual(st, "prepared"); self.assertIn("stable dir", "\n".join(lines)); self.assertIn("kyty", "\n".join(lines))
        with open(os.path.join(cx.dir, "prepared.tsv"), "w") as f:
            f.write("key=oldkey\nrc=0\nhw=g17-26A1\n")                                 # prepared for other hardware: not accepted
        self.assertNotEqual(cache.status(cx)[0], "prepared")

    def test_t6_run_path_adopts_only_when_not_a_dry_run(self):
        self.mkdir_old("kyty-abcdef01", hw="g16-25G83")
        gp = os.path.join(self.tmp, "games", "PPSA00001", "pipelines.kyplst")
        e = self.stable_env()
        cx = cache.context("PPSA00001", dict(e))
        runmod.shader_cache_words("PPSA00001", dict(e), "/app0", adopt=False)
        self.assertFalse(os.path.exists(cx.dir))
        words = runmod.shader_cache_words("PPSA00001", dict(e), "/app0")
        self.assertTrue(os.path.isdir(cx.dir)); self.assertTrue(os.path.isfile(os.path.join(cx.dir, cache.STACK_FILE)))
        self.assertIn(("KYTY_PROGRAM_CACHE_PATH", cx.dir + "/PPSA00001.programs.bin", None), words)

    def test_t6_every_store_has_a_documented_check(self):
        names = " ".join(n for n, _ in cache.STABLE_DIR_CHECKS)
        for store in ("<id>.bin", "programs.bin", "journal", "kk-archive", "kk-libcache", "metal-shaders"):
            self.assertIn(store, names)

    UE5ROWS = "cache\tkind=kyplst\tdir=player\tcode=ue5\tapp_ver=01.001.000\tlimit_mb=448\nue5\tdecoder=0x926510\tfp=1f82e44a65371db3\tabi=oodlelz14"

    def test_ue5_list_with_executable_shaders_keeps_the_eboot(self):
        # a code=ue5 list may hold executable-sourced shaders (SAROS: 41); Kyty ignores the whole list without the eboot, so run and prepare pass it
        self.set_cache_row(self.UE5ROWS); c = self.ctx(); app0 = os.path.join(self.tmp, "app0")
        for words in (cache.launch_words(c, app0), cache.prepare_words(c, app0)):
            self.assertEqual(dict(words)["KYTY_PIPELINE_LIST_EXE"], os.path.join(app0, "eboot.bin"))

    def test_ue5_words_status_and_prepare(self):
        plain = self.ctx(); app0 = os.path.join(self.tmp, "app0")
        self.assertEqual(cache.ue5_words(plain), []); self.assertIsNone(cache.ue5_row(plain.p))
        self.assertIn("KYTY_PIPELINE_LIST_EXE", dict(cache.launch_words(plain, app0)))
        self.set_cache_row(self.UE5ROWS)
        c = self.ctx()
        self.assertEqual(c.dir, os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "kyty-player"))
        w = dict(cache.launch_words(c, app0))
        self.assertIn("KYTY_PIPELINE_LIST_EXE", w)
        self.assertEqual(w["KYTY_PIPELINE_LIST_CODE_STORE"], c.dir + "/PPSA00001.codestore")
        self.assertEqual((w["KYTY_UE5_DECODER"], w["KYTY_UE5_DECODER_FP"], w["KYTY_UE5_DECODER_ABI"]), ("0x926510", "1f82e44a65371db3", "oodlelz14"))
        self.assertEqual(w["KYTY_PIPELINE_REPLAY_ALL"], "1")
        st, lines = cache.status(c)
        self.assertEqual(st, "prepared"); self.assertIn("optional", "\n".join(lines))
        self.assertEqual(w["KYTY_LAUNCH_WAIT_BEFORE_ENTRY"], "1")
        self.assertNotIn("KYTY_LAUNCH_WAIT_BEFORE_ENTRY", dict(cache.launch_words(plain, app0)))
        # refused programs: code=ue5 only (prepare and launch); ASTRO's plain kyplst plan has no such word, so Kyty's default (off) holds
        self.assertEqual(w["KYTY_REFUSED_PROGRAMS"], "1")
        self.assertEqual(dict(cache.prepare_words(c, app0))["KYTY_REFUSED_PROGRAMS"], "1")
        self.assertNotIn("KYTY_REFUSED_PROGRAMS", dict(cache.launch_words(plain, app0)))
        self.assertNotIn("KYTY_REFUSED_PROGRAMS", dict(cache.prepare_words(plain, app0)))

    # ---- status
    def test_status_fresh_partial_prepared_stale(self):
        c = self.ctx()
        self.assertEqual(cache.status(c)[0], "fresh")
        os.makedirs(c.dir)
        self.assertEqual(cache.status(c)[0], "partial")
        with open(os.path.join(c.dir, "prepared.tsv"), "w") as f:
            f.write("key=%s\nrc=0\npipelines=1260\n" % c.key); [f.write("%s=%s\n" % kv) for kv in c.identity().items()]
        st, lines = cache.status(c)
        self.assertEqual(st, "prepared"); self.assertIn("1260", "\n".join(lines))
        # the list grows: another key, the old directory is reported stale and names the changed field
        open(c.list_path, "ab").write(b"x\n")
        c2 = self.ctx()
        st, lines = cache.status(c2)
        self.assertEqual(st, "stale"); self.assertIn("changed: list", "\n".join(lines))
        # a KK commit change is named too
        self.env["NPS5_STACK_KK"] = "dddd444"
        st, lines = cache.status(self.ctx())
        self.assertEqual(st, "stale"); self.assertIn("list, kk", "\n".join(lines))

    def test_failed_prepare_is_not_prepared(self):
        c = self.ctx(); os.makedirs(c.dir)
        open(os.path.join(c.dir, "prepared.tsv"), "w").write("key=%s\nrc=1\n" % c.key)
        self.assertEqual(cache.status(c)[0], "partial")

    def test_status_exit_codes_and_unknown_commit_warning(self):
        self.env["NPS5_STACK_KK"] = ""
        self.env["NPS5_KK_WORK"] = "/nonexistent"; self.env["NPS5_KK_DIR"] = "/nonexistent"
        rc, out, _ = self.run_main(["status", "PPSA00001"])
        self.assertEqual(rc, 1); self.assertIn("state: fresh", out); self.assertIn("no commit known for kk", out)

    # ---- env words
    def test_cache_env_points_everything_at_the_dir(self):
        e = dict(cache.cache_env("PPSA00001", "/c", "mid"))
        self.assertEqual(e["KYTY_PIPELINE_JOURNAL_PATH"], "/c/PPSA00001.pipelines.journal")
        self.assertEqual(e["KK_LIBCACHE_DIR"], "/c/kk-libcache"); self.assertEqual(e["KK_METAL_CACHE_ID"], "mid")
        self.assertNotIn("KK_LIBCACHE_DIR", dict(cache.cache_env("PPSA00001", "/c", "mid", with_libcache=False)))

    # ---- prepare / record / check
    def test_ue5_prepare_carries_the_ue5_words_and_keeps_the_players_journals(self):
        # ue5fix M1: a prepare of a code=ue5 title reads the library (ue5 words, no eboot) and never wipes the player's journals / code store
        self.set_cache_row(self.UE5ROWS)
        c = self.ctx(); os.makedirs(c.dir)
        keep = ["PPSA00001.shaders.journal", "PPSA00001.session_a.pipelines.journal", "PPSA00001.codestore"]
        for f in keep + ["stale.bin"]: open(os.path.join(c.dir, f), "w").write(f)
        calls = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, "  shaders 1\n  permutations 2, pipelines 3\n"
            calls.append(cmd); return 0
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        for f in keep: self.assertEqual(open(os.path.join(c.dir, f)).read(), f, f)
        self.assertFalse(os.path.exists(os.path.join(c.dir, "stale.bin")))
        cmd = " ".join(calls[0])
        for w in ("KYTY_UE5_DECODER=0x926510", "KYTY_UE5_DECODER_FP=1f82e44a65371db3", "KYTY_UE5_DECODER_ABI=oodlelz14", "KYTY_PIPELINE_LIST_CODE_STORE=" + c.dir + "/PPSA00001.codestore", "KYTY_PIPELINE_LIST_PREPARE=1"):
            self.assertIn(w, cmd)
        self.assertIn("KYTY_PIPELINE_LIST_EXE", cmd)

    def test_interrupted_prepare_keeps_the_players_files_and_the_next_one_restores_them(self):
        # ue5fix2 n-n1: the player's journals / code store only ever move to a marked keep dir; an interruption between the move and the
        # restore leaves them there, never deleted, and the next prepare puts them back first (no rmtree of a keep dir)
        self.set_cache_row(self.UE5ROWS)
        c = self.ctx(); os.makedirs(c.dir)
        keep = ["PPSA00001.shaders.journal", "PPSA00001.session_a.pipelines.journal", "PPSA00001.codestore"]
        for f in keep + ["stale.bin"]: open(os.path.join(c.dir, f), "w").write(f)
        def runner(cmd, env=None, capture=False):
            return (0, "  shaders 1\n  permutations 2, pipelines 3\n") if capture else 0
        real = cache.rmtree_inside
        def boom(path, root): raise KeyboardInterrupt()
        cache.rmtree_inside = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.run_main(["prepare", "PPSA00001"], runner)
        finally:
            cache.rmtree_inside = real
        pk = c.dir + ".playerkeep"
        for f in keep: self.assertEqual(open(os.path.join(pk, f)).read(), f, f)          # still there, in the marked keep dir
        self.assertTrue(os.path.isfile(os.path.join(pk, cache.PLAYERKEEP_MARK)))
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        for f in keep: self.assertEqual(open(os.path.join(c.dir, f)).read(), f, f)
        self.assertFalse(os.path.exists(pk)); self.assertFalse(os.path.exists(os.path.join(c.dir, "stale.bin")))

    def test_restore_never_deletes_a_keep_dir_it_cannot_restore(self):
        d = os.path.join(self.tmp, "c"); pk = d + ".playerkeep"; os.makedirs(d); os.makedirs(pk)
        with self.assertRaises(cache.CacheError):                                        # no marker: not ours, left alone
            cache.restore_playerkeep(d, pk)
        open(os.path.join(pk, cache.PLAYERKEEP_MARK), "w").write("m"); open(os.path.join(pk, "a.journal"), "w").write("old"); open(os.path.join(d, "a.journal"), "w").write("new")
        with self.assertRaises(cache.CacheError):                                        # both copies exist: neither is touched
            cache.restore_playerkeep(d, pk)
        self.assertEqual(open(os.path.join(pk, "a.journal")).read(), "old"); self.assertEqual(open(os.path.join(d, "a.journal")).read(), "new")

    def test_prepare_replays_the_players_journals_by_design(self):
        # ue5fix2 n-n4: a code=ue5 prepare runs in the player's dir, so KYTY_PIPELINE_JOURNAL_PATH is the player's own journal (prewarm
        # everything seen); documented in docs/ue5-warmlist.md
        self.set_cache_row(self.UE5ROWS)
        c = self.ctx(); w = dict(cache.cache_env(c.tid, c.dir, c.metal))
        self.assertEqual(w["KYTY_PIPELINE_JOURNAL_PATH"], c.dir + "/PPSA00001.pipelines.journal")
        self.assertIn("journal", open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "ue5-warmlist.md")).read().split("replays")[1][:300])

    def test_ue5_status_says_what_the_store_holds(self):
        # ue5fix2 m10: the state stays "prepared" (nothing is required: exit 0, no "not prepared" warning at run), but the text says what
        # the code store holds instead of calling the cache prepared
        self.set_cache_row(self.UE5ROWS)
        c = self.ctx(); os.makedirs(c.dir)
        st, lines = cache.status(c); txt = "\n".join(lines)
        self.assertEqual(st, "prepared"); self.assertIn("no code store yet", txt); self.assertNotIn("prepared %s" % c.tid, txt)
        open(os.path.join(c.dir, "PPSA00001.codestore"), "w").write("x" * 10)
        txt = "\n".join(cache.status(self.ctx())[1])
        self.assertIn("code store 10 bytes", txt)

    def test_non_ue5_prepare_words_unchanged(self):
        self.assertIn("KYTY_PIPELINE_LIST_EXE", dict(cache.prepare_words(self.ctx(), "/app0")))
        self.assertNotIn("KYTY_UE5_DECODER", dict(cache.prepare_words(self.ctx(), "/app0")))

    def test_prepare_dry_run_names_key_and_command(self):
        rc, out, _ = self.run_main(["prepare", "PPSA00001", "--dry-run"])
        c = self.ctx()
        self.assertEqual(rc, 0)
        for s in (c.key, c.metal, "KYTY_PIPELINE_LIST_PREPARE=1", "KYTY_HELPER_PREWARM=1", "KYTY_PIPELINE_LIST_APP_VER=01.001.000", "--class func", "--tag cache-prepare"):
            self.assertIn(s, out)
        self.assertFalse(os.path.exists(c.dir))

    def test_prepare_writes_prepared_tsv_and_clears_the_old_dir(self):
        c = self.ctx(); os.makedirs(c.dir); open(os.path.join(c.dir, "stale.bin"), "w").write("x")
        calls = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, "  shaders 1\n  permutations 2, pipelines 3\n"
            calls.append(cmd); return 0
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(os.path.join(c.dir, "stale.bin")))
        kv = cache.read_kv(os.path.join(c.dir, "prepared.tsv"))
        self.assertEqual(kv["key"], c.key); self.assertEqual(kv["rc"], "0"); self.assertEqual(kv["pipelines"], "3")
        self.assertEqual(cache.status(self.ctx())[0], "prepared")
        self.assertEqual(len(calls), 1)

    def test_prepare_is_two_pass_with_the_library_cache(self):
        c = self.ctx(); os.makedirs(c.dir)
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)           # any existing file: the runner below plays the builder
        lcd = os.path.join(c.dir, "kk-libcache"); calls = []; seen_at_pass2 = {}
        def runner(cmd, env=None, capture=False):
            if capture: return 0, "  shaders 1\n  permutations 2, pipelines 3\n"
            if cmd[0] == "bash":                                                 # the builder: msl -> metallib
                os.makedirs(lcd + "/lib", exist_ok=True); open(lcd + "/lib/k1.metallib", "w").write("m"); calls.append("build"); return 0
            calls.append("run")
            if calls.count("run") == 1:
                os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/k1.metal", "w").write("src")
                open(os.path.join(c.dir, "from-pass-1.bin"), "w").write("x")     # a pass-1 archive entry: must not survive
            else:
                seen_at_pass2.update(lib=os.path.isfile(lcd + "/lib/k1.metallib"), old=os.path.exists(os.path.join(c.dir, "from-pass-1.bin")))
            return 0
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls, ["run", "build", "run", "build", "run", "build", "run"])     # 2 passes, then 2 warm starts (library build between)
        self.assertEqual(seen_at_pass2, {"lib": True, "old": False})
        kv = cache.read_kv(os.path.join(c.dir, "prepared.tsv"))
        self.assertEqual((kv["passes"], kv["metallibs"], kv["warm"]), ("2", "1", "2"))

    def test_prepare_seeds_pass_1_from_the_metallib_store(self):
        c = self.ctx(); os.makedirs(c.dir)
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        store = c.root + ".metallib-store"; os.makedirs(store); open(store + "/old1.metallib", "w").write("m1")
        lcd = os.path.join(c.dir, "kk-libcache"); seen = {}; envs = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, ""
            if cmd[0] == "bash":
                envs.append(env.get("NPS5_METALLIB_STORE")); return 0
            seen.setdefault("lib_at_run", os.path.isfile(lcd + "/lib/old1.metallib"))
            os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/k1.metal", "w").write("src")
            return 0
        rc, out, err = self.run_main(["prepare", "PPSA00001", "--no-warm"], runner)
        self.assertEqual(rc, 0, err)
        self.assertTrue(seen["lib_at_run"]); self.assertIn("1 metallibs from earlier prepares", out)
        self.assertEqual(set(envs), {store})                                     # the builder is told where the store is
        self.env["NPS5_METALLIB_STORE"] = ""                                      # opt-out: nothing is seeded
        os.makedirs(c.dir, exist_ok=True); seen.clear()
        rc, out, err = self.run_main(["prepare", "PPSA00001", "--no-warm"], runner)
        self.assertFalse(seen["lib_at_run"]); self.assertNotIn("earlier prepares", out)

    def test_prepare_keeps_pass_1_when_every_shader_was_loaded_from_metallibs(self):
        c = self.ctx(); os.makedirs(c.dir)
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        store = c.root + ".metallib-store"; os.makedirs(store); open(store + "/k1.metallib", "w").write("m")
        lcd = os.path.join(c.dir, "kk-libcache"); runs = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, ""
            if cmd[0] == "bash": return 0
            runs.append(cmd[cmd.index("--tag") + 1]); open(os.path.join(c.dir, "archive-%d" % len(runs)), "w").write("x"); return 0
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        self.assertEqual(runs, ["cache-prepare", "cache-warm-1", "cache-warm-2"])      # no pass 2: nothing was compiled from source
        self.assertIn("pass 2 skipped", out); self.assertTrue(os.path.exists(os.path.join(c.dir, "archive-1")))
        self.assertEqual(cache.read_kv(os.path.join(c.dir, "prepared.tsv"))["passes"], "1")
        runs.clear()
        self.env["NPS5_PREPARE_KEEP_PASS1"] = "0"                                       # opt-out: the old always-fresh pass 2
        os.makedirs(c.dir, exist_ok=True)
        self.run_main(["prepare", "PPSA00001", "--no-warm"], runner)
        self.assertEqual(runs, ["cache-prepare", "cache-prepare"])
        del self.env["NPS5_PREPARE_KEEP_PASS1"]
        runs.clear()                                                                    # something new collected in pass 1: fresh pass 2
        def runner2(cmd, env=None, capture=False):
            if not capture and cmd[0] != "bash" and not runs:
                os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/new.metal", "w").write("src")
            return runner(cmd, env, capture)
        self.run_main(["prepare", "PPSA00001", "--no-warm"], runner2)
        self.assertEqual(runs, ["cache-prepare", "cache-prepare"])

    # ---- prepfast (nxprep S1-S5): every switch off by default
    def prepfast_runner(self, c, log, msl_new=True):
        lcd = os.path.join(c.dir, "kk-libcache")
        def runner(cmd, env=None, capture=False):
            if capture: return 0, "  shaders 1\n  permutations 2, pipelines 3\n"
            if cmd[0] == "bash":
                os.makedirs(lcd + "/lib", exist_ok=True); open(lcd + "/lib/k1.metallib", "w").write("m"); return 0
            words = " ".join(cmd)
            log.append((cmd[cmd.index("--tag") + 1], words, os.path.isdir(os.path.join(c.dir, "kk-archive")), os.path.exists(os.path.join(c.dir, "marker"))))
            if msl_new:
                os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/k1.metal", "w").write("src")
            os.makedirs(os.path.join(c.dir, "kk-archive"), exist_ok=True); open(os.path.join(c.dir, "kk-archive", "seg-%d" % len(log)), "w").write("s")
            return 0
        return runner

    def test_prepfast_defaults_change_nothing(self):
        c = self.ctx(); log = []
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        self.assertEqual(self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log))[0], 0)
        for tag, words, _, _ in log:
            for w in ("KK_MSL_COLLECT_ONLY", "KYTY_PREPARE_FLUSH_SYNC", "KK_PIPELINE_ARCHIVE_COMPACT", "KK_ARCHIVE_STAMP_EPOCH", "KK_LIBKEY_CONTENT", "KK_PIPELINE_ARCHIVE_PREFAULT"):
                self.assertNotIn(w, words)
        self.assertFalse(c.stable or c.incremental)
        self.assertEqual(c.kk_id, COMMITS["kk"])

    def test_stable_keys_ignore_the_kk_commit_and_set_the_kk_words(self):
        a = self.ctx(commits=dict(COMMITS, kk="sha-A"))
        b = self.ctx(commits=dict(COMMITS, kk="sha-B"))
        self.assertNotEqual(a.key, b.key)                                    # gate off: a KK commit is a new cache
        self.env["NPS5_PREPARE_STABLE_KEYS"] = "1"
        a = self.ctx(commits=dict(COMMITS, kk="sha-A")); b = self.ctx(commits=dict(COMMITS, kk="sha-B"))
        self.assertEqual(a.key, b.key)                                       # gate on: build-script-only / sha changes keep the cache
        self.assertEqual(a.identity()["kk"], "epoch1")
        k = dict(cache.cache_env("PPSA00001", "/c", "mid", environ=self.env))
        self.assertEqual((k["KK_ARCHIVE_STAMP_EPOCH"], k["KK_LIBKEY_CONTENT"]), ("1", "1"))
        self.assertNotIn("KK_LIBKEY_CONTENT", dict(cache.cache_env("PPSA00001", "/c", "mid", environ={})))
        self.assertNotEqual(self.ctx(commits=dict(COMMITS, kyty="other")).key, a.key)    # the codegen inputs still count
        epoch = os.path.join(self.tmp, "EPOCH")
        open(epoch, "w").write("7\n"); self.assertEqual(cache.archive_epoch(epoch), 7)
        open(epoch, "w").write("junk"); self.assertEqual(cache.archive_epoch(epoch), 1)
        self.assertEqual(cache.archive_epoch(os.path.join(self.tmp, "none")), 1)

    def test_fast_switch_turns_every_gate_on(self):
        self.env["NPS5_PREPARE_FAST"] = "1"
        for g in cache.PREPARE_GATES:
            self.assertTrue(cache.gate(self.env, g), g)
        self.assertFalse(cache.gate({}, "FLUSH_SYNC"))
        k = dict(cache.cache_env("PPSA00001", "/c", "mid", environ=self.env))
        self.assertEqual(k["KK_PIPELINE_ARCHIVE_PREFAULT"], "1")

    def test_collect_only_pass_1_only_and_pass_2_keeps_the_archive(self):
        c = self.ctx(); os.makedirs(c.dir); log = []
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        self.env["NPS5_PREPARE_COLLECT_ONLY"] = "1"
        self.assertEqual(self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log))[0], 0)
        self.assertEqual(len(log), 2)
        self.assertIn("KK_MSL_COLLECT_ONLY=1", log[0][1]); self.assertNotIn("KK_MSL_COLLECT_ONLY", log[1][1])
        self.assertTrue(log[1][2])                                           # pass 2 started with pass 1's archive in place
        # without the gate the archive is thrown away between the passes (as before)
        del self.env["NPS5_PREPARE_COLLECT_ONLY"]; log.clear()
        self.assertEqual(self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log))[0], 0)
        self.assertFalse(log[1][2])
        # single pass / no builder: collect-only would compile nothing at all, so it is not applied
        self.env["NPS5_PREPARE_COLLECT_ONLY"] = "1"; log.clear()
        self.assertEqual(self.run_main(["prepare", "PPSA00001", "--no-warm", "--single-pass"], self.prepfast_runner(c, log))[0], 0)
        self.assertNotIn("KK_MSL_COLLECT_ONLY", log[0][1])
        self.env["NPS5_KK_LIBCACHE_BUILD"] = "/nonexistent/b.sh"; log.clear()
        self.assertEqual(self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log))[0], 0)
        self.assertNotIn("KK_MSL_COLLECT_ONLY", log[0][1])

    def test_flush_sync_and_compaction_words(self):
        c = self.ctx(); log = []
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        self.env["NPS5_PREPARE_FLUSH_SYNC"] = "1"
        self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log))
        self.assertTrue(all("KYTY_PREPARE_FLUSH_SYNC=1" in w for _, w, _, _ in log)); self.assertTrue(all("COMPACT" not in w for _, w, _, _ in log))
        del self.env["NPS5_PREPARE_FLUSH_SYNC"]; self.env["NPS5_PREPARE_COMPACT"] = "1"; log.clear()
        self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log))
        self.assertNotIn("KK_PIPELINE_ARCHIVE_COMPACT", log[0][1]); self.assertIn("KK_PIPELINE_ARCHIVE_COMPACT=1", log[1][1])   # the LAST pass compacts
        self.assertIn("KYTY_PREPARE_FLUSH_SYNC=1", log[1][1])                 # ... and the sync flush is what writes it
        # pass 1 was final (all libraries came from the store): an extra compaction pass runs
        store = c.root + ".metallib-store"; os.makedirs(store); open(store + "/k1.metallib", "w").write("m"); log.clear()
        self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log, msl_new=False))
        self.assertEqual(len(log), 2); self.assertIn("KK_PIPELINE_ARCHIVE_COMPACT=1", log[1][1]); self.assertNotIn("COMPACT", log[0][1])

    def test_incremental_key_ignores_the_list_and_reuses_the_dir(self):
        self.env["NPS5_PREPARE_INCREMENTAL"] = "1"
        a = self.ctx()
        open(os.path.join(self.tmp, "games", "PPSA00001", "pipelines.kyplst"), "wb").write(b"1 2 4 more\n")
        b = self.ctx()
        self.assertNotEqual(a.list_sha, b.list_sha); self.assertEqual(a.key, b.key); self.assertEqual(a.dir, b.dir); self.assertEqual(a.metal, b.metal)
        del self.env["NPS5_PREPARE_INCREMENTAL"]
        self.assertNotEqual(self.ctx().key, a.key)                              # gate off: the list is part of the key again
        self.env["NPS5_PREPARE_INCREMENTAL"] = "1"
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        c = a                                                                   # prepare list A (fresh), then list B into the same dir
        open(os.path.join(self.tmp, "games", "PPSA00001", "pipelines.kyplst"), "wb").write(b"1 2 3\n")
        log = []
        c = self.ctx(); self.assertEqual(self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(c, log))[0], 0)
        self.assertEqual(cache.status(self.ctx())[0], "prepared")
        open(os.path.join(c.dir, "marker"), "w").write("kept")
        open(os.path.join(self.tmp, "games", "PPSA00001", "pipelines.kyplst"), "wb").write(b"1 2 4 more\n")
        st, lines = cache.status(self.ctx())
        self.assertEqual(st, "stale"); self.assertIn("list changed", " ".join(lines))
        log.clear()
        rc, out, err = self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(self.ctx(), log, msl_new=False))
        self.assertEqual(rc, 0, err); self.assertIn("incremental", out)
        self.assertTrue(os.path.exists(os.path.join(c.dir, "marker")))        # the dir was not wiped
        self.assertIn("KK_MSL_COLLECT_ONLY=1", log[0][1]); self.assertTrue(log[0][2] and log[0][3])   # archive and marker present when pass 1 started
        self.assertEqual(cache.status(self.ctx())[0], "prepared")
        kv = cache.read_kv(os.path.join(c.dir, "prepared.tsv")); self.assertEqual(kv["list"], self.ctx().list_sha)
        # a codegen change (Kyty commit) is a new key = a fresh dir, never an incremental one
        d = self.ctx(commits=dict(COMMITS, kyty="other"))
        self.assertNotEqual(d.dir, c.dir)
        # a prepare that failed is not reused
        open(os.path.join(c.dir, "prepared.tsv"), "w").write("key=%s\nrc=1\n" % c.key); log.clear()
        self.run_main(["prepare", "PPSA00001", "--no-warm"], self.prepfast_runner(self.ctx(), log))
        self.assertFalse(os.path.exists(os.path.join(c.dir, "marker")))

    def test_prepare_warm_starts_are_normal_starts_and_optional(self):
        c = self.ctx(); os.makedirs(c.dir)
        self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__)
        lcd = os.path.join(c.dir, "kk-libcache"); runs = []
        def runner(cmd, env=None, capture=False, fail_warm=None):
            if capture: return 0, ""
            if cmd[0] == "bash":
                os.makedirs(lcd + "/lib", exist_ok=True); open(lcd + "/lib/k1.metallib", "w").write("m"); return 0
            os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/k1.metal", "w").write("src")
            runs.append(cmd); return 0
        self.assertEqual(self.run_main(["prepare", "PPSA00001"], runner)[0], 0)
        tags = [r[r.index("--tag") + 1] for r in runs]
        self.assertEqual(tags, ["cache-prepare", "cache-prepare", "cache-warm-1", "cache-warm-2"])
        w = " ".join(runs[2])
        self.assertIn("KYTY_PIPELINE_REPLAY_ALL=1", w); self.assertNotIn("KYTY_PIPELINE_LIST_PREPARE=1", w)   # a normal start, not a prepare
        self.assertIn("KK_LIBCACHE_DIR=%s" % lcd, w)
        p1 = runs[0]; self.assertEqual(p1[p1.index("--secs") + 1], str(cache.PREPARE_SECS))            # a prepare is not cut at 180 s
        runs.clear()
        self.assertEqual(self.run_main(["prepare", "PPSA00001", "--no-warm"], runner)[0], 0)
        self.assertEqual(len(runs), 2)
        self.assertEqual(cache.read_kv(os.path.join(c.dir, "prepared.tsv"))["warm"], "0")
        def failing(cmd, env=None, capture=False):
            if not capture and cmd[0] != "bash" and "cache-warm-1" in cmd: return 5
            return runner(cmd, env, capture)
        rc, out, _ = self.run_main(["prepare", "PPSA00001"], failing)
        self.assertEqual(rc, 0); self.assertIn("warm start 1 ended rc=5", out)                         # a warning, still prepared
        self.assertEqual(cache.status(self.ctx())[0], "prepared")

    def test_prepare_single_pass_flag_and_nothing_collected(self):
        c = self.ctx(); self.env["NPS5_KK_LIBCACHE_BUILD"] = os.path.abspath(__file__); lcd = os.path.join(c.dir, "kk-libcache"); calls = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, ""
            calls.append(cmd[0])
            if cmd[0] == "bash": os.makedirs(lcd + "/lib", exist_ok=True); open(lcd + "/lib/k1.metallib", "w").write("m")
            else: os.makedirs(lcd + "/msl", exist_ok=True); open(lcd + "/msl/k1.metal", "w").write("src")
            return 0
        self.assertEqual(self.run_main(["prepare", "PPSA00001", "--single-pass"], runner)[0], 0)
        self.assertEqual(len([x for x in calls if x != "bash"]), 1)
        calls.clear(); self.env["NPS5_KK_LIBCACHE_BUILD"] = "/nonexistent/builder.sh"           # no builder: one pass, as before
        self.assertEqual(self.run_main(["prepare", "PPSA00001"], runner)[0], 0)
        self.assertEqual(len(calls), 1)

    def test_prepare_failure_stays_unprepared(self):
        def runner(cmd, env=None, capture=False):
            return (0, "") if capture else 3
        rc, _, _ = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 1); self.assertEqual(cache.status(self.ctx())[0], "partial")

    def test_record_runs_each_scene_into_an_empty_arm_and_keeps_journals(self):
        c = self.ctx(); seen = []
        def runner(cmd, env=None, capture=False):
            d = next(x.split("=", 1)[1] for x in cmd if x.startswith("KYTY_PIPELINE_JOURNAL_PATH=")); d = os.path.dirname(d)
            seen.append((cmd[cmd.index("--scene") + 1], d)); os.makedirs(d + "/kk-archive", exist_ok=True)
            for f in ("PPSA00001.pipelines.journal", "PPSA00001.shaders.journal", "PPSA00001.bin"): open(os.path.join(d, f), "w").write("j")
            return 0
        rc, out, _ = self.run_main(["record", "PPSA00001"], runner)
        self.assertEqual([s for s, _ in seen], ["boot", "lvl1"])            # the level:* template is not recorded
        d = seen[0][1]
        self.assertTrue(os.path.isfile(d + "/PPSA00001.pipelines.journal")); self.assertFalse(os.path.exists(d + "/PPSA00001.bin")); self.assertFalse(os.path.exists(d + "/kk-archive"))
        rc, out, _ = self.run_main(["record", "PPSA00001"], runner)
        self.assertEqual(len(seen), 2); self.assertIn("skipped", out)         # a second record leaves the recordings alone

    def test_record_unknown_scene_refused(self):
        rc, _, err = self.run_main(["record", "PPSA00001", "--scene", "nope"])
        self.assertEqual(rc, 2); self.assertIn("scene nope", err)

    def test_parse_check_counts_compiles_and_stalls(self):
        err = "x\n[kk-archive] stats libraries 12 (1 slow, 90 ms), pipelines archive-hit 100 (5 ms) compiled 7 (2 slow, 300 ms)\n[RED] foo bar\n"
        r = cache.parse_check(err, [16.0, 300.0, 16.0, 900.0])
        self.assertEqual((r["compiled"], r["libraries"], r["stalls"], r["worst_ms"]), (7, 12, 2, 900.0))
        self.assertEqual(r["red"], ["foo"]); self.assertAlmostEqual(r["stall_s"], 1.2)
        self.assertIsNone(cache.parse_check("", [])["compiled"])
        self.assertEqual(cache.check_row("s", 0, r).split("\t")[:3], ["s", "0", "7"])

    def test_check_needs_a_prepared_cache(self):
        rc, _, err = self.run_main(["check", "PPSA00001", "--scene", "boot"])
        self.assertEqual(rc, 1); self.assertIn("needs a prepared cache", err)

    def test_check_dry_run_and_real_run_on_a_clone(self):
        c = self.ctx(); os.makedirs(c.dir)
        open(os.path.join(c.dir, "prepared.tsv"), "w").write("key=%s\nrc=0\n" % c.key); open(os.path.join(c.dir, "marker"), "w").write("m")
        rc, out, _ = self.run_main(["check", "PPSA00001", "--scene", "boot", "--dry-run"])
        self.assertEqual(rc, 0); self.assertIn("clone", out)
        runs = os.path.join(self.env["NPS5_SCRATCH"], "runs", "PPSA00001", "cache-check-boot"); os.makedirs(runs)
        def runner(cmd, env=None, capture=False):
            d = next(x.split("=", 1)[1] for x in cmd if x.startswith("KK_LIBCACHE_DIR=")); d = os.path.dirname(d)
            self.assertTrue(os.path.isfile(d + "/marker"))                      # the run sees a clone of the prepared cache
            open(runs + "/run.err", "w").write("[kk-archive] stats libraries 0 (0 slow, 0 ms), pipelines archive-hit 9 (1 ms) compiled 0 (0 slow, 0 ms)\n")
            open(runs + "/run.json", "w").write("{}"); return 0
        rc, out, _ = self.run_main(["check", "PPSA00001", "--scene", "boot"], runner)
        self.assertEqual(rc, 0); self.assertIn("boot\t0\t0", out)
        self.assertTrue(os.path.isfile(os.path.join(c.dir, "marker")))          # the prepared cache itself is untouched
        # a run that is refused (busy, rule) leaves the folder of the PREVIOUS check: its log must not be reported as this check
        old = time.time() - 600; os.utime(runs + "/run.json", (old, old))
        rc, out, _ = self.run_main(["check", "PPSA00001", "--scene", "boot"], lambda cmd, env=None, capture=False: 75)
        self.assertEqual(rc, 1); self.assertIn("boot\t75\t-\t-", out); self.assertIn("not a measurement", out)
        rc, out, _ = self.run_main(["check", "PPSA00001", "--scene", "boot"], lambda cmd, env=None, capture=False: 0)   # "ok" but no new run.json
        self.assertEqual(rc, 1); self.assertIn("boot\tnorun", out)

    def test_torn_prepared_record_is_not_prepared(self):
        c = self.ctx(); os.makedirs(c.dir)
        open(os.path.join(c.dir, "prepared.tsv"), "w").write("key=%s\n" % c.key)          # the writer died before the rc line
        self.assertEqual(cache.status(c)[0], "partial")
        calls = []
        rc, _, _ = self.run_main(["prepare", "PPSA00001"], lambda cmd, env=None, capture=False: (calls.append(cmd), (0, "pipelines 5") if capture else 0)[1])
        self.assertEqual(rc, 0); self.assertEqual(cache.status(self.ctx())[0], "prepared")
        self.assertEqual([n for n in os.listdir(c.dir) if n.endswith(".tmp")], [])

    def test_merge_with_a_failed_export_adopts_nothing(self):
        for n in ("boot", "map"):
            rec = os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "rec", n); os.makedirs(rec)
            for j in ("pipelines", "shaders"): open(os.path.join(rec, "PPSA00001.%s.journal" % j), "w").write("j")
        def runner(cmd, env=None, capture=False):
            if "export" in cmd and any(x.endswith("map.kyplst") for x in cmd): return (1, "boom")
            return cache.default_runner(cmd, env, capture)
        rc, out, _ = self.run_main(["merge", "PPSA00001", "--rebuild", "--write"], runner)
        self.assertEqual(rc, 1); self.assertIn("NOT adopted", out)
        self.assertEqual(open(self.ctx().list_path, "rb").read(), b"1 2 3\n")

    def test_public_layout_finds_the_kosmickrisp_identity(self):
        import subprocess
        work = os.path.join(self.tmp, "build", "kosmickrisp"); mesa = os.path.join(work, "mesa"); os.makedirs(mesa)
        def g(*a, data=None): return subprocess.run(("git", "-C", mesa) + a, input=data, capture_output=True, check=True).stdout.decode().strip()
        g("init", "-q")
        g("fast-import", "--quiet", data=b"commit refs/heads/main\ncommitter T <t@example.invalid> 1 +0000\ndata 1\nc\nM 100644 inline f.c\ndata 6\nint a;\n\n")
        g("symbolic-ref", "HEAD", "refs/heads/main"); g("reset", "-q", "--hard")
        self.env.update(NPS5_STACK_KK="", NPS5_KK_WORK=work, NPS5_KK_DIR="")
        pin = cache.stack_commits(self.env)["kk"]
        self.assertEqual(pin, g("rev-parse", "--short=10", "HEAD"))
        k0 = self.ctx().key
        open(os.path.join(mesa, "f.c"), "w").write("int patched;\n")                 # the series applied, not committed
        self.assertRegex(cache.stack_commits(self.env)["kk"], r"^%s\+[0-9a-f]{8}$" % pin)
        self.assertNotEqual(self.ctx().key, k0)                                        # a changed driver source is a new cache key
        _, out, _ = self.run_main(["status", "PPSA00001"]); self.assertNotIn("no commit known", out)

    def test_gpu_from_the_titles_file_is_checked(self):
        open(os.path.join(self.tmp, "titles", "PPSA00001.env"), "a").write("GPU=../../x\n")
        rc, _, err = self.run_main(["status", "PPSA00001"]); self.assertEqual(rc, 2); self.assertIn("kyty or anyps5", err)

    # ---- merge / verify-list
    def test_merge_unions_with_the_committed_list_and_reports_counts(self):
        rec = os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "rec", "boot"); os.makedirs(rec)
        for n in ("pipelines", "shaders"): open(os.path.join(rec, "PPSA00001.%s.journal" % n), "w").write("j")
        rc, out, err = self.run_main(["merge", "PPSA00001"])
        self.assertEqual(rc, 0, err); self.assertIn("shaders 3", out); self.assertIn("candidate only", out)
        self.assertTrue(os.path.isfile(os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "merged.kyplst")))
        self.assertEqual(open(self.ctx().list_path, "rb").read(), b"1 2 3\n")      # the committed list is not touched without --write

    def test_merge_write_replaces_the_list_and_drops_the_stale_verified(self):
        rec = os.path.join(self.tmp, "myrec"); os.makedirs(rec)
        for n in ("pipelines", "shaders"): open(os.path.join(rec, "PPSA00001.%s.journal" % n), "w").write("j")
        vp = self.ctx().list_path + ".verified"; open(vp, "w").write("sha256=old\n")
        rc, out, _ = self.run_main(["merge", "PPSA00001", "--from", rec, "--write"])
        self.assertEqual(rc, 0); self.assertFalse(os.path.exists(vp)); self.assertEqual(open(self.ctx().list_path).read().strip(), "3 4 5")

    def test_merge_without_recordings_says_so(self):
        rc, _, err = self.run_main(["merge", "PPSA00001"])
        self.assertEqual(rc, 1); self.assertIn("no recordings", err)

    def test_verify_list_calls_the_checker_script_with_the_tool(self):
        calls = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, "check: ok\n" if cmd[1] == "check" else "  shaders 1\n  permutations 2, pipelines 3\n"
            calls.append((cmd, env)); return 0
        rc, out, err = self.run_main(["verify-list", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        cmd, env = calls[0]
        self.assertTrue(cmd[1].endswith("scripts/verify-pipeline-list.sh")); self.assertEqual(env["NPS5_KYPLST_TOOL"], self.env["NPS5_KYPLST_TOOL"])
        self.assertIn("review block", out)

    def test_verify_list_failed_check_stops(self):
        def runner(cmd, env=None, capture=False):
            return (1, "bad\n") if capture else 0
        rc, out, _ = self.run_main(["verify-list", "PPSA00001"], runner)
        self.assertEqual(rc, 1); self.assertIn("do not commit", out)

    def test_clone_dir_copies(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b"); os.makedirs(a); open(a + "/f", "w").write("x")
        cache.clone_dir(a, b); self.assertTrue(os.path.isfile(b + "/f"))

    def test_rmtree_refuses_outside_the_cache_root(self):
        with self.assertRaises(cache.CacheError):
            cache.rmtree_inside(self.tmp, os.path.join(self.tmp, "scratch"))

    def test_usage(self):
        self.assertEqual(self.run_main([])[0], 2)
        self.assertEqual(self.run_main(["frobnicate", "PPSA00001"])[0], 2)
        self.assertEqual(self.run_main(["status"])[0], 2)

    # ---- Metal refresh after a session (programs.bin grew while the title was played)
    def metal_ctx(self):
        gd = os.path.join(self.tmp, "games", "PPSA00001")
        open(os.path.join(gd, "metal-shaders.tsv"), "w").write("format\tnps5-metal-keys\t2\n")
        c = self.ctx(); os.makedirs(os.path.join(c.dir, "metal-shaders"), exist_ok=True)
        return c

    def set_programs(self, c, n):
        with open(os.path.join(c.dir, "PPSA00001.programs.bin"), "wb") as f: f.write(b"x" * n)

    def test_metal_stale_and_refresh(self):
        c = self.metal_ctx(); self.set_programs(c, 100)
        self.assertFalse(cache.metal_stale(c))                              # never generated here: nothing to refresh
        cache.stamp_programs(c)
        self.assertEqual(open(os.path.join(c.dir, "metal-shaders", cache.PROGRAMS_STAMP)).read().strip(), "100")
        self.assertFalse(cache.metal_stale(c))
        self.assertIsNone(cache.metal_refresh("PPSA00001", dict(self.env), runner_cmd=[sys.executable, "-c", "raise SystemExit(7)"]))   # not grown: no run
        self.set_programs(c, 250)
        self.assertTrue(cache.metal_stale(c))
        off = dict(self.env, NPS5_METAL_REFRESH="0")
        self.assertIsNone(cache.metal_refresh("PPSA00001", off, runner_cmd=[sys.executable, "-c", "raise SystemExit(7)"]))
        note = cache.metal_refresh("PPSA00001", dict(self.env), runner_cmd=[sys.executable, "-c", "raise SystemExit(0)"])
        self.assertIn("refreshed", note); self.assertIn("rc 0", note)
        slow = cache.metal_refresh("PPSA00001", dict(self.env), runner_cmd=[sys.executable, "-c", "import time; time.sleep(30)"], limit_s=1)
        self.assertIn("timed out", slow)                                    # bounded
        bad = cache.metal_refresh("PPSA00001", dict(self.env), runner_cmd=["/nonexistent/nps5-x"])
        self.assertIn("skipped", bad)                                       # never raises

    def test_run_refresh_hook(self):
        import types
        c = self.metal_ctx(); self.set_programs(c, 10); cache.stamp_programs(c); self.set_programs(c, 99)
        runmod_called = []
        orig = cache.metal_refresh
        cache.metal_refresh = lambda tid, environ, **kw: runmod_called.append(tid) or "x"
        try:
            plan = types.SimpleNamespace(tid="PPSA00001", gpu="kyty", env={"KYTY_PROGRAM_CACHE_PATH": "p"}, tag="t", sel={}, secs=60, cls="func", scene=None, until=None)
            self.assertEqual(runmod.refresh_metal_after_run(plan, dict(self.env)), "x")
            plan.env = {}                                                   # an explicit cache (no default-cache words): never
            self.assertIsNone(runmod.refresh_metal_after_run(plan, dict(self.env)))
            plan.tag = "cache-warm-1"                                       # a prepare's own warm start: no nested refresh
            self.assertIsNone(runmod.refresh_metal_after_run(plan, dict(self.env)))
            plan.tag = "t"
            plan.env = {"KYTY_PROGRAM_CACHE_PATH": "p"}; plan.gpu = "anyps5"
            self.assertIsNone(runmod.refresh_metal_after_run(plan, dict(self.env)))
        finally:
            cache.metal_refresh = orig
        self.assertEqual(runmod_called, ["PPSA00001"])


    # ---- UE5 extraction: player-owned files, ue5 words from the row, prepare keep/restore
    UE5KEEP = ["PPSA00001.shaders.journal", "PPSA00001.session_a.pipelines.journal", "PPSA00001.pipelines.journal", "PPSA00001.codestore"]

    def ue5_prepare_setup(self):
        self.set_cache_row(self.UE5ROWS)
        c = self.ctx(); os.makedirs(c.dir)
        for f in self.UE5KEEP + ["stale.bin", "PPSA00001.programs.bin", "PPSA00001.codestore.stale"]: open(os.path.join(c.dir, f), "w").write(f)
        os.makedirs(os.path.join(c.dir, "kk-archive")); open(os.path.join(c.dir, "kk-archive", "x"), "w").write("x")
        return c

    @staticmethod
    def ok_runner(seen=None):
        def runner(cmd, env=None, capture=False):
            if seen is not None and not capture: seen.append(cmd)
            return (0, "  shaders 1\n  permutations 2, pipelines 3\n") if capture else 0
        return runner

    def test_ue5_prepare_wipes_the_rest_but_moves_only_journals_and_the_store(self):
        c = self.ue5_prepare_setup(); pk = c.dir + ".playerkeep"; during = {}
        def runner(cmd, env=None, capture=False):
            if not capture:
                during["dir"] = sorted(os.listdir(c.dir)); during["keep"] = os.path.isdir(pk)      # the prepare run itself: files already back, the rest wiped
            return (0, "  shaders 1\n  permutations 2, pipelines 3\n") if capture else 0
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        self.assertEqual([f for f in during["dir"] if f.endswith((".journal", ".codestore"))], sorted(self.UE5KEEP), during)     # restored BEFORE the run
        self.assertFalse(during["keep"]); self.assertNotIn("stale.bin", during["dir"])
        for f in self.UE5KEEP: self.assertEqual(open(os.path.join(c.dir, f)).read(), f)          # back in place afterwards
        for gone in ("stale.bin", "PPSA00001.programs.bin", "PPSA00001.codestore.stale", "kk-archive"):
            self.assertFalse(os.path.exists(os.path.join(c.dir, gone)), gone)
        self.assertFalse(os.path.exists(pk))

    def test_ue5_prepare_that_fails_still_gives_the_players_files_back(self):
        c = self.ue5_prepare_setup()
        def runner(cmd, env=None, capture=False): return (1, "boom\n") if capture else 1
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertNotEqual(rc, 0)
        for f in self.UE5KEEP: self.assertEqual(open(os.path.join(c.dir, f)).read(), f, f)
        self.assertFalse(os.path.exists(c.dir + ".playerkeep"))

    def test_ue5_prepare_refuses_a_keep_dir_that_is_not_its_own(self):
        c = self.ue5_prepare_setup(); pk = c.dir + ".playerkeep"; os.makedirs(pk); open(os.path.join(pk, "mine.txt"), "w").write("not ours")
        seen = []
        rc, out, err = self.run_main(["prepare", "PPSA00001"], self.ok_runner(seen))
        self.assertNotEqual(rc, 0); self.assertIn(cache.PLAYERKEEP_MARK, out + err)
        self.assertEqual(open(os.path.join(pk, "mine.txt")).read(), "not ours")                 # untouched, never deleted
        for f in self.UE5KEEP: self.assertTrue(os.path.isfile(os.path.join(c.dir, f)), f)       # nothing wiped, nothing run
        self.assertEqual(seen, [])

    def test_ue5_prepare_with_a_restore_conflict_changes_nothing(self):
        # an interrupted prepare left the store in the keep dir AND a new one was written since: both stay, the prepare stops
        c = self.ue5_prepare_setup(); pk = c.dir + ".playerkeep"; os.makedirs(pk)
        open(os.path.join(pk, cache.PLAYERKEEP_MARK), "w").write("m"); open(os.path.join(pk, "PPSA00001.codestore"), "w").write("OLD")
        rc, out, err = self.run_main(["prepare", "PPSA00001"], self.ok_runner())
        self.assertNotEqual(rc, 0)
        self.assertEqual(open(os.path.join(pk, "PPSA00001.codestore")).read(), "OLD")
        self.assertEqual(open(os.path.join(c.dir, "PPSA00001.codestore")).read(), "PPSA00001.codestore")
        self.assertTrue(os.path.isfile(os.path.join(c.dir, "stale.bin")))

    def test_restore_playerkeep_is_a_no_op_without_a_keep_dir_and_creates_the_target(self):
        d = os.path.join(self.tmp, "t", "c"); pk = d + ".playerkeep"
        cache.restore_playerkeep(d, pk); self.assertFalse(os.path.exists(d))                   # nothing to restore: nothing created
        os.makedirs(pk); open(os.path.join(pk, cache.PLAYERKEEP_MARK), "w").write("m"); open(os.path.join(pk, "a.codestore"), "w").write("S")
        cache.restore_playerkeep(d, pk)                                                         # the cache dir was wiped away entirely: it is recreated
        self.assertEqual(open(os.path.join(d, "a.codestore")).read(), "S"); self.assertFalse(os.path.exists(pk))
        cache.restore_playerkeep(d, pk)                                                         # and a second call is harmless

    def test_non_ue5_prepare_keeps_no_player_files(self):
        c = self.ctx(); os.makedirs(c.dir)
        for f in ("PPSA00001.shaders.journal", "PPSA00001.codestore", "x.bin"): open(os.path.join(c.dir, f), "w").write(f)
        rc, out, err = self.run_main(["prepare", "PPSA00001"], self.ok_runner())
        self.assertEqual(rc, 0, err)
        self.assertEqual([f for f in os.listdir(c.dir) if f.endswith((".journal", ".codestore", "x.bin"))], [])
        self.assertFalse(os.path.exists(c.dir + ".playerkeep"))

    def test_ue5_words_follow_the_row_and_the_player_dir(self):
        self.set_cache_row("cache\tkind=kyplst\tdir=player\tcode=ue5\tapp_ver=01.001.000\nue5\tdecoder=0xABC123\tfp=0123456789abcdef\tabi=oodlelz14")
        c = self.ctx(); w = dict(cache.ue5_words(c))
        self.assertEqual(w, {"KYTY_PIPELINE_LIST_CODE_STORE": c.dir + "/PPSA00001.codestore", "KYTY_UE5_DECODER": "0xABC123",
                             "KYTY_UE5_DECODER_FP": "0123456789abcdef", "KYTY_UE5_DECODER_ABI": "oodlelz14", "KYTY_LAUNCH_WAIT_BEFORE_ENTRY": "1", "KYTY_REFUSED_PROGRAMS": "1"})
        self.assertNotIn("KYTY_UE5_CODE", w)                                                    # the off switch is the player's, never a profile word
        # the same words reach the prepare run and the launch phase, nothing else adds them
        pw, lw = dict(cache.prepare_words(c, "/app0")), dict(cache.launch_words(c, "/app0"))
        for k, v in w.items():
            if k != "KYTY_LAUNCH_WAIT_BEFORE_ENTRY": self.assertEqual((pw[k], lw[k]), (v, v), k)   # incl. KYTY_REFUSED_PROGRAMS
        self.assertEqual(lw["KYTY_LAUNCH_WAIT_BEFORE_ENTRY"], "1")

    def test_ue5_row_needs_both_the_code_word_and_the_decoder_row(self):
        # a profile with only one half (the schema refuses it; the words must not appear even if one slipped through)
        self.set_cache_row("cache\tkind=kyplst\tdir=player\tapp_ver=01.001.000\nue5\tdecoder=0x1\tfp=0123456789abcdef\tabi=oodlelz14")
        c = self.ctx(); self.assertIsNone(cache.ue5_row(c.p)); self.assertEqual(cache.ue5_words(c), [])
        self.assertNotIn("KYTY_UE5_DECODER", dict(cache.launch_words(c, "/app0")))
        gp = os.path.join(self.tmp, "games", "PPSA00001", "game.tsv")
        keep = [l for l in open(gp).read().splitlines() if not l.startswith(("cache\t", "ue5\t"))]
        open(gp, "w").write("\n".join(keep + ["cache\tkind=kyplst\tdir=player\tcode=ue5\tapp_ver=01.001.000"]) + "\n")
        c = self.ctx(); self.assertIn("code=ue5", c.p.one("cache")); self.assertIsNone(cache.ue5_row(c.p)); self.assertEqual(cache.ue5_words(c), [])


if __name__ == "__main__":
    unittest.main()
