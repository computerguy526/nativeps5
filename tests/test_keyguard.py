#!/usr/bin/env python3 -I
"""keyguard (scripts/nps5lib/keyguard.py): every shipped Metal row is reachable by the key the candidate stack produces, or accounted.
Synthetic lists, keys and records only (no game data)."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
from nps5lib import keyguard as kg  # noqa: E402

FP = "ab" * 32                                   # the candidate Kyty's code generation
LIB = b"\0junk\0" + FP.encode() + b"\0" + kg.FP_MARK + b"\0KYTY_X\0"
H = lambda s, n=16: hashlib.sha256(s.encode()).hexdigest()[:n]


def crow(k, cls="*", ev="exact", text=None):
    return "\t".join([H("p" + k, 32), H("s" + k), H("kk" + k), text or H("b" + k, 64), "32x1x1", ev, "base", cls, "exact", "shipped", "-"])


def grow(k, cls="g16", ev="autoval", text=None, variant="twin"):
    return "\t".join(["gfx", H("v" + k, 32), H("vs" + k) + ":0", H("f" + k, 32), H("fs" + k) + ":0", H("kv" + k), H("kf" + k), text or H("bv" + k, 64), H("bf" + k, 64),
                      variant, "0:0", "0:0:92", "1", "0:0:0:0:0:0:0:15:f:4", ev, "fix6", cls, "exact", "faster", "0.9"])


def pair(k):
    return [H("kv" + k), H("kf" + k)]


def put(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.repo = os.path.join(self.t, "repo"); self.g = os.path.join(self.repo, "games", "PPSA00000")
        os.makedirs(self.g); os.makedirs(os.path.join(self.repo, "kosmickrisp"))
        put(os.path.join(self.repo, "kosmickrisp", "EXPECTED_TREE"), "c" * 40 + "\n")
        self.lock("1" * 40)
        put(os.path.join(self.g, "pipelines.kyplst"), b"KYPLST1\0synthetic")
        self.lib = os.path.join(self.t, "kyty_gpu.dylib"); put(self.lib, LIB)
        self.lists(["A", "B"], ["x", "y"])

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def lock(self, commit):
        put(os.path.join(self.repo, "components.lock"), "# pins\nkyty | GPL | u | b | r | v9.9.9 | %s\nmesa | MIT | u | b | r | - | tree\n" % commit)

    def lists(self, cs, gfx, cs_extra=(), gfx_extra=()):
        self.cs_lines = [crow(k) for k in cs] + list(cs_extra)
        self.gfx_lines = [grow(k) for k in gfx] + list(gfx_extra)
        put(os.path.join(self.g, "metal-shaders.tsv"), "# c\nformat\tnps5-metal-keys\t2\ntitle\tPPSA00000\t1\nemitter\tabcdef1\tv1.0\n" + "\n".join(self.cs_lines) + "\n")
        put(os.path.join(self.g, "metal-gfx-pairs.tsv"), "# c\nformat\tnps5-metal-gfx-pairs\t3\ntitle\tPPSA00000\t1\nemitter\tabcdef1\tv1.0\n" + "\n".join(self.gfx_lines) + "\n")

    def keys(self, live, name="k.jsonl", fp=FP, shas=None):
        """live: {('cs'|'gfx', index of the row in its list): live key list | 'err text' | None (no entry)}; default = the row's own key."""
        out = []
        for lst, lines in (("cs", self.cs_lines), ("gfx", self.gfx_lines)):
            for i, l in enumerate(lines):
                c = l.split("\t")
                old = [c[2]] if lst == "cs" else [c[5], c[6]]
                v = live.get((lst, i), old)
                if v is None:
                    continue
                e = dict(list=lst, row=5 + i, old=old)
                e.update(err=v) if isinstance(v, str) else e.update(key=v)
                out.append(e)
        p = os.path.join(self.t, name)
        put(p, "".join(json.dumps(e) + "\n" for e in out))
        put(p + ".meta.json", json.dumps(dict(programs_fingerprint=fp, **(shas or kg.list_shas(self.g)))))
        return p

    def account(self, *lines):
        put(os.path.join(self.g, kg.ACCOUNT_NAME), kg.ACCOUNT_FORMAT + "\n" + "".join("\t".join(l) + "\n" for l in lines))

    def run_check(self, keys, record=False, lib=True):
        import contextlib
        import io
        argv = ["check", "--game", self.g] + sum((["--keys", k] for k in (keys if isinstance(keys, list) else [keys])), [])
        argv += (["--kyty-lib", self.lib] if lib else []) + (["--record", "--repo", self.repo] if record else [])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kg.main(argv)
        return rc, buf.getvalue()


