#!/usr/bin/env python3
"""Fixture tests for `nps5 cache share` and `nps5 cache merge --contrib` (scripts/nps5lib/cache.py): fake journals and a fake pipeline-list
tool (text lists), a shipped list, the diff = what is new, per-stage counts, no paths in the output or the file, a refused version mismatch,
a tampered file, the confirmation before anything is pushed, and the real list rules (scripts/nps5_check.py) for a contrib path. No game, no GPU."""
import hashlib, io, os, shutil, sys, tempfile, unittest, warnings
warnings.simplefilter("ignore", ResourceWarning)
from contextlib import redirect_stdout, redirect_stderr
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts", "nps5lib")); sys.path.insert(0, HERE)
import cache  # noqa: E402
import test_cache as tc  # noqa: E402

# Fake lists are text: "title T", "version V", "producer P", then entries S:<vertex|pixel|compute>:<id>, M:<id>, P:<id>.
TOOL = r'''#!/usr/bin/env python3
import sys
a = sys.argv[1:]
def read(p):
    return [l.rstrip("\n") for l in open(p)]
def meta(ls, k):
    return next((l.split(" ", 1)[1] for l in ls if l.startswith(k + " ")), "")
def opt(k):
    return a[a.index(k) + 1] if k in a else None
if a[0] == "info":
    ls = read(a[1]); s = [l for l in ls if l.startswith("S:")]
    c = lambda k: len([l for l in s if l.split(":")[1] == k])
    print("%s: 1 bytes\n  title %s, version %s, producer %s, input infos 1 2 3, created 1" % (a[1], meta(ls, "title"), meta(ls, "version"), meta(ls, "producer")))
    print("  shaders %d (vertex %d, pixel %d, compute %d; 0 merged stages; 1 code bytes referenced)" % (len(s), c("vertex"), c("pixel"), c("compute")))
    print("  permutations %d, pipelines %d (0 mesh, 0 without a pixel stage)" % (len([l for l in ls if l.startswith("M:")]), len([l for l in ls if l.startswith("P:")])))
elif a[0] == "export":
    entries, head = set(), None
    for k in ("--shaders", "--pipelines"):
        for l in read(opt(k)):
            if l.startswith(("title ", "version ")): head = (head or []) + [l]
            else: entries.add(l)
    open(opt("--out"), "w").write("\n".join(sorted(set(head)) + ["producer " + (opt("--producer") or "?")] + sorted(entries)) + "\n")
elif a[0] == "merge":
    ins = [x for x in a[1:] if not x.startswith("--") and x != opt("--out") and x != opt("--producer")]
    vs, ent, head = set(), set(), []
    for p in ins:
        ls = read(p); vs.add((meta(ls, "title"), meta(ls, "version")))
        ent |= {l for l in ls if l[:2] in ("S:", "M:", "P:")}
    if len(vs) > 1:
        print("%s is for another title, version or input layout" % ins[-1]); sys.exit(2)
    t, v = vs.pop()
    open(opt("--out"), "w").write("title %s\nversion %s\nproducer %s\n" % (t, v, opt("--producer") or "merge") + "\n".join(sorted(ent)) + "\n")
elif a[0] == "check":
    print("check: ok")
elif a[0] == "verify":
    print("verify: no shader code in the list")
'''
HOME = os.path.expanduser("~")


def put(path, text, mode="w"):
    with open(path, mode) as f:
        f.write(text)


