#!/usr/bin/env python3
"""Tests of the perf-mode wiring in `nps5 run` (settings balanced / performance / custom:, --scenery-keys, the live folder of links, the state-file
watcher, the table Kyty reads) and of scripts/nps5lib/scenery.py Live / Watcher. SYNTHETIC level data only: invented names, no game content."""
import importlib.util, json, os, sys, tempfile, threading, time, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


S = load("nps5_scenery_live", "scripts/nps5lib/scenery.py")
run = load("nps5run_live", "scripts/nps5lib/run.py")
T = load("nps5_test_scenery_live", "tests/test_scenery.py")
wr, level, obj, U, group = T.wr, T.level, T.obj, T.U, T.group

SUB = T.SUB
FLOWER, GRASS = ("fakebloom", "fakesward")          # invented prefixes
KEY_F = "%d:%s" % (len(FLOWER), S.name_hash(FLOWER))
KEY_G = "%d:%s" % (len(GRASS), S.name_hash(GRASS))


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.app0 = os.path.join(self.d, "app0"); self.scratch = os.path.join(self.d, "s")
        self.main = level([obj(1, "coin")] + [obj(10 + i, model="fakebloom_%d" % i) for i in range(10)] + [obj(40 + i, model="fakesward_%d" % i) for i in range(10)])
        self.sub = level([obj(2, model="fakebloom_a"), obj(3, model="fakesward_a"), obj(4, model="fakebloom_b"), group(9, [2, 3, 4])])
        self.files = {"level.lvx": self.main, SUB: self.sub}
        for rel, data in self.files.items():
            p = os.path.join(self.app0, S.LEVELS_REL % "lv", rel); os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f: f.write(data)
        h = lambda b: S.sha256_bytes(b)
        fk = S.file_key(SUB)
        rows = ""
        for cat, key in (("flowers", KEY_F), ("grass", KEY_G)):
            rows += "scenery\tlv\t%s\tsrc=%s\tclass=bg\tdrop=%s\n" % (cat, h(self.main), key)
            rows += "scenery\tlv\t%s\tsrc=%s\tclass=bg\tdrop=%s\tfile=%s\tstrip=suspend_group\n" % (cat, h(self.sub), key, fk)
        rows += "sceneryset\treduced\tflowers,grass\nsceneryset\tminimal\tflowers,grass\nsceneryset\tbalanced\tflowers=50,grass=50\n"
        rows += "sceneryset\tperformance\tflowers,grass,moss,other\n"
        rows += "drawskip\tlv\tflowers\tvs=%s\tps=-\n" % ("a" * 16)
        for t, extra in (("PPSA00001", rows), ("PPSA00002", "")):
            gd = os.path.join(self.d, "games", t); os.makedirs(gd)
            wr(os.path.join(gd, "game.tsv"), T.GAME.replace("PPSA00001", t) + extra)
            wr(os.path.join(gd, "scenes.tsv"), "sl\t-\t-\t-\t60\t-\td\n")
        os.makedirs(os.path.join(self.d, "titles"))
        self.env = dict(NPS5_GAMES_DIR=os.path.join(self.d, "games"), NPS5_SCRATCH=self.scratch, NPS5_PUBLIC_DIR=self.d,
                        NPS5_SELF_MEM_LIMIT_MB="18944", NPS5_KK_ICD="/kk/icd.json", NPS5_ANYPS5_BUILD="/any/build-arm64",
                        NPS5_KYTY_DIR="/kyty", NPS5_BREW_PREFIX="/brew")
        for t in ("PPSA00001", "PPSA00002"):
            wr(os.path.join(self.d, "titles", t + ".env"), 'APP0="%s"\n' % self.app0)

    def plan(self, argv, tid="PPSA00001", env=None):
        t, opts = run.parse_args([tid, "--tag", "fixed"] + argv)
        return run.compose(t, opts, dict(self.env, **(env or {})))

    def read(self, base, rel):
        with open(os.path.join(base, S.LEVELS_REL % "lv", rel), "rb") as f: return f.read()

    def count(self, data): return data.count(b"<LevelObject>")


