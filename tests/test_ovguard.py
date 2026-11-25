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
        self.assertEqual(KEYS, {"cs": {K1}, "fs": {K2}, "vs": {"4" * 16}})        # the `kk` class row (text '-') is not an override

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

    def test_normal_and_fast_math_levels_need_fs_cs(self):
        side = {("cs", K1): {D1}, ("fs", K2): {D2}}
        for env in ({"KYTY_GPU_ACCURACY": "normal"}, {"KYTY_FAST_MATH": "1"}, {"KYTY_FAST_MATH": "2"}):
            self.assertEqual(len(O.check_profile(KEYS, side, {D1}, env, words=False)[0]), 1, env)       # fs guest D2 not denied
            self.assertEqual(O.check_profile(KEYS, side, {D1, D2}, env, words=False), ([], []), env)
        self.assertEqual(O.check_profile(KEYS, side, set(), {"KYTY_FAST_MATH": "0", "KYTY_GPU_ACCURACY": "high"}, words=False), ([], []))

    def test_vertex_stage_only_with_fast_math_vs(self):
        V = "4" * 16; DV = "d" * 16
        side = {("cs", K1): {D1}, ("fs", K2): {D2}, ("vs", V): {DV}}
        low = {"KYTY_GPU_ACCURACY": "low"}
        # ACCURACY=low alone: the vertex guest need not be denied (and is not even required in the sidecar)
        self.assertEqual(O.check_profile(KEYS, {("cs", K1): {D1}, ("fs", K2): {D2}}, {D1, D2}, low, words=False), ([], []))
        self.assertEqual(O.check_profile(KEYS, side, {D1, D2}, low, words=False), ([], []))
        for env in (dict(low, KYTY_FAST_MATH_VS="1"), {"KYTY_FAST_MATH": "3"}, dict(low, KYTY_FAST_MATH="3")):
            e, _ = O.check_profile(KEYS, side, {D1, D2}, env, words=False)
            self.assertEqual(len(e), 1, env); self.assertIn(DV, e[0])                                        # vs guest not denied
            self.assertEqual(O.check_profile(KEYS, side, {D1, D2, DV}, env, words=False)[0], [], env)
            e, _ = O.check_profile(KEYS, {("cs", K1): {D1}, ("fs", K2): {D2}}, {D1, D2}, env, words=False)
            self.assertEqual(len(e), 1, env)                                                                 # vs key missing in the sidecar
        # FAST_MATH_VS alone (accuracy high): cs/fs are untouched, only vs needs a deny
        self.assertEqual(O.check_profile(KEYS, {("vs", V): {DV}}, {DV}, {"KYTY_FAST_MATH_VS": "1"}, words=False), ([], []))
        self.assertEqual(len(O.check_profile(KEYS, {("vs", V): {DV}}, set(), {"KYTY_FAST_MATH_VS": "1"}, words=False)[0]), 1)

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
        self.assertEqual(O.parse_sidecar([["format", "nps5-metal-guests", "1"], ["vs", K1, D1]])[1], [])

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

    def test_xf_sidecar(self):
        xf = ["cs %s %s" % (D1, "0" * 32), "ps %s %s" % (D2, "7" * 32), "vs %s %s" % ("c" * 16, "8" * 32), "garbage"]
        gfx = [["gfx", "9" * 32, "x:0", "7" * 32, "y:0", "4" * 16, K2, "5" * 64, "6" * 64, "twin"]]
        self.assertEqual(O.xf_sidecar_rows(CS, gfx, xf), {("cs", K1, D1), ("fs", K2, D2)})
        self.assertEqual(O.xf_sidecar_rows(CS, gfx, xf + ["vs %s %s" % ("c" * 16, "9" * 32)]), {("cs", K1, D1), ("fs", K2, D2), ("vs", "4" * 16, "c" * 16)})
        self.assertEqual(O.xf_sidecar_rows(CS, gfx, xf[:1]), {("cs", K1, D1)})        # a key with no digest hit stays unmapped

    def test_baseline_waives_only_named_findings_and_shrinks(self):
        base, bad = O.parse_baseline("# c\ncs:%s no local program\nword:KYTY_NATIVE_FMINMAX pending bisect\n" % ("a" * 16))
        self.assertEqual((sorted(base), bad), (["cs:" + "a" * 16, "word:KYTY_NATIVE_FMINMAX"], []))
        errs = ["cs override key %s has no guest hash in x" % ("a" * 16), "fs override key %s has no guest hash in x" % ("b" * 16),
                "KYTY_NATIVE_FMINMAX=1: why; prove it", "KYTY_UNIFORM_BUFFERS=1: why; prove it"]
        left, waived, stale = O.apply_baseline(errs, base)
        self.assertEqual([m.split(" ")[0] for m in left], ["fs", "KYTY_UNIFORM_BUFFERS=1:"])      # new gaps still fail
        self.assertEqual(len(waived), 2); self.assertEqual(stale, [])
        left, waived, stale = O.apply_baseline(errs[1:], base)                                      # the cs gap was fixed: its entry is now an error
        self.assertEqual(len(stale), 1); self.assertIn("cs:" + "a" * 16, stale[0])
        self.assertEqual(O.apply_baseline([], {"word:KYTY_X": "r"}, words_checked=False)[2], [])   # word entries are not judged when words are not checked
        self.assertTrue(O.parse_baseline("cs:zz why")[1] and O.parse_baseline("cs:%s" % ("a" * 16))[1])      # bad token / no reason

    def test_main_dispatches_every_command(self):
        # sidecar-xf was missing from main()'s command list (a real call printed the usage and returned 2)
        names = {"check": "cmd_check", "sidecar": "cmd_sidecar", "sidecar-xf": "cmd_sidecar_xf", "summary": "cmd_summary", "envdump": "cmd_envdump"}
        calls = []
        saved = {n: getattr(O, n) for n in names.values()}
        try:
            for cmd, fn in names.items():
                setattr(O, fn, lambda a, c=cmd: calls.append(c) or 0)
                self.assertEqual(O.main([cmd, "x"]), 0, cmd)
        finally:
            for n, f in saved.items():
                setattr(O, n, f)
        self.assertEqual(calls, list(names))
        self.assertEqual(O.main(["nope"]), 2)


if __name__ == "__main__":
    unittest.main()
