#!/usr/bin/env python3
"""nps5 regress: does a title still boot and draw on this stack? Standard library only.

  nps5 regress check    [--games ID,...] [--quick] [--record] [--baseline REV] [--out DIR] [--tolerance PCT] [--dry-run]
  nps5 regress baseline [--games ID,...] [--quick] [--record] [--baseline REV] [--dry-run]
  nps5 regress list                       profiles, smoke routes, stored baselines, validated rows and whether they are current

Per game: static checks first (no title run: game.py check, the pipeline list's .verified matches its bytes, `nps5 patch --check` on
the canonical binary when it exists), then the games/<id>/smoke.tsv routes of the mode through `nps5 run --class func`.
smoke.tsv: name, scene, mode (quick | full | quick,full), reach (<until spec>@<s>), mem_max_mib, stall_ms_max, description.
--quick runs the routes whose mode has quick, the default (full) runs the routes whose mode has full.
Green / red rule, per route, from run.json and the run's own files (never from a human reading): GREEN needs verdict DONE_UNTIL or
DONE_FIXED (the reach target was reached: TIMEOUT is red; CRASH, FAIL_RC<n>, EARLY_BLACK, EARLY_STALL, DUMP_MODIFIED are red; rc 86 is
red as a memory finding, not a crash), no `[RED]` line in run.err, peak footprint (run.json timings.peak_footprint_mib, when the run
recorded it) at most mem_max_mib, the largest frame after the steady window starts at most stall_ms_max, and a real picture: frame
dumps (pics.py: not black, not flat) or the last `[pstat]` line (black fraction under 0.9); no picture evidence at all is red.
Game status: PASS (all green, no baseline, or within tolerance), CHANGED (green, p95 moved more than 2% but within --tolerance, default
10%), REGRESSION (a red route, or p95 worse than the baseline by more than the tolerance), ERROR (a run could not be judged: no run.json,
refused, static check failed). Baselines: <scratch>/titles/<id>/regress/baselines/<REV, default latest>/<mode>/<route>.json.
--record writes games/<id>/validated.tsv (one row per mode and machine class) ONLY when the game is PASS or CHANGED, every route of the
mode ran green, the profile is in games/ (never a provisional one from NPS5_GAMES_DIR) and the machine class is known. A red result
leaves the rows as they were. NPS5_REGRESS_SKIP=ID,... leaves out a title another agent is working on.
Exit: 0 all PASS/CHANGED, 1 a REGRESSION or ERROR, 2 usage."""
import glob, importlib.util, io, json, os, re, shlex, subprocess, sys, time
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import bmp  # noqa: E402
import frames as fr  # noqa: E402
import pics  # noqa: E402
import run as runmod  # noqa: E402

GOOD_VERDICTS = ("DONE_UNTIL", "DONE_FIXED")
PSTAT = re.compile(r"\[pstat\] wall=([\d.]+) presents=(\d+) luma=([\d.]+) black=([\d.]+) sd=([\d.]+) diff=([\d.]+)")
BLACK_FRACTION_MAX = 0.9
DUMP_LUMA_MIN = 6.0            # mean luma (0-255) of the last dump below this: black screen
DUMP_SHARP_MIN = 0.05          # a flat colour has no edges
MOVED_PCT = 2.0


class RegressError(Exception):
    def __init__(self, msg, code=2):
        super().__init__(msg); self.code = code


# ---------------------------------------------------------------- smoke.tsv
def read_smoke(path):
    routes = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if not line.strip() or line.startswith("#"):
                continue
            c = line.rstrip("\n").split("\t") + [""] * 7
            m = re.fullmatch(r"(.+)@(\d+)", c[3])
            if not m or not c[4].isdigit() or not c[5].isdigit() or c[2] not in ("quick", "full", "quick,full"):
                raise RegressError("%s:%d: need name scene mode(quick|full|quick,full) reach(<until>@<s>) mem_max_mib stall_ms_max" % (path, n))
            routes.append(dict(name=c[0], scene=c[1], modes=c[2].split(","), until=m.group(1), secs=int(m.group(2)), mem_max=int(c[4]), stall_max=int(c[5]), description=c[6]))
    return routes


