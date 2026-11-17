"""nps5 analyze: the log analyses our optimisation decisions rest on, as library functions and one CLI.

  nps5 analyze frames <run dir | frames.log | perf_frames.csv> [--window steady|last:S|t:A-B] [--last S] [--skip-tail S]
  nps5 analyze passes <out log> [--tail FRACTION]      CpPass attachment sizes, draws/frame weighted
  nps5 analyze blocks <out log>                        per CpPass block: top sizes; CpGaps per window
  nps5 analyze gaps <out log> [--min-frames N]         CpGaps busy/idle windows
  nps5 analyze commits <commits log> [--last S] [--skip-end S] [--top N]   GPU busy / idle / pass sizes per frame
  nps5 analyze hot <commits log> <shader dir> [--from-frac F] [--top N]    rank shaders by GPU ms
  nps5 analyze gpu <ioreg text file>                   per-process GPU time from an `ioreg -r -c AGXDeviceUserClient -w 0` dump
Add --json for machine-readable output. Exit codes: 0 ok, 1 unusable input, 2 usage error."""
from __future__ import annotations

import collections
import gzip
import json
import math
import os
import re
import sys
from typing import Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frames as fr  # noqa: E402
from stats import pct, summarize  # noqa: E402


# ---------------------------------------------------------------- CpPass / CpGaps (title out log)
_ATT = re.compile(r"attachments (\d+x\d+) .*draws/frame=([\d.]+)")
_GAP = re.compile(r"^CpGaps (?P<secs>\d+)s: frames (?P<frames>\d+) wall (?P<wall>[\d.]+) ms/frame \| commit (?P<commit>[\d.]+)"
                  r"(?: \((?P<draws>\d+)(?: draws)?/f\))?[^|]*\| gap (?P<gap>[\d.]+) = (?P<rest>.*)")


def pass_sizes(lines: Iterable[str], tail: Optional[float] = None) -> dict[str, float]:
    """Share (percent) of draws per attachment size over the `CpPass: attachments` lines; tail=0.25 keeps the last 25 % of lines."""
    ln = [l for l in lines if l.startswith("CpPass: attachments")]
    if tail is not None:
        ln = ln[int(len(ln) * (1 - tail)):]
    d: collections.Counter = collections.Counter()
    for l in ln:
        m = _ATT.search(l)
        if m:
            d[m.group(1)] += float(m.group(2))
    tot = sum(d.values()) or 1.0
    return {k: 100.0 * v / tot for k, v in d.most_common()}


def pass_blocks(lines: Iterable[str]) -> list[dict[str, float]]:
    """One size-share dict per `CpPass:` header block (a header line without "attachments" opens a block)."""
    blocks: list[collections.Counter] = []
    cur: Optional[collections.Counter] = None
    for l in lines:
        if l.startswith("CpPass:") and "attachments" not in l:
            cur = collections.Counter()
            blocks.append(cur)
        elif l.startswith("CpPass: attachments") and cur is not None:
            m = _ATT.search(l)
            if m:
                cur[m.group(1)] += float(m.group(2))
    out = []
    for c in blocks:
        tot = sum(c.values())
        if tot >= 1:
            out.append({k: 100.0 * v / tot for k, v in c.most_common()})
    return out


def cpgaps(lines: Iterable[str]) -> list[dict]:
    """CpGaps windows: frames, wall/commit/gap ms per frame, busy percent, gap parts {name: ms}."""
    out = []
    for l in lines:
        m = _GAP.match(l)
        if not m:
            continue
        wall, commit = float(m["wall"]), float(m["commit"])
        parts = {k: float(v) for k, v in re.findall(r"([\w.]+) ([\d.]+) \(", m["rest"])}
        out.append(dict(secs=int(m["secs"]), frames=int(m["frames"]), wall=wall, commit=commit, gap=float(m["gap"]),
                        draws=int(m["draws"]) if m["draws"] else None, busy_pct=100 * commit / wall if wall else 0.0, parts=parts))
    return out


# ---------------------------------------------------------------- commit feedback log (GPU busy / idle)
Commit = tuple  # (gpu_start, gpu_end, tokens)


