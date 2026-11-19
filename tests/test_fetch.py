#!/usr/bin/env python3
"""scripts/fetch-components.sh against local repositories: a release lock (tag + commit) is refused when the tag points
elsewhere or is not annotated, and a refused clone leaves nothing behind that a second run would accept."""
import os, shutil, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GIT_ENV = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def git(*a, cwd=None):
    return subprocess.run(["git"] + list(a), cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, **GIT_ENV)).stdout.strip()


class FetchComponents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        t = self.tmp
        os.makedirs(os.path.join(t, "pub", "scripts"))
        for f in ("fetch-components.sh", "nps5-env.sh"):
            shutil.copy(os.path.join(REPO, "scripts", f), os.path.join(t, "pub", "scripts", f))
        src = os.path.join(t, "src")
        git("init", "-q", src)
        git("commit", "-q", "--allow-empty", "-m", "A", cwd=src); self.a = git("rev-parse", "HEAD", cwd=src)
        git("commit", "-q", "--allow-empty", "-m", "B", cwd=src); self.b = git("rev-parse", "HEAD", cwd=src)
        git("tag", "-a", "v9.9", "-m", "release", self.b, cwd=src)
        git("tag", "v9.8", self.b, cwd=src)                                  # lightweight
        os.makedirs(os.path.join(t, "forks"))
        git("clone", "-q", "--bare", src, os.path.join(t, "forks", "nativeps5-kyty"))
        self.dir = os.path.join(t, "root", "kyty")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lock(self, tag, commit):
        with open(os.path.join(self.tmp, "pub", "components.lock"), "w") as f:
            f.write("# comment\nkyty | GPL-2.0-only | https://example.invalid/u.git | %s | ${NPS5_FORK_BASE}/nativeps5-kyty | %s | %s\n"
                    % (self.a, tag, commit))

    def fetch(self):
        env = dict(HOME=self.tmp, PATH=os.environ["PATH"], NPS5_ROOT=os.path.join(self.tmp, "root"), NPS5_KYTY_DIR=self.dir,
                   NPS5_FORK_BASE=os.path.join(self.tmp, "forks"), **GIT_ENV)
        r = subprocess.run(["bash", os.path.join(self.tmp, "pub", "scripts", "fetch-components.sh"), "kyty"],
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def leftovers(self):
        root = os.path.join(self.tmp, "root")
        return sorted(os.listdir(root)) if os.path.isdir(root) else []

    def test_moved_tag_is_refused_every_time(self):
        self.lock("v9.9", self.a)                     # the tag points at B, the lock pins A
        for _ in range(2):                            # v1.1: the second run found the refused clone and exited 0
            rc, out = self.fetch()
            self.assertNotEqual(rc, 0, out); self.assertIn("refusing", out)
            self.assertEqual(self.leftovers(), [], out)

    def test_lightweight_tag_is_refused(self):
        self.lock("v9.8", self.b)
        rc, out = self.fetch()
        self.assertNotEqual(rc, 0, out); self.assertIn("not an annotated tag", out); self.assertEqual(self.leftovers(), [])

    def test_missing_tag_is_refused(self):
        self.lock("v9.7", self.b)
        rc, out = self.fetch()
        self.assertNotEqual(rc, 0, out); self.assertEqual(self.leftovers(), [])

    def test_good_tag_is_checked_out_at_the_commit(self):
        self.lock("v9.9", self.b)
        rc, out = self.fetch()
        self.assertEqual(rc, 0, out); self.assertIn("verified", out)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.dir), self.b)
        self.assertEqual(self.leftovers(), ["kyty"])
        rc, out = self.fetch()                        # an existing checkout is left alone
        self.assertEqual(rc, 0, out); self.assertIn("the commit of the lock", out)


if __name__ == "__main__":
    unittest.main()
