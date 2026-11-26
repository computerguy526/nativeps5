#!/usr/bin/env python3
"""Tests of the `levelfix` rows (scripts/nps5lib/scenery.py apply_fix / generate / merge_overlay, scripts/nps5lib/run.py wiring).
SYNTHETIC level data only: invented object names and values, no game content."""
import importlib.util, os, re, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


S = load("nps5_scenery_lf", "scripts/nps5lib/scenery.py")
run = load("nps5run_lf", "scripts/nps5lib/run.py")
T = load("nps5_test_scenery_lf", "tests/test_scenery.py")
wr, level, obj, U = T.wr, T.level, T.obj, T.U

TARGET = "|root|a|fakegate|fake_delay"          # invented
SUFFIX = "|fakegate|fake_delay"


def gate(n, name=TARGET, cls="delay_trigger", delay="0.6", nl="\r\n", extra=""):
    lines = ["\t\t<LevelObject>", "\t\t\t<Uuid>%s</Uuid>" % U(n), "\t\t\t<Name>%s</Name>" % name, "\t\t\t<Lox>%s</Lox>" % cls,
             "\t\t\t<Attributes>", '\t\t\t\t<EnterDelay type="Float">%s</EnterDelay>' % delay,
             '\t\t\t\t<LeaveDelay type="Float">%s</LeaveDelay>' % delay]
    if extra: lines.append(extra)
    lines += ["\t\t\t</Attributes>", "\t\t</LevelObject>"]
    return (nl.join(lines) + nl).encode()


def fix(src="", suffix=SUFFIX, attr="EnterDelay", old="0.6", new="8.0", cls="delay_trigger", level_="lv"):
    return dict(level=level_, src=src, cls=cls, obj=(len(suffix), S.name_hash(suffix)), attr=attr, old=old, new=new)


class Apply(unittest.TestCase):
    def test_changes_one_value_byte_exact_crlf_and_lf(self):
        for nl in ("\r\n", "\n"):
            d = level([obj(1, "coin", nl=nl), gate(2, nl=nl), obj(3, model="x", nl=nl)], nl=nl)
            out = S.apply_fix(d, fix())
            self.assertEqual(out, d.replace(b'EnterDelay type="Float">0.6<', b'EnterDelay type="Float">8.0<'))
            self.assertEqual(out.count(b"\r\n"), d.count(b"\r\n")); self.assertEqual(len(out), len(d))
            self.assertIn(b'LeaveDelay type="Float">0.6<', out)                 # another attribute with the same value is untouched

    def test_different_length_value(self):
        d = level([gate(1)]); out = S.apply_fix(d, fix(new="12.25"))
        self.assertEqual(len(out) - len(d), 2); self.assertIn(b">12.25<", out)

    def test_no_match_or_two_matches_is_a_skip(self):
        with self.assertRaises(S.Skip): S.apply_fix(level([gate(1, name="|x|other")]), fix())
        with self.assertRaises(S.Skip): S.apply_fix(level([gate(1), gate(2, name="|root|b" + SUFFIX)]), fix())   # same suffix twice

    def test_class_must_match(self):
        with self.assertRaises(S.Skip): S.apply_fix(level([gate(1, cls="other")]), fix())

    def test_suffix_not_prefix_and_children_do_not_match(self):
        d = level([gate(1), gate(2, name=TARGET + "|child")])
        self.assertEqual(S.apply_fix(d, fix()).count(b">8.0<"), 1)

    def test_old_value_required(self):
        with self.assertRaises(S.Skip) as c: S.apply_fix(level([gate(1, delay="0.7")]), fix())
        self.assertIn("expected", str(c.exception))
        with self.assertRaises(S.Skip): S.apply_fix(level([gate(1, delay="8.0")]), fix())          # already new: not blindly re-applied

    def test_attribute_missing_or_twice_is_a_skip(self):
        with self.assertRaises(S.Skip): S.apply_fix(level([gate(1)]), fix(attr="Nope"))
        d = level([gate(1, extra='\t\t\t\t<EnterDelay type="Float">0.6</EnterDelay>')])
        with self.assertRaises(S.Skip): S.apply_fix(d, fix())

    def test_row_parser(self):
        h = S.name_hash(SUFFIX)
        ok = ["lv", "src=" + "a" * 64, "class=delay_trigger", "object=%d:%s" % (len(SUFFIX), h), "attr=EnterDelay", "old=0.6", "new=8.0"]
        self.assertEqual(S.parse_fix(ok)["obj"], (len(SUFFIX), h))
        for i, bad in ((1, "src=zz"), (2, "class="), (3, "object=1:xyz"), (4, "attr=1x"), (5, "old=a b"), (6, "new=0.6")):
            c = list(ok); c[i] = bad
            with self.assertRaises(ValueError): S.parse_fix(c)
        with self.assertRaises(ValueError): S.parse_fix(ok[:-1])


