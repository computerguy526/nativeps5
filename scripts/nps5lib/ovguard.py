#!/usr/bin/env python3
"""ovguard: keeps the Metal override sets (games/<id>/metal-shaders.tsv, metal-gfx-pairs.tsv) reachable on the SHIPPED env.

Why: an override is found by KosmicKrisp's key = hash of the MSL text of the SPIR-V Kyty emits. Any Kyty codegen word that changes the
SPIR-V of a listed shader (KYTY_GPU_ACCURACY=low = fast math for every shader NOT in fast-math-deny.txt, KYTY_NATIVE_FMINMAX, ...) moves
the key and the override silently misses (no log line). INVARIANT: with KYTY_GPU_ACCURACY != high, the guest hash of every override row's
compute kernel / fragment stage is in games/<id>/fast-math-deny.txt.

Rows carry only KosmicKrisp keys, so the guest hash comes from a sidecar, games/<id>/metal-guests.tsv (hashes only, no code):
    format<TAB>nps5-metal-guests<TAB>1
    cs|fs<TAB><kk key, 16 hex><TAB><guest hash, 16 hex>        (one row per pair; a key may map to several guests)
Generate it from a cold run's guest -> KosmicKrisp key map (`stage guest kk [windows]` lines):
    python3 -I scripts/nps5lib/ovguard.py sidecar games/<id> MAP.tsv

Commands (python3 -I scripts/nps5lib/ovguard.py ...):
    check [games/<id> ...] [--strict] [--words]   static check (no sidecar: warning, error under --strict)
    sidecar games/<id> MAP.tsv                    write games/<id>/metal-guests.tsv from a guest->kk map
    summary games/<id> RUNLOG [--min-hit N]       "metal overrides: rows N, keys seen M, hit H" from a run's stderr; exit 3 when low
    envdump                                       print the composed env of this checkout for a run dir (env.dump)
Exit: 0 ok, 1 invariant broken, 2 usage, 3 low override hits."""
import os, re, subprocess, sys

