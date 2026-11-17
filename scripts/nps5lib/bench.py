#!/usr/bin/env python3
"""nps5 bench: named benchmark routes of games/<id>/bench.tsv, run as perf-class `nps5 run` in ABBA order. Standard library only.

  nps5 bench <id> --route NAME [--arm LABEL:K=V,... --arm ...] [--runs N] [--order abba|abab|AB..] [--out DIR]
                  [--require-quiet] [--quiet-wait S] [--min-effect MS] [--dry-run]
  nps5 bench <id> --list                       the routes of bench.tsv
  nps5 bench <id> --route NAME --from-runs --arm A:tag1,tag2 --arm B:tag3,tag4      analyse runs that exist (no title run)

bench.tsv columns: name, scene (scenes.tsv), window, class, description. Window: marker:<m>+<s> (from s seconds after the marker row of
game.tsv until 1 s before the end), present:<scene>+<n> (from the n-th present after that scene opened), <a>s..<b>s (run seconds after the
first present). Times come from the frame log and the `[scene-open] <name> t=<s>` lines of run.err. No --arm = one arm "base" (the profile
as it is). Two or more arms are run in the --order pattern cycled (default ABBA; arm letters A..), --runs each (default 2).
Quiet-machine gate before every run (the checks prepare_golden.sh quiet_gpu made): AC power, no other GPU process, other GPU use under
50 ms/s, no start hold. Not quiet: wait up to --quiet-wait s (default 0), then the run is marked NOISY (--require-quiet refuses instead).
Output: <out>/results.json, results.md (paste-ready), runs.csv, arms.csv. Default out: <scratch>/titles/<id>/bench/<date>-<route>/.
Per arm: p50 / p95 / p99 frame ms in the window, fps, frames, live pipeline compiles, verdicts. Per pair: difference of per-run p50 with its
CI and the between-run spread (scripts/nps5lib/stats.py: one run per arm never gets a verdict). Exit: 0 ok, 1 no usable data / a run failed, 2 usage."""
import csv, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import frames as fr  # noqa: E402
import stats as st  # noqa: E402
import ab  # noqa: E402
import run as runmod  # noqa: E402

OTHERS_MAX_MS = 50.0
SCENE_OPEN = re.compile(r"\[(?:scene|level)-open\] (\S+)(?: t=(\d+(?:\.\d+)?))?")


class BenchError(Exception):
    def __init__(self, msg, code=2):
        super().__init__(msg); self.code = code


# ---------------------------------------------------------------- bench.tsv
def read_routes(path):
    routes = {}
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if not line.strip() or line.startswith("#"):
                continue
            c = line.rstrip("\n").split("\t")
            if len(c) < 4:
                raise BenchError("%s:%d: need name, scene, window, class" % (path, n))
            routes[c[0]] = dict(name=c[0], scene=c[1], window=c[2], cls=c[3], description=c[4] if len(c) > 4 else "")
            if c[3] not in ("perf", "func"):
                raise BenchError("%s:%d: class must be perf or func" % (path, n))
    return routes


# ---------------------------------------------------------------- window maths (pure)
def scene_opens(err_text):
    """{scene name: t} from `[scene-open] <name> t=<s>` (first open wins)."""
    out = {}
    for m in SCENE_OPEN.finditer(err_text):
        if m.group(2) is not None and m.group(1) not in out:
            out[m.group(1)] = float(m.group(2))
    return out


def marker_time(spec, opens, err_text, scene_key="levels/", frames=None):
    """Run-clock time of a game.tsv marker spec: open:<re> (a scene-open whose <key><name>/ matches), log:<re> (first matching line
    that carries t=<s>), present:<n> (end of the n-th frame)."""
    kind, _, arg = spec.partition(":")
    if kind == "open":
        hits = [t for n, t in opens.items() if re.search(arg, "%s%s/" % (scene_key, n))]
        if hits:
            return min(hits)
        raise fr.FramesError("marker %s never opened" % spec)
    if kind == "log":
        for line in err_text.splitlines():
            if re.search(arg, line):
                m = re.search(r"\bt=([\d.]+)", line)
                if m:
                    return float(m.group(1))
        raise fr.FramesError("marker %s: no matching log line with t=" % spec)
    if kind == "present" and frames is not None and arg.isdigit() and 0 < int(arg) <= len(frames):
        return frames.t[int(arg) - 1]
    raise fr.FramesError("marker %s cannot be resolved" % spec)


