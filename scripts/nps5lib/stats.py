"""Shared statistics for the nps5 analysis tools: percentiles, frame-time summaries, bootstrap comparison.
Standard library only. One percentile convention everywhere (nearest rank, upper: sorted[int(p*n)]), the one the
older per-tool scripts used, so numbers stay comparable with earlier notes."""
from __future__ import annotations

import random
from typing import Sequence


def pct(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile, p in [0, 1]. Raises ValueError on an empty sequence."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))]


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of an empty sequence")
    return sum(values) / len(values)


def summarize(frame_ms: Sequence[float]) -> dict:
    """Frame times in ms -> n, p50, p95, mean (ms) and fps (frames per second of summed frame time)."""
    if not frame_ms:
        return dict(n=0, p50=None, p95=None, mean=None, fps=None)
    tot = sum(frame_ms)
    return dict(n=len(frame_ms), p50=pct(frame_ms, .5), p95=pct(frame_ms, .95), mean=tot / len(frame_ms),
                fps=1000.0 * len(frame_ms) / tot if tot > 0 else None)


def spread(values: Sequence[float]) -> float:
    """Between-run spread: max - min (0 for a single value)."""
    return (max(values) - min(values)) if values else 0.0


def bootstrap_ci(values: Sequence[float], iters: int = 2000, alpha: float = .05, seed: int = 1) -> tuple[float, float]:
    """Bootstrap CI of the mean of per-run values (resampling runs)."""
    rng = random.Random(seed)
    n = len(values)
    ms = sorted(mean([values[rng.randrange(n)] for _ in range(n)]) for _ in range(iters))
    return ms[int(alpha / 2 * iters)], ms[min(iters - 1, int((1 - alpha / 2) * iters))]


def bootstrap_diff(a: Sequence[float], b: Sequence[float], iters: int = 4000, alpha: float = .05, seed: int = 1) -> tuple[float, float, float]:
    """Difference of arm means (b - a) with a bootstrap CI that resamples runs inside each arm."""
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    ds = sorted(mean([b[rng.randrange(nb)] for _ in range(nb)]) - mean([a[rng.randrange(na)] for _ in range(na)]) for _ in range(iters))
    return mean(b) - mean(a), ds[int(alpha / 2 * iters)], ds[min(iters - 1, int((1 - alpha / 2) * iters))]


def compare(a: Sequence[float], b: Sequence[float], lower_is_better: bool = True, min_runs: int = 2,
            min_effect: float = 0.0, iters: int = 4000) -> dict:
    """Noise-aware verdict on per-run values of two arms (b against a).
    verdict: "better" / "worse" (for b), "no-difference", or "insufficient" (fewer than min_runs runs in an arm: no
    winner is ever called from one run per arm). A difference counts only if the bootstrap CI excludes 0, the
    magnitude exceeds min_effect and, with fewer than 5 runs per arm (where a bootstrap is too optimistic), it also
    exceeds the larger between-run spread of the two arms."""
    out = dict(runs_a=len(a), runs_b=len(b), mean_a=mean(a) if a else None, mean_b=mean(b) if b else None,
               spread_a=spread(a), spread_b=spread(b), diff=None, ci_lo=None, ci_hi=None, verdict="insufficient",
               reason=None)
    if len(a) < min_runs or len(b) < min_runs:
        out["reason"] = f"need at least {min_runs} runs per arm (have {len(a)} and {len(b)}): no winner from a single run"
        return out
    d, lo, hi = bootstrap_diff(a, b, iters=iters)
    out.update(diff=d, ci_lo=lo, ci_hi=hi)
    noise = max(out["spread_a"], out["spread_b"]) if min(len(a), len(b)) < 5 else 0.0
    if lo <= 0 <= hi or abs(d) <= min_effect or abs(d) <= noise:
        out["verdict"] = "no-difference"
        out["reason"] = "CI includes 0" if lo <= 0 <= hi else ("below the minimum effect" if abs(d) <= min_effect else "within the between-run spread")
    else:
        good = (d < 0) == lower_is_better
        out["verdict"] = "better" if good else "worse"
    return out
