#!/usr/bin/env python3
"""Tests of the identity-allow.txt allowlist of scripts/ci/check_identity.sh: an exact (commit sha, author e-mail) pair passes,
the same e-mail on another commit fails, our own commits must stay GitHub noreply, committer e-mails are never excused.
Scratch repositories only. Run: python3 -I tests/test_identity_allow.py"""
import os, shutil, subprocess, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "ci", "check_identity.sh")
NOREPLY = "1+owner@users.noreply.github.com"
UP = "upstream" + "@" + "dev.invalid"          # not literally an address in this file's lines


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "scripts", "ci"))
        shutil.copy(SCRIPT, os.path.join(self.d, "scripts", "ci", "check_identity.sh"))
        self.g("init", "-q"); self.g("config", "commit.gpgsign", "false")
        self.n = 0
        self.commit(NOREPLY, NOREPLY)                       # base

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def g(self, *a, env=None):
        e = dict(os.environ, **(env or {}))
        return subprocess.run(("git",) + a, cwd=self.d, env=e, capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, author, committer):
        self.n += 1
        open(os.path.join(self.d, "f%d" % self.n), "w").write("x\n")
        self.g("add", "-A")
        env = dict(GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL=author, GIT_COMMITTER_NAME="c", GIT_COMMITTER_EMAIL=committer)
        self.g("commit", "-q", "-m", "c%d" % self.n, env=env)
        return self.g("rev-parse", "HEAD")

    def allow(self, *lines):
        open(os.path.join(self.d, "scripts", "ci", "identity-allow.txt"), "w").write("\n".join(lines) + "\n")

    def run_gate(self):
        r = subprocess.run(("bash", os.path.join(self.d, "scripts", "ci", "check_identity.sh"), "HEAD~%d..HEAD" % (self.n - 1)),
                           cwd=self.d, capture_output=True, text=True)
        return r.returncode, r.stdout

    def test_no_allowlist_fails(self):
        self.commit(UP, NOREPLY)
        self.assertEqual(self.run_gate()[0], 1)

    def test_allowed_pair_passes(self):
        sha = self.commit(UP, NOREPLY)
        self.allow("# upstream pick", sha + " " + UP)
        self.assertEqual(self.run_gate()[0], 0, self.run_gate()[1])

    def test_same_email_other_sha_fails(self):
        sha = self.commit(UP, NOREPLY)
        self.commit(UP, NOREPLY)
        self.allow(sha + " " + UP)
        rc, out = self.run_gate()
        self.assertEqual(rc, 1); self.assertIn(UP, out)

    def test_same_sha_other_email_fails(self):
        sha = self.commit(UP, NOREPLY)
        self.allow(sha + " other" + "@" + "dev.invalid")
        self.assertEqual(self.run_gate()[0], 1)

    def test_committer_email_not_excused(self):
        sha = self.commit(UP, UP)
        self.allow(sha + " " + UP)
        self.assertEqual(self.run_gate()[0], 1)

    def test_own_non_noreply_commit_fails_even_with_allowlist(self):
        self.commit("me" + "@" + "dev.invalid", NOREPLY)
        self.allow("0" * 40 + " " + UP)
        self.assertEqual(self.run_gate()[0], 1)

    def test_short_sha_or_pattern_not_accepted(self):
        sha = self.commit(UP, NOREPLY)
        self.allow(sha[:10] + " " + UP, "* " + UP, "*@dev.invalid")
        self.assertEqual(self.run_gate()[0], 1)


if __name__ == "__main__":
    unittest.main()
