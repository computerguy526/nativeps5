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


def golden_blocks(nl="\r\n"):
    """A fixed synthetic level that exercises every legacy rule at once (removable, linked, kept child, own prefab root, other class)."""
    return [obj(1, "coin", nl=nl), obj(2, model="fakebloom_a", nl=nl), obj(3, model="fakesward_b", nl=nl), obj(4, model="fakebush_a", nl=nl),
            obj(5, "dyn", model="fakebloom_c", nl=nl),
            obj(6, "trigger", extra='\t\t\t\t<T type="Link">%s</T>' % U(7), nl=nl), obj(7, model="fakebloom_d", nl=nl),
            obj(8, model="fakebloom_e", extra="\t\t\t\t<RootUuid>%s</RootUuid>" % U(8), nl=nl),
            obj(9, model="fakebloom_f", nl=nl), obj(10, model="fakebush_b", parent=9, nl=nl),
            obj(11, model="fakesward_c", nl=nl), obj(12, model="fakesward_d", parent=11, nl=nl)] + [obj(20 + i, model="fakesward_%d" % i, nl=nl) for i in range(30)]


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
        real = S.thin_rows
        def bad(data, rules, info=None): raise S.Skip("a Parent no longer resolves")
        S.thin_rows = bad
        try:
            with self.assertRaises(S.Skip): self.gen(dict(self.row, drops=[DROPS[0]]))
        finally:
            S.thin_rows = real
        self.assertFalse(os.path.exists(p))

    def test_changed_rules_regenerate(self):
        self.gen(); r2 = dict(self.row, drops=[DROPS[0]])
        base, lines = self.gen(r2); self.assertIn("generated", lines[0])
        self.assertEqual(open(os.path.join(base, self.rel), "rb").read().count(b"<LevelObject>"), 4)


# ---------------------------------------------------------------- perf mode: categories, sub-level files, stripping, keep percent
def group(n, ids, nl="\r\n", extra=""):
    """A streaming group (class suspend_group) with a <Targets> list in the authors' style: json list, ', ' separator."""
    return obj(n, "suspend_group", extra='\t\t\t\t<Targets type="String">%s</Targets>' % json.dumps([U(i) for i in ids]) + (nl + extra if extra else ""), nl=nl)


def crow(cat, drops, **kw):
    return dict(level="lv", preset=None, cat=cat, src="", cls="bg", drops=drops, file=None, strip=False, keep=0, **kw)


GOLDEN_CRLF = "19339771ed4aa1f0a4ae119aa05c8afa2aa42b7e03e0d499e3adbe11202b12dc"       # sha256 of the v1.4.3 module's output for golden_blocks()
GOLDEN_LF = "b5c4a5f136084d29d601b3ba8f11958786eaf21964a8e4389009f4c0062cce52"


class Legacy(unittest.TestCase):
    def test_legacy_rows_give_the_bytes_of_v143(self):
        for nl, want in (("\r\n", GOLDEN_CRLF), ("\n", GOLDEN_LF)):
            out, n, k = S.thin(level(golden_blocks(nl), nl), ROW)
            self.assertEqual((n, k, S.sha256_bytes(out)), (42, 35, want))

    def test_legacy_row_parses_to_the_old_fields(self):
        r = S.parse_row(["lv", "minimal", "src=" + "a" * 64, "class=bg", "drop=%d:%s" % DROPS[0]])
        self.assertEqual((r["preset"], r["cat"], r["file"], r["strip"], r["keep"]), ("minimal", None, None, False, 0))

    def test_category_rules_in_one_pass_equal_the_legacy_row(self):
        data = level(golden_blocks())
        a = S.thin(data, ROW)[0]
        b = S.thin_rows(data, [crow("flowers", [DROPS[0]]), crow("grass", [DROPS[1]])])[0]
        self.assertEqual(a, b)


