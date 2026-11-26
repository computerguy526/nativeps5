#!/usr/bin/env python3
"""Fixture tests for games/game.py. Synthetic profiles are written to a temp dir; no real title is read."""
import importlib.util, os, sys, tempfile, unittest, io, contextlib
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("game", os.path.join(HERE, "..", "games", "game.py"))
game = importlib.util.module_from_spec(spec); spec.loader.exec_module(game)

GAME = """# fixture
title_id\tPPSA00001
name\tFixture Game
build\t01.001\teboot=0123456789abcdef\tsize=10
build\t01.002\teboot=fedcba9876543210
package\t01.001
gpu\tkyty
env\tA_ALL=1
env\tA_KYTY=1\tgpu=kyty\twhy
env\tA_ANY=1\tgpu=anyps5
env\tA_OLD=1\tbuild=01.001
env\tA_NEW=1\tbuild=01.002
env\tA_SCENE=1\tscene=hoover
env\tA_LVL=1\tscene=level:*
env\tA_PERF=1\tclass=perf
env\tA_ALL=2\tgpu=kyty
env_forbidden\tBAD_VAR=on\tkills the machine
lineage\tbase\trecomp=abc1234\tshift=0x0\nmarker\tworldmap\topen:levels/worldmap/
direct_mem_mib\t1024
stage\tS3
fact.note\tanything at all
"""
SCENES = """title\t-\t-\tmarker:worldmap\t90\tnew\tTitle
hoover\t-skipIntro -lvl hoover\tplay.vpad\tscene:hoover\t120/180\tnew\tLevel
level:*\t-skipIntro -lvl {name}\t20000:tap=cross\tscene:{name}\t150/180\tnew\tAny level
"""


def make(root, tid="PPSA00001", game_tsv=GAME, scenes=SCENES, extra=None):
    d = os.path.join(root, tid); os.makedirs(os.path.join(d, "pad"), exist_ok=True)
    open(os.path.join(d, "game.tsv"), "w").write(game_tsv)
    if scenes is not None: open(os.path.join(d, "scenes.tsv"), "w").write(scenes)
    open(os.path.join(d, "pad", "play.vpad"), "w").write("0:tap=cross\n")
    for k, v in (extra or {}).items(): open(os.path.join(d, k), "w").write(v)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = self.tmp.name
        self.schema = game.load_schema()
    def tearDown(self): self.tmp.cleanup()
    def prof(self, **kw):
        return game.Profile(make(self.root, **kw))
    def check(self, **kw):
        p = self.prof(**kw); return game.check_profile(p, self.schema)
    def has(self, msgs, text): return any(text in m for m in msgs)


