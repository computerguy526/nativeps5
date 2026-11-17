#!/usr/bin/env python3
"""Regenerate docs/env_switches.tsv: every environment switch the components read.

Usage:
  scripts/gen_env_switches.py ps5recomp=<dir> anyps5=<dir> kyty=<dir> kosmickrisp=<mesa dir>/src/kosmickrisp
  scripts/gen_env_switches.py --check ...     exit 1 if the committed table would change

A switch is a string literal (C, C++, Objective-C, Go, Python) or a bare word (shell) that starts with one of
PREFIXES and is made of capitals, digits and underscores. That catches getenv("X") as well as the helper
wrappers the components use (an option table, EnvBool("X"), os.Getenv("X")).

Columns: name, component, meaning, source (first file that names it, relative to the component).
The meaning column is written by hand. Regenerating keeps every meaning already in the table, adds new names
with an empty meaning and drops names that no longer exist in the scanned trees. The scripts and host tools
of this repository are always scanned as component "nativeps5".

Only names and paths of source files are written; nothing is read from a title or a dump.
"""
import os
import re
import sys

PREFIXES = ("KYTY", "NPS5", "APS5", "ASTROHYBRID", "KK", "MESA_KK", "PS4RECOMP", "PS4")
NAME = r"(?:%s)_[A-Z0-9_]*[A-Z0-9]" % "|".join(PREFIXES)
LITERAL = re.compile(r'"(%s)"' % NAME)
WORD = re.compile(r"(?<![A-Za-z0-9_])(%s)(?![A-Za-z0-9_])" % NAME)
CODE_EXT = (".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".go", ".py")
SHELL_EXT = (".sh", ".env", ".example")
SKIP_DIRS = {".git", "3rdparty", "third_party", "vendor", "node_modules", "__pycache__", "docs"}


def scan(root, shell_too):
    found = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("build"))
        for f in sorted(files):
            if f.endswith(CODE_EXT):
                rx = LITERAL
            elif shell_too and f.endswith(SHELL_EXT):
                rx = WORD
            else:
                continue
            path = os.path.join(base, f)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            rel = os.path.relpath(path, root)
            for m in rx.finditer(text):
                found.setdefault(m.group(1), rel)
    return found


def main(argv):
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(here, "docs", "env_switches.tsv")
    old = {}
    old_text = ""
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            old_text = fh.read()
        for line in old_text.splitlines()[1:]:
            p = line.split("\t")
            if len(p) >= 4:
                old[(p[0], p[1])] = p[2]
    rows = []
    comps = [("nativeps5", here, True)]
    for a in args:
        if "=" not in a:
            sys.exit("gen_env_switches: expected name=dir, got %r" % a)
        n, d = a.split("=", 1)
        if not os.path.isdir(d):
            sys.exit("gen_env_switches: %s is not a folder" % d)
        comps.append((n, d, False))
    scanned = {c[0] for c in comps}
    for comp, root, shell_too in comps:
        for name, src in scan(root, shell_too).items():
            rows.append((name, comp, old.get((name, comp), ""), src))
    # keep rows of components that were not scanned this time
    for line in old_text.splitlines()[1:]:
        p = line.split("\t")
        if len(p) >= 4 and p[1] not in scanned:
            rows.append(tuple(p[:4]))
    rows.sort()
    text = "name\tcomponent\tmeaning\tsource\n" + "".join("\t".join(r) + "\n" for r in rows)
    if check:
        if text != old_text:
            print("gen_env_switches: docs/env_switches.tsv is stale; rerun without --check")
            return 1
        return 0
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("gen_env_switches: %d switches (%s)" % (
        len(rows), ", ".join("%s %d" % (c, sum(1 for r in rows if r[1] == c)) for c in sorted({r[1] for r in rows}))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