class NewForms(unittest.TestCase):
    GOOD = ["lv", "flowers", "src=" + "a" * 64, "class=bg", "drop=%d:%s" % DROPS[0]]

    def test_category_row(self):
        r = S.parse_row(self.GOOD + ["file=39:" + "b" * 16, "strip=suspend_group"])
        self.assertEqual((r["cat"], r["preset"], r["file"], r["strip"]), ("flowers", None, "39:" + "b" * 16, True))
        self.assertEqual((S.parse_row(self.GOOD)["file"], S.parse_row(self.GOOD)["strip"]), (None, False))

    def test_eight_bad_category_rows(self):
        bad = [self.GOOD[:4], self.GOOD + ["file=zz"], self.GOOD + ["file=39:" + "b" * 16, "file=39:" + "c" * 16], self.GOOD + ["strip=yes"],
               self.GOOD + ["strip=suspend_group", "strip=suspend_group"], self.GOOD + ["drop=1:" + "a" * 16], ["../x"] + self.GOOD[1:],
               self.GOOD[:2] + ["src=" + "A" * 64] + self.GOOD[3:], self.GOOD[:3] + ["class=b g"] + self.GOOD[4:], ["lv", "trees"] + self.GOOD[2:]]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)): S.parse_row(b)

    def test_legacy_form_takes_no_extra_words(self):
        with self.assertRaises(ValueError): S.parse_row(["lv", "reduced", "src=" + "a" * 64, "class=bg", "drop=%d:%s" % DROPS[0], "strip=suspend_group"])

    def test_sceneryset(self):
        r = S.parse_set(["balanced", "flowers=50,grass=50"]); self.assertEqual(dict(r["cats"]), {"flowers": 50, "grass": 50})
        self.assertEqual(dict(S.parse_set(["performance", "flowers,grass,moss,other"])["cats"]), dict(flowers=0, grass=0, moss=0, other=0))
        for bad in (["full", "flowers"], ["balanced"], ["balanced", "trees"], ["balanced", "flowers,flowers"], ["balanced", "flowers=100"], ["balanced", "flowers=5x"], ["lots", "grass"]):
            with self.assertRaises(ValueError, msg=str(bad)): S.parse_set(bad)

    def test_drawskip(self):
        r = S.parse_skip(["lv", "grass+flowers", "vs=" + "a" * 16, "ps=-"]); self.assertEqual((r["cats"], r["ps"]), (("flowers", "grass"), None))
        self.assertEqual(S.parse_skip(["lv", "moss", "vs=" + "a" * 16, "ps=" + "b" * 16])["ps"], "b" * 16)
        for bad in (["lv", "grass", "vs=" + "a" * 16], ["lv", "grass+grass", "vs=" + "a" * 16, "ps=-"], ["lv", "trees", "vs=" + "a" * 16, "ps=-"],
                    ["lv", "grass", "vs=xyz", "ps=-"], ["lv", "grass", "vs=" + "a" * 16, "ps=12"], ["a/b", "grass", "vs=" + "a" * 16, "ps=-"]):
            with self.assertRaises(ValueError, msg=str(bad)): S.parse_skip(bad)

    def test_setting_grammar(self):
        for ok in ("full", "reduced", "minimal", "balanced", "performance", "custom:flowers", "custom:flowers+grass+moss+other"):
            self.assertTrue(S.valid_setting(ok), ok)
        for bad in ("lots", "custom:", "custom:trees", "custom:flowers+", "Custom:flowers", ""):
            self.assertFalse(S.valid_setting(bad), bad)

    def test_effective_rows_and_masks(self):
        rows = [S.parse_row(["lv", "reduced", "src=" + "a" * 64, "class=bg", "drop=%d:%s" % DROPS[0]]),
                dict(crow("flowers", [DROPS[0]]), level="b"), dict(crow("grass", [DROPS[1]]), level="b"), dict(crow("moss", [DROPS[1]]), level="b"),
                dict(crow("flowers", [DROPS[0]]), level="lv")]
        sets = [S.parse_set(["balanced", "flowers=50,grass=50"]), S.parse_set(["performance", "flowers,grass,moss"]), S.parse_set(["reduced", "flowers,grass"])]
        self.assertEqual(S.effective_rows(rows, sets, "full"), [])
        red = S.effective_rows(rows, sets, "reduced")                       # legacy row of lv wins for lv; category rows for b
        self.assertEqual([(r["level"], r["cat"]) for r in red], [("lv", None), ("b", "flowers"), ("b", "grass")])
        bal = S.effective_rows(rows, sets, "balanced"); self.assertEqual({(r["level"], r["cat"], r["keep"]) for r in bal}, {("b", "flowers", 50), ("b", "grass", 50), ("lv", "flowers", 50)})
        self.assertEqual(len(S.effective_rows(rows, sets, "performance")), 4)
        cu = S.effective_rows(rows, sets, "custom:moss"); self.assertEqual([(r["level"], r["cat"]) for r in cu], [("b", "moss")])
        self.assertEqual(S.preset_mask(sets, "balanced"), {"flowers": 50, "grass": 50}); self.assertEqual(S.preset_mask(sets, "full"), {})


