#!/usr/bin/env python3
"""agxdis: SHADER X-RAY layer 6 -- final Apple GPU (AGX) machine code of a Metal shader.

MSL source | .metallib | MTLBinaryArchive / metal-tt Mach-O | raw code bytes
   -> disassembly + one grep-able STATS line per shader function.

OFFLINE: only compiles pipelines (MTLBinaryArchive) or runs Apple's offline compilers
(xcrun metal / metal-tt). Never draws, dispatches or touches GPU counters.

Decoders (external, see README.md "Licence"):
  G13/G14 (M1/M2)      ext/applegpu      dougallj/applegpu main            BSD-3-Clause
  G15/G16 (M3/M4)      ext/applegpu-m3   TellowKrinkle/applegpu branch M3  BSD-3-Clause
  fallback length/name ext/agx-re        ADevWithAnIdea/agx-re agx-isa     GPL-3.0 (optional, runtime only)

Exit codes: 0 ok | 2 usage | 3 input unreadable | 4 shader compile error | 5 no usable function
            6 pipeline/archive creation failed | 7 no Metal device | 8 unsupported GPU generation
            9 decode incomplete (truncated; or any partial/unknown instruction with --strict)
            10 not a shader archive / no AGX code found | 11 toolchain missing (build, xcrun)
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
GETBIN = os.path.join(BUILD, "getbin")
EXTRACT = os.path.join(BUILD, "metal-archive-extractor")
EXT = os.path.join(HERE, "ext")

CPU_TYPE_APPLEGPU = 0x01000013
# cpusubtype of the applegpu Mach-O slice -> metal-tt arch name.  Measured on this machine by
# compiling one kernel with `xcrun metal-tt -arch <name>` for every accepted name
# (Metal toolchain 27.2, macOS 26.6.2).  467 is what the M4 Pro (AGXG16X) device itself emits.
SUBTYPES = {
    322: "applegpu_g13g", 530: "applegpu_g13s", 594: "applegpu_g13d",
    402: "applegpu_g14g", 434: "applegpu_g14s", 498: "applegpu_g14d",
    275: "applegpu_g15g", 419: "applegpu_g15s", 179: "applegpu_g15d",
    403: "applegpu_g16g", 467: "applegpu_g16s",
    339: "applegpu_g17g", 323: "applegpu_g17p", 355: "applegpu_g17s", 387: "applegpu_g17d",
}
# generation -> ISA family understood by a decoder
ISA_OF_GEN = {13: "g13", 14: "g13", 15: "g15", 16: "g15"}
ISA_DIR = {"g13": "applegpu", "g15": "applegpu-m3"}

# G15+ opcodes that the M3-branch length table knows but has no operand decoder for.
# Names are taken from the comments in ext/applegpu-m3/disassemble.py (the '?' ones are that
# author's guesses); stack_load/stack_store are confirmed here by tests/fixtures/spill.metal.
G15_NAME_OVERRIDES = {
    0x0EF: "stack_store", 0x06F: "stack_load", 0x0D7: "image_store",
    0x267: "threadgroup_load", 0x2E7: "threadgroup_store", 0x657: "vertex_store",
    0x287: "frag_op_287", 0x207: "frag_op_207", 0x6E7: "frag_op_6e7",
}
_g15_names = {}


def g15_table_name(key):
    """Name for an op-7 opcode key that has a length but no operand descriptor: explicit
    overrides above, else the comment on that entry of the M3 branch's length table."""
    if not _g15_names:
        _g15_names.update({"_": "_"})
        src = os.path.join(EXT, ISA_DIR["g15"], "disassemble.py")
        try:
            for line in open(src):
                m = re.match(r"\s*0x([0-9a-fA-F]{3}):\s*\d+,\s*#\s*(.*)", line)
                if m:
                    c = m.group(2).strip().lower()
                    k = int(m.group(1), 16)
                    if re.fullmatch(r"[a-z0-9_]+", c):
                        _g15_names[k] = c
                    else:
                        _g15_names[k] = "op_%03x" % k
        except OSError:
            pass
        _g15_names.update(G15_NAME_OVERRIDES)
    return _g15_names.get(key)


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def run(cmd, data=None, what=None, code=10):
    try:
        p = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise Fail(11, "tool not found: %s (run work/agxdis/build.sh)" % cmd[0])
    if p.returncode != 0:
        raise Fail(code, "%s failed (rc=%d): %s" % (what or os.path.basename(cmd[0]), p.returncode,
                                                    p.stderr.decode("utf-8", "replace").strip()))
    return p.stdout


