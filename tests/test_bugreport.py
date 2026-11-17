#!/usr/bin/env python3
"""Tests for scripts/nps5lib/bugreport.py: redaction, the allow-list, the size cap, log de-duplication, dry run. Fixtures are
built in a temp dir; no real hardware command is run (a fake runner)."""
import importlib.util, io, json, os, sys, tempfile, unittest, zipfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", *path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


br = load("bugreport", "scripts", "nps5lib", "bugreport.py")
U, V = "/" + "Users", "/" + "Volumes"          # built, so the personal-path check of check.sh does not read the fixtures as real paths
HOME = U + "/alice_fixture"


def fake(argv, timeout=10):
    if argv[0] == "git": return "abc123\n" if "rev-parse" in argv else " M x.c\n"
    if argv[0] == "scutil": return "Alices-MacBook\n"
    if argv[0] == "ioreg": return '+-o Pad  <class IOHIDDevice>\n  "Product" = "Wireless Controller"\n  "VendorID" = 1356\n  "ProductID" = 3302\n  "SerialNumber" = "AB12CD34EF56"\n'
    return "tool output\n"


def make_run(root, tag="t1", log=None, extra=None):
    d = os.path.join(root, "PPSA00000", tag); os.makedirs(d)
    rj = dict(schema=1, title="PPSA00000", tag=tag, env={"NPS5_AUDIO_NULL": "1", "NPS5_API_TOKEN": "sekrit", "NPS5_DUMP_DIR": HOME + "/dumps"},
              components={"kyty": "deadbeef"}, arm=None)
    open(os.path.join(d, "run.json"), "w").write(json.dumps(rj))
    open(os.path.join(d, "summary.txt"), "w").write("verdict ok at %s/work, mail alice@corp.example\n" % HOME)
    open(os.path.join(d, "crash.txt"), "w").write("crash in %s/x on Alices-MacBook\nIOPlatformSerialNumber serial: C02XYZ123456\n" % HOME)
    open(os.path.join(d, "run.err"), "w").write(log or "start\nERROR boom\n")
    for junk in ("shader.spv", "frame_1.bmp", "pipeline.journal", "notes.txt"):
        open(os.path.join(d, junk), "w").write("SECRET GAME DATA " + HOME)
    return d


def run_main(args, env=None):
    out = io.StringIO()
    e = {"HOME": HOME, "USER": "alice_fixture", "NPS5_SCRATCH": "/nonexistent"}; e.update(env or {})
    rc = br.main(args, e, runner=fake, doctor=lambda: "doctor says hi %s\n" % HOME, out=out)
    return rc, out.getvalue()


class Redact(unittest.TestCase):
    def test_paths_mail_serial_host(self):
        r = br.Redactor(HOME, "alice_fixture", ["Alices-MacBook"])
        t = r("%s/a/b %s/My Game Disk/x bob@corp.example git@github.com Alices-MacBook serial: ABCDEF123456 "
              "12345678-1234-1234-1234-123456789abc %s/Shared/ok\n" % (HOME, V, U))
        for bad in ("alice", "My Game", "bob@", "Alices", "ABCDEF123456", "12345678-"):
            self.assertNotIn(bad, t)
        for good in ("~/a/b", "git@github.com", U + "/Shared/ok", "<host>", "<serial>", "<uuid>"):
            self.assertIn(good, t)
        self.assertEqual(r.leaks(t), [])

    def test_key_lines_dropped_and_leak_check(self):
        r = br.Redactor(HOME, "alice_fixture")
        t = r("ok\nmaster_key_00 = %s\n" % ("ab" * 20))
        self.assertNotIn("abab", t); self.assertIn("1 line(s)", t)
        self.assertTrue(r.leaks(U + "/someoneelse/x"))

    def test_env_secret_names(self):
        self.assertEqual(br.filter_env({"A_TOKEN": "x", "NPS5_FOO": "1", "MY_PASSWORD": "p"}), {"A_TOKEN": "<removed>", "MY_PASSWORD": "<removed>", "NPS5_FOO": "1"})


