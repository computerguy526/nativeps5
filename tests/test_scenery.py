#!/usr/bin/env python3
"""Tests of scripts/nps5lib/scenery.py and its wiring in `nps5 run` (--scenery). SYNTHETIC level data only: invented model names,
no game content. Hash keys are computed here from the invented names."""
import importlib.util, json, os, re, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


S = load("nps5_scenery_t", "scripts/nps5lib/scenery.py")
run = load("nps5run_scen", "scripts/nps5lib/run.py")

def wr(path, text):
    with open(path, "w") as f: f.write(text)


U = lambda n: "%08X-0000-0000-0000-000000000000" % n


def obj(n, cls="bg", model=None, parent=None, extra="", nl="\r\n"):
    lines = ["\t\t<LevelObject>", "\t\t\t<Uuid>%s</Uuid>" % U(n), "\t\t\t<Name>|o%d</Name>" % n, "\t\t\t<Lox>%s</Lox>" % cls]
    if parent: lines.append("\t\t\t<Parent>%s</Parent>" % U(parent))
    lines += ["\t\t\t<Attributes>", '\t\t\t\t<Position type="Vector3">0 0 0</Position>']
    if model: lines.append('\t\t\t\t<Odx type="String">%s</Odx>' % model)
    if extra: lines.append(extra)
    lines += ["\t\t\t</Attributes>", "\t\t</LevelObject>"]
    return (nl.join(lines) + nl).encode()


def level(blocks, nl="\r\n"):
    return ("<Level>%s\t<LevelObjects>%s" % (nl, nl)).encode() + b"".join(blocks) + ("\t</LevelObjects>%s\t<Resources>%s\t</Resources>%s</Level>%s" % (nl, nl, nl, nl)).encode()


PRE_A, PRE_B = "fakebloom", "fakesward"          # invented prefixes
DROPS = [(len(PRE_A), S.name_hash(PRE_A)), (len(PRE_B), S.name_hash(PRE_B))]
ROW = dict(level="lv", preset="reduced", src="", cls="bg", drops=DROPS)


def sample(nl="\r\n"):
    return [obj(1, "coin", nl=nl), obj(2, model="fakebloom_a", nl=nl), obj(3, model="fakesward_b", nl=nl), obj(4, model="fakebush_a", nl=nl),
            obj(5, "dyn", model="fakebloom_c", nl=nl)]


