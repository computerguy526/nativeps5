#!/usr/bin/env python3
"""Read the logs of an automated in-game scenery-keys run and say whether the CHAIN worked (see tests/test_scenery_chain.py for the no-game version).

  scenerycheck.py <run.err> <runner console log> [--presses N]

The run: `nps5 run <id> ... --scenery-keys on --env KYTY_SCENE_KEYS_SCRIPT=3:6,25:6 2>&1 | tee console.log` (Kyty presses 6 itself at +3 s and +25 s).
Checks (each prints PASS/FAIL/INFO; exit 1 on a FAIL):
  press    run.err has `[scenery] key script: press ...` and `[scenery] seq N: ... flowers OFF` (Kyty took the key and wrote the state file)
  staged   console has `nps5 run: scenery: seq N staged` for every seq Kyty logged (the launcher's watcher repointed the live links)
  opened   INFO: `[ps4-vfs] overlay` lines in run.err after the first press (the runtime opened a level file through the live links; a level
           entered after the press shows the variant; before the press is the full file)

  scenerycheck.py --reentry <run.err> [--scene NAME]     (the leave-planet / re-enter recipe, run.err only)
  press    a scripted press switched flowers OFF (`[scenery] seq N: flowers OFF`)
  left     after the press the run showed a scene other than NAME (the pad left the planet: world map, transition)
  reentry  NAME's scene line shows up AGAIN after the press and after leaving, with `F-` (the level was entered with the flowers-off variant)
  variant  INFO: `[ps4-vfs] overlay` open line after the re-entry (only when the runtime logs opens; else "not logged")
  The scene lines are `[scenery] scene NAME: N shader pairs active (F- G+ M+ O+ ...)`; the first one (F+) is the level boot before the press.

  Deterministic version (--strict, implied by --draws): needs the runtime's NPS5_VFS_OVERLAY_LOG=1 lines and the Kyty draw summaries
  (KYTY_FLIGHTREC=1 KYTY_FLIGHTREC_DRAWSUM=1 KYTY_FLIGHTREC_DUMP_ON=scene:NAME+6, see flightRec.h):
  scenerycheck.py --reentry <run.err> --scene NAME [--min-draws N] [--flower-pairs FILE] --draws <dir of *.draws.txt | files>   (--draws LAST)
  variant  PASS when an overlay open of the re-entry resolves to a variant with flowers switched off (`.../cats-flowers[+...]/...`), FAIL when
           every re-entry open resolves to the player's own file or none is logged (`[ps4-vfs] overlay open <rel> -> <resolved target>`)
  draws    PASS when the flower draws vanish: draws per flip before (scene entry #1 window) >= N (default 5) and <= 5 % of that after the
           re-entry (last entry window). Flower draws = the (vs,ps) pairs of --flower-pairs (lines `<vs hex> <ps hex>`), else every pair that was
           drawn >= N/20 per flip before and not at all after (the report lists them with their counts).
"""
import glob, os, re, sys

SCENE_RE = re.compile(r"\[scenery\] scene (\S+): (\d+) shader pairs active \((F[+-])")


OPEN_RE = re.compile(r"\[ps4-vfs\] overlay open (\S+) -> (\S+)")
OFF_VARIANT_RE = re.compile(r"/cats-(?:[^/+]+\+)*flowers(?:\+[^/]*)?/")   # `flowers` without `~N`: the category is OFF (a thinned one is `flowers~50`)


def parse_draws(text):
    """One Kyty draw summary (<dump>.draws.txt) -> dict(label, flips, draws, pairs={(vs, ps): count}); None when it is not one."""
    if not text.startswith("# kyty draw summary v1"):
        return None
    m = re.search(r"^# dump (\d+) why (\S+) flip (\d+) window_flips (\d+) window_ms (\d+) draws (\d+) pairs (\d+) label (\S+)", text, re.M)
    if not m:
        return None
    pairs = {(a, b): int(c) for a, b, c in re.findall(r"^D ([0-9a-f]{16}) ([0-9a-f]{16}) (\d+)$", text, re.M)}
    return dict(dump=int(m.group(1)), why=m.group(2), flips=int(m.group(4)), ms=int(m.group(5)), draws=int(m.group(6)), label=m.group(8), pairs=pairs)


