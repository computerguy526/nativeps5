"""nps5 report: a PR evidence bundle (markdown) from runs.

  nps5 report <run>... [--base RUN] [--out DIR] [--window steady|last:S|t:A-B] [--pics RUN RUN] [--xray A.csv B.csv]

<run> is a run directory or ID/TAG under the runs root. Runs are grouped by their arm label (run.json arm.label;
none = "baseline"). The report has: the runs and their components, environment differences against the baseline run
(--base, else the first run), the fps table with a bootstrap CI per arm and a verdict when there are two arms, a
picture sheet and difference metrics (with --out: DIR/sheet.png; arms' first runs, or --pics), the guard / watchdog /
loop-cap / memory-limit lines found in the run logs, and shader deltas from two `work/xray/batch.sh` CSVs.
Everything is scrubbed: absolute paths become <path>; only numbers, hashes and log lines are written, never game
files. Output goes to DIR/report.md (and stdout without --out). Check it with scripts/check.sh before pasting.
Exit codes: 0 ok, 1 no usable runs, 2 usage."""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ab  # noqa: E402
import bmp  # noqa: E402
import pics  # noqa: E402
import stats as st  # noqa: E402

# A path starts at a known root ("/Users", "C:\\Users", "~/"...). It may contain spaces, so the match runs to the closing quote
# (when quoted) or else to the end of the line / the next table pipe: over-scrubbing a tail is safe, leaking a directory name is not.
_ROOTS = r"(?:~(?=[/\\]|$)|[A-Za-z]:[\\/]Users(?=[/\\])|/(?:Users|home|private|var|tmp|Volumes|opt|Applications|Library|System|nix|mnt|root)(?=[/\\\s'\"`),;|]|$))"
_QPATH = re.compile(r"(?P<q>['\"`])" + _ROOTS + r"(?:[/\\][^\n]*?)?(?P=q)")
_PATH = re.compile(r"(?<![\w.<])" + _ROOTS + r"(?:[/\\](?:[^\n|]*[^\s|])?)?")
GUARD = re.compile(r"guard|watchdog|loop[-_ ]?cap|capped|self memory limit|start hold|\[run_sandboxed\]|refus|memory pressure|NPS5_RUN_MAX|aborting|timed out|timeout", re.I)


def scrub(s: str, cut: bool = True) -> str:
    """Absolute and home paths -> <path>. cut: also shorten a free-text log line to 220 characters (never for lines the
    report builds itself: a cut table or env-diff row is a broken row)."""
    s = _QPATH.sub(lambda m: m.group("q") + "<path>" + m.group("q"), s)
    s = _PATH.sub("<path>", s)
    return s if not cut or len(s) <= 220 else s[:217] + "..."


def load_run(spec: str, environ: dict) -> dict:
    t, _, tag = spec.partition("/")
    d = spec if os.path.isdir(spec) else ab.resolve_run(t, tag, environ)
    if not os.path.isdir(d):
        raise FileNotFoundError(f"no such run: {spec}")
    rj = {}
    p = os.path.join(d, "run.json")
    if os.path.isfile(p):
        with open(p) as f:
            rj = json.load(f)
    if not isinstance(rj, dict):                      # valid JSON that is not an object: no metadata, the run still counts
        rj = {}
    for k in ("arm", "machine", "build", "env", "components", "logs", "timings"):
        if not isinstance(rj.get(k), dict):
            rj[k] = {}
    return dict(dir=d, tag=str(rj.get("tag") or os.path.basename(os.path.normpath(d))), rj=rj, arm=str(rj["arm"].get("label") or "baseline"))


def guard_lines(run: dict, limit: int = 12) -> list[str]:
    """Guard / watchdog lines of the run's logs. Only files whose real path is inside the run directory are read (run.json
    is data, not trusted: a crafted `logs` entry must not pull another file into the report); deduped by real path."""
    seen, out = set(), []
    root = os.path.realpath(run["dir"])
    logs = run["rj"].get("logs") or {}
    paths = [os.path.join(run["dir"], n) for n in ("run.out", "run.err")] + [p for p in logs.values() if isinstance(p, str)]
    for p in paths:
        rp = os.path.realpath(os.path.join(run["dir"], p))      # a relative entry means relative to the run directory
        if rp in seen or not rp.startswith(root + os.sep) or not os.path.isfile(rp):
            continue
        seen.add(rp)
        with open(rp, errors="replace") as f:
            for line in f:
                if GUARD.search(line):
                    s = scrub(line.strip())
                    if s not in out:
                        out.append(s)
                    if len(out) >= limit:
                        return out
    return out


