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


if __name__ == "__main__":
    unittest.main()
