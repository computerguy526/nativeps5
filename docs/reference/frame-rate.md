# Frame rate and pacing

Each entry: symptom, cause, fix, where it was found. Scene names and numbers for PPSA21564 are in
[games/PPSA21564](../../games/PPSA21564/README.md). Read [profiling.md](profiling.md) before measuring anything.

## The game's GPU clock decides its resolution

**Symptom.** The same scene ran at 36 fps in one run and 23 fps in every later one. Nothing in the save data, the
work directory, the caches or the settings differed. Title frame time was bimodal between runs (22-24 or 32-36 fps).

**Cause.** The game has dynamic resolution (DRS). Its controller sums GPU timer intervals that the game records as
end-of-pipe clock writes in the command stream (about 880 per frame), three frames back. It steps down when the
total is over 16.67 ms and steps up after 31 updates with room; the levels are 1080p, 1368p, 1872p and 2160p.
The back end wrote those clock values **when the command processor recorded the packet**, not when the GPU finished
the work. So the game measured how fast our CPU thread records commands (a few milliseconds), concluded the GPU had
room, and climbed to 4K, where the real GPU needed 42 ms per frame. A feedback loop with two stable states: one
early run had a 2.7 s compile stall, the timers read huge, DRS dropped to 1080p, and the then slower recording kept
it there. That was the "36 fps run".

**Fix.** Give the game an honest GPU clock without GPU timestamp queries (which are banned,
[machine-safety.md](machine-safety.md)). Mode `KYTY_EOP_TIMESTAMPS=cbtime`: a clock write's value is the completion
time of the host submission that contains it, read from the host clock when the submission's timeline semaphore
signals (under KosmicKrisp that semaphore is a Metal shared event the queue signals on completion). A submission
holds many clock writes, so its GPU interval, from the previous completion (or the first record time when the GPU
was idle) to its own completion, is spread over its clock writes in proportion to their record times. Properties,
unit-tested: a value is never earlier than its record time, values are monotonic in recording order, the last value
of a group is the completion time, and the deltas of a GPU-bound stream sum to real GPU time. When the scene is
CPU-bound the values fall back to record time plus lag, which is correct.

**Measured** (interleaved runs, same binary, same work directory):

| Scene | record-time clock | completion clock |
|---|---|---|
| title | 42.7 ms, 4K for 75 % of draws | 21.1 ms, 1080p for 73 % of draws (2.0x) |
| world map | 82.2 ms | 44.1 ms (1.9x) |
| level, 1,700 draws | 55.5 ms | 50.5 ms |
| levels, 5,000 draws | 103.7 / 94.6 ms | 83.6 / 76.6 ms |

The game's own DRS goes 4K -> 1368p -> 1080p in the first seconds and stays; no oscillation was seen. No game patch
is needed for it.

**Known limit.** The interval is completion to completion, so GPU idle time inside a group counts while the CPU is
still recording. If a scene sticks at the lowest level although the GPU has room, the next step is per-submission
GPU start and end times from Metal completion handlers.

**General rule.** Any value the guest can read is an input to the game. A clock, a counter or a statistic that is
"harmlessly approximate" can steer the game's own quality controller. Check what the game does with it before
choosing a cheap implementation. The same holds for the texture streamer's LOD statistics
([rendering.md](rendering.md), "LOD statistics").

Found on: PPSA21564.

## Resolution that is not dynamic resolution

**Symptom.** A patch that locks the DRS level at 1080p halved the world map's frame time (80.5 -> 45.3 ms) and did
nothing on the title (43.0-43.5 ms against 42.8).

**Cause.** The title's 4K scene came from a scene-parameter resolution mode, a different mechanism. Forcing the one
1080p parameter set gave the title 43 -> 34 ms and made the world map worse (97 ms, one run).

**Fix.** None of the patches is the general fix; the honest clock above is, and it removed the title's 4K by itself.
A patch that helps one scene must be measured on every scene before it becomes a default.

Found on: PPSA21564.

## Presents stepped in 16.7 ms units