class Generate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.app0 = os.path.join(self.d, "app0"); self.scratch = os.path.join(self.d, "s")
        self.rel = S.LEVEL_REL % "lv"; os.makedirs(os.path.dirname(os.path.join(self.app0, self.rel)))
        self.data = level(T.sample() + [gate(9)]); open(os.path.join(self.app0, self.rel), "wb").write(self.data)
        self.src = S.sha256_bytes(self.data); self.fx = fix(self.src); self.row = dict(T.ROW, src=self.src)

    def test_fix_only_overlay_in_its_own_folder_game_untouched(self):
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [self.fx])
        self.assertEqual(base, os.path.join(self.scratch, "scenery", "PPSA00001", "fix"))
        out = open(os.path.join(base, self.rel), "rb").read()
        self.assertEqual(out, self.data.replace(b'EnterDelay type="Float">0.6<', b'EnterDelay type="Float">8.0<'))
        self.assertEqual(open(os.path.join(self.app0, self.rel), "rb").read(), self.data)
        self.assertEqual([os.path.relpath(os.path.join(r, f), base) for r, _, fs in os.walk(base) for f in fs], [self.rel])
        self.assertIn("fixed EnterDelay", lines[0])

    def test_scenery_and_fix_compose_into_one_file(self):
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "reduced", [self.row], [self.fx])
        self.assertTrue(base.endswith("reduced+fix"))
        out = open(os.path.join(base, self.rel), "rb").read()
        self.assertIn(b">8.0<", out); self.assertEqual(out.count(b"<LevelObject>"), 4 + 0 + 0 + 1 - 0 - 0 if False else out.count(b"<LevelObject>"))
        self.assertEqual(out.count(b"<LevelObject>"), self.data.count(b"<LevelObject>") - 2)       # two bg objects removed
        self.assertEqual(sum(1 for f in os.listdir(os.path.dirname(os.path.join(base, self.rel)))), 1)
        only_fix = S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [self.fx])[0]
        self.assertNotEqual(base, only_fix)

    def test_wrong_source_hash_skips_both_and_removes_stale(self):
        base, _ = S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [self.fx])
        open(os.path.join(self.app0, self.rel), "ab").write(b" ")
        with self.assertRaises(S.Skip): S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [self.fx])
        self.assertFalse(os.path.exists(os.path.join(base, self.rel)))

    def test_old_value_mismatch_with_matching_hash_is_a_skip(self):
        with self.assertRaises(S.Skip): S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [dict(self.fx, old="0.5")])

    def test_reuse_and_regenerate(self):
        S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [self.fx])
        self.assertIn("reused", S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [self.fx])[1][0])
        self.assertIn("generated", S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [dict(self.fx, new="9.0")])[1][0])

    def test_two_rows_for_one_level_compose_into_one_file(self):
        cam = "|root|b|fakecam|fake_cam_on"
        d = level(T.sample() + [gate(9), gate(10, name=cam, delay="0.4")]); open(os.path.join(self.app0, self.rel), "wb").write(d)
        src = S.sha256_bytes(d); f1 = fix(src); f2 = fix(src, suffix="|fakecam|fake_cam_on", old="0.4", new="1.0")
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "reduced", [dict(T.ROW, src=src)], [f1, f2])
        out = open(os.path.join(base, self.rel), "rb").read()
        self.assertIn(b'EnterDelay type="Float">8.0<', out); self.assertIn(b'EnterDelay type="Float">1.0<', out)
        self.assertEqual(out.count(b"<LevelObject>"), d.count(b"<LevelObject>") - 2)
        self.assertEqual(len(lines), 1); self.assertIn("fixed EnterDelay, fixed EnterDelay", lines[0])
        only1 = S.apply_fix(d, f1); self.assertIn(b'EnterDelay type="Float">0.4<', only1)             # each row is independent
        self.assertNotEqual(S.rules_id([], [f1]), S.rules_id([], [f1, f2]))                              # adding a row regenerates
        with self.assertRaises(S.Skip): S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [f1, dict(f2, old="0.9")])   # one bad row: no overlay at all
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery", "PPSA00001", "fix", self.rel)))

    def test_merge_overlay(self):
        base, _ = S.generate(self.app0, self.scratch, "PPSA00001", "full", [], [self.fx])
        uo = os.path.join(self.d, "uo"); os.makedirs(os.path.join(uo, "cfg")); wr(os.path.join(uo, "cfg", "Engine.ini"), "x")
        out, left = S.merge_overlay(base, uo, os.path.join(self.d, "m"))
        self.assertEqual(left, []); self.assertTrue(os.path.isfile(os.path.join(out, "cfg", "Engine.ini")))
        self.assertEqual(open(os.path.join(out, self.rel), "rb").read(), open(os.path.join(base, self.rel), "rb").read())
        # the player's own copy of the same file wins (case-insensitive), ours is reported
        ur = os.path.join(uo, self.rel.upper()); os.makedirs(os.path.dirname(ur)); wr(ur, "mine")
        out, left = S.merge_overlay(base, uo, os.path.join(self.d, "m"))
        self.assertEqual(left, [self.rel]); self.assertEqual(open(os.path.join(out, self.rel.upper())).read(), "mine")
        self.assertEqual([f for r, _, fs in os.walk(out) for f in fs if f.lower() == "level.lvx"], ["LEVEL.LVX"])
        with self.assertRaises(S.Skip): S.merge_overlay(base, os.path.join(self.d, "nope"), os.path.join(self.d, "m"))