class Strip(unittest.TestCase):
    def row(self, strip=True):
        return dict(crow("flowers", [DROPS[0]]), strip=strip)

    def thin(self, blocks, nl="\r\n", strip=True, info=None):
        data = level(blocks, nl); out, n, k = S.thin_rows(data, [self.row(strip)], info); return data, out, n, k

    def targets(self, out):
        return [json.loads(m.group(2).decode()) for m in S.TARGETS.finditer(out)]

    def test_listed_only_in_targets_is_removed_and_leaves_the_list(self):
        for nl in ("\r\n", "\n"):
            for ids, gone, want in (([2, 3, 4], 2, [3, 4]), ([2, 3, 4], 3, [2, 4]), ([2, 3, 4], 4, [2, 3]), ([3], 3, [])):
                # only the object `gone` is a flower; the others are kept bushes
                blocks = [obj(n, model=("fakebloom_a" if n == gone else "fakebush_a"), nl=nl) for n in (2, 3, 4)] + [group(9, ids, nl)]
                data, out, n, k = self.thin(blocks, nl)
                self.assertEqual(k, 1); self.assertNotIn(U(gone).encode(), out)
                self.assertEqual(self.targets(out), [[U(i) for i in want]])
                self.assertEqual(out.count(b"\r\n"), 0 if nl == "\n" else out.count(b"\n"))

    def test_without_strip_a_targets_mention_keeps_the_object(self):
        blocks = [obj(2, model="fakebloom_a"), group(9, [2])]
        data, out, n, k = self.thin(blocks, strip=False); self.assertEqual((k, out), (0, data))

    def test_targets_text_keeps_its_bytes_when_nothing_leaves(self):
        blocks = [obj(2, model="fakebush_a"), obj(3, model="fakebloom_a"), group(9, [2])]
        data, out, n, k = self.thin(blocks); self.assertEqual(k, 1)
        self.assertIn(json.dumps([U(2)]).encode(), out)

    def test_other_references_still_keep_it(self):
        for extra in ('\t\t\t\t<Blockers type="String">["%s"]</Blockers>' % U(2), '\t\t\t\t<T type="Link">%s</T>' % U(2)):
            blocks = [obj(2, model="fakebloom_a"), obj(5, "trigger", extra=extra), group(9, [2])]
            data, out, n, k = self.thin(blocks); self.assertEqual(k, 0, extra); self.assertEqual(out, data)

    def test_blockers_inside_the_group_object_itself_keep_it(self):
        blocks = [obj(2, model="fakebloom_a"), group(9, [2], extra='\t\t\t\t<Blockers type="String">["%s"]</Blockers>' % U(2))]
        data, out, n, k = self.thin(blocks); self.assertEqual(k, 0)

    def test_mention_in_a_non_group_targets_does_not_count(self):
        blocks = [obj(2, model="fakebloom_a"), obj(5, "other_class", extra='\t\t\t\t<Targets type="String">["%s"]</Targets>' % U(2))]
        data, out, n, k = self.thin(blocks); self.assertEqual(k, 0)

    def test_two_mentions_in_one_list_still_strip(self):
        blocks = [obj(2, model="fakebloom_a"), group(9, [2, 2])]
        data, out, n, k = self.thin(blocks); self.assertEqual(k, 1); self.assertEqual(self.targets(out), [[]])

    def test_empty_group_is_kept_and_counted(self):
        info = {}
        data, out, n, k = self.thin([obj(2, model="fakebloom_a"), group(9, [2])], info=info)
        self.assertEqual((k, info["emptied"]), (1, 1)); self.assertIn(U(9).encode(), out); self.assertEqual(self.targets(out), [[]])
        info2 = {}; S.thin_rows(data, [self.row()], info2)                                  # a group that was empty before is not counted
        data2, out2, n2, k2 = self.thin([group(9, [])], info=info2); self.assertEqual(info2["emptied"], 0)

    def test_removed_child_and_parent_leave_the_list_and_nothing_dangles(self):
        blocks = [obj(2, model="fakebloom_a"), obj(3, model="fakebloom_b", parent=2), group(9, [2, 3])]
        data, out, n, k = self.thin(blocks); self.assertEqual(k, 2)
        for i in (2, 3): self.assertNotIn(U(i).encode(), out)
        S.check_output(data, out, n, k, {U(2), U(3)})

    def test_a_kept_child_keeps_the_parent_and_its_list_entry(self):
        blocks = [obj(2, model="fakebloom_a"), obj(3, model="fakebush_a", parent=2), group(9, [2])]
        data, out, n, k = self.thin(blocks); self.assertEqual((k, out), (0, data))

    def test_check_output_refuses_a_removed_id_left_behind_and_a_bad_list(self):
        data = level([obj(2, model="fakebloom_a"), group(9, [2])]); out = data.replace(obj(2, model="fakebloom_a"), b"")
        with self.assertRaises(S.Skip): S.check_output(data, out, 2, 1, {U(2)})               # the id is still in Targets
        bad = data.replace(json.dumps([U(2)]).encode(), b"[oops")
        with self.assertRaises(S.Skip): S.check_output(data, bad.replace(obj(2, model="fakebloom_a"), b""), 2, 1)

    def test_unparseable_targets_is_a_skip_when_it_must_be_rewritten(self):
        g = obj(9, "suspend_group", extra='\t\t\t\t<Targets type="String">not json %s</Targets>' % U(2))
        with self.assertRaises(S.Skip): S.thin_rows(level([obj(2, model="fakebloom_a"), g]), [self.row()])

    def test_second_pass_changes_nothing(self):
        data = level([obj(2, model="fakebloom_a"), obj(3, model="fakebush_a"), group(9, [2, 3])])
        once, _, _ = S.thin_rows(data, [self.row()]); twice, _, k = S.thin_rows(once, [self.row()]); self.assertEqual((twice, k), (once, 0))