class Rules(unittest.TestCase):
    def thin(self, blocks, row=ROW, nl="\r\n"):
        data = level(blocks, nl); out, n, k = S.thin(data, row); return data, out, n, k

    def test_removes_matching_bg_only(self):
        b = sample(); data, out, n, k = self.thin(b)
        self.assertEqual((n, k), (5, 2))
        self.assertEqual(out, data.replace(b[1], b"").replace(b[2], b""))

    def test_other_models_and_classes_kept(self):
        b = [obj(1, model="fakebush_a"), obj(2, "dyn", model="fakebloom_a"), obj(3, model=None)]
        data, out, n, k = self.thin(b); self.assertEqual((k, out), (0, data))

    def test_prefix_length_is_part_of_the_key(self):
        row = dict(ROW, drops=[(len(PRE_A) + 1, S.name_hash(PRE_A))])        # right hash, wrong length: no match
        data, out, n, k = self.thin(sample(), row); self.assertEqual(k, 0)
        self.assertFalse(S.matches("fake", DROPS))                             # shorter than the prefix

    def test_linked_object_kept(self):
        b = [obj(1, "trigger", extra='\t\t\t\t<T type="Link">%s</T>' % U(2)), obj(2, model="fakebloom_a"), obj(3, model="fakebloom_b")]
        data, out, n, k = self.thin(b); self.assertEqual(k, 1); self.assertIn(U(2).encode(), out); self.assertNotIn(U(3).encode(), out)

    def test_any_other_mention_keeps_object_but_own_prefab_root_does_not(self):
        own = '\t\t\t\t<RootUuid>%s</RootUuid>' % U(2)
        b = [obj(2, model="fakebloom_a", extra=own), obj(3, model="fakebloom_b", extra='<X>%s</X>' % U(3)), obj(4, "x", extra="<RootUuid>%s</RootUuid>" % U(3))]
        data, out, n, k = self.thin(b); self.assertEqual(k, 1); self.assertNotIn(U(2).encode(), out); self.assertIn(U(3).encode(), out)

    def test_kept_child_keeps_parent_and_removed_children_go_with_it(self):
        b = [obj(1, model="fakebloom_a"), obj(2, model="fakebush_a", parent=1),          # kept child: parent kept
             obj(3, model="fakebloom_b"), obj(4, model="fakesward_a", parent=3)]          # all removable: both go
        data, out, n, k = self.thin(b); self.assertEqual(k, 2)
        self.assertIn(U(1).encode(), out); self.assertNotIn(U(3).encode(), out); self.assertNotIn(U(4).encode(), out)
        S.check_output(data, out, n, k)

    def test_crlf_and_lf_preserved_and_order_kept(self):
        for nl in ("\r\n", "\n"):
            b = sample(nl); data, out, n, k = self.thin(b, nl=nl)
            self.assertEqual(out, data.replace(b[1], b"").replace(b[2], b""))
            self.assertEqual(out.count(b"\r\n"), 0 if nl == "\n" else out.count(b"\n"))
            self.assertLess(out.index(U(1).encode()), out.index(U(4).encode()))

    def test_empty_rule_is_identical_bytes_and_second_pass_changes_nothing(self):
        data = level(sample()); out, n, k = S.thin(data, dict(ROW, drops=[])); self.assertEqual((out, k), (data, 0))
        once, _, _ = S.thin(data, ROW); twice, _, k2 = S.thin(once, ROW); self.assertEqual((twice, k2), (once, 0))

    def test_output_check_catches_a_broken_result(self):
        data = level(sample())
        with self.assertRaises(S.Skip): S.check_output(data, data[:-20], 5, 0)           # truncated / not well formed
        with self.assertRaises(S.Skip): S.check_output(data, level(sample())[:], 5, 1)    # count mismatch
        dang = level([obj(1, model="x"), obj(2, model="y", parent=1)]); gone = level([obj(2, model="y", parent=1)])
        with self.assertRaises(S.Skip): S.check_output(dang, gone, 2, 1)                  # a Parent that no longer resolves

    def test_row_parser(self):
        good = ["lv", "reduced", "src=" + "a" * 64, "class=bg", "drop=%d:%s" % DROPS[0]]
        self.assertEqual(S.parse_row(good)["drops"], [DROPS[0]])
        for bad in (good[:4], ["lv", "full"] + good[2:], good[:2] + ["src=zz"] + good[3:], good[:4] + ["drop=3:xyz"], ["../x"] + good[1:], [".."] + good[1:], [".hid"] + good[1:], ["a/b"] + good[1:]):
            with self.assertRaises(ValueError): S.parse_row(bad)


class Generate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.app0 = os.path.join(self.d, "app0"); self.scratch = os.path.join(self.d, "s")
        self.rel = S.LEVEL_REL % "lv"; os.makedirs(os.path.dirname(os.path.join(self.app0, self.rel)))
        self.data = level(sample()); open(os.path.join(self.app0, self.rel), "wb").write(self.data)
        self.row = dict(ROW, src=S.sha256_bytes(self.data))

    def gen(self, row=None):
        return S.generate(self.app0, self.scratch, "PPSA00001", "reduced", [row or self.row])

    def test_generates_into_the_scratch_folder_and_never_writes_the_game(self):
        before = open(os.path.join(self.app0, self.rel), "rb").read()
        base, lines = self.gen()
        self.assertEqual(base, os.path.join(self.scratch, "scenery", "PPSA00001", "reduced"))
        out = open(os.path.join(base, self.rel), "rb").read()
        self.assertEqual(out.count(b"<LevelObject>"), 3); self.assertEqual(open(os.path.join(self.app0, self.rel), "rb").read(), before)
        self.assertIn("2 of 5", lines[0])
        files = [os.path.relpath(os.path.join(r, f), base) for r, _, fs in os.walk(base) for f in fs]
        self.assertEqual(files, [self.rel])                      # only the level file is in the folder the runtime mounts

    def test_second_call_reuses_and_a_damaged_output_is_regenerated(self):
        base, _ = self.gen(); p = os.path.join(base, self.rel); st = os.stat(p).st_mtime_ns
        self.assertIn("reused", self.gen()[1][0]); self.assertEqual(os.stat(p).st_mtime_ns, st)
        open(p, "ab").write(b" "); self.assertIn("generated", self.gen()[1][0])
        self.assertEqual(open(p, "rb").read().count(b"<LevelObject>"), 3)

    def test_changed_source_hash_writes_nothing_and_removes_a_stale_overlay(self):
        base, _ = self.gen(); p = os.path.join(base, self.rel)
        open(os.path.join(self.app0, self.rel), "wb").write(self.data + b"<!-- other version -->")
        with self.assertRaises(S.Skip) as c: self.gen()
        self.assertIn("not the file", str(c.exception)); self.assertFalse(os.path.exists(p))

    def test_unreadable_source_is_a_skip(self):
        os.unlink(os.path.join(self.app0, self.rel))
        with self.assertRaises(S.Skip): self.gen()
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery")) and os.path.exists(os.path.join(self.scratch, "scenery", "PPSA00001", "reduced", self.rel)))

    def test_failed_sanity_check_leaves_no_overlay(self):
        base, _ = self.gen(); p = os.path.join(base, self.rel)
        real = S.thin
        def bad(data, row): raise S.Skip("a Parent no longer resolves")
        S.thin = bad
        try:
            with self.assertRaises(S.Skip): self.gen(dict(self.row, drops=[DROPS[0]]))
        finally:
            S.thin = real
        self.assertFalse(os.path.exists(p))

    def test_changed_rules_regenerate(self):
        self.gen(); r2 = dict(self.row, drops=[DROPS[0]])
        base, lines = self.gen(r2); self.assertIn("generated", lines[0])
        self.assertEqual(open(os.path.join(base, self.rel), "rb").read().count(b"<LevelObject>"), 4)


