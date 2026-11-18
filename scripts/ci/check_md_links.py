#!/usr/bin/env python3
"""Internal markdown link check: every relative [text](path#anchor) in tracked *.md files must point at a tracked
file or directory (anchors are not checked, external URLs are ignored). Usage: scripts/ci/check_md_links.py"""
import os, re, subprocess, sys

files = subprocess.check_output(["git", "ls-files", "-z", "*.md"], text=True).split("\0")
tracked = set(subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0"))
dirs = {os.path.dirname(t) for t in tracked}
dirs |= {os.path.dirname(d) for d in list(dirs)}
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
bad = 0
for f in filter(None, files):
    if not os.path.isfile(f): continue
    fence = False
    for n, line in enumerate(open(f, encoding="utf-8", errors="replace"), 1):
        if line.lstrip().startswith("```"): fence = not fence
        if fence: continue
        line = re.sub(r"`[^`]*`", "", line)
        for m in LINK.finditer(line):
            t = m.group(1).split("#")[0].split("?")[0]
            if not t or re.match(r"[a-z][a-z0-9+.-]*:", t) or t.startswith("//"): continue
            p = os.path.normpath(os.path.join(os.path.dirname(f), t.lstrip("/") if t.startswith("/") else t))
            if t.startswith("/"): p = os.path.normpath(t.lstrip("/"))
            if p in tracked or p.rstrip("/") in dirs or p == ".": continue
            print(f"{f}:{n}: broken link {m.group(1)}", file=sys.stderr); bad += 1
print(f"markdown links: {'FAILED, %d broken' % bad if bad else 'ok'}")
sys.exit(1 if bad else 0)
