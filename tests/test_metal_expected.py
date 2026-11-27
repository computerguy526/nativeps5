#!/usr/bin/env python3
"""Tests for scripts/nps5lib/metalexpected.py: the presence manifest games/<id>/metal-expected.tsv vs the Metal row lists.
Synthetic failure modes (a) ship row absent / no .verified, (b) list row missing from the manifest, (c) previous-release row vanished,
(d) order / duplicates, update helper, plus the real files of every game that has a manifest. No game data, no GPU."""
import hashlib, os, shutil, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "scripts", "nps5lib"))
import metal, metalexpected as me  # noqa: E402


def h(s, n=16):
    return hashlib.sha256(s.encode()).hexdigest()[:n]


def crow(k, cls="*", mode="base"):
    return dict(kk=h(k), cls=cls, mode=mode)


def grow(k, cls="g16", mode="fix6", ev="autoval"):
    return dict(kk_vs=h("v" + k), kk_fs=h("f" + k), cls=cls, mode=mode, evidence=ev)


def gk(k):
    return "%s/%s" % (h("v" + k), h("f" + k))


def build(comp, gfx, offs=None, prev=None):
    """A manifest text for the given lists, through update() (so the format is the real one)."""
    return me.update("", comp, gfx, offs, prev, title="PPSA00000 01.000.000")


COMP = [crow("c1"), crow("c2", "g16")]
GFX = [grow("a", "g16"), grow("a", "g18"), grow("b", "*"), grow("slow", "g18", "kk")]