# ---------------------------------------------------------------- input / archive handling

def sniff(data):
    if data[:4] == b"MTLB":
        return "metallib"
    if len(data) >= 4:
        be = struct.unpack(">I", data[:4])[0]
        le = struct.unpack("<I", data[:4])[0]
        if be in (0xCBFEBABE, 0xCAFEBABE) or le in (0xFEEDFACF, 0xFEEDFACE):
            return "archive"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    if "\x00" in text:
        return "unknown"
    return "msl"


def archive_arch(data):
    """Return (subtype, arch name or None) of the applegpu slice of a binary archive."""
    be = struct.unpack(">I", data[:4])[0]
    if be in (0xCBFEBABE, 0xCAFEBABE):
        n = struct.unpack(">I", data[4:8])[0]
        if n > 64 or 8 + 20 * n > len(data):
            raise Fail(10, "corrupt fat header in shader archive")
        for i in range(n):
            cpu, sub, off, size, _ = struct.unpack(">IIIII", data[8 + 20 * i:28 + 20 * i])
            if cpu == CPU_TYPE_APPLEGPU:
                return sub, SUBTYPES.get(sub)
        raise Fail(10, "binary archive has no applegpu slice (not compiled for an Apple GPU)")
    if len(data) < 12:
        raise Fail(10, "file too small to be a shader archive")
    magic, cpu, sub = struct.unpack("<III", data[:12])
    if magic != 0xFEEDFACF or cpu != CPU_TYPE_APPLEGPU:
        raise Fail(10, "Mach-O is not an applegpu shader archive (cputype 0x%x)" % cpu)
    return sub, SUBTYPES.get(sub)


def gen_of(arch):
    m = re.match(r"applegpu_g(\d+)", arch or "")
    return int(m.group(1)) if m else None


def list_functions(path):
    out = run([GETBIN, "--list", path], what="getbin --list", code=4).decode()
    fns = []
    for line in out.splitlines():
        kind, _, name = line.partition(" ")
        if kind in ("vertex", "fragment", "kernel") and name:
            fns.append((kind, name))
    return fns


MTL_FMT = {"rgba8unorm": "RGBA8Unorm", "bgra8unorm": "BGRA8Unorm", "rgba16float": "RGBA16Float",
           "rgba32float": "RGBA32Float", "r32float": "R32Float", "rg16float": "RG16Float",
           "rg11b10float": "RG11B10Float", "rgb10a2unorm": "RGB10A2Unorm", "rgba8uint": "RGBA8Uint",
           "rgba8sint": "RGBA8Sint", "rgba16uint": "RGBA16Uint", "rgba32uint": "RGBA32Uint",
           "rgba32sint": "RGBA32Sint", "r32uint": "R32Uint", "r32sint": "R32Sint", "rg32uint": "RG32Uint",
           "rg32sint": "RG32Sint", "r8unorm": "R8Unorm", "rg8unorm": "RG8Unorm"}