class KeepPercent(unittest.TestCase):
    def thin(self, n, keep, extra=()):
        blocks = [obj(100 + i, model="fakebloom_%d" % i) for i in range(n)] + list(extra)
        data = level(blocks); out, total, k = S.thin_rows(data, [dict(crow("flowers", [DROPS[0]]), keep=keep)]); return data, out, total, k

    def test_half_removes_half_plus_or_minus_one_and_is_stable(self):
        for n in (1000, 7, 2, 1):
            data, out, total, k = self.thin(n, 50)
            self.assertLessEqual(abs(k - n / 2.0), 1, n)
            data2, out2, total2, k2 = self.thin(n, 50); self.assertEqual(out, out2)

    def test_other_percentages_and_extremes(self):
        self.assertEqual(self.thin(100, 30)[3], 70); self.assertEqual(self.thin(100, 0)[3], 100); self.assertEqual(self.thin(100, 99)[3], 1)
        self.assertEqual(self.thin(40, 100)[3], 0)

    def test_the_kept_ones_are_the_same_whatever_else_is_in_the_file(self):
        _, a, _, _ = self.thin(60, 50)
        _, b, _, _ = self.thin(60, 50, extra=[obj(5, model="fakebush_a")])
        kept = lambda o: {x for x in re.findall(rb"<Uuid>(.*?)</Uuid>", o) if int(x[:8], 16) >= 100}
        self.assertEqual(kept(a), kept(b))

    def test_a_kept_child_still_keeps_its_parent(self):
        blocks = [obj(200 + i, model="fakebloom_%d" % i) for i in range(10)] + [obj(300, model="fakebush_a", parent=205)]
        data = level(blocks); out, n, k = S.thin_rows(data, [dict(crow("flowers", [DROPS[0]]), keep=0)])
        self.assertEqual(k, 9); self.assertIn(U(205).encode(), out)


