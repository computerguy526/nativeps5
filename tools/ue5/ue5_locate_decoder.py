#!/usr/bin/env python3
"""ue5_locate_decoder: find the Oodle decoder entry of a translated UE5 title (the `ue5 decoder= fp=` row of games/<id>/game.tsv).

  python3 -I tools/ue5/ue5_locate_decoder.py <build dir | guest_image.bin> [--base 0xHEX] [--prologue HEX] [--all] [--json]

Method (uewarmlist plan, section 0): the image holds the Oodle strings `OodleLZ_Decompress` and
`OODLE ERROR : bad parameter : decodeTo != __null`. Find every x86-64 `lea reg,[rip+disp32]` that points at one of them, take the
enclosing function start (the end of the int3 padding run before the lea), and count its direct `call rel32` callers. The function
that references both strings is the decoder. The guest vaddr comes from the region table of the image header (`NPS5IMG\\x01`, u32 nregions, u32 nsegments, then the regions {vaddr, size, file offset} and the
segment table), so a file position inside a region maps to region vaddr + (position - file offset); a headerless image is flat
(--base + file offset). The scan is region-local; a candidate needs a 16-aligned start, 64 bytes inside its region and at least one
direct caller. The fingerprint is taken from the image bytes the loader copies to the guest address (what Kyty hashes at run time):
still check that the printed vaddr is an address-map function entry and run the Kyty selftest (docs/ue5-warmlist.md).

Output: numbers only (vaddr, lea offset, direct caller count, FNV-1a 64 of the first 64 bytes) and the two game.tsv rows. No bytes of
the image are printed. Exit codes: 0 exactly one decoder found (or --all), 1 none / ambiguous, 2 usage or unreadable image.
--prologue HEX: also require the function to start with these bytes (the same Oodle build has the same prologue across titles)."""
import json, mmap, os, re, struct, sys

STRINGS = (b"OodleLZ_Decompress\0", b"OODLE ERROR : bad parameter : decodeTo != __null\0")
LEA = re.compile(rb"(?=[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d])", re.S)
CALL = re.compile(rb"(?=\xe8)", re.S)
FNV_OFF, FNV_PRIME, M64 = 0xcbf29ce484222325, 0x100000001b3, (1 << 64) - 1
MIN_PAD = 2                      # an int3 run this long ends the previous function (a lone 0xCC can be inside an immediate)


def fnv1a64(b):
    h = FNV_OFF
    for c in b:
        h = ((h ^ c) * FNV_PRIME) & M64
    return h


MAGIC = b"NPS5IMG\x01"


class ImageError(ValueError):
    pass


def regions(img, base=0):
    """-> [(vaddr, size, file_offset)]. A translated build's guest_image.bin (emitter.EncodeGuestImage, loaded by recomp_runtime.c
    load_guest_image) is: magic[8], u32 nregions, u32 nsegments, nregions x {u64 vaddr, u64 size, u64 file_offset}, nsegments x
    {u64 vaddr, u64 memsz, u32 pf_flags, u32 reserved}, then the region bytes. Exactly nregions entries are regions (the segment table
    only carries protections). Without the header the image is flat: file offset + base = vaddr."""
    if bytes(img[:8]) == MAGIC:
        if len(img) < 16:
            raise ImageError("truncated NPS5IMG header")
        nreg, nseg = struct.unpack_from("<II", img, 8)
        if nreg == 0 or nreg > 4096 or nseg > 65536:
            raise ImageError("implausible NPS5IMG table sizes (%d regions, %d segments)" % (nreg, nseg))
        hdr = 16 + 24 * nreg + 24 * nseg
        if hdr > len(img):
            raise ImageError("NPS5IMG tables run past the end of the file")
        out = []
        for i in range(nreg):
            v, sz, fo = struct.unpack_from("<QQQ", img, 16 + 24 * i)
            if fo < hdr or fo + sz > len(img):
                raise ImageError("NPS5IMG region %d lies outside the file" % i)
            out.append((v, sz, fo))
        return out
    return [(base, len(img), 0)]


def region_of(regs, pos):
    for r in regs:
        if r[2] <= pos < r[2] + r[1]:
            return r
    return None


def f2v(regs, pos):
    r = region_of(regs, pos)
    return None if r is None else r[0] + pos - r[2]


def image_path(p):
    if os.path.isdir(p):
        p = os.path.join(p, "guest_image.bin")
    return p


def string_offsets(img):
    out = {}
    for s in STRINGS:
        offs, i = [], img.find(s)
        while i >= 0:
            offs.append(i); i = img.find(s, i + 1)
        out[s] = offs
    return out


def function_start(img, off, region):
    """File offset of the start of the function holding `off`, scanning only inside `region` (vaddr, size, file offset): the first byte
    after the nearest int3 padding run (>= MIN_PAD) whose end is 16-byte aligned in guest memory (a lone `CC CC` inside an immediate,
    e.g. the divide-by-10 constant 0xCCCCCCCD, does not end on an aligned boundary). The region's first byte counts as a start when no
    padding precedes (a function at the start of the region). None when there is no such start."""
    v, sz, fo = region
    i = off
    while i > fo:
        if img[i - 1] == 0xCC and i - fo >= MIN_PAD and img[i - MIN_PAD:i] == b"\xcc" * MIN_PAD and (v + i - fo) % 16 == 0 and img[i] != 0xCC:
            return i
        i -= 1
    return fo if v % 16 == 0 else None


