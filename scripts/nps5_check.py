#!/usr/bin/env python3
"""nps5 pre-commit / pre-PR check: game data, secrets, personal paths, tests.

Usage:
  scripts/check.sh                 check staged changes (pre-commit mode)
  scripts/check.sh --tree          check every tracked file
  scripts/check.sh --range A..B    check files and added lines of a commit range
  scripts/check.sh --tests         also run the commands in .nps5-check-tests
  scripts/check.sh --selftest      plant known-bad files in a scratch repo; the check must catch each

Works in any git repository (this one and the component repositories). Exit 0 = clean,
1 = findings, 2 = usage or harness error. Reads only git; never opens files outside the repo.

Per-repo configuration (all optional, in the repository root):
  .nps5-check-allow    one glob per line: paths exempt from the file-type, size, secret and home-path rules
                       (e.g. tests/fixtures/*.spv built from this repo's own GLSL). Say why in a comment.
  .nps5-check-tests    one shell command per line, run by --tests from the repository root.
  .nps5-check-local    NOT committed: extra forbidden regexes, one per line (your user name,
                       private remote names, machine names).

Game profiles (games/<title id>/) may hold only the files games/README.md lists. A pipeline list
(pipelines.kyplst: keys, hashes and state, no code) is accepted only when it is well-formed, no decompressed
section contains a shader binary or shader text, and pipelines.kyplst.verified records the no-game-code proof
(scripts/verify-pipeline-list.sh) for exactly those bytes. Shader binaries in any form are always rejected.
"""
import argparse, fnmatch, hashlib, os, re, struct, subprocess, sys, tempfile

SKIP_DIRS = ("3rdparty/", "vendor/", "externals/", "third_party/")
MAX_BYTES = 1_000_000

# Extensions that are game data or run artefacts in this project. A legitimate source-built
# fixture goes into .nps5-check-allow with a comment that says where its source is.
BAD_EXT = {
    # guest executables and containers
    "self", "sprx", "prx", "pkg", "pfs", "sfo", "ucas", "utoc", "pak", "psarc", "at9", "bik", "gnf", "dds",
    # shader and pipeline dumps and caches
    "spv", "metallib", "air", "msl", "sb", "req", "journal", "known", "profdata", "profraw",
    # frame dumps, captures and traces
    "bmp", "ppm", "raw", "trace", "gputrace", "dmp",
    # generic binaries and archives
    "elf", "bin", "dat", "o", "a", "dylib", "so", "exe", "dll", "zip", "7z", "gz", "zst", "xz", "tar",
}
SOURCE_EXT = {"c", "cc", "cpp", "cxx", "h", "hpp", "inc", "m", "mm", "go", "rs", "py", "sh", "md", "cmake",
              "yml", "yaml", "toml", "s", "S", "glsl", "vert", "frag", "comp", "mesh", "patch"}
BAD_PATH = [
    r"(^|/)(out|dumps?|runs_out|cache[^/]*|frames?|captures?|logs?)/",
    r"(^|/)eboot(\.|$)", r"(^|/)sce_(sys|module)/", r"(^|/)(PPSA|CUSA|PCAS|PLAS)\d{5}[^/]*/",
    r"frame_\d+\.", r"\.(programs|pipelines|shaders)\.", r"perf_frames\.csv$", r"(^|/)pcmap\.bin$",
]
# A game profile folder, games/<title id>/, may hold exactly these files (games/README.md). Everything else that
# carries a title ID in its path is treated as a dump.
PROFILE_RE = re.compile(r"^games/((?:PPSA|CUSA|PCAS|PLAS)\d{5})/([^/]+)$")
PROFILE_FILES = {"README.md", "game.tsv", "patches.py", "bench.tsv", "validated.tsv",
                 "pipelines.kyplst", "pipelines.kyplst.verified", "scenes.tsv", "smoke.tsv"}