class CheckTests(Base):
    def test_valid(self):
        errs, warns = self.check()
        self.assertEqual(errs, [])

    def test_cache_kinds(self):
        # uewarmC: kind=journal is valid (with dir=player), kind=foo is still refused
        for row, ok in (("cache\tkind=journal\n", True), ("cache\tkind=kyplst\tdir=player\tlimit_mb=448\n", True), ("cache\tkind=none\n", True),
                        ("cache\tkind=foo\n", False), ("cache\tkind=journal\tdir=elsewhere\n", False)):
            errs, _ = self.check(game_tsv=GAME + row)
            self.assertEqual(errs == [], ok, (row, errs))

    UE5 = "ue5\tdecoder=0x926510\tfp=1f82e44a65371db3\tabi=oodlelz14\n"
    UE5C = "cache\tkind=kyplst\tdir=player\tcode=ue5\tapp_ver=01.005.002\tlimit_mb=448\n"

    def test_ue5_rows(self):
        # synthetic values only; the schema accepts a good pair and refuses every malformed or half-present one
        ok = ((self.UE5C + self.UE5, True), (self.UE5C, False), (self.UE5, False),
              ("cache\tkind=kyplst\tcode=ue5\n" + self.UE5, False), ("cache\tkind=journal\tdir=player\tcode=ue5\n" + self.UE5, False),
              (self.UE5C + "cache\tkind=none\n", False),
              (self.UE5C + self.UE5.replace("0x926510", "926510"), False), (self.UE5C + self.UE5.replace("1f82e44a65371db3", "1f82e4"), False),
              (self.UE5C + self.UE5.replace("oodlelz14", "oodle"), False), (self.UE5C + "ue5\tdecoder=0x1\tfp=1f82e44a65371db3\n", False),
              (self.UE5C + self.UE5 + self.UE5, False), (self.UE5C.replace("code=ue5", "code=ue4") + self.UE5, False))
        for rows, good in ok:
            errs, _ = self.check(game_tsv=GAME + rows)
            self.assertEqual(errs == [], good, (rows, errs))

    def test_saros_cache_rows(self):
        p = game.Profile(os.path.join(game.REPO, "games", "PPSA07631"))
        self.assertEqual(p.one("cache"), "kind=journal")
        rows = {r[0] for r in p.get("env")}
        for w in ("ASTROHYBRID_CACHE_TITLE_ID=PPSA07631", "KYTY_PIPELINE_CACHE_SAVE_INTERVAL_S=300", "KYTY_PIPELINE_CACHE_SAVE_QUIET_S=5",
                  "KYTY_PIPELINE_CACHE_SAVE_MIN_NEW=64", "KYTY_PIPELINE_CACHE_SAVE_ON_TERM=1"):
            self.assertIn(w, rows)
        self.assertFalse([w for w in rows if w.startswith("KK_METAL_CACHE_ID")])

    def test_missing_required(self):
        errs, _ = self.check(game_tsv=GAME.replace("gpu\tkyty\n", ""))
        self.assertTrue(self.has(errs, "missing required key 'gpu'"))

    def test_unknown_key(self):
        errs, _ = self.check(game_tsv=GAME + "mystery\t1\n")
        self.assertTrue(self.has(errs, "unknown key 'mystery'"))

    def test_bad_types(self):
        for line, msg in (("direct_mem_mib\tabc\n", "decimal"), ("window\t1920\n", "<width>x<height>"),
                          ("guest_bias\t10\n", "hex"), ("stage\tS9\n", "S0..S8"), ("gpu_profile_ban\tmaybe\n", "on|off")):
            errs, _ = self.check(game_tsv=GAME + line)
            self.assertTrue(self.has(errs, msg), (line, errs))
        errs, _ = self.check(game_tsv=GAME.replace("eboot=0123456789abcdef", "eboot=xyz"))
        self.assertTrue(self.has(errs, "eboot"))

    def test_duplicate_single_key(self):
        errs, _ = self.check(game_tsv=GAME + "name\tSecond\n")
        self.assertTrue(self.has(errs, "may appear once"))

    def test_bad_condition(self):
        errs, _ = self.check(game_tsv=GAME + "env\tX=1\tmoon=full\n")
        self.assertTrue(self.has(errs, "bad condition"))
        errs, _ = self.check(game_tsv=GAME + "env\tX=1\tgpu=vulkan\n")
        self.assertTrue(self.has(errs, "gpu= must be"))

    def test_absolute_path_rejected(self):
        errs, _ = self.check(game_tsv=GAME + "fact.where\t/Users/someone/dump\n")
        self.assertTrue(self.has(errs, "absolute"))
        errs, _ = self.check(game_tsv=GAME + "preset\t/etc/x.env\n")
        self.assertTrue(self.has(errs, "relative"))

    def test_env_row_hits_forbidden(self):
        errs, _ = self.check(game_tsv=GAME + "env\tBAD_VAR=on\n")
        self.assertTrue(self.has(errs, "refused by env_forbidden"))

    def test_mem_limit_refused(self):
        errs, _ = self.check(game_tsv=GAME + "env\tNPS5_SELF_MEM_LIMIT_MB=24000\n")
        self.assertTrue(self.has(errs, "18944"))

    def test_title_id_folder_mismatch_and_package(self):
        errs, _ = self.check(game_tsv=GAME.replace("title_id\tPPSA00001", "title_id\tPPSA00002").replace("package\t01.001", "package\t09.9"))
        self.assertTrue(self.has(errs, "does not match folder"))
        self.assertTrue(self.has(errs, "package 09.9"))

    def test_scene_checks(self):
        errs, _ = self.check(scenes="hoover\t-\tmissing.vpad\tscene:hoover\t90\tnew\tx\n")
        self.assertTrue(self.has(errs, "pad file pad/missing.vpad"))
        errs, _ = self.check(scenes="a\t-\t-\tmarker:nope\t90\tnew\tx\n")
        self.assertTrue(self.has(errs, "marker 'nope'"))
        errs, _ = self.check(scenes="a\t-\t-\tsometime\t90\tnew\tx\n")
        self.assertTrue(self.has(errs, "bad until"))
        errs, _ = self.check(scenes="a\t/Users/x/a\t-\tpic\t90\tnew\tx\n")
        self.assertTrue(self.has(errs, "absolute"))

    def test_bench_smoke_scene_reference(self):
        bench = "b1\thoover\tmarker:worldmap+15\tperf\tok\nb2\tgone\tmarker:worldmap+15\tperf\tbad\nb3\tlevel:ice\t5s..9s\tperf\ttemplate\n"
        errs, _ = self.check(extra={"bench.tsv": bench})
        self.assertEqual(len([e for e in errs if "bench.tsv" in e]), 1)
        self.assertTrue(self.has(errs, "scene 'gone' is not defined"))
        errs, _ = self.check(extra={"bench.tsv": "b1\thoover\tmarker:worldmap+15\tperf\tok\n", "smoke.tsv": "s1\ttitle\tquick\tmarker:worldmap@60\t18000\t500\tok\n"})
        self.assertEqual(errs, [])

    def test_stage_evidence(self):
        errs, _ = self.check(game_tsv=GAME.replace("stage\tS3", "stage\tS8"))
        for need in ("bench.tsv missing", "smoke.tsv missing", "no full row"):
            self.assertTrue(self.has(errs, need), need)

    def test_patches_builds_cross_check(self):
        errs, _ = self.check(extra={"patches.py": "BUILDS = {'01.001': '0123456789abcdef', '03.1': 'aaaaaaaaaaaaaaaa'}\ndef main():\n    pass\n"})
        self.assertTrue(self.has(errs, "BUILDS version 03.1"))
        errs, _ = self.check(extra={"patches.py": "BUILDS = {'01.001': 'ffffffffffffffff'}\n"})
        self.assertTrue(self.has(errs, "differs"))
        for bad in ("''", "'0123'", "5"):           # an empty or short prefix is a prefix of every build ID
            errs, _ = self.check(extra={"patches.py": "BUILDS = {'01.001': %s}\n" % bad})
            self.assertTrue(self.has(errs, "at least 16 lowercase hex"), bad)
        errs, _ = self.check(extra={"patches.py": "BUILDS = [1]\n"}); self.assertTrue(self.has(errs, "cannot parse"))

    def test_smoke_reach_is_an_until_spec(self):
        for bad, need in (("nonsense@60", "bad reach target"), ("marker:nomarker@60", "is not defined"), ("marker:worldmap@0", "at least 1")):
            errs, _ = self.check(extra={"smoke.tsv": "s1\ttitle\tquick\t%s\t18000\t500\tok\n" % bad})
            self.assertTrue(self.has(errs, need), bad)

    def test_validated_rows_are_checked(self):
        ok = "# c\nfull\tkyty=329f43a6 kk=8110779 ps5recomp=ce4c7dd lineage=kyty6+dr date=2026-11-17 class=m4pro-24g machine=Mac16,8 macos=26.6.2\n" \
             "quick\tanyps5=abcdef1 kk=0123456789+89abcdef ps5recomp=ce4c7dd date=2026-11-17\n"
        errs, _ = self.check(extra={"validated.tsv": ok}); self.assertEqual(errs, [])
        for bad, need in (("full kyty=1234567 kk=1234567 ps5recomp=1234567 date=2026-11-17\n", "needs <quick|full>"),
                          ("fast\tkyty=1234567 kk=1234567 ps5recomp=1234567 date=2026-11-17\n", "needs <quick|full>"),
                          ("full\tkk=1234567 ps5recomp=1234567 date=2026-11-17\n", "exactly one of"),
                          ("full\tkyty=1234567 anyps5=1234567 kk=1234567 ps5recomp=1234567 date=2026-11-17\n", "exactly one of"),
                          ("full\tkyty=unknown kk=1234567 ps5recomp=1234567 date=2026-11-17\n", "must be commits"),
                          ("full\tkyty=1234567 kk=1234567 ps5recomp=1234567 date=yesterday\n", "date=")):
            errs, _ = self.check(extra={"validated.tsv": bad}); self.assertTrue(self.has(errs, need), bad)


