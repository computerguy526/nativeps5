#!/usr/bin/env python3
"""Format check of components.lock: 7 ' | ' separated fields per line, known names, 40-hex commits for the
cloned components. Usage: scripts/ci/check_components_lock.py [path]"""
import re, sys

path = sys.argv[1] if len(sys.argv) > 1 else "components.lock"
CLONED = {"ps5recomp", "kyty", "anyps5"}
errs, names = [], []
for n, line in enumerate(open(path, encoding="utf-8"), 1):
    line = line.rstrip("\n")
    if not line.strip() or line.startswith("#"):
        continue
    f = [x.strip() for x in line.split("|")]
    if len(f) != 7:
        errs.append(f"{path}:{n}: {len(f)} fields, expected 7"); continue
    name, lic, up, base, repo, branch, commit = f
    names.append(name)
    if not re.fullmatch(r"[a-z0-9_]+", name): errs.append(f"{path}:{n}: bad name {name!r}")
    if not lic: errs.append(f"{path}:{n}: empty licence")
    if not up.startswith("https://"): errs.append(f"{path}:{n}: upstream is not an https URL")
    if name in CLONED:
        if not re.match(r"[0-9a-f]{40}\b", base): errs.append(f"{path}:{n}: upstream base is not a 40-hex commit")
        if not repo.startswith("${NPS5_FORK_BASE}/"): errs.append(f"{path}:{n}: repository must start with ${{NPS5_FORK_BASE}}/")
        if not re.fullmatch(r"v[0-9]+(\.[0-9]+){1,2}", branch): errs.append(f"{path}:{n}: the ref must be a release tag (vN.N or vN.N.N), not a branch")
        if not re.fullmatch(r"[0-9a-f]{40}", commit): errs.append(f"{path}:{n}: commit is not 40 hex")
dup = {x for x in names if names.count(x) > 1}
if dup: errs.append(f"{path}: duplicate names {sorted(dup)}")
missing = CLONED - set(names)
if missing: errs.append(f"{path}: missing components {sorted(missing)}")
for e in errs: print(e, file=sys.stderr)
print(f"components.lock: {'FAILED' if errs else 'ok'} ({len(names)} entries)")
sys.exit(1 if errs else 0)
