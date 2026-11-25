#!/usr/bin/env python3
"""harvestall: the shipped pipeline list (games/<id>/pipelines.kyplst) must hold every shader permutation any session compiled, not only the ones
a journal recorded. Reads the program cache (programs.bin, KPRC), the shader journals and the list, and reports / gates the gap.

Why (v1.4, 2026-11-25): the harvest compared shader and pipeline JOURNALS only. Programs that reach programs.bin through the shader precompile /
compute prewarm path are never journaled, so 70 compute shaders (78 permutations; 38 of them other static variants of override-listed guest
hashes) and 5 mesh shaders sat in programs.bin and in no list: the harvest said "no new entries" and the player compiled them live.

Everything here reads HASHES, STATIC STATE and SPECIALIZATIONS only (the list holds no game code); programs.bin / journals are LOCAL files that carry
guest code and are never shipped. A list shader is identified like the emulator identifies a program source:
    (stage, guest hash, user-data count, code size, static state words, wave size, user-data base, plain flag).

Commands (python3 -I scripts/nps5lib/harvestall.py ...):
    coverage LIST [--programs F...] [--journals F...]      programs / journal sources and permutations the list lacks (exit 1 when any)
    gate GAMEDIR [--programs F...]                         ovguard-style: every override guest hash (metal-guests.tsv, fast-math-deny.txt) is a
                                                           shader of the right stage in the list; with --programs also every program source with
                                                           such a guest hash (all static variants) and every permutation of it is in the list
Exit: 0 ok, 1 gap, 2 usage / unreadable input."""
import os, re, struct, subprocess, sys

HEX16 = re.compile(r"^[0-9a-f]{16}$")
# ShaderType (graphics/shader/shader.h): Unknown 0, Vertex 1, Pixel 2, Fetch 3, Compute 4, Mesh 5
VERTEX, PIXEL, COMPUTE, MESH = 1, 2, 4, 5
STAGE_OF_KIND = {"cs": (COMPUTE,), "fs": (PIXEL,), "vs": (VERTEX, MESH)}
KPRC_MAGIC = 0x4352504B


class FormatError(Exception):
    pass


# ---- specialization layouts (tools/pipeline-list/exportBase.h) -------------------------------------------------------------
_IMG_INT21 = 8 * 4 + 2
_IMG = _IMG_INT21 + 2 + 4 + 1
_IMG_V13 = 9 * 4 + 2


def reads_current(b):
    """ReadsAsSpecialization(bytes, int21=false): this build's record layout."""
    n, pos = len(b), 0
    if n < 4:
        return False
    nb = struct.unpack_from("<I", b, 0)[0]
    pos = 4
    if nb > n - pos:
        return False
    for _ in range(nb):
        if n - pos < 13 or b[pos + 12] > 1:
            return False
        pos += 13
    if n - pos < 4:
        return False
    c = struct.unpack_from("<I", b, pos)[0]
    pos += 4
    if c > n - pos:
        return False
    for _ in range(c):
        if n - pos < 32:
            return False
        pos += 32
        for _f in range(4):
            if pos >= n or b[pos] > 1:
                return False
            pos += 1
        if n - pos < 4:
            return False
        pos += 4
        if pos >= n or b[pos] > 1:
            return False
        pos += 1
    return pos == n


def upgrade_spec(b):
    """The specialization in this build's layout (int21 and v1.3 records are upgraded like the emulator does), or None when it reads as no layout."""
    b = bytes(b)
    if reads_current(b):
        return b
    n = len(b)
    if n < 4:
        return None
    nb = struct.unpack_from("<I", b, 0)[0]
    pos = 4
    if nb > n - pos:
        return None
    for _ in range(nb):
        if n - pos < 13 or b[pos + 12] > 1:
            return None
        pos += 13
    if n - pos < 4:
        return None
    c = struct.unpack_from("<I", b, pos)[0]
    pos += 4
    rest = n - pos
    out = bytearray(b[:pos])
    if rest == c * _IMG_INT21:
        for i in range(c):
            rec = b[pos + i * _IMG_INT21: pos + (i + 1) * _IMG_INT21]
            if rec[32] > 1 or rec[33] > 1:
                return None
            out += rec + bytes(_IMG - _IMG_INT21)
    elif rest == c * _IMG_V13:
        for i in range(c):
            rec = b[pos + i * _IMG_V13: pos + (i + 1) * _IMG_V13]
            if struct.unpack_from("<I", rec, 32)[0] != 0xFFFFFFFF or rec[36] > 1 or rec[37] > 1:
                return None
            out += rec[:32] + rec[36:38] + bytes(_IMG - _IMG_INT21)
    else:
        return None
    return bytes(out) if reads_current(bytes(out)) else None


# ---- the list (pipelineList.h: sections, zstd) -------------------------------------------------------------------------------
class ListIndex:
    """shaders: [ident]; perms: {(ident, cursor, spec)}; idents: set."""
    def __init__(self, shaders=(), perms=()):
        self.shaders = list(shaders)
        self.idents = set(self.shaders)
        self.perms = set(perms)

    def hashes(self):
        out = {}
        for s in self.shaders:
            out.setdefault(s[1], set()).add(s[0])
        return out


