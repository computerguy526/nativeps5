#!/usr/bin/env python3
"""ovguard: keeps the Metal override sets (games/<id>/metal-shaders.tsv, metal-gfx-pairs.tsv) reachable on the SHIPPED env.

Why: an override is found by KosmicKrisp's key = hash of the MSL text of the SPIR-V Kyty emits. Any Kyty codegen word that changes the
SPIR-V of a listed shader (KYTY_GPU_ACCURACY=low = fast math for every shader NOT in fast-math-deny.txt, KYTY_NATIVE_FMINMAX, ...) moves
the key and the override silently misses (no log line). INVARIANT: with KYTY_GPU_ACCURACY != high, the guest hash of every override row's
compute kernel / fragment stage is in games/<id>/fast-math-deny.txt.

Rows carry only KosmicKrisp keys, so the guest hash comes from a sidecar, games/<id>/metal-guests.tsv (hashes only, no code):
    format<TAB>nps5-metal-guests<TAB>1
    cs|fs|vs<TAB><kk key, 16 hex><TAB><guest hash, 16 hex>        (one row per pair; a key may map to several guests)
Generate it from a cold run's guest -> KosmicKrisp key map (`stage guest kk [windows]` lines):
    python3 -I scripts/nps5lib/ovguard.py sidecar games/<id> MAP.tsv

Commands (python3 -I scripts/nps5lib/ovguard.py ...):
    check [games/<id> ...] [--strict] [--words]   static check (no sidecar: warning, error under --strict)
    sidecar-xf games/<id> XFKEYS.txt...           same from `kyty_msl_dump --programs <programs.bin> --xf-keys` output (digest -> guest; no run needed)
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
    out = {"cs": set(), "fs": set(), "vs": set()}
    for r in shaders_rows or ():
        if len(r) >= 4 and HEX32.match(r[0]) and HEX16.match(r[2]) and r[3] != "-":
            out["cs"].add(r[2])
    for r in pairs_rows or ():
        if len(r) >= 9 and r[0] == "gfx" and HEX16.match(r[6]) and r[8] != "-":
            out["fs"].add(r[6])
            out["vs"].add(r[5])
    return out


def parse_sidecar(rows):
    """-> ({(kind, kk): {guest}}, [problems])"""
    m, bad = {}, []
    if not rows or tuple(rows[0][:3]) != SIDECAR_HEAD:
        return m, ["first row must be: format<TAB>nps5-metal-guests<TAB>1"]
    for i, r in enumerate(rows[1:], 2):
        if len(r) != 3 or r[0] not in ("cs", "fs", "vs") or not HEX16.match(r[1]) or not HEX16.match(r[2]):
            bad.append("row %d: expected cs|fs|vs <16 hex kk key> <16 hex guest hash>" % i)
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
    # What alters SPIR-V (source: spirvEmitterAlu.cpp FastMathLevel/FastMathVertex, CodegenOptions.cpp): KYTY_GPU_ACCURACY normal|low and
    # KYTY_FAST_MATH>=1 give pixel/compute shaders (cs, fs) fast math unless denied; the vertex stage only with KYTY_FAST_MATH_VS=1 or KYTY_FAST_MATH>=3.
    acc = env.get("KYTY_GPU_ACCURACY", "high")
    fm = _int(env.get("KYTY_FAST_MATH", "0"))
    fsc = acc in ("normal", "low") or fm >= 1
    vs = env.get("KYTY_FAST_MATH_VS", "0") not in ("", "0") or fm >= 3
    kinds = (["cs", "fs"] if fsc else []) + (["vs"] if vs else [])
    why = "ACCURACY=%s FAST_MATH=%s FAST_MATH_VS=%s" % (acc, fm, env.get("KYTY_FAST_MATH_VS", "0"))
    if kinds:
        need = sum(len(keys[k]) for k in kinds)
        if need and sidecar is None:
            (errs if strict else warns).append(
                "%s and %d override keys of stages %s, but no %s: cannot prove the keys survive fast math" % (why, need, "/".join(kinds), SIDECAR))
        elif need:
            for kind in kinds:
                for kk in sorted(keys[kind]):
                    g = sidecar.get((kind, kk))
                    if not g:
                        errs.append("%s override key %s has no guest hash in %s (add it: every row needs one while %s)" % (kind, kk, SIDECAR, why))
                    elif not g <= deny:
                        errs.append("%s override key %s: guest %s is not in fast-math-deny.txt (%s gives it fast math: the key moves, the override misses)"
                                    % (kind, kk, ",".join(sorted(g - deny)), why))
    if sidecar is not None:
        known = {(k, v) for k in ("cs", "fs", "vs") for v in keys[k]}
        for k in sorted(set(sidecar) - known):
            warns.append("%s sidecar key %s has no override row (stale)" % k)
    if words:
        for w, (bad, why) in sorted(RISKY_WORDS.items()):
            if w in env and bad(env[w], env):
                (errs if strict else warns).append("%s=%s: %s; prove it key-neutral for the override set or deny-cover it" % (w, env[w], why))
    return errs, warns


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def resolve_env(repo, gid):
    r = subprocess.run([sys.executable, "-I", os.path.join(repo, "games", "game.py"), gid, "--env", "--gpu", "kyty"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("game.py --env failed: " + r.stderr.strip()[:200])
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)


def load_profile(d):
    return (override_keys(read_rows(os.path.join(d, "metal-shaders.tsv")), read_rows(os.path.join(d, "metal-gfx-pairs.tsv"))),
            read_rows(os.path.join(d, SIDECAR)), os.path.join(d, "fast-math-deny.txt"))


BASELINE = "override-guard.baseline"


def parse_baseline(text):
    """-> ({token: reason}, [bad lines]). Line: `cs:<kk key>|fs:<kk key>|vs:<kk key>|word:<KYTY_NAME> <reason>`; the reason is required. Shrink-only: an entry
    that no longer matches a finding is itself an error (remove it), and a new finding is never added here by the check."""
    out, bad = {}, []
    for n, l in enumerate(text.splitlines(), 1):
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        tok, _, reason = l.partition(" ")
        if not re.match(r"^((cs|fs|vs):[0-9a-f]{16}|word:KYTY_[A-Z0-9_]+)$", tok) or not reason.strip():
            bad.append("line %d: expected `cs:<key>|fs:<key>|vs:<key>|word:<NAME> <reason>`" % n)
        else:
            out[tok] = reason.strip()
    return out, bad


def finding_token(msg):
    m = re.match(r"^(cs|fs|vs) override key ([0-9a-f]{16})", msg)
    if m:
        return "%s:%s" % (m.group(1), m.group(2))
    m = re.match(r"^(KYTY_[A-Z0-9_]+)=", msg)
    return "word:%s" % m.group(1) if m else None


def apply_baseline(errs, base, words_checked=True):
    """-> (remaining errors, waived [(msg, reason)], stale errors). Only the keys/words the baseline names are waived."""
    left, waived, hit = [], [], set()
    for m in errs:
        t = finding_token(m)
        if t in base:
            waived.append((m, base[t])); hit.add(t)
        else:
            left.append(m)
    stale = ["baseline entry %s (%s) matches no finding any more: remove it (the baseline only shrinks)" % (t, r)
             for t, r in sorted(base.items()) if t not in hit and (words_checked or not t.startswith("word:"))]
    return left, waived, stale


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
        bp = os.path.join(d, BASELINE)
        if os.path.isfile(bp):
            base, bad = parse_baseline(open(bp).read())
            for b in bad:
                e.append("%s: %s" % (BASELINE, b))
            e, waived, stale = apply_baseline(e, base, words)
            e += stale
            for m, r in waived:
                print("ovguard: %s: baseline: %s [%s]" % (d, m.split(":")[0][:60], r))
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
        if len(r) >= 3 and r[0] in ("cs", "fs", "vs") and HEX16.match(r[1]) and HEX16.match(r[2]) and r[2] in keys[r[0]]:
            rows.add((r[0], r[2], r[1]))
    miss = {(k, v) for k in ("cs", "fs", "vs") for v in keys[k]} - {(a, b) for a, b, _ in rows}
    write_sidecar(d, rows)
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


def xf_sidecar_rows(compute_rows, gfx_rows, xf_lines):
    """-> {(kind, kk, guest)} from `kyty_msl_dump --programs <play cache programs.bin> --xf-keys` lines (`cs|ps|vs <guest> <digest>`):
    a row's program digest (compute: column 1, pair fragment: column 4) names its guest hash. The method behind the 153-hash deny list."""
    g = {}
    for l in xf_lines:
        f = l.split()
        if len(f) == 3 and f[0] in ("cs", "ps", "vs") and HEX16.match(f[1]) and HEX32.match(f[2]):
            g.setdefault((f[0], f[2]), set()).add(f[1])
    rows = set()
    for r in compute_rows or ():
        if len(r) >= 4 and HEX32.match(r[0]) and HEX16.match(r[2]) and r[3] != "-":
            rows |= {("cs", r[2], x) for x in g.get(("cs", r[0]), ())}
    for r in gfx_rows or ():
        if len(r) >= 9 and r[0] == "gfx" and HEX32.match(r[3]) and HEX16.match(r[6]) and r[8] != "-":
            rows |= {("fs", r[6], x) for x in g.get(("ps", r[3]), ())}
        if len(r) >= 9 and r[0] == "gfx" and HEX32.match(r[1]) and HEX16.match(r[5]) and r[7] != "-":
            rows |= {("vs", r[5], x) for x in g.get(("vs", r[1]), ())}
    return rows