def lvl_app0(files):
    """app0 with a level folder `lv` holding the given {relative path: bytes}."""
    d = tempfile.mkdtemp(); app0 = os.path.join(d, "app0")
    for rel, data in files.items():
        p = os.path.join(app0, S.LEVELS_REL % "lv", rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f: f.write(data)
    return d, app0


SUB = "0A1B2C3D-0000-0000-0000-00000000AAAA.lvx"
SUB2 = "0A1B2C3D-0000-0000-0000-00000000BBBB.lvx"


class MultiFile(unittest.TestCase):
    def setUp(self):
        self.main = level([obj(1, "coin"), obj(2, model="fakebloom_a"), obj(3, model="fakesward_a")])
        self.sub = level([obj(2, model="fakebloom_a"), obj(3, model="fakesward_a"), obj(4, model="fakebloom_b"), group(9, [2, 3, 4])])
        self.sub2 = level([obj(2, model="fakebloom_z"), obj(3, model="fakesward_z")])
        self.d, self.app0 = lvl_app0({"level.lvx": self.main, SUB: self.sub, SUB2: self.sub2, "layers/x/level.lvx": self.main})
        self.scratch = os.path.join(self.d, "s")

    def rows(self):
        mk = lambda cat, drop, data, file=None, strip=False: dict(crow(cat, [drop]), src=S.sha256_bytes(data), file=file, strip=strip)
        return [mk("flowers", DROPS[0], self.main), mk("grass", DROPS[1], self.main),
                mk("flowers", DROPS[0], self.sub, S.file_key(SUB), True), mk("grass", DROPS[1], self.sub, S.file_key(SUB), True),
                mk("flowers", DROPS[0], self.sub2, S.file_key(SUB2))]

    def out(self, base, rel):
        with open(os.path.join(base, S.LEVELS_REL % "lv", rel), "rb") as f: return f.read()

    def test_file_key_is_the_hash_of_the_lower_case_path(self):
        self.assertEqual(S.file_key(SUB), S.row_key(len(SUB), SUB.lower()))
        self.assertEqual(S.file_key("layers\\x\\level.lvx".replace("\\", os.sep)), S.row_key(18, "layers/x/level.lvx"))
        self.assertNotIn("0a1b2c3d", S.file_key(SUB))

    def test_resolve_file(self):
        self.assertEqual(S.resolve_file(self.app0, "lv", None), "level.lvx")
        self.assertEqual(S.resolve_file(self.app0, "lv", S.file_key(SUB)), SUB)
        self.assertEqual(S.resolve_file(self.app0, "lv", S.file_key("layers/x/level.lvx")), "layers/x/level.lvx")
        with self.assertRaises(S.Skip): S.resolve_file(self.app0, "lv", "5:" + "0" * 16)                      # no match
        real = S.level_files
        S.level_files = lambda a, l: {"5:" + "0" * 16: ["a.lvx", "A.lvx"]}
        try:
            with self.assertRaises(S.Skip): S.resolve_file(self.app0, "lv", "5:" + "0" * 16)                 # two matches
        finally:
            S.level_files = real

    def test_deep_and_hidden_files_are_not_candidates(self):
        d, app0 = lvl_app0({"a/b/c/level.lvx": self.main, ".hid.lvx": self.main, ".d/x.lvx": self.main, "note.txt": b"x"})
        self.assertEqual(S.level_files(app0, "lv"), {})

    def test_generate_writes_every_file_and_keeps_the_others_out(self):
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "performance", self.rows())
        self.assertEqual(self.out(base, "level.lvx").count(b"<LevelObject>"), 1)
        self.assertEqual(self.out(base, SUB).count(b"<LevelObject>"), 1)                  # only the group is left, with an empty list
        self.assertIn(b'<Targets type="String">[]</Targets>', self.out(base, SUB))
        self.assertEqual(self.out(base, SUB2).count(b"<LevelObject>"), 1)
        files = sorted(os.path.relpath(os.path.join(r, f), base) for r, _, fs in os.walk(base) for f in fs)
        self.assertEqual(len(files), 3); self.assertFalse(any("layers" in f for f in files))
        self.assertTrue(any("now empty" in l for l in lines))
        self.assertEqual(sum(1 for l in lines if "generated" in l), 3)

    def test_one_files_hash_mismatch_skips_only_that_file(self):
        rows = self.rows(); rows[2] = dict(rows[2], src="0" * 64); rows[3] = dict(rows[3], src="0" * 64)
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "performance", rows)
        self.assertTrue(os.path.isfile(os.path.join(base, S.LEVEL_REL % "lv"))); self.assertTrue(os.path.isfile(os.path.join(base, S.LEVELS_REL % "lv", SUB2)))
        self.assertFalse(os.path.exists(os.path.join(base, S.LEVELS_REL % "lv", SUB)))
        self.assertEqual(sum(l.startswith("WARNING") for l in lines), 1)

    def test_a_missing_sub_level_file_is_one_warning_and_the_rest_is_written(self):
        rows = self.rows(); rows[4] = dict(rows[4], file="9:" + "1" * 16)
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "performance", rows)
        self.assertEqual(sum(l.startswith("WARNING") for l in lines), 1); self.assertTrue(os.path.isfile(os.path.join(base, S.LEVEL_REL % "lv")))

    def test_every_file_failing_is_a_skip(self):
        rows = [dict(r, src="0" * 64) for r in self.rows()]
        with self.assertRaises(S.Skip): S.generate(self.app0, self.scratch, "PPSA00001", "performance", rows)

    def test_a_stale_file_of_a_failing_job_is_removed(self):
        base, _ = S.generate(self.app0, self.scratch, "PPSA00001", "performance", self.rows())
        rows = self.rows(); rows[2] = dict(rows[2], src="0" * 64); rows[3] = dict(rows[3], src="0" * 64)
        S.generate(self.app0, self.scratch, "PPSA00001", "performance", rows)
        self.assertFalse(os.path.exists(os.path.join(base, S.LEVELS_REL % "lv", SUB)))

    def test_second_pass_reuses_and_changed_keep_regenerates(self):
        S.generate(self.app0, self.scratch, "PPSA00001", "performance", self.rows())
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "performance", self.rows()); self.assertTrue(all("reused" in l for l in lines), lines)
        base, lines = S.generate(self.app0, self.scratch, "PPSA00001", "performance", [dict(r, keep=50) for r in self.rows()])
        self.assertTrue(any("generated" in l for l in lines))

    def test_legacy_and_category_rows_share_one_folder_naming(self):
        base, _ = S.generate(self.app0, self.scratch, "PPSA00001", "balanced", self.rows())
        self.assertEqual(base, os.path.join(self.scratch, "scenery", "PPSA00001", "balanced"))
        base, _ = S.generate(self.app0, self.scratch, "PPSA00001", "custom-flowers", self.rows())
        self.assertTrue(base.endswith("custom-flowers"))

    def test_game_folder_is_never_written(self):
        before = {os.path.join(r, f): open(os.path.join(r, f), "rb").read() for r, _, fs in os.walk(self.app0) for f in fs}
        S.generate(self.app0, self.scratch, "PPSA00001", "performance", self.rows())
        after = {os.path.join(r, f): open(os.path.join(r, f), "rb").read() for r, _, fs in os.walk(self.app0) for f in fs}
        self.assertEqual(before, after)


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