class Verdicts(Base):
    def test_all_rows_live_passes(self):
        rc, out = self.run_check(self.keys({}))
        self.assertEqual(rc, 0, out); self.assertIn("keyguard: PASS", out)

    def test_moved_key_fails(self):
        """The 2026-11-27 regression: the code generation moved a row's key and no row carries the new one."""
        rc, out = self.run_check(self.keys({("gfx", 0): pair("moved")}))
        self.assertEqual(rc, 1); self.assertIn("FAIL MOVED: gfx %s" % "/".join(pair("x")), out); self.assertIn("UNREACHABLE", out)
        rc, out = self.run_check(self.keys({("cs", 1): [H("other")]}, "k2.jsonl"))
        self.assertEqual(rc, 1); self.assertIn("FAIL MOVED: cs %s" % H("kkB"), out)

    def test_moved_key_with_alias_row_is_covered(self):
        self.lists(["A"], ["x"], gfx_extra=[grow("x2", text=H("bvx", 64)).replace(H("bfx2", 64), H("bfx", 64))])   # same texts, today's pair
        rc, out = self.run_check(self.keys({("gfx", 0): pair("x2")}))
        self.assertEqual(rc, 0, out); self.assertIn("COVERED 1", out)

    def test_alias_row_with_other_texts_does_not_cover(self):
        self.lists(["A"], ["x"], gfx_extra=[grow("x2")])                                    # today's pair is listed, with ANOTHER text
        rc, out = self.run_check(self.keys({("gfx", 0): pair("x2")}))
        self.assertEqual(rc, 1); self.assertIn("FAIL MOVED", out)

    def test_star_row_needs_cover_on_every_class(self):
        g17 = crow("A2", cls="g17", text=H("bA", 64))
        self.lists(["A"], ["x"], cs_extra=[g17], gfx_extra=[grow("z", cls="g18")])           # classes of the lists: g16 g17 g18
        rc, out = self.run_check(self.keys({("cs", 0): [H("kkA2")]}))
        self.assertEqual(rc, 1); self.assertIn("classes g16,g18", out)
        self.account(("cs", H("kkA"), "*", "moved", "g16 and g18: KosmicKrisp kernel is faster there (timed), no row wanted"))
        rc, out = self.run_check(self.keys({("cs", 0): [H("kkA2")]}))
        self.assertEqual(rc, 0, out); self.assertIn("INFO accounted MOVED", out)

    def test_accounted_states_pass_with_reason(self):
        k = self.keys({("gfx", 0): "no list pipeline with the row's permutations", ("cs", 0): "not in programs.bin (kernel)", ("gfx", 1): pair("gone")})
        self.assertEqual(self.run_check(k)[0], 1)
        self.account(("gfx", "/".join(pair("x")), "g16", "nopipe", "list holds only the plain twin of this pipeline (twin row candidate)"),
                     ("cs", H("kkA"), "*", "noprog", "kernel only compiled in the final boss level, not in the prepare cache"),
                     ("gfx", "/".join(pair("y")), "g16", "moved", "re-key pending on the alias branch, validation run booked"))
        rc, out = self.run_check(k)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("INFO accounted"), 3)

    def test_accounting_needs_a_real_reason(self):
        k = self.keys({("gfx", 0): pair("gone")})
        for why in ("todo", "moved", "n/a", "short reason", "REASON? (today's key 1234567890abcdef)"):
            self.account(("gfx", "/".join(pair("x")), "g16", "moved", why))
            rc, out = self.run_check(k)
            self.assertEqual(rc, 1, why); self.assertIn("the reason must say WHY", out)

    def test_accounting_of_another_state_fails(self):
        """A row accounted `noprog` (not checkable) that a fuller cache shows MOVED must fail: the accounting hid a real miss."""
        self.account(("cs", H("kkA"), "*", "noprog", "kernel only compiled in the final boss level, not in the prepare cache"))
        rc, out = self.run_check(self.keys({("cs", 0): [H("elsewhere")]}))
        self.assertEqual(rc, 1); self.assertIn("MOVED but accounted `noprog`", out)

    def test_stale_accounting_fails_and_unknown_entry_fails(self):
        self.account(("gfx", "/".join(pair("x")), "g16", "moved", "re-key pending on the alias branch, validation run booked"))
        rc, out = self.run_check(self.keys({}))
        self.assertEqual(rc, 1); self.assertIn("stale accounting", out)
        self.account(("gfx", "/".join(pair("nosuch")), "g16", "moved", "re-key pending on the alias branch, validation run booked"))
        rc, out = self.run_check(self.keys({}))
        self.assertEqual(rc, 1); self.assertIn("accounting names no row", out)
        self.account(("cs", H("kkA"), "*", "noprog", "kernel only compiled in the final boss level, not in the prepare cache"))
        rc, out = self.run_check(self.keys({}))                                              # a noprog entry that became checkable: information
        self.assertEqual(rc, 0, out); self.assertIn("accounting no longer needed", out)

    def test_tool_failure_and_missing_evidence_are_never_accountable(self):
        for v, word in (("no keys: vk error", "NOKEY"), (None, "NOEVID")):
            self.account(("gfx", "/".join(pair("x")), "g16", "moved", "re-key pending on the alias branch, validation run booked"))
            rc, out = self.run_check(self.keys({("gfx", 0): v}))
            self.assertEqual(rc, 1); self.assertIn("FAIL %s" % word, out); self.assertIn("not accountable", out)

    def test_several_programs_bin_union(self):
        """A row missing in the prepare cache and present in a level cache of the same code generation is judged by the level cache."""
        k1 = self.keys({("cs", 0): "not in programs.bin (kernel)"}, "k1.jsonl"); k2 = self.keys({}, "k2.jsonl")
        self.assertEqual(self.run_check([k1, k2])[0], 0)
        k3 = self.keys({("cs", 0): [H("moved")]}, "k3.jsonl")
        self.assertEqual(self.run_check([k1, k3])[0], 1)

    def test_mesh_and_vsonly_rows_are_rows(self):
        self.lists(["A"], ["x"], gfx_extra=[grow("m", variant="mesh-twin")])
        rows = kg.load_rows(self.g)
        self.assertEqual(len([r for r in rows if r["list"] == "gfx"]), 2)
        rc, out = self.run_check(self.keys({("gfx", 1): pair("meshmoved")}))
        self.assertEqual(rc, 1); self.assertIn("FAIL MOVED: gfx %s" % "/".join(pair("m")), out)


