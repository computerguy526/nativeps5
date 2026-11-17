#!/usr/bin/env python3
"""nps5 log: read a noisy run log the easy way. Python 3, standard library only.

  nps5 log <run|file.err> [--summary] [--rate [FIRST,EVERY]] [--level E|W|I|D] [--grep RE] [--out FILE]

Default: the condensed log (head, tail, every warning/error line, one sample of each repeated line with its count) and the
SUMMARY block. --summary prints only the block; --rate FIRST,EVERY (default 3,50) prints the whole log with rate limiting
(the first FIRST lines of each kind, then every EVERYth, then a "(xK repeated)" line per kind), keeping the order;
--level hides lines below the level (E > W > I > D). The log itself is never changed, so tools that parse it keep working.

Log line convention (docs/reference/logging.md): `[subsystem] LEVEL message`, LEVEL in E, W, I, D; a line without a level
token is I, and a line saying ERROR / FATAL / WARN / [RED] / crash / assert is read as E or W (older lines). NPS5_LOG=
subsys:level,... (and a bare level, e.g. NPS5_LOG=W) is read by the runtime emitters to raise or lower a subsystem.
The SUMMARY block: verdict and wall time, fps p50 and frame-time p95 (from `fps: N` and `[framerate] ... measured N fps`),
shader compiles still live, stalls, guard lines, crashes / asserts, warnings and errors, and the settings of the run.
Exit codes: 0 ok, 1 nothing to read, 2 usage."""
from __future__ import annotations

import json
import os
import re
import sys

LEVELS = "DIWE"
LOG_HEAD, LOG_TAIL, LOG_SAMPLES, LOG_IMPORTANT_REPEAT = 60, 120, 1200, 3
IMPORTANT = re.compile(r"\bWARN|\bERROR|\bFATAL|\[RED\]|\bcrash|\bassert|\babort|\bpanic|SIGSEGV|SIGABRT|\bwatchdog|\bguard\b|"
                       r"self memory limit|not readable|Metal compiler|\bstall(?:ed)?\b|\bfailed\b|\bI/O error|^\[[^\]]+\] [EW] ", re.I)
