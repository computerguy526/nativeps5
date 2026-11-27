#!/usr/bin/env python3
"""nps5 pre-commit / pre-PR check: game data, secrets, personal paths, tests.

Usage:
  scripts/check.sh                 check staged changes (pre-commit mode)
  scripts/check.sh --tree          check every tracked file
  scripts/check.sh --range A..B    check files and added lines of every commit of a range
  scripts/check.sh --history       the same for every commit reachable from HEAD (before a first publication)
  scripts/check.sh --tests         also run the commands in .nps5-check-tests
  scripts/check.sh --selftest      plant known-bad files in a scratch repo; the check must catch each

Works in any git repository (this one and the component repositories). Exit 0 = clean,
1 = findings, 2 = usage or harness error. Content is read from git objects (the index for the staged and --tree
modes, each commit of a --range), never from the work tree, so what is checked is what a commit holds.
--range A..B checks every commit of the range against its first parent (a file added and deleted again inside
the range is still in the history, so it is still checked); a single revision X means X..HEAD.

Per-repo configuration (all optional, in the repository root):
  .nps5-check-allow    one glob per line: paths exempt from the file-type, binary, size, secret and home-path rules
                       (e.g. tests/fixtures/*.spv built from this repo's own GLSL). Say why in a comment.
                       A glob wide enough to exempt a dump (it matches eboot.bin somewhere)
                       is itself a failure, and a change to this file is reported for review.
  .nps5-check-tests    one shell command per line, run by --tests from the repository root.
  .nps5-check-local    NOT committed: extra forbidden regexes, one per line (your user name,
                       private remote names, machine names).

Game profiles (games/<title id>/) may hold only the files games/README.md lists. A pipeline list
(pipelines.kyplst: keys, hashes and state, no code) is accepted only when it is well-formed, no decompressed
section contains a shader binary or shader text, and pipelines.kyplst.verified records the no-game-code proof
(scripts/verify-pipeline-list.sh) for exactly those bytes. Shader binaries in any form are always rejected.

What the content rules catch: known file types and magic numbers (also under a harmless name), any binary file
that is not allowlisted, archives and compressed data, encoded blobs in text (long base64 / hex runs, git binary
patches), symbolic links that leave the repository, and third-party folders (3rdparty/, vendor/, ...), which skip
the text rules only. Limits, stated plainly: text that was encrypted or re-encoded to look like prose, and the
variable-length fields of a pipeline list, cannot be proven free of game data by inspection; for the list that
is what the .verified proof against the game is for, and for the rest it is review.
"""
import argparse, fnmatch, hashlib, os, posixpath, re, struct, subprocess, sys, tempfile

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
BAD_PATH_TITLE = BAD_PATH[3]
# Third-party folders (SKIP_DIRS) skip the text rules (their authors' paths and addresses) and the generic binary and
# size rules, never the game-data rules: a dump does not become acceptable by sitting in vendor/.
GAME_EXT = {"self", "sprx", "prx", "pkg", "pfs", "sfo", "ucas", "utoc", "pak", "psarc", "at9", "bik", "gnf",
            "metallib", "air", "journal", "known", "profdata", "profraw", "gputrace", "kyplst"}
BAD_PATH_GAME = [BAD_PATH[1], BAD_PATH[2], BAD_PATH[3], BAD_PATH[7]]
READ_CAP = 8 << 20          # bytes of one object held in memory (above MAX_BYTES and KYPLST_MAX_BYTES)
# An .nps5-check-allow glob that matches one of these exempts dumps, not fixtures.
ALLOW_PROBES = ("eboot.bin", "x/eboot.bin", "games/PPSA00000/eboot.bin", "a/b/c.self", "dump/sce_sys/param.json")
# Encoded binaries in a text file: lines of base64 / hex characters only. Hash lists stay far below the limit per file
# because their lines carry names and separators.
BINARY_PATCH = re.compile(r"^GIT binary patch\n(literal|delta) \d+", re.M)
ENCODED_LINE, ENCODED_LINE_MIN, ENCODED_MAX = re.compile(r"[A-Za-z0-9+/=_-]+"), 60, 8192
# A game profile folder, games/<title id>/, may hold exactly these files (games/README.md). Everything else that
# carries a title ID in its path is treated as a dump.
PROFILE_RE = re.compile(r"^games/((?:PPSA|CUSA|PCAS|PLAS)\d{5})/([^/]+)$")
PROFILE_FILES = {"README.md", "game.tsv", "patches.py", "bench.tsv", "validated.tsv",
                 "pipelines.kyplst", "pipelines.kyplst.verified", "pipelines.contrib.tsv", "scenes.tsv", "smoke.tsv",
                 "metal-shaders.tsv", "metal-shaders.tsv.verified", "metal-gfx-pairs.tsv", "metal-gfx-pairs.tsv.verified", "fast-math-deny.txt",
                 "metal-guests.tsv", "metal-digests.tsv", "override-guard.baseline", "promotions.tsv",
                 "keyguard-account.tsv", "keyguard.record"}
# keyguard (scripts/nps5lib/keyguard.py): games/<id>/keyguard.record is the release gate's PASS "every shipped Metal row is reachable by the key
# this stack produces", bound to the Kyty commit of components.lock, kosmickrisp/EXPECTED_TREE and the sha256 of the lists. A tree whose
# record names another Kyty, KosmicKrisp tree or list is refused (the 2026-11-27 lesson: a code-generation change silently de-keyed 116 rows);
# a title with Metal rows and no record is a warning. keyguard-account.tsv = the reasoned exceptions (hashes and words).
KEYGUARD_FILES = ("metal-shaders.tsv", "metal-gfx-pairs.tsv", "pipelines.kyplst", "keyguard-account.tsv", "keyguard.record")
KEYGUARD_PINS = ("components.lock", "kosmickrisp/EXPECTED_TREE")
KEYGUARD_ACCOUNT_RE = re.compile(rb"^(?:gfx\t[0-9a-f]{16}/[0-9a-f]{16}|cs\t[0-9a-f]{16})\t(?:\*|g\d{1,3}(?:@os\d{1,3})?)\t(?:moved|nopipe|noprog)\t[^\t;{}]{16,240}$")
KEYGUARD_RECORD_RE = re.compile(rb"^(?:result=(?:PASS|FAIL)|(?:kyty|kk_tree)=[0-9a-f]{40}|(?:kyty_codegen|[a-z_]+_sha256)=[0-9a-f]{0,64}|rows=[A-Za-z+:0-9 ]{0,200}"
                                rb"|date=\d{4}-\d\d-\d\d|(?:note|carried)=[^;{}]{0,200})$")