def load_draws(paths):
    """Files or directories -> parsed summaries sorted by dump number."""
    files = []
    for p in paths:
        files += sorted(glob.glob(os.path.join(p, "*.draws.txt"))) if os.path.isdir(p) else [p]
    out = [d for d in (parse_draws(open(f, errors="replace").read()) for f in files) if d]
    return sorted(out, key=lambda d: d["dump"])


def load_pairs(text):
    return {(a.lower().zfill(16), b.lower().zfill(16)) for a, b in re.findall(r"^\s*([0-9a-fA-F]{1,16})[\s,/]+([0-9a-fA-F]{1,16})\s*$", text, re.M)}


def check_draws(sums, scene, min_draws=5, flower_pairs=None):
    """(c): the flower draws per flip before (first entry of `scene`) and after (last entry). -> (status, message)."""
    ent = [d for d in sums if re.fullmatch(r"scene:%s#\d+" % re.escape(scene), d["label"])]
    if len(ent) < 2:
        return ("FAIL", "need the draw summaries of 2 entries of %s (labels scene:%s#1, #2), found %d (%s)" % (scene, scene, len(ent), ", ".join(d["label"] for d in sums) or "none"))
    b, a = ent[0], ent[-1]
    if b["flips"] < 1 or a["flips"] < 1:
        return ("FAIL", "a window has no flips (before %d, after %d)" % (b["flips"], a["flips"]))
    rb = {k: v / b["flips"] for k, v in b["pairs"].items()}
    ra = {k: v / a["flips"] for k, v in a["pairs"].items()}
    if flower_pairs:
        fam = sorted(flower_pairs); src = "%d given flower pairs" % len(fam)
    else:
        fam = sorted(k for k, v in rb.items() if v >= min_draws / 20.0 and k not in ra); src = "%d pairs drawn before and gone after" % len(fam)
    before = sum(rb.get(k, 0) for k in fam); after = sum(ra.get(k, 0) for k in fam)
    ok = before >= min_draws and after <= 0.05 * before
    top = ", ".join("%s/%s %.1f->%.1f" % (k[0][:8], k[1][:8], rb.get(k, 0), ra.get(k, 0)) for k in sorted(fam, key=lambda k: -rb.get(k, 0))[:4])
    return ("PASS" if ok else "FAIL", "flower draws/flip %.1f (%s#1, %d flips, %d draws) -> %.1f (%s#%s, %d flips, %d draws), %s; need before >= %g and after <= 5%%; top: %s" % (
        before, b["label"], b["flips"], b["draws"], after, a["label"], a["label"].split("#")[-1], a["flips"], a["draws"], src, min_draws, top or "-"))


def check_reentry(err, scene="underwater_aerial_garden", strict=False, draws=None, min_draws=5, flower_pairs=None):
    lines = err.splitlines()
    press = next((i for i, l in enumerate(lines) if re.search(r"\[scenery\] seq \d+: flowers OFF", l)), None)
    out = [("PASS" if press is not None else "FAIL", "press", "flowers OFF state write %s" % ("at log line %d" % (press + 1) if press is not None else "not found"))]
    sc = []                                   # (line, name, F flag) with consecutive duplicates collapsed
    for i, l in enumerate(lines):
        m = SCENE_RE.search(l)
        if m and (not sc or sc[-1][1] != m.group(1)):
            sc.append((i, m.group(1), m.group(3)))
    first = next((k for k, x in enumerate(sc) if x[1] == scene), None)
    after = [x for x in sc if press is not None and x[0] > press]
    left = [x for x in after if x[1] != scene]
    out.append(("PASS" if left else "FAIL", "left", "scenes after the press other than %s: %s" % (scene, ", ".join(x[1] for x in left[:6]) or "none")))
    back = None
    if left:
        back = next((x for x in after if x[1] == scene and x[0] > left[0][0]), None)
    out.append(("PASS" if back and back[2] == "F-" else "FAIL", "reentry",
                "%s re-entered at log line %s with %s (boot entry: %s)" % (scene, back[0] + 1 if back else "-", back[2] if back else "never",
                                                                       sc[first][2] if first is not None else "none")))
    ov = [l for l in lines[back[0]:] if "[ps4-vfs] overlay" in l and "mounted" not in l] if back else []
    strict = strict or draws is not None
    if not strict:
        out.append(("INFO", "variant", "%d overlay open line(s) after the re-entry%s" % (len(ov), ": " + ov[0][:160] if ov else " (the runtime does not log opens: not logged)")))
        return out
    opens = [OPEN_RE.search(l) for l in lines[back[0]:]] if back else []
    opens = [m for m in opens if m]
    mine = [m for m in opens if scene in m.group(1)] or opens          # this level's files when the path names it
    good = [m for m in mine if OFF_VARIANT_RE.search(m.group(2))]
    if good:
        out.append(("PASS", "variant", "%d of %d overlay open(s) after the re-entry resolve to a flowers-off variant, e.g. %s -> %s" % (len(good), len(mine), good[0].group(1), good[0].group(2))))
    elif mine:
        out.append(("FAIL", "variant", "%d overlay open(s) after the re-entry, none in a flowers-off variant: %s -> %s" % (len(mine), mine[0].group(1), mine[0].group(2))))
    else:
        out.append(("FAIL", "variant", "no `[ps4-vfs] overlay open` line after the re-entry (run with NPS5_VFS_OVERLAY_LOG=1 on a runtime that has it)"))
    if draws is not None:
        st, msg = check_draws(draws, scene, min_draws, flower_pairs)
        out.append((st, "draws", msg))
    return out