RE_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def fragment_interface(src, float_fmt):
    """Scan MSL text. -> (stub vertex source or None, {colour index: format}, has_depth_output).
    A fragment-only file (the usual shape of dumped translator output) cannot become a render
    pipeline, so a pass-through vertex shader with matching [[user(...)]] outputs is appended.
    Colour formats are not in the MSL: float4 outputs get float_fmt, narrower / integer outputs
    a format with matching component count and class. They only influence the fragment
    epilogue (pack + tile store)."""
    text = RE_COMMENT.sub(" ", src)
    colors = {}
    for m in re.finditer(r"([A-Za-z_][\w:]*(?:<[^<>]*>)?)\s+\w+\s*\[\[\s*color\s*\(\s*(\d+)\s*\)", text):
        ty, idx = m.group(1).split("::")[-1], int(m.group(2))
        if idx > 7:
            continue
        comps = int(ty[-1]) if ty[-1] in "234" else 1
        if ty.startswith(("uint", "ushort", "uchar")):
            fmt = {1: "r32uint", 2: "rg32uint"}.get(comps, "rgba8uint")
        elif ty.startswith(("int", "short", "char")):
            fmt = {1: "r32sint", 2: "rg32sint"}.get(comps, "rgba8sint")
        else:  # a format may not have more components than the shader writes
            fmt = {1: "r8unorm", 2: "rg8unorm", 3: "rg11b10float"}.get(comps, float_fmt)
        colors.setdefault(idx, fmt)
    depth = re.search(r"\[\[\s*depth\s*\(", text) is not None
    f = re.search(r"\bfragment\b[^;{()]*?(\w+)\s*\(", text)
    if not f or re.search(r"(^|[\s\]])vertex\s+[\w:<>]+\s+\w+\s*\(", text):
        return None, colors, depth
    members = []
    depth_p, i = 1, f.end()
    while i < len(text) and depth_p:
        depth_p += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    params = text[f.end():i - 1]
    si = re.search(r"([A-Za-z_][\w:]*)\s+\w+\s*\[\[\s*stage_in\s*\]\]", params)
    if si:
        st = re.search(r"\bstruct\s+%s\s*\{(.*?)\}\s*;" % re.escape(si.group(1).split("::")[-1]), text, re.S)
        if st:
            for mm in re.finditer(r"([A-Za-z_][\w:]*(?:<[^<>]*>)?)\s+(\w+)\s*\[\[([^;]*)\]\]\s*;", st.group(1)):
                u = re.search(r"user\s*\(\s*([\w.]+)\s*\)", mm.group(3))
                if u:
                    members.append((mm.group(1), mm.group(2), u.group(1)))
    lines = ["", "// --- appended by agxdis: pass-through vertex stage for a fragment-only input ---",
             "struct agxdis_VOut {", "    metal::float4 agxdis_pos [[position]];"]
    lines += ["    %s %s [[user(%s)]];" % m3 for m3 in members]
    lines += ["};", "vertex agxdis_VOut agxdis_vs(uint agxdis_vid [[vertex_id]]) {",
              "    agxdis_VOut o = {};",
              "    o.agxdis_pos = metal::float4(float(agxdis_vid & 1u), float(agxdis_vid >> 1), 0.0f, 1.0f);",
              "    return o;", "}", ""]
    return "\n".join(lines), colors, depth


def prepare_msl(path, data, float_fmt, tmp):
    """-> (path to compile, colours, depth, stub_added)"""
    src = data.decode("utf-8")
    stub, colors, depth = fragment_interface(src, float_fmt)
    if not stub:
        return path, colors, depth, False
    out = os.path.join(tmp, "with_stub_vs.metal")
    with open(out, "w") as f:
        f.write(src + stub)
    return out, colors, depth, True


def compile_device(path, functions, tmp, colors=None, depth=False):
    out = os.path.join(tmp, "archive.bin")
    cmd = [GETBIN, "-o", out]
    for f in functions:
        cmd += ["-f", f]
    for idx in sorted(colors or {}):
        cmd += ["--color", "%d=%s" % (idx, colors[idx])]
    if depth:
        cmd.append("--depth")
    cmd.append(path)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise Fail(11, "getbin not built (run work/agxdis/build.sh)")
    if p.returncode != 0:
        raise Fail(p.returncode if p.returncode in (3, 4, 5, 6, 7) else 6,
                   p.stderr.decode("utf-8", "replace").strip())
    return open(out, "rb").read()