def load_commits(text: str) -> list[Commit]:
    """Lines "<gpu_start> <gpu_end> [PRESENT] <tokens...>"; bad or non-positive intervals are skipped."""
    out = []
    for l in text.splitlines():
        a = l.split()
        if len(a) < 2:
            continue
        try:
            s, e = float(a[0]), float(a[1])
        except ValueError:
            continue
        if e > s > 0:
            out.append((s, e, a[2:]))
    return out


def read_commits(path: str) -> list[Commit]:
    p = path if os.path.exists(path) else path + ".gz"
    with (gzip.open(p, "rt", errors="replace") if p.endswith(".gz") else open(p, errors="replace")) as f:
        return load_commits(f.read())


_RP = re.compile(r"R(\d+)x(\d+)/c(\d+)\*(\d+)/d(\d+)")


def parse_tokens(toks: list[str]) -> list[list]:
    """Commit tokens -> passes: ['R', (w, h), (colour fmt, n, depth fmt), Counter(pipelines)] or ['C', hash, size-token, dispatches].
    Tokens that do not parse are skipped, never fatal."""
    out: list[list] = []
    curp = None
    pend = None
    for t in toks:
        if t == "PRESENT" or not t:
            continue
        n = 1
        if t[0] in "PC" and "*" in t:
            t, _, k = t.rpartition("*")
            n = int(k) if k.isdigit() else 1
        if t[0] == "R":
            m = _RP.match(t)
            if not m:
                continue
            w, h, cf, nc, df = (int(x) for x in m.groups())
            curp = ["R", (w, h), (cf, nc, df), collections.Counter()]
            out.append(curp)
        elif t[0] == "P":
            if curp is None:
                curp = ["R", (0, 0), (0, 0, 0), collections.Counter()]
                out.append(curp)
            curp[3][t[1:]] += n
        elif t[0] == "C":
            if "/" in t:
                pend = t
                continue
            out.append(["C", t[1:], pend, n])
            pend = None
            curp = None
    return out


def commit_stats(commits: list[Commit], last: float = 20.0, skip_end: float = 6.0, top: int = 14, window: Optional[tuple[float, float]] = None) -> dict:
    """Per-frame GPU accounting over a window (default: the last `last` s before `skip_end` s from the end;
    window=(from, to) are seconds before the last commit). A frame = the commits up to and including a PRESENT commit.
    busy = union of GPU intervals; each commit's exclusive time = the part not covered by commits that started earlier,
    so exclusive times sum to busy. Returns dict with ok False when fewer than 3 frames fall in the window or they all end at one instant."""
    if not commits:
        return dict(ok=False, frames=0)
    tend = max(c[1] for c in commits)
    t0, t1 = (tend - window[0], tend - window[1]) if window else (tend - skip_end - last, tend - skip_end)
    frames, cur = [], []
    for c in commits:
        cur.append(c)
        if c[2] and c[2][0] == "PRESENT":
            frames.append(cur)
            cur = []
    frames = [f for f in frames if t0 <= f[-1][1] <= t1]
    if len(frames) < 3:
        return dict(ok=False, frames=len(frames))
    allc = sorted((c for f in frames for c in f), key=lambda c: c[0])
    excl, hi, gaps = {}, allc[0][0], []
    for c in allc:
        a = max(c[0], hi)
        if c[0] > hi:
            gaps.append(c[0] - hi)
        excl[id(c)] = max(0.0, c[1] - a)
        hi = max(hi, c[1])
    nf = len(frames)
    ival = [(frames[i][-1][1] - frames[i - 1][-1][1]) * 1000 for i in range(1, nf)]
    busy = [sum(excl[id(c)] for c in f) * 1000 for f in frames]
    span = [sum(c[1] - c[0] for c in f) * 1000 for f in frames]
    wall = (frames[-1][-1][1] - frames[0][-1][1]) * 1000 / (nf - 1)
    if not wall > 0:                                  # every frame ended at the same instant: no rate to report (and fps would divide by 0)
        return dict(ok=False, frames=nf, reason=f"{nf} frames in the window all end at the same instant")
    sizes: dict = collections.defaultdict(lambda: [0, 0])
    classes: dict = collections.defaultdict(lambda: [0.0, 0.0, 0])
    npass = ndisp = 0
    for f in frames:
        for c in f:
            key: list[str] = []
            for x in parse_tokens(c[2]):
                if x[0] == "R":
                    npass += 1
                    sizes[(x[1], x[2])][0] += 1
                    sizes[(x[1], x[2])][1] += sum(x[3].values())
                    key.append(f"{x[1][0]}x{x[1][1]}")
                else:
                    ndisp += x[3]
                    key.append("CS")
            k = " ".join(dict.fromkeys(key)) or ("PRESENT-only" if c[2][:1] == ["PRESENT"] else "(copies/blits)")
            e = classes[k]
            e[0] += excl[id(c)] * 1000
            e[1] += (c[1] - c[0]) * 1000
            e[2] += 1
    tot = sum(e[0] for e in classes.values()) or 1.0
    big = [g for g in gaps if g > .001]
    return dict(
        ok=True, frames=nf, window_s=t1 - t0, interval_p50=pct(ival, .5), interval_p95=pct(ival, .95), wall_mean=wall, fps=1000 / wall,
        busy_ms=sum(busy) / nf, busy_pct=100 * sum(busy) / nf / wall, busy_p50=pct(busy, .5), span_ms=sum(span) / nf, idle_ms=wall - sum(busy) / nf,
        commits_per_frame=sum(len(f) for f in frames) / nf, passes_per_frame=npass / nf, dispatches_per_frame=ndisp / nf,
        sizes=[dict(w=k[0][0], h=k[0][1], passes=v[0] / nf, binds=v[1] / nf) for k, v in sorted(sizes.items(), key=lambda kv: -kv[1][1])[:top]],
        classes=[dict(cls=k, ms=e[0] / nf, pct=100 * e[0] / tot, span=e[1] / nf, n=e[2] / nf) for k, e in sorted(classes.items(), key=lambda kv: -kv[1][0])[:top]],
        idle_gaps_per_frame=len(gaps) / nf, idle_gap_ms=sum(gaps) * 1000 / nf, big_gaps_per_frame=len(big) / nf, big_gap_ms=sum(big) * 1000 / nf)


