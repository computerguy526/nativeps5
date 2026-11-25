#!/usr/bin/env python3
"""Tests of scripts/nps5lib/loadedcheck.py (synthetic lsof text)."""
import importlib.util, os, sys, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("lc", os.path.join(HERE, "..", "scripts", "nps5lib", "loadedcheck.py"))
lc = importlib.util.module_from_spec(spec); spec.loader.exec_module(lc)

TXT = """native_on 1 u REG 1,18 33242128 1 /box/libs/rc13/kyty_gpu.dylib
native_on 1 u REG 1,18 1000 2 /box/old/build-arm64/core/libs/libs/unpatched/libnps5_gpu.dylib
native_on 1 u REG 1,18 1000 3 /usr/lib/libSystem.B.dylib
"""

class T(unittest.TestCase):
    def test_parse_finds_the_watched_libraries_only(self):
        f = lc.mapped_images(TXT)
        self.assertEqual(sorted(f), ["kyty_gpu.dylib", "libnps5_gpu.dylib"])

    def test_match_is_clean(self):
        f = lc.mapped_images(TXT)
        self.assertEqual(lc.compare(f, "/box/libs/rc13/kyty_gpu.dylib", "/box/old/build-arm64/core/libs/libs/unpatched"), [])

    def test_wrong_kyty_and_fallback_host_are_reported(self):
        f = lc.mapped_images(TXT)
        bad = lc.compare(f, "/box/libs/rc14/kyty_gpu.dylib", "/box/new/flat")
        self.assertEqual(len(bad), 2); self.assertIn("kyty_gpu.dylib loaded from", bad[0]); self.assertIn("libnps5_gpu.dylib loaded from", bad[1])

    def test_not_loaded_is_a_problem(self):
        self.assertEqual(lc.compare({}, "/a/kyty_gpu.dylib", "/b"), ["kyty_gpu.dylib is not loaded", "libnps5_gpu.dylib is not loaded"])
        self.assertEqual(lc.compare({}), [])

if __name__ == "__main__": unittest.main()