def write_sidecar(d, rows):
    with open(os.path.join(d, SIDECAR), "w") as f:
        f.write("# nativeps5: guest shader hash of every Metal override key (hashes only, no shader code).\n"
                "# INVARIANT (scripts/nps5lib/ovguard.py check): while KYTY_GPU_ACCURACY is not high, every guest hash here is in fast-math-deny.txt.\n")
        f.write("\t".join(SIDECAR_HEAD) + "\n")
        for kind, kk, g in sorted(rows):
            f.write("%s\t%s\t%s\n" % (kind, kk, g))


def cmd_sidecar_xf(args):
    if len(args) < 2:
        return usage()
    d = args[0]
    lines = []
    for p in args[1:]:
        with open(p, errors="replace") as f:
            lines += f.read().splitlines()
    rows = xf_sidecar_rows(read_rows(os.path.join(d, "metal-shaders.tsv")), read_rows(os.path.join(d, "metal-gfx-pairs.tsv")), lines)
    keys = load_profile(d)[0]
    miss = {(k, v) for k in ("cs", "fs") for v in keys[k]} - {(a, b) for a, b, _ in rows}
    write_sidecar(d, rows)
    print("ovguard: wrote %d rows; %d override keys have no guest hash yet" % (len(rows), len(miss)))
    return 0 if not miss else 1


def cmd_envdump(args):
    repo = os.getcwd()
    gid = args[0] if args else "PPSA21564"
    for k, v in sorted(resolve_env(repo, gid).items()):
        print("%s=%s" % (k, v))
    return 0


def usage():
    print(__doc__); return 2


def main(argv):
    if not argv or argv[0] not in ("check", "sidecar", "sidecar-xf", "summary", "envdump"):
        return usage()
    return {"check": cmd_check, "sidecar": cmd_sidecar, "sidecar-xf": cmd_sidecar_xf, "summary": cmd_summary, "envdump": cmd_envdump}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