def window_bounds(window, frames, total, opens, markers=None, err_text="", scene_key="levels/"):
    """(start, end) in run seconds. end = total - 1 for marker / present windows (the last second holds the shutdown)."""
    m = re.fullmatch(r"marker:([\w.-]+)\+(\d+(?:\.\d+)?)", window)
    if m:
        spec = (markers or {}).get(m.group(1))
        if spec is None:
            raise fr.FramesError("marker %s is not a game.tsv marker row" % m.group(1))
        return marker_time(spec, opens, err_text, scene_key, frames) + float(m.group(2)), total - 1
    m = re.fullmatch(r"present:([\w:.-]+)\+(\d+)", window)
    if m:
        if m.group(1) not in opens:
            raise fr.FramesError("%s was not opened" % m.group(1))
        after = [i for i in range(len(frames)) if frames.t[i] >= opens[m.group(1)]]
        n = int(m.group(2))
        if len(after) <= n:
            raise fr.FramesError("only %d presents after %s opened (need %d)" % (len(after), m.group(1), n))
        return frames.t[after[n]], total - 1
    m = re.fullmatch(r"(\d+(?:\.\d+)?)s\.\.(\d+(?:\.\d+)?)s", window)
    if m:
        t0 = frames.t[0]
        return t0 + float(m.group(1)), t0 + float(m.group(2))
    raise BenchError("bad window %r (marker:<m>+<s> | present:<scene>+<n> | <a>s..<b>s)" % window)


def measure(run_dir, window, markers=None, scene_key="levels/"):
    """One run -> dict(n, p50, p95, p99, fps, start, end, compiles, verdict, rc, short). Raises FramesError without usable data."""
    f = fr.load_run_frames(run_dir)
    total = fr.run_total_seconds(run_dir) or f.t[-1]
    try:
        err = open(os.path.join(run_dir, "run.err"), errors="replace").read()
    except OSError:
        err = ""
    a, b = window_bounds(window, f, total, scene_opens(err), markers, err, scene_key)
    w = fr.time_window(f, a, b)
    sel = [f.ms[i] for i in range(len(f)) if a <= f.t[i] <= b]
    cm = re.findall(r"pipelines archive-hit \d+ \(\d+ ms\) compiled (\d+)", err)
    rj = {}
    try:
        rj = json.load(open(os.path.join(run_dir, "run.json")))
    except (OSError, ValueError):
        pass
    s = w.stats
    return dict(run=os.path.basename(os.path.normpath(run_dir)), n=s["n"], p50=s["p50"], p95=s["p95"], p99=st.pct(sel, .99), fps=s["fps"],
                start=round(a, 2), end=round(b, 2), short=w.short, compiles=int(cm[-1]) if cm else None,
                verdict=rj.get("verdict"), rc=rj.get("rc"), peak_mib=(rj.get("timings") or {}).get("peak_footprint_mib"))


# ---------------------------------------------------------------- quiet gate
def quiet_gate(power=runmod.power_source, others=runmod.gpu_others, procs=runmod.gpu_processes, hold=None):
    """-> (quiet: bool, reasons)."""
    why = []
    if power() != "AC":
        why.append("not on AC power")
    pr = procs()
    if pr:
        why.append("another GPU process: " + pr[0].strip()[:60])
    o = others()
    if o is not None and o > OTHERS_MAX_MS:
        why.append("other GPU use %.0f ms/s (limit %.0f)" % (o, OTHERS_MAX_MS))
    if hold and hold():
        why.append("start hold is set")
    return (not why), why


