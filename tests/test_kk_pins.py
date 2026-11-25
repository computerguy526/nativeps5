"""KosmicKrisp pins: EXPECTED_TREE / MESA_COMMIT are full git hashes and the patch series is gap-free."""
import os
import re
import unittest

KK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kosmickrisp")


def _read(name):
    with open(os.path.join(KK, name)) as f:
        return f.read().strip()


class KkPins(unittest.TestCase):
    def test_hashes(self):
        for name in ("EXPECTED_TREE", "MESA_COMMIT"):
            self.assertRegex(_read(name), r"^[0-9a-f]{40}$", name)

    def test_patches_numbered_without_gaps(self):
        nums = sorted(int(m.group(1)) for m in
                      (re.match(r"(\d{4})-.*\.patch$", p) for p in os.listdir(os.path.join(KK, "patches"))) if m)
        self.assertEqual(nums, list(range(1, len(nums) + 1)))

    def test_archive_flush_patch_present(self):
        names = os.listdir(os.path.join(KK, "patches"))
        self.assertTrue(any(n.startswith("0041-") for n in names), "patch 0041 (nps5_kk_archive_flush) missing")

    def test_fx_plain_compiler_patch_present(self):
        names = os.listdir(os.path.join(KK, "patches"))
        self.assertTrue(any(n.startswith("0043-") for n in names), "patch 0043 (MetalFX plain compiler, archfix) missing")


if __name__ == "__main__":
    unittest.main()