class EnvTests(Base):
    def env(self, **kw):
        p = self.prof(); d = {k: v for k, v, _ in game.compose_env(p, **kw)}; return d

    def test_layers_and_defaults(self):
        d = self.env()
        self.assertEqual(d["NPS5_TITLE_ID"], "PPSA00001"); self.assertEqual(d["PS4_DIRECT_MEM_SIZE"], "1024m")
        self.assertEqual(d["A_ALL"], "2"); self.assertIn("A_KYTY", d); self.assertNotIn("A_ANY", d)
        self.assertIn("A_OLD", d); self.assertNotIn("A_NEW", d)          # package build 01.001
        self.assertNotIn("A_SCENE", d); self.assertNotIn("A_PERF", d)    # inputs not given

    def test_gpu_build_scene_class(self):
        d = self.env(gpu="anyps5"); self.assertIn("A_ANY", d); self.assertNotIn("A_KYTY", d); self.assertEqual(d["A_ALL"], "1")
        d = self.env(build="01.002"); self.assertIn("A_NEW", d); self.assertNotIn("A_OLD", d)
        d = self.env(build="fedcba98"); self.assertIn("A_NEW", d)       # eboot prefix
        d = self.env(scene="hoover"); self.assertIn("A_SCENE", d); self.assertNotIn("A_LVL", d)
        d = self.env(scene="level:ice"); self.assertIn("A_LVL", d); self.assertNotIn("A_SCENE", d)
        d = self.env(cls="perf"); self.assertIn("A_PERF", d)

    def test_errors(self):
        p = self.prof()
        with self.assertRaises(game.Err): game.compose_env(p, scene="nope")
        with self.assertRaises(game.Err): game.compose_env(p, build="77.7")

    def test_forbidden_exit_78(self):
        p = self.prof(game_tsv=GAME + "env\tBAD_VAR=on\n")
        with self.assertRaises(game.Err) as c: game.compose_env(p)
        self.assertEqual(c.exception.code, 78)
        p = self.prof(game_tsv=GAME + "env\tBAD_VAR=off\n")      # other value is fine
        game.compose_env(p)
        p = self.prof(game_tsv=GAME.replace("BAD_VAR=on", "BAD_VAR") + "env\tBAD_VAR=whatever\n")  # key-only rule
        with self.assertRaises(game.Err) as c: game.compose_env(p)
        self.assertEqual(c.exception.code, 78)

    def test_mem_limit_78(self):
        p = self.prof(game_tsv=GAME + "env\tNPS5_SELF_MEM_LIMIT_MB=30000\n")
        with self.assertRaises(game.Err) as c: game.compose_env(p)
        self.assertEqual(c.exception.code, 78)

    def test_preset_and_framerate_block(self):
        extra = GAME + "preset\ttitles/kyty-preset.env\nframerate_block\t0x10\tbuild=01.002\nframerate_block\t0x20\tbuild=01.001\n"
        p = self.prof(game_tsv=extra); d = {k: v for k, v, _ in game.compose_env(p)}
        self.assertEqual(d["NPS5_FRAMERATE_BLOCK"], "0x20")
        self.assertIn("KYTY_PIPELINE_JOURNAL", d) if os.path.isfile(os.path.join(game.REPO, "titles/kyty-preset.env")) else None


