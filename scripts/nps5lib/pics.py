"""nps5 pics: compare the frame dumps of two runs.

  nps5 pics <runA> <runB> [--out sheet.png] [--max N] [--width PX] [--regions N] [--counts C,C,...] [--json]

<run> is a run directory (its frames/ folder or the folder itself holds BMP dumps) or ID/TAG under the runs root.
Dumps are paired by present count (the first number in the file name) and name; each pair gets a row in the PNG sheet:
A | B | difference heat (regions x regions grid of mean luma difference, black to white at 32/255). Printed per pair:
mean abs difference, max, share of samples differing by more than 2, worst region. Printed per run: flicker, the
mean |luma change| and the share of samples changing by more than 16/255 between consecutive dumps of one series
(large on a static scene), and sharpness. Standard library only (BMP in, PNG out; Pillow is not needed).
Unreadable (truncated, corrupt) dumps are skipped and counted; --max also bounds how many dumps per series the flicker
metric decodes (at least 2). --out is written through a temporary file and never through a symlink.
Exit codes: 0 ok, 1 no matching or no readable dumps (or the sheet cannot be written), 2 usage."""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import tempfile
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmp  # noqa: E402
from ab import resolve_run  # noqa: E402


def dump_dir(run: str) -> str:
    f = os.path.join(run, "frames")
    return f if glob.glob(os.path.join(f, "*.bmp")) else run


def list_dumps(directory: str) -> dict[tuple[str, int], str]:
    """{(series, present count): path}. The series is the file name with its first standalone number replaced by '#'."""
    out = {}
    for p in glob.glob(os.path.join(directory, "*.bmp")):
        name = os.path.basename(p)
        m = re.search(r"(?<![A-Za-z0-9])\d+", name) or re.search(r"\d+", name)       # not the 3 of "k3_"
        if m:
            out[(name[:m.start()] + "#" + name[m.end():], int(m.group()))] = p
    return out


def pair_dumps(a: dict, b: dict, counts: Optional[set[int]] = None) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """-> (pairs [(series, count, pathA, pathB)], only in A, only in B)."""
    keys = sorted(set(a) & set(b), key=lambda k: (k[1], k[0]))
    if counts:
        keys = [k for k in keys if k[1] in counts]
    return ([(k[0], k[1], a[k], b[k]) for k in keys], sorted(set(a) - set(b)), sorted(set(b) - set(a)))


def _img(p: str, cache: Optional[dict] = None, keep: bool = False) -> bmp.Image:
    """Decode a dump once: pair images are kept in `cache` for the sheet; flicker only reads from it."""
    if cache is not None and p in cache:
        return cache[p]
    im = bmp.read_bmp(p)
    if keep and cache is not None:
        cache[p] = im
    return im


def write_atomic(path: str, data: bytes) -> None:
    """Write via a temporary file in the same directory + os.replace (a killed run leaves no truncated file); refuses a symlink destination."""
    if os.path.islink(path):
        raise OSError(f"refusing to write through a symlink: {os.path.basename(path)}")
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def flicker_series(dumps: dict[tuple[str, int], str], max_px: int = 100_000, limit: Optional[int] = None, cache: Optional[dict] = None) -> dict[str, dict]:
    """Per series: consecutive-dump flicker (mean, share > 16) averaged, and mean sharpness. `limit`: at most this many
    dumps per series (the first by present count). Unreadable dumps are skipped and counted in `unreadable`."""
    series: dict[str, list[tuple[int, str]]] = {}
    for (s, c), p in dumps.items():
        series.setdefault(s, []).append((c, p))
    out = {}
    for s, lst in series.items():
        lst.sort()
        if limit:
            lst = lst[:limit]
        prev, fl, sh, bad = None, [], [], 0
        for _, p in lst:
            try:
                im = _img(p, cache)
            except (bmp.BmpError, OSError):
                bad += 1
                continue
            sh.append(bmp.sharpness(bmp.luma(im, max_px)))
            if prev is not None:
                f = bmp.flicker(prev, im, max_px)
                if f:
                    fl.append(f)
            prev = im
        if not sh:
            out[s] = dict(dumps=0, unreadable=bad, sharpness=None, flicker_mean=None, flicker_over16=None)
            continue
        out[s] = dict(dumps=len(sh), unreadable=bad, sharpness=sum(sh) / len(sh),
                      flicker_mean=sum(f["mean"] for f in fl) / len(fl) if fl else None,
                      flicker_over16=sum(f["over16"] for f in fl) / len(fl) if fl else None)
    return out


def compare_pair(pa: str, pb: str, regions: int = 8, cache: Optional[dict] = None) -> dict:
    a, b = _img(pa, cache, True), _img(pb, cache, True)
    d = bmp.diff_metrics(a, b)
    if d is None:
        return dict(size_mismatch=True, a=(a.w, a.h), b=(b.w, b.h))
    g = bmp.region_diff(a, b, regions)
    worst = max(((v, y, x) for y, r in enumerate(g) for x, v in enumerate(r)))
    return dict(size_mismatch=False, region_grid=g, worst_region=dict(row=worst[1], col=worst[2], mad=worst[0]), **d)


