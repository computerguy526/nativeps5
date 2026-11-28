#!/usr/bin/env python3
"""nps5 indreg: the in-game regression check for the indirect-draw null-base crash (indcrash 2026-11-28). Standard library only.

The v1.4.4 RC crashed (SIGBUS on kyty:Thread_Gpu in CommandProcessor::ExecDrawIndirect) right after the first
"[indirect] draw SKIPPED (no base)" line, ~15 s into a level the player had never entered before: the title set the
indirect-args base to a null object's member and Kyty read the record at guest 0x20. Kyty fixes it (null-page base and
record addresses are skipped, never read); this module judges a player-path run into that level so the release
rehearsal (rehearse.sh r8) keeps proving it.

  indreg.py check   <run dir> --scene NAME           verdict on an existing run dir (offline): exit 0 PASS, 1 FAIL
  indreg.py prepare --fixture DIR --dest DIR            copy the USER save fixture (read-only source) to a scratch save dir

PASS needs: the scene was entered ("[scene-open] NAME" / "[level-open] NAME" / "[scenery] scene NAME:"), run.json verdict
DONE_UNTIL (the watcher held the scene for the until's +N s), no FATAL line, no crash report. Any "[indirect] ... SKIPPED"
line followed by a FATAL is named as the indcrash class. The skip counts (front "no base", back "args in null page") are
reported: they show the guarded path ran.
prepare never writes into the fixture, refuses a live save dir (a name without ".bak-"), a destination that exists, and a
destination inside a git checkout (the save is game data: never in a commit)."""
import json, os, re, shutil, subprocess, sys

FATAL = re.compile(r"^FATAL:|^Signal \d+ on host thread|crash report", re.M)
SKIP_FRONT = re.compile(r"^\[indirect\] draw SKIPPED \(no base\)", re.M)
SKIP_BACK = re.compile(r"^\[indirect\] .*SKIPPED \(args in null page\)", re.M)
SKIP_ANY = re.compile(r"^\[indirect\] .*SKIPPED", re.M)


def entered(text, scene):
    s = re.escape(scene)
    return re.search(r"^\[(?:scene-open|level-open)\] %s\b|^\[scenery\] scene %s:" % (s, s), text, re.M) is not None


def judge(text, run_json, scene):
    """-> (ok, problems, info). Pure: text = run.err, run_json = dict (or None)."""
    problems, info = [], {}
    info["front_skips"] = len(SKIP_FRONT.findall(text))
    info["back_skips"] = len(SKIP_BACK.findall(text))
    if not entered(text, scene):
        problems.append("scene %s never entered" % scene)
    fatal = FATAL.search(text)
    if fatal:
        skip = None
        for m in SKIP_ANY.finditer(text, 0, fatal.start()):
            skip = m
        if skip is not None:
            problems.append("indcrash class: an [indirect] SKIPPED line followed by a FATAL (%s)" % text[skip.start():text.find("\n", skip.start())][:160])
        else:
            problems.append("FATAL: %s" % text[fatal.start():text.find("\n", fatal.start())][:160])
    verdict = (run_json or {}).get("verdict")
    if verdict != "DONE_UNTIL":
        problems.append("run verdict %s (DONE_UNTIL needed: the scene held for the until's +N s)" % verdict)
    return not problems, problems, info


def check(run_dir, scene):
    try:
        text = open(os.path.join(run_dir, "run.err"), errors="replace").read()
    except OSError as e:
        return False, ["no run.err: %s" % e], {}
    try:
        rj = json.load(open(os.path.join(run_dir, "run.json")))
    except (OSError, ValueError) as e:
        rj = None
        ok, p, info = judge(text, rj, scene)
        return False, ["no run.json: %s" % e] + p, info
    return judge(text, rj, scene)


def in_git_checkout(path):
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def prepare(fixture, dest):
    """Copy fixture -> dest. -> list of problems (empty = done)."""
    fx, ds = os.path.realpath(fixture), os.path.realpath(dest)   # realpath both: a symlinked parent cannot hide the fixture
    if not os.path.isdir(fx):
        return ["fixture %s is not a directory" % fixture]
    if ".bak-" not in os.path.basename(fx):
        return ["fixture %s is not a backup copy (*.bak-*): the live save is never used" % fixture]
    if not os.path.isdir(os.path.join(fx, "savedata")):
        return ["fixture %s has no savedata/" % fixture]
    if os.path.exists(ds):
        return ["destination %s exists (never overwritten)" % dest]
    if ds == fx or ds.startswith(fx + os.sep):
        return ["destination inside the fixture"]
    repo = in_git_checkout(os.path.dirname(ds))
    if repo:
        return ["destination inside the git checkout %s (a save is game data)" % repo]
    os.makedirs(os.path.dirname(ds), exist_ok=True)
    if sys.platform == "darwin" and subprocess.run(["cp", "-c", "-R", fx, ds]).returncode == 0:   # APFS clone
        return []
    shutil.copytree(fx, ds, symlinks=True)
    return []


def main(argv):
    if len(argv) >= 2 and argv[0] == "check":
        run_dir, scene, i = argv[1], None, 2
        while i < len(argv):
            if argv[i] == "--scene":
                scene = argv[i + 1]; i += 2
            else:
                print(__doc__); return 2
        if not scene:
            print("check needs --scene NAME"); return 2
        ok, problems, info = check(run_dir, scene)
        print("[indreg] %s %s: scene %s, front skips %d, back skips %d%s" % (
            "PASS" if ok else "FAIL", run_dir, scene, info.get("front_skips", 0), info.get("back_skips", 0),
            "" if ok else ": " + "; ".join(problems)))
        return 0 if ok else 1
    if len(argv) == 5 and argv[0] == "prepare" and argv[1] == "--fixture" and argv[3] == "--dest":
        problems = prepare(argv[2], argv[4])
        print("[indreg] prepare %s" % ("done: " + argv[4] if not problems else "REFUSED: " + "; ".join(problems)))
        return 0 if not problems else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