class AstroProfileTests(unittest.TestCase):
    """The shipped ASTRO BOT profile (real file): the v1.4 depth-compression word is on for Kyty only and a later --env row can switch it off."""
    def compose(self, **kw):
        p = game.Profile(os.path.join(game.REPO, "games", "PPSA21564"))
        return {k: v for k, v, _ in game.compose_env(p, **kw)}

    def test_depth_feedback_usage_off_for_kyty_only(self):
        self.assertEqual(self.compose(gpu="kyty")["KYTY_DEPTH_FEEDBACK_USAGE"], "0")
        self.assertNotIn("KYTY_DEPTH_FEEDBACK_USAGE", self.compose(gpu="anyps5"))

    def test_best_run_words_not_in_the_profile(self):
        d = self.compose(gpu="kyty")
        for k in ("KYTY_MEMTRIM_BIG_WIDTH", "KYTY_FLIGHTREC", "NPS5_QOS_LOG"):   # test-rig words stay out of the shipped env
            self.assertNotIn(k, d)
        self.assertEqual(d["NPS5_QOS_GPU"], "rt")


class AstroPairRowTests(unittest.TestCase):
    """folship: the v1.4 foliage / skinned-bone rows (evidence autoval,folship) in the ASTRO Metal pair list."""
    def rows(self):
        p = os.path.join(game.REPO, "games", "PPSA21564", "metal-gfx-pairs.tsv")
        return [l.split("\t") for l in open(p).read().splitlines() if l.startswith("gfx\t")]

    def test_folship_rows_exact_and_faster(self):
        fs = [r for r in self.rows() if r[14] == "autoval,folship"]
        self.assertEqual(len(fs), 14)  # 16 before the shipgate drop
        for r in fs:
            self.assertEqual(r[18], "faster"); self.assertIn(r[17], ("exact", "outprec:1"))  # relabel56: D9-class rows are outprec:1
            self.assertIn(r[16], ("g16", "g17", "g18"))
            self.assertLess(float(r[19]), 1.0)
            self.assertRegex(r[5] + r[6], r"^[0-9a-f]{32}$")
        self.assertEqual({c: sum(1 for r in fs if r[16] == c) for c in ("g16", "g17", "g18")}, {"g16": 3, "g17": 7, "g18": 4})

    def test_folship_rows_do_not_shadow_other_rows(self):
        rows = self.rows()
        old = {(r[5], r[6], r[16]) for r in rows if r[14] != "autoval,folship"}
        for r in rows:
            if r[14] == "autoval,folship":
                self.assertNotIn((r[5], r[6], r[16]), old | {(r[5], r[6], "*")})

    def test_valfix_dropped_rows_stay_out(self):
        """valfix: no row may carry the text of a row removed after the level-profile check (hashes in tests/data/valfix_dropped.tsv)."""
        p = os.path.join(HERE, "data", "valfix_dropped.tsv")
        gone = {tuple(l.split("\t")[2:4]) for l in open(p).read().splitlines() if l and not l.startswith("#")}
        self.assertEqual(len(gone), 117)
        for r in self.rows():
            self.assertNotIn((r[7], r[8]), gone)

    def test_shipgate_dropped_rows_stay_out_and_lists_are_consistent(self):
        """v1.4 fin2: the 32 texts with a shipgate FAIL (tier SHIPGATE) do not ship; the guest sidecar holds no key of a removed row; the deny list holds every guest."""
        p = os.path.join(HERE, "data", "valfix_dropped.tsv")
        sg = [l.split("\t") for l in open(p).read().splitlines() if l and not l.startswith("#")]
        sg = [r for r in sg if r[6] == "SHIPGATE"]
        self.assertEqual(len({(r[2], r[3]) for r in sg}), 32)
        shipped = {(r[7], r[8]) for r in self.rows()}
        for r in sg:
            self.assertNotIn((r[2], r[3]), shipped)
        d = os.path.join(game.REPO, "games", "PPSA21564")
        deny = {l.strip() for l in open(os.path.join(d, "fast-math-deny.txt")) if l.strip() and not l.startswith("#")}
        keys = {("vs", r[5]) for r in self.rows()} | {("fs", r[6]) for r in self.rows()}
        for l in open(os.path.join(d, "metal-guests.tsv")).read().splitlines():
            f = l.split("\t")
            if len(f) == 3 and f[0] in ("vs", "fs"):
                self.assertIn((f[0], f[1]), keys)
            if len(f) == 3 and f[0] in ("cs", "fs"):
                self.assertIn(f[2], deny)

    def test_relabel56_d9_rows_are_outprec1(self):
        """relabel56: the 56 D9-class rows ship as outprec:1 (never exact); none of the 33 P7-only dropped rows ships."""
        p = os.path.join(HERE, "data", "relabel56.tsv")
        keys = {tuple(l.split("\t")) for l in open(p).read().splitlines() if l and not l.startswith("#")}
        self.assertEqual(len(keys), 56)
        rows = self.rows()
        got = {(r[5], r[6], r[15], r[16]): r[17] for r in rows}
        for k in keys:
            self.assertEqual(got.get(k), "outprec:1", k)
        p7 = [l.split("\t") for l in open(os.path.join(HERE, "data", "valfix_dropped.tsv")).read().splitlines()
              if l and not l.startswith("#")]
        p7 = [r for r in p7 if r[6] == "P7"]
        self.assertEqual(len(p7), 33)
        shipped = {(r[5], r[6], r[15], r[16]) for r in rows}
        for r in p7:
            self.assertNotIn((r[0], r[1], r[4], r[5]), shipped)
            self.assertNotIn((r[2], r[3]), {(x[7], x[8]) for x in rows})

    def test_verified_record_matches_row_count(self):
        d = os.path.join(game.REPO, "games", "PPSA21564")
        rec = open(os.path.join(d, "metal-gfx-pairs.tsv.verified")).read()
        self.assertIn("reproduced=%d of %d" % (len(self.rows()), len(self.rows())), rec)


