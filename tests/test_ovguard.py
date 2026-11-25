#!/usr/bin/env python3
"""Tests of scripts/nps5lib/ovguard.py (override keys must survive the shipped codegen words). Synthetic hashes only; no title, no GPU."""
import os, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib"))
import ovguard as O  # noqa: E402

D1, D2 = "a" * 16, "b" * 16          # guest hashes
K1, K2, K3 = "1" * 16, "2" * 16, "3" * 16   # kk keys
CS = [["0" * 32, "1" * 16, K1, "f" * 64, "32x1x1", "exact"], ["0" * 32, "1" * 16, K3, "-", "32x1x1", "kk"]]
GFX = [["gfx", "0" * 32, "x:0", "-", "y:0", "4" * 16, K2, "5" * 64, "6" * 64, "twin"]]
KEYS = O.override_keys(CS, GFX)


class T(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(KEYS, {"cs": {K1}, "fs": {K2}})        # the `kk` class row (text '-') is not an override

    def test_high_accuracy_needs_nothing(self):
        self.assertEqual(O.check_profile(KEYS, None, set(), {}, strict=True, words=False), ([], []))
        self.assertEqual(O.check_profile(KEYS, None, set(), {"KYTY_GPU_ACCURACY": "high"}, strict=True, words=False), ([], []))

    def test_low_without_sidecar(self):
        e, w = O.check_profile(KEYS, None, set(), {"KYTY_GPU_ACCURACY": "low"})
        self.assertEqual((len(e), len(w)), (0, 1))
        e, w = O.check_profile(KEYS, None, set(), {"KYTY_GPU_ACCURACY": "low"}, strict=True)
        self.assertEqual((len(e), len(w)), (1, 0))

    def test_low_all_denied_passes(self):
        side = {("cs", K1): {D1}, ("fs", K2): {D2}}
        self.assertEqual(O.check_profile(KEYS, side, {D1, D2}, {"KYTY_GPU_ACCURACY": "low"}, strict=True), ([], []))

    def test_low_not_denied_fails(self):
        side = {("cs", K1): {D1}, ("fs", K2): {D2}}
        e, _ = O.check_profile(KEYS, side, {D1}, {"KYTY_GPU_ACCURACY": "low"})
        self.assertEqual(len(e), 1); self.assertIn(D2, e[0])

    def test_missing_guest_row_fails_even_non_strict(self):
        e, _ = O.check_profile(KEYS, {("cs", K1): {D1}}, {D1}, {"KYTY_GPU_ACCURACY": "low"})
        self.assertEqual(len(e), 1); self.assertIn(K2, e[0])

    def test_one_undenied_guest_of_several_fails(self):
        e, _ = O.check_profile(KEYS, {("cs", K1): {D1, D2}, ("fs", K2): {D1}}, {D1}, {"KYTY_GPU_ACCURACY": "low"})
        self.assertEqual(len(e), 1)

    def test_stale_sidecar_row_warns(self):
        _, w = O.check_profile(KEYS, {("cs", K1): {D1}, ("fs", K2): {D2}, ("cs", "9" * 16): {D1}}, {D1, D2}, {"KYTY_GPU_ACCURACY": "low"})
        self.assertEqual(len(w), 1)

    def test_risky_words(self):
        env = {"KYTY_NATIVE_FMINMAX": "1", "KYTY_UNIFORM_BUFFERS": "1"}
        e, w = O.check_profile(KEYS, None, set(), env)
        self.assertEqual((len(e), len(w)), (0, 2))
        e, _ = O.check_profile(KEYS, None, set(), env, strict=True)
        self.assertEqual(len(e), 2)
        env["KYTY_NATIVE_FMINMAX_DENY"] = "1"                      # the deny-aware switch clears FMINMAX only
        self.assertEqual(len(O.check_profile(KEYS, None, set(), env, strict=True)[0]), 1)
        self.assertEqual(O.check_profile(KEYS, None, set(), {"KYTY_PS_PER_VERTEX": "auto"}, strict=True), ([], []))

    def test_no_overrides_no_findings(self):
        self.assertEqual(O.check_profile({"cs": set(), "fs": set()}, None, set(), {"KYTY_GPU_ACCURACY": "low"}, strict=True), ([], []))

    def test_sidecar_parse(self):
        m, bad = O.parse_sidecar([["format", "nps5-metal-guests", "1"], ["cs", K1, D1], ["cs", K1, D2], ["fs", K2, D2]])
        self.assertEqual((m[("cs", K1)], bad), ({D1, D2}, []))
        self.assertTrue(O.parse_sidecar([["cs", K1, D1]])[1])
        self.assertTrue(O.parse_sidecar([["format", "nps5-metal-guests", "1"], ["cs", "zz", D1]])[1])
        self.assertTrue(O.parse_sidecar([["format", "nps5-metal-guests", "1"], ["gs", K1, D1]])[1])

    def test_summary(self):
        log = ("[kk-mslinject] seen %s len=9 compute canon=%s\n[kk-mslinject] seen %s\n[kk-mslinject] override %s local_size 32 1 1\n"
               "[kk-mslinject] gfx override %s/%s 1.5 ms\n" % ("7" * 16, K1, K2, K1, "4" * 16, K2))
        self.assertEqual(O.summary(KEYS, {("4" * 16, K2)}, log), (2, 2, 2))
        self.assertEqual(O.summary(KEYS, {("4" * 16, K2)}, "[kk-mslinject] seen %s\n" % K1), (2, 1, 0))     # seen, nothing injected
        self.assertEqual(O.summary(KEYS, {("4" * 16, K2)}, ""), (2, 0, 0))

    def test_sidecar_generate_and_check(self):
        with tempfile.TemporaryDirectory() as d:
            def w(n, rows):
                with open(os.path.join(d, n), "w") as f:
                    f.write("\n".join("\t".join(r) for r in rows) + "\n")
            w("metal-shaders.tsv", CS); w("metal-gfx-pairs.tsv", GFX)
            w("map.tsv", [["cs", D1, K1, "3"], ["fs", D2, K2, "2"], ["fs", D2, "9" * 16, "1"]])     # last: not an override key, dropped
            self.assertEqual(O.main(["sidecar", d, os.path.join(d, "map.tsv")]), 0)
            keys, side, _ = O.load_profile(d)
            m, bad = O.parse_sidecar(side)
            self.assertEqual((sorted(m), bad), ([("cs", K1), ("fs", K2)], []))
            self.assertEqual(O.check_profile(keys, m, {D1, D2}, {"KYTY_GPU_ACCURACY": "low"}, strict=True), ([], []))


if __name__ == "__main__":
    unittest.main()