class Evidence(Base):
    def test_spirv_of_another_code_generation_is_stale(self):
        rc, out = self.run_check(self.keys({}, fp="cd" * 32))
        self.assertEqual(rc, 1); self.assertIn("STALE SPIR-V", out)

    def test_keys_of_another_list_are_refused(self):
        k = self.keys({})
        self.lists(["A", "B"], ["x", "y", "new"])
        rc, out = self.run_check(k)
        self.assertEqual(rc, 1); self.assertIn("is for another metal-gfx-pairs.tsv", out); self.assertIn("FAIL NOEVID", out)

    def test_keys_without_meta_or_fingerprint_are_refused(self):
        k = self.keys({}); os.remove(k + ".meta.json")
        self.assertIn("no readable .meta.json", self.run_check(k)[1])
        self.assertIn("names no Kyty code generation", self.run_check(self.keys({}, "k2.jsonl", fp=""))[1])

    def test_line_shift_is_caught(self):
        k = self.keys({})
        txt = kg._read(k).replace('"row": 5,', '"row": 6,', 1).replace('"row": 6,', '"row": 5,', 2).replace('"row": 5,', '"row": 6,', 1)
        put(k, txt)
        rc, out = self.run_check(k)
        self.assertEqual(rc, 1)


class Record(Base):
    def test_record_written_only_on_pass_with_lib(self):
        self.assertEqual(self.run_check(self.keys({("gfx", 0): pair("gone")}), record=True)[0], 1)
        self.assertFalse(os.path.exists(os.path.join(self.g, kg.RECORD_NAME)))
        rc, out = self.run_check(self.keys({}), record=True, lib=False)
        self.assertEqual(rc, 1); self.assertIn("NOT recorded", out)
        rc, out = self.run_check(self.keys({}), record=True)
        self.assertEqual(rc, 0, out)
        rec = kg.parse_record(kg._read(os.path.join(self.g, kg.RECORD_NAME)))
        self.assertEqual((rec["result"], rec["kyty"], rec["kyty_codegen"], rec["kk_tree"]), ("PASS", "1" * 40, FP, "c" * 40))
        self.assertEqual(kg.main(["record-check", "--repo", self.repo, "--kyty-lib", self.lib]), 0)

    def problems(self, lib=None):
        i = kg.repo_inputs(self.repo, "PPSA00000")
        return kg.record_problems(i["record"], i["lock"], i["kk_tree"], i["shas"], i["account_sha"], lib)

    def test_stale_record_fails(self):
        self.assertEqual(self.problems(), ["no keyguard record"])
        self.run_check(self.keys({}), record=True)
        self.assertEqual(self.problems(LIB), [])
        self.lock("2" * 40)                                                                  # Kyty pin moved
        self.assertTrue(any("STALE: recorded for Kyty" in p for p in self.problems()))
        self.lock("1" * 40)
        open(os.path.join(self.repo, "kosmickrisp", "EXPECTED_TREE"), "w").write("d" * 40 + "\n")   # KosmicKrisp patch series moved
        self.assertTrue(any("KosmicKrisp tree" in p for p in self.problems()))
        put(os.path.join(self.repo, "kosmickrisp", "EXPECTED_TREE"), "c" * 40 + "\n")
        self.lists(["A", "B"], ["x", "y", "z"])                                              # a row was added
        self.assertTrue(any("another metal-gfx-pairs.tsv" in p for p in self.problems()))
        self.lists(["A", "B"], ["x", "y"])
        self.assertEqual(self.problems(), [])
        self.account(("gfx", "/".join(pair("x")), "g16", "moved", "an accounting edit after the record"))
        self.assertTrue(any(kg.ACCOUNT_NAME in p for p in self.problems()))
        os.remove(os.path.join(self.g, kg.ACCOUNT_NAME))
        self.assertTrue(any("code generation" in p for p in self.problems(LIB.replace(FP.encode(), b"ef" * 32))))   # another library
        self.assertEqual(kg.main(["record-check", "--repo", self.repo]), 0)

    def test_failed_record_is_not_a_record(self):
        self.run_check(self.keys({}), record=True)
        p = os.path.join(self.g, kg.RECORD_NAME)
        text = kg._read(p).replace("result=PASS", "result=FAIL")
        with open(p, "w") as f:
            f.write(text)
        self.assertIn("the record is not a PASS", self.problems())

    def test_carry_only_for_the_same_code_generation(self):
        import contextlib
        import io
        self.run_check(self.keys({}), record=True)
        self.lock("2" * 40)
        other = os.path.join(self.t, "other.dylib"); put(other, LIB.replace(FP.encode(), b"ef" * 32))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(kg.main(["carry", "--repo", self.repo, "--game", "PPSA00000", "--kyty-lib", other]), 1)
            self.assertTrue(self.problems())
            self.assertEqual(kg.main(["carry", "--repo", self.repo, "--game", "PPSA00000", "--kyty-lib", self.lib]), 0)
        self.assertEqual(self.problems(LIB), [])
        self.assertIn("carried=", kg._read(os.path.join(self.g, kg.RECORD_NAME)))

    def test_propose_prints_lines_the_check_refuses_until_written(self):
        import contextlib
        import io
        k = self.keys({("gfx", 0): pair("gone")})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kg.main(["propose", "--game", self.g, "--keys", k])
        line = buf.getvalue().strip().split("\t")
        self.assertEqual(line[:4], ["gfx", "/".join(pair("x")), "g16", "moved"])
        self.account(tuple(line))
        self.assertIn("the reason must say WHY", self.run_check(k)[1])