class Trim(unittest.TestCase):
    def test_dedupe_keeps_important_and_counts(self):
        lines = ["[x] boot"] + ["[kyty-seam] watch interval: %d sets, %d faults" % (i, i * 2) for i in range(500)]
        lines += ["[a] WARN shader slow %d" % i for i in range(1)] + ["[b] FATAL assert failed foo"] * 10 + ["tail %d" % i for i in range(20)]
        out = br.trim_log("\n".join(lines), head=1, tail=5)
        self.assertEqual(out.count("watch interval"), 2)          # one sample + the summary line (none of the other 499)
        self.assertIn("(x500 repeated) [kyty-seam] watch interval: N sets, N faults", out)
        self.assertEqual(out.count("FATAL assert failed foo"), br.LOG_IMPORTANT_REPEAT + 1)   # kept 3 times + the summary row
        self.assertIn("WARN shader slow", out); self.assertIn("[x] boot", out); self.assertIn("tail 19", out)
        self.assertLess(len(out), len("\n".join(lines)) // 3)

    def test_cut_cap(self):
        t = br.cut("x" * 5000, 1000)
        self.assertLessEqual(len(t.encode()), 1000); self.assertIn("cut here", t)


class Zip(unittest.TestCase):
    def test_allow_list_privacy_and_listing(self):
        with tempfile.TemporaryDirectory() as root:
            make_run(root, log="line\n" * 1000 + "ERROR boom %s/x\n" % HOME)
            dest = os.path.join(root, "out.zip")
            rc, out = run_main(["t1", "--out", dest], {"NPS5_RUNS_ROOT": root, "NPS5_SCRATCH": root})
            self.assertEqual(rc, 0, out)
            z = zipfile.ZipFile(dest)
            names = set(z.namelist())
            self.assertEqual(names, {"README.txt", "run.json", "summary.txt", "crash.txt", "log.err.txt", "run-summary.txt", "doctor.txt", "machine.txt", "components.txt", "settings.txt", "controllers.txt"})
            for banned in ("shader.spv", "frame_1.bmp", "pipeline.journal", "notes.txt"):
                self.assertNotIn(banned, names)
            blob = "".join(z.read(n).decode() for n in names)
            for bad in ("alice", "SECRET GAME", "sekrit", "corp.example", "Alices", "C02XYZ", "AB12CD34EF56"):
                self.assertNotIn(bad, blob)
            self.assertIn("NPS5_API_TOKEN=<removed>", z.read("settings.txt").decode())
            self.assertIn("Wireless Controller", z.read("controllers.txt").decode())
            for n in names:
                self.assertIn(n, out)                              # the list is printed
            self.assertLess(z.getinfo("log.err.txt").file_size, 2000)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            make_run(root)
            cwd = os.getcwd(); os.chdir(root)
            try:
                rc, out = run_main(["--last", "--dry-run"], {"NPS5_RUNS_ROOT": root})
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 0); self.assertIn("dry run", out)
            self.assertEqual([f for f in os.listdir(root) if f.endswith(".zip")], [])

    def test_missing_run_and_usage(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(run_main(["nope"], {"NPS5_RUNS_ROOT": root})[0], 1)
            self.assertEqual(run_main(["--bogus"])[0], 2)

    def test_total_cap(self):
        with tempfile.TemporaryDirectory() as root:
            d = make_run(root)
            open(os.path.join(d, "summary.txt"), "w").write("s" * (3 * br.FILE_CAP))
            open(os.path.join(d, "crash.txt"), "w").write("c" * (3 * br.FILE_CAP))
            dest = os.path.join(root, "o.zip")
            self.assertEqual(run_main(["t1", "--out", dest], {"NPS5_RUNS_ROOT": root})[0], 0)
            z = zipfile.ZipFile(dest)
            self.assertLessEqual(sum(i.file_size for i in z.infolist()), br.TOTAL_CAP + 8192)
            self.assertLessEqual(z.getinfo("summary.txt").file_size, br.FILE_CAP + 200)


if __name__ == "__main__":
    unittest.main()