HEX16 = re.compile(r"^[0-9a-f]{16}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
SIDECAR = "metal-guests.tsv"
SIDECAR_HEAD = ("format", "nps5-metal-guests", "1")
# Codegen words besides ACCURACY that change the SPIR-V of listed shaders and that no deny list covers. value-test -> why.
RISKY_WORDS = {
    "KYTY_NATIVE_FMINMAX": (lambda v, e: v == "1" and e.get("KYTY_NATIVE_FMINMAX_DENY") != "1",
                            "min/max lowering changes the SPIR-V of every shader (set KYTY_NATIVE_FMINMAX_DENY=1 to spare the deny list)"),
    "KYTY_UNIFORM_BUFFERS": (lambda v, e: v == "1", "constant-only buffers become UBOs: SPIR-V changes for every shader"),
    "KYTY_PS_PER_VERTEX": (lambda v, e: v not in ("", "auto"), "fragment parameter path changes the SPIR-V of pair fragments"),
    "KYTY_CB_SHADER_MASK_EXPORTS": (lambda v, e: v == "1", "colour export routing changes the SPIR-V of pair fragments"),
}


def read_rows(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return None


def override_keys(shaders_rows, pairs_rows):
    """-> {'cs': {kk key}, 'fs': {kk key}} of rows that carry our own text (a `kk` class row has '-' hashes: KosmicKrisp's own shader)."""
    out = {"cs": set(), "fs": set()}
    for r in shaders_rows or ():
        if len(r) >= 4 and HEX32.match(r[0]) and HEX16.match(r[2]) and r[3] != "-":
            out["cs"].add(r[2])
    for r in pairs_rows or ():
        if len(r) >= 9 and r[0] == "gfx" and HEX16.match(r[6]) and r[8] != "-":
            out["fs"].add(r[6])
    return out


def parse_sidecar(rows):
    """-> ({(kind, kk): {guest}}, [problems])"""
    m, bad = {}, []
    if not rows or tuple(rows[0][:3]) != SIDECAR_HEAD:
        return m, ["first row must be: format<TAB>nps5-metal-guests<TAB>1"]
    for i, r in enumerate(rows[1:], 2):
        if len(r) != 3 or r[0] not in ("cs", "fs") or not HEX16.match(r[1]) or not HEX16.match(r[2]):
            bad.append("row %d: expected cs|fs <16 hex kk key> <16 hex guest hash>" % i)
            continue
        m.setdefault((r[0], r[1]), set()).add(r[2])
    return m, bad


def parse_deny(rows_text):
    return {l.strip() for l in rows_text.splitlines() if HEX16.match(l.strip())}


def check_profile(keys, sidecar, deny, env, strict=False, words=True):
    """-> (errors, warnings). keys = override_keys(); sidecar = {(kind, kk): {guest}} or None; deny = set of guest hashes; env = {K: V}."""
    errs, warns = [], []
    nkeys = len(keys["cs"]) + len(keys["fs"])
    if not nkeys:
        return errs, warns
    acc = env.get("KYTY_GPU_ACCURACY", "high")
    if acc != "high":
        if sidecar is None:
            (errs if strict else warns).append(
                "KYTY_GPU_ACCURACY=%s and %d override keys, but no %s: cannot prove the keys survive fast math" % (acc, nkeys, SIDECAR))
        else:
            for kind in ("cs", "fs"):
                for kk in sorted(keys[kind]):
                    g = sidecar.get((kind, kk))
                    if not g:
                        errs.append("%s override key %s has no guest hash in %s (add it: every row needs one while ACCURACY=%s)" % (kind, kk, SIDECAR, acc))
                    elif not g <= deny:
                        errs.append("%s override key %s: guest %s is not in fast-math-deny.txt (ACCURACY=%s gives it fast math: the key moves, the override misses)"
                                    % (kind, kk, ",".join(sorted(g - deny)), acc))
    if sidecar is not None:
        known = {(k, v) for k in ("cs", "fs") for v in keys[k]}
        for k in sorted(set(sidecar) - known):
            warns.append("%s sidecar key %s has no override row (stale)" % k)
    if words:
        for w, (bad, why) in sorted(RISKY_WORDS.items()):
            if w in env and bad(env[w], env):
                (errs if strict else warns).append("%s=%s: %s; prove it key-neutral for the override set or deny-cover it" % (w, env[w], why))
    return errs, warns


def resolve_env(repo, gid):
    r = subprocess.run([sys.executable, "-I", os.path.join(repo, "games", "game.py"), gid, "--env", "--gpu", "kyty"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("game.py --env failed: " + r.stderr.strip()[:200])
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)


def load_profile(d):
    return (override_keys(read_rows(os.path.join(d, "metal-shaders.tsv")), read_rows(os.path.join(d, "metal-gfx-pairs.tsv"))),
            read_rows(os.path.join(d, SIDECAR)), os.path.join(d, "fast-math-deny.txt"))


def cmd_check(args):
    strict, words = "--strict" in args, "--words" in args or "--strict" in args
    dirs = [a for a in args if not a.startswith("--")] or sorted(
        os.path.join("games", x) for x in os.listdir("games") if re.match(r"^[A-Z]{4}\d{5}$", x))
    rc = 0
    for d in dirs:
        keys, side, denyp = load_profile(d)
        deny = parse_deny(open(denyp).read()) if os.path.isfile(denyp) else set()
        sidecar = None
        if side is not None:
            sidecar, bad = parse_sidecar(side)
            for b in bad:
                print("ovguard: %s: %s: %s" % (d, SIDECAR, b)); rc = 1
        repo = os.path.dirname(os.path.dirname(os.path.abspath(d)))
        env = resolve_env(repo, os.path.basename(os.path.abspath(d)))
        e, w = check_profile(keys, sidecar, deny, env, strict, words)
        for m in w:
            print("ovguard: %s: warning: %s" % (d, m))
        for m in e:
            print("ovguard: %s: ERROR: %s" % (d, m)); rc = 1
        print("ovguard: %s: %d cs + %d fs override keys, accuracy=%s, %d errors, %d warnings"
              % (d, len(keys["cs"]), len(keys["fs"]), env.get("KYTY_GPU_ACCURACY", "high"), len(e), len(w)))
    return rc


def cmd_sidecar(args):
    if len(args) != 2:
        return usage()
    d, mp = args
    keys = load_profile(d)[0]
    rows = set()
    # map columns: stage guest kk [windows]
    for r in read_rows(mp) or ():
        if len(r) >= 3 and r[0] in ("cs", "fs") and HEX16.match(r[1]) and HEX16.match(r[2]) and r[2] in keys[r[0]]:
            rows.add((r[0], r[2], r[1]))
    miss = {(k, v) for k in ("cs", "fs") for v in keys[k]} - {(a, b) for a, b, _ in rows}
    with open(os.path.join(d, SIDECAR), "w") as f:
        f.write("# nativeps5: guest shader hash of every Metal override key (hashes only, no shader code).\n"
                "# INVARIANT (scripts/nps5lib/ovguard.py check): while KYTY_GPU_ACCURACY is not high, every guest hash here is in fast-math-deny.txt.\n")
        f.write("\t".join(SIDECAR_HEAD) + "\n")
        for kind, kk, g in sorted(rows):
            f.write("%s\t%s\t%s\n" % (kind, kk, g))
    print("ovguard: wrote %d rows; %d override keys have no guest hash yet" % (len(rows), len(miss)))
    return 0


SEEN_RE = re.compile(r"\[kk-mslinject\] seen ([0-9a-f]{16})(?: len=\d+ \w+ canon=([0-9a-f]{16}))?")
OVR_RE = re.compile(r"\[kk-mslinject\] override ([0-9a-f]{16}) local_size")
GFX_RE = re.compile(r"\[kk-mslinject\] gfx override ([0-9a-f]{16})/([0-9a-f]{16}) [0-9.]+ ms")


def summary(keys, pair_rows, log):
    """-> (rows, seen, hit): rows = override rows of the set, seen = of their keys KosmicKrisp logged (own or canonical key),
    hit = override texts actually injected (compute kernels + graphics pairs)."""
    seen = set()
    for a, b in SEEN_RE.findall(log):
        seen.add(a)
        if b:
            seen.add(b)
    allk = keys["cs"] | keys["fs"]
    ovk = set(OVR_RE.findall(log))
    ovg = {(a, b) for a, b in GFX_RE.findall(log)}
    return len(keys["cs"]) + len(pair_rows), len(allk & seen), len(ovk & keys["cs"]) + len(ovg)


def cmd_summary(args):
    mh = None
    if "--min-hit" in args:
        i = args.index("--min-hit"); mh = int(args[i + 1]); del args[i:i + 2]
    if len(args) != 2:
        return usage()
    d, lp = args
    pairs = read_rows(os.path.join(d, "metal-gfx-pairs.tsv")) or []
    pr = {(r[5], r[6]) for r in pairs if len(r) >= 9 and r[0] == "gfx" and r[8] != "-"}
    keys = load_profile(d)[0]
    rows, seen, hit = summary(keys, pr, open(lp, errors="replace").read())
    line = "metal overrides: rows %d, keys seen %d, hit %d" % (rows, seen, hit)
    flag = ""
    if mh is not None and hit < mh:
        flag = "  LOW: hit %d < required %d (override keys moved? run `ovguard.py check --strict`; a WARM run prints no override lines, only COLD runs count)" % (hit, mh)
    elif seen and hit == 0:
        flag = "  LOW: keys seen but no override injected (warm cache run, or injection failing)"
    print(line + flag)
    return 3 if flag and mh is not None else 0


def cmd_envdump(args):
    repo = os.getcwd()
    gid = args[0] if args else "PPSA21564"
    for k, v in sorted(resolve_env(repo, gid).items()):
        print("%s=%s" % (k, v))
    return 0


def usage():
    print(__doc__); return 2


def main(argv):
    if not argv or argv[0] not in ("check", "sidecar", "summary", "envdump"):
        return usage()
    return {"check": cmd_check, "sidecar": cmd_sidecar, "summary": cmd_summary, "envdump": cmd_envdump}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