class SceneTests(Base):
    def test_lookup(self):
        p = self.prof(); rows = p.scenes()
        r = game.scene_dict(game.scene_lookup(rows, "hoover")); self.assertEqual(r["args"], "-skipIntro -lvl hoover")
        r = game.scene_dict(game.scene_lookup(rows, "level:ice_iceberg"))
        self.assertEqual((r["args"], r["until"]), ("-skipIntro -lvl ice_iceberg", "scene:ice_iceberg"))
        self.assertIsNone(game.scene_lookup(rows, "nope")); self.assertIsNone(game.scene_lookup(rows, "level:*"))
        self.assertIsNone(game.scene_lookup(rows, "other:x"))


class SearchPathTests(Base):
    def setUp(self):
        Base.setUp(self)
        self.prov = os.path.join(self.root, "prov"); os.makedirs(self.prov)

    def test_repo_only_and_provisional_new_title(self):
        make(self.prov, tid="PPSA00009")
        found = game.find_all({"NPS5_GAMES_DIR": self.prov})
        self.assertIn("PPSA00009", found); d, prov = found["PPSA00009"][0]; self.assertTrue(prov)
        self.assertNotIn("PPSA00009", game.find_all({}))

    def test_override_precedence(self):
        # the repo games/ folder is stood in for by REPO; shadow a real repo id from the provisional folder
        repo_ids = list(game.find_all({}))
        if not repo_ids: self.skipTest("no repo profile to shadow")
        tid = repo_ids[0]; make(self.prov, tid=tid, game_tsv=GAME.replace("PPSA00001", tid).replace("Fixture Game", "Shadow"))
        env = {"NPS5_GAMES_DIR": self.prov}
        d, prov = game.find_profile(tid, env); self.assertFalse(prov)                       # repo first by default
        d, prov = game.find_profile(tid, dict(env, NPS5_GAMES_FIRST="1")); self.assertTrue(prov)  # provisional first
        self.assertEqual(game.Profile(d, prov).one("name"), "Shadow")
        self.assertEqual(len(game.find_all(env)[tid]), 2)

    def test_two_provisional_dirs_order(self):
        a = os.path.join(self.root, "a"); b = os.path.join(self.root, "b")
        make(a, tid="PPSA00007", game_tsv=GAME.replace("PPSA00001", "PPSA00007").replace("Fixture Game", "A"))
        make(b, tid="PPSA00007", game_tsv=GAME.replace("PPSA00001", "PPSA00007").replace("Fixture Game", "B"))
        d, _ = game.find_profile("PPSA00007", {"NPS5_GAMES_DIR": a + ":" + b})
        self.assertEqual(game.Profile(d).one("name"), "A")

    def test_unknown_id(self):
        with self.assertRaises(game.Err): game.find_profile("PPSA99999", {})