def check_keyguard_text(name, data):
    """-> reasons a games/<id>/keyguard-account.tsv or keyguard.record is refused: short text of hashes, counts and reason words only."""
    if b"\0" in data or len(data) > (64 << 10):
        return ["a keyguard file is a short text file"]
    rx, fmt = (KEYGUARD_ACCOUNT_RE, b"format\tnps5-keyguard-account\t1") if name == "keyguard-account.tsv" else (KEYGUARD_RECORD_RE, None)
    bad = []
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line or rx.match(line) or line == fmt or (line.startswith(b"#") and len(line) <= 200 and not re.search(rb"[;{}]", line)):
            continue
        bad.append("line %d is not a %s line or a short comment" % (n, "`gfx|cs key class moved|nopipe|noprog reason`" if fmt else "record `key=value`"))
        if len(bad) >= 3:
            break
    return bad


def keyguard_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("nps5_keyguard", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nps5lib", "keyguard.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def check_keyguard_record(game, sibling):
    """-> (failures, warnings) for games/<game>: is the committed keyguard record this tree's? sibling(path) -> blob bytes (b'' = absent)."""
    g = "games/%s/" % game
    sha = lambda p: hashlib.sha256(sibling(g + p)).hexdigest() if sibling(g + p) else ""
    rows = any(l.startswith(b"gfx\t") for l in sibling(g + "metal-gfx-pairs.tsv").split(b"\n")) or \
        any(re.match(rb"^[0-9a-f]{32}\t", l) for l in sibling(g + "metal-shaders.tsv").split(b"\n"))
    record = sibling(g + "keyguard.record")
    if not record:
        return [], (["%skeyguard.record: this title ships Metal rows and has no keyguard record (rehearse.sh r7: every row must be reachable by today's key)" % g]
                    if rows else [])
    kg = keyguard_module()
    bad = kg.record_problems(record.decode(errors="replace"), sibling("components.lock").decode(errors="replace"), sibling("kosmickrisp/EXPECTED_TREE").decode(errors="replace"),
                             dict(gfx_sha256=sha("metal-gfx-pairs.tsv"), cs_sha256=sha("metal-shaders.tsv"), kyplst_sha256=sha("pipelines.kyplst")), sha("keyguard-account.tsv"))
    return ["%skeyguard.record: %s" % (g, b) for b in bad], []
# games/<title id>/metal-shaders.tsv (scripts/nps5lib/metal.py): keys only. Comment lines, the three header lines, then rows of
# program digest, specialization key, KosmicKrisp MSL key, sha256 of our MSL, local size, evidence tag: hashes, never shader text.
# v1.2.1 (keys format 2, pairs format 3): per GPU class rows end with five short words: emitter mode, GPU class, near-exact rule,
# verdict, B/A (scripts/nps5lib/metal.py); a `kk` row (KosmicKrisp's shader on that class) has '-' for its text hashes.
METAL_CLASS_COLS = (rb"\t(?:base|kk|[a-z][a-z0-9_]{0,15}(?:=[0-9a-z]{1,8})?(?:\+[a-z][a-z0-9_]{0,15}(?:=[0-9a-z]{1,8})?){0,7})"
                    rb"\t(?:\*|g\d{2}[a-z]?(?:@os\d{2})?|@os\d{2})\t[a-z0-9][a-z0-9:=_.,-]{0,47}\t(?:faster|shipped|slower)\t(?:-|\d\.\d{1,4})$")
# Review L4: the class columns and '-' hashes are accepted ONLY in a per-class list (keys format 2 / pairs format 3), and are
# required there; a v1.2-format list keeps the v1.2 row form (else it would pass here and fail every install).
METAL_ROW_RE = re.compile(rb"^[0-9a-f]{32}\t[0-9a-f]{16}\t[0-9a-f]{16}\t[0-9a-f]{64}\t\d{1,4}x\d{1,4}x\d{1,4}\t[a-z0-9_,]{0,32}$")
METAL_ROW2_RE = re.compile(rb"^[0-9a-f]{32}\t[0-9a-f]{16}\t[0-9a-f]{16}\t(?:[0-9a-f]{64}|-)\t\d{1,4}x\d{1,4}x\d{1,4}\t[a-z0-9_,.+-]{0,48}" + METAL_CLASS_COLS)
# games/<title id>/metal-gfx-pairs.tsv: the same for (vertex, fragment) pairs. Hashes plus three short number fields (descriptor
# offsets binding:offset, vertex attributes loc:binding:format, colour mask) that the emitter needs; no shader text.
# Format 2 (v1.1.1): a spec key may carry the record's push-data cursor (:n), and a blend column (per colour attachment
# en:sc:dc:co:sa:da:ao:wm:class:components, ';'-joined: numbers and one class letter) comes before the evidence.
_GFX_ROW = (rb"^gfx\t[0-9a-f]{32}\t[0-9a-f]{16}(?::\d{1,4})?\t(?:[0-9a-f]{32}|-)\t(?:[0-9a-f]{16}(?::\d{1,4})?|-)\t[0-9a-f]{16}\t[0-9a-f]{16}\t%s\t%s"
            rb"\t(?:mipstats|twin|mesh|mesh-twin)\t[0-9:,]{0,400}\t[0-9:,]{0,200}\t\d{1,3}(?:\t(?:\d{1,3}:){8}[fusix]:\d(?:;(?:\d{1,3}:){8}[fusix]:\d){0,7}|\t)?\t[a-z0-9_+,.-]{0,48}")
METAL_GFX_ROW_RE = re.compile(_GFX_ROW % (rb"[0-9a-f]{64}", rb"[0-9a-f]{64}") + rb"$")
METAL_GFX_ROW3_RE = re.compile(_GFX_ROW % (rb"(?:[0-9a-f]{64}|-)", rb"(?:[0-9a-f]{64}|-)") + METAL_CLASS_COLS)
METAL_GFX_OK_LINE = b"result=verify: hashes and layout numbers only, no shader text"


REPRO_RE = re.compile(rb"^reproduced=(\d+) of (\d+)$")


def text_rows(data, gfx):
    """Rows of a keys/pairs list that carry a text hash (every row except the `kk` ones of a per-class list)."""
    n = 0
    for line in data.split(b"\n"):
        f = line.split(b"\t")
        if (gfx and f[0] == b"gfx" and len(f) > 8 and f[7] != b"-") or (not gfx and len(f) > 5 and len(f[0]) == 32 and f[3] != b"-"):
            n += 1
    return n


def per_class(data):
    return re.search(rb"^format\tnps5-metal-(?:keys\t2|gfx-pairs\t3)$", data, re.M) is not None


def check_gfx_record(data, record, gfx=True):
    """A metal-gfx-pairs.tsv with pair rows (and, from v1.2.1, any per-class list: keys format 2 / pairs format 3) needs its .verified
    record (proof record, as pipelines.kyplst.verified): the list's sha256 and the result line. A per-class list's record also needs
    `reproduced=N of N` with N = its rows with a text: the public kyty_msl_dump regenerated EVERY row's text from its mode
    (`nps5 cache metal <id> --reproduce` writes the line). -> reasons."""
    name = "metal-gfx-pairs.tsv" if gfx else "metal-shaders.tsv"
    if not (gfx and any(line.startswith(b"gfx\t") for line in data.split(b"\n"))) and not per_class(data):
        return []
    if not record:
        return ["a %s list with rows needs its %s.verified record" % ("graphics pairs" if gfx else "per-class keys", name)]
    lines = record.split(b"\n")
    if ("sha256=" + hashlib.sha256(data).hexdigest()).encode() not in lines:
        return ["%s.verified is for another version of the list (sha256 differs)" % name]
    if METAL_GFX_OK_LINE not in lines:
        return ["%s.verified lacks the result line" % name]
    if per_class(data):
        m = next((REPRO_RE.match(l) for l in lines if REPRO_RE.match(l)), None)
        n = text_rows(data, gfx)
        if not m or int(m.group(1)) != n or int(m.group(2)) != n:
            return ["%s.verified must record reproduced=%d of %d (every row's text regenerated by the public generator)" % (name, n, n)]
    return []


METAL_HEAD_RE = re.compile(rb"^(format\tnps5-metal-(?:keys|gfx-pairs)\t\d+|title\t(?:PPSA|CUSA|PCAS|PLAS)\d{5}\t[0-9.]{1,16}|emitter\t[0-9a-f]{7,40}(?:\tv\d+\.\d+(?:\.\d+)?(?:-dev)?)?)$")


def check_metal_keys(data, gfx=False):
    """-> reasons a games/<id>/metal-shaders.tsv is refused: anything but comments, header lines and key rows (hashes)."""
    if b"\0" in data or len(data) > (1 << 20):
        return ["a Metal keys list is a short text file"]
    bad = []
    pc = per_class(data)
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line:
            continue
        if line.startswith(b"#"):          # prose comments only: nothing that reads like shader source
            if len(line) <= 200 and not re.match(rb"#\s*(include|pragma|define|if|endif)\b", line) and not re.search(rb"[;{}]|\[\[", line):
                continue
            bad.append("line %d is a comment that looks like shader source" % n)
            continue
        rx = (METAL_GFX_ROW3_RE if gfx else METAL_ROW2_RE) if pc else (METAL_GFX_ROW_RE if gfx else METAL_ROW_RE)
        if not (rx.match(line) or METAL_HEAD_RE.match(line)):
            bad.append("line %d is not a key row (hashes only) or a header line" % n)
            if len(bad) >= 3:
                break
    return bad
# games/<title id>/fast-math-deny.txt (KYTY_FAST_MATH_DENY, v1.1.2): comment lines and 64-bit guest shader hashes, one per line.
DENY_ROW_RE = re.compile(rb"^[0-9a-f]{16}$")


def check_deny_list(data):
    """-> reasons a games/<id>/fast-math-deny.txt is refused: anything but short prose comments and 16-hex-digit hash lines."""
    if b"\0" in data or len(data) > (64 << 10):
        return ["a fast-math deny list is a short text file"]
    bad = []
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line or DENY_ROW_RE.match(line) or (line.startswith(b"#") and len(line) <= 200 and not re.search(rb"[;{}]", line)):
            continue
        bad.append("line %d is not a guest shader hash (16 hex digits) or a comment" % n)
        if len(bad) >= 3:
            break
    return bad


# games/<title id>/metal-guests.tsv (scripts/nps5lib/ovguard.py): guest shader hash of each Metal override key. Hashes only.
GUESTS_ROW_RE = re.compile(rb"^(?:cs|fs|vs)\t[0-9a-f]{16}\t[0-9a-f]{16}$")


def check_guests_list(data):
    """-> reasons a games/<id>/metal-guests.tsv is refused: anything but the format row, short comments and `cs|fs kk guest` hash rows."""
    if b"\0" in data or len(data) > (256 << 10):
        return ["a guest-hash sidecar is a short text file"]
    bad = []
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line or GUESTS_ROW_RE.match(line) or line == b"format\tnps5-metal-guests\t1" or \
                (line.startswith(b"#") and len(line) <= 200 and not re.search(rb"[;{}]", line)):
            continue
        bad.append("line %d is not a `cs|fs|vs <kk key> <guest hash>` row, the format row or a comment" % n)
        if len(bad) >= 3:
            break
    return bad


# games/<title id>/metal-digests.tsv (scripts/nps5lib/metal.py read_digest_aliases): old program digest -> new digest. Hashes only.
DIGESTS_ROW_RE = re.compile(rb"^alias\t[0-9a-f]{32}\t[0-9a-f]{32}(?:\t[0-9a-f]{16})?$")


def check_digests_list(data):
    """-> reasons a games/<id>/metal-digests.tsv is refused: anything but the format row, short comments and `alias old new [guest]` hash rows."""
    if b"\0" in data or len(data) > (64 << 10):
        return ["a digest-alias table is a short text file"]
    bad = []
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line or DIGESTS_ROW_RE.match(line) or line == b"format\tnps5-metal-digests\t1" or \
                (line.startswith(b"#") and len(line) <= 400 and not re.search(rb"[;{}]", line)):
            continue
        bad.append("line %d is not an `alias <old digest> <new digest> [guest hash]` row, the format row or a comment" % n)
        if len(bad) >= 3:
            break
    return bad


# games/<title id>/promotions.tsv (scripts/nps5lib/profdrift.py): validated / rejected words with verdict, evidence names, status. No paths, no code.
PROMO_ROW_RE = re.compile(rb"^PPSA\d{5}\t[^\t]{3,200}\t(?:KEEP|NEUTRAL|LOSS)\t[^\t]{3,300}\t\d{4}-\d{2}-\d{2}\t(?:pending|rejected|optin|landed [0-9a-f]{7,40})\t[^\t]{1,40}(?:\t[^\t]{0,300})?$")


def check_promotions_list(data):
    """-> reasons a games/<id>/promotions.tsv is refused: anything but the format row, short comments and register rows."""
    if b"\0" in data or len(data) > (128 << 10):
        return ["a promotion register is a short text file"]
    bad = []
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line or line == b"format\tnps5-promotions\t1" or (line.startswith(b"#") and len(line) <= 300 and not re.search(rb"[{}]|/Users/|~/", line)):
            continue
        if PROMO_ROW_RE.match(line) and not re.search(rb"/Users/|~/|[{}]", line):
            continue
        bad.append("line %d is not a register row (title, word=value, verdict, evidence, date, status, owner), the format row or a comment" % n)
        if len(bad) >= 3:
            break
    return bad


# games/<title id>/override-guard.baseline (scripts/nps5lib/ovguard.py): shrink-only accepted findings, `cs|fs|vs:<kk key>` or `word:<KYTY_NAME>` + a reason. No code.
BASELINE_ROW_RE = re.compile(rb"^(?:(?:cs|fs|vs):[0-9a-f]{16}|word:KYTY_[A-Z0-9_]+) [^{}\[\]]{3,300}$")


def check_baseline_list(data):
    """-> reasons a games/<id>/override-guard.baseline is refused: anything but short comments and `token reason` rows."""
    if b"\0" in data or len(data) > (64 << 10):
        return ["an override baseline is a short text file"]
    bad = []
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line or BASELINE_ROW_RE.match(line) or (line.startswith(b"#") and len(line) <= 300 and not re.search(rb"[{}]", line)):
            continue
        bad.append("line %d is not a `<cs|fs|vs>:<kk key>|word:<NAME> <reason>` row or a comment" % n)
        if len(bad) >= 3:
            break
    return bad


# shared shader lists (nps5 cache share): games/<title id>/contrib/<date>-<8 hex>.kyplst with its .verified record
CONTRIB_RE = re.compile(r"^games/((?:PPSA|CUSA|PCAS|PLAS)\d{5})/contrib/\d{4}-\d{2}-\d{2}-[0-9a-f]{8}\.kyplst(\.verified)?$")
# pad scripts (input timings only): games/<title id>/pad/<name>.vpad
PROFILE_PAD_RE = re.compile(r"^games/(?:PPSA|CUSA|PCAS|PLAS)\d{5}/pad/[A-Za-z0-9_.-]+\.vpad$")
KYPLST_MAGIC = b"KYPLST1\0"
KYPLST_SECTIONS = {1, 2, 3, 4, 5, 6}   # info, levels, shaders, permutations, pipelines, files (the `<runtime>` entry of a UE5 list) (pipelineList.h in the kyty fork)
KYPLST_MAX_BYTES, KYPLST_MAX_RAW = 4 << 20, 64 << 20   # the committed list is 70 kB; a section may not unpack to more than 64 MiB
KYPLST_OK_LINE = "verify: no shader code in the list"
# Things that must not occur inside a decompressed list section: shader binaries and shader text in any form.
KYPLST_FORBIDDEN = [
    (b"\x03\x02\x23\x07", "SPIR-V"), (b"MTLB", "Metal library"), (b"BC\xc0\xde", "LLVM bitcode"),
    (b"\xde\xc0\x17\x0b", "LLVM bitcode wrapper"), (b"\x7fELF", "ELF"), (b"\x54\x14\xf5\xee", "PS5 SELF"),
    (b"\x4f\x15\x3d\x1d", "PS4 SELF"), (b"metal_stdlib", "MSL text"), (b"OrbShdr", "PS shader binary"),
    (b"Shdr", "PS shader binary"), (b"target triple", "AIR text"), (b"OpCapability", "SPIR-V text"),
    (b"\x28\xb5\x2f\xfd", "nested zstd data"), (b"\xfd7zXZ\x00", "nested xz data"), (b"PK\x03\x04", "a nested zip archive"),
    (b"7z\xbc\xaf\x27\x1c", "a nested 7z archive"), (b"DXBC", "a DirectX shader container"),
]


def zstd_one_frame(data):
    """True when `data` is exactly one zstd frame: no second frame, no skippable frame (a hidden payload the
    decompressor would skip), no trailing bytes. Walks the frame header and the block headers only."""
    try:
        if data[:4] != b"\x28\xb5\x2f\xfd":
            return False
        fhd = data[4]
        single, off = (fhd >> 5) & 1, 5
        off += 0 if single else 1
        off += (0, 1, 2, 4)[fhd & 3]
        off += ((1 if single else 0), 2, 4, 8)[fhd >> 6]
        while True:
            h = data[off] | data[off + 1] << 8 | data[off + 2] << 16
            off += 3
            kind, size = (h >> 1) & 3, h >> 3
            if kind == 3:
                return False
            off += 1 if kind == 1 else size
            if h & 1:
                break
        off += 4 if fhd & 4 else 0
        return off == len(data)
    except IndexError:
        return False


def zstd_decompress(data, limit):
    """The decompressed bytes, at most `limit` + 1 of them (a larger output is a bomb: the caller compares sizes).
    None when the data cannot be decompressed here."""
    try:
        from compression import zstd          # Python 3.14+
        try:
            return zstd.ZstdDecompressor().decompress(data, max_length=limit + 1)
        except zstd.ZstdError:
            return None
    except ImportError:
        pass
    try:
        p = subprocess.Popen(("zstd", "-d", "-c", "-q"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    import threading
    def feed():
        try:
            p.stdin.write(data); p.stdin.close()
        except OSError:
            pass
    t = threading.Thread(target=feed, daemon=True); t.start()
    out = p.stdout.read(limit + 1)
    over = len(out) > limit
    if over:
        p.kill()
    p.stdout.close(); rc = p.wait(); t.join(5)
    return out if over or rc == 0 else None


def check_kyplst(path, data, sidecar):
    """A pipeline list is accepted only as games/<title id>/pipelines.kyplst, well-formed, free of shader binaries
    and shader text in every decompressed section, and with a .verified record of the no-game-code proof
    (scripts/verify-pipeline-list.sh, which needs the game) for exactly these bytes. Returns a list of problems."""
    m = PROFILE_RE.match(path)
    if not m or m.group(2) != "pipelines.kyplst":
        m = CONTRIB_RE.match(path)
        if not m or m.group(2):
            return ["a pipeline list is only accepted as games/<title id>/pipelines.kyplst or games/<title id>/contrib/<date>-<hash>.kyplst"]
    if len(data) > KYPLST_MAX_BYTES:
        return [f"{len(data)} bytes (a pipeline list is keys and hashes: limit {KYPLST_MAX_BYTES})"]
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
        if sid not in KYPLST_SECTIONS or sid in seen or off + stored > len(data) or raw_size > KYPLST_MAX_RAW:
            return [f"section {sid}: unknown, repeated or out of bounds"]
        seen.add(sid)
        comp = data[off:off + stored]
        off += stored
        if not zstd_one_frame(comp):
            return [f"section {sid}: its stored bytes are not exactly one zstd frame (extra or skippable frames can hide data)"]
        raw = zstd_decompress(comp, raw_size)
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
    if 1 not in seen:
        bad.append("no info section (title ID unknown)")
    if off != len(data):
        bad.append(f"{len(data) - off} bytes after the last section")
    rec = dict(l.split("=", 1) for l in sidecar.decode(errors="replace").splitlines() if "=" in l and not l.startswith("#"))
    if not sidecar:
        bad.append("no pipelines.kyplst.verified next to it: run scripts/verify-pipeline-list.sh against your own copy of the game")
    elif rec.get("sha256") != hashlib.sha256(data).hexdigest():
        bad.append("pipelines.kyplst.verified is for other bytes: rerun scripts/verify-pipeline-list.sh")
    elif rec.get("result") != KYPLST_OK_LINE:
        bad.append("pipelines.kyplst.verified does not record a passed no-game-code proof")
    elif "runtime" in rec and not re.match(r"0 runtime shaders not covered\b", rec["runtime"]):
        bad.append("pipelines.kyplst.verified records runtime shaders the proof did not see the code of (every <runtime> shader must be covered by a --shaders journal)")
    return bad


MAGIC = [
    (b"\x7fELF", "ELF executable"), (b"\x4f\x15\x3d\x1d", "PS4 SELF"), (b"\x54\x14\xf5\xee", "PS5 SELF"),
    (b"\x7fCNT", "PKG"), (b"\x03\x02\x23\x07", "SPIR-V"), (b"MTLB", "Metal library"),
    (b"BC\xc0\xde", "LLVM bitcode"), (b"\xde\xc0\x17\x0b", "LLVM bitcode wrapper"),
    (b"BM", "BMP image"), (b"\x89PNG", "PNG image"), (b"\xff\xd8\xff", "JPEG image"),
    (b"\x28\xb5\x2f\xfd", "zstd data"), (b"\x1f\x8b", "gzip data"), (b"PK\x03\x04", "zip archive"),
    (b"\xfd7zXZ\x00", "xz data"), (b"BZh", "bzip2 data"), (b"7z\xbc\xaf\x27\x1c", "7z archive"), (b"\x04\x22\x4d\x18", "lz4 data"),
    (b"Rar!\x1a\x07", "rar archive"), (b"!<arch>\n", "ar archive / static library"), (b"\x7fSCE", "PS SELF"),
    (b"\xcf\xfa\xed\xfe", "Mach-O binary"), (b"\xca\xfe\xba\xbe", "Mach-O fat binary"),
    (b"\xff\x6c\x70\x72\x6f\x66", "LLVM profile data"), (b"\x81\x52\x66\x6f\x72\x70\x6c\xff", "LLVM profile data"),
]
GAME_MAGIC = [m for m in MAGIC if m[1] in ("PS4 SELF", "PS5 SELF", "PKG", "PS SELF", "Metal library")]
SECRETS = [
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}", r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"sk-ant-[A-Za-z0-9_-]{20,}", r"sk-[A-Za-z0-9]{40,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}", r"AIza[0-9A-Za-z_-]{35}", r"pplx-[A-Za-z0-9]{30,}",
    r"(?i)\b(api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*['\"][A-Za-z0-9/+_-]{20,}['\"]",
]
# /Users/<name> and /home/<name>; shared and placeholder names are fine.
# Also after a compiler flag (-I, -isystem directly before the path) and in a file:// URL, where the plain look-behind would miss it.
HOME_RE = re.compile(r"(?:(?<![\w/.$}>-])|(?<=file://)|(?<![\w-])-[A-Za-z]{1,10})"
                     r"(?P<p>(?:/Users|/home)/(?!Shared\b|runner\b|you\b|me\b|user\b|username\b|name\b|USER\b|bob\b|alice\b|ally\b|\$|<|\{|\*|\.\.\.)[A-Za-z0-9._-]+)")
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


def third_party(path):
    return path.startswith(SKIP_DIRS) or any("/" + d in path for d in SKIP_DIRS)


def home_hit(line):
    for rx in (HOME_RE, WINHOME_RE, VOLUME_RE):
        m = rx.search(line)
        if m:
            return m.group("p") if rx is HOME_RE else m.group(0)
    return None


def encoded_bytes(text):
    """Bytes of the text that sit in lines made of base64 or hex characters only (an encoded file, not prose or code)."""
    return sum(len(l) for l in text.split("\n") if len(l) >= ENCODED_LINE_MIN and ENCODED_LINE.fullmatch(l.strip()) and len(set(l)) >= 10)


class Blobs:
    """Object contents from one `git cat-file --batch` process. Only the first READ_CAP bytes of an object are kept
    (the size is always the real one), so a huge file costs no memory."""
    def __init__(self, root):
        self.p = subprocess.Popen(("git", "cat-file", "--batch"), cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def get(self, sha):
        """-> (size, first bytes); (None, b"") when git has no such object."""
        self.p.stdin.write(sha.encode() + b"\n"); self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) != 3:
            return None, b""
        size, left, keep = int(head[2]), int(head[2]), []
        while left:
            b = self.p.stdout.read(min(left, 1 << 20))
            if not b:
                sys.stderr.write("nps5 check: git cat-file ended early\n"); sys.exit(2)
            left -= len(b)
            if sum(map(len, keep)) < READ_CAP:
                keep.append(b)
        self.p.stdout.read(1)
        return size, b"".join(keep)[:READ_CAP]

    def close(self):
        self.p.stdin.close(); self.p.stdout.close(); self.p.wait()


class Check:
    def __init__(self, root, mode, rng):
        self.root, self.mode, self.rng = root, mode, rng
        self.allow = read_list(root, ".nps5-check-allow")
        self.local = [re.compile(x) for x in read_list(root, ".nps5-check-local")]
        self.fail, self.warn = [], []

    # ---- what to check: [(label, entries {path: (mode, sha)}, changed paths, diff args for added lines or None)]
    def _raw(self, *args):
        """{path: (mode, sha)} of `git diff --raw -z` style output (added, copied, modified, renamed, type-changed)."""
        out, toks = {}, git(*args, "--raw", "-z", "--no-renames", "--no-abbrev", "--diff-filter=ACMRT", cwd=self.root).split("\0")
        for i in range(0, len(toks) - 1, 2):
            f = toks[i].split()
            if len(f) >= 5 and f[0].startswith(":"):
                out[toks[i + 1]] = (f[1], f[3])
        return out

    def _index(self):
        out = {}
        for e in git("ls-files", "-s", "-z", cwd=self.root).split("\0"):
            if e:
                meta, path = e.split("\t", 1)
                mode, sha, stage = meta.split()
                out[path] = (mode, sha)
        return out

    def units(self):
        if self.mode == "tree":
            idx = self._index()
            return [("", idx, idx, None)]
        if self.mode == "staged":
            return [("", self._index(), self._raw("diff", "--cached"), ("diff", "--cached"))]
        rng = "HEAD" if self.mode == "history" else self.rng if ".." in self.rng else self.rng + "..HEAD"
        r = subprocess.run(("git", "rev-list", "--reverse", "--parents", rng, "--"), cwd=self.root, capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write("nps5 check: bad --range %r: %s" % (self.rng, r.stderr)); sys.exit(2)
        empty = git("hash-object", "-t", "tree", os.devnull, cwd=self.root).strip()
        out = []
        for line in r.stdout.splitlines():
            c, *parents = line.split()
            base = parents[0] if parents else empty
            out.append((c[:10] + ":", None, self._raw("diff-tree", "-r", base, c), ("diff", base, c), c))
        return out

    def added_lines(self, path, data, diff):
        """Text to scan for secrets and personal data: added lines in diff modes, everything in tree mode."""
        if diff is None:
            return list(enumerate(data.decode(errors="replace").splitlines(), 1))
        res, n = [], 0
        for l in git(*diff, "-U0", "--no-color", "--", path, cwd=self.root, check=False).splitlines():
            m = re.match(r"@@ -\S+ \+(\d+)", l)
            if m:
                n = int(m.group(1)); continue
            if l.startswith("+") and not l.startswith("+++"):
                res.append((n, l[1:])); n += 1
        return res

    def run(self):
        for g in self.allow:
            if any(fnmatch.fnmatch(p, g) for p in ALLOW_PROBES):
                self.fail.append(f".nps5-check-allow: glob {g!r} is wide enough to exempt a game dump; name the fixture folder and type")
        blobs = Blobs(self.root)
        try:
            for unit in self.units():
                label, entries, changed, diff = unit[:4]
                def sibling(path, commit=unit[4] if len(unit) > 4 else None):
                    if entries is not None:
                        sha = entries.get(path, (None, None))[1]
                    else:
                        sha = git("rev-parse", "-q", "--verify", f"{commit}:{path}", cwd=self.root, check=False).strip()
                    return blobs.get(sha)[1] if sha else b""
                for path in sorted(changed):
                    mode, sha = changed[path]
                    if mode == "160000":
                        continue    # a submodule pointer: no content here; the component repository runs this check itself
                    size, data = blobs.get(sha)
                    if size is None:
                        self.fail.append(f"{label}{path}: cannot read the object from git; not accepted unchecked"); continue
                    self.one(label, path, mode, size, data, sibling, diff)
                # keyguard: a change of the Kyty pin, the KosmicKrisp tree, a Metal list or the accounting needs the record of that very state
                ids = {m.group(1) for m in map(PROFILE_RE.match, changed) if m and m.group(2) in KEYGUARD_FILES}
                if any(p in changed for p in KEYGUARD_PINS):
                    names = entries.keys() if entries is not None else git("ls-tree", "-r", "--name-only", unit[4], "--", "games", cwd=self.root, check=False).splitlines()
                    ids |= {m.group(1) for m in map(PROFILE_RE.match, names) if m and m.group(2) == "keyguard.record"}
                for game in sorted(ids):
                    bad, warn = check_keyguard_record(game, sibling)
                    self.fail += [label + b for b in bad]; self.warn += [label + w for w in warn]
        finally:
            blobs.close()
        return self

    def one(self, label, path, mode, size, data, sibling, diff):
        F = lambda msg: self.fail.append(f"{label}{path}{msg}")
        W = lambda msg: self.warn.append(f"{label}{path}{msg}")
        if path in (".nps5-check-allow", ".nps5-check-tests") and diff is not None:
            W(": changes what the check exempts or runs: a reviewer must read this change")
        if mode == "120000":
            target = data.decode(errors="replace")
            if target.startswith("/") or posixpath.normpath(posixpath.join(posixpath.dirname(path), target)).startswith(".."):
                F(f": symbolic link to {target!r} leaves the repository (its target is not checked and may be personal)")
            return
        exempt = allowed(path, self.allow)
        vendor = third_party(path)
        low = path.lower()
        ext = low.rsplit(".", 1)[-1] if "." in os.path.basename(path) else ""
        profile = PROFILE_RE.match(path)
        if CONTRIB_RE.match(path) and not ext == "kyplst":     # the .verified record next to a shared list: text only
            if not data.startswith(b"#") or b"\0" in data or len(data) > 4096:
                F(": a .verified record is a short text file")
            return
        if profile and profile.group(2) not in PROFILE_FILES:
            F(": not a profile file (games/README.md lists them); game-derived data stays out")
        if profile and profile.group(2) in ("metal-shaders.tsv", "metal-gfx-pairs.tsv"):
            for why in check_metal_keys(data, profile.group(2) == "metal-gfx-pairs.tsv"):
                F(f": {why}")
            for why in check_gfx_record(data, sibling(path + ".verified"), profile.group(2) == "metal-gfx-pairs.tsv"):
                F(f": {why}")
            return
        if profile and profile.group(2) == "fast-math-deny.txt":
            for why in check_deny_list(data):
                F(f": {why}")
            return
        if profile and profile.group(2) == "metal-digests.tsv":
            for why in check_digests_list(data):
                F(f": {why}")
            return
        if profile and profile.group(2) == "override-guard.baseline":
            for why in check_baseline_list(data):
                F(f": {why}")
            return
        if profile and profile.group(2) == "promotions.tsv":
            for why in check_promotions_list(data):
                F(f": {why}")
            return
        if profile and profile.group(2) in ("keyguard-account.tsv", "keyguard.record"):
            for why in check_keyguard_text(profile.group(2), data):
                F(f": {why}")
            return
        if profile and profile.group(2) == "metal-guests.tsv":
            for why in check_guests_list(data):
                F(f": {why}")
            return
        if profile and profile.group(2) in ("metal-gfx-pairs.tsv.verified", "metal-shaders.tsv.verified"):
            if not data.startswith(b"#") or b"\0" in data or len(data) > 4096:
                F(": a .verified record is a short text file")
            return
        if ext == "kyplst":
            for why in check_kyplst(path, data, sibling(path + ".verified")):
                F(f": {why}")
            return
        if not exempt:
            if ext in (GAME_EXT if vendor else BAD_EXT):
                F(f": file type .{ext} is game data, a run artefact or a build output")
            for rx in (BAD_PATH_GAME if vendor else BAD_PATH):
                if rx == BAD_PATH_TITLE and ((profile and profile.group(2) in PROFILE_FILES) or PROFILE_PAD_RE.match(path)):
                    continue
                if re.search(rx, path, re.I) and not (rx == BAD_PATH[0] and ext in SOURCE_EXT):
                    F(f": path looks like a dump, cache, capture or run output ({rx})"); break
            hit = False
            for mg, what in (GAME_MAGIC if vendor else MAGIC):
                if data.startswith(mg):
                    F(f": content is {what}"); hit = True; break
            if not hit and not vendor and data[257:262] == b"ustar":
                F(": content is a tar archive"); hit = True
            if not vendor:
                if size > MAX_BYTES:
                    F(f": {size} bytes (limit {MAX_BYTES}); large files are usually dumps")
                if not hit and b"\0" in data:
                    F(": binary content under a text name or unknown type; a source-built fixture goes into .nps5-check-allow with its source")
        if b"\0" in data or vendor:
            return  # binary, or third-party source (its own authors' paths and addresses): the content rules above applied
        text = data.decode(errors="replace")
        if not exempt:
            if BINARY_PATCH.search(text):
                F(": holds a git binary patch (an encoded binary file)")
            enc = encoded_bytes(text)
            if enc > ENCODED_MAX:
                F(f": {enc} bytes of base64 / hex lines; an encoded file is checked as the binary it is, not accepted as text")
        for n, line in self.added_lines(path, data, diff):
            # long (minified, generated) lines are scanned in overlapping pieces: bounded regex time, nothing skipped
            for piece in ([line] if len(line) <= 4000 else [line[i:i + 4000] for i in range(0, len(line), 3500)]):
                self.line(label, path, n, piece, exempt)

    def line(self, label, path, n, line, exempt):
        for rx in SECRETS:
            if re.search(rx, line) and not exempt:
                self.fail.append(f"{label}{path}:{n}: looks like a secret ({rx[:28]}...)")
        if not exempt:
            m = home_hit(line)
            if m:
                self.fail.append(f"{label}{path}:{n}: personal absolute path {m!r}: use $NPS5_ROOT or a relative path")
        for m in EMAIL_RE.finditer(line):
            if not EMAIL_OK.search(m.group(0)) and not exempt and os.path.basename(path) not in ("NOTICE", "LICENSE", "AUTHORS"):
                self.warn.append(f"{label}{path}:{n}: e-mail address {m.group(0)}: only with its owner's consent")
        for rx in self.local:
            if rx.search(line):
                self.fail.append(f"{label}{path}:{n}: matches a .nps5-check-local rule ({rx.pattern})")
        for rx, why in MACHINE:
            if re.search(rx, line):
                self.warn.append(f"{label}{path}:{n}: {why}")


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
        "vendor/lib/eboot.bin": b"\x54\x14\xf5\xeejunk", "docs/packed.md": b"\xfd7zXZ\x00" + b"\0" * 16,     # a third-party folder, a renamed archive
        "games/PPSA00001/metal-shaders.tsv": b"format\tnps5-metal-keys\t1\n#include <metal_stdlib>\n",   # MSL in a keys list
        "games/PPSA00001/metal-gfx-pairs.tsv": b"format\tnps5-metal-gfx-pairs\t1\nfloat4 main0(float4 v) { return v; }\n",   # MSL in a pairs list
        "late.txt": b"text\n" * 4000 + b"\0\1\2", "flags.mk": b"CFLAGS=-I/Users/" + b"somebody/include\n",     # binary after a text start, a path behind a flag
    }
    clean = {"3rdparty/lib/a.c": b"// built in /Users/" + b"author/src\nint x;\n", "ok.sh": b"cd \"$NPS5_ROOT\" && ls /Users/Shared\n", "ok.md": b"mail nobody@example.com\n",
             "games/PPSA00000/game.tsv": b"title_id\tPPSA00000\n", "games/PPSA00000/README.md": b"# Example\n",
             "games/PPSA00000/metal-shaders.tsv": b"# keys\nformat\tnps5-metal-keys\t1\ntitle\tPPSA00000\t01.000.000\nemitter\tabcdef0\n" +
             b"0" * 32 + b"\t" + b"1" * 16 + b"\t" + b"2" * 16 + b"\t" + b"3" * 64 + b"\t64x1x1\texact\n",
             "games/PPSA00000/metal-gfx-pairs.tsv": b"# pairs\nformat\tnps5-metal-gfx-pairs\t1\ntitle\tPPSA00000\t01.000.000\n" + b"gfx\t" + b"0" * 32 + b"\t" + b"1" * 16 + b"\t" +
             b"2" * 32 + b"\t" + b"3" * 16 + b"\t" + b"4" * 16 + b"\t" + b"5" * 16 + b"\t" + b"6" * 64 + b"\t" + b"7" * 64 + b"\ttwin\t0:0,58:16\t0:0:109\t1\texact\n"}
    gfx2 = (b"# pairs v2\nformat\tnps5-metal-gfx-pairs\t2\ntitle\tPPSA00000\t01.000.000\nemitter\t" + b"a" * 40 + b"\tv1.1.1\ngfx\t" + b"0" * 32 + b"\t" +
            b"1" * 16 + b":5\t" + b"2" * 32 + b"\t" + b"3" * 16 + b":0\t" + b"4" * 16 + b"\t" + b"5" * 16 + b"\t" + b"6" * 64 + b"\t" + b"7" * 64 +
            b"\tmipstats\t0:0,58:16\t\t1\t1:1:6:0:1:0:0:7:f:4\tfuzz2+mutant+play2\n")
    clean["games/PPSA00002/metal-gfx-pairs.tsv"] = gfx2                     # format 2 (cursor, blend) with its proof record
    clean["games/PPSA00002/metal-gfx-pairs.tsv.verified"] = (b"# proof\nsha256=" + hashlib.sha256(gfx2).hexdigest().encode() + b"\n" +
                                                             METAL_GFX_OK_LINE + b"\n")
    g1 = clean["games/PPSA00000/metal-gfx-pairs.tsv"]
    clean["games/PPSA00000/metal-gfx-pairs.tsv.verified"] = b"# proof\nsha256=" + hashlib.sha256(g1).hexdigest().encode() + b"\n" + METAL_GFX_OK_LINE + b"\n"
    planted["games/PPSA00003/metal-gfx-pairs.tsv"] = gfx2                   # pair rows without the proof record
    clean["games/PPSA00000/fast-math-deny.txt"] = b"# guest hashes\n" + b"0123456789abcdef\n" * 3             # v1.1.2 deny list: hashes only
    planted["games/PPSA00005/keyguard.record"] = b"# keyguard\nresult=PASS\nkyty=" + b"1" * 40 + b"\nkk_tree=" + b"2" * 40 + b"\n"   # a record for another Kyty / KosmicKrisp / list
    planted["games/PPSA00006/keyguard-account.tsv"] = b"format\tnps5-keyguard-account\t1\nfloat4 main0(float4 v) { return v; }\n"   # code in an accounting file
    planted["games/PPSA00004/fast-math-deny.txt"] = b"0123456789abcdef\nfloat4 main0(float4 v) { return v; }\n"   # code in a deny list
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
    ap.add_argument("--tree", action="store_true"); ap.add_argument("--range"); ap.add_argument("--history", action="store_true")
    ap.add_argument("--tests", action="store_true"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warnings fail too")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    root = git("rev-parse", "--show-toplevel").strip()
    c = Check(root, "tree" if a.tree else "history" if a.history else "range" if a.range else "staged", a.range or "").run()
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