# pad scripts (input timings only): games/<title id>/pad/<name>.vpad
PROFILE_PAD_RE = re.compile(r"^games/(?:PPSA|CUSA|PCAS|PLAS)\d{5}/pad/[A-Za-z0-9_.-]+\.vpad$")
KYPLST_MAGIC = b"KYPLST1\0"
KYPLST_SECTIONS = {1, 2, 3, 4, 5}   # info, levels, shaders, permutations, pipelines (pipelineList.h in the kyty fork)
KYPLST_OK_LINE = "verify: no shader code in the list"
# Things that must not occur inside a decompressed list section: shader binaries and shader text in any form.
KYPLST_FORBIDDEN = [
    (b"\x03\x02\x23\x07", "SPIR-V"), (b"MTLB", "Metal library"), (b"BC\xc0\xde", "LLVM bitcode"),
    (b"\xde\xc0\x17\x0b", "LLVM bitcode wrapper"), (b"\x7fELF", "ELF"), (b"\x54\x14\xf5\xee", "PS5 SELF"),
    (b"\x4f\x15\x3d\x1d", "PS4 SELF"), (b"metal_stdlib", "MSL text"), (b"OrbShdr", "PS shader binary"),
    (b"Shdr", "PS shader binary"), (b"target triple", "AIR text"), (b"OpCapability", "SPIR-V text"),
]


def zstd_decompress(data):
    try:
        from compression import zstd          # Python 3.14+
        return zstd.decompress(data)
    except ImportError:
        pass
    try:
        r = subprocess.run(("zstd", "-d", "-c", "-q"), input=data, capture_output=True)
    except OSError:
        return None
    return r.stdout if r.returncode == 0 else None


def check_kyplst(path, data, sidecar):
    """A pipeline list is accepted only as games/<title id>/pipelines.kyplst, well-formed, free of shader binaries
    and shader text in every decompressed section, and with a .verified record of the no-game-code proof
    (scripts/verify-pipeline-list.sh, which needs the game) for exactly these bytes. Returns a list of problems."""
    m = PROFILE_RE.match(path)
    if not m or m.group(2) != "pipelines.kyplst":
        return ["a pipeline list is only accepted as games/<title id>/pipelines.kyplst"]
    if len(data) < 16 or data[:8] != KYPLST_MAGIC:
        return ["not a KYPLST1 pipeline list"]
    version, count = struct.unpack_from("<II", data, 8)
    if version != 2 or not 1 <= count <= 16:
        return [f"unknown pipeline list version {version} or section count {count}"]
    off, bad, seen = 16, [], set()
    for _ in range(count):
        if off + 32 > len(data):
            return ["truncated section header"]
        sid, _flags, stored, raw_size, _hash = struct.unpack_from("<IIQQQ", data, off)
        off += 32
        if sid not in KYPLST_SECTIONS or sid in seen or off + stored > len(data) or raw_size > 64 * 1024 * 1024:
            return [f"section {sid}: unknown, repeated or out of bounds"]
        seen.add(sid)
        raw = zstd_decompress(data[off:off + stored])
        off += stored
        if raw is None:
            return ["cannot decompress a section (needs Python 3.14 or the zstd tool); not accepted unchecked"]
        if len(raw) != raw_size:
            return [f"section {sid}: decompressed size differs from its header"]
        for needle, what in KYPLST_FORBIDDEN:
            if needle in raw:
                bad.append(f"section {sid} contains {what}")
        if sid == 1:
            n = struct.unpack_from("<I", raw, 0)[0] if len(raw) >= 4 else 0
            if raw[4:4 + n].decode(errors="replace") != m.group(1):
                bad.append("the list's title ID is not the profile folder's")
    if off != len(data):
        bad.append(f"{len(data) - off} bytes after the last section")
    rec = dict(l.split("=", 1) for l in sidecar.decode(errors="replace").splitlines() if "=" in l and not l.startswith("#"))
    if not sidecar:
        bad.append("no pipelines.kyplst.verified next to it: run scripts/verify-pipeline-list.sh against your own copy of the game")
    elif rec.get("sha256") != hashlib.sha256(data).hexdigest():
        bad.append("pipelines.kyplst.verified is for other bytes: rerun scripts/verify-pipeline-list.sh")
    elif rec.get("result") != KYPLST_OK_LINE:
        bad.append("pipelines.kyplst.verified does not record a passed no-game-code proof")
    return bad


