#!/usr/bin/env python3
"""Tests for scripts/nps5lib/hw.py (hardware detection, machine class, derived limits) and the hardware conditions of
games/game.py. No real hardware is read except in one smoke test; machines are fake sysctl tables or specs."""
import importlib.util, io, os, sys, tempfile, unittest, contextlib
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
def put(path, text, mode="w"):
    with open(path, mode) as f: f.write(text)
def load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", *path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
hw = load("hw", "scripts", "nps5lib", "hw.py")
game = load("game", "games", "game.py")

REF = {"machdep.cpu.brand_string": "Apple M4 Pro", "hw.memsize": str(24 << 30), "hw.perflevel0.physicalcpu": "10",
       "hw.perflevel1.physicalcpu": "4", "kern.osproductversion": "26.6.2", "kern.osversion": "25G83", "hw.model": "Mac16,8"}
BATT_AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged\n"


def fake(table, batt=BATT_AC, name="MacBook Pro"):
    def run(argv, timeout=10):
        if argv[0].endswith("sysctl"): return table.get(argv[-1], "") + "\n"
        if argv[0].endswith("pmset"): return batt
        if argv[0].endswith("system_profiler"): return '{"SPHardwareDataType":[{"machine_name":"%s"}]}' % name
        return ""
    return run


class Detect(unittest.TestCase):
    def test_reference_machine(self):
        h = hw.detect({}, fake(REF), full=True)
        self.assertEqual((h["class"], h["chip_key"], h["gen"], h["gpufam"]), ("m4pro-24g", "m4pro", 4, "g16"))
        self.assertEqual((h["pcores"], h["ecores"], h["ram_gb"], h["ram_mb"]), (10, 4, 24, 24576))
        self.assertEqual((h["os"], h["os_major"], h["osbuild"], h["model"]), ("26.6.2", 26, "25G83", "Mac16,8"))
        self.assertEqual((h["power"], h["fan"], h["machine_name"]), ("ac", "yes", "MacBook Pro"))
        self.assertEqual(hw.cache_key(h), "g16-25G83")
        self.assertEqual(hw.verdict(h)[0], "tested")

    def test_chip_names(self):
        for brand, key, gen in (("Apple M1", "m1", 1), ("Apple M2 Pro", "m2pro", 2), ("Apple M3 Max", "m3max", 3),
                                ("Apple M2 Ultra", "m2ultra", 2), ("Apple M5", "m5", 5), ("Apple M10 Pro", "m10pro", 10),
                                ("Intel(R) Core(TM) i9", "unknown", 0), ("", "unknown", 0), ("Apple processor", "unknown", 0)):
            self.assertEqual(hw.chip_key(brand), (key, gen), brand)

    def test_gpu_family_by_generation(self):
        for chip, fam in (("m1", "g13"), ("m2max", "g14"), ("m3pro", "g15"), ("m4", "g16"), ("m5", "g17")):
            self.assertEqual(hw.parse_spec("%s-16g" % chip)["gpufam"], fam)
        self.assertEqual(hw.parse_spec("chip=m9 ram=32")["gpufam"], "unknown")

    def test_not_a_mac(self):
        self.assertIsNone(hw.detect({}, fake({})))
        self.assertIsNone(hw.detect({}, fake({"machdep.cpu.brand_string": "Intel(R) Core(TM) i7", "hw.memsize": str(16 << 30)})))
        self.assertIsNone(hw.detect({"NPS5_HW": "none"}, fake(REF)))

    def test_ram_rounding(self):
        for mem, gb in ((8 << 30, 8), (16 << 30, 16), (18 << 30, 18), (36 << 30, 36), ((24 << 30) - (200 << 20), 24), (128 << 30, 128)):
            t = dict(REF, **{"hw.memsize": str(mem)})
            self.assertEqual(hw.detect({}, fake(t))["ram_gb"], gb)

    def test_no_perf_levels(self):
        t = {k: v for k, v in REF.items() if "perflevel" not in k}; t["hw.physicalcpu"] = "8"
        h = hw.detect({}, fake(t)); self.assertEqual((h["pcores"], h["ecores"]), (8, 0))

    def test_power_and_fan(self):
        h = hw.detect({}, fake(REF, "Now drawing from 'Battery Power'\n -InternalBattery-0\t80%\n", "MacBook Air"), full=True)
        self.assertEqual((h["power"], h["fan"]), ("battery", "no"))
        h = hw.detect({}, fake(REF, "Now drawing from 'AC Power'\n", "Mac mini"), full=True)      # desktop: no battery line
        self.assertEqual((h["power"], h["fan"]), ("ac", "yes"))
        h = hw.detect({}, fake(REF, ""))                                                           # pmset failed, no profiler
        self.assertEqual((h["power"], h["fan"]), ("unknown", "unknown"))

    def test_env_override_and_spec(self):
        h = hw.detect({"NPS5_HW": "chip=m2 ram=16 pcores=4 ecores=4 os=26.1 osbuild=25B78 name=MacBook_Air power=battery"}, fake(REF))
        self.assertEqual((h["class"], h["gpufam"], h["os_major"], h["fan"], h["power"]), ("m2-16g", "g14", 26, "no", "battery"))
        h = hw.parse_spec("m3pro-36g"); self.assertEqual((h["pcores"], h["ecores"], h["chip"]), (6, 6, "Apple M3 Pro"))
        for bad in ("", "ram=16", "chip=m1", "m1-16g colour=red", "chip=intel ram=16", "nonsense"):
            with self.assertRaises(ValueError, msg=bad): hw.parse_spec(bad)

    def test_this_machine_smoke(self):
        h = hw.detect({})                      # real sysctl; None on a non-Mac CI host
        if h is not None:
            self.assertRegex(h["class"], r"^m\d+(pro|max|ultra)?-\d+g$")
            self.assertGreater(h["pcores"], 0)


