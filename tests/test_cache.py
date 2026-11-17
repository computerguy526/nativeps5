#!/usr/bin/env python3
"""Fixture tests for scripts/nps5lib/cache.py: stack key / Metal id derivation, status on fresh / partial / prepared / stale dirs,
list merge and verify-list through a fake pipeline-list tool, prepare / record / check as dry runs and with a fake runner. No title is run."""
import io, os, shutil, sys, tempfile, unittest, warnings
warnings.simplefilter("ignore", ResourceWarning)
from contextlib import redirect_stdout, redirect_stderr
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib"))
import cache  # noqa: E402
import run as runmod  # noqa: E402

GAME = "title_id\tPPSA00001\nname\tFixture\nbuild\t01.001\teboot=0123456789abcdef\npackage\t01.001\ngpu\tkyty\ncanonical_lineage\tlin1\ncache\tkind=kyplst\tlimit_mb=448\tapp_ver=01.001.000\n"
SCENES = "boot\t-\t-\t-\t60\tnew\tboot\nlvl1\t-x\t-\t-\t60\tnew\tlevel\nlevel:*\t-lvl {name}\t-\t-\t60\tnew\ttemplate\n"
COMMITS = dict(kyty="aaaa111", kk="bbbb222", ps5recomp="cccc333")
# a fake kyty_pipeline_list: info prints counts from the file's first line, merge concatenates, export copies, check / verify succeed
TOOL = """#!/bin/bash
case $1 in
 info) echo "  title X, version 1, 2 things"; echo "  shaders $(head -1 $2 | cut -d' ' -f1); permutations $(head -1 $2 | cut -d' ' -f2), pipelines $(head -1 $2 | cut -d' ' -f3)"; echo "  permutations $(head -1 $2 | cut -d' ' -f2), pipelines $(head -1 $2 | cut -d' ' -f3)";;
 export) while [ $# -gt 0 ]; do [ "$1" = --out ] && out=$2; shift; done; echo "3 4 5" > $out;;
 merge) shift; [ "$1" = --out ] && out=$2 && shift 2; cat "$@" | sort -u | tail -1 > $out;;
 check) echo "check: ok";;
 verify) echo "verify: no shader code in the list";;
esac
"""