MAGIC = [
    (b"\x7fELF", "ELF executable"), (b"\x4f\x15\x3d\x1d", "PS4 SELF"), (b"\x54\x14\xf5\xee", "PS5 SELF"),
    (b"\x7fCNT", "PKG"), (b"\x03\x02\x23\x07", "SPIR-V"), (b"MTLB", "Metal library"),
    (b"BC\xc0\xde", "LLVM bitcode"), (b"\xde\xc0\x17\x0b", "LLVM bitcode wrapper"),
    (b"BM", "BMP image"), (b"\x89PNG", "PNG image"), (b"\xff\xd8\xff", "JPEG image"),
    (b"\x28\xb5\x2f\xfd", "zstd data"), (b"\x1f\x8b", "gzip data"), (b"PK\x03\x04", "zip archive"),
    (b"\xcf\xfa\xed\xfe", "Mach-O binary"), (b"\xca\xfe\xba\xbe", "Mach-O fat binary"),
    (b"\xff\x6c\x70\x72\x6f\x66", "LLVM profile data"), (b"\x81\x52\x66\x6f\x72\x70\x6c\xff", "LLVM profile data"),
]
SECRETS = [
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}", r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"sk-ant-[A-Za-z0-9_-]{20,}", r"sk-[A-Za-z0-9]{40,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}", r"AIza[0-9A-Za-z_-]{35}", r"pplx-[A-Za-z0-9]{30,}",
    r"(?i)\b(api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*['\"][A-Za-z0-9/+_-]{20,}['\"]",
]
# /Users/<name> and /home/<name>; shared and placeholder names are fine.
HOME_RE = re.compile(r"(?<![\w/.$}>-])(/Users|/home)/(?!Shared\b|runner\b|you\b|me\b|user\b|username\b|name\b|USER\b|bob\b|alice\b|ally\b|\$|<|\{|\*|\.\.\.)[A-Za-z0-9._-]+")
WINHOME_RE = re.compile(r"[A-Za-z]:\\Users\\(?!Public\b|<|%)[A-Za-z0-9._-]+")
VOLUME_RE = re.compile(r"/Volumes/(?!\$|<|\{|\*|Macintosh HD\b)[A-Za-z0-9._-]+")
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,128}\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(noreply|no-reply|@localhost|@example\.|@invalid|@users\.noreply\.github\.com|@anthropic\.com$|^git@)", re.I)
MACHINE = [  # warnings only: values that are right for one machine
    (r"(?<![\w.])18944(?![\w.])", "memory self-limit sized for a 24 GB Mac: derive it from the config (NPS5_SELF_MEM_LIMIT_MB)"),
    (r"/opt/homebrew/", "Homebrew prefix hard-coded: use $(brew --prefix) or NPS5_BREW_PREFIX"),
]


def git(*a, cwd=None, binary=False, check=True):
    r = subprocess.run(("git",) + a, cwd=cwd, capture_output=True)
    if check and r.returncode != 0:
        sys.stderr.write(r.stderr.decode(errors="replace"))
        sys.exit(2)
    return r.stdout if binary else r.stdout.decode(errors="replace")


def read_list(root, name):
    p = os.path.join(root, name)
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8", errors="replace") as f:
        return [l.split(" #")[0].strip() for l in f if l.strip() and not l.lstrip().startswith("#")]


def allowed(path, allow):
    return any(fnmatch.fnmatch(path, g) for g in allow)