def select_routes(routes, quick):
    return [r for r in routes if ("quick" if quick else "full") in r["modes"]]


# ---------------------------------------------------------------- picture check (pics.py / bmp.py, or [pstat])
def picture_check(run_dir, err_text=""):
    """-> dict(ok, source, detail). dumps: the last dump must be neither black nor flat; pstat: the last line's black fraction."""
    d = pics.dump_dir(run_dir)
    dumps = pics.list_dumps(d)
    if dumps:
        series = pics.flicker_series(dumps)
        last = {}
        for (s, c), p in dumps.items():
            if s not in last or c > last[s][0]:
                last[s] = (c, p)
        worst = None
        for s, (_, p) in last.items():
            im = bmp.read_bmp(p); y = bmp.luma(im, 100_000)
            mean = sum(sum(r) for r in y) / max(1, sum(len(r) for r in y)); sh = bmp.sharpness(y)
            if mean < DUMP_LUMA_MIN or sh < DUMP_SHARP_MIN:
                worst = "%s: mean luma %.1f, sharpness %.2f (black or flat)" % (os.path.basename(p), mean, sh)
        return dict(ok=worst is None, source="dumps", detail=worst or "%d dump series, last frames drawn" % len(series))
    ps = PSTAT.findall(err_text)
    if ps:
        black = float(ps[-1][3])
        return dict(ok=black < BLACK_FRACTION_MAX, source="pstat", detail="last [pstat] black=%.2f luma=%s" % (black, ps[-1][2]))
    return dict(ok=False, source="none", detail="no picture evidence (no frame dumps, no [pstat] line)")


# ---------------------------------------------------------------- one route (pure on its inputs)
def judge_route(route, rj, frames, err_text, pic):
    """-> dict(name, status green|red|error, reasons[], metrics). `rj` None = no run.json, `frames` a fr.Frames or None."""
    r = dict(name=route["name"], status="green", reasons=[], metrics={})
    if rj is None:
        return dict(r, status="error", reasons=["no run.json (the run was refused or did not start)"])
    bad = runmod.validate_run_json(rj)
    if bad:
        return dict(r, status="error", reasons=["run.json invalid: " + "; ".join(bad[:3])])
    v, rc = rj.get("verdict"), rj.get("rc")
    if v == "RUNNING":
        return dict(r, status="error", reasons=["run.json says RUNNING (run killed or still going)"])
    red = r["reasons"]
    if rc == 86:
        red.append("rc 86: memory limit reached (a memory finding, not a fault)")
    elif v == "TIMEOUT":
        red.append("reach target %s not reached within %ss" % (route["until"], route["secs"]))
    elif v not in GOOD_VERDICTS:
        red.append("verdict %s (rc %s)" % (v, rc))
    kinds = sorted(set(re.findall(r"\[RED\] ?(\S+)", err_text)))
    if kinds:
        red.append("[RED] lines: " + ", ".join(kinds[:4]))
    peak = (rj.get("timings") or {}).get("peak_footprint_mib")
    if peak is not None:
        r["metrics"]["peak_mib"] = peak
        if peak > route["mem_max"]:
            red.append("peak footprint %s MiB over %d" % (peak, route["mem_max"]))
    if frames is not None and len(frames):
        try:
            w = fr.steady_window(frames)
            r["metrics"].update(p50=round(w.stats["p50"], 3), p95=round(w.stats["p95"], 3), fps=round(w.stats["fps"], 2), n=w.stats["n"])
            after = [frames.ms[i] for i in range(len(frames)) if frames.t[i] >= w.start_s]
            worst = max(after) if after else 0.0
            r["metrics"]["stall_ms"] = round(worst, 1)
            if worst > route["stall_max"]:
                red.append("largest frame after the window start %.0f ms over %d" % (worst, route["stall_max"]))
        except fr.FramesError as e:
            r["metrics"]["window"] = str(e)
    r["metrics"]["picture"] = pic["source"]
    if not pic["ok"]:
        red.append("picture: " + pic["detail"])
    if red:
        r["status"] = "red"
    return r