def hot_shaders(commits_text: str, from_frac: float = 0.5, top: int = 40) -> dict:
    """Rank shader hashes by GPU ms of the commits they appear in (an upper bound). Log lines:
    "<time> <gpu ms> <x> <tokens>" where C<hash> is a compute shader and P<fs>+<vs> a pipeline. -> {span, rows: [(ms_per_s, hash, commits)]}"""
    rows = []
    for l in commits_text.splitlines():
        p = l.rstrip("\n").split(" ", 3)
        if len(p) < 4:
            continue
        try:
            rows.append((float(p[0]), float(p[1]), p[3].split()))
        except ValueError:
            pass
    rows = rows[int(len(rows) * from_frac):]
    if len(rows) < 2:
        return dict(span=0.0, rows=[])
    ms: collections.Counter = collections.Counter()
    cnt: collections.Counter = collections.Counter()
    for _, m, toks in rows:
        for tk in toks:
            if tk[0] == "C":
                ms[tk[1:17]] += m
                cnt[tk[1:17]] += 1
            elif tk[0] == "P" and "+" in tk:
                a, b = tk[1:].split("+", 1)
                for h in (a, b):
                    ms[h] += m
                    cnt[h] += 1
    span = rows[-1][0] - rows[0][0]
    if span <= 0:
        return dict(span=0.0, rows=[])
    return dict(span=span, rows=[(v / span, h, cnt[h]) for h, v in ms.most_common(top)])


# ---------------------------------------------------------------- per-process GPU time (ioreg)
def parse_ioreg(text: str) -> dict[tuple[int, str], int]:
    """`ioreg -r -c AGXDeviceUserClient -w 0` text -> {(pid, process): accumulated GPU ns}."""
    res: dict[tuple[int, str], int] = {}
    for blk in re.split(r"\n(?=[ |]*\+-o )", text):
        if "accumulatedGPUTime" not in blk:
            continue
        m = re.search(r'"IOUserClientCreator" = "pid (\d+), ([^"]*)"', blk)
        if not m:
            continue
        t = sum(int(x) for x in re.findall(r'"accumulatedGPUTime"=(\d+)', blk))
        k = (int(m.group(1)), m.group(2))
        res[k] = res.get(k, 0) + t
    return res