class Defaults(unittest.TestCase):
    def test_reference_values_exact(self):
        d = hw.defaults(hw.parse_spec("m4pro-24g"))
        self.assertEqual(d, {"NPS5_SELF_MEM_LIMIT_MB": 18944, "vram_budget_mb": 6144, "NPS5_GUARD_RUN_CAP_MB": 20480,
                             "NPS5_GUARD_FAST_MB": 18432, "NPS5_GUARD_SWAP_MB": 12288, "NPS5_GUARD_HOLD_SWAP_MB": 12288,
                             "NPS5_GUARD_HOLD_LOAD": 12, "NPS5_FUNC_HOLD_LOAD": 24, "NPS5_BUILD_JOBS": 4,
                             "journal_threads": 4, "skip_threads": 3})

    def test_known_points(self):
        d = hw.defaults(hw.parse_spec("m1-16g"))
        self.assertEqual((d["NPS5_SELF_MEM_LIMIT_MB"], d["vram_budget_mb"], d["NPS5_GUARD_HOLD_LOAD"], d["NPS5_BUILD_JOBS"],
                          d["journal_threads"], d["skip_threads"]), (12629, 4096, 6, 2, 2, 1))
        d = hw.defaults(hw.parse_spec("m4max-64g"))
        self.assertEqual((d["NPS5_SELF_MEM_LIMIT_MB"], d["vram_budget_mb"], d["NPS5_GUARD_HOLD_LOAD"], d["journal_threads"]), (50517, 6144, 14, 4))

    def test_invariants_over_every_machine(self):
        n = 0
        for ram in (8, 16, 18, 24, 32, 36, 48, 64, 96, 128, 192, 512):
            for p in range(1, 33):
                for e in (0, 2, 4, 6, 8):
                    d = hw.defaults(dict(ram_mb=ram * 1024, pcores=p, ecores=e)); mb = ram * 1024; n += 1
                    # the title's own limit comes first, then fast polling is already on, then the guard's kill; all below RAM
                    self.assertLess(d["NPS5_GUARD_FAST_MB"], d["NPS5_SELF_MEM_LIMIT_MB"])
                    self.assertLess(d["NPS5_SELF_MEM_LIMIT_MB"], d["NPS5_GUARD_RUN_CAP_MB"])
                    self.assertLess(d["NPS5_GUARD_RUN_CAP_MB"], mb)
                    self.assertGreaterEqual(mb - d["NPS5_SELF_MEM_LIMIT_MB"], mb * 11 // 48)        # at least 11/48 of RAM left
                    self.assertLessEqual(d["vram_budget_mb"], 6144); self.assertGreaterEqual(d["vram_budget_mb"], 2048)
                    self.assertLessEqual(d["vram_budget_mb"], max(2048, mb // 4))
                    self.assertTrue(2 <= d["NPS5_BUILD_JOBS"] <= 4 and 2 <= d["journal_threads"] <= 4 and 1 <= d["skip_threads"] <= 3)
                    self.assertGreaterEqual(d["NPS5_GUARD_HOLD_LOAD"], 4)
                    self.assertEqual(d["NPS5_FUNC_HOLD_LOAD"], 2 * d["NPS5_GUARD_HOLD_LOAD"])
                    if p >= 10: self.assertEqual((d["journal_threads"], d["skip_threads"], d["NPS5_BUILD_JOBS"]), (4, 3, 4))
        self.assertEqual(n, 12 * 32 * 5)

    def test_monotonic(self):
        prev = None
        for ram in range(8, 257):
            d = hw.defaults(dict(ram_mb=ram * 1024, pcores=10, ecores=4))
            if prev:
                for k in ("NPS5_SELF_MEM_LIMIT_MB", "vram_budget_mb", "NPS5_GUARD_RUN_CAP_MB", "NPS5_GUARD_FAST_MB", "NPS5_GUARD_SWAP_MB"):
                    self.assertGreaterEqual(d[k], prev[k], (ram, k))
            prev = d
        prev = None
        for p in range(1, 40):
            d = hw.defaults(dict(ram_mb=24576, pcores=p, ecores=4))
            if prev:
                for k in ("journal_threads", "skip_threads", "NPS5_BUILD_JOBS", "NPS5_GUARD_HOLD_LOAD"):
                    self.assertGreaterEqual(d[k], prev[k], (p, k))
            prev = d

    def test_env_lines_keep_what_is_set(self):
        h = hw.parse_spec("m1-16g")
        lines = hw.env_lines(h, {})
        self.assertIn("export NPS5_HW_CLASS=m1-16g", lines); self.assertIn("export NPS5_SELF_MEM_LIMIT_MB=12629", lines)
        self.assertIn("export NPS5_HW_CACHE_KEY=g13-unknown", lines)
        lines = hw.env_lines(h, {"NPS5_SELF_MEM_LIMIT_MB": "9000", "NPS5_BUILD_JOBS": "1"})
        self.assertFalse([l for l in lines if "SELF_MEM" in l or "BUILD_JOBS" in l])
        for l in lines: self.assertRegex(l, r"^export NPS5_[A-Z_]+=[A-Za-z0-9-]+$")      # safe to eval

    def test_cache_key_is_shell_safe(self):
        h = hw.parse_spec("chip=m4 ram=16 osbuild=25G83;rm"); self.assertEqual(hw.cache_key(h), "g16-25G83rm")


class Verdict(unittest.TestCase):
    def v(self, spec): return hw.verdict(hw.parse_spec(spec))

    def test_statuses(self):
        self.assertEqual(self.v("chip=m4pro ram=24 os=26.6.2")[0], "tested")
        self.assertEqual(self.v("chip=m4pro ram=24 os=27.0")[0], "untested")
        self.assertEqual(self.v("chip=m4pro ram=48 os=26.6")[0], "untested")
        self.assertEqual(self.v("chip=m4 ram=16 os=26.6")[0], "experimental")
        self.assertEqual(self.v("chip=m2 ram=8 os=26.6")[0], "unsupported")
        self.assertEqual(self.v("chip=m4pro ram=24 os=15.5")[0], "unsupported")

    def test_notes(self):
        j = lambda spec: " ".join(self.v(spec)[1])
        self.assertIn("384 threads", j("chip=m1 ram=16 os=26.1")); self.assertIn("384 threads", j("chip=m2max ram=64 os=26.1"))
        self.assertNotIn("384 threads", j("chip=m3 ram=24 os=26.1"))
        self.assertIn("agxdis refuses", j("chip=m5 ram=24 os=26.1"))
        self.assertIn("no fan", j("chip=m3 ram=24 os=26.1 name=MacBook_Air")); self.assertNotIn("no fan", j("chip=m3 ram=24 os=26.1 name=MacBook_Pro"))
        self.assertIn("battery", j("chip=m3 ram=24 os=26.1 power=battery"))
        self.assertIn("exit code 86", j("chip=m3 ram=16 os=26.1"))
        self.assertIn("Metal 4", j("chip=m3 ram=24 os=15.1"))
        self.assertEqual(self.v("chip=m4pro ram=24 os=26.6.2 power=ac name=MacBook_Pro")[1], [])

    def test_doctor_output_and_exit_code(self):
        for spec, rc, word in (("chip=m4pro ram=24 os=26.6.2 osbuild=25G83", 0, "TESTED"), ("m1-16g", 0, "EXPERIMENTAL"), ("chip=m1 ram=8 os=26.0", 1, "UNSUPPORTED")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out): r = hw.main(["--hw", spec, "doctor"], {})
            self.assertEqual(r, rc); self.assertIn("support: " + word, out.getvalue()); self.assertIn("machine class", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out): r = hw.main(["doctor"], {"NPS5_HW": "none"})
        self.assertEqual(r, 1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err): self.assertEqual(hw.main(["--hw", "junk", "class"], {}), 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(hw.main(["--hw", "m2pro-32g", "class"], {}), 0)
        self.assertEqual(out.getvalue().strip(), "m2pro-32g")


# ---------------------------------------------------------------- game.py hardware conditions
GAME = """title_id\tPPSA00001
name\tFixture
build\t01.001\teboot=0123456789abcdef
package\t01.001
gpu\tkyty
stage\tS0
env\tBASE=1
env\tBUDGET=6144\tgpu=kyty\tmeasured
env\tBUDGET=4096\tgpu=kyty,ram<=16\tuntested
env\tBIG=1\tram>=32
env\tSMALLCPU=1\tcores<=4
env\tBIGCPU=1\tcores>=10
env\tREF=1\thw=m4pro-24g
env\tCHIP=1\thw=m4pro
env\tNOTREF=1\thw!=m4pro-24g
env\tOLDGPU=1\tgpufam=g13
env\tNOTG16=1\tgpufam!=g16
env\tNEWOS=1\tos>=27
env\tOLDOS=1\tos<=26
env\tCACHE=id.{gpufam}.{osbuild}.{hw}\thw!=m4pro-24g
env\tBOTH=1\tgpu=anyps5,ram<=16
"""


class GameHw(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = os.path.join(self.tmp.name, "PPSA00001"); os.makedirs(d)
        put(os.path.join(d, "game.tsv"), GAME); self.p = game.Profile(d, True)
        self._rows = game.hw_default_rows; game.hw_default_rows = lambda: []          # the repository table is tested apart
    def tearDown(self):
        game.hw_default_rows = self._rows; self.tmp.cleanup()
    def env(self, spec, **kw):
        h = hw.parse_spec(spec) if spec else None
        return {k: v for k, v, _ in game.compose_env(self.p, hw=h, **kw)}

    def test_no_hardware_no_condition_holds(self):
        self.assertEqual(self.env(None), {"NPS5_TITLE_ID": "PPSA00001", "BASE": "1", "BUDGET": "6144"})

    def test_reference(self):
        e = self.env("chip=m4pro ram=24 os=26.6.2 osbuild=25G83")
        self.assertEqual(set(e), {"NPS5_TITLE_ID", "BASE", "BUDGET", "BIGCPU", "REF", "CHIP", "OLDOS"}); self.assertEqual(e["BUDGET"], "6144")

    def test_small_machine(self):
        e = self.env("chip=m1 ram=16 os=27.0 osbuild=26A100")
        self.assertEqual(set(e), {"NPS5_TITLE_ID", "BASE", "BUDGET", "SMALLCPU", "NOTREF", "OLDGPU", "NOTG16", "NEWOS", "CACHE"})
        self.assertEqual(e["BUDGET"], "4096"); self.assertEqual(e["CACHE"], "id.g13.26A100.m1-16g")
        self.assertNotIn("BOTH", e); self.assertIn("BOTH", self.env("m1-16g", gpu="anyps5"))

    def test_boundaries(self):
        self.assertEqual(self.env("m4-16g")["BUDGET"], "4096"); self.assertEqual(self.env("m3pro-18g")["BUDGET"], "6144")
        self.assertIn("BIG", self.env("m4max-32g")); self.assertNotIn("BIG", self.env("m4pro-24g"))
        self.assertIn("SMALLCPU", self.env("chip=m4 ram=24 pcores=4")); self.assertNotIn("SMALLCPU", self.env("chip=m4 ram=24 pcores=5"))
        self.assertIn("CHIP", self.env("m4pro-48g")); self.assertNotIn("REF", self.env("m4pro-48g")); self.assertIn("NOTREF", self.env("m4pro-48g"))

    def test_unknown_field_never_holds(self):
        e = self.env("chip=m9 ram=24 pcores=0")          # unknown GPU family, unknown cores, unknown OS
        for k in ("OLDGPU", "NOTG16", "SMALLCPU", "BIGCPU", "NEWOS", "OLDOS"): self.assertNotIn(k, e)

    def test_placeholders_without_hardware(self):
        self.assertEqual(game.hw_fill("a.{gpufam}.{osbuild}.{hw}", None), "a.unknown.unknown.unknown")
        self.assertEqual(game.hw_fill("plain{x}", None), "plain{x}")
        self.assertEqual(game.hw_fill("x.{osbuild}", {"osbuild": "25G83;$(id)"}), "x.25G83id")

    def test_parse_when(self):
        ok = "gpu=kyty,ram<=16,cores>=4,hw=m4pro-24g,hw!=m1,gpufam=g13,gpufam!=g16,os>=26,os<=27,ram>=8,cores<=6"
        c, e = game.parse_when(ok); self.assertIsNone(e); self.assertEqual(len(c), 11)
        for bad in ("ram<16", "ram=16", "ram<=x", "cores<=", "hw=", "gpufam=G 16", "os>=26.1", "ram<=16;gpu=kyty", "cpu<=4", "hw==m1"):
            self.assertIsNotNone(game.parse_when(bad)[1], bad)

    def test_check_accepts_and_rejects(self):
        schema = game.load_schema(); errs, _ = game.check_profile(self.p, schema)
        self.assertFalse([x for x in errs if "condition" in x], errs)
        put(os.path.join(self.p.dir, "game.tsv"), "env\tX=1\tram<16\n", "a")
        errs, _ = game.check_profile(game.Profile(self.p.dir, True), schema)
        self.assertTrue([x for x in errs if "bad condition" in x])

    def test_mem_limit_follows_the_machine(self):
        put(os.path.join(self.p.dir, "game.tsv"), "env\tNPS5_SELF_MEM_LIMIT_MB=18944\n", "a")
        p = game.Profile(self.p.dir, True)
        game.compose_env(p, hw=None); game.compose_env(p, hw=hw.parse_spec("m4pro-24g")); game.compose_env(p, hw=hw.parse_spec("m4max-64g"))
        with self.assertRaises(game.Err) as cm: game.compose_env(p, hw=hw.parse_spec("m1-16g"))
        self.assertEqual(cm.exception.code, 78); self.assertIn("12629", str(cm.exception))

    def test_cli_hw_option(self):
        def run(*a, environ=None):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = game.main(list(a), dict(environ or {}, NPS5_GAMES_DIR=self.tmp.name, NPS5_GAMES_FIRST="1"))
            return rc, out.getvalue(), err.getvalue()
        rc, out, _ = run("PPSA00001", "--env", "--hw", "m1-16g"); self.assertEqual(rc, 0); self.assertIn("BUDGET=4096\n", out)
        rc, out, _ = run("PPSA00001", "--env", "--hw", "none"); self.assertIn("BUDGET=6144\n", out); self.assertNotIn("NOTREF", out)
        rc, out, _ = run("PPSA00001", "--env", environ={"NPS5_HW": "m2-16g"}); self.assertIn("BUDGET=4096\n", out); self.assertNotIn("OLDGPU", out)
        rc, out, _ = run("PPSA00001", "--env", "--hw", "m1-16g", "--explain"); self.assertIn("BUDGET=4096\t# env [gpu=kyty,ram<=16]", out)
        rc, _, err = run("PPSA00001", "--env", "--hw", "toaster"); self.assertEqual(rc, 2); self.assertIn("--hw", err)


class RepoTables(unittest.TestCase):
    """The committed tables: games/hw-defaults.tsv and the real profiles."""
    def rows(self): return [c for _, c in game.hw_default_rows()]

    def test_hw_defaults_check_clean(self):
        self.assertEqual(game.check_hw_defaults(), []); self.assertTrue(self.rows())

    def test_reference_machine_matches_no_row(self):
        ref = hw.parse_spec("chip=m4pro ram=24 os=26.6.2 osbuild=25G83")
        for gpu in ("kyty", "anyps5"):
            ctx = dict(gpu=gpu, version="", eboot="", scene_names=set(), hw=ref, **{"class": None})
            for c in self.rows():
                self.assertFalse(game.cond_holds(game.parse_when(c[2])[0], ctx), c)

    def test_thread_rows_equal_the_formulas(self):
        keys = {"KYTY_PIPELINE_JOURNAL_THREADS": ("journal_threads", 4), "KYTY_PIPELINE_SKIP_THREADS": ("skip_threads", 3)}
        for p in range(1, 25):
            h = dict(hw.parse_spec("m4pro-24g"), pcores=p); d = hw.defaults(h); got = {}
            ctx = dict(gpu="kyty", version="", eboot="", scene_names=set(), hw=h, **{"class": None})
            for c in self.rows():
                if game.cond_holds(game.parse_when(c[2])[0], ctx):
                    k, _, v = c[1].partition("="); got[k] = int(v)
            for k, (name, code_default) in keys.items():
                self.assertEqual(got.get(k, code_default), d[name], (p, k))

    def test_bad_hw_default_rows_are_caught(self):
        orig = game.hw_default_rows
        try:
            for row, word in ((["env", "A=1", "gpu=kyty", "why"], "hardware condition"), (["env", "A=1", "ram<=16"], "reason"),
                              (["gpu", "kyty"], "only env rows"), (["env", "A", "ram<=16", "x"], "KEY=VALUE"), (["env", "A=1", "ram<16", "x"], "bad condition")):
                game.hw_default_rows = lambda row=row: [(1, row)]
                self.assertTrue([e for e in game.check_hw_defaults() if word in e], row)
        finally:
            game.hw_default_rows = orig

    def test_astro_profile(self):
        d = os.path.join(HERE, "..", "games", "PPSA21564")
        if not os.path.isfile(os.path.join(d, "game.tsv")): self.skipTest("no Astro profile")
        p = game.Profile(d); env = lambda spec, gpu: {k: v for k, v, _ in game.compose_env(p, gpu=gpu, hw=hw.parse_spec(spec) if spec else None)}
        ref, none = env("chip=m4pro ram=24 os=26.6.2 osbuild=25G83", "kyty"), env(None, "kyty")
        self.assertEqual(list(ref.items()), list(none.items()))                 # the reference machine = the profile without hardware
        self.assertEqual(ref["KYTY_VRAM_BUDGET_MB"], "6144"); self.assertNotIn("KYTY_TEX_MIP_SKIP", ref)
        self.assertEqual(ref["KK_METAL_CACHE_ID"], "com.nativeps5.kyty.PPSA21564")
        small = env("chip=m4 ram=16 os=26.6.2 osbuild=25G83", "kyty")
        self.assertEqual((small["KYTY_VRAM_BUDGET_MB"], small["KYTY_TEX_MIP_SKIP"], small["KYTY_TEXTURE_RESIDENT_TRIM"]), ("4096", "2", "1"))
        self.assertEqual(small["KK_METAL_CACHE_ID"], "com.nativeps5.kyty.PPSA21564.g16.25G83")
        self.assertEqual((small["KYTY_PIPELINE_JOURNAL_THREADS"], small["KYTY_PIPELINE_SKIP_THREADS"]), ("2", "1"))
        big = env("chip=m4max ram=64 os=26.6.2 osbuild=25G83", "kyty")
        self.assertEqual(big["KYTY_VRAM_BUDGET_MB"], "6144"); self.assertNotIn("KYTY_TEX_MIP_SKIP", big)
        self.assertEqual({k: v for k, v in big.items() if k != "KK_METAL_CACHE_ID"}, {k: v for k, v in ref.items() if k != "KK_METAL_CACHE_ID"})
        a_ref, a_small = env("m4pro-24g", "anyps5"), env("m1-16g", "anyps5")
        self.assertNotIn("APS5_TEX_MIP_SKIP_LEVELS", a_ref); self.assertEqual(a_small["APS5_TEX_MIP_SKIP_LEVELS"], "2")
        self.assertEqual(set(a_small) - set(a_ref), {"APS5_TEX_MIP_SKIP_LEVELS"})
        for row in p.get("env"):                                               # every hardware row but the cache id says untested
            conds = game.parse_when(row[1] if len(row) > 1 else "-")[0]
            if any(game.is_hw_cond(k) for k, _ in conds) and not row[0].startswith("KK_METAL_CACHE_ID"):
                self.assertIn("untested", row[2], row)


class CachePerHardware(unittest.TestCase):
    """scripts/nps5lib/run.py keep_cache_per_hardware: a work dir's compiled caches belong to one GPU family + OS build."""
    def setUp(self):
        self.run = load("run", "scripts", "nps5lib", "run.py"); self.tmp = tempfile.TemporaryDirectory()
        self.c = os.path.join(self.tmp.name, "home", "cache"); self.msgs = []
    def tearDown(self): self.tmp.cleanup()
    def keep(self, key): return self.run.keep_cache_per_hardware(self.c, key, self.msgs.append)
    def stamp(self):
        with open(os.path.join(self.c, ".hwkey")) as f: return f.read().strip()

    def test_cycle(self):
        self.assertIsNone(self.keep("g16-25G83")); self.assertEqual(self.stamp(), "g16-25G83")
        put(os.path.join(self.c, "pipelines"), "warm")
        self.assertIsNone(self.keep("g16-25G83")); self.assertTrue(os.path.isfile(os.path.join(self.c, "pipelines"))); self.assertEqual(self.msgs, [])
        self.assertEqual(self.keep("g16-25H10"), "g16-25G83")                        # macOS update
        self.assertFalse(os.path.exists(os.path.join(self.c, "pipelines"))); self.assertEqual(self.stamp(), "g16-25H10")
        self.assertTrue(os.path.isfile(self.c + ".g16-25G83/pipelines")); self.assertIn("Set aside as cache.g16-25G83", self.msgs[0])
        self.assertEqual(self.keep("g13-25H10"), "g16-25H10")                        # the work dir was copied to an M1
        self.assertTrue(os.path.isdir(self.c + ".g16-25H10"))
        self.assertEqual(self.keep("g16-25G83"), "g13-25H10")                        # back: the caches of that key return
        with open(os.path.join(self.c, "pipelines")) as f: self.assertEqual(f.read(), "warm")
        self.assertFalse(os.path.exists(self.c + ".g16-25G83"))

    def test_unstamped_cache_is_adopted(self):
        os.makedirs(self.c); put(os.path.join(self.c, "pipelines"), "warm")       # a cache from before the stamp existed
        self.assertIsNone(self.keep("g16-25G83")); self.assertTrue(os.path.isfile(os.path.join(self.c, "pipelines")))

    def test_no_key_or_bad_key_does_nothing(self):
        for k in (None, "", "../x", "a b", "g16/25"):
            self.assertIsNone(self.keep(k)); self.assertFalse(os.path.exists(self.c))

    def test_aside_name_taken(self):
        self.keep("a-1"); os.makedirs(self.c + ".a-1"); put(self.c + ".a-1/older", "x")
        self.keep("b-2")
        left = sorted(n for n in os.listdir(os.path.dirname(self.c)) if n.startswith("cache.a-1"))
        self.assertEqual(len(left), 2)                                              # nothing was overwritten or deleted

    def test_bad_stamp_content_is_ignored(self):
        os.makedirs(self.c); put(os.path.join(self.c, ".hwkey"), "../../etc\n")
        self.assertIsNone(self.keep("g16-25G83")); self.assertEqual(self.stamp(), "g16-25G83")


if __name__ == "__main__":
    unittest.main()