def start_hold(guard, now=time.time):
    """The machine guard's start hold (<guard>/start.hold, fresh for 30 s): the machine is too loaded for a title run."""
    h = os.path.join(guard, "start.hold")
    try:
        return now() - os.stat(h).st_mtime < 30
    except OSError:
        return False


def wait_quiet(gate, wait_s, sleep=time.sleep, now=time.time):
    t0 = now()
    while True:
        ok, why = gate()
        if ok or now() - t0 >= wait_s:
            return ok, why
        sleep(min(20, max(1, wait_s)))


# ---------------------------------------------------------------- aggregate
def summarize_arms(per_arm, min_effect=0.0):
    """per_arm = [(label, [measure dict...]), ...] -> dict(arms=[...], pairs=[...]). Pairs compare every later arm with the first."""
    arms = []
    for label, runs in per_arm:
        p50 = [r["p50"] for r in runs]
        arms.append(dict(label=label, runs=runs, p50_mean=st.mean(p50) if p50 else None, p50_spread=st.spread(p50),
                         p95_mean=st.mean([r["p95"] for r in runs]) if runs else None, p99_mean=st.mean([r["p99"] for r in runs]) if runs else None,
                         fps_mean=st.mean([r["fps"] for r in runs]) if runs else None, noisy=any(r.get("noisy") for r in runs)))
    pairs = []
    for b in arms[1:]:
        for key, lower in (("p50", True), ("p95", True), ("fps", False)):
            pairs.append(dict(a=arms[0]["label"], b=b["label"], metric=key,
                              **st.compare([r[key] for r in arms[0]["runs"]], [r[key] for r in b["runs"]], lower_is_better=lower, min_effect=min_effect if lower else 0.0)))      # --min-effect is in ms: not a threshold for fps
    return dict(arms=arms, pairs=pairs)


def render_md(route, res, meta):
    o = ["### nps5 bench %s: route `%s` (%s)" % (meta["title"], route["name"], route["window"]), "",
         "Scene `%s`, class %s, order %s, %d run(s) per arm. %s" % (route["scene"], route["cls"], meta["order"], meta["runs"], "**NOISY: the machine was not quiet** (%s)." % "; ".join(meta["noisy_reasons"]) if meta["noisy_reasons"] else "Quiet machine gate passed for every run."),
         "", "| arm | runs | p50 ms | p95 ms | p99 ms | fps | frames | live compiles | spread p50 |", "|---|---|---|---|---|---|---|---|---|"]
    for a in res["arms"]:
        if not a["runs"]:
            o.append("| %s | 0 | - | - | - | - | - | - | - |" % a["label"]); continue
        comp = [r["compiles"] for r in a["runs"] if r["compiles"] is not None]
        o.append("| %s%s | %d | %.2f | %.2f | %.2f | %.1f | %d | %s | %.2f |" % (a["label"], " (NOISY)" if a["noisy"] else "", len(a["runs"]), a["p50_mean"], a["p95_mean"], a["p99_mean"],
                                                                             a["fps_mean"], sum(r["n"] for r in a["runs"]), "/".join(map(str, comp)) or "-", a["p50_spread"]))
    for p in res["pairs"]:
        unit = "fps" if p["metric"] == "fps" else "ms"
        o.append("- %s, %s vs %s: %s" % (p["metric"], p["b"], p["a"], "%s (%s)" % (p["verdict"].upper(), p["reason"]) if p["diff"] is None else
                                       "%+.2f %s (95%% CI %+.2f .. %+.2f), %d+%d runs -> %s" % (p["diff"], unit, p["ci_lo"], p["ci_hi"], p["runs_a"], p["runs_b"], p["verdict"].upper())))
    bad = [(a["label"], r["run"], r["verdict"]) for a in res["arms"] for r in a["runs"] if r["verdict"] not in (None, "DONE_FIXED", "DONE_UNTIL")]
    if bad:
        o += ["", "Verdicts other than DONE: " + ", ".join("%s/%s=%s" % b for b in bad)]
    return "\n".join(o) + "\n"


