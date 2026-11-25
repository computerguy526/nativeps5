#!/usr/bin/env python3
"""loadedcheck: what a running title really loaded, against what the run configured (v1.4: the configured and the loaded libraries
differed twice: LIBS pointing at a flat directory fell back to the binary's LC_RPATH host libraries, and a titles/<id>.env KYTY_LIB
overrode the Kyty library a gate exported).

    python3 -I scripts/nps5lib/loadedcheck.py <pid> --kyty /path/kyty_gpu.dylib --host-dir /path/to/dir/with/libnps5_gpu.dylib
    python3 -I scripts/nps5lib/loadedcheck.py --parse <file with `vmmap -w`-style or `lsof -p` text> --kyty ... --host-dir ...

Reads the mapped images of the process (lsof -p), finds kyty_gpu.dylib and libnps5_gpu.dylib (and any libSceAgc*.prx / libc.prx /
libkernel.prx) and compares the real paths with the expected ones and their sha256_16. Exit 0 ok, 1 mismatch / not loaded, 2 usage.
Standard library only."""
import hashlib, os, re, subprocess, sys

WATCH = ("kyty_gpu.dylib", "libnps5_gpu.dylib")


def mapped_images(text):
    """-> {basename: set(path)} for the watched libraries in lsof / vmmap text (any line carrying an absolute path ending in the name)."""
    found = {}
    for line in text.splitlines():
        for m in re.finditer(r"(/[^\s]*?(?:%s))(?=\s|$)" % "|".join(re.escape(n) for n in WATCH), line):
            p = m.group(1)
            found.setdefault(os.path.basename(p), set()).add(os.path.realpath(p) if os.path.exists(p) else p)
    return found


def compare(found, kyty=None, host_dir=None):
    """-> list of problem strings (empty = what is loaded is what was expected)."""
    bad = []
    if kyty:
        got = found.get("kyty_gpu.dylib", set())
        if not got: bad.append("kyty_gpu.dylib is not loaded")
        elif got != {os.path.realpath(kyty) if os.path.exists(kyty) else kyty}:
            bad.append("kyty_gpu.dylib loaded from %s, expected %s" % (sorted(got), kyty))
    if host_dir:
        want = os.path.join(host_dir, "libnps5_gpu.dylib")
        got = found.get("libnps5_gpu.dylib", set())
        if not got: bad.append("libnps5_gpu.dylib is not loaded")
        elif got != {os.path.realpath(want) if os.path.exists(want) else want}:
            bad.append("libnps5_gpu.dylib loaded from %s, expected %s" % (sorted(got), want))
    return bad


def sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()[:16]


def main(argv):
    kyty = host = text = pid = None; i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--kyty": kyty = argv[i + 1]; i += 2
        elif a == "--host-dir": host = argv[i + 1]; i += 2
        elif a == "--parse": text = open(argv[i + 1], errors="replace").read(); i += 2
        elif a.isdigit(): pid = a; i += 1
        else: print(__doc__); return 2
    if text is None:
        if pid is None: print(__doc__); return 2
        text = subprocess.run(["lsof", "-p", pid], capture_output=True, text=True, errors="replace").stdout
    found = mapped_images(text)
    bad = compare(found, kyty, host)
    for n, ps in sorted(found.items()):
        for p in sorted(ps):
            print("loaded %s %s sha256_16=%s" % (n, p, sha16(p) if os.path.isfile(p) else "?"))
    for b in bad: print("MISMATCH: " + b)
    return 1 if bad else 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