class Check:
    def __init__(self, root, mode, rng):
        self.root, self.mode, self.rng = root, mode, rng
        self.allow = read_list(root, ".nps5-check-allow")
        self.local = [re.compile(x) for x in read_list(root, ".nps5-check-local")]
        self.fail, self.warn = [], []

    def files(self):
        if self.mode == "tree":
            out = git("ls-files", "-z", cwd=self.root)
        elif self.mode == "range":
            out = git("diff", "--name-only", "-z", "--diff-filter=ACMR", self.rng, cwd=self.root)
        else:
            out = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", cwd=self.root)
        return [f for f in out.split("\0") if f and not f.startswith(SKIP_DIRS) and not any("/" + d in f for d in SKIP_DIRS)]

    def blob(self, path):
        if self.mode == "tree":
            p = os.path.join(self.root, path)
            if os.path.islink(p) or not os.path.isfile(p):
                return b""
            with open(p, "rb") as f:
                return f.read()
        rev = self.rng.split("..")[-1] if self.mode == "range" else ""
        return git("show", f"{rev}:{path}", cwd=self.root, binary=True, check=False)

    def added_lines(self, path, data):
        """Text to scan for secrets and personal data: added lines in diff modes, everything in tree mode."""
        if self.mode == "tree":
            return list(enumerate(data.decode(errors="replace").splitlines(), 1))
        args = ("diff", "-U0", "--no-color") + (("--cached",) if self.mode == "staged" else (self.rng,)) + ("--", path)
        res, n = [], 0
        for l in git(*args, cwd=self.root, check=False).splitlines():
            m = re.match(r"@@ -\S+ \+(\d+)", l)
            if m:
                n = int(m.group(1)); continue
            if l.startswith("+") and not l.startswith("+++"):
                res.append((n, l[1:])); n += 1
        return res

    def run(self):
        for path in self.files():
            data = self.blob(path)
            exempt = allowed(path, self.allow)
            ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
            profile = PROFILE_RE.match(path)
            if profile and profile.group(2) not in PROFILE_FILES:
                self.fail.append(f"{path}: not a profile file (games/README.md lists them); game-derived data stays out")
            if ext == "kyplst":
                for why in check_kyplst(path, data, self.blob(path + ".verified")):
                    self.fail.append(f"{path}: {why}")
                continue
            if not exempt:
                if ext in BAD_EXT:
                    self.fail.append(f"{path}: file type .{ext} is game data, a run artefact or a build output")
                for rx in BAD_PATH:
                    if rx == BAD_PATH[3] and ((profile and profile.group(2) in PROFILE_FILES) or PROFILE_PAD_RE.match(path)):
                        continue
                    if re.search(rx, path) and not (rx == BAD_PATH[0] and ext in SOURCE_EXT):
                        self.fail.append(f"{path}: path looks like a dump, cache, capture or run output ({rx})"); break
                for mg, what in MAGIC:
                    if data.startswith(mg):
                        self.fail.append(f"{path}: content is {what}"); break
                if len(data) > MAX_BYTES:
                    self.fail.append(f"{path}: {len(data)} bytes (limit {MAX_BYTES}); large files are usually dumps")
            if b"\0" in data[:8192]:
                continue  # binary: content rules above already applied
            if os.path.basename(path) in ("nps5_check.py",) :
                continue  # the rules themselves
            for n, line in self.added_lines(path, data):
                line = line[:4000]  # bounds regex time on minified or generated lines
                for rx in SECRETS:
                    if re.search(rx, line) and not exempt:
                        self.fail.append(f"{path}:{n}: looks like a secret ({rx[:28]}...)")
                for rx in (HOME_RE, WINHOME_RE, VOLUME_RE):
                    m = rx.search(line)
                    if m and not exempt:
                        self.fail.append(f"{path}:{n}: personal absolute path {m.group(0)!r}: use $NPS5_ROOT or a relative path")
                for m in EMAIL_RE.finditer(line):
                    if not EMAIL_OK.search(m.group(0)) and not exempt and os.path.basename(path) not in ("NOTICE", "LICENSE", "AUTHORS"):
                        self.warn.append(f"{path}:{n}: e-mail address {m.group(0)}: only with its owner's consent")
                for rx in self.local:
                    if rx.search(line):
                        self.fail.append(f"{path}:{n}: matches a .nps5-check-local rule ({rx.pattern})")
                for rx, why in MACHINE:
                    if re.search(rx, line):
                        self.warn.append(f"{path}:{n}: {why}")
        return self


