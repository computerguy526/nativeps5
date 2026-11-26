#!/usr/bin/env python3
"""ue5_locate_decoder: find the Oodle decoder entry of a translated UE5 title (the `ue5 decoder= fp=` row of games/<id>/game.tsv).

  python3 -I tools/ue5/ue5_locate_decoder.py <build dir | guest_image.bin> [--base 0xHEX] [--prologue HEX] [--all] [--json]

Method (uewarmlist plan, section 0): the image holds the Oodle strings `OodleLZ_Decompress` and
`OODLE ERROR : bad parameter : decodeTo != __null`. Find every x86-64 `lea reg,[rip+disp32]` that points at one of them, take the
enclosing function start (the end of the int3 padding run before the lea), and count its direct `call rel32` callers. The function
that references both strings is the decoder. The guest vaddr is `--base` + the file offset (default base 0: guest_image.bin is the
loaded image from its lowest address).

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


def function_start(img, off):
    """Start of the function holding offset `off`: the first byte after the nearest int3 run (>= MIN_PAD) before it."""
    i = off
    while i > 0:
        if img[i - 1] == 0xCC and i >= MIN_PAD and img[i - MIN_PAD:i] == b"\xcc" * MIN_PAD:
            return i
        i -= 1
    return 0


def lea_refs(img, targets):
    """-> [(lea offset, target offset)] of every rip-relative lea whose target is in `targets`."""
    hit = []
    size = len(img)
    for m in LEA.finditer(img):
        o = m.start()
        if o + 7 > size:
            break
        t = o + 7 + struct.unpack_from("<i", img, o + 3)[0]
        if t in targets:
            hit.append((o, t))
    return hit


def direct_callers(img, entry):
    n = 0
    size = len(img)
    for m in CALL.finditer(img):
        o = m.start()
        if o + 5 > size:
            break
        if o + 5 + struct.unpack_from("<i", img, o + 1)[0] == entry:
            n += 1
    return n


def locate(img, base=0, prologue=None):
    """-> list of dicts, one per function that references an Oodle string; `both` is true for the decoder (references both)."""
    strs = string_offsets(img)
    targets = {o: s for s, offs in strs.items() for o in offs}
    funcs = {}
    for lea, t in lea_refs(img, targets):
        f = function_start(img, lea)
        d = funcs.setdefault(f, dict(entry=f, strings=set(), lea=lea - f))
        d["strings"].add(targets[t])
        d["lea"] = min(d["lea"], lea - f)
    out = []
    for f, d in sorted(funcs.items()):
        head = bytes(img[f:f + 64])
        if prologue and not head.startswith(prologue):
            continue
        out.append(dict(vaddr=base + f, lea=d["lea"], fp="%016x" % fnv1a64(head), head_bytes=len(head),
                        callers=direct_callers(img, f), both=len(d["strings"]) == len(STRINGS)))
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
    finally:
        img.close()
    pick = found if show_all else [d for d in found if d["both"]]
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
        print("ue5_locate_decoder: %s" % ("no function references both Oodle strings" if not pick else "%d candidate functions (give --prologue, or --all to list)" % len(pick)),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