def make_sheet(pairs: list[tuple], path: str, width: int = 320, regions: int = 8, gap: int = 4, cache: Optional[dict] = None) -> None:
    """PNG with one row per pair: A | B | heat (size-mismatched pairs show A and B, a grey heat). The sheet is as wide as
    the widest row; narrower rows are padded with black. No pairs: ValueError, nothing is written."""
    if not pairs:
        raise ValueError("no pairs for the sheet")
    rows_out: list[bytes] = []
    sheet_w = 0
    for _, _, pa, pb in pairs:
        a, b = _img(pa, cache, True), _img(pb, cache, True)
        wa, ha, ra = bmp.thumb_rgb(a, width)
        wb, hb, rb = bmp.thumb_rgb(b, width)
        h = max(ha, hb)
        g = bmp.region_diff(a, b, regions)
        heat = bmp.heat_rgb(g, wa, h) if g else [b"\x40\x40\x40" * wa] * h
        sep = b"\xff\xff\xff" * gap
        pad = lambda rs, w_, i: rs[i] if i < len(rs) else b"\0" * (3 * w_)  # noqa: E731
        for y in range(h):
            rows_out.append(pad(ra, wa, y) + sep + pad(rb, wb, y) + sep + heat[y])
        rows_out.extend([b"\xff\xff\xff" * (wa + wb + wa + 2 * gap)] * gap)
        sheet_w = max(sheet_w, wa + wb + wa + 2 * gap)
    rows_out = [r + b"\0" * (3 * sheet_w - len(r)) for r in rows_out]
    write_atomic(path, bmp.png_bytes(sheet_w, len(rows_out), rows_out))


def main(argv: list[str], environ: Optional[dict] = None) -> int:
    environ = dict(os.environ if environ is None else environ)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    o: dict = dict(max=6, width=320, regions=8)
    pos, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--out", "--max", "--width", "--regions", "--counts"):
            if i + 1 >= len(argv):
                print(f"nps5 pics: {a} needs a value", file=sys.stderr)
                return 2
            try:
                v = argv[i + 1]
                o[a[2:]] = v if a == "--out" else {int(x) for x in v.split(",")} if a == "--counts" else int(v)
            except ValueError:
                print(f"nps5 pics: bad value for {a}", file=sys.stderr)
                return 2
            i += 1
        elif a == "--json":
            o["json"] = True
        elif a.startswith("--"):
            print(f"nps5 pics: unknown option {a}", file=sys.stderr)
            return 2
        else:
            pos.append(a)
        i += 1
    if len(pos) != 2:
        print("usage: nps5 pics <runA> <runB> [--out sheet.png]", file=sys.stderr)
        return 2
    for k in ("max", "width", "regions"):
        if o[k] < 1:
            print(f"nps5 pics: --{k} must be at least 1", file=sys.stderr)
            return 2
    dirs = []
    for p in pos:
        t, _, tag = p.partition("/")
        d = p if os.path.isdir(p) else resolve_run(t, tag, environ)
        if not os.path.isdir(d):
            print(f"nps5 pics: no such run: {p}", file=sys.stderr)
            return 2
        dirs.append(dump_dir(d))
    da, db = list_dumps(dirs[0]), list_dumps(dirs[1])
    pairs, only_a, only_b = pair_dumps(da, db, o.get("counts"))
    if not pairs:
        print(f"nps5 pics: no dumps at matching present counts ({len(da)} in A, {len(db)} in B)", file=sys.stderr)
        return 1
    cache: dict = {}
    good, results, bad_pairs = [], [], 0
    for s, c, pa, pb in pairs:                    # decode only until --max readable pairs are in hand
        if len(good) >= o["max"]:
            break
        try:
            r = compare_pair(pa, pb, o["regions"], cache)
        except (bmp.BmpError, OSError):
            bad_pairs += 1
            continue
        r.pop("region_grid", None)
        good.append((s, c, pa, pb))
        results.append(dict(series=s, count=c, **r))
    if not good:
        print(f"nps5 pics: no readable dumps ({bad_pairs} matching pair(s) unreadable)", file=sys.stderr)
        return 1
    pairs = good
    lim = max(o["max"], 2)
    res = dict(pairs=results, only_a=len(only_a), only_b=len(only_b), flicker_a=flicker_series(da, limit=lim, cache=cache), flicker_b=flicker_series(db, limit=lim, cache=cache))
    res["unreadable"] = dict(pairs=bad_pairs, flicker=sum(f["unreadable"] for k in ("flicker_a", "flicker_b") for f in res[k].values()))
    if o.get("out"):
        try:
            make_sheet(pairs, o["out"], o["width"], o["regions"], cache=cache)
        except (OSError, bmp.BmpError) as e:
            print(f"nps5 pics: cannot write the sheet: {e}", file=sys.stderr)
            return 1
        res["sheet"] = o["out"]
    if o.get("json"):
        print(json.dumps(res, default=list))
        return 0
    for p in res["pairs"]:
        if p["size_mismatch"]:
            print(f"{p['series']} @{p['count']}: SIZE MISMATCH {p['a']} vs {p['b']}")
        elif p["identical"]:
            print(f"{p['series']} @{p['count']}: identical")
        elif p.get("sample_miss"):
            print(f"{p['series']} @{p['count']}: DIFFERS (only between sample points; sampled mad 0, true size unknown)")
        else:
            w = p["worst_region"]
            print(f"{p['series']} @{p['count']}: mad {p['mad']:.3f} max {p['max']} frac>2 {p['frac']:.4f} worst region r{w['row']}c{w['col']} ({w['mad']:.1f})")
    for name, key in (("A", "flicker_a"), ("B", "flicker_b")):
        for s, f in res[key].items():
            if not f["dumps"]:
                continue
            print(f"flicker {name} {s}: dumps {f['dumps']} sharp {f['sharpness']:.2f}" + (f" mean|d| {f['flicker_mean']:.2f} >16: {100 * f['flicker_over16']:.2f}%" if f["flicker_mean"] is not None else ""))
    u = res["unreadable"]
    if u["pairs"] or u["flicker"]:
        print(f"unreadable dumps skipped: {u['pairs']} pair(s), {u['flicker']} flicker dump(s)")
    if only_a or only_b:
        print(f"unpaired: {len(only_a)} only in A, {len(only_b)} only in B")
    if o.get("out"):
        print(f"sheet: {o['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