def gpu_rates(a: dict, b: dict, dt: float, title_prefix: str = "native_") -> dict:
    """Two ioreg snapshots dt seconds apart -> ms of GPU per second for the title process, WindowServer and all others."""
    r = {k: (b[k] - a.get(k, 0)) / 1e6 / dt for k in b}
    title = [(v, k) for k, v in r.items() if k[1].startswith(title_prefix)]
    tv, tk = max(title) if title else (0.0, (0, "-"))
    ws = sum(v for k, v in r.items() if k[1] == "WindowServer")
    oth = sum(v for v in r.values() if v > 0) - ws - sum(v for v, _ in title)
    return dict(title_pid=tk[0], title=tv, windowserver=ws, others=max(0.0, oth))


# ---------------------------------------------------------------- CLI
def _text(path: str) -> str:
    with open(path, errors="replace") as f:
        return f.read()


def _frames_from(path: str) -> fr.Frames:
    if os.path.isdir(path):
        return fr.load_run_frames(path)
    t = _text(path)
    return fr.parse_perf_csv(t) if path.endswith(".csv") else fr.parse_frames_log(t)


def _opts(argv: list[str], spec: dict[str, type]) -> tuple[list[str], dict]:
    pos, o, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            o["json"] = True
        elif a in spec:
            if i + 1 >= len(argv):
                raise SystemExit(f"nps5 analyze: {a} needs a value")
            try:
                o[a[2:].replace("-", "_")] = spec[a](argv[i + 1])
            except ValueError:
                raise SystemExit(f"nps5 analyze: bad value for {a}: {argv[i + 1]}")
            i += 1
        elif a.startswith("--"):
            raise SystemExit(f"nps5 analyze: unknown option {a}")
        else:
            pos.append(a)
        i += 1
    return pos, o


def main(argv: list[str], environ: Optional[dict] = None) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    kind, rest = argv[0], argv[1:]
    try:
        return _run(kind, rest)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        return e.code or 0
    except (fr.FramesError, OSError) as e:
        print(f"nps5 analyze {kind}: {e}", file=sys.stderr)
        return 1