# prefix keys (<len>:<hash16>) of every prefix of length 4.. of the two families that carry collision bodies (hashes only, no names)
COLLISION_KEYS = set("""4:f4c0e6d6798221af 5:d8d232e64bf82976 6:3241fa28189b14f6 7:f62aa2eac49d3e3d 8:767fa005c4a36b0e 9:7509cb42b60895af 10:353254d7572e7a3c
11:d336372b100e21b5 12:3baf8ca8bacb7ed3 13:584ad9c3e594fb39 14:f56bc0bf7e20bfef 15:77b4421b246b4ce2 16:40668d5c36e0cf5a 17:87d79a2270fea9f7 18:a0fc5714a51596ed
19:9645c26dad834bc3 4:5ac52c67c10a6cea 5:05018fdc8a6da61f 6:c51cdffb6e3816d6 7:0feae16d55365acf 8:6cf05305b20fcb92 9:dcf6ffeb6fe38696 10:b3b44c9e7410248d
11:c4e33d5836228af7 12:3d3f6b0dbb2d88fe 13:60a0add054f7350b 14:73bbbc796cb2d495 15:b5feec6e4411f333 16:836a70147f427b79 14:58739d7a4505ff30 15:8e8e83bd8e6a0bab""".split())


def profile_rows(t, kind):
    f = os.path.join(REPO, "games", t, "game.tsv")
    return [l.rstrip("\n").split("\t")[1:] for l in open(f) if l.startswith(kind + "\t")] if os.path.isfile(f) else []