def run_tests(root):
    cmds = read_list(root, ".nps5-check-tests")
    if not cmds:
        print("tests: no .nps5-check-tests in this repository"); return 0
    bad = 0
    for c in cmds:
        print(f"tests: $ {c}", flush=True)
        if subprocess.run(c, shell=True, cwd=root).returncode != 0:
            print(f"tests: FAILED: {c}"); bad += 1
    return bad


def selftest():
    me = os.path.abspath(__file__)
    planted = {
        "eboot.bin": b"\x54\x14\xf5\xeejunk", "shader/a.spv": b"\x03\x02\x23\x07" + b"\0" * 16,
        "notes/frame_001.bmp": b"BM" + b"\0" * 64, "renamed.txt": b"\x7fELF\x02\x01\x01" + b"\0" * 32,
        "work/x/out/list.txt": b"pipeline list\n", "big.txt": b"a" * (MAX_BYTES + 1),
        "key.sh": b"TOKEN=ghp_" + b"A" * 36 + b"\n", "path.sh": b"cd /Users/" + b"somebody/Downloads/x\n", "vol.sh": b"D=/Volumes/" + b"MyDrive/ps5\n",
        "pem.txt": b"-----BEGIN RSA PRIVATE" + b" KEY-----\n",
        "games/PPSA00000/pipelines.kyplst": KYPLST_MAGIC + struct.pack("<II", 2, 1) + b"junk",   # malformed, no proof
        "lists/stray.kyplst": KYPLST_MAGIC + struct.pack("<II", 2, 0),                           # outside a profile
        "games/PPSA00000/shaders.tsv": b"not a profile file\n",
        "games/PPSA00000/vs_0001.metallib": b"MTLB" + b"\0" * 16,
    }
    clean = {"ok.sh": b"cd \"$NPS5_ROOT\" && ls /Users/Shared\n", "ok.md": b"mail nobody@example.com\n",
             "games/PPSA00000/game.tsv": b"title_id\tPPSA00000\n", "games/PPSA00000/README.md": b"# Example\n"}
    with tempfile.TemporaryDirectory() as d:
        git("init", "-q", cwd=d)
        for k, v in {**planted, **clean}.items():
            os.makedirs(os.path.dirname(os.path.join(d, k)) or d, exist_ok=True)
            with open(os.path.join(d, k), "wb") as f:
                f.write(v)
        git("add", "-A", cwd=d)
        missed = []
        for mode in ("staged", "tree"):
            c = Check(d, mode, "").run()
            text = "\n".join(c.fail)
            missed += [f"{mode}:{k}" for k in planted if k + ":" not in text]
            missed += [f"{mode}:false positive {k}" for k in clean if k + ":" in text]
    if missed:
        print("selftest FAILED:", ", ".join(missed)); return 1
    print(f"selftest ok: {len(planted)} planted problems caught in staged and tree mode, {len(clean)} clean files passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", action="store_true"); ap.add_argument("--range")
    ap.add_argument("--tests", action="store_true"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warnings fail too")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    root = git("rev-parse", "--show-toplevel").strip()
    c = Check(root, "tree" if a.tree else "range" if a.range else "staged", a.range or "").run()
    for w in c.warn:
        print("warn:", w)
    for f in c.fail:
        print("FAIL:", f)
    bad = len(c.fail) + (len(c.warn) if a.strict else 0)
    if a.tests:
        bad += run_tests(root)
    print(f"nps5 check: {len(c.fail)} failure(s), {len(c.warn)} warning(s)" + ("" if bad else ": clean"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