class Settings(Base):
    def test_balanced_keeps_half_and_performance_removes_all(self):
        pl = self.plan(["--scenery", "balanced"]); od = pl.env["NPS5_OVERLAY_DIR"]
        self.assertTrue(od.endswith(os.path.join("PPSA00001", "balanced")))
        self.assertEqual(self.count(self.read(od, "level.lvx")), 1 + 5 + 5)
        pl = self.plan(["--scenery", "performance"]); od = pl.env["NPS5_OVERLAY_DIR"]
        self.assertEqual(self.count(self.read(od, "level.lvx")), 1)
        sub = self.read(od, SUB); self.assertEqual(self.count(sub), 1); self.assertIn(b'<Targets type="String">[]</Targets>', sub)

    def test_custom_flowers_only(self):
        pl = self.plan(["--scenery", "custom:flowers"]); od = pl.env["NPS5_OVERLAY_DIR"]
        self.assertTrue(od.endswith("custom-flowers")); self.assertEqual(self.count(self.read(od, "level.lvx")), 1 + 10)

    def test_reduced_and_minimal_use_the_sceneryset_rows(self):
        a = self.plan(["--scenery", "reduced"]).env["NPS5_OVERLAY_DIR"]; b = self.plan(["--scenery", "minimal"]).env["NPS5_OVERLAY_DIR"]
        self.assertEqual(self.read(a, "level.lvx"), self.read(b, "level.lvx"))

    def test_bad_settings_refused_exit_2(self):
        for bad in ("lots", "custom:", "custom:trees", "custom:flowers+"):
            with self.assertRaises(run.Refuse, msg=bad) as c: self.plan(["--scenery", bad])
            self.assertEqual(c.exception.code, 2)
        with self.assertRaises(run.Refuse) as c: self.plan(["--scenery-keys", "maybe"])
        self.assertEqual(c.exception.code, 2)

    def test_full_and_unset_equal_the_base_even_with_the_new_rows(self):
        base = self.plan([]); full = self.plan(["--scenery", "full"]); viaenv = self.plan([], env={"NPS5_SCENERY": "full", "NPS5_SCENERY_KEYS": "off"})
        self.assertEqual(base.words(), full.words()); self.assertEqual(base.words(), viaenv.words())
        self.assertFalse(any(w.startswith(("NPS5_OVERLAY_DIR", "NPS5_SCENERY", "KYTY_SCENE")) for w in base.words()))
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery"))); self.assertEqual(base.warnings, [])
        self.assertIsNone(base.live)

    def test_another_title_composes_to_the_same_words_with_every_setting(self):
        base = self.plan([], tid="PPSA00002")
        for argv in (["--scenery", "performance"], ["--scenery", "balanced"], ["--scenery-keys", "on"], ["--scenery", "custom:grass", "--scenery-keys", "on"]):
            pl = self.plan(argv, tid="PPSA00002"); self.assertEqual(pl.words(), base.words(), argv); self.assertIsNone(pl.live)
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery")))

    def test_another_games_file_is_left_alone_and_the_rest_written(self):
        with open(os.path.join(self.app0, S.LEVELS_REL % "lv", SUB), "ab") as f: f.write(b" ")
        pl = self.plan(["--scenery", "performance"]); od = pl.env["NPS5_OVERLAY_DIR"]
        self.assertTrue(os.path.isfile(os.path.join(od, S.LEVEL_REL % "lv"))); self.assertFalse(os.path.exists(os.path.join(od, S.LEVELS_REL % "lv", SUB)))
        self.assertEqual(sum("WARNING" in w for w in pl.warnings), 1)

    def test_extra_rows_file_adds_local_rows_and_refuses_other_kinds(self):
        wr(os.path.join(self.d, "extra.tsv"), "# local\nsceneryset\tbalanced\tgrass=50\n")
        pl = self.plan(["--scenery", "balanced"], env={"NPS5_SCENERY_EXTRA": os.path.join(self.d, "extra.tsv")})
        self.assertNotIn("NPS5_SCENERY_EXTRA", pl.env)
        wr(os.path.join(self.d, "bad.tsv"), "env\tA=1\n")
        with self.assertRaises(run.Refuse) as c: self.plan(["--scenery", "balanced"], env={"NPS5_SCENERY_EXTRA": os.path.join(self.d, "bad.tsv")})
        self.assertEqual(c.exception.code, 78)
        with self.assertRaises(run.Refuse): self.plan(["--scenery", "balanced"], env={"NPS5_SCENERY_EXTRA": os.path.join(self.d, "none.tsv")})