def lea_refs(img, regs, targets):
    """-> [(lea file offset, target vaddr)] of every rip-relative lea whose target vaddr is in `targets`."""
    hit = []
    size = len(img)
    for m in LEA.finditer(img):
        o = m.start()
        if o + 7 > size:
            break
        va = f2v(regs, o)
        if va is None:
            continue
        t = va + 7 + struct.unpack_from("<i", img, o + 3)[0]
        if t in targets:
            hit.append((o, t))
    return hit


def direct_callers(img, regs, entry_va):
    n = 0
    size = len(img)
    for m in CALL.finditer(img):
        o = m.start()
        if o + 5 > size:
            break
        va = f2v(regs, o)
        if va is not None and va + 5 + struct.unpack_from("<i", img, o + 1)[0] == entry_va:
            n += 1
    return n


def locate(img, base=0, prologue=None):
    """-> list of dicts, one per function that references an Oodle string; `both` is true for the decoder (references both)."""
    regs = regions(img, base)
    strs = string_offsets(img)
    targets = {f2v(regs, o): s for s, offs in strs.items() for o in offs if f2v(regs, o) is not None}
    funcs = {}
    for lea, t in lea_refs(img, regs, targets):
        f = function_start(img, lea, region_of(regs, lea))
        if f is None:
            continue
        d = funcs.setdefault(f, dict(entry=f, strings=set(), lea=lea - f))
        d["strings"].add(targets[t])
        d["lea"] = min(d["lea"], lea - f)
    out = []
    for f, d in sorted(funcs.items()):
        r = region_of(regs, f)
        head = bytes(img[f:f + 64])
        if r is None or f + 64 > r[2] + r[1]:      # Kyty hashes 64 bytes at the vaddr: all 64 must be in this one region
            sys.stderr.write("ue5_locate_decoder: candidate at file offset 0x%x has fewer than 64 bytes in its region: skipped\n" % f)
            continue
        if prologue and not head.startswith(prologue):
            continue
        va = f2v(regs, f)
        out.append(dict(vaddr=va, lea=d["lea"], fp="%016x" % fnv1a64(head), head_bytes=len(head),
                        callers=direct_callers(img, regs, va), both=len(d["strings"]) == len(STRINGS)))
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0 if argv else 2
    path, base, prologue, show_all, as_json = None, 0, None, False, False
    try:
        i = 0
        while i < len(argv):
            a = argv[i]
            if a == "--base": base = int(argv[i + 1], 16); i += 1
            elif a == "--prologue": prologue = bytes.fromhex(argv[i + 1]); i += 1
            elif a == "--all": show_all = True
            elif a == "--json": as_json = True
            elif a.startswith("-"): raise ValueError("unknown option %s" % a)
            elif path is None: path = a
            else: raise ValueError("one build dir or image expected")
            i += 1
        if path is None: raise ValueError("build dir or guest_image.bin expected")
    except (ValueError, IndexError) as e:
        print("ue5_locate_decoder: %s" % e, file=sys.stderr); return 2
    p = image_path(path)
    try:
        with open(p, "rb") as f:
            if os.fstat(f.fileno()).st_size == 0:
                raise OSError("empty image")
            img = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    except OSError as e:
        print("ue5_locate_decoder: cannot read %s: %s" % (os.path.basename(p), e), file=sys.stderr); return 2
    try:
        found = locate(img, base, prologue)
    except ImageError as e:
        print("ue5_locate_decoder: %s: %s" % (os.path.basename(p), e), file=sys.stderr); return 2
    finally:
        img.close()
    # a decoder is called: no direct caller means the "entry" is not a function start (or the function is only called through a pointer)
    nocall = [d for d in found if d["both"] and d["callers"] == 0]
    pick = found if show_all else [d for d in found if d["both"] and d["callers"] > 0]
    if as_json:
        print(json.dumps(pick, sort_keys=True))
    else:
        for d in pick:
            print("decoder=0x%x lea=+0x%x callers=%d fp=%s%s" % (d["vaddr"], d["lea"], d["callers"], d["fp"], "" if d["both"] else " (one string only)"))
        if len(pick) == 1 and pick[0]["both"]:
            d = pick[0]
            print("ue5\tdecoder=0x%x\tfp=%s\tabi=oodlelz14" % (d["vaddr"], d["fp"]))
    if show_all:
        return 0 if pick else 1
    if len(pick) != 1:
        print("ue5_locate_decoder: %s" % (("%d function(s) reference both Oodle strings but none has a direct caller (not a function start? use --all)" % len(nocall) if nocall else "no function references both Oodle strings") if not pick else "%d candidate functions (give --prologue, or --all to list)" % len(pick)),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