def compare_baseline(metrics, base, tol_pct):
    """-> (state, note): same | moved | worse. Compares p95 (the number a stall shows up in) with the stored baseline."""
    if not base or base.get("p95") is None or metrics.get("p95") is None:
        return "same", "no baseline"
    d = 100.0 * (metrics["p95"] - base["p95"]) / base["p95"]
    note = "p95 %.2f vs baseline %.2f ms (%+.1f%%)" % (metrics["p95"], base["p95"], d)
    if d > tol_pct:
        return "worse", note
    return ("moved" if abs(d) > MOVED_PCT else "same"), note


def game_status(static, routes, baselines, tol_pct, expected):
    """-> (status, notes). static: [(name, ok|skip|fail, detail)]; routes: judge_route results; expected: route count that had to run."""
    notes = []
    if any(s[1] == "fail" for s in static):
        return "ERROR", ["static check failed: " + "; ".join("%s: %s" % (s[0], s[2]) for s in static if s[1] == "fail")]
    if any(x["status"] == "error" for x in routes) or len(routes) < expected:
        return "ERROR", ["%s: %s" % (x["name"], "; ".join(x["reasons"])) for x in routes if x["status"] == "error"] or ["fewer routes ran than the mode needs"]
    if any(x["status"] == "red" for x in routes):
        return "REGRESSION", ["%s: %s" % (x["name"], "; ".join(x["reasons"])) for x in routes if x["status"] == "red"]
    state = "PASS"
    for x in routes:
        s, n = compare_baseline(x["metrics"], baselines.get(x["name"]), tol_pct)
        if s == "worse":
            state = "REGRESSION"; notes.append("%s: %s over the %.0f%% tolerance" % (x["name"], n, tol_pct))
        elif s == "moved" and state == "PASS":
            state = "CHANGED"; notes.append("%s: %s" % (x["name"], n))
    return state, notes


# ---------------------------------------------------------------- validated.tsv
ROW_KEYS = ("kyty", "kk", "ps5recomp", "lineage", "date", "class", "machine", "macos")


def validated_row(mode, rjs, cls, macos, date):
    """One validated.tsv line from the run.json files of a green mode. The component commits of every route must agree."""
    comps = {k: {(rj.get("components") or {}).get(k) for rj in rjs} for k in ("kyty", "kk", "ps5recomp")}
    for k, vs in comps.items():
        if len(vs) != 1 or None in vs:
            raise RegressError("routes ran on different or unknown %s commits (%s): not recorded" % (k, ", ".join(sorted(map(str, vs)))), 1)
    first = rjs[0]
    lin = "%s+%s" % (first.get("lineage") or "unknown", "+".join(first.get("patches") or []) or "none")
    return "%s\tkyty=%s kk=%s ps5recomp=%s lineage=%s date=%s class=%s machine=%s macos=%s" % (
        mode, next(iter(comps["kyty"]))[:10], next(iter(comps["kk"]))[:10], next(iter(comps["ps5recomp"]))[:10], lin, date, cls,
        (first.get("machine") or {}).get("model") or "unknown", macos or "unknown")


def update_validated(path, row):
    """Replace the row with the same mode and class, keep every other line (comments and other classes), else append."""
    mode, _, rest = row.partition("\t")
    cls = re.search(r"class=(\S+)", rest).group(1)
    lines, done = [], False
    if os.path.isfile(path):
        for l in open(path).read().splitlines():
            m = re.match(r"([a-z,]+)\t.*\bclass=(\S+)", l)
            if m and m.group(1) == mode and m.group(2) == cls:
                if not done: lines.append(row); done = True
                continue
            lines.append(l)
    if not done:
        lines.append(row)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def may_record(provisional, cls, status, routes, expected):
    """-> None when allowed, else the refusal reason. The rule in one place: green only, never provisional, class known."""
    if provisional:
        return "profile is provisional (found outside games/): never recorded"
    if not cls:
        return "machine class unknown (NPS5_HW_CLASS / scripts/nps5lib/hw.py class): cannot write class="
    if status not in ("PASS", "CHANGED"):
        return "status %s: validated.tsv is written only for PASS or CHANGED" % status
    if len(routes) < expected or expected == 0 or any(x["status"] != "green" for x in routes):
        return "not every route of the mode ran green"
    return None