class CliTests(Base):
    def run_cli(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = game.main(argv, env)
        return rc, out.getvalue(), err.getvalue()

    def test_cli(self):
        make(self.root); env = {"NPS5_GAMES_DIR": self.root}
        rc, out, _ = self.run_cli(["list"], env); self.assertEqual(rc, 0); self.assertIn("PPSA00001", out); self.assertIn("provisional", out)
        rc, out, _ = self.run_cli(["PPSA00001", "gpu"], env); self.assertEqual((rc, out.strip()), (0, "kyty"))
        rc, out, _ = self.run_cli(["PPSA00001", "nokey"], env); self.assertEqual(rc, 1)
        rc, out, _ = self.run_cli(["PPSA00001", "--env", "--scene", "hoover"], env); self.assertIn("A_SCENE=1", out)
        rc, out, _ = self.run_cli(["PPSA00001", "--scene", "level:x"], env); self.assertIn("args=-skipIntro -lvl x", out)
        rc, out, _ = self.run_cli(["check", "PPSA00001"], env); self.assertEqual(rc, 0, out)
        rc, _, err = self.run_cli(["PPSA00002", "gpu"], env); self.assertEqual(rc, 2)
        rc, _, _ = self.run_cli([], env); self.assertEqual(rc, 64)

    def test_schema_loads_every_validator(self):
        for key, s in self.schema.items():
            self.assertIn(s["type"], game.VALIDATORS, key)


if __name__ == "__main__":
    unittest.main()