class Keys(Base):
    def live_target(self, pl, rel="level.lvx"):
        return os.path.realpath(os.path.join(pl.env["NPS5_OVERLAY_DIR"], S.LEVELS_REL % "lv", rel))

    def test_keys_off_by_default_adds_no_word_and_no_folder(self):
        pl = self.plan(["--scenery", "performance"])
        self.assertFalse(any(w.startswith("KYTY_SCENE") for w in pl.words())); self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery", "PPSA00001", "live")))
        self.assertIsNone(pl.live)

    def test_keys_on_with_full_links_to_the_players_own_files(self):
        pl = self.plan(["--scenery-keys", "on"])
        self.assertEqual(pl.env["NPS5_OVERLAY_DIR"], os.path.join(self.scratch, "scenery", "PPSA00001", "live"))
        self.assertEqual(pl.env["KYTY_SCENE_KEYS"], "1")
        self.assertEqual(os.path.dirname(pl.env["KYTY_SCENE_STATE_FILE"]), os.path.dirname(pl.env["KYTY_SCENE_SKIP_FILE"]))
        self.assertNotIn(pl.env["NPS5_OVERLAY_DIR"], pl.env["KYTY_SCENE_STATE_FILE"].replace("live.state", ""))
        for rel in ("level.lvx", SUB):
            self.assertEqual(self.live_target(pl, rel), os.path.realpath(os.path.join(self.app0, S.LEVELS_REL % "lv", rel)))
        self.assertIsNotNone(pl.live)
        base = self.plan([]); self.assertEqual([w for w in pl.words() if not w.startswith(("NPS5_OVERLAY_DIR", "KYTY_SCENE"))], base.words())

    def test_keys_on_with_a_preset_starts_thinned_and_writes_the_table(self):
        pl = self.plan(["--scenery", "performance", "--scenery-keys", "on"])
        self.assertEqual(self.count(open(self.live_target(pl), "rb").read()), 1)
        tb = open(pl.env["KYTY_SCENE_SKIP_FILE"]).read().splitlines()
        self.assertIn("start\tflowers=0,grass=0,moss=0,other=0", tb)
        self.assertIn("preset\tbalanced\tflowers=50,grass=50,moss=100,other=100", tb); self.assertIn("preset\tperformance\tflowers=0,grass=0,moss=0,other=0", tb)
        self.assertIn("skip\tlv\tflowers\t%s\t-" % ("a" * 16), tb)
        self.assertIn("level\tlv\tflowers=both,grass=entry", tb)       # help panel (key 4): flowers has a category row and a drawskip row, grass only the row
        self.assertLess(tb.index("preset\tbalanced\tflowers=50,grass=50,moss=100,other=100"), tb.index("preset\tperformance\tflowers=0,grass=0,moss=0,other=0"))

    def test_level_hows_for_the_f1_panel(self):
        pr = lambda lv, cat: S.parse_row([lv, cat, "src=" + "0" * 64, "class=bg", "drop=" + KEY_F])
        sk = lambda lv, cats: S.parse_skip([lv, cats, "vs=" + "a" * 16, "ps=-"])
        self.assertEqual(S.level_hows([], []), {})
        self.assertEqual(S.level_hows([pr("l1", "moss"), pr("l1", "moss"), pr("l2", "other")], [sk("l1", "moss+grass"), sk("l3", "flowers")]),
                         {"l1": {"moss": "both", "grass": "instant"}, "l2": {"other": "entry"}, "l3": {"flowers": "instant"}})
        # legacy rows (no category) are not a key's business
        self.assertEqual(S.level_hows([S.parse_row(["l9", "reduced", "src=" + "0" * 64, "class=bg", "drop=" + KEY_F])], []), {})
        # the table text: sorted levels, categories in the fixed order, nothing for a table without rows
        path = os.path.join(self.d, "t.tsv")
        S.write_skip_table(path, [], {c: 100 for c in S.CATS}, [sk("l3", "flowers")], [pr("l2", "other"), pr("l1", "moss"), pr("l1", "flowers")])
        tb = open(path).read().splitlines()
        self.assertEqual([l for l in tb if l.startswith("level")], ["level\tl1\tflowers=entry,moss=entry", "level\tl2\tother=entry", "level\tl3\tflowers=instant"])
        S.write_skip_table(path, [], {c: 100 for c in S.CATS})
        self.assertFalse([l for l in open(path).read().splitlines() if l.startswith("level")])

    def test_keys_need_kyty_and_category_rows(self):
        pl = self.plan(["--scenery-keys", "on", "--gpu", "anyps5"])
        self.assertFalse(any(w.startswith("KYTY_SCENE") for w in pl.words())); self.assertTrue(any("need the Kyty" in w for w in pl.warnings))
        self.assertNotIn("NPS5_OVERLAY_DIR", pl.env)

    def test_keys_by_environment(self):
        pl = self.plan([], env={"NPS5_SCENERY_KEYS": "1"}); self.assertEqual(pl.env["KYTY_SCENE_KEYS"], "1"); self.assertNotIn("NPS5_SCENERY_KEYS", pl.env)

    def test_dry_run_sets_the_words_and_writes_nothing(self):
        t, opts = run.parse_args(["PPSA00001", "--scenery-keys", "on", "--dry-run"])
        pl = run.compose(t, opts, dict(self.env)); self.assertEqual(pl.env["KYTY_SCENE_KEYS"], "1")
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery")))

    def test_players_own_overlay_wins_and_links_stay_live(self):
        uo = os.path.join(self.d, "uo"); os.makedirs(os.path.join(uo, "data", "x")); wr(os.path.join(uo, "data", "x", "mine.txt"), "m")
        pl = self.plan(["--scenery-keys", "on", "--env", "NPS5_OVERLAY_DIR=" + uo])
        merged = pl.env["NPS5_OVERLAY_DIR"]; self.assertNotEqual(merged, uo)
        self.assertTrue(os.path.isfile(os.path.join(merged, "data", "x", "mine.txt")))
        live, state = pl.live
        live.repoint({"flowers": 0, "grass": 0})
        self.assertEqual(self.count(open(os.path.join(merged, S.LEVEL_REL % "lv"), "rb").read()), 1)        # the merged link follows the swap
        # a player file at the same path wins over the generated one
        os.makedirs(os.path.join(uo, S.LEVELS_REL % "lv")); wr(os.path.join(uo, S.LEVEL_REL % "lv"), "player")
        pl2 = self.plan(["--scenery-keys", "on", "--env", "NPS5_OVERLAY_DIR=" + uo])
        self.assertEqual(open(os.path.join(pl2.env["NPS5_OVERLAY_DIR"], S.LEVEL_REL % "lv")).read(), "player")
        self.assertTrue(any("WARNING: your overlay has" in w for w in pl2.warnings))