_NUM = re.compile(r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{8,}\b|\d+(?:\.\d+)?")
TAGGED = re.compile(r"^\[([A-Za-z0-9_.+-]+)\] (?:([EWID]) )?")
ERR_WORDS = re.compile(r"\bFATAL\b|\bERROR\b|\[RED\]|\bcrash(?:ed)?\b|\bassert|\babort|\bpanic|SIGSEGV|SIGABRT", re.I)
WARN_WORDS = re.compile(r"\bWARN(?:ING)?\b|\bfailed\b|\bnot readable\b|\bstall(?:ed)?\b", re.I)
CRASH = re.compile(r"\bFATAL\b|\bcrash(?:ed)?\b|\bassert|\babort|\bpanic|SIGSEGV|SIGABRT|Metal compiler", re.I)
FPS_A = re.compile(r"\bfps: (\d+(?:\.\d+)?)")
FPS_B = re.compile(r"\[framerate\] frame \d+ measured (\d+(?:\.\d+)?) fps")
LIVE = re.compile(r"compiling shaders? .*?\+ live (\d+)|compiling shader \(live\) (\d+)")
STALL = re.compile(r"\bstall(?:ed)?\b|\bhitch\b|frame took \d{3,} ms", re.I)
GUARD = re.compile(r"\[RED\]|exiting (?:cleanly|with code)|SIGKILL|\bkilled\b|watchdog (?:fired|killed|timeout)|queue-error exit [1-9]|refus|memory pressure level|timed out", re.I)
VERDICT = re.compile(r"verdict=(\S+)(?: wall=(\S+))?")


def norm(line: str) -> str:
    return _NUM.sub("N", line.strip())[:200]


def level_of(line: str) -> str:
    """E, W, I or D of a log line: its own token if `[subsys] X msg`, else inferred from the words."""
    m = TAGGED.match(line)
    if m and m.group(2):
        return m.group(2)
    if ERR_WORDS.search(line):
        return "E"
    if WARN_WORDS.search(line):
        return "W"
    return "I"


def parse_level(s: str, default: str = "I") -> str:
    s = (s or "").strip().upper()[:1]
    return s if s and s in LEVELS else default


def subsystem_of(line: str):
    m = TAGGED.match(line)
    return m.group(1) if m else None


def filter_level(lines, level: str):
    lo = LEVELS.index(parse_level(level, "D"))
    return [l for l in lines if LEVELS.index(level_of(l)) >= lo]


def trim_log(text: str, head: int = LOG_HEAD, tail: int = LOG_TAIL, samples: int = LOG_SAMPLES) -> str:
    """Log text -> head + tail + every important line (the same line at most LOG_IMPORTANT_REPEAT times) + the first line of
    every other kind (numbers and hashes normalised), then a table of what repeated and how often."""
    lines = text.splitlines()
    n = len(lines)
    count, imp_count, keep, budget = {}, {}, [], samples
    for i, l in enumerate(lines):
        k = norm(l)
        count[k] = count.get(k, 0) + 1
        if i < head or i >= n - tail:
            keep.append(i)
        elif IMPORTANT.search(l):
            imp_count[k] = imp_count.get(k, 0) + 1
            if imp_count[k] <= LOG_IMPORTANT_REPEAT:
                keep.append(i)
        elif count[k] == 1 and budget > 0:
            keep.append(i); budget -= 1
    out, last = [], -1
    for i in keep:
        if i != last + 1:
            out.append("[... %d line(s) not shown ...]" % (i - last - 1))
        out.append(lines[i]); last = i
    if last < n - 1:
        out.append("[... %d line(s) not shown ...]" % (n - 1 - last))
    rep = sorted(((c, k) for k, c in count.items() if c > 1), reverse=True)[:60]
    if rep:
        out.append("")
        out.append("--- repeated lines (numbers and hashes shown as N): count x line ---")
        out += ["(x%d repeated) %s" % (c, k) for c, k in rep]
    out.append("--- %d line(s) in the log, %d shown ---" % (n, len(keep)))
    return "\n".join(out) + "\n"


def rate_limit(lines, first: int = 3, every: int = 50):
    """The whole log in order, but of each kind of line (numbers and hashes normalised) only the first `first`, then every
    `every`th; then one "(xK repeated)" line per kind that was cut. Warnings and errors are never cut."""
    seen, out = {}, []
    for l in lines:
        k = norm(l)
        c = seen[k] = seen.get(k, 0) + 1
        if level_of(l) in "EW" or c <= first or (every > 0 and c % every == 0):
            out.append(l)
    for k, c in seen.items():
        shown = min(c, first) + (c // every if every > 0 and c > first else 0)
        if c > shown and c > first:
            out.append("(x%d repeated, %d shown) %s" % (c, shown, k))
    return out


def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def summary(text: str, rj: dict | None = None, out_text: str = "") -> str:
    rj = rj if isinstance(rj, dict) else {}
    fps, live, stalls, guard, crash, nerr, nwarn = [], 0, 0, [], [], 0, 0
    lines = text.splitlines()
    for l in lines:
        for rx in (FPS_A, FPS_B):
            m = rx.search(l)
            if m and float(m.group(1)) > 0:
                fps.append(float(m.group(1)))
        m = LIVE.search(l)
        if m:
            live = max(live, int(m.group(1) or m.group(2)))
        if STALL.search(l):
            stalls += 1
        lv = level_of(l)
        nerr += lv == "E"; nwarn += lv == "W"
        if CRASH.search(l) and len(crash) < 5 and l.strip() not in crash:
            crash.append(l.strip()[:160])
        if GUARD.search(l):
            k = norm(l)
            if k not in [norm(g) for g in guard] and len(guard) < 5:
                guard.append(l.strip()[:160])
    v = VERDICT.search(out_text or "") if out_text else None
    verdict = rj.get("verdict") or (v.group(1) if v else None)
    wall = (rj.get("timings") or {}).get("wall_s") or (v.group(2) if v and v.group(2) else None)
    rows = ["==== SUMMARY ====",
            "verdict   %s%s" % (verdict or "unknown", " (%ss wall)" % wall if wall else "")]
    if fps:
        p50 = pct(fps, 50); p5 = pct(fps, 5)
        rows.append("fps       p50 %.1f, frame-time p95 %.1f ms (slowest 5%%: %.1f fps), %d samples" % (p50, 1000.0 / p5 if p5 else 0.0, p5, len(fps)))
    else:
        rows.append("fps       no fps lines in the log")
    rows.append("compiles  %s live shader compile(s) at the peak" % live)
    rows.append("stalls    %d stall line(s)" % stalls)
    rows.append("crashes    %s" % ("%d line(s): %s" % (sum(1 for l in lines if CRASH.search(l)), " | ".join(crash[:2])) if crash else "none"))
    rows.append("log       %d error line(s), %d warning line(s), %d line(s) in all" % (nerr, nwarn, len(lines)))
    if guard:
        rows.append("guards    " + " | ".join(guard[:3]))
    env = rj.get("env") if isinstance(rj.get("env"), dict) else {}
    if env:
        keep = {k: v for k, v in sorted(env.items()) if not re.search(r"(?i)key|secret|token|passw|credential|cookie", k) and not k.startswith(("DYLD", "VK_", "SDL_"))}
        rows.append("settings  " + " ".join("%s=%s" % kv for kv in list(keep.items())[:24]) + (" ..." if len(keep) > 24 else ""))
    if rj.get("class") or rj.get("scene"):
        rows.append("run       class=%s scene=%s gpu=%s until=%s" % (rj.get("class"), rj.get("scene"), rj.get("gpu"), rj.get("until")))
    rows.append("=================")
    return "\n".join(rows) + "\n"


def write_run_summary(run_dir: str, rj: dict) -> str:
    """The SUMMARY block of a finished run: written to <run>/run.summary.txt and returned. Never raises on a missing log."""
    def rd(n):
        try:
            with open(os.path.join(run_dir, n), errors="replace") as f:
                return f.read()
        except OSError:
            return ""
    text = summary(rd("run.err"), rj, rd("run.out"))
    try:
        with open(os.path.join(run_dir, "run.summary.txt"), "w") as f:
            f.write(text)
    except OSError:
        pass
    return text


def main(argv, environ=None):
    environ = dict(os.environ if environ is None else environ)
    target, mode, rate, level, rx, outp, i = None, "both", None, None, None, None, 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__); return 0
        elif a == "--summary": mode = "summary"
        elif a == "--rate":
            rate = (3, 50); mode = "rate"
            if i + 1 < len(argv) and re.fullmatch(r"\d+,\d+", argv[i + 1]):
                rate = tuple(int(x) for x in argv[i + 1].split(",")); i += 1
        elif a == "--level" and i + 1 < len(argv): level = argv[i + 1]; i += 1
        elif a == "--grep" and i + 1 < len(argv):
            try: rx = re.compile(argv[i + 1])
            except re.error: print("nps5 log: bad --grep expression", file=sys.stderr); return 2
            i += 1
        elif a == "--out" and i + 1 < len(argv): outp = argv[i + 1]; i += 1
        elif a.startswith("-"): print("nps5 log: unknown option %s" % a, file=sys.stderr); return 2
        elif target is None: target = a
        else: print("nps5 log: unexpected argument %s" % a, file=sys.stderr); return 2
        i += 1
    if not target:
        print("nps5 log: needs a run directory, a run tag, or a log file", file=sys.stderr); return 2
    rj, errp, outt = {}, None, ""
    if os.path.isfile(target):
        errp = target
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bugreport                      # run lookup is shared with `nps5 bug-report`
        d = target if os.path.isdir(target) else bugreport.resolve(target, environ)
        if not d:
            print("nps5 log: no such run or file: %s" % target, file=sys.stderr); return 1
        try:
            with open(os.path.join(d, "run.json")) as f: rj = json.load(f)
        except (OSError, ValueError): rj = {}
        cands = [os.path.join(d, "run.err")] + bugreport.private_logs(d, os.path.basename(os.path.dirname(os.path.normpath(d))))[:1]
        errp = next((p for p in cands if os.path.isfile(p)), None)
    if not errp:
        print("nps5 log: no log found for %s" % target, file=sys.stderr); return 1
    with open(errp, errors="replace") as f:
        text = f.read()
    op = errp[:-4] + ".out" if errp.endswith(".err") else None
    if op and os.path.isfile(op):
        with open(op, errors="replace") as f: outt = f.read()[-20000:]
    lines = text.splitlines()
    if level: lines = filter_level(lines, level)
    if rx: lines = [l for l in lines if rx.search(l)]
    if mode == "summary": body = summary(text, rj, outt)
    elif mode == "rate": body = "\n".join(rate_limit(lines, *rate)) + "\n" + summary(text, rj, outt)
    else: body = trim_log("\n".join(lines)) + summary(text, rj, outt)
    if outp:
        with open(outp, "w") as f: f.write(body)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
