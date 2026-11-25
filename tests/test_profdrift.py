#!/usr/bin/env python3
"""Tests of scripts/nps5lib/profdrift.py (promotion register vs the composed profile; env.dump diff). Synthetic words only; no title, no GPU."""
import os, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts", "nps5lib"))
import profdrift as P  # noqa: E402

HEAD = "format\tnps5-promotions\t1\n"


def reg(*rows):
    return HEAD + "".join("\t".join(r) + "\n" for r in rows)


def row(word, verdict="KEEP", status="pending", title="PPSA00001", ev="doc-x tag1", date="2026-11-25", owner="o"):
    return (title, word, verdict, ev, date, status, owner)


class Register(unittest.TestCase):
    def test_parse_ok(self):
        rows, bad = P.parse_register(reg(row("A_B=1"), row("NPS5_UECVAR_SET[r.X.Y]=0", status="landed abc1234")))
        self.assertEqual(bad, [])
        self.assertEqual((rows[1]["name"], rows[1]["item"], rows[1]["value"], rows[1]["commit"]), ("NPS5_UECVAR_SET", "r.X.Y", "0", "abc1234"))

    def test_problems(self):
        _, bad = P.parse_register(reg(row("A=1", verdict="WIN"), row("B=1", status="landed"), row("C=1", date="11-25"), row("nonsense"), row("D=1", ev="-")))
        self.assertEqual(len(bad), 5)

    def test_bad_head_and_path(self):
        _, bad = P.parse_register("x\ty\tz\n")
        self.assertTrue(bad)
        _, bad = P.parse_register(reg(row("A=1", ev="see ~/work/x")))
        self.assertTrue(any("path" in b for b in bad))

    def test_unresolved(self):
        rows, bad = P.parse_register(reg(row("?later-words")))
        self.assertEqual(bad, []); self.assertTrue(rows[0]["unresolved"])


class Check(unittest.TestCase):
    def rows(self, *r):
        rows, bad = P.parse_register(reg(*r)); self.assertEqual(bad, []); return rows

    def test_present_value_and_item(self):
        env = {"A": "1", "NPS5_UECVAR_SET": "r.X=0, r.Y.Z=2.5"}
        rows = self.rows(row("A=1"), row("NPS5_UECVAR_SET[r.Y.Z]=2.5"), row("NPS5_UECVAR_SET[r.X]=0"))
        e, w, i = P.check_rows(rows, env)
        self.assertEqual((e, w, len(i)), ([], [], 3))

    def test_missing_pending_warns_release_fails(self):
        rows = self.rows(row("A=1"))
        e, w, _ = P.check_rows(rows, {})
        self.assertEqual((len(e), len(w)), (0, 1))
        e, w, _ = P.check_rows(rows, {}, release=True)
        self.assertEqual((len(e), len(w)), (1, 0))

    def test_landed_missing_fails_and_wrong_value(self):
        rows = self.rows(row("A=1", status="landed abc1234"))
        e, _, _ = P.check_rows(rows, {"A": "2"})
        self.assertEqual(len(e), 1); self.assertIn("'2'", e[0])

    def test_item_missing_or_other_value(self):
        rows = self.rows(row("NPS5_UECVAR_SET[r.X]=0", status="landed abc1234"))
        self.assertEqual(len(P.check_rows(rows, {"NPS5_UECVAR_SET": "r.X=1"})[0]), 1)
        self.assertEqual(len(P.check_rows(rows, {"NPS5_UECVAR_SET": "r.Q=0"})[0]), 1)

    def test_rejected_loss_present_fails(self):
        rows = self.rows(row("A=1", verdict="LOSS", status="rejected"))
        self.assertEqual(len(P.check_rows(rows, {"A": "1"})[0]), 1)
        self.assertEqual(P.check_rows(rows, {"A": "0"})[0], [])
        self.assertEqual(P.check_rows(rows, {})[0], [])

    def test_neutral_and_optin_ignored(self):
        rows = self.rows(row("A=1", verdict="NEUTRAL", status="optin"), row("B=1", status="rejected"))
        self.assertEqual(P.check_rows(rows, {}, release=True), ([], [], []))

    def test_unresolved_release(self):
        rows = self.rows(row("?words"))
        self.assertEqual((len(P.check_rows(rows, {})[0]), len(P.check_rows(rows, {})[1])), (0, 1))
        self.assertEqual(len(P.check_rows(rows, {}, release=True)[0]), 1)


class Diff(unittest.TestCase):
    def test_parse_formats(self):
        d = P.parse_dump("# env dump x\nA=1\n  B=two words\n[nps5-config] C=3 (profile)\n[nps5-config] title=T tag=t\nnps5 run X: dry run\nD=\n")
        self.assertEqual(d, {"A": "1", "B": "two words", "C": "3", "D": ""})

    def test_diff_sections_and_ignored(self):
        dump = {"RIG": "1", "SAME": "1", "VAL": "a", "KYTY_FLIGHTREC": "1", "NPS5_WINDOW_X": "1", "NPS5_CACHE_DIR": "/a", "KYTY_DUMP_DIR": "/q", "P": "/one/x"}
        comp = {"SAME": "1", "VAL": "b", "ONLYP": "z", "P": "/two/y"}
        d = P.diff_env(dump, comp)
        self.assertEqual(d["rig_only"], [("RIG", "1")]); self.assertEqual(d["profile_only"], [("ONLYP", "z")]); self.assertEqual(d["value"], [("VAL", "a", "b")])
        for k in ("KYTY_FLIGHTREC", "NPS5_WINDOW_X", "NPS5_CACHE_DIR", "KYTY_DUMP_DIR", "P"):
            self.assertIn(k, d["ignored"])

    def test_extra_ignore(self):
        self.assertEqual(P.diff_env({"FOO": "1"}, {}, [r"FO+"])["rig_only"], [])


class PrivacyGate(unittest.TestCase):
    def test_check_py_accepts_registers_refuses_junk(self):
        sys.path.insert(0, os.path.join(REPO, "scripts"))
        import nps5_check as C
        for t in P.titles_with_register(REPO):
            with open(os.path.join(REPO, "games", t, "promotions.tsv"), "rb") as f:
                self.assertEqual(C.check_promotions_list(f.read()), [])
        self.assertTrue(C.check_promotions_list(b"format\tnps5-promotions\t1\nfree text\n"))
        self.assertTrue(C.check_promotions_list(b"format\tnps5-promotions\t1\nPPSA00001\tA=1\tKEEP\t/Users/x/doc\t2026-11-25\tpending\to\n"))


EMPTY_OK = ("PPSA24096", "PPSA30552")


class Shipped(unittest.TestCase):
    def test_registers_wellformed(self):
        for t in P.titles_with_register(REPO):
            rows, bad = P.read_register(REPO, t)
            self.assertEqual(bad, [], t)
            self.assertTrue(rows or t in EMPTY_OK, t)         # the bring-up titles carry an empty register (header only) so strict mode does not skip them

    def test_compose_real_profile(self):
        for t in P.titles_with_register(REPO):
            env = P.compose(REPO, t)
            self.assertEqual(env["NPS5_TITLE_ID"], t)

    def test_cli_exit_codes(self):
        self.assertEqual(P.main([]), 2)
        self.assertEqual(P.main(["check", "--all"]), 0)     # pending rows only warn


if __name__ == "__main__":
    unittest.main()