GAME = """title_id\tPPSA00001
name\tFixture
build\t01.001\teboot=0123456789abcdef
package\t01.001
gpu\tkyty
canonical_lineage\tlin1
patches_default\ta b
env\tA_ALL=1
"""


class Wiring(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.app0 = os.path.join(self.d, "app0"); self.scratch = os.path.join(self.d, "s")
        rel = S.LEVEL_REL % "lv"; os.makedirs(os.path.dirname(os.path.join(self.app0, rel)))
        data = level(sample()); open(os.path.join(self.app0, rel), "wb").write(data)
        self.src = S.sha256_bytes(data); self.rel = rel
        for t, extra in (("PPSA00001", self.rows()), ("PPSA00002", "")):
            gd = os.path.join(self.d, "games", t); os.makedirs(gd)
            wr(os.path.join(gd, "game.tsv"), GAME.replace("PPSA00001", t) + extra)
            wr(os.path.join(gd, "scenes.tsv"), "sl\t-\t-\t-\t60\t-\td\n")
        os.makedirs(os.path.join(self.d, "titles"))
        self.env = dict(NPS5_GAMES_DIR=os.path.join(self.d, "games"), NPS5_SCRATCH=self.scratch, NPS5_PUBLIC_DIR=self.d,
                        NPS5_SELF_MEM_LIMIT_MB="18944", NPS5_KK_ICD="/kk/icd.json", NPS5_ANYPS5_BUILD="/any/build-arm64",
                        NPS5_KYTY_DIR="/kyty", NPS5_BREW_PREFIX="/brew")

    def rows(self):
        d = ",".join("%d:%s" % x for x in DROPS)
        return "".join("scenery\tlv\t%s\tsrc=%s\tclass=bg\tdrop=%s\n" % (p, S.sha256_bytes(level(sample())), d) for p in ("reduced", "minimal"))

    def titles(self, tid, text):
        wr(os.path.join(self.d, "titles", tid + ".env"), 'APP0="%s"\n%s' % (self.app0, text))

    def plan(self, argv, tid="PPSA00001", env=None, dry=False):
        t, opts = run.parse_args([tid] + argv)
        return run.compose(t, opts, dict(self.env, **(env or {})))

    def test_unset_and_full_add_nothing_and_equal_a_launch_without_the_feature(self):
        self.titles("PPSA00001", "")
        base = self.plan([]); full = self.plan(["--scenery", "full"]); viaenv = self.plan([], env={"NPS5_SCENERY": "full"})
        self.assertEqual(base.words(), full.words()); self.assertEqual(base.words(), viaenv.words())
        self.assertFalse(any(w.startswith(("NPS5_OVERLAY_DIR", "NPS5_SCENERY")) for w in base.words()))
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery"))); self.assertEqual(base.warnings, [])

    def test_reduced_sets_the_overlay_and_generates_it(self):
        self.titles("PPSA00001", "")
        pl = self.plan(["--scenery", "reduced"])
        od = pl.env["NPS5_OVERLAY_DIR"]; self.assertEqual(od, os.path.join(self.scratch, "scenery", "PPSA00001", "reduced"))
        self.assertTrue(os.path.isfile(os.path.join(od, self.rel))); self.assertNotIn("NPS5_SCENERY", pl.env)
        base = self.plan([]); self.assertEqual([w for w in pl.words() if not w.startswith("NPS5_OVERLAY_DIR")], base.words())

    def test_setting_from_environment_and_titles_env(self):
        self.titles("PPSA00001", "ENV=\"NPS5_SCENERY=minimal\"\n")
        self.assertIn("minimal", self.plan([]).env["NPS5_OVERLAY_DIR"])
        self.titles("PPSA00001", ""); self.assertIn("reduced", self.plan([], env={"NPS5_SCENERY": "reduced"}).env["NPS5_OVERLAY_DIR"])
        self.assertIn("minimal", self.plan(["--scenery", "minimal"], env={"NPS5_SCENERY": "reduced"}).env["NPS5_OVERLAY_DIR"])

    def test_own_overlay_dir_is_combined_not_refused(self):
        self.titles("PPSA00001", "")
        uo = os.path.join(self.d, "uo"); os.makedirs(os.path.join(uo, "cfg")); wr(os.path.join(uo, "cfg", "Engine.ini"), "x")
        pl = self.plan(["--scenery", "reduced", "--env", "NPS5_OVERLAY_DIR=" + uo])
        od = pl.env["NPS5_OVERLAY_DIR"]; self.assertNotEqual(od, uo)
        self.assertTrue(os.path.isfile(os.path.join(od, "cfg", "Engine.ini"))); self.assertTrue(os.path.isfile(os.path.join(od, self.rel)))
        self.assertEqual(self.plan([]).env.get("NPS5_OVERLAY_DIR"), None)           # without the setting nothing is touched
        self.titles("PPSA00001", 'ENV="NPS5_OVERLAY_DIR=/y"\n')
        self.assertEqual(self.plan([]).env["NPS5_OVERLAY_DIR"], "/y")

    def test_bad_value_refused(self):
        self.titles("PPSA00001", "")
        with self.assertRaises(run.Refuse) as c: self.plan(["--scenery", "lots"])
        self.assertEqual(c.exception.code, 2)

    def test_title_without_rows_is_unchanged(self):
        self.titles("PPSA00002", "")
        base = self.plan([], tid="PPSA00002"); pl = self.plan(["--scenery", "minimal"], tid="PPSA00002")
        self.assertEqual(pl.words(), base.words()); self.assertTrue(any("no scenery rows" in w for w in pl.warnings))

    def test_other_game_version_gets_no_overlay_and_a_warning(self):
        self.titles("PPSA00001", "")
        open(os.path.join(self.app0, self.rel), "ab").write(b" ")
        pl = self.plan(["--scenery", "reduced"]); self.assertNotIn("NPS5_OVERLAY_DIR", pl.env)
        self.assertEqual(sum("not applied" in w for w in pl.warnings), 1)

    def test_dry_run_writes_nothing(self):
        self.titles("PPSA00001", "")
        t, opts = run.parse_args(["PPSA00001", "--scenery", "reduced", "--dry-run"])
        pl = run.compose(t, opts, dict(self.env)); self.assertIn("NPS5_OVERLAY_DIR", pl.env)
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "scenery")))


