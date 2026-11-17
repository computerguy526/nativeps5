"""nps5 ab: an A/B comparison with noise-aware statistics.

  nps5 ab <id> --arm A:K=V,... --arm B:K=V,... [--reps N] [--order ABBA] [--scene S] [--until SPEC] [--class perf|func]
          [--prefix P] [--window steady|last:S|t:A-B] [--min-effect MS] [--json] [--dry-run]
  nps5 ab <id> --from-runs --arm A:tag1,tag2 --arm B:tag3,tag4 [--window ...] [--json]

First form: runs every arm through `nps5 run` in the given order (ABBA... cycled until each arm has --reps runs;
default 3 reps, order ABBA) and analyses the results. Second form: analyses runs that already exist (a tag under
<runs root>/<id>/, or a run directory path). Per run: p50 and p95 frame time over the steady window (loading phase
excluded). Per arm: between-run mean and spread. B against A: difference of per-run p50 with a bootstrap CI and a
verdict better / worse / no-difference. One run per arm never gets a verdict ("insufficient").
Runs root: $NPS5_RUNS_ROOT, else $NPS5_SCRATCH/runs. Exit codes: 0 analysed, 1 not enough usable data,
2 usage, otherwise the failing `nps5 run` exit code."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frames as fr  # noqa: E402
import stats as st  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def runs_root(environ: dict) -> str:
    return environ.get("NPS5_RUNS_ROOT") or os.path.join(environ.get("NPS5_SCRATCH") or os.path.join(REPO, "scratch"), "runs")


def resolve_run(title: str, tag: str, environ: dict) -> str:
    """A run directory from a path or a tag."""
    if os.path.isdir(tag):
        return tag
    return os.path.join(runs_root(environ), title, tag)


def sequence(n_arms: int, reps: int, order: str) -> list[int]:
    """Arm indices in execution order: the pattern (letters A.. = arm 0..) cycled, each arm stopping at `reps` runs."""
    letters = [ord(c.upper()) - 65 for c in order if c.isalpha()]
    if not letters or any(i < 0 or i >= n_arms for i in letters) or set(letters) != set(range(n_arms)):
        raise ValueError(f"--order {order!r} must use each of the {n_arms} arms' letters (A..)")
    left, seq = [reps] * n_arms, []
    while any(left):
        for i in letters:
            if left[i]:
                seq.append(i)
                left[i] -= 1
    return seq


def parse_arm(s: str) -> tuple[str, str]:
    label, sep, rest = s.partition(":")
    if not sep or not label:
        raise ValueError(f"--arm needs LABEL:... (got {s!r})")
    return label, rest


def run_metrics(run_dir: str, window: str = "steady") -> dict:
    """p50 / p95 / fps of one run over the window; raises FramesError when the run has no usable data."""
    f = fr.load_run_frames(run_dir)
    if window == "steady":
        w = fr.steady_window(f)
    elif window.startswith("last:"):
        w = fr.last_window(f, float(window[5:]))
    elif window.startswith("t:"):
        a, _, b = window[2:].partition("-")
        w = fr.time_window(f, float(a), float(b))
    else:
        raise ValueError(f"bad --window {window!r}")
    s = w.stats
    return dict(run=os.path.basename(os.path.normpath(run_dir)), n=s["n"], p50=s["p50"], p95=s["p95"], fps=s["fps"],
                short=w.short, covered_s=w.covered_s, notes=w.notes)


def analyse(arms: list[tuple[str, list[str]]], title: str, environ: dict, window: str = "steady", min_effect: float = 0.0) -> dict:
    """arms = [(label, [tag or dir, ...]), ...] (two arms). -> per-arm runs and the B-vs-A comparisons."""
    res = dict(window=window, arms=[], compare={})
    for label, tags in arms:
        runs, skipped = [], []
        for t in tags:
            try:
                runs.append(run_metrics(resolve_run(title, t, environ), window))
            except (fr.FramesError, OSError) as e:
                skipped.append(dict(run=t, reason=str(e)))
        res["arms"].append(dict(label=label, runs=runs, skipped=skipped))
    if len(res["arms"]) == 2:
        a, b = res["arms"]
        for key, lower in (("p50", True), ("p95", True), ("fps", False)):
            res["compare"][key] = st.compare([r[key] for r in a["runs"]], [r[key] for r in b["runs"]], lower_is_better=lower, min_effect=min_effect)
    return res


def format_text(res: dict) -> str:
    out = []
    for a in res["arms"]:
        out.append(f"arm {a['label']}: {len(a['runs'])} run(s)")
        for r in a["runs"]:
            out.append(f"  {r['run']}: n={r['n']} p50 {r['p50']:.2f} p95 {r['p95']:.2f} ms, {r['fps']:.1f} fps" + (f"  (SHORT: {r['covered_s']:.0f} s steady)" if r["short"] else ""))
        for s in a["skipped"]:
            out.append(f"  skipped {s['run']}: {s['reason']}")
        if a["runs"]:
            v = [r["p50"] for r in a["runs"]]
            out.append(f"  p50 mean {st.mean(v):.2f} ms, between-run spread {st.spread(v):.2f} ms")
    names = [a["label"] for a in res["arms"]]
    for key, c in res["compare"].items():
        unit = "fps" if key == "fps" else "ms"
        if c["diff"] is None:
            out.append(f"{key}: {c['verdict']} ({c['reason']})")
        else:
            out.append(f"{key} {names[1]} vs {names[0]}: {c['diff']:+.2f} {unit} (95% CI {c['ci_lo']:+.2f} .. {c['ci_hi']:+.2f}), {c['runs_a']}+{c['runs_b']} runs -> {c['verdict'].upper()}" + (f" ({c['reason']})" if c["verdict"] == "no-difference" else ""))
    return "\n".join(out)


def main(argv: list[str], environ: Optional[dict] = None) -> int:
    environ = dict(os.environ if environ is None else environ)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    VAL = {"--arm": "arm", "--reps": "reps", "--order": "order", "--scene": "scene", "--until": "until", "--class": "cls", "--prefix": "prefix",
           "--window": "window", "--min-effect": "min_effect", "--secs": "secs"}
    o: dict = dict(arm=[], reps=3, order="ABBA", window="steady", min_effect=0.0, cls="perf")
    pos, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in VAL:
            if i + 1 >= len(argv):
                print(f"nps5 ab: {a} needs a value", file=sys.stderr)
                return 2
            v = argv[i + 1]
            if a == "--arm":
                o["arm"].append(v)
            else:
                try:
                    o[VAL[a]] = int(v) if a == "--reps" else float(v) if a == "--min-effect" else v
                except ValueError:
                    print(f"nps5 ab: bad value for {a}: {v}", file=sys.stderr)
                    return 2
            i += 1
        elif a in ("--from-runs", "--json", "--dry-run"):
            o[a[2:].replace("-", "_")] = True
        elif a.startswith("--"):
            print(f"nps5 ab: unknown option {a}", file=sys.stderr)
            return 2
        else:
            pos.append(a)
        i += 1
    if len(pos) != 1 or len(o["arm"]) != 2:
        print("usage: nps5 ab <id> --arm A:... --arm B:... (exactly two arms); see nps5 ab --help", file=sys.stderr)
        return 2
    title = pos[0]
    try:
        arms = [parse_arm(s) for s in o["arm"]]
        if o.get("from_runs"):
            sel = [(l, [t for t in rest.split(",") if t]) for l, rest in arms]
        else:
            if o["reps"] < 1:
                raise ValueError("--reps must be at least 1")
            seq = sequence(2, o["reps"], o["order"])
            prefix = o.get("prefix") or time.strftime("ab%m%d%H%M")
            counts = [0, 0]
            plan = []
            for idx in seq:
                counts[idx] += 1
                tag = f"{prefix}-{arms[idx][0]}{counts[idx]}"
                cmd = [sys.executable, os.path.join(REPO, "scripts", "nps5"), "run", title, "--arm", f"{arms[idx][0]}:{arms[idx][1]}", "--tag", tag, "--class", o["cls"]]
                for k, flag in (("scene", "--scene"), ("until", "--until"), ("secs", "--secs")):
                    if o.get(k):
                        cmd += [flag, str(o[k])]
                plan.append((idx, tag, cmd))
            if o.get("dry_run"):
                for idx, tag, cmd in plan:
                    print(f"[{arms[idx][0]}] " + " ".join(cmd[1:]))
                return 0
            tags: list[list[str]] = [[], []]
            for idx, tag, cmd in plan:
                rc = subprocess.call(cmd, env=environ)
                if rc != 0:
                    print(f"nps5 ab: run {tag} exited {rc}; stopping (completed runs are kept under their tags)", file=sys.stderr)
                    return rc
                tags[idx].append(tag)
            sel = [(arms[0][0], tags[0]), (arms[1][0], tags[1])]
        res = analyse(sel, title, environ, o["window"], o["min_effect"])
    except ValueError as e:
        print(f"nps5 ab: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res) if o.get("json") else format_text(res))
    return 0 if all(a["runs"] for a in res["arms"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
