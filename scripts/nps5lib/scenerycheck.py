#!/usr/bin/env python3
"""Read the logs of an automated in-game scenery-keys run and say whether the CHAIN worked (see tests/test_scenery_chain.py for the no-game version).

  scenerycheck.py <run.err> <runner console log> [--presses N]

The run: `nps5 run <id> ... --scenery-keys on --env KYTY_SCENE_KEYS_SCRIPT=3:6,25:6 2>&1 | tee console.log` (Kyty presses 6 itself at +3 s and +25 s).
Checks (each prints PASS/FAIL/INFO; exit 1 on a FAIL):
  press    run.err has `[scenery] key script: press ...` and `[scenery] seq N: ... flowers OFF` (Kyty took the key and wrote the state file)
  staged   console has `nps5 run: scenery: seq N staged` for every seq Kyty logged (the launcher's watcher repointed the live links)
  opened   INFO: `[ps4-vfs] overlay` lines in run.err after the first press (the runtime opened a level file through the live links; a level
           entered after the press shows the variant; before the press is the full file)
"""
import re, sys


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