class Profile(unittest.TestCase):
    """The shipped profile: rows parse, only the ASTRO profile has them, and no model name is written anywhere in the repo."""
    def test_rows_validate_and_other_profiles_have_none(self):
        found = []
        for t in sorted(os.listdir(os.path.join(REPO, "games"))):
            f = os.path.join(REPO, "games", t, "game.tsv")
            if not os.path.isfile(f): continue
            rows = [l.rstrip("\n").split("\t")[1:] for l in open(f) if l.startswith("scenery\t")]
            if t != "PPSA21564": self.assertEqual(rows, [], t)
            for r in rows: S.parse_row(r)
            if t == "PPSA21564": found = rows
        self.assertEqual(sorted(S.parse_row(r)["preset"] for r in found), ["minimal", "reduced"])

    def test_keep_word_parser(self):
        good = ["lv", "reduced", "src=" + "a" * 64, "class=bg", "drop=9:" + "b" * 16, "keep=20:" + "c" * 16]
        self.assertEqual(S.parse_row(good)["keeps"], [(20, "c" * 16)])
        self.assertEqual(S.parse_row(good[:5])["keeps"], [])
        self.assertEqual(S.parse_row(good[:5] + ["keep=3:" + "c" * 16 + ",12:" + "d" * 16])["keeps"], [(3, "c" * 16), (12, "d" * 16)])
        for bad in ("keep=", "keep=20:abc", "keep=0:" + "c" * 16, "keep=20:" + "C" * 16, "drop=20:" + "c" * 16, "keep=20:" + "c" * 16 + ",", "keep=x:" + "c" * 16):
            with self.assertRaises(ValueError, msg=bad):
                S.parse_row(good[:5] + [bad])
        with self.assertRaises(ValueError):
            S.parse_row(good + ["extra"])

    def test_keep_excludes_a_model_family_and_changes_the_rules_id(self):
        blocks = [obj(1, model="fakebloom_a"), obj(2, model="fakebloom_solid_a"), obj(3, model="fakebloom_solid_b"), obj(4, model="fakebush_a")]
        row = dict(ROW, keeps=[(len("fakebloom_solid"), S.name_hash("fakebloom_solid"))])
        out, n, k = S.thin(level(blocks), row)
        self.assertEqual(k, 1)                                        # only fakebloom_a goes
        self.assertEqual(out.count(b"fakebloom_solid"), 2)
        self.assertEqual(S.thin(level(blocks), ROW)[2], 3)               # without keep the family goes too
        self.assertNotEqual(S.rules_id([row]), S.rules_id([ROW]))

    def test_minimal_keeps_collision_objects(self):
        # v1.4.4: the moss family (19-char prefix key below) has collision bodies; no preset may drop it. minimal == reduced for now.
        f = open(os.path.join(REPO, "games", "PPSA21564", "game.tsv")).read().splitlines()
        rows = {r["preset"]: r for r in (S.parse_row(l.split("\t")[1:]) for l in f if l.startswith("scenery\t"))}
        self.assertEqual(rows["minimal"]["drops"], rows["reduced"]["drops"])
        self.assertEqual(rows["minimal"]["src"], rows["reduced"]["src"])
        for r in rows.values():
            self.assertNotIn((19, "9645c26dad834bc3"), r["drops"])
            self.assertIn((20, "e0e9ae7d18a3dd97"), r["keeps"])      # the grass_bump family (collision shapes) is kept by both presets
            self.assertNotIn((20, "e0e9ae7d18a3dd97"), r["drops"])

    LEVEL = os.environ.get("NPS5_TEST_APE_LEVEL", "")

    @unittest.skipUnless(bool(LEVEL) and os.path.isfile(LEVEL), "local game file not present (NPS5_TEST_APE_LEVEL)")
    def test_real_apes_level_removes_1058_and_keeps_the_collision_family(self):
        data = open(self.LEVEL, "rb").read()
        f = open(os.path.join(REPO, "games", "PPSA21564", "game.tsv")).read().splitlines()
        rows = [S.parse_row(l.split("\t")[1:]) for l in f if l.startswith("scenery\t")]
        for r in rows:
            if r["src"] != S.sha256_bytes(data):
                self.skipTest("another version of the level file")
            _, n, k = S.thin(data, r)
            self.assertEqual((n, k), (5203, 1058), r["preset"])        # was 1070 before the keep word: 12 collision objects stay
            objs, cand = S.plan(data, r["cls"], r["drops"], r["keeps"])
            self.assertFalse([o for o in objs if o["uuid"] in cand and o["model"] and S.name_hash(o["model"][:20]) == "e0e9ae7d18a3dd97"])

    def test_no_model_name_in_shipped_files(self):
        # the names stay out of the repository: scan the rows' files for readable name fragments of the kind the keys stand for
        bad = re.compile(r"veg_|apeescape_|_moss|flower_|grass_blade")
        for rel in ("scripts/nps5lib/scenery.py", "games/PPSA21564/game.tsv", "games/schema.tsv", "tests/test_scenery.py"):
            for i, l in enumerate(open(os.path.join(REPO, rel)), 1):
                if rel.endswith("test_scenery.py") and "bad = re.compile" in l: continue
                self.assertIsNone(bad.search(l), "%s:%d" % (rel, i))


if __name__ == "__main__":
    unittest.main()