def _sections(b):
    if b[:8] != b"KYPLST1\x00" and not b.startswith(b"KYPLST1"):
        raise FormatError("not a KYPLST1 list")
    cnt = struct.unpack_from("<I", b, 12)[0]
    pos, out = 16, {}
    for _ in range(cnt):
        sid, fl, st, raw, _h = struct.unpack_from("<IIQQQ", b, pos)
        pos += 32
        data = b[pos:pos + st]
        pos += st
        if fl & 1:
            r = subprocess.run(["zstd", "-dc"], input=data, capture_output=True)
            if r.returncode != 0:
                raise FormatError("zstd failed on section %d" % sid)
            data = r.stdout
        out[sid] = data
    return out


class _R:
    def __init__(s, b):
        s.b, s.p = b, 0

    def get(s, f):
        v = struct.unpack_from("<" + f, s.b, s.p)[0]
        s.p += struct.calcsize(f)
        return v

    def arr(s, f):
        n = s.get("I")
        v = struct.unpack_from("<%d%s" % (n, f), s.b, s.p)
        s.p += n * struct.calcsize(f)
        return list(v)

    def bytes_(s):
        n = s.get("I")
        v = s.b[s.p:s.p + n]
        s.p += n
        return v


def read_list(path):
    try:
        with open(path, "rb") as f:
            secs = _sections(f.read())
        r = _R(secs[3])
        shaders = []
        for _ in range(r.get("I")):
            r.get("I"); r.get("Q"); cs = r.get("I")
            [r.get("Q") for _ in range(3)]; r.get("Q"); r.get("I"); [r.get("Q") for _ in range(3)]
            stage = r.get("I"); r.get("B"); h = r.get("Q"); ud = r.get("I"); wave = r.get("I"); udb = r.get("I"); plain = r.get("B")
            statics = tuple(r.arr("I")); r.bytes_()
            shaders.append((stage, h, ud, cs, statics, wave, udb, plain))
        r = _R(secs[4])
        perms = set()
        for _ in range(r.get("I")):
            si, cur, spec = r.get("I"), r.get("I"), r.bytes_()
            perms.add((shaders[si], cur, spec))
    except (struct.error, KeyError, IndexError) as e:
        raise FormatError("%s: %s" % (path, e))
    return ListIndex(shaders, perms)


# ---- programs.bin (tools/msl-dump/kprcLoader.h) ------------------------------------------------------------------------------
def read_programs(path):
    """-> ({digest: ident}, [(digest, cursor, spec_bytes)]). A damaged record ends the read (records before it stay)."""
    with open(path, "rb") as f:
        d = f.read()
    if len(d) < 32:
        raise FormatError("%s: too short" % path)
    n = struct.unpack_from("<Q", d, 16)[0]
    off = 16 + 8 + n + 8
    srcs, perms = {}, []
    while off + 24 <= len(d):
        magic, kind, size = struct.unpack_from("<IIQ", d, off)
        if magic != KPRC_MAGIC or size > len(d) - off - 24:
            break
        p = off + 24
        dig = d[p:p + 16]
        q = p + 16
        try:
            if kind == 1:
                kl = struct.unpack_from("<I", d, q)[0]
                k0 = q + 4
                _lay, stg, h, ud, cs, ns = struct.unpack_from("<IIQIII", d, k0)
                w0 = k0 + 28
                ss = struct.unpack_from("<%dI" % ns, d, w0)
                wv, ub, pl = struct.unpack_from("<IIB", d, w0 + 4 * ns)
                srcs[dig] = (stg, h, ud, cs, tuple(ss), wv, ub, pl)
            else:
                cur, sl = struct.unpack_from("<II", d, q)
                perms.append((dig, cur, d[q + 8:q + 8 + sl]))
        except struct.error:
            break
        off = p + size
    return srcs, perms


def programs_view(path):
    """-> (set of idents, {(ident, cursor, spec) in this build's layout}, unreadable count)"""
    srcs, perms = read_programs(path)
    out, bad = set(), 0
    for dig, cur, spec in perms:
        if dig not in srcs:
            continue
        s2 = upgrade_spec(spec)
        if s2 is None:
            bad += 1
            continue
        out.add((srcs[dig], cur, s2))
    return set(srcs.values()), out, bad