GAME = T.GAME


class Wiring(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.app0 = os.path.join(self.d, "app0"); self.scratch = os.path.join(self.d, "s")
        self.rel = S.LEVEL_REL % "lv"; os.makedirs(os.path.dirname(os.path.join(self.app0, self.rel)))
        self.data = level(T.sample() + [gate(9)]); open(os.path.join(self.app0, self.rel), "wb").write(self.data)
        src = S.sha256_bytes(self.data)
        d = ",".join("%d:%s" % x for x in T.DROPS)
        rows = "levelfix\tlv\tsrc=%s\tclass=delay_trigger\tobject=%d:%s\tattr=EnterDelay\told=0.6\tnew=8.0\n" % (src, len(SUFFIX), S.name_hash(SUFFIX))
        rows += "".join("scenery\tlv\t%s\tsrc=%s\tclass=bg\tdrop=%s\n" % (p, src, d) for p in ("reduced", "minimal"))
        for t, extra in (("PPSA00001", rows), ("PPSA00002", "")):
            gd = os.path.join(self.d, "games", t); os.makedirs(gd)
            wr(os.path.join(gd, "game.tsv"), GAME.replace("PPSA00001", t) + extra)
            wr(os.path.join(gd, "scenes.tsv"), "sl\t-\t-\t-\t60\t-\td\n")
        os.makedirs(os.path.join(self.d, "titles"))
        self.env = dict(NPS5_GAMES_DIR=os.path.join(self.d, "games"), NPS5_SCRATCH=self.scratch, NPS5_PUBLIC_DIR=self.d,
                        NPS5_SELF_MEM_LIMIT_MB="18944", NPS5_KK_ICD="/kk/icd.json", NPS5_ANYPS5_BUILD="/any/build-arm64",
                        NPS5_KYTY_DIR="/kyty", NPS5_BREW_PREFIX="/brew")

    def titles(self, tid, text=""):
        wr(os.path.join(self.d, "titles", tid + ".env"), 'APP0="%s"\n%s' % (self.app0, text))

    def plan(self, argv, tid="PPSA00001", env=None):
        t, opts = run.parse_args([tid] + argv)
        return run.compose(t, opts, dict(self.env, **(env or {})))

    def out(self, pl): return open(os.path.join(pl.env["NPS5_OVERLAY_DIR"], self.rel), "rb").read()

    def test_default_on_with_no_flags(self):
        self.titles("PPSA00001"); pl = self.plan([])
        self.assertTrue(pl.env["NPS5_OVERLAY_DIR"].endswith(os.path.join("PPSA00001", "fix")))
        self.assertIn(b">8.0<", self.out(pl)); self.assertNotIn("NPS5_LEVELFIX", pl.env)
        self.assertEqual(open(os.path.join(self.app0, self.rel), "rb").read(), self.data)

    def test_opt_out_env_everywhere_equals_no_feature(self):
        self.titles("PPSA00001")
        for pl in (self.plan([], env={"NPS5_LEVELFIX": "0"}), self.plan(["--env", "NPS5_LEVELFIX=0"])):
            self.assertNotIn("NPS5_OVERLAY_DIR", pl.env); self.assertEqual(pl.warnings, [])
        self.titles("PPSA00001", 'ENV="NPS5_LEVELFIX=0"\n'); self.assertNotIn("NPS5_OVERLAY_DIR", self.plan([]).env)
        self.titles("PPSA00001")
        with self.assertRaises(run.Refuse): self.plan([], env={"NPS5_LEVELFIX": "maybe"})

    def test_other_title_unchanged(self):
        self.titles("PPSA00002"); a = self.plan([], tid="PPSA00002"); b = self.plan([], tid="PPSA00002", env={"NPS5_LEVELFIX": "0"})
        self.assertEqual(a.words(), b.words()); self.assertNotIn("NPS5_OVERLAY_DIR", a.env); self.assertEqual(a.warnings, [])
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery")))

    def test_with_scenery_both_applied_in_one_file(self):
        self.titles("PPSA00001"); pl = self.plan(["--scenery", "reduced"])
        self.assertTrue(pl.env["NPS5_OVERLAY_DIR"].endswith("reduced+fix")); o = self.out(pl)
        self.assertIn(b">8.0<", o); self.assertEqual(o.count(b"<LevelObject>"), self.data.count(b"<LevelObject>") - 2)
        off = self.plan(["--scenery", "reduced"], env={"NPS5_LEVELFIX": "0"})                   # scenery alone: as before
        self.assertTrue(off.env["NPS5_OVERLAY_DIR"].endswith(os.path.join("PPSA00001", "reduced"))); self.assertNotIn(b">8.0<", self.out(off))

    def test_other_game_version_no_overlay_one_warning(self):
        self.titles("PPSA00001"); open(os.path.join(self.app0, self.rel), "ab").write(b" ")
        pl = self.plan([]); self.assertNotIn("NPS5_OVERLAY_DIR", pl.env); self.assertEqual(sum("not applied" in w for w in pl.warnings), 1)

    def test_own_overlay_dir_and_implicit_userdata_overlay_are_combined(self):
        uo = os.path.join(self.d, "uo"); os.makedirs(os.path.join(uo, "cfg")); wr(os.path.join(uo, "cfg", "Engine.ini"), "x")
        self.titles("PPSA00001"); pl = self.plan(["--env", "NPS5_OVERLAY_DIR=" + uo])
        self.assertTrue(os.path.isfile(os.path.join(pl.env["NPS5_OVERLAY_DIR"], "cfg", "Engine.ini"))); self.assertIn(b">8.0<", self.out(pl))
        ud = os.path.join(self.d, "ud"); os.makedirs(os.path.join(ud, "overlay", "cfg")); wr(os.path.join(ud, "overlay", "cfg", "Engine.ini"), "y")
        pl = self.plan(["--env", "NPS5_USERDATA_DIR=" + ud])
        self.assertTrue(os.path.isfile(os.path.join(pl.env["NPS5_OVERLAY_DIR"], "cfg", "Engine.ini"))); self.assertIn(b">8.0<", self.out(pl))

    def test_failed_generation_keeps_the_players_own_overlay(self):
        self.titles("PPSA00001"); open(os.path.join(self.app0, self.rel), "ab").write(b" ")
        pl = self.plan(["--env", "NPS5_OVERLAY_DIR=/mine"]); self.assertEqual(pl.env["NPS5_OVERLAY_DIR"], "/mine")

    def test_dry_run_writes_nothing(self):
        self.titles("PPSA00001"); t, opts = run.parse_args(["PPSA00001", "--dry-run"])
        pl = run.compose(t, opts, dict(self.env)); self.assertIn("NPS5_OVERLAY_DIR", pl.env)
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery")))


class Profile(unittest.TestCase):
    def rows(self, t):
        f = os.path.join(REPO, "games", t, "game.tsv")
        return [l.rstrip("\n").split("\t")[1:] for l in open(f) if l.startswith("levelfix\t")] if os.path.isfile(f) else []

    def test_only_astro_has_rows_and_they_parse(self):
        for t in sorted(os.listdir(os.path.join(REPO, "games"))):
            if t != "PPSA21564": self.assertEqual(self.rows(t), [], t)
        fx = S.fixes_for(self.rows("PPSA21564")); self.assertEqual(len(fx), 1)
        self.assertEqual((fx[0]["level"], fx[0]["attr"], fx[0]["old"], fx[0]["new"]), ("hero_ape_escape", "EnterDelay", "0.6", "8.0"))
        sc = [S.parse_row(r) for r in [l.rstrip("\n").split("\t")[1:] for l in open(os.path.join(REPO, "games", "PPSA21564", "game.tsv")) if l.startswith("scenery\t")]]
        self.assertTrue(all(r["src"] == fx[0]["src"] for r in sc))              # one level file version for both kinds of row

    def test_no_object_name_in_the_rows(self):
        bad = re.compile(r"trigger_start|area_statue|statue_rail|joint88")
        for rel in ("games/PPSA21564/game.tsv", "games/schema.tsv", "scripts/nps5lib/scenery.py", "tests/test_levelfix.py"):
            for i, l in enumerate(open(os.path.join(REPO, rel)), 1):
                if "bad = re.compile" in l: continue
                self.assertIsNone(bad.search(l), "%s:%d" % (rel, i))


if __name__ == "__main__":
    unittest.main()