def _run(kind: str, rest: list[str]) -> int:
    spec = {"--window": str, "--last": float, "--skip-tail": float, "--tail": float, "--min-frames": int, "--skip-end": float,
            "--top": int, "--from-frac": float}
    pos, o = _opts(rest, spec)
    js = o.get("json")
    if kind == "frames":
        if len(pos) != 1:
            raise SystemExit("usage: nps5 analyze frames <run dir | file> [--window steady|last:S|t:A-B]")
        f = _frames_from(pos[0])
        w = o.get("window", "steady")
        bad = SystemExit(f"nps5 analyze: bad --window {w!r} (steady, last:SECONDS or t:FROM-TO)")
        try:
            nums = [float(w[5:])] if w.startswith("last:") else [float(x) for x in (w[2:].partition("-")[0], w[2:].partition("-")[2])] if w.startswith("t:") else []
        except ValueError:
            raise bad
        if not all(math.isfinite(x) for x in nums) or (w != "steady" and not nums):
            raise bad
        if w == "steady":
            win = fr.steady_window(f, o.get("last", 30.0), o.get("skip_tail", 2.0))
        elif w.startswith("last:"):
            win = fr.last_window(f, nums[0], o.get("skip_tail", 1.0))
        else:
            win = fr.time_window(f, nums[0], nums[1])
        d = dict(window=[win.start_s, win.end_s], covered_s=win.covered_s, short=win.short, loading_frames_cut=win.loading_frames_cut, notes=win.notes, **win.stats)
        if js:
            print(json.dumps(d))
        else:
            s = win.stats
            print(f"[{win.start_s:.1f}-{win.end_s:.1f} s] n={s['n']} p50 {s['p50']:.1f} p95 {s['p95']:.1f} mean {s['mean']:.1f} ms, {s['fps']:.1f} fps"
                  + (f"  SHORT: covered {win.covered_s:.1f} of {win.requested_s:.0f} s" if win.short else "") + "".join(f"\n  note: {n}" for n in win.notes))
        return 0
    if kind in ("passes", "blocks", "gaps"):
        if len(pos) != 1:
            raise SystemExit(f"usage: nps5 analyze {kind} <out log>")
        lines = _text(pos[0]).splitlines()
        if kind == "passes":
            d = {"all": pass_sizes(lines), "tail": pass_sizes(lines, o.get("tail", .25))}
            print(json.dumps(d) if js else "\n".join(f"{k}: " + " ".join(f"{s}:{v:.0f}%" for s, v in list(x.items())[:5]) for k, x in d.items()))
        elif kind == "blocks":
            b = pass_blocks(lines)
            g = [x for x in cpgaps(lines) if x["frames"] >= 20]
            print(json.dumps(dict(blocks=b, gaps=g)) if js else "\n".join([f"pass {i}: " + " ".join(f"{s}:{v:.0f}%" for s, v in list(x.items())[:4]) for i, x in enumerate(b)]
                                                                         + ["gaps(frames,ms,draws): " + " ".join(f"{x['frames']}/{x['wall']:.1f}/{x['draws']}" for x in g)]))
        else:
            g = [x for x in cpgaps(lines) if x["frames"] >= o.get("min_frames", 20)]
            if js:
                print(json.dumps(g))
            for x in g[-8:]:
                top = sorted(x["parts"].items(), key=lambda kv: -kv[1])[:6]
                if not js:
                    print(f"frames {x['frames']:4d} wall {x['wall']:6.1f} commit {x['commit']:5.1f} busy% {x['busy_pct']:4.1f} | " + " ".join(f"{k} {v:.1f}" for k, v in top))
        return 0
    if kind == "commits":
        if len(pos) != 1:
            raise SystemExit("usage: nps5 analyze commits <commits log> [--last S] [--skip-end S] [--top N]")
        s = commit_stats(read_commits(pos[0]), o.get("last", 20.0), o.get("skip_end", 6.0), o.get("top", 14))
        if not s["ok"]:
            print(s.get("reason") or f"only {s['frames']} frames in window", file=sys.stderr)
            return 1
        if js:
            print(json.dumps(s))
        else:
            print(f"window {s['window_s']:.0f} s, {s['frames']} frames; present interval p50 {s['interval_p50']:.1f} p95 {s['interval_p95']:.1f} mean {s['wall_mean']:.1f} ms ({s['fps']:.1f} fps)")
            print(f"GPU busy {s['busy_ms']:.1f} ms/frame = {s['busy_pct']:.0f} % of wall; idle {s['idle_ms']:.1f} ms/frame; commits/frame {s['commits_per_frame']:.0f}, passes/frame {s['passes_per_frame']:.0f}, dispatches/frame {s['dispatches_per_frame']:.0f}")
            for c in s["classes"]:
                print(f"  {c['ms']:6.2f} ms ({c['pct']:4.1f}%) span {c['span']:6.2f} n {c['n']:5.1f} | {c['cls'][:100]}")
        return 0
    if kind == "hot":
        if len(pos) != 2:
            raise SystemExit("usage: nps5 analyze hot <commits log> <shader dir> [--from-frac F] [--top N]")
        h = hot_shaders(_text(pos[0]), o.get("from_frac", .5), o.get("top", 40) * 3)
        shown = 0
        print(f"# span {h['span']:.1f} s")
        for rate, hs, n in h["rows"]:
            msl = os.path.join(pos[1], hs + ".metal")
            if not os.path.exists(msl):
                continue
            info = os.path.join(pos[1], hs + ".info")
            m = re.search(r"stage=(\w+)", _text(info)) if os.path.exists(info) else None
            t = _text(msl)
            print(f"{rate:8.1f} ms/s {m.group(1) if m else '?':5} {hs} {len(t):8} nan={t.count('!isnan('):5} safe={t.count('math_mode(safe)'):5} coh={t.count('(coherent device '):5} cnt={n}")
            shown += 1
            if shown >= o.get("top", 40):
                break
        return 0
    if kind == "gpu":
        if len(pos) != 1:
            raise SystemExit("usage: nps5 analyze gpu <ioreg text file>")
        for (pid, name), ns in sorted(parse_ioreg(_text(pos[0])).items(), key=lambda kv: -kv[1]):
            print(f"{ns / 1e6:12.1f} ms GPU total  pid {pid} {name}")
        return 0
    raise SystemExit(f"unknown analysis {kind!r}; see nps5 analyze --help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