# ---- shader journals (shaderPrecompile.cpp, KYSHJRN1) --------------------------------------------------------------------------
def read_journal(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[:8] != b"KYSHJRN1":
        raise FormatError("%s: not a shader journal" % path)
    il = struct.unpack_from("<I", d, 12)[0]
    pos = 16 + il + 8
    srcs, ents = [], []
    while len(d) - pos >= 24:
        magic, kind, size = struct.unpack_from("<IIQ", d, pos)
        if magic != 0x524A484B or size > len(d) - pos - 24:
            break
        p, o = d[pos + 24: pos + 24 + size], 0
        try:
            if kind in (1, 3):
                st, _k, h, ud, wv, ub, pl = struct.unpack_from("<IBQIIIB", p, 0)
                o = struct.calcsize("<IBQIIIB")
                ns = struct.unpack_from("<I", p, o)[0]; o += 4
                ss = struct.unpack_from("<%dI" % ns, p, o); o += 4 * ns
                nc = struct.unpack_from("<I", p, o)[0]
                srcs.append((st, h, ud, nc, tuple(ss), wv, ub, pl))
            elif kind == 2:
                si, cur, sl = struct.unpack_from("<III", p, 0)
                ents.append((si, cur, p[12:12 + sl]))
            else:
                break
        except struct.error:
            break
        pos += 24 + size
    view = set()
    for si, cur, spec in ents:
        s2 = upgrade_spec(spec)
        if s2 is not None and si < len(srcs):
            view.add((srcs[si], cur, s2))
    return set(srcs), view


# ---- coverage / gate -----------------------------------------------------------------------------------------------------------
def coverage(lst, sources):
    """sources: [(label, idents, perms)] -> (missing {ident: {labels}}, missing perms {(ident,cursor,spec): {labels}})."""
    ms, mp = {}, {}
    for label, idents, perms in sources:
        for i in idents:
            if i not in lst.idents:
                ms.setdefault(i, set()).add(label)
        for p in perms:
            if p not in lst.perms:
                mp.setdefault(p, set()).add(label)
    return ms, mp


def parse_guests(text):
    """metal-guests.tsv -> {(kind, guest hash int)}; fast-math-deny.txt lines are separate (parse_deny)."""
    out = set()
    for l in text.splitlines():
        f = l.split("\t")
        if len(f) >= 3 and f[0] in STAGE_OF_KIND and HEX16.match(f[2]):
            out.add((f[0], int(f[2], 16)))
    return out


def parse_deny(text):
    return {int(l.strip(), 16) for l in text.splitlines() if HEX16.match(l.strip())}


def override_gate(lst, guests, deny, program_views=()):
    """Errors (strings). guests: {(kind, hash)}, deny: {hash} (every kind), program_views: [(label, idents, perms)]."""
    errs = []
    by = lst.hashes()
    for kind, h in sorted(guests):
        if not set(STAGE_OF_KIND[kind]) & by.get(h, set()):
            errs.append("override guest %s %016x is not a %s shader of the list" % (kind, h, kind))
    for h in sorted(deny):
        if h not in by:
            errs.append("fast-math-deny guest %016x is not in the list" % h)
    wanted = {h for _, h in guests} | set(deny)
    for label, idents, perms in program_views:
        for i in sorted(idents):
            if i[1] in wanted and i not in lst.idents:
                errs.append("%s: override guest %016x (stage %d, %d static words, wave %d) has a program source the list lacks" % (label, i[1], i[0], len(i[4]), i[5]))
        for p in sorted(perms):
            if p[0][1] in wanted and p[0] in lst.idents and p not in lst.perms:
                errs.append("%s: override guest %016x has a permutation (cursor %d, %d spec bytes) the list lacks" % (label, p[0][1], p[1], len(p[2])))
    return errs


def _args(argv, flag):
    out, i = [], 0
    while i < len(argv):
        if argv[i] == flag:
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                out.append(argv[i]); i += 1
        else:
            i += 1
    return out


def _views(argv):
    views = []
    for p in _args(argv, "--programs"):
        ids, perms, bad = programs_view(p)
        views.append(("pb:" + os.path.basename(os.path.dirname(os.path.abspath(p))), ids, perms))
        if bad:
            print("harvestall: %s: %d permutations with an unreadable specialization" % (p, bad))
    for p in _args(argv, "--journals"):
        ids, perms = read_journal(p)
        views.append(("sj:" + os.path.basename(os.path.dirname(os.path.abspath(p))), ids, perms))
    return views


def cmd_coverage(argv):
    lst = read_list(argv[0])
    ms, mp = coverage(lst, _views(argv[1:]))
    kinds = {}
    for i in ms:
        kinds[i[0]] = kinds.get(i[0], 0) + 1
    print("harvestall: list %d shaders / %d permutations; missing %d shaders (by stage %s), %d permutations" % (
        len(lst.shaders), len(lst.perms), len(ms), dict(sorted(kinds.items())), len(mp)))
    return 1 if ms or mp else 0


def _slurp(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def cmd_gate(argv):
    d = argv[0]
    lst = read_list(os.path.join(d, "pipelines.kyplst"))
    guests = parse_guests(_slurp(os.path.join(d, "metal-guests.tsv")))
    deny = parse_deny(_slurp(os.path.join(d, "fast-math-deny.txt")))
    views = _views(argv[1:])
    errs = override_gate(lst, guests, deny, views)
    for e in errs:
        print("harvestall: %s: ERROR: %s" % (d, e))
    print("harvestall: %s: %d override guests + %d deny hashes, %d program caches, %d errors" % (d, len(guests), len(deny), len(views), len(errs)))
    return 1 if errs else 0


def main(argv):
    if len(argv) < 2 or argv[0] not in ("coverage", "gate"):
        print(__doc__)
        return 2
    try:
        return {"coverage": cmd_coverage, "gate": cmd_gate}[argv[0]](argv[1:])
    except (OSError, FormatError) as e:
        print("harvestall: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
