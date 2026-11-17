"""Frame-time inputs for the nps5 tools: one parser per on-disk format, flip times, and window selection.

Formats:
  frames log   lines "<time_us>,<frame_us>" (the title's own frame-times log; time = end of frame on the title clock)
  perf csv     header "frame,wall_ms,..." (host flip hook), one row per presented frame
A "window" is a slice of the run to take statistics over. The steady window (`steady_window`) never includes the
loading phase: it starts after the last stretch whose median frame time is far above the settled level."""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stats import pct, summarize  # noqa: F401  (re-exported for callers)


class FramesError(ValueError):
    """Unusable frame data (empty, no parsable rows, unknown layout)."""


@dataclass
class Frames:
    """Frame i ended at t[i] seconds (run clock) and took ms[i] milliseconds."""
    t: list[float]
    ms: list[float]
    source: str = ""

    def __len__(self) -> int:
        return len(self.ms)


def parse_frames_log(text: str) -> Frames:
    """Parse "<time_us>,<frame_us>" lines. Torn or foreign lines (a run killed mid-write, headers) are skipped."""
    t: list[float] = []
    ms: list[float] = []
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2 or not (parts[0].strip().isdigit() and parts[1].strip().isdigit()):
            continue
        t.append(int(parts[0]) / 1e6)
        ms.append(int(parts[1]) / 1000.0)
    if not ms:
        raise FramesError("no frame rows in the frames log")
    return Frames(t, ms, "frames.log")


def flip_times(wall_ms: Sequence[float], total_s: Optional[float] = None) -> list[float]:
    """Process time of each flip. With the run's total wall time the last flip is placed at the end and the rest are
    counted backwards from it (the way the per-run summary does it); else the first flip is at its own wall time."""
    n = len(wall_ms)
    end = [0.0] * n
    if total_s is None:
        acc = 0.0
        for i, w in enumerate(wall_ms):
            acc += w / 1000.0
            end[i] = acc
        return end
    t = total_s
    for i in range(n - 1, -1, -1):
        end[i] = t
        t -= wall_ms[i] / 1000.0
    return end