class CheckerRule(Base):
    """scripts/nps5_check.py: the tree's record must be the tree's (Kyty pin, KosmicKrisp tree, lists); no record = warning."""
    def chk(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("nps5_check", os.path.join(REPO, "scripts", "nps5_check.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod

    def sibling(self, path):
        p = os.path.join(self.repo, path)
        return kg._read(p, "rb") if os.path.isfile(p) else b""

    def test_record_rule(self):
        chk = self.chk()
        bad, warn = chk.check_keyguard_record("PPSA00000", self.sibling)
        self.assertEqual(bad, []); self.assertEqual(len(warn), 1); self.assertIn("no keyguard record", warn[0])   # rows, no record: warning
        self.run_check(self.keys({}), record=True)
        self.assertEqual(chk.check_keyguard_record("PPSA00000", self.sibling), ([], []))
        self.lock("2" * 40)                                                                  # components.lock moves Kyty without a fresh record
        bad, warn = chk.check_keyguard_record("PPSA00000", self.sibling)
        self.assertEqual(len(bad), 1); self.assertIn("STALE: recorded for Kyty", bad[0])
        self.lock("1" * 40)
        put(os.path.join(self.repo, "kosmickrisp", "EXPECTED_TREE"), "e" * 40 + "\n")        # KosmicKrisp EXPECTED_TREE moves
        self.assertIn("KosmicKrisp tree", chk.check_keyguard_record("PPSA00000", self.sibling)[0][0])
        self.assertEqual(chk.check_keyguard_record("PPSA99999", self.sibling), ([], []))     # a title with no rows and no record: nothing

    def test_file_formats(self):
        chk = self.chk()
        self.run_check(self.keys({}), record=True)
        self.assertEqual(chk.check_keyguard_text("keyguard.record", self.sibling("games/PPSA00000/keyguard.record")), [])
        ok = (kg.ACCOUNT_FORMAT + "\n# why\ngfx\t%s\tg16\tmoved\tre-key pending on the alias branch, validation run booked\ncs\t%s\t*\tnoprog\tkernel only compiled in the final boss level\n"
              % ("/".join(pair("x")), H("kkA"))).encode()
        self.assertEqual(chk.check_keyguard_text("keyguard-account.tsv", ok), [])
        self.assertTrue(chk.check_keyguard_text("keyguard-account.tsv", b"float4 main0(float4 v) { return v; }\n"))
        self.assertTrue(chk.check_keyguard_text("keyguard-account.tsv", ok + b"gfx\tnotakey\tg16\tmoved\ta reason that is long enough\n"))
        self.assertTrue(chk.check_keyguard_text("keyguard.record", b"result=PASS\npath=/somewhere/private\n"))


if __name__ == "__main__":
    unittest.main()
