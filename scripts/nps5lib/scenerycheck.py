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
"""
import re, sys

SCENE_RE = re.compile(r"\[scenery\] scene (\S+): (\d+) shader pairs active \((F[+-])")


def check_reentry(err, scene="underwater_aerial_garden"):
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
    out.append(("INFO", "variant", "%d overlay open line(s) after the re-entry%s" % (len(ov), ": " + ov[0][:160] if ov else " (the runtime does not log opens: not logged)")))
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
        if len(a) != 1:
            print(__doc__); return 2
        res = check_reentry(open(a[0], errors="replace").read(), scene)
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