def check(err, console, presses=1):
    out = []
    kp = re.findall(r"\[scenery\] key script: press (\d+) at ([\d.]+) s", err)
    seqs = [int(m) for m in re.findall(r"\[scenery\] seq (\d+): ", err)]
    staged = {int(m) for m in re.findall(r"nps5 run: scenery: seq (\d+) staged", console)}
    out.append(("PASS" if len(kp) >= presses and len(seqs) >= presses else "FAIL", "press",
                "%d scripted press(es), %d state write(s) logged by Kyty (need %d)" % (len(kp), len(seqs), presses)))
    miss = [s for s in seqs if s not in staged]
    out.append(("PASS" if seqs and not miss else "FAIL", "staged", "seq %s staged by the launcher%s" % (sorted(staged), "; MISSING %s" % miss if miss else "")))
    lines = err.splitlines()
    first = next((i for i, l in enumerate(lines) if "[scenery] seq " in l), None)
    ov = [l for l in lines[first:] if "[ps4-vfs] overlay" in l] if first is not None else []
    out.append(("INFO", "opened", "%d overlay open line(s) after the first press%s" % (len(ov), ": " + ov[0][:160] if ov else "")))
    return out


def main(argv):
    if "--reentry" in argv:
        a = [x for x in argv if x != "--reentry"]
        scene = "underwater_aerial_garden"
        if "--scene" in a:
            k = a.index("--scene"); scene = a[k + 1]; del a[k:k + 2]
        strict = "--strict" in a
        a = [x for x in a if x != "--strict"]
        min_draws = 5.0; fp = None; dr = None
        if "--min-draws" in a:
            k = a.index("--min-draws"); min_draws = float(a[k + 1]); del a[k:k + 2]
        if "--flower-pairs" in a:
            k = a.index("--flower-pairs"); fp = load_pairs(open(a[k + 1], errors="replace").read()); del a[k:k + 2]
        if "--draws" in a:
            k = a.index("--draws"); dr = load_draws(a[k + 1:]); del a[k:]
        if len(a) != 1:
            print(__doc__); return 2
        res = check_reentry(open(a[0], errors="replace").read(), scene, strict, dr, min_draws, fp)
        for st, name, msg in res: print("%-4s %-7s %s" % (st, name, msg))
        return 1 if any(r[0] == "FAIL" for r in res) else 0
    a = [x for x in argv if not x.startswith("--")]
    n = int(argv[argv.index("--presses") + 1]) if "--presses" in argv else 1
    a = [x for x in a if x != str(n)] if "--presses" in argv else a
    if len(a) != 2:
        print(__doc__); return 2
    res = check(open(a[0], errors="replace").read(), open(a[1], errors="replace").read(), n)
    for st, name, msg in res: print("%-4s %-7s %s" % (st, name, msg))
    return 1 if any(r[0] == "FAIL" for r in res) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
