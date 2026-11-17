# Evidence for a performance or picture change

A PR that claims a speed-up or "no picture change" carries numbers a reviewer can trust. The tools below make the
trustworthy version the cheap one. All of them are `nps5 <command>` (standard library Python; no game data is read
or written outside the run directories you point them at).

## The loop

1. Measure with `nps5 run` (one run, `run.json` written next to the logs). Perf runs need a quiet machine
   (AGENTS.md); anything else is a functional run.
2. Compare with `nps5 ab`: two arms, interleaved (`ABBA`), several runs each.
3. Check the picture with `nps5 pics`.
4. Write the PR evidence with `nps5 report` and paste it under "Evidence" in the PR template.

## `nps5 analyze`: one run, one question

| command | answers |
|---|---|
| `analyze frames <run dir or log>` | p50 / p95 / mean / fps over the steady window |
| `analyze passes <out log>` | share of draws per render-target size (all, last 25 %) |
| `analyze blocks <out log>`, `analyze gaps <out log>` | pass sizes per block; CPU busy / idle windows |
| `analyze commits <commits log>` | GPU busy, idle, passes and classes per frame (union of intervals) |
| `analyze hot <commits log> <shader dir>` | shaders ranked by GPU ms of the commits they appear in (upper bound) |
| `analyze gpu <ioreg dump>` | per-process GPU time |

### The steady window

A run starts with a loading phase (frames of hundreds of ms). A window that reaches into it gives wrong p50,
mean, fps, and p95, and a run killed by a timeout ends in a stall that eats the end of the window. `steady` (the
default) therefore:

- finds where the run settles: the loading phase ends after the last stretch of 30 frames whose median is more than
  3x the settled median (an isolated stall does not count as loading);
- drops trailing stall frames (a hang before the kill) and skips the last 2 s;
- never claims coverage it does not have: a run with 15 s of steady frames asked for 30 s says `SHORT`.

Use `--window last:S` or `--window t:A-B` only when you mean exactly that slice. Percentiles are nearest-rank
(upper): `sorted[int(p * n)]`, the same everywhere.

## `nps5 ab`: is B faster than A?

    nps5 ab <id> --arm A:K=V,... --arm B:K=V,... --reps 3 --order ABBA [--scene NAME]
    nps5 ab <id> --from-runs --arm A:tag1,tag2,tag3 --arm B:tag4,tag5,tag6

Per run: p50 and p95 over the steady window. Per arm: the mean of the per-run p50 and the between-run spread (max -
min). B against A: the difference with a bootstrap CI that resamples runs, and a verdict:

- **better** / **worse**: the CI excludes 0 and (with fewer than 5 runs per arm) the difference exceeds the larger
  between-run spread;
- **no-difference**: anything else; identical configurations land here (they differ by up to 3 fps run to run);
- **insufficient**: fewer than 2 usable runs in an arm. One run per arm never gets a winner.

Runs that cannot be analysed are listed as skipped and not counted. `--dry-run` prints the `nps5 run` commands
without starting anything.

## `nps5 pics <runA> <runB>`

Pairs the BMP dumps of two runs by present count and series, writes a PNG sheet (A | B | difference heat), and prints
the mean absolute difference, the maximum, the share of samples differing by more than 2, and the worst of 8x8
regions. Per run it prints flicker: the mean change and the share of samples changing by more than 16/255 between
consecutive dumps of a series (large on a static scene), and sharpness. Large images are sampled on a grid
(250 000 pixels at most); identical files compare exactly equal. Say "no picture change" only for `identical` pairs,
and say how many pairs and at which present counts.

## `nps5 report <run>... [--out DIR]`

Writes `DIR/report.md` (and `sheet.png`): runs and machine, components (commits), environment differences against the
baseline run, the fps table with a CI per arm and the verdict, the picture metrics and sheet, the guard / watchdog /
loop-cap / memory-limit lines found in the run logs, and (with `--xray A.csv B.csv`, two `tools/xray/batch.sh`
outputs) per-shader deltas. Absolute paths are replaced by `<path>`. Run `scripts/check.sh` on what you paste.
Attach `sheet.png` to the PR; never commit it.
