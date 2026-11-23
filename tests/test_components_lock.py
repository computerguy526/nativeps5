"""scripts/ci/check_components_lock.py: release tags vN.N, vN.N.N and vN.N.N.N (hotfix) are accepted, branches are not."""
import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = "a" * 40


def run(tag):
    rows = [f"{n} | GPL-2.0-only | https://example.org/{n}.git | {H} | ${{NPS5_FORK_BASE}}/{n} | {tag} | {H}"
            for n in ("ps5recomp", "kyty", "anyps5")]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "components.lock")
        with open(p, "w") as f:
            f.write("\n".join(rows) + "\n")
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts/ci/check_components_lock.py"), p],
                              capture_output=True, text=True).returncode


class ComponentsLock(unittest.TestCase):
    def test_release_tags_pass(self):
        for t in ("v1.2", "v1.2.3", "v1.2.3.1"):
            self.assertEqual(run(t), 0, t)

    def test_branches_and_long_tags_fail(self):
        for t in ("main", "v1.2.3.1.1", "v1"):
            self.assertNotEqual(run(t), 0, t)


if __name__ == "__main__":
    unittest.main()
