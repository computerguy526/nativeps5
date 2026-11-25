#!/usr/bin/env python3
"""Tests for scripts/nps5lib/cachezip.py with synthetic files: eligibility rules, real APFS compression (skipped where unavailable),
content identical after compression, idempotence, in-use / fresh / journal files skipped, verify failure handling, env switch."""
import os, shutil, subprocess, sys, tempfile, time, unittest, warnings
warnings.simplefilter("ignore", ResourceWarning)
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib"))
import cachezip as cz  # noqa: E402

TEXT = (b"float4 main0(float4 v [[stage_in]]) { return v * 0.5; }\n" * 4000)   # ~220 KB, compresses well


def put(root, rel, data=TEXT, age=3600):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data); t = time.time() - age; os.utime(p, (t, t)); return p


class Eligibility(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp()
    def tearDown(self): shutil.rmtree(self.d, ignore_errors=True)

    def reasons(self, **kw):
        return {os.path.relpath(p, self.d): r for p, r in cz.candidates(self.d, **kw)}

    def test_rules(self):
        put(self.d, "metal-shaders/a.fs.metal"); put(self.d, "x.programs.bin"); put(self.d, "x.pipelines.journal"); put(self.d, "x.pipelines.known")
        put(self.d, "tiny.bin", b"abc"); put(self.d, "fresh.bin", age=5); put(self.d, "busy.bin")
        x = put(self.d, "run.sh"); os.chmod(x, 0o755)
        r = self.reasons(open_set={os.path.realpath(os.path.join(self.d, "busy.bin"))})
        self.assertIsNone(r["metal-shaders/a.fs.metal"]); self.assertIsNone(r["x.programs.bin"])
        self.assertEqual(r["x.pipelines.journal"], "rewritten every run"); self.assertEqual(r["x.pipelines.known"], "rewritten every run")
        self.assertEqual(r["tiny.bin"], "small"); self.assertEqual(r["fresh.bin"], "modified recently")
        self.assertEqual(r["busy.bin"], "in use"); self.assertEqual(r["run.sh"], "executable")

    def test_min_age_zero_takes_fresh(self):
        put(self.d, "fresh.bin", age=0)
        self.assertIsNone(self.reasons(min_age=0, open_set=set())["fresh.bin"])


class Logic(unittest.TestCase):
    """compress_dir with an injected compressor: no APFS needed."""
    def setUp(self): self.d = tempfile.mkdtemp()
    def tearDown(self): shutil.rmtree(self.d, ignore_errors=True)

    def test_dry_run_changes_nothing(self):
        p = put(self.d, "a.bin"); before = open(p, "rb").read(); calls = []
        res = cz.compress_dir(self.d, dry_run=True, open_set=set(), compress=lambda f: calls.append(f) or True, log=lambda *_: None)
        self.assertEqual(calls, []); self.assertEqual(res["files"], 1); self.assertEqual(open(p, "rb").read(), before)

    def test_in_use_file_is_never_handed_to_the_compressor(self):
        p = put(self.d, "live.bin"); q = put(self.d, "cold.bin"); calls = []
        cz.compress_dir(self.d, open_set={os.path.realpath(p)}, compress=lambda f: calls.append(os.path.basename(f)) or True, log=lambda *_: None)
        self.assertEqual(calls, ["cold.bin"])

    def test_verify_failure_is_reported(self):
        p = put(self.d, "a.bin")
        def corrupt(f): open(f, "ab").write(b"x"); return True
        res = cz.compress_dir(self.d, open_set=set(), compress=corrupt, log=lambda *_: None)
        self.assertEqual(res["failed"], [p])

    def test_no_lsof_means_nothing_compressed(self):
        put(self.d, "a.bin"); calls = []
        old = cz.open_files; cz.open_files = lambda r: None
        try: cz.compress_dir(self.d, compress=lambda f: calls.append(f) or True, log=lambda *_: None)
        finally: cz.open_files = old
        self.assertEqual(calls, [])

    def test_env_switch(self):
        put(self.d, "a.bin"); msgs = []
        self.assertIsNone(cz.maybe_compress(self.d, {"NPS5_CACHE_COMPRESS": "0"}, log=msgs.append)); self.assertEqual(msgs, [])

    def test_default_follows_constant(self):
        put(self.d, "a.bin"); msgs = []
        old = cz.DEFAULT_ON; cz.DEFAULT_ON = False
        try: self.assertIsNone(cz.maybe_compress(self.d, {}, log=msgs.append))
        finally: cz.DEFAULT_ON = old
        self.assertEqual(msgs, [])

    def test_missing_dir(self):
        self.assertEqual(cz.compress_dir(os.path.join(self.d, "nope"), log=lambda *_: None)["files"], 0)


@unittest.skipUnless(sys.platform == "darwin" and cz._tool(), "needs macOS APFS and afsctool or ditto")
class Real(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp(dir=os.environ.get("NPS5_TEST_TMP"))
    def tearDown(self): shutil.rmtree(self.d, ignore_errors=True)

    def test_compress_identical_idempotent(self):
        p = put(self.d, "libcache/a.metal"); q = put(self.d, "x.programs.bin", os.urandom(1000) + TEXT * 3); j = put(self.d, "x.shaders.journal")
        want = {f: open(f, "rb").read() for f in (p, q, j)}; mt = {f: os.stat(f).st_mtime_ns for f in want}
        res = cz.compress_dir(self.d, open_set=set(), log=lambda *_: None)
        if not cz.is_compressed(p):
            self.skipTest("this filesystem does not support APFS compression")
        self.assertEqual(res["failed"], []); self.assertGreater(res["saved"], 0)
        for f in (p, q):
            self.assertTrue(cz.is_compressed(f)); self.assertEqual(open(f, "rb").read(), want[f]); self.assertLess(abs(os.stat(f).st_mtime_ns - mt[f]), 1000000)  # afsctool keeps mtime to the microsecond
        self.assertFalse(cz.is_compressed(j))
        again = cz.compress_dir(self.d, open_set=set(), log=lambda *_: None)
        self.assertEqual(again["files"], 0); self.assertEqual(again["skipped"].get("already compressed"), 2)

    def test_open_file_skipped_by_real_lsof(self):
        p = put(self.d, "held.bin"); q = put(self.d, "free.bin")
        with open(p, "rb"):
            res = cz.compress_dir(self.d, log=lambda *_: None)
        self.assertFalse(cz.is_compressed(p)); self.assertEqual(res["skipped"].get("in use"), 1)


class Decision(unittest.TestCase):
    """Prompt logic of `nps5 cache prepare` (synthetic: no TTY, no game, no real compression)."""
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.env = {"NPS5_DATA_STATE_DIR": os.path.join(self.d, "state")}
        self.old = cz.DEFAULT_ON
    def tearDown(self): cz.DEFAULT_ON = self.old; shutil.rmtree(self.d, ignore_errors=True)

    def asker(self, ans, log):
        def ask(q): log.append(q); return ans
        return ask

    def test_env_overrides_never_prompt(self):
        asked = []
        self.assertTrue(cz.want_compress(dict(self.env, NPS5_CACHE_COMPRESS="1"), True, self.asker("n", asked)))
        self.assertFalse(cz.want_compress(dict(self.env, NPS5_CACHE_COMPRESS="0"), True, self.asker("y", asked)))
        self.assertEqual(asked, [])

    def test_non_tty_uses_default_without_prompt(self):
        asked = []
        cz.DEFAULT_ON = False; self.assertFalse(cz.want_compress(self.env, False, self.asker("y", asked)))
        cz.DEFAULT_ON = True; self.assertTrue(cz.want_compress(self.env, False, self.asker("n", asked)))
        cz.record_game_answer(self.env, True); cz.DEFAULT_ON = False
        self.assertFalse(cz.want_compress(self.env, False, self.asker("y", asked)))   # non-interactive ignores the game answer
        self.assertEqual(asked, [])

    def test_tty_prompt_answers(self):
        cz.DEFAULT_ON = False; q = []
        self.assertTrue(cz.want_compress(self.env, True, self.asker("y", q), log=lambda *_: None))
        self.assertTrue(cz.want_compress(self.env, True, self.asker("YES", q), log=lambda *_: None))
        self.assertFalse(cz.want_compress(self.env, True, self.asker("n", q), log=lambda *_: None))
        self.assertFalse(cz.want_compress(self.env, True, self.asker("", q), log=lambda *_: None))   # default [y/N]
        self.assertIn("[y/N]", q[0])

    def test_game_files_yes_makes_default_yes(self):
        cz.DEFAULT_ON = False; q = []
        cz.record_game_answer(self.env, True)
        self.assertTrue(cz.want_compress(self.env, True, self.asker("", q), log=lambda *_: None)); self.assertIn("[Y/n]", q[0])
        self.assertFalse(cz.want_compress(self.env, True, self.asker("n", q), log=lambda *_: None))   # still can say no
        cz.record_game_answer(self.env, False)
        self.assertFalse(cz.want_compress(self.env, True, self.asker("", q), log=lambda *_: None))
        self.assertTrue(cz.want_compress(dict(self.env, NPS5_GAME_COMPRESS="1"), True, self.asker("", q), log=lambda *_: None))

    def test_eof_takes_default(self):
        def eof(_): raise EOFError
        cz.DEFAULT_ON = True; self.assertTrue(cz.want_compress(self.env, True, eof, log=lambda *_: None))
        cz.DEFAULT_ON = False; self.assertFalse(cz.want_compress(self.env, True, eof, log=lambda *_: None))

    def test_game_running_refuses(self):
        put(self.d, "cache/a.bin"); msgs = []
        r = cz.maybe_compress(os.path.join(self.d, "cache"), dict(self.env, NPS5_CACHE_COMPRESS="1"), log=msgs.append,
                              running=lambda: ["123 native_astro"])
        self.assertIsNone(r); self.assertIn("game is running", msgs[0])

    def test_ps_failure_refuses(self):
        msgs = []
        r = cz.maybe_compress(self.d, dict(self.env, NPS5_CACHE_COMPRESS="1"), log=msgs.append, running=lambda: ["ps failed: x"])
        self.assertIsNone(r)

    def test_game_regex(self):
        self.assertTrue(cz.GAME_RE.search("/x/ue2boot/native_out3")); self.assertTrue(cz.GAME_RE.search("/opt/kyty"))
        self.assertFalse(cz.GAME_RE.search("/usr/bin/python3"))


class OverLimit(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp(); self.m, self.dm = cz.MAX_SIZE, cz.DITTO_MAX
    def tearDown(self): cz.MAX_SIZE, cz.DITTO_MAX = self.m, self.dm; shutil.rmtree(self.d, ignore_errors=True)

    def test_over_2gib_counted_and_reported(self):
        put(self.d, "big.bin"); put(self.d, "ok.bin", TEXT[:1000] * 10); cz.MAX_SIZE = 100000
        res = cz.compress_dir(self.d, dry_run=True, open_set=frozenset(), log=lambda *_: None)
        self.assertEqual(res["over_limit_files"], 1); self.assertEqual(res["over_limit_bytes"], len(TEXT))
        self.assertIn("over the size limit", cz.report(res, True)); self.assertIn("1 file(s)", cz.report(res, True))

    def test_ditto_limit_counted(self):
        put(self.d, "big.bin"); cz.DITTO_MAX = 100000; old = cz._tool; cz._tool = lambda: ("ditto", "/usr/bin/ditto")
        try: res = cz.compress_dir(self.d, dry_run=True, open_set=frozenset(), log=lambda *_: None)
        finally: cz._tool = old
        self.assertEqual(res["over_limit_files"], 1); self.assertEqual(res["files"], 0)

    def test_nothing_over_limit_no_text(self):
        put(self.d, "a.bin"); res = cz.compress_dir(self.d, dry_run=True, open_set=frozenset(), log=lambda *_: None)
        self.assertNotIn("size limit", cz.report(res, True))


if __name__ == "__main__":
    unittest.main()
