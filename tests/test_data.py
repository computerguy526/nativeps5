#!/usr/bin/env python3
"""Tests for scripts/nps5lib/data.py (`nps5 data status|compress|decompress`): the layout helpers and the skip rules
on every platform, and the real thing in a temporary folder on macOS/APFS: round trip, idempotence, what is left
alone, a crash in the middle of a file (exception and a killed process), a file that changes while it is processed,
resume, refusal of a non-APFS volume. All data is generated here; the tests need no game."""
import contextlib, importlib.util, io, os, stat, struct, sys, tempfile, time, types, unittest, hashlib
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
def load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", *path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
data = load("data", "scripts", "nps5lib", "data.py")

OLD = time.time() - 3600


def put(path, content, old=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(content)
    if old: os.utime(path, (OLD, OLD))


def text(n, seed=0):
    """n compressible bytes that differ per seed."""
    line = ("line %d of a generated test file, nothing from any game; " % seed).encode()
    return (line * (n // len(line) + 1))[:n]


def noise(n, seed=1):
    out = b""; h = hashlib.sha256(b"%d" % seed).digest()
    while len(out) < n:
        h = hashlib.sha256(h).digest(); out += h
    return out[:n]


def sha(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def fake_stat(**kw):
    d = dict(st_mode=stat.S_IFREG | 0o644, st_flags=0, st_nlink=1, st_size=100000, st_mtime=OLD, st_ctime=OLD, st_blocks=200)
    d.update(kw); return types.SimpleNamespace(**d)


def on_apfs():
    if sys.platform != "darwin": return False
    try:
        return data.fs_type(tempfile.gettempdir()) == "apfs"
    except data.DataError:
        return False


class Pure(unittest.TestCase):
    def test_skip_reasons(self):
        self.assertEqual(data.skip_reason(fake_stat(), 4096), "")
        self.assertEqual(data.skip_reason(fake_stat(st_mode=stat.S_IFLNK | 0o777), 4096), "symlink")
        self.assertEqual(data.skip_reason(fake_stat(st_mode=stat.S_IFIFO | 0o644), 4096), "not a regular file")
        self.assertEqual(data.skip_reason(fake_stat(st_flags=data.UF_COMPRESSED), 4096), "already compressed")
        self.assertEqual(data.skip_reason(fake_stat(st_nlink=2), 4096), "hard link")
        self.assertEqual(data.skip_reason(fake_stat(st_size=4095), 4096), "tiny")
        self.assertEqual(data.skip_reason(fake_stat(st_size=0), 0), "tiny")
        self.assertEqual(data.skip_reason(fake_stat(st_size=data.MAX_SIZE), 4096), "2 GiB or larger")
        self.assertEqual(data.skip_reason(fake_stat(st_flags=0x2), 4096), "immutable or append-only")
        self.assertEqual(data.skip_reason(fake_stat(st_ctime=time.time() - 5), 4096), "changed in the last minute")
        self.assertEqual(data.skip_reason(fake_stat(st_ctime=time.time() + 5), 4096), "changed in the last minute")
        # the change time decides: a modification time can be anything (copy tools, another machine's clock), and a
        # change time far ahead means the clock was set back since the last write (a write now would move it to now)
        self.assertEqual(data.skip_reason(fake_stat(st_mtime=time.time() - 5), 4096), "")
        self.assertEqual(data.skip_reason(fake_stat(st_mtime=time.time() + 86400), 4096), "")
        self.assertEqual(data.skip_reason(fake_stat(st_ctime=time.time() + 7200), 4096), "")
        self.assertEqual(data.skip_reason(fake_stat(st_flags=data.SF_DATALESS), 4096), "cloud file not on this disk")

    def test_payload_layout(self):
        head, fork = data.decmpfs_payload(10, [b"abc"])
        self.assertIsNone(fork)
        self.assertEqual(head, struct.pack("<4sIQ", b"fpmc", 11, 10) + b"abc")
        chunks = [b"x" * 5000, b"y" * 7]
        head, fork = data.decmpfs_payload(70000, chunks)
        self.assertEqual(head, struct.pack("<4sIQ", b"fpmc", 12, 70000))
        self.assertEqual(struct.unpack("<3I", fork[:12]), (12, 5012, 5019))
        self.assertEqual(fork[12:], b"x" * 5000 + b"y" * 7)
        self.assertEqual(bytes(data.decmpfs_payload(70000, list(chunks), consume=True)[1]), bytes(fork))
        self.assertEqual(data.stored_bytes(10, [b"abc"]), 0)
        self.assertEqual(data.stored_bytes(70000, chunks), 8192)
        big = [b"z" * (data.INLINE_MAX + 1)]                       # one chunk too large for the xattr goes to the fork
        self.assertEqual(data.decmpfs_payload(60000, big)[0][4:8], struct.pack("<I", 12))

    def test_tmp_names(self):
        self.assertTrue(data.is_tmp(".nps5z-123-4.tmp"))
        for n in ("a.tmp", ".nps5z-1", "x.nps5z-1-1.tmp", "file.bin"):
            self.assertFalse(data.is_tmp(n))

    def test_arguments(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(data.main(["frobnicate", "x"]), 2)
            self.assertEqual(data.main(["compress"]), 2)
            self.assertEqual(data.main(["compress", "a", "b"]), 2)
            self.assertEqual(data.main(["compress", "a", "--jobs", "0"]), 2)
            self.assertEqual(data.main(["compress", "a", "--bogus"]), 2)
        self.assertIn("unknown command", err.getvalue())

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = data.state_path("/some/folder", {"NPS5_DATA_STATE_DIR": d})
            self.assertTrue(p.startswith(d))
            self.assertEqual(data.load_state(p), {"nogain": {}})
            data.save_state(p, {"nogain": {"a/b": "1:2"}})
            self.assertEqual(data.load_state(p)["nogain"], {"a/b": "1:2"})
            with open(p + ".json", "w") as f: f.write("{broken")
            self.assertEqual(data.load_state(p), {"nogain": {}})
            lock = data.take_lock(p)
            if sys.platform == "darwin" or sys.platform.startswith("linux"):
                with self.assertRaises(data.DataError): data.take_lock(p)
            lock.close()
            data.take_lock(p).close()


@unittest.skipUnless(on_apfs(), "needs macOS with the temporary directory on APFS")
class Apfs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        # files made here have a change time of now (nobody can set it); the recent-change rule is tested on its own
        recent = data.RECENT_S; data.RECENT_S = 0; self.addCleanup(setattr, data, "RECENT_S", recent)
        self.root = os.path.join(os.path.realpath(self.tmp.name), "game")
        self.env = {"NPS5_DATA_STATE_DIR": os.path.join(os.path.realpath(self.tmp.name), "state"), "HOME": self.tmp.name}
        put(self.root + "/big.bin", text(700000, 1))                 # several chunks -> resource fork
        put(self.root + "/sub/mid.dat", text(20000, 2))              # one chunk, compresses into the xattr
        put(self.root + "/sub/noise.bin", noise(300000))             # does not shrink
        put(self.root + "/sub/tiny.txt", text(100, 3))
        put(self.root + "/sub/deep/mixed.bin", text(200000, 4) + noise(100000, 5) + text(50000, 6))
        put(self.root + "/sub/linked.bin", text(50000, 7))
        os.link(self.root + "/sub/linked.bin", self.root + "/sub/linked2.bin")
        os.symlink("big.bin", self.root + "/link")
        put(self.root + "/fresh.bin", text(50000, 8), old=False)
        os.utime(self.root + "/fresh.bin", (time.time() + 3600,) * 2)     # a modification time in the future: still compressed
        self.files = ["big.bin", "sub/mid.dat", "sub/noise.bin", "sub/tiny.txt", "sub/deep/mixed.bin", "sub/linked.bin", "fresh.bin"]
        self.before = {f: sha(os.path.join(self.root, f)) for f in self.files}

    def run_cmd(self, *argv):
        out = io.StringIO(); err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = data.main(list(argv), self.env, out)
        return rc, out.getvalue(), err.getvalue()

    def flags(self, rel):
        return os.lstat(os.path.join(self.root, rel)).st_flags & data.UF_COMPRESSED

    def intact(self):
        for f in self.files:
            self.assertEqual(sha(os.path.join(self.root, f)), self.before[f], f)

    def no_tmp(self):
        left = [p for p, _ in data.walk(self.root) if data.is_tmp(os.path.basename(p))]
        self.assertEqual(left, [])

    def test_dry_run_changes_nothing(self):
        rc, out, _ = self.run_cmd("compress", self.root)
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN", out); self.assertIn("to compress: 5 files", out)
        for f in self.files: self.assertFalse(self.flags(f), f)
        self.assertFalse(os.path.exists(self.env["NPS5_DATA_STATE_DIR"]))

    def test_roundtrip(self):
        st0 = os.lstat(self.root + "/big.bin")
        os.chmod(self.root + "/sub/mid.dat", 0o640)
        rc, out, err = self.run_cmd("compress", self.root, "--apply", "--jobs", "2")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("done: 4 files compressed", out); self.assertNotIn("NOT DONE YET", out)
        self.intact(); self.no_tmp()
        for f in ("big.bin", "sub/mid.dat", "sub/deep/mixed.bin", "fresh.bin"): self.assertTrue(self.flags(f), f)
        for f in ("sub/noise.bin", "sub/tiny.txt", "sub/linked.bin"): self.assertFalse(self.flags(f), f)
        self.assertGreater(os.lstat(self.root + "/fresh.bin").st_mtime, time.time() + 3000)
        st1 = os.lstat(self.root + "/big.bin")
        self.assertEqual((st1.st_size, st1.st_mtime_ns, stat.S_IMODE(st1.st_mode), st1.st_uid), (st0.st_size, st0.st_mtime_ns, stat.S_IMODE(st0.st_mode), st0.st_uid))
        self.assertLess(st1.st_blocks, st0.st_blocks)
        self.assertLessEqual(os.lstat(self.root + "/sub/mid.dat").st_blocks, 8)     # was 40: now in the xattr (at most one block is reported)
        self.assertEqual(stat.S_IMODE(os.lstat(self.root + "/sub/mid.dat").st_mode), 0o640)
        self.assertTrue(os.path.islink(self.root + "/link"))
        self.assertEqual(os.lstat(self.root + "/sub/linked.bin").st_nlink, 2)
        # random access and mmap through the kernel
        import mmap
        with open(self.root + "/big.bin", "rb") as f:
            f.seek(650001); self.assertEqual(f.read(10), text(700000, 1)[650001:650011])
            with mmap.mmap(f.fileno(), 0, prot=mmap.PROT_READ) as m: self.assertEqual(m[70000:70020], text(700000, 1)[70000:70020])
        rc, out, _ = self.run_cmd("status", self.root)
        self.assertEqual(rc, 0); self.assertIn("compressed: 4 files", out); self.assertIn("1 no gain", out)
        # idempotent: a second run has nothing to do and does not retry the file that did not shrink
        rc, out, _ = self.run_cmd("compress", self.root, "--apply")
        self.assertEqual(rc, 0); self.assertIn("done: 0 files compressed", out); self.assertIn("compressing 0 files", out)
        # undo
        rc, out, _ = self.run_cmd("decompress", self.root)
        self.assertIn("DRY RUN", out); self.assertTrue(self.flags("big.bin"))
        rc, out, err = self.run_cmd("decompress", self.root, "--apply")
        self.assertEqual((rc, err), (0, "")); self.assertIn("done: 4 files stored plain again", out)
        self.intact(); self.no_tmp()
        for f in self.files: self.assertFalse(self.flags(f), f)
        self.assertEqual(os.lstat(self.root + "/big.bin").st_mtime_ns, st0.st_mtime_ns)

    def test_xattrs_are_carried(self):
        p = self.root + "/big.bin"
        fd = os.open(p, os.O_RDONLY)
        try: data._load(); data._xattr_set(fd, b"org.example.note", b"keep me")
        finally: os.close(fd)
        os.utime(p, (OLD, OLD))
        self.assertEqual(data.compress_file(p)[0], "done")
        fd = os.open(p, os.O_RDONLY)
        try: self.assertEqual(data._xattr_get(fd, b"org.example.note"), b"keep me")
        finally: os.close(fd)
        self.assertEqual(data.decompress_file(p)[0], "done")
        fd = os.open(p, os.O_RDONLY)
        try: self.assertEqual([n for n in data._xattr_names(fd) if not n.startswith(b"com.apple.")], [b"org.example.note"])
        finally: os.close(fd)

    def test_resource_forks(self):
        data._load()
        hdr = struct.pack(">4I", 256, 256, 0, 30)
        blank = (hdr + data.BLANK_FORK_NOTE).ljust(256, b"\0") + hdr + struct.pack(">IHHHHH", 0, 0, 0, 28, 30, 0xFFFF)
        self.assertEqual(len(blank), 286)
        self.assertTrue(data.blank_fork(b"")); self.assertTrue(data.blank_fork(blank))
        self.assertFalse(data.blank_fork(b"real fork data")); self.assertFalse(data.blank_fork(blank[:-2] + b"\0\0"))
        self.assertFalse(data.blank_fork(b"x" * 286))
        for rel, fork, want in (("big.bin", b"", "done"), ("sub/mid.dat", blank, "done"), ("sub/deep/mixed.bin", b"real fork data", "kept")):
            p = os.path.join(self.root, rel)
            fd = os.open(p, os.O_RDONLY)
            try: data._xattr_set(fd, data.XA_RSRC, fork)
            finally: os.close(fd)
            os.utime(p, (OLD, OLD))
            r = data.compress_file(p)
            self.assertEqual(r[0], want, rel)
        self.assertEqual(r[3], "has a resource fork")
        self.intact(); self.no_tmp()
        self.assertTrue(self.flags("big.bin")); self.assertFalse(self.flags("sub/deep/mixed.bin"))
        self.assertEqual(data.decompress_file(self.root + "/big.bin")[0], "done"); self.intact()

    def test_exception_mid_file_leaves_the_original(self):
        for stage in ("written", "verified"):
            def hook(s, path, tmp, stage=stage):
                if s == stage:
                    self.assertTrue(os.path.exists(tmp)); self.assertEqual(sha(path), self.before["big.bin"])
                    raise OSError(5, "simulated failure at " + stage)
            r = data.compress_file(self.root + "/big.bin", hook=hook)
            self.assertEqual(r[0], "error"); self.assertIn("simulated", r[3])
            self.intact(); self.no_tmp(); self.assertFalse(self.flags("big.bin"))

    def test_killed_process_mid_file(self):
        """A process that dies between writing the temporary file and the rename (power loss, kill -9)."""
        for stage in ("written", "verified"):
            pid = os.fork()
            if pid == 0:
                try:
                    data.compress_file(self.root + "/big.bin", hook=lambda s, p, t: os._exit(9) if s == stage else None)
                finally:
                    os._exit(0)
            self.assertEqual(os.waitpid(pid, 0)[1] >> 8, 9)
            self.intact(); self.assertFalse(self.flags("big.bin"))
            left = [p for p, _ in data.walk(self.root) if data.is_tmp(os.path.basename(p))]
            self.assertEqual(len(left), 1)
            rc, out, _ = self.run_cmd("status", self.root)
            self.assertIn("1 leftover temporary file", out)
            rc, out, err = self.run_cmd("compress", self.root, "--apply", "--jobs", "1")
            self.assertEqual((rc, err), (0, "")); self.assertIn("removed 1 leftover", out)
            self.intact(); self.no_tmp(); self.assertTrue(self.flags("big.bin"))
            self.assertEqual(self.run_cmd("decompress", self.root, "--apply")[0], 0)

    def test_file_changed_while_processed(self):
        p = self.root + "/big.bin"
        def hook(s, path, tmp):
            if s == "verified":
                with open(path, "ab") as f: f.write(b"new bytes written by somebody else")
        r = data.compress_file(p, hook=hook)
        self.assertEqual(r[0], "error"); self.assertIn("changed", r[3])
        with open(p, "rb") as f: self.assertTrue(f.read().endswith(b"somebody else"))
        self.assertFalse(self.flags("big.bin")); self.no_tmp()

    def test_write_after_compress_stays_correct(self):
        p = self.root + "/big.bin"
        self.assertEqual(data.compress_file(p)[0], "done")
        with open(p, "ab") as f: f.write(b"tail")
        with open(p, "rb") as f: self.assertEqual(f.read(), text(700000, 1) + b"tail")
        self.assertFalse(self.flags("big.bin"))                    # macOS stores a written file plain again

    def test_left_alone(self):
        self.assertEqual(data.compress_file(self.root + "/sub/noise.bin")[::3], ("kept", "no gain"))
        self.assertEqual(data.compress_file(self.root + "/sub/tiny.txt")[::3], ("kept", "tiny"))
        self.assertEqual(data.compress_file(self.root + "/sub/linked.bin")[::3], ("kept", "hard link"))
        self.assertEqual(data.compress_file(self.root + "/link")[::3], ("kept", "symlink"))
        data.RECENT_S = 60
        self.assertEqual(data.compress_file(self.root + "/fresh.bin")[::3], ("kept", "changed in the last minute"))
        self.assertEqual(data.compress_file(self.root + "/big.bin")[::3], ("kept", "changed in the last minute"))  # old mtime, new ctime
        data.RECENT_S = 0
        self.assertEqual(data.compress_file(self.root + "/missing")[0], "error")
        self.assertEqual(data.decompress_file(self.root + "/big.bin")[::3], ("kept", "not compressed"))
        os.chflags(self.root + "/big.bin", stat.UF_IMMUTABLE)
        try: self.assertEqual(data.compress_file(self.root + "/big.bin")[::3], ("kept", "immutable or append-only"))
        finally: os.chflags(self.root + "/big.bin", 0)
        self.intact(); self.no_tmp()

    def test_pause_and_state_outside_the_folder(self):
        names = sorted(os.listdir(self.root))
        rc, out, _ = self.run_cmd("compress", self.root, "--apply", "--quiet", "--pause-while", self.root + "/does-not-exist")
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(os.listdir(self.root)), names)     # no lock, state or temporary file inside the folder
        self.assertTrue(any(n.endswith(".json") for n in os.listdir(self.env["NPS5_DATA_STATE_DIR"])))

    def test_refuses_non_apfs_and_non_folders(self):
        orig = data.fs_type
        data.fs_type = lambda p: "exfat"
        try:
            rc, out, err = self.run_cmd("compress", self.root, "--apply")
        finally:
            data.fs_type = orig
        self.assertEqual(rc, 2); self.assertIn("exfat", err); self.assertIn("APFS", err)
        self.intact()
        for f in self.files: self.assertFalse(self.flags(f))
        self.assertEqual(self.run_cmd("status", self.root + "/big.bin")[0], 2)
        os.symlink(self.root, self.root + "-link")
        self.assertEqual(self.run_cmd("compress", self.root + "-link")[0], 2)

    def test_not_enough_space(self):
        orig = data.free_bytes
        data.free_bytes = lambda folder: 1000
        try:
            rc, out, err = self.run_cmd("compress", self.root, "--apply")
        finally:
            data.free_bytes = orig
        self.assertEqual(rc, 2); self.assertIn("not enough free space", err)
        self.intact()
        for f in self.files: self.assertFalse(self.flags(f))

    # ---- dcreview additions ----

    def test_metadata_preserved(self):
        for birth in (1_400_000_000.25, 1_550_000_000.5):          # before and AFTER the modification time
            with self.subTest(birth=birth):
                self._metadata_preserved(birth)

    def _metadata_preserved(self, birth):
        p = self.root + "/big.bin"
        data._load()
        fd = os.open(p, os.O_RDONLY)
        try:
            data._xattr_set(fd, b"com.apple.FinderInfo", b"TEXTttxt" + b"\0" * 24)
            data._xattr_set(fd, b"org.example.big", noise(9000, 3))                     # larger than an inline xattr
        finally: os.close(fd)
        os.chmod(p, 0o604)
        os.utime(p, ns=(1_600_000_000_123_456_789, 1_500_000_000_987_654_321))
        fd = os.open(p, os.O_RDONLY)
        try: data._set_crtime(fd, types.SimpleNamespace(st_birthtime=birth))
        finally: os.close(fd)
        os.chflags(p, stat.UF_HIDDEN)
        st0 = os.lstat(p); self.assertEqual(st0.st_birthtime, birth)
        def meta():
            st = os.lstat(p); fd = os.open(p, os.O_RDONLY)
            try: xa = {n: data._xattr_get(fd, n) for n in data._xattr_names(fd) if n not in (data.XA_DECMPFS, data.XA_RSRC)}
            finally: os.close(fd)
            return (stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, st.st_size, st.st_mtime_ns, st.st_atime_ns,
                    st.st_birthtime, st.st_flags & ~data.UF_COMPRESSED, xa)
        m0 = meta()
        self.assertEqual(data.compress_file(p)[0], "done"); self.assertTrue(self.flags("big.bin"))
        self.assertEqual(meta(), m0); self.intact()
        self.assertEqual(data.decompress_file(p)[0], "done"); self.assertFalse(self.flags("big.bin"))
        self.assertEqual(meta(), m0); self.intact()
        self.assertEqual(os.lstat(p).st_nlink, st0.st_nlink)
        os.chflags(p, 0)

    def test_exact_roundtrip_at_the_edges(self):
        """Sizes around the chunk and the inline-xattr limits, and data that is part noise: compress then decompress
        must give the same bytes, read through the kernel each time."""
        sizes = [4096, 4097, data.INLINE_MAX, 65535, 65536, 65537, 2 * 65536, 2 * 65536 + 1, (1 << 21) + 5]
        made = {}
        for n in sizes:
            for kind, gen in (("t", text), ("m", lambda k, seed: text(k // 2, seed) + noise(k - k // 2, seed))):
                rel = "edge/%s%d.bin" % (kind, n); content = gen(n, n % 97)
                put(os.path.join(self.root, rel), content); made[rel] = hashlib.sha256(content).hexdigest()
        rc, out, err = self.run_cmd("compress", self.root + "/edge", "--apply", "--jobs", "3")
        self.assertEqual((rc, err), (0, ""))
        compressed = [r for r in made if self.flags(r)]
        self.assertGreater(len(compressed), len(made) // 2)
        for rel, h in made.items(): self.assertEqual(sha(os.path.join(self.root, rel)), h, rel)
        rc, out, err = self.run_cmd("decompress", self.root + "/edge", "--apply")
        self.assertEqual((rc, err), (0, ""))
        for rel, h in made.items():
            self.assertEqual(sha(os.path.join(self.root, rel)), h, rel); self.assertFalse(self.flags(rel), rel)
        self.no_tmp()

    def test_sparse_file(self):
        p = self.root + "/sub/sparse.bin"
        with open(p, "wb") as f: f.seek(50 << 20); f.write(b"end")
        os.utime(p, (OLD, OLD)); h = sha(p)
        calls = [0]; orig = data._encode_n
        def counting(b, out):
            calls[0] += 1; return orig(b, out)
        data._encode_n = counting
        try: r = data.compress_file(p)
        finally: data._encode_n = orig
        self.assertEqual(r[::3], ("kept", "no gain"))               # holes take no disk: nothing to gain
        self.assertLess(calls[0], 4)                                # and it gave up at once, not after 50 MB
        self.assertEqual(sha(p), h); self.no_tmp()

    def test_memory_budget_limits_files_in_flight(self):
        for i in range(12): put(self.root + "/many/f%02d.bin" % i, text(300000, 100 + i))
        orig_budget, orig_work = data.INFLIGHT_BYTES, data._work
        live = [0]; peak = [0]; lock = __import__("threading").Lock()
        def work(job):
            n = os.lstat(job[1]).st_size
            with lock: live[0] += n; peak[0] = max(peak[0], live[0])
            try: time.sleep(0.02); return orig_work(job)
            finally:
                with lock: live[0] -= n
        data.INFLIGHT_BYTES, data._work = 650000, work
        try: rc, out, err = self.run_cmd("compress", self.root + "/many", "--apply", "--jobs", "4")
        finally: data.INFLIGHT_BYTES, data._work = orig_budget, orig_work
        self.assertEqual((rc, err), (0, "")); self.assertIn("done: 12 files compressed", out)
        self.assertLessEqual(peak[0], 650000)
        self.assertGreaterEqual(peak[0], 600000)                    # still two at a time

    def test_refused_places(self):
        home = os.path.realpath(self.env["HOME"])
        for p in ("/", "/System/Library", "/Library/Caches", "/Applications", "/usr/local", home, os.path.dirname(home),
                  "/private", os.path.join("/System", "Volumes", "Data")):
            self.assertTrue(data.refused_place(p, self.env), p)
        os.makedirs(home + "/Library/Application Support/x", exist_ok=True)
        self.assertTrue(data.refused_place(home + "/Library/Application Support/x", self.env))
        self.assertEqual(data.refused_place(self.root, self.env), "")
        rc, out, err = self.run_cmd("compress", "/", "--apply")
        self.assertEqual(rc, 2); self.assertIn("refusing", err)
        rc, out, err = self.run_cmd("decompress", home, "--apply")
        self.assertEqual(rc, 2); self.assertIn("home folder", err)
        self.assertEqual(self.run_cmd("status", self.root)[0], 0)

    def test_leftover_of_a_live_run_is_kept(self):
        """A run on a parent folder must not delete the temporary file another, still running, run is working on."""
        live = os.path.join(self.root, "sub", "%s%d-1%s" % (data.TMP_PREFIX, os.getppid(), data.TMP_SUFFIX))
        pid = os.fork()
        if pid == 0: os._exit(0)
        os.waitpid(pid, 0)                                          # a pid that has just ended
        dead = os.path.join(self.root, "%s%d-1%s" % (data.TMP_PREFIX, pid, data.TMP_SUFFIX))
        put(live, b"x" * 5000); put(dead, b"y" * 5000)
        rc, out, err = self.run_cmd("compress", self.root, "--apply")
        self.assertEqual(rc, 0); self.assertTrue(os.path.exists(live)); self.assertFalse(os.path.exists(dead))
        os.unlink(live)

    def test_reader_open_during_compress_sees_the_original(self):
        """The game may hold a file open while it is replaced: its descriptor keeps reading the original bytes."""
        p = self.root + "/big.bin"; want = text(700000, 1)
        with open(p, "rb") as f:
            first = f.read(100000)
            self.assertEqual(data.compress_file(p)[0], "done")
            rest = f.read()
        self.assertEqual(first + rest, want); self.intact()

    def test_interrupt_any_time_leaves_every_file_whole(self):
        import signal, subprocess
        made = {}
        for i in range(150):
            rel = "int/d%d/f%03d.bin" % (i % 7, i); c = text(150000 + 997 * i, 300 + i)
            put(os.path.join(self.root, rel), c); made[rel] = hashlib.sha256(c).hexdigest()
        code = ("import importlib.util,sys;sp=importlib.util.spec_from_file_location('d',sys.argv[1]);"
                "d=importlib.util.module_from_spec(sp);sp.loader.exec_module(d);d.RECENT_S=0;"
                "sys.exit(d.main(sys.argv[4:],{'NPS5_DATA_STATE_DIR':sys.argv[2],'HOME':sys.argv[3]}))")
        script = os.path.join(HERE, "..", "scripts", "nps5lib", "data.py")
        for delay in (0.15, 0.4):
            pr = subprocess.Popen([sys.executable, "-I", "-c", code, script, self.env["NPS5_DATA_STATE_DIR"], self.env["HOME"],
                                   "compress", self.root + "/int", "--apply", "--jobs", "3"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(delay); pr.send_signal(signal.SIGINT); o, e = pr.communicate(timeout=60)
            if os.environ.get("NPS5_TEST_VERBOSE"): print(delay, pr.returncode, e[-200:], file=sys.stderr)
            self.assertIn(pr.returncode, (0, 1, -signal.SIGINT), e)
            for rel, h in made.items(): self.assertEqual(sha(os.path.join(self.root, rel)), h, rel)
        rc, out, err = self.run_cmd("compress", self.root + "/int", "--apply")
        self.assertEqual((rc, err), (0, "")); self.no_tmp()
        self.assertTrue(all(self.flags(r) for r in made))
        for rel, h in made.items(): self.assertEqual(sha(os.path.join(self.root, rel)), h, rel)

    # ---- second review (dcreview2) ----

    def raw_decmpfs(self, path):
        """The decmpfs xattr and the resource fork as stored (XATTR_SHOWCOMPRESSION shows what the kernel hides)."""
        import ctypes
        fd = os.open(path, os.O_RDONLY)
        try:
            got = []
            for name in (data.XA_DECMPFS, data.XA_RSRC):
                n = data._libc.fgetxattr(fd, name, None, 0, 0, 0x20)
                if n < 0: got.append(None); continue
                buf = ctypes.create_string_buffer(max(n, 1)); n = data._libc.fgetxattr(fd, name, buf, n, 0, 0x20)
                got.append(buf.raw[:n])
            return got
        finally: os.close(fd)

    def test_streamed_layout_is_the_reference_layout(self):
        """The fork written chunk by chunk in place is byte for byte what decmpfs_payload() builds from the whole file
        (offset table, chunk boundaries, inline vs fork), at every size edge."""
        sizes = [1, 4095, data.INLINE_MAX + 1, 65535, 65536, 65537, 3 * 65536 - 1, 3 * 65536, 3 * 65536 + 1, (1 << 20) + 17]
        seen = set()
        for n in sizes:
            for kind, content in (("t", text(n, n % 89)), ("m", text(n // 3, 1) + noise(n // 3, 2) + text(n - 2 * (n // 3), 3))):
                p = os.path.join(self.root, "lay", "%s%d.bin" % (kind, n)); put(p, content)
                had = os.lstat(p).st_blocks * 512; r = data.compress_file(p, min_size=1)
                ref = data.lzfse_chunks(content)
                if data.stored_bytes(n, ref) + 4096 > had:
                    self.assertEqual(r[::3], ("kept", "no gain"), p); continue
                self.assertEqual(r[0], "done", (p, r))
                head, fork = data.decmpfs_payload(n, ref)
                got_head, got_fork = self.raw_decmpfs(p)
                self.assertEqual(got_head, head, p)
                self.assertEqual(got_fork, None if fork is None else bytes(fork), p)
                self.assertEqual(sha(p), hashlib.sha256(content).hexdigest(), p)
                seen.add("fork" if fork is not None else "inline")
        self.assertEqual(seen, {"fork", "inline"})

    def test_streaming_memory_is_one_compressed_copy(self):
        """Peak memory of compressing one file is about its compressed size (was ~2.5x: chunk list + a zeroed fork)."""
        import subprocess
        p = self.root + "/mem.bin"
        with open(p, "wb") as f:
            for i in range(1600): f.write(noise(32768, i) + bytes(32768))            # 100 MiB, about half compressible
        os.utime(p, (OLD, OLD))
        code = ("import importlib.util,sys,resource;sp=importlib.util.spec_from_file_location('d',sys.argv[1]);"
                "d=importlib.util.module_from_spec(sp);sp.loader.exec_module(d);d.RECENT_S=0;d._load();"
                "r0=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;r=d.compress_file(sys.argv[2]);"
                "print(r[0],r[2],resource.getrusage(resource.RUSAGE_SELF).ru_maxrss-r0)")
        script = os.path.join(HERE, "..", "scripts", "nps5lib", "data.py")
        o = subprocess.run([sys.executable, "-I", "-c", code, script, p], stdout=subprocess.PIPE, text=True, timeout=120).stdout.split()
        self.assertEqual(o[0], "done"); stored, grew = int(o[1]), int(o[2])
        self.assertLess(grew, stored * 1.4 + (16 << 20), o)

    def test_file_that_grows_or_shrinks_while_read(self):
        p = self.root + "/big.bin"; orig = data._has_acl
        for change in ("grow", "shrink"):
            def acl(fd, change=change):                              # runs after the open, before the read
                with open(p, "ab" if change == "grow" else "r+b") as f:
                    f.write(b"more") if change == "grow" else f.truncate(300000)
                return orig(fd)
            data._has_acl = acl
            try: r = data.compress_file(p)
            finally: data._has_acl = orig
            self.assertEqual(r[::3], ("kept", "changed"), change); self.assertFalse(self.flags("big.bin")); self.no_tmp()
        with open(p, "rb") as f: self.assertEqual(f.read(), text(700000, 1)[:300000])

    def test_one_file_larger_than_the_memory_budget_runs_alone(self):
        for i in range(4): put(self.root + "/huge/f%d.bin" % i, text(400000, 200 + i))
        orig = data.INFLIGHT_BYTES; data.INFLIGHT_BYTES = 1000
        try: rc, out, err = self.run_cmd("compress", self.root + "/huge", "--apply", "--jobs", "4")
        finally: data.INFLIGHT_BYTES = orig
        self.assertEqual((rc, err), (0, "")); self.assertIn("done: 4 files compressed", out)

    def test_a_worker_exception_fails_one_file_only(self):
        for i in range(5): put(self.root + "/w/f%d.bin" % i, text(100000 + i, 300 + i))
        bad = os.path.realpath(self.root + "/w/f2.bin"); orig = data._compress_into
        def boom(fd, st, *a):
            if st.st_size == 100002: raise RuntimeError("simulated bug in a worker")
            return orig(fd, st, *a)
        data._compress_into = boom
        try: rc, out, err = self.run_cmd("compress", self.root + "/w", "--apply", "--jobs", "3")
        finally: data._compress_into = orig
        self.assertEqual(rc, 1); self.assertIn("done: 4 files compressed", out); self.assertIn("1 failed", out)
        self.assertIn("f2.bin: RuntimeError: simulated bug", out)
        self.assertEqual(sha(bad), hashlib.sha256(text(100002, 302)).hexdigest()); self.assertFalse(self.flags("w/f2.bin"))
        self.no_tmp()

    def test_compress_right_after_decompress_says_so(self):
        """Every file a decompress (or a copy) just wrote has a change time of now: compress leaves them for a minute,
        and must say so instead of reporting a quiet success."""
        self.assertEqual(self.run_cmd("compress", self.root, "--apply")[0], 0)
        self.assertEqual(self.run_cmd("decompress", self.root, "--apply")[0], 0)
        data.RECENT_S = 60
        rc, out, err = self.run_cmd("compress", self.root)
        self.assertEqual(rc, 0); self.assertIn("to compress: 0 files", out); self.assertIn("note: ", out)
        rc, out, err = self.run_cmd("compress", self.root, "--apply")
        self.assertEqual((rc, err), (3, "")); self.assertIn("done: 0 files compressed", out)   # 3 = not done yet
        self.assertRegex(out, r"NOT DONE YET: [4-9] file\(s\) changed in the last minute")
        for f in self.files: self.assertFalse(self.flags(f), f)
        self.intact()

    def test_refused_places_ignore_letter_case_links_and_firmlinks(self):
        home = os.path.realpath(self.env["HOME"]); env = self.env
        os.makedirs(home + "/Library/Caches/x", exist_ok=True); os.makedirs(home + "/Games/g1", exist_ok=True)
        os.makedirs(home + "/Downloads/game", exist_ok=True)
        link = os.path.join(home, "Games", "to_home"); os.symlink(home, link)
        data_vol = os.path.join("/System", "Volumes", "Data")
        for p in (home.upper(), home.lower(), home + "/library/caches/x", home + "/LIBRARY", "/applications", "/USR/local",
                  "/system/volumes/data", data_vol + home, link, link + "/"):
            self.assertTrue(data.refused_place(p, env), p)
        # a game folder inside the home folder, also named through a firmlink, a link or other letter case, is fine
        for p in (home + "/Games/g1", home + "/Downloads/game", home + "/GAMES/G1", data_vol + home + "/Games/g1",
                  link + "/Downloads/game", self.root):
            self.assertEqual(data.refused_place(p, env), "", p)
        real = os.path.expanduser("~")
        self.assertTrue(data.refused_place(real, {"HOME": real}))
        self.assertTrue(data.refused_place(real.swapcase(), {"HOME": real}))
        self.assertTrue(data.refused_place(os.path.dirname(real).lower(), {"HOME": real}))       # /users holds it
        rc, out, err = self.run_cmd("compress", home.swapcase(), "--apply")
        self.assertEqual(rc, 2); self.assertIn("home folder", err)
        self.assertEqual(self.run_cmd("status", home.swapcase())[0], 0)      # status works anywhere

    def test_one_lock_whatever_the_spelling(self):
        spellings = (self.root, self.root.swapcase(), os.path.join("/System", "Volumes", "Data") + self.root, self.root + "/")
        self.assertEqual(len({data.state_path(p, self.env) for p in spellings}), 1)
        lock = data.take_lock(data.state_path(self.root, self.env))
        try:
            rc, out, err = self.run_cmd("compress", self.root.swapcase(), "--apply")
            self.assertEqual(rc, 2); self.assertIn("another `nps5 data` run", err)
        finally: lock.close()

    def test_leftover_of_a_reused_pid_is_removed(self):
        """A crashed run's pid can be taken by another program: a temporary file untouched for a day is a leftover
        even then."""
        stale = os.path.join(self.root, "%s%d-1%s" % (data.TMP_PREFIX, os.getppid(), data.TMP_SUFFIX))
        put(stale, b"x" * 5000)
        self.assertTrue(data._tmp_alive(stale))
        orig = data.STALE_TMP_S; data.STALE_TMP_S = -1
        try:
            self.assertFalse(data._tmp_alive(stale))
            rc, out, err = self.run_cmd("compress", self.root, "--apply")
        finally: data.STALE_TMP_S = orig
        self.assertEqual(rc, 0); self.assertFalse(os.path.exists(stale)); self.assertIn("removed 1 leftover", out)


def _hdiutil(*a):
    import subprocess
    return subprocess.run(["hdiutil"] + list(a), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)


def disk_images_ok():
    import shutil
    return on_apfs() and shutil.which("hdiutil") is not None and not os.environ.get("NPS5_TEST_NO_DISKIMAGE")


@unittest.skipUnless(disk_images_ok(), "needs macOS with hdiutil (set NPS5_TEST_NO_DISKIMAGE=1 to skip)")
class DiskImages(unittest.TestCase):
    """Small disk images made here (40 MB each, detached and deleted afterwards): a FAT volume, and a nearly full
    APFS volume for out-of-space in the middle of a file."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.mounts = []
        base = os.path.realpath(cls.tmp.name)
        for name, fs in (("apfs", "APFS"), ("fat", "MS-DOS")):
            img = os.path.join(base, name + ".dmg"); mnt = os.path.join(base, name)
            os.mkdir(mnt)
            r = _hdiutil("create", "-size", "40m", "-fs", fs, "-volname", "NPS5T" + name.upper(), "-o", img, "-quiet")
            if r.returncode == 0:
                r = _hdiutil("attach", "-nobrowse", "-noverify", "-noautoopen", "-mountpoint", mnt, img)
            if r.returncode != 0:
                cls.tearDownClass(); raise unittest.SkipTest("hdiutil: " + r.stderr.strip())
            cls.mounts.append(mnt); setattr(cls, name, mnt)
        cls.env = {"NPS5_DATA_STATE_DIR": os.path.join(base, "state"), "HOME": base}

    @classmethod
    def tearDownClass(cls):
        for m in cls.mounts:
            if _hdiutil("detach", m, "-quiet").returncode != 0: _hdiutil("detach", m, "-force", "-quiet")
        cls.mounts = []; cls.tmp.cleanup()

    def setUp(self):
        recent = data.RECENT_S; data.RECENT_S = 0; self.addCleanup(setattr, data, "RECENT_S", recent)
        m = (data.START_MARGIN, data.RUN_MARGIN, data.DECOMPRESS_MARGIN)       # scaled to a 40 MB volume
        data.START_MARGIN, data.RUN_MARGIN, data.DECOMPRESS_MARGIN = 2 << 20, 1 << 20, 2 << 20
        self.addCleanup(lambda: setattr(data, "START_MARGIN", m[0]) or setattr(data, "RUN_MARGIN", m[1]) or setattr(data, "DECOMPRESS_MARGIN", m[2]))

    def run_cmd(self, *argv):
        out = io.StringIO(); err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = data.main(list(argv), self.env, out)
        return rc, out.getvalue(), err.getvalue()

    def test_fat_volume_is_refused(self):
        g = os.path.join(self.fat, "game"); put(g + "/a.bin", text(100000, 1)); h = sha(g + "/a.bin")
        names = sorted(os.listdir(g))                               # macOS may add its own ._ file on FAT
        for cmd in ("compress", "decompress", "status"):
            rc, out, err = self.run_cmd(cmd, g, "--apply") if cmd != "status" else self.run_cmd(cmd, g)
            self.assertEqual(rc, 2, cmd); self.assertIn("msdos", err); self.assertIn("APFS", err)
        self.assertEqual(sha(g + "/a.bin"), h)
        self.assertEqual(sorted(os.listdir(g)), names)

    def test_volume_root_is_refused(self):
        rc, out, err = self.run_cmd("compress", self.apfs, "--apply")
        self.assertEqual(rc, 2); self.assertIn("top of a volume", err)

    def _fill(self):
        fill = os.path.join(self.apfs, "fill"); self.addCleanup(lambda: os.path.exists(fill) and os.unlink(fill))
        try:
            with open(fill, "wb") as f:
                while True: f.write(noise(1 << 20, len(str(f.tell())) + f.tell())); f.flush()
        except OSError: pass
        try:
            with open(fill, "ab") as f:
                while True: f.write(b"z" * 4096); f.flush(); os.fsync(f.fileno())
        except OSError: pass
        return fill

    def test_out_of_space_in_the_middle_of_a_file(self):
        g = os.path.join(self.apfs, "game"); p = g + "/big.bin"
        put(p, text(3 << 20, 4)); put(g + "/c.bin", text(2 << 20, 5))
        h = sha(p); hc = sha(g + "/c.bin")
        self.assertEqual(data.compress_file(g + "/c.bin")[0], "done")
        fill = self._fill()
        r = data.compress_file(p)
        self.assertEqual(r[0], "error"); self.assertIn("space", r[3])           # the resource fork did not fit
        self.assertEqual(sha(p), h); self.assertFalse(os.lstat(p).st_flags & data.UF_COMPRESSED)
        r = data.decompress_file(g + "/c.bin")                                   # 2 MB plain does not fit either
        self.assertEqual(r[0], "error"); self.assertIn("space", r[3])
        self.assertEqual(sha(g + "/c.bin"), hc); self.assertTrue(os.lstat(g + "/c.bin").st_flags & data.UF_COMPRESSED)
        self.assertEqual(sorted(os.listdir(g)), ["big.bin", "c.bin"])           # no temporary file left
        rc, out, err = self.run_cmd("compress", g, "--apply")                   # the run refuses before it starts
        self.assertEqual(rc, 2); self.assertIn("not enough free space", err)
        os.unlink(fill)
        rc, out, err = self.run_cmd("compress", g, "--apply")
        self.assertEqual((rc, err), (0, "")); self.assertTrue(os.lstat(p).st_flags & data.UF_COMPRESSED)
        self.assertEqual(sha(p), h)
        rc, out, err = self.run_cmd("decompress", g, "--apply")
        self.assertEqual((rc, err), (0, "")); self.assertEqual((sha(p), sha(g + "/c.bin")), (h, hc))


if __name__ == "__main__":
    unittest.main()