def get(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def lst(version, *entries, title="PPSA00001"):
    return "title %s\nversion %s\nproducer shipped\n%s\n" % (title, version, "\n".join(entries))


def stub_rules(rel, data, side):
    ok = ("sha256=" + hashlib.sha256(data).hexdigest()).encode() in side
    return [] if ok else ["pipelines.kyplst.verified is for other bytes"]


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.env = tc.make(self.tmp, list_bytes=None)
        tool = os.path.join(self.tmp, "tool.py"); put(tool, TOOL); os.chmod(tool, 0o755); self.env["NPS5_KYPLST_TOOL"] = tool
        self.gd = os.path.join(self.tmp, "games", "PPSA00001"); self.shipped = os.path.join(self.gd, "pipelines.kyplst")
        self.write_shipped(lst("01.001.000", "S:vertex:1", "S:pixel:2", "M:1", "M:2", "P:1", "P:2"))
        self.old_rules, cache.kyplst_rules = cache.kyplst_rules, stub_rules
        self.calls = []

    def tearDown(self):
        cache.kyplst_rules = self.old_rules; shutil.rmtree(self.tmp, ignore_errors=True)

    def write_shipped(self, text):
        put(self.shipped, text)

    def journals(self, name, text, version="01.001.000"):
        d = os.path.join(self.tmp, "scratch", "cache", "PPSA00001", name); os.makedirs(d, exist_ok=True)
        for k in ("shaders", "pipelines"):
            put(os.path.join(d, "PPSA00001.%s.journal" % k), "title PPSA00001\nversion %s\n%s\n" % (version, text if k == "shaders" else ""))
        return d

    def runner(self, cmd, env=None, capture=False):
        self.calls.append(cmd)
        if cmd[0] == "bash" and cmd[1].endswith("verify-pipeline-list.sh"):          # the script itself needs the real tool build: emulate it
            data = get(cmd[2], "rb")
            put(cmd[2] + ".verified", "# record\nsha256=%s\nresult=verify: no shader code in the list\n" % hashlib.sha256(data).hexdigest())
            return 0, "verify: no shader code in the list\nverify-pipeline-list: wrote %s.verified (game %s)\n" % (cmd[2], cmd[3])
        if cmd[0] in ("gh", "git"):
            return (0, "https://github.com/x/y/pull/1\n") if capture else 0
        return cache.default_runner(cmd, env, capture)

    def run_main(self, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        env = dict(self.env)
        with redirect_stdout(out), redirect_stderr(err):
            rc = cache.main(argv, env, self.runner, cache.context("PPSA00001", dict(env), commits=tc.COMMITS))
        return rc, out.getvalue() + err.getvalue()

    def contribs(self):
        d = os.path.join(self.gd, "contrib")
        return sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []

    # ---- share
    def test_diff_is_only_what_is_new_with_stage_counts(self):
        self.journals("a", "S:vertex:1\nS:vertex:3\nS:compute:4\nM:1\nM:3\nM:4\nP:1\nP:3\nP:4\nP:5")
        rc, out = self.run_main(["share", "PPSA00001", "--from", os.path.join(self.tmp, "scratch", "cache", "PPSA00001", "a")])
        self.assertEqual(rc, 0, out)
        self.assertIn("NEW:          shaders 2 (vertex 1, pixel 0, compute 1), permutations 2, pipelines 3", out)
        self.assertIn("title PPSA00001 01.001.000", out)
        fs = self.contribs(); self.assertEqual(len(fs), 2, fs)
        self.assertRegex(fs[0], r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{8}\.kyplst$"); self.assertTrue(fs[1].endswith(".verified"))
        self.assertIn("gh pr create", out); self.assertIn("Shader list", out); self.assertIn("pipelines.kyplst", fs[0] and "pipelines.kyplst")
        txt = get(os.path.join(self.gd, "contrib", fs[0]))
        self.assertIn("title PPSA00001\n", txt); self.assertIn("version 01.001.000\n", txt)
        self.assertIn("producer nps5-share ", txt)

    def test_nothing_new_says_thanks_and_writes_nothing(self):
        self.journals("a", "S:vertex:1\nM:2\nP:1")
        rc, out = self.run_main(["share", "PPSA00001"])
        self.assertEqual(rc, 0); self.assertIn("Thanks", out); self.assertEqual(self.contribs(), [])

    def test_no_private_paths_in_output_or_file(self):
        self.journals("a", "S:pixel:9\nM:9\nP:9")
        rc, out = self.run_main(["share", "PPSA00001"])
        self.assertEqual(rc, 0, out)
        for bad in (self.tmp, os.path.realpath(self.tmp), HOME, os.path.join(self.tmp, "app0")):
            self.assertNotIn(bad, out)
        for f in self.contribs():
            self.assertNotIn(self.tmp.encode(), get(os.path.join(self.gd, "contrib", f), "rb"))
            self.assertNotIn(HOME.encode(), get(os.path.join(self.gd, "contrib", f), "rb"))

    def test_other_version_is_refused(self):
        self.journals("a", "S:pixel:9\nM:9\nP:9", version="01.002.000")
        rc, out = self.run_main(["share", "PPSA00001"])
        self.assertNotEqual(rc, 0); self.assertIn("per game version", out); self.assertEqual(self.contribs(), [])

    def test_failed_proof_deletes_the_list(self):
        self.journals("a", "S:pixel:9\nM:9\nP:9")
        real = self.runner
        self.runner = lambda cmd, env=None, capture=False: (1, "verify: FOUND code\n") if cmd[0] == "bash" else real(cmd, env, capture)
        rc, out = self.run_main(["share", "PPSA00001"])
        self.assertEqual(rc, 1); self.assertEqual([f for f in self.contribs() if f.endswith(".kyplst")], [])

    def test_pr_needs_confirmation(self):
        self.journals("a", "S:pixel:9\nM:9\nP:9")
        env = dict(self.env)
        for answer, pushed in (("n", False), ("y", True)):
            self.calls.clear(); out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                ctx = cache.context("PPSA00001", dict(env), commits=tc.COMMITS)
                rc = cache.cmd_share(ctx, dict(cache.parse(["share", "PPSA00001", "--pr"])[2], confirm=lambda _p: answer), self.runner)
            joined = [" ".join(c) for c in self.calls]
            self.assertEqual(any("push" in j for j in joined), pushed, joined)
            self.assertEqual(any("pr create" in j for j in joined), pushed)
            for f in self.contribs(): os.remove(os.path.join(self.gd, "contrib", f))

    # ---- maintainer merge
    def share_one(self, text, name="a"):
        self.journals(name, text); rc, out = self.run_main(["share", "PPSA00001"]); self.assertEqual(rc, 0, out)
        return [os.path.join(self.gd, "contrib", f) for f in self.contribs() if f.endswith(".kyplst")]

    def test_merge_contrib_folds_dedupes_and_keeps_provenance(self):
        files = self.share_one("S:pixel:9\nM:9\nP:9\nP:1")
        rc, out = self.run_main(["merge", "PPSA00001", "--contrib"] + files + ["--write"])
        self.assertEqual(rc, 0, out); self.assertIn("gained: shaders 1 (vertex 0, pixel 1, compute 0), permutations 1, pipelines 1", out)
        self.assertEqual(get(self.shipped).count("P:"), 3)
        self.assertFalse(os.path.exists(self.shipped + ".verified"))
        prov = os.path.join(self.gd, "pipelines.contrib.tsv"); rows = [l for l in get(prov).splitlines() if not l.startswith("#")]
        self.assertEqual(len(rows), 1)
        rc, out = self.run_main(["merge", "PPSA00001", "--contrib"] + files + ["--write"])       # again: nothing new, no second row
        self.assertEqual(len([l for l in get(prov).splitlines() if not l.startswith("#")]), 1)
        self.assertEqual(get(self.shipped).count("P:"), 3)

    def test_merge_contrib_refuses_other_version_and_tampered(self):
        files = self.share_one("S:pixel:9\nM:9\nP:9")
        before = get(self.shipped)
        self.write_shipped(before.replace("01.001.000", "01.002.000"))
        rc, out = self.run_main(["merge", "PPSA00001", "--contrib"] + files + ["--write"])
        self.assertEqual(rc, 2); self.assertIn("per game version", out)
        self.write_shipped(before)
        put(files[0], "P:666\n", "a")                                  # changed after its proof
        rc, out = self.run_main(["merge", "PPSA00001", "--contrib"] + files + ["--write"])
        self.assertEqual(rc, 2); self.assertIn("other bytes", out); self.assertEqual(get(self.shipped), before)

    def test_parse_info_title_and_version(self):
        d = cache.parse_info("  title PPSA21564, version 01.007.000, producer x\n  shaders 832 (vertex 479, pixel 230, compute 123; 4 merged)\n  permutations 878, pipelines 1544 (38 mesh)")
        self.assertEqual((d["title"], d["version"], d["vertex"], d["pixel"], d["compute"], d["pipelines"]), ("PPSA21564", "01.007.000", 479, 230, 123, 1544))
        self.assertEqual(cache.stage_summary(d), "shaders 832 (vertex 479, pixel 230, compute 123), permutations 878, pipelines 1544")


class TestRealRules(unittest.TestCase):
    """The repository's own list rules accept a contrib path and reject a tampered file."""
    def test_contrib_path_and_tamper(self):
        import test_check as tk
        if not tk.HAVE_ZSTD:
            self.skipTest("needs Python 3.14 or the zstd tool")
        data = tk.kyplst([tk.section(1, tk.info("PPSA00000")), tk.section(3, os.urandom(64) * 4)])
        p = "games/PPSA00000/contrib/2026-11-17-0a1b2c3d.kyplst"
        self.assertEqual(tk.C.check_kyplst(p, data, tk.sidecar(data)), [])
        self.assertIn("other bytes", " ".join(tk.C.check_kyplst(p, data + b"", tk.sidecar(data[:-1] + b"X"))))
        self.assertIn("only accepted as", " ".join(tk.C.check_kyplst("games/PPSA00000/contrib/x.kyplst", data, tk.sidecar(data))))
        self.assertIn("no pipelines.kyplst.verified", " ".join(tk.C.check_kyplst(p, data, b"")))


if __name__ == "__main__":
    unittest.main()