**Symptom.** Frame times jumped between multiples of 16.7 ms although the scene's cost was steady.

**Cause.** The back end's present thread showed a finished frame only at its own 60 Hz virtual vertical-blank tick.
A finished frame waited 7-10 ms for the tick (p95 17-21 ms). It was not the game, the swapchain or the panel.

**Fix.** Present when the frame is ready (`KYTY_PRESENT_ABS_VBLANK=1`, now the default): frame-time standard
deviation 6.4-9.7 ms -> 1.1-3.6 ms at the same fps, and 5-15 ms less latency.

**Related, measured.** A steady cap with the stock limiter gives a standard deviation of 0.7-1.5 ms but adds two
full frame intervals of latency (world map 117 ms uncapped, 236 ms at a 17 fps cap), because the game keeps
producing and two finished frames wait in the flip queue. A cap that holds the game at its frame start instead
keeps the cadence and cuts latency below the uncapped value (20 fps cap: 64 ms). All of these are flip times in the
present thread, not time on glass.

Found on: PPSA21564.

## The game slept on the wrong clock

**Symptom.** A level ran at 5 fps with flips of over 250 ms, and the picture went dark.

**Cause.** The engine's frame pacer compared a runtime clock that subtracted GPU-stall credit with a driver clock
that did not.

**Fix.** One clock for both: level p50 79 -> 37 ms, p95 555 -> 39 ms, no flip over 250 ms.

Found on: PPSA30552.

## CPU-bound levels

**Symptom.** Levels with about 5,000 draws per frame ran at 10 fps at the lowest resolution. Lowering resolution
further changed nothing.

**Cause.** The frame was the back end's command-processor thread, not the GPU. Per frame at 104 ms: 30 ms building
and recording draws, 44 ms waiting for draw-preparation workers, 18 ms waiting for the GPU. A worker needed 87-146 us
per draw, of which 73-132 us were recorded guest reads (40 reads, 640 bytes per draw): each read took a
reader-writer lock and two ordered-map lookups, and the upstream direct-read fast path was dead code with hosted
guest memory. The GPU waits were the buffer garbage collector, which ran after every completed submission (8 per
frame) whenever device memory was above its trigger, and first settled pending readbacks.

**Fix.** A lock-free read path for hosted memory: 9-12 us per draw, the wait for workers 50-55 -> 0.3-0.7 ms per
frame. In the interleaved comparison with the completion clock the two levels went 103.7 -> 83.6 and 94.6 -> 76.6 ms.
A lazy settle for the collector is built and unmeasured.

**What is left.** Commit cost per draw is 1.2-1.4x upstream's on a PC, set by the share of draws that start a new
run (a start costs 11-14.5 us, a continuation 1.7-1.9 us). One 480x270 effect pass costs 211-218 us per draw (19
draws, 4.1 ms) and is not explained yet.

Found on: PPSA21564.

## Things that did not raise the frame rate

Measured, so they need a new angle before anyone repeats them:

| Idea | Result | Why |
|---|---|---|
| Uncapped sync | 35.8 fps, no faster than capped | the title was GPU-bound, not pacer-bound |
| Batching or folding submissions | submits 53.5 -> 18.3 per frame, frame time +0.05 ms | the wait moves, blocked time does not fall |
| Parallel command-buffer recording | rejected before building | recording was 3 % of the committing thread |
| A cheaper fence poll | process CPU 49.7 -> 45.0 ms per frame, fps +2 % | the old polls were a busy wait for the GPU |
| Spin instead of yield in worker waits | 67.3 against 68.6 ms, +10 % CPU | 70 % of CPU in that level was wait and wake churn, not work |
| A shorter pad relay interval (1 ms) | broke a scripted stick input entirely | reverted to 4 ms; input changes need a functional check, not only a latency number |

Thread quality-of-service classes for the hot threads gave +4-5 % fps and a much better p99 (43 -> 35 ms) in two
in-run comparisons on PPSA30552; it is opt-in until it is repeated on a quiet machine.