def make(tmp, list_bytes=b"1 2 3\n"):
    gd = os.path.join(tmp, "games", "PPSA00001"); os.makedirs(gd)
    open(os.path.join(gd, "game.tsv"), "w").write(GAME); open(os.path.join(gd, "scenes.tsv"), "w").write(SCENES)
    if list_bytes is not None: open(os.path.join(gd, "pipelines.kyplst"), "wb").write(list_bytes)
    tool = os.path.join(tmp, "tool.sh"); open(tool, "w").write(TOOL); os.chmod(tool, 0o755)
    os.makedirs(os.path.join(tmp, "app0"))
    os.makedirs(os.path.join(tmp, "titles"))
    open(os.path.join(tmp, "titles", "PPSA00001.env"), "w").write("APP0=%s\n" % os.path.join(tmp, "app0"))
    return dict(NPS5_GAMES_DIR=os.path.join(tmp, "games"), NPS5_GAMES_FIRST="1", NPS5_SCRATCH=os.path.join(tmp, "scratch"), NPS5_SELF_MEM_LIMIT_MB="18944",
                NPS5_PUBLIC_DIR=tmp, NPS5_KK_ICD="x", NPS5_HW_CACHE_KEY="g16-25G83", NPS5_KYPLST_TOOL=tool,
                NPS5_STACK_KYTY=COMMITS["kyty"], NPS5_STACK_KK=COMMITS["kk"], NPS5_STACK_RECOMP=COMMITS["ps5recomp"], PATH=os.environ["PATH"])


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.env = make(self.tmp)
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, **kw):
        return cache.context("PPSA00001", dict(self.env), **kw)

    def run_main(self, argv, runner=None, ctx=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cache.main(argv, dict(self.env), runner or cache.default_runner, ctx)
        return rc, out.getvalue(), err.getvalue()

    # ---- identity
    def test_key_changes_with_every_input(self):
        base = cache.stack_key("l", "k", "kk", "r", "hw")
        self.assertEqual(base, cache.stack_key("l", "k", "kk", "r", "hw"))
        self.assertEqual(len(base), 8)
        for args in (("L", "k", "kk", "r", "hw"), ("l", "K", "kk", "r", "hw"), ("l", "k", "KK", "r", "hw"), ("l", "k", "kk", "R", "hw"), ("l", "k", "kk", "r", "HW")):
            self.assertNotEqual(base, cache.stack_key(*args), args)

    def test_key_follows_the_list_bytes_and_the_hw_key(self):
        a = self.ctx()
        open(a.list_path, "ab").write(b"more\n")
        b = self.ctx()
        self.assertNotEqual(a.key, b.key)
        self.env["NPS5_HW_CACHE_KEY"] = "g17-26A1"
        self.assertNotEqual(b.key, self.ctx().key)
        c = self.ctx()
        self.assertEqual(c.metal, "com.nativeps5.kyty.PPSA00001." + c.key)
        self.assertTrue(c.dir.endswith("cache/PPSA00001/kyty-" + c.key))

    def test_no_kyplst_row_is_refused(self):
        open(os.path.join(self.tmp, "games", "PPSA00001", "game.tsv"), "a").write("")
        s = open(os.path.join(self.tmp, "games", "PPSA00001", "game.tsv")).read().replace("cache\tkind=kyplst", "cache\tkind=none")
        open(os.path.join(self.tmp, "games", "PPSA00001", "game.tsv"), "w").write(s)
        rc, _, err = self.run_main(["status", "PPSA00001"])
        self.assertEqual(rc, 2); self.assertIn("no `cache kind=kyplst`", err)

    # ---- status
    def test_status_fresh_partial_prepared_stale(self):
        c = self.ctx()
        self.assertEqual(cache.status(c)[0], "fresh")
        os.makedirs(c.dir)
        self.assertEqual(cache.status(c)[0], "partial")
        with open(os.path.join(c.dir, "prepared.tsv"), "w") as f:
            f.write("key=%s\nrc=0\npipelines=1260\n" % c.key); [f.write("%s=%s\n" % kv) for kv in c.identity().items()]
        st, lines = cache.status(c)
        self.assertEqual(st, "prepared"); self.assertIn("1260", "\n".join(lines))
        # the list grows: another key, the old directory is reported stale and names the changed field
        open(c.list_path, "ab").write(b"x\n")
        c2 = self.ctx()
        st, lines = cache.status(c2)
        self.assertEqual(st, "stale"); self.assertIn("changed: list", "\n".join(lines))
        # a KK commit change is named too
        self.env["NPS5_STACK_KK"] = "dddd444"
        st, lines = cache.status(self.ctx())
        self.assertEqual(st, "stale"); self.assertIn("list, kk", "\n".join(lines))

    def test_failed_prepare_is_not_prepared(self):
        c = self.ctx(); os.makedirs(c.dir)
        open(os.path.join(c.dir, "prepared.tsv"), "w").write("key=%s\nrc=1\n" % c.key)
        self.assertEqual(cache.status(c)[0], "partial")

    def test_status_exit_codes_and_unknown_commit_warning(self):
        self.env["NPS5_STACK_KK"] = ""
        self.env["NPS5_KK_WORK"] = "/nonexistent"; self.env["NPS5_KK_DIR"] = "/nonexistent"
        rc, out, _ = self.run_main(["status", "PPSA00001"])
        self.assertEqual(rc, 1); self.assertIn("state: fresh", out); self.assertIn("no commit known for kk", out)

    # ---- env words
    def test_cache_env_points_everything_at_the_dir(self):
        e = dict(cache.cache_env("PPSA00001", "/c", "mid"))
        self.assertEqual(e["KYTY_PIPELINE_JOURNAL_PATH"], "/c/PPSA00001.pipelines.journal")
        self.assertEqual(e["KK_LIBCACHE_DIR"], "/c/kk-libcache"); self.assertEqual(e["KK_METAL_CACHE_ID"], "mid")
        self.assertNotIn("KK_LIBCACHE_DIR", dict(cache.cache_env("PPSA00001", "/c", "mid", with_libcache=False)))

    # ---- prepare / record / check
    def test_prepare_dry_run_names_key_and_command(self):
        rc, out, _ = self.run_main(["prepare", "PPSA00001", "--dry-run"])
        c = self.ctx()
        self.assertEqual(rc, 0)
        for s in (c.key, c.metal, "KYTY_PIPELINE_LIST_PREPARE=1", "KYTY_PIPELINE_LIST_APP_VER=01.001.000", "--class func", "--tag cache-prepare"):
            self.assertIn(s, out)
        self.assertFalse(os.path.exists(c.dir))

    def test_prepare_writes_prepared_tsv_and_clears_the_old_dir(self):
        c = self.ctx(); os.makedirs(c.dir); open(os.path.join(c.dir, "stale.bin"), "w").write("x")
        calls = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, "  shaders 1\n  permutations 2, pipelines 3\n"
            calls.append(cmd); return 0
        rc, out, err = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(os.path.join(c.dir, "stale.bin")))
        kv = cache.read_kv(os.path.join(c.dir, "prepared.tsv"))
        self.assertEqual(kv["key"], c.key); self.assertEqual(kv["rc"], "0"); self.assertEqual(kv["pipelines"], "3")
        self.assertEqual(cache.status(self.ctx())[0], "prepared")
        self.assertEqual(len(calls), 1)

    def test_prepare_failure_stays_unprepared(self):
        def runner(cmd, env=None, capture=False):
            return (0, "") if capture else 3
        rc, _, _ = self.run_main(["prepare", "PPSA00001"], runner)
        self.assertEqual(rc, 1); self.assertEqual(cache.status(self.ctx())[0], "partial")

    def test_record_runs_each_scene_into_an_empty_arm_and_keeps_journals(self):
        c = self.ctx(); seen = []
        def runner(cmd, env=None, capture=False):
            d = next(x.split("=", 1)[1] for x in cmd if x.startswith("KYTY_PIPELINE_JOURNAL_PATH=")); d = os.path.dirname(d)
            seen.append((cmd[cmd.index("--scene") + 1], d)); os.makedirs(d + "/kk-archive", exist_ok=True)
            for f in ("PPSA00001.pipelines.journal", "PPSA00001.shaders.journal", "PPSA00001.bin"): open(os.path.join(d, f), "w").write("j")
            return 0
        rc, out, _ = self.run_main(["record", "PPSA00001"], runner)
        self.assertEqual([s for s, _ in seen], ["boot", "lvl1"])            # the level:* template is not recorded
        d = seen[0][1]
        self.assertTrue(os.path.isfile(d + "/PPSA00001.pipelines.journal")); self.assertFalse(os.path.exists(d + "/PPSA00001.bin")); self.assertFalse(os.path.exists(d + "/kk-archive"))
        rc, out, _ = self.run_main(["record", "PPSA00001"], runner)
        self.assertEqual(len(seen), 2); self.assertIn("skipped", out)         # a second record leaves the recordings alone

    def test_record_unknown_scene_refused(self):
        rc, _, err = self.run_main(["record", "PPSA00001", "--scene", "nope"])
        self.assertEqual(rc, 2); self.assertIn("scene nope", err)

    def test_parse_check_counts_compiles_and_stalls(self):
        err = "x\n[kk-archive] stats libraries 12 (1 slow, 90 ms), pipelines archive-hit 100 (5 ms) compiled 7 (2 slow, 300 ms)\n[RED] foo bar\n"
        r = cache.parse_check(err, [16.0, 300.0, 16.0, 900.0])
        self.assertEqual((r["compiled"], r["libraries"], r["stalls"], r["worst_ms"]), (7, 12, 2, 900.0))
        self.assertEqual(r["red"], ["foo"]); self.assertAlmostEqual(r["stall_s"], 1.2)
        self.assertIsNone(cache.parse_check("", [])["compiled"])
        self.assertEqual(cache.check_row("s", 0, r).split("\t")[:3], ["s", "0", "7"])

    def test_check_needs_a_prepared_cache(self):
        rc, _, err = self.run_main(["check", "PPSA00001", "--scene", "boot"])
        self.assertEqual(rc, 1); self.assertIn("needs a prepared cache", err)

    def test_check_dry_run_and_real_run_on_a_clone(self):
        c = self.ctx(); os.makedirs(c.dir)
        open(os.path.join(c.dir, "prepared.tsv"), "w").write("key=%s\nrc=0\n" % c.key); open(os.path.join(c.dir, "marker"), "w").write("m")
        rc, out, _ = self.run_main(["check", "PPSA00001", "--scene", "boot", "--dry-run"])
        self.assertEqual(rc, 0); self.assertIn("clone", out)
        runs = os.path.join(self.env["NPS5_SCRATCH"], "runs", "PPSA00001", "cache-check-boot"); os.makedirs(runs)
        def runner(cmd, env=None, capture=False):
            d = next(x.split("=", 1)[1] for x in cmd if x.startswith("KK_LIBCACHE_DIR=")); d = os.path.dirname(d)
            self.assertTrue(os.path.isfile(d + "/marker"))                      # the run sees a clone of the prepared cache
            open(runs + "/run.err", "w").write("[kk-archive] stats libraries 0 (0 slow, 0 ms), pipelines archive-hit 9 (1 ms) compiled 0 (0 slow, 0 ms)\n"); return 0
        rc, out, _ = self.run_main(["check", "PPSA00001", "--scene", "boot"], runner)
        self.assertEqual(rc, 0); self.assertIn("boot\t0\t0", out)
        self.assertTrue(os.path.isfile(os.path.join(c.dir, "marker")))          # the prepared cache itself is untouched

    # ---- merge / verify-list
    def test_merge_unions_with_the_committed_list_and_reports_counts(self):
        rec = os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "rec", "boot"); os.makedirs(rec)
        for n in ("pipelines", "shaders"): open(os.path.join(rec, "PPSA00001.%s.journal" % n), "w").write("j")
        rc, out, err = self.run_main(["merge", "PPSA00001"])
        self.assertEqual(rc, 0, err); self.assertIn("shaders 3", out); self.assertIn("candidate only", out)
        self.assertTrue(os.path.isfile(os.path.join(self.env["NPS5_SCRATCH"], "cache", "PPSA00001", "merged.kyplst")))
        self.assertEqual(open(self.ctx().list_path, "rb").read(), b"1 2 3\n")      # the committed list is not touched without --write

    def test_merge_write_replaces_the_list_and_drops_the_stale_verified(self):
        rec = os.path.join(self.tmp, "myrec"); os.makedirs(rec)
        for n in ("pipelines", "shaders"): open(os.path.join(rec, "PPSA00001.%s.journal" % n), "w").write("j")
        vp = self.ctx().list_path + ".verified"; open(vp, "w").write("sha256=old\n")
        rc, out, _ = self.run_main(["merge", "PPSA00001", "--from", rec, "--write"])
        self.assertEqual(rc, 0); self.assertFalse(os.path.exists(vp)); self.assertEqual(open(self.ctx().list_path).read().strip(), "3 4 5")

    def test_merge_without_recordings_says_so(self):
        rc, _, err = self.run_main(["merge", "PPSA00001"])
        self.assertEqual(rc, 1); self.assertIn("no recordings", err)

    def test_verify_list_calls_the_checker_script_with_the_tool(self):
        calls = []
        def runner(cmd, env=None, capture=False):
            if capture: return 0, "check: ok\n" if cmd[1] == "check" else "  shaders 1\n  permutations 2, pipelines 3\n"
            calls.append((cmd, env)); return 0
        rc, out, err = self.run_main(["verify-list", "PPSA00001"], runner)
        self.assertEqual(rc, 0, err)
        cmd, env = calls[0]
        self.assertTrue(cmd[1].endswith("scripts/verify-pipeline-list.sh")); self.assertEqual(env["NPS5_KYPLST_TOOL"], self.env["NPS5_KYPLST_TOOL"])
        self.assertIn("review block", out)

    def test_verify_list_failed_check_stops(self):
        def runner(cmd, env=None, capture=False):
            return (1, "bad\n") if capture else 0
        rc, out, _ = self.run_main(["verify-list", "PPSA00001"], runner)
        self.assertEqual(rc, 1); self.assertIn("do not commit", out)

    def test_clone_dir_copies(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b"); os.makedirs(a); open(a + "/f", "w").write("x")
        cache.clone_dir(a, b); self.assertTrue(os.path.isfile(b + "/f"))

    def test_rmtree_refuses_outside_the_cache_root(self):
        with self.assertRaises(cache.CacheError):
            cache.rmtree_inside(self.tmp, os.path.join(self.tmp, "scratch"))

    def test_usage(self):
        self.assertEqual(self.run_main([])[0], 2)
        self.assertEqual(self.run_main(["frobnicate", "PPSA00001"])[0], 2)
        self.assertEqual(self.run_main(["status"])[0], 2)


if __name__ == "__main__":
    unittest.main()