def write_outputs(out, route, res, meta):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(dict(meta=meta, route=route, **res), f, indent=1); f.write("\n")
    with open(os.path.join(out, "results.md"), "w") as f:
        f.write(render_md(route, res, meta))
    with open(os.path.join(out, "runs.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["route", "arm", "run", "n", "p50_ms", "p95_ms", "p99_ms", "fps", "start_s", "end_s", "compiles", "peak_mib", "rc", "verdict", "noisy"])
        for a in res["arms"]:
            for r in a["runs"]:
                w.writerow([route["name"], a["label"], r["run"], r["n"], "%.3f" % r["p50"], "%.3f" % r["p95"], "%.3f" % r["p99"], "%.2f" % r["fps"], r["start"], r["end"],
                            r["compiles"] if r["compiles"] is not None else "", r["peak_mib"] or "", r["rc"] if r["rc"] is not None else "", r["verdict"] or "", int(bool(r.get("noisy")))])
    with open(os.path.join(out, "arms.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["route", "arm", "runs", "p50_mean", "p95_mean", "p99_mean", "fps_mean", "p50_spread", "noisy"])
        for a in res["arms"]:
            if a["runs"]:
                w.writerow([route["name"], a["label"], len(a["runs"]), "%.3f" % a["p50_mean"], "%.3f" % a["p95_mean"], "%.3f" % a["p99_mean"], "%.2f" % a["fps_mean"], "%.3f" % a["p50_spread"], int(a["noisy"])])


# ---------------------------------------------------------------- CLI
def parse(argv):
    o = dict(arm=[], runs=2, order="ABBA", out=None, route=None, min_effect=0.0, quiet_wait=0.0)
    VAL = {"--route": "route", "--arm": "arm", "--runs": "runs", "--order": "order", "--out": "out", "--min-effect": "min_effect", "--quiet-wait": "quiet_wait"}
    pos, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in VAL:
            if i + 1 >= len(argv):
                raise BenchError("%s needs a value" % a)
            k, v = VAL[a], argv[i + 1]
            if k == "arm": o["arm"].append(v)
            else:
                try: o[k] = int(v) if k == "runs" else float(v) if k in ("min_effect", "quiet_wait") else v
                except ValueError: raise BenchError("bad value for %s: %s" % (a, v))
            i += 1
        elif a in ("--dry-run", "--list", "--from-runs", "--require-quiet"):
            o[a[2:].replace("-", "_")] = True
        elif a.startswith("--"):
            raise BenchError("unknown option %s" % a)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 1:
        raise BenchError("usage: nps5 bench <id> --route NAME [--arm A:... --arm B:...] [--runs N]; see --help")
    if o["runs"] < 1:
        raise BenchError("--runs must be at least 1")
    if not (o["quiet_wait"] >= 0 and o["min_effect"] >= 0):
        raise BenchError("--quiet-wait and --min-effect must be numbers >= 0")
    return pos[0], o


def main(argv, environ=None, caller=subprocess.call, gate=None):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0 if argv else 2
    try:
        tid, o = parse(argv)
        environ = runmod.machine_env(dict(os.environ if environ is None else environ))
        g = runmod._game()
        try: p = g.Profile(*g.find_profile(tid, environ))
        except g.Err as e: raise BenchError(str(e))
        bp = os.path.join(p.dir, "bench.tsv")
        if not os.path.isfile(bp):
            raise BenchError("games/%s has no bench.tsv" % tid)
        routes = read_routes(bp)
        if o.get("list"):
            for r in routes.values():
                print("%-12s scene %-26s %-36s %s  %s" % (r["name"], r["scene"], r["window"], r["cls"], r["description"]))
            return 0
        if o["route"] not in routes:
            raise BenchError("--route is required; routes: %s" % ", ".join(routes))
        route = routes[o["route"]]
        if g.scene_lookup(p.scenes(), route["scene"]) is None:
            raise BenchError("route %s: scene %s is not in scenes.tsv" % (route["name"], route["scene"]))
        markers = {c[0]: c[1] for c in p.get("marker") if len(c) > 1}
        scene_key = p.one("scene_open_key", "levels/") or "levels/"
        arm_specs = [ab.parse_arm(s) for s in o["arm"]] or [("base", "")]
        labels = [l for l, _ in arm_specs]
        if len(set(labels)) != len(labels):
            raise BenchError("arm labels must differ")
        order = o["order"].upper()
        if len(arm_specs) == 1:
            order = "A"
        elif len(arm_specs) > 2 and set(order) != set(chr(65 + i) for i in range(len(arm_specs))):
            order = "".join(chr(65 + i) for i in range(len(arm_specs)))      # ABBA is a two-arm pattern: more arms go ABC.. cycled
        seq = ab.sequence(len(arm_specs), o["runs"], order)
        prefix = time.strftime("bench%m%d%H%M")
        plan, counts = [], [0] * len(arm_specs)
        for idx in seq:
            counts[idx] += 1
            tag = "%s-%s%d" % (prefix, labels[idx], counts[idx])
            cmd = [sys.executable, os.path.join(REPO, "scripts", "nps5"), "run", tid, "--scene", route["scene"], "--class", route["cls"], "--tag", tag]
            if arm_specs[idx][1]:
                cmd += ["--arm", "%s:%s" % arm_specs[idx]]
            plan.append((idx, tag, cmd))
        out = o["out"] or os.path.join(environ["NPS5_SCRATCH"], "titles", tid, "bench", "%s-%s" % (time.strftime("%Y%m%d"), route["name"]))
        runs_root = ab.runs_root(environ)
        if o.get("dry_run"):
            for idx, tag, cmd in plan:
                print("[%s] %s" % (labels[idx], " ".join(cmd[1:])))
            print("quiet gate before every run; output %s" % out); return 0
        noisy_reasons, tags = [], [[] for _ in arm_specs]
        if o.get("from_runs"):
            for l, rest in arm_specs:
                tags[labels.index(l)] = [t for t in rest.split(",") if t]
            noisy = {}
        else:
            guard = environ.get("NPS5_GUARD_DIR") or os.path.join(environ["NPS5_SCRATCH"], "guard")
            gate = gate or (lambda: quiet_gate(hold=lambda: start_hold(guard)))
            noisy = {}
            for idx, tag, cmd in plan:
                ok, why = wait_quiet(gate, o["quiet_wait"])
                if not ok:
                    if o.get("require_quiet"):
                        raise BenchError("machine not quiet (%s); --require-quiet" % "; ".join(why), 75)
                    noisy[tag] = True; noisy_reasons += [w for w in why if w not in noisy_reasons]
                rc = caller(cmd, env=environ)
                if rc != 0:
                    print("nps5 bench: run %s exited %s; stopping (finished runs are kept)" % (tag, rc), file=sys.stderr); return 1
                tags[idx].append(tag)
        per_arm = []
        for (l, _), ts in zip(arm_specs, tags):
            rs = []
            for t in ts:
                try:
                    r = measure(ab.resolve_run(tid, t, environ) if not os.path.isdir(t) else t, route["window"], markers, scene_key)
                    r["noisy"] = bool(noisy.get(t)); rs.append(r)
                except (fr.FramesError, OSError) as e:
                    print("nps5 bench: skipped %s: %s" % (t, e), file=sys.stderr)
            per_arm.append((l, rs))
        res = summarize_arms(per_arm, o["min_effect"])
        meta = dict(title=tid, order=order, runs=o["runs"], noisy_reasons=noisy_reasons, date=time.strftime("%Y-%m-%d"), runs_root=runs_root)
        write_outputs(out, route, res, meta)
        sys.stdout.write(render_md(route, res, meta)); print("written: %s" % out)
        return 0 if all(a["runs"] for a in res["arms"]) else 1
    except BenchError as e:
        print("nps5 bench: %s" % e, file=sys.stderr); return e.code
    except runmod.Refuse as e:
        print("nps5 bench: %s" % e, file=sys.stderr); return e.code
    except OSError as e:
        print("nps5 bench: %s" % e, file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