def env_diff(base: dict, other: dict) -> list[tuple[str, Optional[str], Optional[str]]]:
    return [(k, base.get(k), other.get(k)) for k in sorted(set(base) | set(other)) if base.get(k) != other.get(k)]


def xray_delta(a_csv: str, b_csv: str) -> list[str]:
    """Rows keyed by input/id; numeric columns that differ -> "col a -> b"."""
    def rows(p):
        with open(p, newline="") as f:
            return {r.get("input") or r.get("id"): r for r in csv.DictReader(f)}
    ra, rb = rows(a_csv), rows(b_csv)
    out = []
    for k in sorted(set(ra) & set(rb), key=str):
        ch = []
        for c, v in ra[k].items():
            w = rb[k].get(c)
            if c in ("input", "id", "error") or w is None or v == w:
                continue
            try:
                ch.append(f"{c} {float(v):g} -> {float(w):g}")
            except ValueError:
                pass
        if ch:
            out.append(f"`{k}`: " + ", ".join(ch))
    miss = len(set(ra) ^ set(rb))
    if miss:
        out.append(f"{miss} shader(s) present in only one CSV")
    return out


def build(specs: list[str], environ: dict, window: str = "steady", base: Optional[str] = None, out_dir: Optional[str] = None,
          pic_pair: Optional[tuple[str, str]] = None, xray: Optional[tuple[str, str]] = None) -> str:
    runs = [load_run(s, environ) for s in specs]
    basr = load_run(base, environ) if base else runs[0]
    L = ["# Evidence", ""]
    # runs
    L += ["## Runs", "", "| tag | arm | class | gpu | scene | build | rc | verdict |", "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        j = r["rj"]
        L.append("| " + " | ".join(str(x) if x not in (None, "") else "-" for x in (r["tag"], r["arm"], j.get("class"), j.get("gpu"), j.get("scene"),
                                                                                  (j.get("build") or {}).get("version"), j.get("rc"), j.get("verdict"))) + " |")
    m = (runs[0]["rj"].get("machine") or {})
    L += ["", f"Machine: {m.get('chip') or '?'}, {m.get('mem_gb') or '?'} GB ({m.get('model') or '?'})", ""]
    comps = {}
    for r in runs:
        comps.setdefault(json.dumps(r["rj"].get("components") or {}, sort_keys=True), []).append(r["tag"])
    L += ["## Components", ""]
    for c, tags in comps.items():
        L.append("- " + ", ".join(f"{k} `{v}`" for k, v in json.loads(c).items() if v) + f"  ({', '.join(tags)})")
    # env
    L += ["", f"## Environment differences vs `{basr['tag']}`", ""]
    any_d = False
    for r in runs:
        if r is basr:
            continue
        d = env_diff(basr["rj"].get("env") or {}, r["rj"].get("env") or {})
        if d:
            any_d = True
            L.append(f"- `{r['tag']}`: " + "; ".join(f"`{k}` {scrub(str(a), False) if a is not None else '(unset)'} -> {scrub(str(b), False) if b is not None else '(unset)'}" for k, a, b in d))
    if not any_d:
        L.append("None: every run used the same environment.")
    # fps
    arms: dict[str, list[dict]] = {}
    skipped = []
    for r in runs:
        try:
            m = ab.run_metrics(r["dir"], window)            # computed first: a failing run must not leave an empty arm behind
        except (ValueError, OSError) as e:
            skipped.append(f"`{r['tag']}`: {e}")
            continue
        arms.setdefault(r["arm"], []).append(m)
    if not arms:
        raise ValueError("no usable runs: " + scrub("; ".join(skipped) or "none given"))
    L += ["", f"## Frame times (window: {window})", ""]
    if arms:
        L += ["| arm | runs | p50 per run (ms) | p50 mean (95% CI) | p95 mean (ms) | fps mean |", "|---|---|---|---|---|---|"]
        for lab, rs in arms.items():
            v = [x["p50"] for x in rs]
            ci = "%.2f (%.2f .. %.2f)" % ((st.mean(v),) + st.bootstrap_ci(v)) if len(v) > 1 else "%.2f (one run: no CI)" % v[0]
            L.append(f"| {lab} | {len(rs)} | {', '.join('%.2f' % x for x in v)} | {ci} | {st.mean([x['p95'] for x in rs]):.2f} | {st.mean([x['fps'] for x in rs]):.1f} |")
        if any(x["short"] for rs in arms.values() for x in rs):
            L += ["", "Some runs have a steady window shorter than 90% of the request; see the run notes."]
        if len(arms) == 2:
            (la, ra), (lb, rb) = arms.items()
            for key, lower in (("p50", True), ("fps", False)):
                c = st.compare([x[key] for x in ra], [x[key] for x in rb], lower_is_better=lower)
                L.append("")
                L.append(f"{key} `{lb}` vs `{la}`: " + (f"{c['diff']:+.2f} (95% CI {c['ci_lo']:+.2f} .. {c['ci_hi']:+.2f}), **{c['verdict']}**" if c["diff"] is not None else f"**{c['verdict']}** ({c['reason']})"))
    for s in skipped:
        L.append(f"- no frame data: {scrub(s)}")
    # pictures
    L += ["", "## Pictures", ""]
    pp = pic_pair
    if not pp and len(arms) >= 2:
        labs = list(arms)
        first = {r["arm"]: r for r in reversed(runs)}
        pp = (first[labs[0]]["dir"], first[labs[1]]["dir"])
    shown = False
    if pp:
        da, db = pics.list_dumps(pics.dump_dir(pp[0])), pics.list_dumps(pics.dump_dir(pp[1]))
        pairs, oa, ob = pics.pair_dumps(da, db)
        good, bad, cache = [], 0, {}
        for s, c, pa, pb in pairs:
            if len(good) >= 4:
                break
            try:
                r = pics.compare_pair(pa, pb, cache=cache)
            except (bmp.BmpError, OSError):                  # a truncated dump is skipped and counted, not fatal
                bad += 1
                continue
            good.append((s, c, pa, pb))
            L.append(f"- {s.replace('#', 'N')} at present {c}: " + ("size mismatch" if r["size_mismatch"] else "identical" if r["identical"] else "differs, but only between sample points (sampled mean abs diff 0)" if r.get("sample_miss")
                                                                  else f"mean abs diff {r['mad']:.3f}, max {r['max']}, {100 * r['frac']:.2f}% of samples differ by more than 2"))
        if bad:
            L.append(f"- {bad} unreadable dump pair(s) skipped")
        if good:
            if out_dir:
                pics.make_sheet(good, os.path.join(out_dir, "sheet.png"), cache=cache)
                L += ["", "![A | B | difference](sheet.png)  (left: first arm, middle: second arm, right: difference heat)"]
            shown = True
    if not shown:
        L.append("No dumps at matching present counts (or fewer than two arms)." )
    # guard lines
    L += ["", "## Guard, watchdog and cap lines", ""]
    for r in runs:
        gl = guard_lines(r)
        L.append(f"- `{r['tag']}`: " + ("none" if not gl else ""))
        L += [f"  - `{x}`" for x in gl]
    if xray:
        L += ["", "## Shader deltas (xray)", ""]
        d = xray_delta(*xray)
        L += [f"- {x}" for x in d] or ["No differences in the compared columns."]
    return scrub_all("\n".join(L) + "\n")


def scrub_all(text: str) -> str:
    return "\n".join(scrub(l, False) if "(sheet.png)" not in l else l for l in text.split("\n"))     # no cutting: these are report-built lines


def main(argv: list[str], environ: Optional[dict] = None) -> int:
    environ = dict(os.environ if environ is None else environ)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    specs, o, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        n = {"--base": 1, "--out": 1, "--window": 1, "--pics": 2, "--xray": 2}.get(a)
        if n:
            if i + n >= len(argv):
                print(f"nps5 report: {a} needs {n} value(s)", file=sys.stderr)
                return 2
            o[a[2:]] = argv[i + 1] if n == 1 else tuple(argv[i + 1:i + 1 + n])
            i += n
        elif a.startswith("--"):
            print(f"nps5 report: unknown option {a}", file=sys.stderr)
            return 2
        else:
            specs.append(a)
        i += 1
    if not specs:
        print("usage: nps5 report <run>... [--out DIR]", file=sys.stderr)
        return 2
    try:
        ab.check_window(o.get("window", "steady"))
    except ValueError as e:
        print(f"nps5 report: {e}", file=sys.stderr)
        return 2
    try:
        if o.get("out"):
            os.makedirs(o["out"], exist_ok=True)
        text = build(specs, environ, o.get("window", "steady"), o.get("base"), o.get("out"), o.get("pics"), o.get("xray"))
        if o.get("out"):
            pics.write_atomic(os.path.join(o["out"], "report.md"), text.encode())
    except (OSError, ValueError) as e:                   # includes FileNotFoundError, JSONDecodeError, a refused symlink
        print(f"nps5 report: {e}", file=sys.stderr)
        return 1
    if o.get("out"):
        print(f"nps5 report: wrote {os.path.join(o['out'], 'report.md')}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
