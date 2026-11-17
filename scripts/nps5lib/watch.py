#!/usr/bin/env python3
"""nps5 watch: the run watcher behind `nps5 run --until` (ported from the private smartwatch.py). Standard library only.

A `Judge` (pure, no I/O) is fed what a run prints and dumps, and says when to stop it. `follow()` is the live driver.

Until spec (scenes.tsv `until`, `nps5 run --until`), optionally followed by `,black:N` and/or `,stall:N` (seconds):
  first-present[+N]    the first present, then N s            pic[+N]          the first real picture, then N s
  marker:<name>[+N]    a marker row of game.tsv (log:<re> | open:<re> | present:<n>), then N s (a picture is required too
                       for log: and open: markers)
  scene:<name>[+N]     "[scene-open] <name>" (alias "[level-open]") log line, or an open file under <scene_open_key><name>/
  log:<re>[+N]  open:<re>[+N]   the same, spelled out        time:N   N s of run        frames:N   N presents
  worldmap/title/level:<name>   legacy private spellings (accepted, mapped to the forms above)
Signals (cheap; no screenshots needed):
  "[pstat] wall= presents= luma= black= sd= diff="   AnyPS5 with APS5_PRESENT_STATS=<ms>
  "[scene-open] <name> t=<s>" / "[level-open] ..."   runtime VFS (game.tsv scene_open_key; NPS5_SCENE_OPEN_KEY)
  "[prebuild] Compiling shaders A / B (S s)"          shader pre-build counter; "[prebuild] A / B pipelines in X s ..." ends it
  perf_frames.csv rows (frames.parse_perf_csv), dumped *.bmp frames (bmp.luma), log growth, process CPU time
Verdicts: DONE_UNTIL | EARLY_BLACK | EARLY_STALL | TIMEOUT (target not reached) | DONE_FIXED (no target: ran the full secs)
Without a picture signal (no [pstat], no frame dumps) a target that needs a picture is never reached: it ends TIMEOUT.
Thresholds (s): black_fail (profile blank_fail_s, default 40), stall_fail (stall_fail_s, 45), ceiling max_s (the run's secs)."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmp  # noqa: E402
import frames  # noqa: E402

NUM = r"(\d+(?:\.\d+)?)"         # a number float() accepts: a torn line ("luma=." or "1.2.3") must not stop the watcher
PSTAT = re.compile(r"\[pstat\] wall=%s presents=(\d+) luma=%s black=%s sd=%s diff=%s" % (NUM, NUM, NUM, NUM, NUM))
SCENE_OPEN = re.compile(r"\[(?:scene|level)-open\] (\S+)(?: t=%s)?" % NUM)
PREB = re.compile(r"\[prebuild\] Compiling shaders\s+(\d+) / (\d+)")
PREB_DONE = re.compile(r"\[prebuild\] (\d+) / (\d+) pipelines in")
DONE, BLACK, STALL, TIMEOUT, FIXED = "DONE_UNTIL", "EARLY_BLACK", "EARLY_STALL", "TIMEOUT", "DONE_FIXED"


class UntilError(ValueError):
    pass


def parse_until(spec, markers=None, scene_key="levels/"):
    """-> dict(kind, arg, n, need_pic, black, stall) or None. kind: pic first-present log open scene present time frames.
    `markers` maps a marker name to its spec (game.tsv marker rows). Raises UntilError on a bad or unknown spec."""
    if not spec or spec == "-":
        return None
    parts = spec.split(",")
    mods = {}
    for extra in parts[1:]:
        m = re.fullmatch(r"(black|stall):(\d+(?:\.\d+)?)", extra)
        if not m:
            raise UntilError("bad until modifier %r (black:N | stall:N)" % extra)
        mods[m.group(1)] = float(m.group(2))
    m = re.fullmatch(r"(.+?)(?:\+(\d+(?:\.\d+)?))?", parts[0])
    what, n = m.group(1), float(m.group(2) or 0)
    out = dict(kind=None, arg=None, n=n, need_pic=True, black=mods.get("black"), stall=mods.get("stall"), scene_key=scene_key)
    if what == "first-present":
        out.update(kind="first-present", need_pic=False)
    elif what in ("pic", "title"):
        out.update(kind="pic")
    elif what == "worldmap":
        out.update(kind="scene", arg="worldmap")
    elif what.startswith(("scene:", "level:")):
        out.update(kind="scene", arg=what.split(":", 1)[1])
    elif what.startswith("log:"):
        out.update(kind="log", arg=what[4:])
    elif what.startswith("open:"):
        out.update(kind="open", arg=what[5:])
    elif what.startswith("time:") and what[5:].isdigit():
        out.update(kind="time", arg=None, n=float(what[5:]) + n, need_pic=False)
    elif what.startswith("frames:") and what[7:].isdigit():
        out.update(kind="frames", arg=int(what[7:]), need_pic=False)
    elif what.startswith("marker:"):
        name = what[7:]
        if name not in (markers or {}):
            raise UntilError("marker %r is not defined in the profile" % name)
        sub = parse_until(markers[name] + ("+%g" % n if n else ""), None, scene_key)
        if sub["kind"] not in ("log", "open", "present"):
            raise UntilError("marker %r: bad spec %r" % (name, markers[name]))
        sub.update(black=out["black"], stall=out["stall"])
        return sub
    elif what.startswith("present:") and what[8:].isdigit():
        out.update(kind="present", arg=int(what[8:]))
    else:
        raise UntilError("bad until %r (first-present|pic|marker:<m>|scene:<s>|log:<re>|open:<re>|time:N|frames:N, +N, ,black:N ,stall:N)" % spec)
    if out["kind"] in ("log", "open"):
        try:
            re.compile(out["arg"])
        except re.error as e:
            raise UntilError("bad regex in until %r: %s" % (spec, e))
    return out


class Judge:
    def __init__(self, secs, until=None, blank_fail=40.0, stall_fail=45.0, max_s=None, extend_quiet=10.0,
                 luma_min=10.0, black_max=0.75, markers=None, scene_key="levels/"):
        self.secs = float(secs)
        self.max_s = float(max_s if max_s is not None else secs)
        self.until = parse_until(until, markers, scene_key) if isinstance(until, str) or until is None else until
        u = self.until or {}
        self.blank_fail = float(u.get("black") or blank_fail)
        self.stall_fail = float(u.get("stall") or stall_fail)
        self.extend_quiet = extend_quiet
        self.luma_min, self.black_max = luma_min, black_max
        self.scene_key = scene_key
        self.t_first_present = None      # age of the first present (presents > 0)
        self.t_first_sample = None       # age of the first picture measurement (pstat or a dumped frame)
        self.t_pic = None                # age of the first real picture
        self.black_since = None          # start of the current black run, None while a picture shows
        self.last_presents = 0
        self.t_present_progress = None
        self.t_prebuild = None
        self.prebuild_cnt = None
        self.prebuild_active = False
        self.t_log = 0.0                 # last stderr growth
        self.t_cpu = 0.0                 # last time the process burned > 0.3 core
        self.t_marker = None
        self.t_frames = None
        self.note = ""
        self.window_pending = False
        self.scenes = {}                 # scene name -> age of its [scene-open] line

    # ---- inputs
    def feed_present(self, age, presents):
        if presents > 0 and self.t_first_present is None:
            self.t_first_present = age
            self.prebuild_active = False             # the first frame ends the pre-build phase
        if presents > self.last_presents:
            self.t_present_progress = age
            self.last_presents = presents
        u = self.until
        if u and u["kind"] in ("present", "frames") and self.t_frames is None and presents >= u["arg"]:
            self.t_frames = age

    def feed_picture(self, age, luma, black):
        """One measurement of the screen. The dither hatch (luma 5.7, black 0.90) counts as black; a picture has luma >= 10 or black <= 0.75."""
        if self.t_first_sample is None:
            self.t_first_sample = age
        if luma >= self.luma_min or black <= self.black_max:
            self.black_since = None
            if self.t_pic is None:
                self.t_pic = age
        elif self.black_since is None:
            self.black_since = age

    def feed_line(self, age, line):
        self.t_log = age
        m = PSTAT.search(line)
        if m:
            self.feed_present(age, int(m.group(2)))
            self.feed_picture(age, float(m.group(3)), float(m.group(4)))
            return
        m = PREB.search(line)
        if m:
            cnt = (int(m.group(1)), int(m.group(2)))
            if cnt != self.prebuild_cnt:
                self.prebuild_cnt, self.t_prebuild, self.prebuild_active = cnt, age, True
            return
        if PREB_DONE.search(line):
            self.prebuild_active = False
            self.t_prebuild = age
            return
        u = self.until
        m = SCENE_OPEN.search(line)
        if m:
            self.scenes.setdefault(m.group(1), age)
            if u and self.t_marker is None:
                if u["kind"] == "scene" and u["arg"] == m.group(1):
                    self.t_marker = age
                elif u["kind"] == "open" and re.search(u["arg"], "%s%s/" % (self.scene_key, m.group(1)), re.I):
                    self.t_marker = age
            return
        if u and u["kind"] == "log" and self.t_marker is None and re.search(u["arg"], line):
            self.t_marker = age

    def feed_cpu(self, age, cores):
        if cores > 0.3:
            self.t_cpu = age

    def feed_open_files(self, age, text):
        u = self.until
        if u and u["kind"] == "open" and self.t_marker is None and re.search(u["arg"], text):
            self.t_marker = age
        if u and u["kind"] == "scene" and self.t_marker is None and re.search(r"%s%s/" % (re.escape(self.scene_key), re.escape(u["arg"])), text):
            self.t_marker = age

    # ---- decision
    def progressing(self, age):
        if self.prebuild_active and self.t_prebuild is not None and age - self.t_prebuild < self.extend_quiet:
            return True
        if self.t_first_present is None and age - max(self.t_log, self.t_cpu) < self.extend_quiet:
            return True
        return self.window_pending       # target reached before the cap: let its window finish (bounded by max_s)

    def _t0(self):
        u = self.until
        k = u["kind"]
        if k == "pic":
            return self.t_pic
        if k == "first-present":
            return self.t_first_present
        if k == "time":
            return 0.0
        if k in ("present", "frames"):
            return self.t_frames
        return self.t_marker

    def decide(self, age):
        """-> None (keep going) or (verdict, detail)."""
        u = self.until
        if u:
            t0 = self._t0()
            if t0 is not None and u["need_pic"] and u["kind"] != "pic":
                t0 = max(t0, self.t_pic) if self.t_pic is not None else None
            if t0 is not None and age - t0 >= u["n"] and (self.black_since is None or not u["need_pic"]) and (self.t_pic is not None or not u["need_pic"]):
                return (DONE, "target reached at %.0fs, window %.0fs done at %.0fs" % (t0, u["n"], age))
            self.window_pending = t0 is not None
        # black: no picture N s after it should have appeared, or black for N s after one was shown
        ref = None
        if self.prebuild_active and self.t_prebuild is not None and age - self.t_prebuild < self.stall_fail:
            ref = None            # compiling: black is expected, the stall rule watches the counter
        elif self.t_pic is None and self.t_first_sample is not None:
            ref = max(self.t_first_present or 0, self.t_first_sample, self.t_prebuild or 0)
        elif self.black_since is not None:
            ref = self.black_since
        if ref is not None and age - ref >= self.blank_fail:
            return (BLACK, "black/no picture for %.0fs (since %.0fs; first present %s)" % (age - ref, ref, self.t_first_present))
        # stall: no progress
        prog = [self.t_present_progress, self.t_prebuild, self.t_log if self.t_first_present is None else None,
                self.t_cpu if self.t_first_present is None else None]
        newest = max([p for p in prog if p is not None] + [0.0])
        if age - newest >= self.stall_fail:
            return (STALL, "no progress for %.0fs (last present %s, prebuild %s, log %.0fs)" % (age - newest, self.t_present_progress, self.t_prebuild, self.t_log))
        if age >= self.secs:
            if age < self.max_s and self.progressing(age):
                self.note = "extended past %.0fs while progressing" % self.secs
                return None
            if self.t_first_sample is not None and (self.t_pic is None or (self.black_since is not None and age - self.black_since >= 10)):
                since = self.t_first_sample if self.t_pic is None else self.black_since
                return (BLACK, "black at the %.0fs cap (black %.0fs, since %.0fs, presents %d)" % (self.secs, age - since, since, self.last_presents))
            if age >= self.max_s and self.max_s > self.secs:
                return (TIMEOUT, "hard ceiling %.0fs" % self.max_s) if u else (FIXED, "ceiling %.0fs" % self.max_s)
            return (TIMEOUT, "target not reached in %.0fs" % self.secs) if u else (FIXED, "ran %.0fs" % self.secs)
        return None


# ---------------------------------------------------------------- live driver
def bmp_stats(path, max_px=20000):
    """(mean luma, share of near-black samples) of a dumped frame, via bmp.luma; None when unreadable (half-written)."""
    try:
        y = bmp.luma(bmp.read_bmp(path), max_px)
    except (OSError, bmp.BmpError, ValueError, IndexError):
        return None
    flat = [v for row in y for v in row]
    if not flat:
        return None
    return sum(flat) / len(flat), sum(1 for v in flat if v < 8.0) / len(flat)


def profile_markers(p):
    """game.tsv marker rows -> {name: spec}."""
    return {c[0]: c[1] for c in (p.get("marker") if p else []) if len(c) >= 2}


def judge_for(plan, environ=None, max_s=None):
    """The Judge for a run plan: until from the plan, markers + thresholds from its profile."""
    p = plan.p
    num = lambda k, d: float(p.one(k) or d) if p and re.fullmatch(r"\d+", str(p.one(k) or "")) else d  # noqa: E731
    key = (plan.env.get("NPS5_SCENE_OPEN_KEY") or (p.one("scene_open_key") if p else None) or "levels/")
    return Judge(plan.secs, plan.until, num("blank_fail_s", 40.0), num("stall_fail_s", 45.0), max_s or plan.secs,
                 markers=profile_markers(p), scene_key=key)


class Sources:
    """Polls the files a run writes: new stderr text, perf csv rows, the newest dumped frame. Cheap; each call is incremental."""
    def __init__(self, err_path, run_dir):
        self.err, self.dir = err_path, run_dir
        self.pos, self.buf, self.seen_bmp, self.csv_n = 0, "", None, 0

    def lines(self):
        try:
            with open(self.err, "r", errors="replace") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except OSError:
            return []
        if not chunk:
            return []
        self.buf += chunk
        *ls, self.buf = self.buf.split("\n")
        return ls

    def presents(self):
        for d in (self.dir, os.path.join(self.dir, "frames")):
            c = os.path.join(d, "perf_frames.csv")
            if os.path.isfile(c):
                try:
                    return len(frames.parse_perf_csv(frames._read(c)))
                except (frames.FramesError, OSError):
                    return None
        return None

    def picture(self):
        d = os.path.join(self.dir, "frames")
        try:
            names = [os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(".bmp")]
            newest = max(names, key=os.path.getmtime)
        except (OSError, ValueError):
            return None
        sig = (newest, os.path.getmtime(newest))
        if sig == self.seen_bmp:
            return None
        st = bmp_stats(newest)
        if st:
            self.seen_bmp = sig
        return st


def stop_child(pr, grace=30, sleep=time.sleep):
    """SIGTERM (gtimeout forwards it to the title), then SIGKILL of that one pid after `grace` s."""
    try:
        pr.terminate()
    except OSError:
        pass
    for _ in range(int(grace)):
        if pr.poll() is not None:
            return pr.returncode
        sleep(1)
    try:
        pr.kill()
    except OSError:
        pass
    return pr.wait()


def follow(pr, judge, src, now=time.time, sleep=time.sleep, poll=1.0, lsof=None, log=None):
    """Drives `judge` from `src` while the child `pr` (a Popen-like) runs. Returns (verdict or None, detail, rc).
    verdict None = the child ended by itself. The child is stopped cleanly when the judge decides."""
    t0 = now()
    cpu_prev = age_prev = None
    lsof_t = -99
    while pr.poll() is None:
        age = now() - t0
        for ln in src.lines():
            judge.feed_line(age, ln)
        n = src.presents()
        if n is not None:
            judge.feed_present(age, n)
        pic = src.picture()
        if pic:
            judge.feed_picture(age, pic[0], pic[1])
            judge.feed_present(age, max(judge.last_presents, 1))     # a dumped frame proves a present
        cpu = getattr(pr, "cpu_seconds", lambda: None)()
        if cpu is not None and cpu_prev is not None and age > age_prev:
            judge.feed_cpu(age, (cpu - cpu_prev) / (age - age_prev))
        cpu_prev, age_prev = cpu, age
        u = judge.until
        if lsof and u and u["kind"] in ("open", "scene") and judge.t_marker is None and age - lsof_t >= 3:
            lsof_t = age
            judge.feed_open_files(age, lsof())
        v = judge.decide(age)
        if v:
            if log:
                log("[watch] %s at %.0fs: %s" % (v[0], age, v[1]))
            return v[0], v[1], stop_child(pr, sleep=sleep)
        sleep(poll)
    return None, "", pr.returncode