class T(unittest.TestCase):
    def test_roundtrip_ok(self):
        t = build(COMP, GFX)
        self.assertEqual(me.check(t, COMP, GFX), [])
        meta, rows = me.parse_manifest(t)
        st = {(r[0], r[1], r[2]): r[3] for r in rows}
        self.assertEqual(st[("compute", h("c1"), "g17")], "ship")           # `*` row ships on every class
        self.assertEqual(st[("compute", h("c2"), "g17")], "off:untested-g17")
        self.assertEqual(st[("gfx", gk("slow"), "g18")], "off:slower-g18")  # kk row = KosmicKrisp is faster there
        self.assertEqual(st[("gfx", gk("a"), "g17")], "off:untested-g17")

    def test_a_ship_row_absent_from_list(self):
        t = build(COMP, GFX)
        bad = me.check(t, COMP, [r for r in GFX if r["cls"] != "g18" or r["mode"] == "kk"])      # row a g18 left the list
        self.assertTrue(any("says ship" in b and gk("a") in b and "g18" in b for b in bad), bad)

    def test_a_pendval_and_verified(self):
        t = build(COMP, GFX)
        g = GFX[:2] + [dict(GFX[2], evidence="autoval,pendval")] + GFX[3:]
        self.assertTrue(any("pendval" in b for b in me.check(t, COMP, g, tempfile.gettempdir())))
        d = tempfile.mkdtemp()
        try:
            for n, rows in ((metal.LIST_NAME, COMP), (metal.GFX_LIST_NAME, GFX)):
                open(os.path.join(d, n), "w").write("x\n")
            self.assertTrue(any("no metal-shaders.tsv.verified" in b or "has no" in b for b in me.verified_problems(d, COMP, GFX)))
            for n, rows in ((metal.LIST_NAME, COMP), (metal.GFX_LIST_NAME, GFX)):
                nt = sum(1 for r in rows if r["mode"] != "kk")
                open(os.path.join(d, n + ".verified"), "w").write("sha256=%s\nreproduced=%d of %d\n" % (hashlib.sha256(b"x\n").hexdigest(), nt, nt))
            self.assertEqual(me.verified_problems(d, COMP, GFX), [])
            open(os.path.join(d, metal.GFX_LIST_NAME), "w").write("changed\n")                 # the list moved on, the record did not
            self.assertTrue(any("sha256 differs" in b for b in me.verified_problems(d, COMP, GFX)))
        finally:
            shutil.rmtree(d)

    def test_b_list_row_missing_from_manifest(self):
        t = build(COMP, GFX)
        bad = me.check(t, COMP, GFX + [grow("new", "g16")])
        self.assertTrue(any("not in the manifest" in b and gk("new") in b for b in bad), bad)
        bad = me.check(t, COMP + [crow("c3")], GFX)
        self.assertTrue(any("not in the manifest" in b for b in bad), bad)

    def test_b_manifest_off_but_list_ships(self):
        t = build(COMP, GFX).replace("\tship\t", "\toff:untested-g16\t", 1)
        self.assertTrue(any("list ships" in b for b in me.check(t, COMP, GFX)))

    def test_new_row_then_update(self):
        t = build(COMP, GFX)
        g2 = GFX + [grow("new", "g16")]
        self.assertTrue(me.check(t, COMP, g2))
        t2 = me.update(t, COMP, g2)
        self.assertEqual(me.check(t2, COMP, g2), [])
        self.assertIn("off:untested-g17", t2)                                              # kept / new gaps stay off with a reason

    def test_c_prev_row_vanishes(self):
        prev = me.list_states(COMP, GFX)
        t = build(COMP, GFX, prev=prev)
        gone = [r for r in GFX if not (r["cls"] == "g18" and r["mode"] != "kk")]
        # the row left the list: the manifest still says ship (a) and update refuses without a reason
        self.assertTrue(me.check(t, COMP, gone))
        with self.assertRaises(me.ExpectedError):
            me.update(t, COMP, gone, prev_states=prev)
        t2 = me.update(t, COMP, gone, {("gfx", gk("a"), "g18"): "off:dropped-correctness:abc1234"}, prev)
        self.assertEqual(me.check(t2, COMP, gone, prev_states=prev), [])
        # deleting the prev lines by hand breaks the seal; deleting them from a git-known tag breaks the tag check
        lines = [l for l in t2.splitlines() if gk("a") not in l or "g18" not in l]
        bad = me.check("\n".join(lines) + "\n", COMP, gone, prev_states=prev)
        self.assertTrue(any("seal" in b for b in bad) and any("prev=1" in b for b in bad), bad)
        # a placeholder reason is not a reason
        t3 = t2.replace("off:dropped-correctness:abc1234", "off:unknown")
        self.assertTrue(any("placeholder" in b for b in me.check(t3, COMP, gone, prev_states=prev)))

    def test_d_order_duplicates_malformed(self):
        t = build(COMP, GFX)
        ls = t.splitlines()
        i = next(n for n, l in enumerate(ls) if l.startswith("compute\t"))
        sw = ls[:]; sw[i], sw[i + 1] = sw[i + 1], sw[i]
        self.assertTrue(any("out of order" in b for b in me.check("\n".join(sw) + "\n", COMP, GFX)))
        dup = ls[:i + 1] + [ls[i]] + ls[i + 1:]
        self.assertTrue(any("duplicate" in b for b in me.check("\n".join(dup) + "\n", COMP, GFX)))
        self.assertTrue(any("not a manifest row" in b for b in me.check(t + "gfx\tzz\tg16\tship\t0\n", COMP, GFX)))
        self.assertTrue(any("not a manifest row" in b for b in me.check(t.replace("\tship\t", "\tmaybe\t", 1), COMP, GFX)))

    def test_osn_pin_counts_for_its_class(self):
        st = me.list_states([], [grow("p", "g16@os27")])
        self.assertEqual(st, {("gfx", gk("p"), "g16"): "ship"})

    def test_real_files(self):
        found = 0
        for gid in sorted(os.listdir(os.path.join(REPO, "games"))):
            gd = os.path.join(REPO, "games", gid)
            if not os.path.isfile(os.path.join(gd, me.NAME)):
                continue
            found += 1
            comp, gfx = me.read_lists(gd)
            prev = me.tag_states(REPO, me.PREV_TAG, gid)          # None in a clone without tags: the seal still protects the prev rows
            with open(os.path.join(gd, me.NAME)) as f:
                bad = me.check(f.read(), comp, gfx, gd, prev)
            self.assertEqual(bad, [], "%s: %s\nupdate: python3 scripts/nps5lib/metalexpected.py update %s" % (gid, "\n".join(bad[:20]), gid))
        self.assertTrue(found, "no games/<id>/%s" % me.NAME)


if __name__ == "__main__":
    unittest.main()