def parse_perf_csv(text: str, total_s: Optional[float] = None) -> Frames:
    rows = list(csv.DictReader(text.splitlines()))
    wall = []
    for r in rows:
        try:
            wall.append(float(r["wall_ms"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not wall:
        raise FramesError("no wall_ms rows in the perf csv")
    return Frames(flip_times(wall, total_s), wall, "perf_frames.csv")


def _read(path: str) -> str:
    with open(path, errors="replace") as f:
        return f.read()


def run_total_seconds(run_dir: str) -> Optional[float]:
    """Wall time of a run: run.json timings.wall_s, else a "wall_time_s: N" line of summary.txt."""
    p = os.path.join(run_dir, "run.json")
    if os.path.isfile(p):
        try:
            with open(p) as f:
                w = json.load(f).get("timings", {}).get("wall_s")
            if isinstance(w, (int, float)):
                return float(w)
        except (ValueError, OSError):
            pass
    for d in (run_dir, os.path.join(run_dir, "frames")):
        s = os.path.join(d, "summary.txt")
        if os.path.isfile(s):
            m = re.search(r"^wall_time_s:\s*([0-9.]+)", _read(s), re.M)
            if m:
                return float(m.group(1))
    return None


def load_run_frames(run_dir: str) -> Frames:
    """Frame data of a run directory: perf_frames.csv (in the dir or its frames/), else *.frames.log."""
    for d in (run_dir, os.path.join(run_dir, "frames")):
        c = os.path.join(d, "perf_frames.csv")
        if os.path.isfile(c):
            fr = parse_perf_csv(_read(c), run_total_seconds(run_dir))
            fr.source = c
            return fr
    logs = sorted(glob.glob(os.path.join(run_dir, "*.frames.log")) + glob.glob(os.path.join(run_dir, "frames", "*.frames.log")))
    if logs:
        fr = parse_frames_log(_read(logs[-1]))
        fr.source = logs[-1]
        return fr
    raise FramesError(f"no perf_frames.csv or *.frames.log in {os.path.basename(os.path.normpath(run_dir))}")


@dataclass
class Window:
    """Statistics over a slice of a run, plus what was cut and why."""
    stats: dict
    start_s: float
    end_s: float
    requested_s: float
    covered_s: float
    loading_frames_cut: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def short(self) -> bool:
        return self.covered_s < 0.9 * self.requested_s


def settled_index(ms: Sequence[float], settle: int = 30, factor: float = 3.0) -> Optional[int]:
    """Index of the first frame after the loading phase, or None if the run never settles.
    Reference = median of the second half. A stretch of `settle` frames is loading when its median exceeds
    factor x reference; the settled part starts after the last loading-like frame of the last such stretch (an isolated
    stall does not move it)."""
    n = len(ms)
    if n == 0:
        return None
    ref = pct(ms[n // 2:], .5)
    w = min(settle, n)
    last_bad = -1
    for i in range(0, n - w + 1):
        if pct(ms[i:i + w], .5) > factor * ref:
            last_bad = i
    start = 0
    if last_bad >= 0:
        start = 1 + max(j for j in range(last_bad, last_bad + w) if ms[j] > factor * ref)
    return start if start < n else None


def steady_window(fr: Frames, last: float = 30.0, skip_tail: float = 2.0, settle: int = 30, factor: float = 3.0) -> Window:
    """Stats over [end - skip_tail - last, end - skip_tail], never including the loading phase, and never claiming
    coverage the run does not have (a 20 s steady run asked for 30 s says covered 20 s, short)."""
    i0 = settled_index(fr.ms, settle, factor)
    if i0 is None:
        raise FramesError("run never settles: no steady window")
    notes = []
    n_end = len(fr)
    ref = pct(fr.ms[len(fr) // 2:], .5)
    while n_end > i0 + 1 and fr.ms[n_end - 1] > factor * ref:     # a run that hangs and is killed ends in a stall: not steady state
        n_end -= 1
    if n_end < len(fr):
        notes.append(f"{len(fr) - n_end} trailing stall frame(s) dropped (hang or kill at the end of the run)")
    end = fr.t[n_end - 1] - skip_tail
    start = end - last
    steady_t = fr.t[i0 - 1] if i0 > 0 else fr.t[0] - fr.ms[0] / 1000.0
    cut = 0
    if start < steady_t:
        cut = sum(1 for i in range(i0) if start <= fr.t[i] <= end)
        if cut:
            notes.append(f"{cut} loading frames inside the requested window were cut")
        start_eff = steady_t
    else:
        start_eff = start
    sel = [fr.ms[i] for i in range(i0, n_end) if start_eff < fr.t[i] <= end]
    return _window(sel, start_eff, end, last, cut, notes)


def time_window(fr: Frames, a: float, b: float, min_frames: int = 5) -> Window:
    """Stats over frames that ended in [a, b] (run clock). Fewer than min_frames raises FramesError."""
    sel = [fr.ms[i] for i in range(len(fr)) if a <= fr.t[i] <= b]
    if len(sel) < min_frames:
        raise FramesError(f"{len(sel)} frames in [{a:.1f}, {b:.1f}] s")
    return _window(sel, a, b, b - a, 0, [])


def _window(sel: Sequence[float], a: float, b: float, requested: float, cut: int, notes: list[str]) -> Window:
    if not sel:
        raise FramesError("empty window")
    return Window(summarize(sel), a, b, requested, max(0.0, b - a), cut, notes)


def last_window(fr: Frames, secs: float, skip_tail: float = 1.0) -> Window:
    return time_window(fr, fr.t[-1] - skip_tail - secs, fr.t[-1] - skip_tail)


def scene_window(fr: Frames, opens: dict[str, float], spec: str, total: float) -> Window:
    """Window by scene spec: title | logo | worldmap | level:<name> | last:<s> | t:<a>-<b> (see an4 notes in docs/agents/evidence.md).
    `opens` maps a [level-open] name to its time. level:X starts at the 1700th present after X opened."""
    def need(name: str) -> float:
        if name not in opens:
            raise FramesError(f"{name} was not opened")
        return opens[name]
    if spec in ("title", "worldmap"):
        return time_window(fr, need("worldmap") + 15, total - 1)
    if spec == "logo":
        return time_window(fr, need("ps_logo") + 12, opens.get("worldmap", total) - 2)
    if spec.startswith("level:"):
        o = need(spec[6:])
        after = [i for i in range(len(fr)) if fr.t[i] >= o]
        if len(after) <= 1700:
            raise FramesError(f"only {len(after)} presents after level-open (need 1700)")
        return time_window(fr, fr.t[after[1700]], total - 1)
    if spec.startswith("last:"):
        return time_window(fr, total - 1 - float(spec[5:]), total - 1)
    if spec.startswith("t:"):
        a, _, b = spec[2:].partition("-")
        return time_window(fr, float(a), float(b))
    raise FramesError(f"unknown scene spec {spec!r}")