class Profile(unittest.TestCase):
    """The shipped profile: rows parse, only the ASTRO profile has them, and no model name is written anywhere in the repo."""
    def test_rows_validate_and_other_profiles_have_none(self):
        found = []
        for t in sorted(os.listdir(os.path.join(REPO, "games"))):
            if not os.path.isfile(os.path.join(REPO, "games", t, "game.tsv")): continue
            for kind in ("scenery", "sceneryset", "drawskip"):
                rows = profile_rows(t, kind)
                if t != "PPSA21564": self.assertEqual(rows, [], (t, kind))
            for r in profile_rows(t, "scenery"): S.parse_row(r)
            for r in profile_rows(t, "sceneryset"): S.parse_set(r)
            for r in profile_rows(t, "drawskip"): S.parse_skip(r)
            if t == "PPSA21564": found = profile_rows(t, "scenery")
        self.assertEqual(sorted(S.parse_row(r)["preset"] for r in found if S.parse_row(r)["preset"]), ["minimal", "reduced"])
        self.assertEqual({S.parse_row(r)["cat"] for r in found} - {None}, {"flowers", "grass", "moss", "other"})

    def test_presets_are_defined_and_nothing_removes_more_than_flowers_and_grass_below_performance(self):
        sets = {S.parse_set(r)["preset"]: S.parse_set(r)["cats"] for r in profile_rows("PPSA21564", "sceneryset")}
        self.assertEqual(sorted(sets), ["balanced", "minimal", "performance", "reduced"])
        self.assertEqual(dict(sets["balanced"]), {"flowers": 50, "grass": 50}); self.assertEqual(dict(sets["minimal"]), dict(sets["reduced"]))
        self.assertEqual(sorted(sets["performance"]), ["flowers", "grass", "moss", "other"])

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

    def test_perf_mode_rules_honour_keeps_next_to_percent_keep(self):
        """v1.4.4 merge: `keeps` (models never removed, a list) and `keep` (percent of a category that stays) are different fields; thin_rows takes both."""
        blocks = [obj(1, model="fakebloom_a"), obj(2, model="fakebloom_solid_a"), obj(3, model="fakebloom_solid_b"), obj(4, model="fakebush_a")]
        keepfam = [(len("fakebloom_solid"), S.name_hash("fakebloom_solid"))]
        fam = [(len("fakebloom"), S.name_hash("fakebloom"))]
        legacy = dict(ROW, drops=fam, keeps=keepfam)
        cat = dict(ROW, preset=None, cat="flowers", drops=fam, keep=0)           # a category row without keeps: the collision family would go
        out, n, k = S.thin_rows(level(blocks), [legacy])
        self.assertEqual(k, 1)                                                    # only fakebloom_a; the two kept-family objects stay
        out2, n2, k2 = S.thin_rows(level(blocks), [cat])
        self.assertEqual(k2, 3)
        objs, cand = S.plan(level(blocks), legacy["cls"], fam, keepfam)           # plan(): 4th positional argument is the keeps list
        self.assertEqual(len(cand), 1)
        self.assertNotEqual(S.rules_id([legacy]), S.rules_id([dict(legacy, keeps=[])]))

    def test_category_rows_take_no_keep_word_and_legacy_rows_do(self):
        w = ["lv", "flowers", "src=" + "a" * 64, "class=bg", "drop=9:" + "b" * 16]
        self.assertEqual(S.parse_row(w)["keep"], 0)
        with self.assertRaises(ValueError):
            S.parse_row(w + ["keep=20:" + "c" * 16])
        self.assertEqual(S.parse_row(["lv", "minimal"] + w[2:] + ["keep=20:" + "c" * 16])["keeps"], [(20, "c" * 16)])

    def test_keep_excludes_a_model_family_and_changes_the_rules_id(self):
        blocks = [obj(1, model="fakebloom_a"), obj(2, model="fakebloom_solid_a"), obj(3, model="fakebloom_solid_b"), obj(4, model="fakebush_a")]
        row = dict(ROW, keeps=[(len("fakebloom_solid"), S.name_hash("fakebloom_solid"))])
        out, n, k = S.thin(level(blocks), row)
        self.assertEqual(k, 1)                                        # only fakebloom_a goes
        self.assertEqual(out.count(b"fakebloom_solid"), 2)
        self.assertEqual(S.thin(level(blocks), ROW)[2], 3)               # without keep the family goes too
        self.assertNotEqual(S.rules_id([row]), S.rules_id([ROW]))

    def test_minimal_keeps_collision_objects(self):
        # v1.4.4: the moss family has collision bodies; no preset may drop it. minimal == reduced for now.
        rows = {r["preset"]: r for r in (S.parse_row(c) for c in profile_rows("PPSA21564", "scenery")) if r["preset"]}
        self.assertEqual(rows["minimal"]["drops"], rows["reduced"]["drops"]); self.assertEqual(rows["minimal"]["src"], rows["reduced"]["src"])
        for r in rows.values():
            self.assertNotIn((19, "9645c26dad834bc3"), r["drops"])
            self.assertIn((20, "e0e9ae7d18a3dd97"), r["keeps"])      # the collision-shape family is kept by both presets
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

    def test_no_collision_family_in_rows(self):
        # no row of any category (and no legacy row) may carry a key that stands for a collision family (big moss, small rocks)
        for c in profile_rows("PPSA21564", "scenery"):
            for n, h in S.parse_row(c)["drops"]:
                self.assertNotIn("%d:%s" % (n, h), COLLISION_KEYS, c[:3])

    def test_sub_level_files_and_levels_are_hashed_or_plain_folder_names(self):
        for c in profile_rows("PPSA21564", "scenery"):
            r = S.parse_row(c)
            if r["file"]: self.assertRegex(r["file"], r"^[1-9]\d{0,2}:[0-9a-f]{16}$")
        text = open(os.path.join(REPO, "games", "PPSA21564", "game.tsv")).read()
        self.assertIsNone(re.search(r"\.lvx|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-", text))                 # no file names, no GUIDs

    def test_no_model_name_in_shipped_files(self):
        # the names stay out of the repository: scan the rows' files for readable name fragments of the kind the keys stand for
        bad = re.compile(r"veg_|apeescape_|_moss|flower_|grass_blade|grass_bump|grass_set|general_rock|fallen_leaf|\|root\|")
        for rel in ("scripts/nps5lib/scenery.py", "games/PPSA21564/game.tsv", "games/schema.tsv", "tests/test_scenery.py", "tests/test_scenery_live.py",
                    "scripts/nps5lib/run.py"):
            for i, l in enumerate(open(os.path.join(REPO, rel)), 1):
                if rel.endswith("test_scenery.py") and "bad = re.compile" in l: continue
                self.assertIsNone(bad.search(l), "%s:%d" % (rel, i))


if __name__ == "__main__":
    unittest.main()
