#!/usr/bin/env python3
"""Tests of scripts/nps5_check.py: the evasions a game-data / secrets / personal-path check must not fall for.
Scratch git repositories only (index and fast-import; no commit command, so no identity is needed). Run: python3 -I tests/test_check.py"""
import base64, hashlib, importlib.util, os, struct, subprocess, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("nps5_check", os.path.join(REPO, "scripts", "nps5_check.py"))
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)

HOME = "/Users/" + "somebody"           # split so this file passes the check it tests
ELF = b"\x7fELF\x02\x01\x01" + b"\0" * 32
SELF5 = b"\x54\x14\xf5\xee" + b"junk" * 8


def zcompress(data):
    try:
        from compression import zstd
        return zstd.compress(data)
    except ImportError:
        r = subprocess.run(("zstd", "-c", "-q"), input=data, capture_output=True)
        return r.stdout if r.returncode == 0 else None


HAVE_ZSTD = False
try:
    HAVE_ZSTD = bool(zcompress(b"x"))
except OSError:
    pass


def git(d, *a, data=None):
    r = subprocess.run(("git",) + a, cwd=d, input=data, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.decode()


class Repo:
    def __init__(self, files=None, links=None, allow=None):
        self.t = tempfile.TemporaryDirectory(); self.d = self.t.name
        git(self.d, "init", "-q")
        if allow is not None:
            files = dict(files or {}); files[".nps5-check-allow"] = allow.encode()
        self.write(files or {}, links or {})

    def write(self, files, links=None):
        for k, v in files.items():
            p = os.path.join(self.d, k); os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f: f.write(v)
        for k, v in (links or {}).items():
            p = os.path.join(self.d, k); os.makedirs(os.path.dirname(p), exist_ok=True); os.symlink(v, p)
        git(self.d, "add", "-A")

    def check(self, mode="tree", rng=""):
        return C.Check(self.d, mode, rng).run()

    def fails(self, mode="tree", rng=""):
        return "\n".join(self.check(mode, rng).fail)

    def close(self): self.t.cleanup()


class Base(unittest.TestCase):
    def repo(self, *a, **k):
        r = Repo(*a, **k); self.addCleanup(r.close); return r

    def caught(self, files, needle, links=None, modes=("tree", "staged")):
        r = self.repo(files, links)
        for m in modes:
            self.assertIn(needle, r.fails(m), "%s mode missed %s" % (m, needle))
        return r

    def clean(self, files, links=None, allow=None):
        r = self.repo(files, links, allow)
        for m in ("tree", "staged"):
            self.assertEqual(r.fails(m), "", m)
        return r


class TestEvasions(Base):
    def test_selftest(self):
        self.assertEqual(C.selftest(), 0)

    def test_dump_in_third_party_folder(self):
        for p in ("vendor/eboot.bin", "3rdparty/x/data.self", "src/vendor/blob.txt", "externals/PPSA12345/a.txt", "third_party/x/sce_sys/param.json"):
            self.caught({p: SELF5}, p + ":")

    def test_third_party_source_keeps_its_authors_paths(self):
        self.clean({"3rdparty/lib/a.c": ("// built in %s/src\nint x;\n" % HOME).encode(), "vendor/big.c": b"int a;\n" * 200000})

    def test_renamed_executable_and_archives(self):
        for name, magic in (("notes.txt", ELF), ("a.md", b"\xfd7zXZ\x00" + b"\0" * 20), ("b.md", b"BZh91AY&SY" + b"\1" * 20),
                            ("c.txt", b"7z\xbc\xaf\x27\x1c" + b"\0" * 20), ("d.txt", b"\x04\x22\x4d\x18" + b"\1" * 20),
                            ("e.txt", b"Rar!\x1a\x07\x00" + b"\1" * 20), ("f.txt", b"a" * 257 + b"ustar\x0000" + b"\0" * 250),
                            ("g.c", b"\x1f\x8b\x08" + b"\1" * 20), ("h.txt", b"\x28\xb5\x2f\xfd" + b"\1" * 20)):
            self.caught({name: magic}, name + ": content is")

    def test_binary_hidden_behind_a_text_start(self):
        self.caught({"late.txt": b"hello world\n" * 2000 + b"\0\1\2\3" * 100}, "late.txt: binary content")
        self.caught({"utf16.txt": ("cd %s/x\n" % HOME).encode("utf-16")}, "utf16.txt: binary content")

    def test_encoded_blob_in_text(self):
        blob = base64.encodebytes(os.urandom(12000))
        self.caught({"data.txt": b"# just notes\n" + blob}, "data.txt: ")
        self.caught({"hex.txt": "\n".join(os.urandom(40).hex() for _ in range(200)).encode() + b"\n"}, "hex.txt: ")
        self.caught({"fix.patch": b"diff --git a/x b/x\nGIT binary patch\nliteral 12\nabc\n"}, "fix.patch: holds a git binary patch")

    def test_hash_lists_and_separators_are_text(self):
        rows = "".join("%s  file%d.c\n" % (hashlib.sha256(b"%d" % i).hexdigest(), i) for i in range(400))
        self.clean({"SHA256SUMS": rows.encode(), "doc.md": ("-" * 100 + "\n" + "=" * 100 + "\n").encode() * 200,
                    "one.txt": (hashlib.sha256(b"x").hexdigest() + "\n").encode() * 20})

    def test_symlinks(self):
        self.caught({"ok.sh": b"ls\n"}, "abs: symbolic link", links={"abs": HOME + "/secret"})
        self.caught({"ok.sh": b"ls\n"}, "sub/up: symbolic link", links={"sub/up": "../../outside"})
        self.clean({"ok.sh": b"ls\n"}, links={"sub/in": "../ok.sh", "same": "ok.sh"})

    def test_tree_mode_reads_git_not_the_work_tree(self):
        r = self.repo({"notes.txt": ELF, "path.sh": ("cd %s\n" % HOME).encode()})
        os.remove(os.path.join(r.d, "notes.txt"))                       # still in the index: still checked
        with open(os.path.join(r.d, "path.sh"), "w") as f: f.write("ls\n")
        out = r.fails("tree")
        self.assertIn("notes.txt: content is ELF", out); self.assertIn("path.sh:1: personal absolute path", out)

    def test_large_file(self):
        self.caught({"big.txt": b"a\n" * (5 << 20)}, "big.txt: %d bytes" % (10 << 20))

    def test_case_and_profile_paths(self):
        self.caught({"Dump/EBOOT.BIN.txt": b"x\n"}, "Dump/EBOOT.BIN.txt: path looks like")
        self.caught({"games/PPSA00000/extra/a.txt": b"x\n"}, "games/PPSA00000/extra/a.txt: path looks like")
        self.caught({"x/ppsa12345/readme.txt": b"x\n"}, "x/ppsa12345/readme.txt: path looks like")

    def test_copy_of_the_checker_is_not_exempt(self):
        self.caught({"tools/nps5_check.py": b"K = 'ghp_" + b"A" * 36 + b"'\n"}, "tools/nps5_check.py:1: looks like a secret")


class TestText(Base):
    def test_home_path_forms(self):
        for i, line in enumerate(("CFLAGS=-I%s/include", "x = -isystem%s/inc", "see file://%s/doc.html", "-L%s/lib", "cd %s", "'%s/x'")):
            self.caught({"f%d.mk" % i: (line % HOME + "\n").encode()}, "f%d.mk:1: personal absolute path" % i)

    def test_placeholders_and_relative_paths_pass(self):
        self.clean({"ok.md": b"cd /Users/you/x; ls /home/user; docs/Users/guide; $HOME/home/x; -I/Users/<name>/inc; --user-I/Users\n"})

    def test_secret_far_into_a_long_line(self):
        for col in (10, 3990, 4005, 9000):
            self.caught({"min.js": b"a" * col + b" ghp_" + b"B" * 36 + b" " + b"c" * 6000 + b"\n"}, "min.js:1: looks like a secret")

    def test_staged_mode_checks_added_lines_only(self):
        r = self.repo({"a.sh": b"ls\n"})
        self.assertEqual(r.fails("staged"), "")
        r.write({"a.sh": ("ls\ncd %s\n" % HOME).encode()})
        self.assertIn("a.sh:2: personal absolute path", r.fails("staged"))


class TestAllow(Base):
    def test_fixture_allowed(self):
        self.clean({"tests/fixtures/a.spv": b"\x03\x02\x23\x07" + b"\0" * 16}, allow="# built from a.comp\ntests/fixtures/*.spv\n")

    def test_wide_glob_is_refused(self):
        for g in ("*", "*.bin", "games/*", "*/eboot.bin", "**"):
            r = self.repo({"eboot.bin": SELF5}, allow=g + "\n")
            self.assertIn(".nps5-check-allow: glob", r.fails("tree"), g)

    def test_allow_change_is_reported(self):
        r = self.repo({"a.sh": b"ls\n"}, allow="tests/fixtures/*.spv\n")
        self.assertTrue(any(".nps5-check-allow: changes what the check exempts" in w for w in r.check("staged").warn))
        self.assertFalse(any("changes what the check" in w for w in r.check("tree").warn))


def section(sid, raw, stored=None, raw_size=None):
    stored = zcompress(raw) if stored is None else stored
    return struct.pack("<IIQQQ", sid, 0, len(stored), len(raw) if raw_size is None else raw_size, 0) + stored


def info(tid="PPSA00000"):
    return struct.pack("<I", len(tid)) + tid.encode() + b"\0" * 16


def kyplst(sections):
    return C.KYPLST_MAGIC + struct.pack("<II", 2, len(sections)) + b"".join(sections)


def sidecar(data, result=C.KYPLST_OK_LINE):
    return ("# record\nsha256=%s\nresult=%s\n" % (hashlib.sha256(data).hexdigest(), result)).encode()


@unittest.skipUnless(HAVE_ZSTD, "needs Python 3.14 or the zstd tool")
class TestPipelineList(Base):
    P = "games/PPSA00000/pipelines.kyplst"

    def problems(self, data, side=None, path=None):
        return C.check_kyplst(path or self.P, data, sidecar(data) if side is None else side)

    def test_good_list(self):
        data = kyplst([section(1, info()), section(3, os.urandom(64) * 4)])
        self.assertEqual(self.problems(data), [])
        self.clean({self.P: data, self.P + ".verified": sidecar(data), "games/PPSA00000/game.tsv": b"title_id\tPPSA00000\n"})

    def test_real_list_of_the_repository(self):
        p = os.path.join(REPO, "games", "PPSA21564", "pipelines.kyplst")
        self.assertEqual(C.check_kyplst("games/PPSA21564/pipelines.kyplst", open(p, "rb").read(), open(p + ".verified", "rb").read()), [])

    def test_skippable_frame_hides_a_payload(self):
        z = zcompress(os.urandom(64))
        hidden = b"\x50\x2a\x4d\x18" + struct.pack("<I", 4000) + (b"\x10\x32\x54\x76" * 1000)      # zstd skippable frame: the decoder skips it
        data = kyplst([section(1, info()), section(3, os.urandom(64), stored=z + hidden, raw_size=64)])
        self.assertIn("not exactly one zstd frame", " ".join(self.problems(data)))

    def test_second_frame_and_trailing_bytes(self):
        a, b = zcompress(b"A" * 64), zcompress(b"B" * 64)
        self.assertIn("not exactly one zstd frame", " ".join(self.problems(kyplst([section(1, info()), section(3, b"", stored=a + b, raw_size=128)]))))
        self.assertIn("not exactly one zstd frame", " ".join(self.problems(kyplst([section(1, info()), section(3, b"", stored=a + b"tail", raw_size=64)]))))
        data = kyplst([section(1, info())]) + b"extra"
        self.assertIn("bytes after the last section", " ".join(self.problems(data)))

    def test_decompression_bomb_is_bounded(self):
        bomb = zcompress(b"\0" * (32 << 20))
        self.assertLess(len(bomb), 1 << 16)
        self.assertLessEqual(len(C.zstd_decompress(bomb, 1000)), 1001)
        data = kyplst([section(1, info()), section(3, b"", stored=bomb, raw_size=1000)])
        self.assertIn("decompressed size differs", " ".join(self.problems(data)))
        data = kyplst([section(1, info()), section(3, b"", stored=bomb, raw_size=1 << 40)])
        self.assertIn("out of bounds", " ".join(self.problems(data)))

    def test_forbidden_content_in_a_section(self):
        for payload, what in ((b"\x03\x02\x23\x07" + b"\0" * 60, "SPIR-V"), (b"x" * 10 + b"metal_stdlib", "MSL text"),
                              (b"pad" + zcompress(os.urandom(100)), "nested zstd"), (b"MTLB" + b"\1" * 40, "Metal library")):
            self.assertIn(what, " ".join(self.problems(kyplst([section(1, info()), section(3, payload)]))))

    def test_info_section_and_title(self):
        self.assertIn("no info section", " ".join(self.problems(kyplst([section(3, b"x" * 64)]))))
        self.assertIn("title ID", " ".join(self.problems(kyplst([section(1, info("PPSA99999"))]))))

    def test_proof_record(self):
        data = kyplst([section(1, info())])
        self.assertIn("no pipelines.kyplst.verified", " ".join(self.problems(data, side=b"")))
        self.assertIn("for other bytes", " ".join(self.problems(data, side=sidecar(data + b"x"))))
        self.assertIn("does not record a passed", " ".join(self.problems(data, side=sidecar(data, "verify: FAILED"))))

    def test_place_and_size(self):
        data = kyplst([section(1, info())])
        self.assertIn("only accepted as", " ".join(self.problems(data, path="lists/x.kyplst")))
        self.assertIn("limit", " ".join(self.problems(data + b"\0" * (C.KYPLST_MAX_BYTES + 1))))
        self.caught({"vendor/x.kyplst": data}, "vendor/x.kyplst:")

    def test_one_frame_walker(self):
        z = zcompress(b"hello" * 1000)
        self.assertTrue(C.zstd_one_frame(z))
        for bad in (z + z, z + b"\0", z[:-1], b"", b"\x28\xb5\x2f\xfd", b"\x50\x2a\x4d\x18\x00\x00\x00\x00" + z):
            self.assertFalse(C.zstd_one_frame(bad))


def fast_import(d, commits, ref="t"):
    """commits: [(mark, parent mark or None, {path: bytes or None})] on refs/heads/t. No identity from git configuration is used."""
    s = b""
    for mark, parent, files in commits:
        s += b"commit refs/heads/" + ref.encode() + b"\nmark :%d\ncommitter T <t@example.invalid> 1 +0000\ndata 1\nc\n" % mark
        if parent: s += b"from :%d\n" % parent
        for p, v in files.items():
            s += (b"D %s\n" % p.encode()) if v is None else (b"M 100644 inline %s\ndata %d\n%s\n" % (p.encode(), len(v), v))
        s += b"\n"
    git(d, "fast-import", "--quiet", "--export-marks=marks", data=s)
    with open(os.path.join(d, "marks")) as f:
        return {int(l.split()[0][1:]): l.split()[1] for l in f}


class TestRange(Base):
    def setUp(self):
        self.r = self.repo()
        self.m = fast_import(self.r.d, [(1, None, {"a.sh": b"ls\n"}), (2, 1, {"notes.txt": ELF, "p.sh": ("cd %s\n" % HOME).encode()}),
                                        (3, 2, {"notes.txt": None, "p.sh": b"ls\n"}), (4, 3, {"b.sh": b"ls\n"})])

    def test_added_then_deleted_inside_the_range_is_still_history(self):
        out = self.r.fails("range", "%s..%s" % (self.m[1], self.m[4]))
        self.assertIn("%s:notes.txt: content is ELF" % self.m[2][:10], out)
        self.assertIn("%s:p.sh:1: personal absolute path" % self.m[2][:10], out)

    def test_clean_range_and_forms(self):
        self.assertEqual(self.r.fails("range", "%s..%s" % (self.m[3], self.m[4])), "")
        self.assertIn("notes.txt", self.r.fails("range", "%s...%s" % (self.m[1], self.m[4])))     # three dots used to skip every content rule
        git(self.r.d, "update-ref", "HEAD", self.m[4])
        self.assertIn("notes.txt", self.r.fails("range", self.m[1]))                              # one revision = X..HEAD

    def test_root_commit_is_checked(self):
        m = fast_import(self.r.d, [(9, None, {"eboot.bin": SELF5}), (10, 9, {"eboot.bin": None, "a.sh": b"ls\n"})], ref="u")
        git(self.r.d, "update-ref", "HEAD", m[10])
        self.assertIn("%s:eboot.bin" % m[9][:10], self.r.fails("history"))
        self.assertEqual(self.r.fails("range", "%s..%s" % (m[9], m[10])), "")

    def test_bad_range_is_an_error_not_a_pass(self):
        with self.assertRaises(SystemExit) as e:
            self.r.check("range", "nosuchrev..HEAD")
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
