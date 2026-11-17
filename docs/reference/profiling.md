# Profiling and measuring

Most wrong conclusions in this project came from a measurement that was valid arithmetic on an invalid run. This
page is the checklist and the tools. GPU timestamp queries are not among the tools
([machine-safety.md](machine-safety.md)).

## Before a measured run

| Check | How | Why |
|---|---|---|
| AC power, no Low Power Mode | `pmset -g batt \| head -1` says "AC Power" | On battery in Low Power Mode every run was about 1 fps; all runs of 30 minutes were invalid |
| No other GPU client is busy | per-process GPU time from the IOKit registry (`accumulatedGPUTime` of each GPU user client, read-only): everything that is not the title below about 50 ms per second | WindowServer alone took 240-750 ms of GPU per second for almost two hours (a video or animated wallpaper on the desktop is enough); the title measured 0.9 fps. The driver's own "GPU busy" still printed 100 %, because commit feedback includes time spent waiting behind other clients |
| No second GPU process of ours | `pgrep -fl "native\|agc_frame_replay\|kk_spv\|spvpipe\|harness"` | safety and validity |
| Quiet CPU | load below about 12 for a performance run; no builds | The same build measured 26.7-28.8 fps under agent load and 37 fps quiet |
| No memory pressure | macOS pressure level normal, swap not growing | a swapping title measures the disk |
| Warm caches on both arms | one warm-up run and the offline prewarm per configuration | a cold pipeline cache looks like a regression: 0.05 fps in one "arm" |
| A known reference still matches | one canonical scene against its known frame time | after a reboot, a power change, or anything odd |

## Two classes of run

- **Performance runs** need the quiet machine: other agents' builds pause while one runs. Use them only for frame
  times you will quote.
- **Functional runs** (does it boot, is the picture right, how much memory) run under load. Back-to-back
  performance-class runs once starved every build on the machine for hours.

Frame times from a functional run are not comparable with a performance run. Comparisons under load are done
inside one run (below).

## Comparing two things

- Identical runs differ by up to 3 fps on one title (34.0 against 37.1 for the same settings) and by 2.6 ms p50 in
  gameplay on another. **One run per side proves nothing.** Differences under 1.5 ms are not resolved by a single
  pair.
- Use interleaved whole runs (A B B A, or A B A B), or switch inside one run: an A/B period in the driver, or a
  live-switch file that changes an option every 10-14 s with the first 2 s of each phase dropped. Every arm then
  sees the same scene and the same machine load.
- In-run switching is valid only on a static scene. A moving scene needs whole runs, medians of three, compared by
  game time, not frame counts.
- Say the scene with every number. Menu, cutscene and gameplay differ by a factor of two. For a whole morning every
  "37 fps" figure of one title was the menu, because the scripted button press landed in the logos.
- Check the run reached the scene: look at frame dumps or scene log lines, not only fps. A phase measured as "world
  map steady state" was the loading screen in front of it.
- State the baseline of a delta. A shader census reported -19.5 % for a change that was already in the default
  build, because its baseline was the unoptimised output.
- Apply the same warm-up and prewarm to both arms, with a cache that is warm for exactly that arm.

## What to report

Frame time p50 and p95 and fps, peak footprint, number of runs and their order, the scene, run length, the commit
of every component, the switches set, power source, and what you did not check. The pull-request template has the
list.

## Where the frame goes (CPU side)

The Kyty back end prints exclusive time of its command-processor thread per frame (`KYTY_CP_GAP_STATS=1`) and
per-pass draw counts and sizes (`KYTY_CP_PASS_STATS=1`):

| Term | Meaning | Large means |
|---|---|---|
| commit | the thread building and recording draws | per-draw CPU cost; many draws |
| headwait | waiting for a draw-preparation worker | host CPU in the workers, not the guest and not the GPU |
| gpuwait | waiting on the GPU timeline | GPU-bound, or a collector waiting for the GPU |
| fault | time in the memory-watch fault handler | the memory seam ([architecture.md](architecture.md)) |
| idle / blocked | waiting on neither | the game or pacing |

Two readings from real scenes: 2.3 ms commit and 26 ms gpuwait in a 42.6 ms frame is GPU-bound; 30 ms commit plus
44 ms headwait in a 104 ms frame is CPU-bound in the back end before any GPU wait. Their timers once read zero on
arm64; check that a statistic moves before trusting its zero.

For the guest side: a time-sampled CPU profile of the process, read as a critical-path view, not one thread's
profile. In one level the guest's own code was 7.4 % of process CPU; 70 % was wait, wake and yield churn.

## Where the frame goes (GPU side, safely)

| Tool | Gives | Limit |
|---|---|---|
| Commit feedback (`KK_GPU_BUSY_LOG`, per-commit log) | GPU start and end per command buffer; busy share; milliseconds per commit | includes waiting behind other GPU clients; a commit's time is shared by everything in it |
| Per-process GPU time from the IOKit registry | who is using the GPU, in wall time | per process, not per pass |
| Knock-out (empty shader bodies, skipped draws) inside one run | what a shader or pass is worth on the critical path | the picture is garbage by design; attribution only |
| MSL override by shader hash, switched live | the gain of a shader variant without rebuilding the driver | text-level variants only |
| `tools/agxdis`, `tools/xray` | instructions, registers, spills, load widths, offline | instruction counts are not time |
| Xcode Metal System Trace | everything, sampled by the OS | needs Xcode; short windows |

**Never turn a profile row into a saving.** Per-pass timestamp profiling serialised the GPU (36.4 ms of "GPU time"
in a frame that takes 28 ms unprofiled); a 4.2 ms "clear" row vanished when the clear was removed and the frame
time did not move. Trust the shape, then confirm by removing the thing.

**Estimate the ceiling first.** (share of the work removed) x (its share of the critical path). Under a few
percent, do not build it. With a title 94 % GPU-bound, every CPU-side fix was capped at about 2 fps, and five of
them were built before that arithmetic was done.

## Rules of thumb that held

- At 28 ms per frame, 1 ms of GPU is about 1.3 fps; at 17 ms it is about 3.7 fps.
- Picture appears 40-66 s after the first frame in levels of PPSA21564; a run that ends earlier proves nothing about
  black.
- Compile stalls are in the compile totals, not in frame-time percentiles of a warm window. Report both.
- A run that exits through a guard may truncate its buffered frame log; do not compute fps from it.
- An intermittent bug needs at least five runs per arm.
- Run tags that differ only by letter case overwrite each other on a case-insensitive file system.