def compile_offline(path, kind, functions, arch, tmp, colors=None, depth=False):
    """xcrun metal (MSL -> metallib) + xcrun metal-tt (metallib -> AGX archive). No Metal device
    is needed for the compile itself; getbin --list is used only to enumerate entry points."""
    lib = path
    if kind == "msl":
        lib = os.path.join(tmp, "in.metallib")
        src = path
        if not path.endswith(".metal"):
            src = os.path.join(tmp, "in.metal")
            with open(src, "wb") as f:
                f.write(open(path, "rb").read())
        run(["xcrun", "metal", "-o", lib, src], what="xcrun metal (MSL compile)", code=4)
    fns = list_functions(lib)
    if functions:
        missing = [f for f in functions if f not in [n for _, n in fns]]
        if missing:
            raise Fail(5, "function '%s' not in library" % missing[0])
        fns = [(k, n) for k, n in fns if n in functions]
    by = {}
    for k, n in fns:
        if k in by:
            raise Fail(5, "more than one %s entry point (%s, %s); pick with -f" % (k, by[k], n))
        by[k] = n
    if not by:
        raise Fail(5, "no vertex/fragment/kernel entry point found")
    pipes = {}
    if "kernel" in by:
        pipes["compute_pipelines"] = [{"compute_function": by["kernel"]}]
    if "vertex" in by:
        rp = {"vertex_function": by["vertex"]}
        if "fragment" in by:
            rp["fragment_function"] = by["fragment"]
            cols = colors or {0: "rgba8unorm"}
            rp["color_attachments"] = [{"pixel_format": MTL_FMT[cols.get(i, "rgba8unorm")]}
                                       for i in range(max(cols) + 1)]
            if depth:
                rp["depth_attachment_pixel_format"] = "Depth32Float"
        else:
            rp["depth_attachment_pixel_format"] = "Depth32Float"
        pipes["render_pipelines"] = [rp]
    elif "fragment" in by:
        raise Fail(5, "a fragment function needs a vertex function in the same input")
    js = os.path.join(tmp, "p.mtlp-json")
    with open(js, "w") as f:
        json.dump({"pipelines": pipes}, f)
    out = os.path.join(tmp, "archive.bin")
    p = subprocess.run(["xcrun", "metal-tt", "-arch", arch, "-o", out, js, lib],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0 or not os.path.exists(out):
        err = p.stderr.decode("utf-8", "replace").strip()
        if "invalid architecture" in err:
            raise Fail(8, "metal-tt does not know architecture '%s'" % arch)
        raise Fail(6, "metal-tt failed: %s" % err)
    return open(out, "rb").read()


def extract_functions(archive, want_prolog):
    """-> list of (stage, symbol, offset, code bytes), deterministic order."""
    res = []
    for stage in ("vertex", "fragment", "compute", "dylib"):
        blob = run([EXTRACT, "--extract-" + stage, "-", "-"], data=archive, what="archive extractor")
        if not blob:
            continue
        syms = []
        for line in run([EXTRACT, "--list-shaders", "-"], data=blob, what="archive extractor").decode().splitlines():
            off, _, name = line.partition(" ")
            if name.strip():
                syms.append((int(off, 0), name.strip()))
        order = {"_agc.main.constant_program": 2, "_agc.main": 1}
        syms.sort(key=lambda s: (order.get(s[1], 0), s[0]))
        for off, name in syms:
            if name == "_agc.main.constant_program" and not want_prolog:
                continue
            code = run([EXTRACT, "--extract-named-shader", name, "--output", "-", "-"], data=blob,
                       what="archive extractor")
            if not any(code):
                continue
            res.append((stage, name, off, code))
    return res


# ---------------------------------------------------------------- decoding

class Ins:
    __slots__ = ("off", "raw", "mnem", "text", "src")

    def __init__(self, off, raw, mnem, text, src):
        self.off, self.raw, self.mnem, self.text, self.src = off, raw, mnem, text, src


_loaded = {}


def load_decoder(isa):
    if isa in _loaded:
        return _loaded[isa]
    d = os.path.join(EXT, ISA_DIR[isa])
    if not os.path.exists(os.path.join(d, "applegpu.py")):
        raise Fail(11, "decoder missing: %s (run work/agxdis/build.sh)" % d)
    for m in ("applegpu", "disassemble"):
        sys.modules.pop(m, None)
    sys.path.insert(0, d)
    try:
        import applegpu
        import disassemble
    finally:
        sys.path.remove(d)
    _loaded[isa] = (applegpu, disassemble)
    return _loaded[isa]


_agxre = [None]


def agxre():
    """Optional third decoder (GPL-3.0, clean-room G16/G17 DB); used only for instruction
    lengths/names the BSD decoder does not know. Returns module or False."""
    if _agxre[0] is None:
        d = os.path.join(EXT, "agx-re", "tools", "agx-isa")
        _agxre[0] = False
        if os.environ.get("AGXDIS_NO_AGXRE") != "1" and os.path.exists(os.path.join(d, "isadb.py")):
            sys.path.insert(0, d)
            try:
                import isadb
                _agxre[0] = isadb
            except Exception:
                _agxre[0] = False
            finally:
                sys.path.remove(d)
    return _agxre[0]


def is_padding(code, p):
    rest = code[p:]
    if len(rest) % 2:
        return False
    words = set(rest[i:i + 2] for i in range(0, len(rest), 2))
    return words <= {b"\x00\x00", b"\x08\x00", b"\x06\x00", b"\x0e\x00"}


def descr_decode(applegpu, n, pc):
    for o in applegpu.instruction_descriptors:
        try:
            if o.matches(n):
                return o.decode_mnem(n), str(o.disassemble(n, pc=pc)).rstrip(), o.decode_size(n)
        except Exception as e:  # decoder bug on an unexpected encoding
            return None, "<decoder exception %s>" % type(e).__name__, None
    return None, None, None


def decode(code, isa, base=0):
    """-> (list[Ins], truncated_bytes)"""
    applegpu, disassemble = load_decoder(isa)
    if isa == "g13":
        out, p = [], 0
        while p < len(code):
            if out and out[-1].mnem == "stop" and is_padding(code, p):
                break
            n = applegpu.opcode_to_number(code[p:])
            mnem, text, size = descr_decode(applegpu, n, p + base)
            if mnem is None or not size:
                out.append(Ins(p, code[p:p + 2], "unknown", text or "<unknown>", "unknown"))
                p += 2
                continue
            out.append(Ins(p, code[p:p + size], mnem, text, "decoder"))
            p += size
        return out, 0
    return decode_g15(code, applegpu, disassemble, base)


def decode_g15(code, applegpu, disassemble, base):
    """G15/G16: the instruction length comes from the opcode (length table / heuristics of the
    M3 branch), then the operand descriptor is looked up.
      ' ' decoder : length + operands decoded by the M3 branch
      '~' table   : length from the M3 length table, name from its comments, no operand decode
      '~' agx-re  : no M3 descriptor/table entry; name + length from agx-re's G16/G17 DB
                    (plus its trailing 'pad_operand' word if any), no operand decode
      '?' unknown : nobody names it; length = M3 heuristic. Tokenisation after it is less certain.
    Decoding stops (trunc) when no source knows the length."""
    out, p = [], 0
    db = agxre()
    while p < len(code):
        if out and out[-1].mnem == "stop" and is_padding(code, p):
            break
        try:
            length = disassemble.instruction_length(code, p)
        except IndexError:
            length = 0
        if length and p + length <= len(code):
            raw = code[p:p + length]
            mnem, text, size = descr_decode(applegpu, applegpu.opcode_to_number(raw), p + base)
            if mnem is not None and size == length:
                out.append(Ins(p, raw, mnem, text, "decoder"))
                p += length
                continue
            key = code[p] | ((code[p + 1] & 0xF) << 8)
            name = g15_table_name(key) if code[p] & 7 == 7 else ("texture_unk" if code[p] & 7 == 5 else None)
            if name:
                out.append(Ins(p, raw, name, name + "  <operands not decoded>", "table"))
                p += length
                continue
        name = None
        if (length == 6 and code[p] & 7 in (0, 1) and code[p + 2] & 7 == 7 and p + 8 <= len(code)
                and os.environ.get("AGXDIS_NO_FALU8") != "1"):
            # float ALU form with op-select 0b111 (seen writing perspective-divided varyings /
            # coordinate registers in fragment shaders): the M3 heuristic says 6 bytes, but the
            # instruction carries one more word. Rule measured on real fragment shaders
            # (README "Decode coverage"): with 8 bytes the stream stays in sync.
            n8 = 8 + 2 * (code[p + 6] & 3)  # extension words, same scheme as the 6-byte form
            if p + n8 > len(code):
                n8 = 8
            out.append(Ins(p, code[p:p + n8], "falu_111", "falu_111  <float ALU variant, operands not decoded>", "table"))
            p += n8
            continue
        if db:
            try:
                rec, alen = db.decode_one(code, p)
                if rec and not rec.get("error") and alen and p + alen <= len(code):
                    name = rec.get("op_mnemonic") or rec["mnemonic"]
                    length = alen
                    if p + alen < len(code):
                        rec2, len2 = db.decode_one(code, p + alen)
                        if rec2 and rec2.get("mnemonic") == "pad_operand" and p + alen + len2 <= len(code):
                            length += len2
            except Exception:
                pass
        if not length or p + length > len(code):
            return out, len(code) - p
        if name and name != "pad_operand":
            out.append(Ins(p, code[p:p + length], name, name + "  <agx-re name/length only>", "agx-re"))
        else:
            out.append(Ins(p, code[p:p + length], "unknown", "<unknown>", "unknown"))
        p += length
    return out, 0


# ---------------------------------------------------------------- statistics

RE_GPR = re.compile(r"(?<![A-Za-z0-9_])r(\d+)[lh]?(?![A-Za-z0-9])")
RE_UREG = re.compile(r"(?<![A-Za-z0-9_])u(\d+)[lh]?(?![A-Za-z0-9])")
RE_SREG = re.compile(r"(?<![A-Za-z0-9_])st(\d+)[lh]?(?![A-Za-z0-9])")
RE_ANYREG = re.compile(r"(?<![A-Za-z0-9_])(st|cr|r|u)\d+[lh]?(?![A-Za-z0-9])")
RE_CREG = re.compile(r"(?<![A-Za-z0-9_])cr(\d+)[lh]?(?![A-Za-z0-9])")
RE_TARGET = re.compile(r"\b0x([0-9A-Fa-f]+)\s*$")
TYPE_BITS = {"i8": 8, "i16": 16, "i32": 32, "u8": 8, "u16": 16, "u32": 32, "f16": 16, "f32": 32}


def base_mnem(m):
    return m.split(".")[0]


def operands(text):
    parts = text.split(None, 1)
    return [t.strip() for t in parts[1].split(",")] if len(parts) > 1 else []


def mem_width(text):
    """'device_load 5, i32, xyzw, r0_r1_r2_r3, ...' -> ('i32x4', 16 bytes)"""
    ops = operands(text)
    for i, t in enumerate(ops):
        if t in TYPE_BITS and i + 1 < len(ops):
            m = ops[i + 1]
            if m and set(m) <= set("xyzw"):
                return "%sx%d" % (t, len(m)), TYPE_BITS[t] // 8 * len(m)
            if m in ("pair", "quad"):
                c = 2 if m == "pair" else 4
                return "%sx%d" % (t, c), TYPE_BITS[t] // 8 * c
    return "?", 0


def tex_kind(isa, m):
    """Decoder mnemonic -> MSL meaning. On G15/G16 the M3 branch's names are the other way
    round from MSL: its 'texture_load' is emitted for MSL sample() (sampler used) and its
    'texture_sample' for MSL read() -- measured with 6 own shaders (README 'Texture ops',
    tests texsample/texrw/texnames). On G13 the names match MSL."""
    if isa == "g15":
        return "tex_sample" if m == "texture_load" else "tex_load"
    return "tex_sample" if m == "texture_sample" else "tex_load"


def stats(ins, truncated, nbytes, isa):
    s = {
        "bytes": nbytes, "instr": len(ins), "gpr": 0, "gpr_max": -1, "ureg": 0, "creg": 0,
        "stack_ld": 0, "stack_st": 0, "spill_ld": 0, "spill_st": 0, "spill_slots": 0, "atomic": 0,
        "tex_other": 0,
        "dload": 0, "dload_bytes": 0, "dload_w": {}, "dstore": 0, "dstore_bytes": 0, "dstore_w": {},
        "tgload": 0, "tgstore": 0,
        "tex_sample": 0, "tex_load": 0, "tex_store": 0,
        "loops": 0, "while": 0, "if": 0, "else": 0, "jmp": 0, "call": 0,
        "partial": 0, "unknown": 0, "unsure": 0, "trunc": truncated, "hist": {},
    }
    gprs, uregs, cregs, back, slots = set(), set(), set(), set(), set()
    for i in ins:
        m = base_mnem(i.mnem)
        s["hist"][m] = s["hist"].get(m, 0) + 1
        if i.src in ("table", "agx-re"):
            s["partial"] += 1
        elif i.src == "unknown":
            s["unknown"] += 1
        elif ".todo" in i.text.split(None, 1)[0] or "<decoder exception" in i.text:
            # the decoder itself flags this encoding as not fully understood (unexpected bits):
            # the mnemonic is used, its operands are NOT (they produced bogus registers, e.g. r318)
            s["unsure"] += 1
            if m == "get_stack":
                s["spill_ld"] += 1
        else:
            parts = i.text.split(None, 1)
            body = parts[1] if len(parts) > 1 else ""
            body = body.replace("_", " ")
            gprs.update(int(x) for x in RE_GPR.findall(body))
            uregs.update(int(x) for x in RE_UREG.findall(body))
            cregs.update(int(x) for x in RE_CREG.findall(body))
            st = RE_SREG.findall(body)
            if st:
                # G15+: 'stN' = stack (spill-slot) registers. get_stack = reload; any other
                # instruction whose destination (first register operand) is stN = spill store.
                slots.update(int(x) for x in st)
                first = RE_ANYREG.search(body)
                if m == "get_stack":
                    s["spill_ld"] += 1
                elif first and first.group(1) == "st":
                    s["spill_st"] += 1
        if m in ("stack_load", "stack_store"):
            s["stack_ld" if m == "stack_load" else "stack_st"] += 1
        elif m.startswith("atomic"):
            s["atomic"] += 1
        elif m == "texture_unk":
            s["tex_other"] += 1
        elif m in ("device_load", "device_store"):
            k = "dload" if m == "device_load" else "dstore"
            w, b = mem_width(i.text)
            s[k] += 1
            s[k + "_bytes"] += b
            s[k + "_w"][w] = s[k + "_w"].get(w, 0) + 1
        elif m == "threadgroup_load":
            s["tgload"] += 1
        elif m == "threadgroup_store":
            s["tgstore"] += 1
        elif m in ("texture_sample", "texture_load"):
            s[tex_kind(isa, m)] += 1
        elif m in ("image_store", "image_write", "tex_write", "texture_store"):
            s["tex_store"] += 1
        elif m.startswith("while"):
            s["while"] += 1
        elif m.startswith("if"):
            s["if"] += 1
        elif m.startswith("else"):
            s["else"] += 1
        elif m.startswith("jmp"):
            s["jmp"] += 1
            t = RE_TARGET.search(i.text)
            if t and int(t.group(1), 16) <= i.off:
                back.add(int(t.group(1), 16))
        elif m.startswith("call"):
            s["call"] += 1
    # a loop = a distinct backward jump target; `while` count is the cross-check when jump
    # targets could not be parsed
    s["loops"] = len(back) if back else s["while"]
    s["gpr"], s["gpr_max"] = len(gprs), (max(gprs) if gprs else -1)
    s["ureg"], s["creg"], s["spill_slots"] = len(uregs), len(cregs), len(slots)
    return s


def fmt_w(d):
    return ",".join("%s:%d" % (k, d[k]) for k in sorted(d)) or "-"


def stats_line(stage, sym, arch, isa, s):
    return ("STATS stage=%s fn=%s gpu=%s isa=%s bytes=%d instr=%d gpr=%d gpr_max=%s ureg=%d creg=%d "
            "stack_ld=%d stack_st=%d spill_ld=%d spill_st=%d spill_slots=%d spill=%d "
            "dload=%d dload_bytes=%d dload_w=%s dstore=%d dstore_bytes=%d dstore_w=%s "
            "tgload=%d tgstore=%d atomic=%d tex_sample=%d tex_load=%d tex_store=%d tex_other=%d "
            "loops=%d if=%d else=%d jmp=%d call=%d partial=%d unknown=%d unsure=%d trunc=%d complete=%d") % (
        stage, sym, arch or "?", isa, s["bytes"], s["instr"], s["gpr"],
        ("r%d" % s["gpr_max"]) if s["gpr_max"] >= 0 else "-", s["ureg"], s["creg"],
        s["stack_ld"], s["stack_st"], s["spill_ld"], s["spill_st"], s["spill_slots"],
        1 if (s["stack_ld"] or s["stack_st"] or s["spill_ld"] or s["spill_st"]) else 0,
        s["dload"], s["dload_bytes"], fmt_w(s["dload_w"]), s["dstore"], s["dstore_bytes"], fmt_w(s["dstore_w"]),
        s["tgload"], s["tgstore"], s["atomic"], s["tex_sample"], s["tex_load"], s["tex_store"], s["tex_other"],
        s["loops"], s["if"], s["else"], s["jmp"], s["call"], s["partial"], s["unknown"], s["unsure"], s["trunc"],
        0 if s["trunc"] else 1)


# ---------------------------------------------------------------- main

def main(argv):
    ap = argparse.ArgumentParser(prog="agxdis.sh", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="MSL source, .metallib, binary archive (MTLBinaryArchive / metal-tt), or raw code with --raw")
    ap.add_argument("-f", "--function", action="append", default=[], help="entry point(s) to compile (MSL/metallib input)")
    ap.add_argument("--arch", help="compile offline with xcrun metal-tt for this GPU (e.g. applegpu_g13g, applegpu_g16s) instead of this Mac's device")
    ap.add_argument("--format", default="rgba8unorm", choices=sorted(MTL_FMT), help="pixel format assumed for float colour outputs (default rgba8unorm); affects only the fragment epilogue")
    ap.add_argument("--raw", action="store_true", help="input is raw instruction bytes of one function (needs --isa)")
    ap.add_argument("--isa", choices=["g13", "g15"], help="force decoder family: g13 = G13/G14 (M1/M2), g15 = G15/G16 (M3/M4)")
    ap.add_argument("--prolog", action="store_true", help="also show _agc.main.constant_program (uniform prolog)")
    ap.add_argument("--stats-only", action="store_true", help="print only the STATS lines")
    ap.add_argument("--json", metavar="FILE", help="also write per-function stats as JSON ('-' = stdout instead of text)")
    ap.add_argument("--keep", metavar="FILE", help="save the binary archive that was disassembled")
    ap.add_argument("--strict", action="store_true", help="exit 9 if any instruction is only partially decoded or unknown")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0

    try:
        data = open(a.input, "rb").read()
    except OSError as e:
        raise Fail(3, "cannot read input '%s': %s" % (a.input, e.strerror))
    if not data:
        raise Fail(3, "input '%s' is empty" % a.input)

    arch, stub_added = None, False
    with tempfile.TemporaryDirectory(prefix="agxdis.") as tmp:
        if a.raw:
            if not a.isa:
                raise Fail(2, "--raw needs --isa g13|g15")
            funcs = [("raw", "raw", 0, data)]
        else:
            kind = sniff(data)
            if kind == "unknown":
                raise Fail(3, "input '%s' is not MSL text, a metallib or a shader archive (binary garbage?); use --raw --isa for raw code" % a.input)
            if kind == "archive":
                archive = data
            else:
                src, colors, depth, stub = a.input, None, False, False
                fns = list(a.function)
                if kind == "msl":
                    src, colors, depth, stub = prepare_msl(a.input, data, a.format, tmp)
                    stub_added = stub
                    if stub and fns and "agxdis_vs" not in fns:
                        fns.append("agxdis_vs")
                if a.arch:
                    archive = compile_offline(src, kind, fns, a.arch, tmp, colors, depth)
                else:
                    archive = compile_device(src, fns, tmp, colors, depth)
            if a.keep:
                with open(a.keep, "wb") as f:
                    f.write(archive)
            sub, arch = archive_arch(archive)
            if arch is None and not a.isa:
                raise Fail(8, "unsupported GPU: unknown applegpu cpusubtype %d in archive (known: G13..G17); "
                              "pass --isa g13|g15 to try a decoder anyway" % sub)
            funcs = extract_functions(archive, a.prolog)
            if stub_added:  # the appended pass-through vertex stage is ours, not the user's shader
                funcs = [f for f in funcs if f[0] != "vertex"]
            if not funcs:
                raise Fail(10, "no AGX shader code found in archive")
        isa = a.isa
        if not isa:
            g = gen_of(arch)
            isa = ISA_OF_GEN.get(g)
            if not isa:
                sizes = ", ".join("%s/%s=%dB" % (st, sy, len(c)) for st, sy, _, c in funcs)
                raise Fail(8, "unsupported GPU generation G%s (%s): no verified decoder for this ISA "
                              "(supported: G13/G14 via applegpu, G15/G16 via applegpu M3 branch). "
                              "Raw code extracted OK: %s. Pass --isa g15 to try the G15/G16 decoder anyway."
                              % (g, arch, sizes))

        results, lines = [], []
        rc = 0
        for stage, sym, off, code in funcs:
            ins, trunc = decode(code, isa)
            s = stats(ins, trunc, sum(len(i.raw) for i in ins) + trunc, isa)
            fn = {"_agc.main": "main", "_agc.main.constant_program": "prolog"}.get(sym, sym)
            if not a.stats_only:
                lines.append("; %s %s (%s, %s, isa %s)" % (stage, fn, sym, arch or "raw", isa))
                for i in ins:
                    mark = {"decoder": " ", "table": "~", "agx-re": "~", "unknown": "?"}[i.src]
                    note = ""
                    if base_mnem(i.mnem) in ("texture_sample", "texture_load"):
                        note = "   ; = MSL %s" % {"tex_sample": "sample()", "tex_load": "read()"}[tex_kind(isa, base_mnem(i.mnem))]
                    lines.append("%5x:%s %-40s %s%s" % (i.off, mark, i.raw.hex(), i.text, note))
                if trunc:
                    p = len(code) - trunc
                    lines.append("%5x:! <decode stopped: unknown instruction length; %d bytes not decoded> %s"
                                 % (p, trunc, code[p:p + 16].hex()))
            lines.append(stats_line(stage, fn, arch, isa, s))
            if not a.stats_only:
                lines.append("")
            s.update({"stage": stage, "fn": fn, "gpu": arch, "isa": isa})
            results.append(s)
            if trunc or (a.strict and (s["partial"] or s["unknown"])):
                rc = 9
        if a.json == "-":
            print(json.dumps(results, indent=1, sort_keys=True))
        else:
            sys.stdout.write("\n".join(lines).rstrip("\n") + "\n")
            if a.json:
                with open(a.json, "w") as f:
                    json.dump(results, f, indent=1, sort_keys=True)
        if rc == 9:
            sys.stderr.write("agxdis: warning: decode incomplete (see trunc=/partial=/unknown= in STATS)\n")
        return rc


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as e:
        sys.stderr.write("agxdis: error: %s\n" % e)
        sys.exit(e.code)
    except BrokenPipeError:
        sys.exit(0)