# ---------------------------------------------------------------- static checks
def static_checks(tid, environ, gmod=None, patcher=None):
    out = []
    g = gmod or runmod._game()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = g.cmd_check([tid], environ)
    out.append(("game.py check", "ok" if rc == 0 else "fail", (buf.getvalue().strip().splitlines() or [""])[-1]))
    try:
        p = g.Profile(*g.find_profile(tid, environ))
        lst = os.path.join(p.dir, "pipelines.kyplst")
        if os.path.isfile(lst):
            import hashlib
            want = None
            vp = lst + ".verified"
            if os.path.isfile(vp):
                m = re.search(r"^sha256=(\w+)", open(vp).read(), re.M); want = m.group(1) if m else None
            have = hashlib.sha256(open(lst, "rb").read()).hexdigest()
            out.append(("pipeline list verified", "ok" if want == have else "fail", "pipelines.kyplst.verified matches" if want == have else ".verified missing or for other bytes: nps5 cache verify-list %s" % tid))
    except g.Err:
        pass
    if patcher:
        out.append(patcher(tid, environ))
    return out


def patch_check(tid, environ):
    try:
        plan = runmod.compose(tid, {"class": "func"}, environ)
    except Exception as e:
        return ("patch --check", "skip", "cannot compose a run: %s" % e)
    src = plan.sel.get("src") or plan.sel["path"]
    if not plan.sel.get("lineage") or not os.path.isfile(src):
        return ("patch --check", "skip", "no canonical binary at %s" % src)
    spec = importlib.util.spec_from_file_location("nps5_patch", os.path.join(HERE, "patch.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.main([tid, "--check", src], environ)
    return ("patch --check", "ok" if rc == 0 else "fail", os.path.basename(src))


# ---------------------------------------------------------------- baselines
def baseline_dir(environ, tid, rev, mode):
    return os.path.join(environ["NPS5_SCRATCH"], "titles", tid, "regress", "baselines", rev, mode)


def load_baselines(d):
    out = {}
    for p in glob.glob(os.path.join(d, "*.json")):
        try: out[os.path.basename(p)[:-5]] = json.load(open(p))
        except (OSError, ValueError): pass
    return out


def save_baselines(d, routes):
    os.makedirs(d, exist_ok=True)
    for x in routes:
        with open(os.path.join(d, x["name"] + ".json"), "w") as f:
            json.dump(x["metrics"], f, indent=1); f.write("\n")


# ---------------------------------------------------------------- one game
def run_route(tid, route, tag):
    cmd = [sys.executable, os.path.join(REPO, "scripts", "nps5"), "run", tid, "--scene", route["scene"], "--class", "func", "--tag", tag,
           "--until", route["until"], "--secs", str(route["secs"])]
    return cmd


def collect(run_dir):
    rj = None
    try: rj = json.load(open(os.path.join(run_dir, "run.json")))
    except (OSError, ValueError): pass
    try: err = open(os.path.join(run_dir, "run.err"), errors="replace").read()
    except OSError: err = ""
    try: f = fr.load_run_frames(run_dir)
    except (fr.FramesError, OSError): f = None
    return rj, err, f


def regress_game(tid, o, environ, caller=subprocess.call, statics=None, now=time.time, macos=None):
    g = runmod._game()
    try:
        p = g.Profile(*g.find_profile(tid, environ))
    except g.Err as e:
        raise RegressError(str(e))
    sp = os.path.join(p.dir, "smoke.tsv")
    if not os.path.isfile(sp):
        raise RegressError("games/%s has no smoke.tsv" % tid)
    mode = "quick" if o["quick"] else "full"
    routes = select_routes(read_smoke(sp), o["quick"])
    if not routes:
        raise RegressError("games/%s/smoke.tsv has no %s routes" % (tid, mode))
    prefix = time.strftime("rg%m%d%H%M%S", time.localtime(now()))
    root = os.path.join(environ["NPS5_SCRATCH"], "runs", tid)
    plan = []
    for r in routes:                                   # a tag is never reused: two checks in one second get -2, -3 ...
        tag, n = "%s-%s" % (prefix, r["name"]), 1
        while os.path.exists(os.path.join(root, tag)):
            n += 1; tag = "%s-%s-%d" % (prefix, r["name"], n)
        plan.append((r, tag))
    if o["dry"]:
        for r, tag in plan:
            print("[dry-run] %s" % " ".join(shlex.quote(c) for c in run_route(tid, r, tag)[1:]))
        return dict(id=tid, status="DRY", notes=[], routes=[], mode=mode)
    static = (statics or (lambda t, e: static_checks(t, e, patcher=patch_check)))(tid, environ)
    results, rjs = [], []
    if not any(s[1] == "fail" for s in static):
        for r, tag in plan:
            cmd = run_route(tid, r, tag)
            rc = caller(cmd, env=dict(environ, NPS5_RUN_REASON=environ.get("NPS5_RUN_REASON") or "nps5 regress %s %s" % (tid, mode)))
            rj, err, f = collect(os.path.join(root, tag))
            res = judge_route(r, rj, f, err, picture_check(os.path.join(root, tag), err) if rj else dict(ok=False, source="none", detail="no run"))
            res["tag"], res["rc"] = tag, rc
            results.append(res)
            if rj: rjs.append(rj)
    base = load_baselines(baseline_dir(environ, tid, o["baseline"], mode))
    status, notes = game_status(static, results, base, o["tolerance"], len(routes))
    cls = (rjs[0].get("machine") or {}).get("class") if rjs else None
    out = dict(id=tid, mode=mode, status=status, notes=notes, routes=results, static=static, recorded=None)
    if o["cmd"] == "baseline":
        if status in ("PASS", "CHANGED") and not any(x["status"] != "green" for x in results):
            save_baselines(baseline_dir(environ, tid, o["baseline"], mode), results); out["baseline_saved"] = True
        else:
            out["baseline_saved"] = False; notes.append("baseline NOT stored: the run is not green")
    if o["record"]:
        why = may_record(p.provisional, cls, status, results, len(routes))
        if why is None:
            try:
                row = validated_row(mode, rjs, cls, macos or _macos(environ), time.strftime("%Y-%m-%d", time.gmtime(now())))
                update_validated(os.path.join(p.dir, "validated.tsv"), row); out["recorded"] = row
            except RegressError as e:
                why = str(e)
        if why: out["record_refused"] = why
    return out


def _macos(environ):
    try:
        import hw
        h = hw.detect(environ)
        return h["os"] if h else None
    except Exception:
        return None


def render(res):
    o = ["%s %s: %s" % (res["id"], res.get("mode", ""), res["status"])]
    for s in res.get("static", []):
        o.append("  static %-24s %s  %s" % (s[0], s[1].upper(), s[2]))
    for x in res["routes"]:
        m = x["metrics"]
        o.append("  route %-12s %-5s %s%s" % (x["name"], x["status"].upper(), ("p50 %.2f p95 %.2f ms stall %s ms; " % (m["p50"], m["p95"], m.get("stall_ms", "-"))) if "p95" in m else "", "; ".join(x["reasons"])))
    o += ["  note: " + n for n in res["notes"]]
    if res.get("recorded"): o.append("  recorded in validated.tsv: " + res["recorded"].replace("\t", " "))
    if res.get("record_refused"): o.append("  NOT recorded: " + res["record_refused"])
    return "\n".join(o)


# ---------------------------------------------------------------- list
def cmd_list(environ):
    g = runmod._game()
    import cache as cachemod
    cur = cachemod.stack_commits(environ)
    for tid, hits in sorted(g.find_all(environ).items()):
        p = g.Profile(*hits[0])
        sp = os.path.join(p.dir, "smoke.tsv")
        rts = read_smoke(sp) if os.path.isfile(sp) else []
        bdir = os.path.join(environ["NPS5_SCRATCH"], "titles", tid, "regress", "baselines")
        bases = sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []
        print("%s%s: %d smoke route(s) (%s quick), baselines: %s" % (tid, " [provisional]" if p.provisional else "", len(rts), len(select_routes(rts, True)), ", ".join(bases) or "none"))
        for _, c in (p.optfile("validated.tsv") or []):
            f = dict(w.partition("=")[::2] for w in c[1].split()) if len(c) > 1 else {}
            ok = all(cur.get(k, "x").startswith(f[k][:7]) or f[k].startswith(cur.get(k, "x")[:7]) for k in ("kyty", "kk", "ps5recomp") if k in f)
            print("  validated %-5s class=%-12s %s %s" % (c[0], f.get("class", "-"), f.get("date", "-"), "current" if ok else "OLD (stack moved since)"))
    return 0


# ---------------------------------------------------------------- CLI
def parse(argv):
    o = dict(cmd=None, games=None, quick=False, record=False, baseline="latest", out=None, tolerance=10.0, dry=False)
    pos, i = [], 0
    VAL = {"--games": "games", "--baseline": "baseline", "--out": "out", "--tolerance": "tolerance"}
    while i < len(argv):
        a = argv[i]
        if a in VAL:
            if i + 1 >= len(argv): raise RegressError("%s needs a value" % a)
            try: o[VAL[a]] = float(argv[i + 1]) if a == "--tolerance" else argv[i + 1]
            except ValueError: raise RegressError("bad value for %s" % a)
            i += 1
        elif a in ("--quick", "--record"): o[a[2:]] = True
        elif a == "--dry-run": o["dry"] = True
        elif a.startswith("--"): raise RegressError("unknown option %s" % a)
        else: pos.append(a)
        i += 1
    if len(pos) != 1 or pos[0] not in ("check", "baseline", "list"):
        raise RegressError("usage: nps5 regress check|baseline|list [--games ID,...] [--quick] [--record]; see --help")
    o["cmd"] = pos[0]
    return o


def main(argv, environ=None, caller=subprocess.call, statics=None):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0 if argv else 2
    try:
        o = parse(argv)
        environ = runmod.machine_env(dict(os.environ if environ is None else environ))
        if o["cmd"] == "list":
            return cmd_list(environ)
        g = runmod._game()
        ids = o["games"].split(",") if o["games"] else [t for t, hits in sorted(g.find_all(environ).items()) if os.path.isfile(os.path.join(hits[0][0], "smoke.tsv"))]
        skip = [x for x in environ.get("NPS5_REGRESS_SKIP", "").split(",") if x]
        ids = [t for t in ids if t not in skip]
        if not ids:
            raise RegressError("no games to check (smoke.tsv missing everywhere, or all skipped)")
        worst = 0
        results = []
        for tid in ids:
            try:
                res = regress_game(tid, o, environ, caller, statics)
            except RegressError as e:
                res = dict(id=tid, mode="", status="ERROR", notes=[str(e)], routes=[])
            results.append(res); print(render(res))
            worst = worst or (0 if res["status"] in ("PASS", "CHANGED", "DRY") else 1)
        if o["out"] and not o["dry"]:
            os.makedirs(o["out"], exist_ok=True)
            with open(os.path.join(o["out"], "regress.json"), "w") as f:
                json.dump(results, f, indent=1, default=str); f.write("\n")
        return worst
    except RegressError as e:
        print("nps5 regress: %s" % e, file=sys.stderr); return e.code
    except runmod.Refuse as e:
        print("nps5 regress: %s" % e, file=sys.stderr); return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