class LiveWatch(Base):
    def make(self, fixes=False):
        t, opts = run.parse_args(["PPSA00001", "--scenery-keys", "on", "--tag", "fixed"])
        pl = run.compose(t, opts, dict(self.env)); return pl, pl.live[0], pl.live[1]

    def link(self, live, rel="level.lvx"): return live.link_path("lv", rel)

    def n(self, live, rel="level.lvx"):
        with open(self.link(live, rel), "rb") as f: return self.count(f.read())

    def test_repoint_swaps_between_variants_and_back(self):
        pl, live, _ = self.make(); self.assertEqual(self.n(live), 1 + 20)
        ch, lines = live.repoint({"flowers": 0}); self.assertEqual(ch, 2); self.assertEqual(self.n(live), 1 + 10)
        live.repoint({"flowers": 0, "grass": 0}); self.assertEqual(self.n(live), 1)
        self.assertEqual(self.count(open(self.link(live, SUB), "rb").read()), 1)
        live.repoint({"flowers": 50}); self.assertEqual(self.n(live), 1 + 5 + 10)
        live.repoint({}); self.assertEqual(self.n(live), 1 + 20)
        self.assertEqual(os.path.realpath(self.link(live)), os.path.realpath(os.path.join(self.app0, S.LEVEL_REL % "lv")))
        self.assertEqual(live.repoint({})[0], 0)                                                       # nothing changed, no swap

    def test_variant_folder_names(self):
        self.assertEqual(S.Live.variant({}), "full"); self.assertEqual(S.Live.variant({"grass": 100}), "full")
        self.assertEqual(S.Live.variant({"flowers": 0, "grass": 50}), "cats-flowers+grass~50"); self.assertEqual(S.Live.variant({"other": 0}), "cats-other")

    def test_a_failing_file_points_at_the_players_own_file(self):
        pl, live, _ = self.make()
        with open(os.path.join(self.app0, S.LEVELS_REL % "lv", SUB), "ab") as f: f.write(b" ")
        live.repoint({"flowers": 0}); self.assertEqual(self.n(live), 1 + 10)
        self.assertEqual(os.path.realpath(self.link(live, SUB)), os.path.realpath(os.path.join(self.app0, S.LEVELS_REL % "lv", SUB)))

    def test_reader_never_sees_a_missing_link_during_swaps(self):
        pl, live, _ = self.make(); path = self.link(live); stop = threading.Event(); errs = []; reads = [0]
        def reader():
            while not stop.is_set():
                try:
                    with open(path, "rb") as f: f.read()
                    reads[0] += 1
                except OSError as e: errs.append(e)
        th = threading.Thread(target=reader); th.start()
        try:
            for i in range(150): live.repoint([{"flowers": 0}, {"grass": 0}, {}, {"flowers": 0, "grass": 0}][i % 4])
        finally:
            stop.set(); th.join()
        self.assertEqual(errs, []); self.assertGreater(reads[0], 50)

    def test_swap_link_is_atomic_and_idempotent(self):
        d = tempfile.mkdtemp(); a, b = os.path.join(d, "a"), os.path.join(d, "b"); wr(a, "A"); wr(b, "B"); ln = os.path.join(d, "sub", "ln")
        self.assertEqual(S.swap_link(ln, a), 1); self.assertEqual(S.swap_link(ln, a), 0); self.assertEqual(S.swap_link(ln, b), 1)
        self.assertEqual(open(ln).read(), "B"); self.assertEqual([f for f in os.listdir(os.path.dirname(ln)) if f.startswith(".lnk")], [])

    def test_state_file_with_a_new_seq_repoints_once(self):
        pl, live, state = self.make(); logs = []; w = S.Watcher(live, state, logs.append)
        self.assertIsNone(w.poll())                                                                       # no state file yet
        wr(state, "seq=1\npreset=custom\nflowers=0\ngrass=100\nmoss=100\nother=100\n"); self.assertEqual(w.poll()["seq"], 1)
        self.assertEqual(self.n(live), 1 + 10); self.assertTrue(any("seq 1 staged" in l for l in logs))
        self.assertIsNone(w.poll())                                                                       # same content: nothing
        wr(state, "seq=1\nflowers=0\ngrass=0\n"); self.assertIsNone(w.poll())                            # same seq, other content: not a new press
        wr(state, "seq=2\nflowers=0\ngrass=0\n"); w.poll(); self.assertEqual(self.n(live), 1)

    def test_bad_state_is_ignored_with_one_warning(self):
        pl, live, state = self.make(); logs = []; w = S.Watcher(live, state, logs.append)
        for bad in ("garbage", "seq=x\n", "seq=1\nflowers=500\n", "seq=1\nflowers\n"):
            wr(state, bad); self.assertIsNone(w.poll()); self.assertIsNone(w.poll())
        self.assertEqual(sum("ignored" in l for l in logs), 4); self.assertEqual(self.n(live), 1 + 20)
        wr(state, "seq=3\nflowers=0\n"); self.assertEqual(w.poll()["seq"], 3)

    def test_parse_state(self):
        r = S.parse_state("# c\nseq=7\npreset=balanced\nflowers=50\ngrass=100\n"); self.assertEqual((r["seq"], r["preset"], r["mask"]), (7, "balanced", {"flowers": 50, "grass": 100}))
        self.assertEqual(S.parse_state("seq=1")["preset"], "custom")

    def test_thread_picks_up_a_press_and_stops(self):
        pl, live, state = self.make(); logs = []; w = S.Watcher(live, state, logs.append, interval=0.02).start()
        try:
            wr(state, "seq=1\nflowers=0\ngrass=0\n")
            end = time.time() + 5
            while time.time() < end and self.n(live) != 1: time.sleep(0.02)
            self.assertEqual(self.n(live), 1)
        finally:
            w.stop()

    def test_start_removes_a_stale_state_file(self):
        pl, live, state = self.make(); wr(state, "seq=9\nflowers=0\n"); w = S.Watcher(live, state, lambda m: None, interval=5).start(); w.stop()
        self.assertFalse(os.path.exists(state))

    def test_level_fix_is_in_every_variant_including_nothing_off(self):
        data = self.main
        fx = dict(level="lv", src=S.sha256_bytes(data), cls="coin", obj=(3, S.name_hash("|o1")), attr="Position", old="0 0 0", new="1 0 0")
        rows = [S.parse_row(["lv", "flowers", "src=" + S.sha256_bytes(data), "class=bg", "drop=" + KEY_F])]
        live = S.Live(self.app0, self.scratch, "PPSA00001", rows, [fx])
        for mask, n in (({}, 1 + 20), ({"flowers": 0}, 1 + 10)):
            live.repoint(mask)
            with open(live.link_path("lv", "level.lvx"), "rb") as f: out = f.read()
            self.assertIn(b">1 0 0<", out); self.assertEqual(self.count(out), n)
        self.assertIn("cats-flowers+fix", os.path.realpath(live.link_path("lv", "level.lvx")))


if __name__ == "__main__":
    unittest.main()
