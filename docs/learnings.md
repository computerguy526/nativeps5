# Learnings

Dated, evidence-backed observations from bringing two PS5 titles up on Apple silicon: what worked, what did not,
and what the work taught about working this way. Written so that a newcomer does not repeat a dead end and knows
which numbers to distrust.

How to read it:

- Scenes differ by a factor of two. Every number names its scene. Title IDs: PPSA30552 (an Unreal Engine title, on
  the `anyps5` back end) and PPSA21564 (on the `kyty` back end).
- "n=" is the number of runs per arm. One run per arm is a hint, not a result
  ([reference/profiling.md](reference/profiling.md)).
- The mechanism behind each line is in [docs/reference](reference/README.md); the full row-by-row table for
  PPSA30552 is [playbook.md](playbook.md). This page is the digest.
- To add a learning: one row, a date, the scene, the numbers with n, and the verdict. Negative results are as
  welcome as wins. A changed verdict edits the old row and says so; it does not add a contradicting one.

## 1. What worked

| Date | What | Scene | Evidence | Why it worked |
|---|---|---|---|---|
| 2026-11-17 | Honest GPU clock for the game's dynamic resolution (`KYTY_EOP_TIMESTAMPS=cbtime`) | PPSA21564 title / world map / levels | title 42.7 -> 21.1 ms (2.0x), world map 82.2 -> 44.1 ms (1.9x), levels 103.7 -> 83.6, 94.6 -> 76.6, 55.5 -> 50.5 ms; interleaved runs, same binary; pictures checked | The game's clock writes carried CPU record time, so the game thought the GPU was idle and chose 4K. Completion times need no GPU queries |
| 2026-11-16 | Choosing the GPU back end by measurement | PPSA21564, same session | title 28.8 against 8.0 fps, world map 16 against 4.2 fps, 11.5 against 16.5 GiB | A one-day spike of the alternative back end settled a question that weeks of tuning the first one could not |
| 2026-11-17 | Texture resident trim | PPSA21564 level load | 4 of 8 levels died at the memory limit; with the trim all reach their picture, peaks 16.6-16.8 GiB; fps unchanged (n=1) | 2.9-3.2 GiB was whole mip chains of textures with only coarse levels resident; Metal commits the whole chain at the first write |
| 2026-11-16 | Alias-view trim in the memory seam | PPSA21564 level | peak 19.7-20.6 -> 16.2 GiB, frame time unchanged | macOS counts a page in the footprint once per mapping |
| 2026-11-17 | Lock-free guest reads in draw preparation | PPSA21564, 5,000-draw levels | 87-146 -> 9-12 us per draw; wait for workers 50-55 -> 0.3-0.7 ms per frame | Each of 40 reads per draw took a reader-writer lock and two map lookups |
| 2026-11-16 | Larger eager-readback set | PPSA21564 level | fault handler 1,250-1,700 -> 8-77 ms per second | The worker threads sampled more GPU-written pages than the hot set held |
| 2026-11-16 | Per-vertex inputs unpacked in the vertex shader | PPSA21564 level | black triangles gone; colours within 1-3 % of a console capture | Raw-vertex reads of packed fields had been given interpolated bits |
| 2026-11-17 | Present when ready | PPSA21564 | frame-time stdev 6.4-9.7 -> 1.1-3.6 ms at the same fps; 5-15 ms less latency (in-run phases) | The present thread waited for its own 60 Hz tick |
| 2026-11-17 | Persistent pipeline archive | PPSA21564 world map, warm (n=1) | Metal compile time 25.0 -> 0.42 s, compiles 46 -> 1, time in frames over 250 ms 12.0 -> 0.3 s | Compiled pipelines survive the process and a `SIGTERM` |
| 2026-11-16 | Result-preserving trims of the MSL workarounds (default) | PPSA21564 world map | 57.6 -> 53.7 ms GPU per frame (n=1,303 frames); independent in-run check -2.9 ms | Guards only where the operand can need them |
| 2026-11-17 | Exact folds in giant shaders | offline, layer 6 | pixel shaders -16 to -65 % instructions, megakernels -1.2 to -4.0 %; 463.9 million GPU checks, no mismatch | Metal cannot fold a wave reduction of a constant. In-game number pending |
| 2026-11-15 | One clock for the engine's frame pacer | PPSA30552 level | p50 79 -> 37 ms, p95 555 -> 39 ms | Two clocks, one with stall credit, one without |
| 2026-11-15 | Scene resolution, settings overlay and a GPU budget | PPSA30552 | footprint 17-20 -> 8.6-8.8 GB; menu p50 240 -> 151 ms | 17 GB of the footprint was the GPU layer |
| 2026-11-15 | Pipeline journal, offline prewarm, compile workers sized for Metal | PPSA30552 | first frame 10.5-12.0 -> 2.0-2.2 s warm; pipeline stalls 78.5 -> 2.5 s | Compiles moved out of the frame loop |
| 2026-11-15 | Exact write watch | PPSA30552 frame replay | flushes 222 -> 83; replay p50 151.7 -> 47.7 ms | Fewer conservative flushes |
| 2026-11-15 | Menu blur at low quality | PPSA30552 menu | -9.5 ms in 2 of 2 interleaved pairs; changes the look, user's decision | One widget made two 4K passes and 146 MiB of copies per frame |
| 2026-11-15 | Lean HLE shims and clock | microbenchmarks | malloc+free 13.4 -> 6.8 ns; process clock 16.1 -> 2.9 ns | Fewer layers at the call boundary |
| 2026-11-17 | Offline root-causing of a picture bug | PPSA21564 video clips | a two-pitch simulation reproduced the frame dump exactly before any run | The cause was in the video HLE, not in the GPU back end where everyone looked |

## 2. What did not work

Do not retry these without a new angle. "Neutral" means inside run-to-run noise.

| Date | Idea | Scene | Result | Why |
|---|---|---|---|---|
| 2026-11-18 | Compute prologue: record compute dispatches that sit between draws into a separate command buffer so the render pass stays open (AnyPS5 #15548 idea) | PPSA21564 world map, Sky Garden | Counted only about 2 (world map) and 4.5 (Sky Garden) hoistable pass splits per frame; ceiling under 1 % (at most 0.15 of 36 ms and 0.5 of 65 ms); not implemented | Kyty runs mesh draws as native mesh pipelines, so the compute-between-draws pattern AnyPS5 fixed barely exists here; most splits are depth-layout flips caused by the draws themselves |
| 2026-11-18 | Render-pass merging into one Metal command buffer (wolf-kosmickrisp idea) | PPSA21564 | Not built | KosmicKrisp already records one Metal command buffer per Vulkan command buffer; merging further only saves CPU time and these scenes are GPU-bound |
| 2026-11-18 | Bound the render area of attachment-less passes to the scissor (shadPS4 #5452) | PPSA21564 Sky Garden | about 37 such passes per frame, all already 128x128 | Already done: the render area comes from the scissor union (KytyPS5 PR #108); the remaining ~1.7 ms is per-encoder overhead, not area |
| 2026-11-18 | Primitive restart off on list topologies (shadPS4 #5161) | PPSA21564 Sky Garden | 0 list draws with restart enabled out of ~5,000 per frame | Kyty already disables restart for lists; KosmicKrisp unrolls only ~2 draws per frame, for its indexed-indirect robustness workaround |
| 2026-11-18 | Move mid-pass buffer copies to a pre-pass upload command buffer (shadPS4 #5458) | PPSA21564 Sky Garden | about 0.2 such copies per frame | Nothing to move |
| 2026-11-18 | Periodic or count-based submit | PPSA21564 | Not retried | Same mechanism as the earlier PPSA30552 dead end (mid-pass segment commit, [playbook.md](playbook.md) row 8) |
| 2026-11-18 | Draw distance: bias the game's own model LOD selection (LodRatioBias in the renderer's debug settings, exposed as a `far_detail` setting) | PPSA21564 Sky Garden, warm cache | 0 to 4 % (p50 63.0 -> 60.7 ms at the strongest bias), distant props visibly thin out | The cost is near-field: grass and hedges, nearby G-buffer geometry, lighting compute and shadow casters. Caveat: measured at the level start (the scripted pad did not move the player) |
| 2026-11-18 | Lower texture quality far away: extra sampler mip bias (+1) and an anisotropy cap of 4 | PPSA21564 Sky Garden | -1 to -5 % GPU time per frame, inside noise (contended machine, no clean ABBA) | Texture bandwidth is not the limit in this scene |
| 2026-11-18 | Switch off the game's own render stages (SSR, SSAO, screen-space shadows, depth of field, both fogs, voxel particles) through their enable flags | PPSA21564 Sky Garden | All seven together about -2.5 ms (3.6 %), borderline; each alone within +-3 ms noise | The frame is shader- and vertex-bound in the G-buffer, lighting and compute passes, not in post effects |
| 2026-11-18 | Game-side CPU patches (physics substeps, job counts) | PPSA21564 gameplay samples | Guest code is 0.8 % of samples | Not where the time goes |
| 2026-11-18 | Let KosmicKrisp follow the app's Vulkan barriers instead of a full barrier at every encoder end (`KK_VKSYNC`, same idea as Mesa MR !46247) | PPSA21564 Sky Garden, Hoover | Neutral; vertex, fragment and compute overlap rose by only 0.5 % | 92 % of Kyty's 250-600 barriers per frame ask for all stages on both sides; the app side has to narrow first (in progress) |
| 2026-11-18 | Keep a sampled depth target in the feedback layout to avoid pass splits (`KYTY_DEPTH_FEEDBACK_ASPECT`) | PPSA21564 Sky Garden | GPU watchdog hang shortly after the path turned on | Covers about 5 draws per frame; shelved |
| 2026-11-18 | Wider write-fault service window (1 MiB) | PPSA21564 Sky Garden | 12.55 vs 12.70 fps, neutral | Faults per frame halve but each costs more; the window already adapts (256 KiB) |
| 2026-11-18 | Selective buffer-collector settle (`KYTY_GC_SETTLE_SELECTIVE=1`) | PPSA21564 Sky Garden under memory pressure | Fast (p50 73-75 -> 62 ms) but wrong: a flicker on the player's helmet, reproduced with the switch on and gone with it off | A buffer can be retired and reused while a pending readback or GPU use still covers it; off by default until a correct version exists |
| 2026-11-15 | Batching or folding submissions | PPSA30552 menu | submits 53.5 -> 18.3 per frame; frame time +0.05 ms (8 pairs) | The title was 94 % GPU-bound; the wait moved |
| 2026-11-15 | Mid-pass segment commit | PPSA30552 | forced: 28.3-29.8 against 27.0-27.4 ms: a loss | More submissions, same GPU work |
| 2026-11-15 | Cheaper fence polls, lock-free hand-off, verify memo | PPSA30552 menu | CPU -5 ms per frame; fps +0 to +2 % | CPU fell, fps did not: the polls were a busy wait for the GPU |
| 2026-11-15 | Parallel command recording | PPSA30552 | rejected before building | Recording was 3 % of the committing thread |
| 2026-11-15 | Clears as render-pass load operations | PPSA30552 | 87 % of cleared bytes converted, frame time equal | The 4.2 ms "clear" in the profile was the profiler serialising the GPU |
| 2026-11-15 | Render-target pool aliasing | PPSA30552 | +3.8 % fps in one pair: noise | Ceiling was 1.2 ms per frame |
| 2026-11-15 | Uncapped sync; settings-only variable sets | PPSA30552 menu | equal | GPU-bound, not pacer-bound |
| 2026-11-15 | A set of seven parallel-rendering engine variables | PPSA30552 | hangs 3 s after start in 7 runs | Not bisected; blocks nothing else |
| 2026-11-15 | Skipping draws until their pipeline is built | PPSA30552 cold | 51 stalls of 117-184 ms and visible pop-in | Broke the picture |
| 2026-11-15 | Per-pass GPU timestamp profile | PPSA30552 | 28.8 -> 17.2 fps while profiling; ran out of counter heaps; later a kernel panic | Banned ([reference/machine-safety.md](reference/machine-safety.md)) |
| 2026-11-15 | Vector lane liveness in the translator | microbenchmark win (5.05 -> 1.84 host instructions per x86) | deterministic guest crash 50-60 s into a cutscene | A gather's index register was not counted as a read. Fixed later; the lesson is to gate translator changes on a long title run |
| 2026-11-15 | Lean thread-local storage and rwlock | microbenchmark win | use-after-free | Not merged |
| 2026-11-15 | Fastest stack lowering in the translator | | 13.6 million forbidden outcomes in a TSO litmus test | Unsound; the sound one costs +18 % instructions and stays |
| 2026-11-15 | Clearing big depth targets by copy | Metal probe | 5.4 ms against 0.63 ms for a clear pass | Depth targets are compressed; the clear is the cheap path |
| 2026-11-15 | 16-bit float pass-through in the shader recompiler | static count | 0 of 31,356 instructions are 16-bit float arithmetic | Nothing to pass through |
| 2026-11-15 | Offloading guest physics to the Neural Engine | design review | rejected | Millisecond dispatch latency, 16-bit tensors, not bit-exact: gameplay would diverge |
| 2026-11-16 | Render-pass merge, narrower encoder-end barrier, attachment loads | PPSA21564 world map | 0-1 merges per second of 1,120; +0.8 %; noise | Passes are closed by dispatches, barriers and clock writes; loads cost nothing measurable |
| 2026-11-16 | Early fragment tests, fast-math in MSL | PPSA21564 world map | 42.1 and 41.5 against 41.4 ms | No gain on top of workaround removal |
| 2026-11-16 | Spin instead of yield in worker waits | PPSA21564 level | 67.3 against 68.6 ms, +10 % CPU | The level was not CPU-bound in guest code: 7.4 % of process CPU |
| 2026-11-16 | Generated geometry stage for per-vertex inputs | PPSA21564 level | correct picture, killed at the memory limit | Replaced by the vertex-shader unpack |
| 2026-11-17 | Resolution-mode patch as a global default | PPSA21564 | title 43 -> 34 ms, world map 45 -> 97 ms | A patch that helps one scene hurt another |
| 2026-11-17 | Resolution-lock patch for the title | PPSA21564 title | 43.0-43.5 against 42.8 ms | The title's 4K was not dynamic resolution |
| 2026-11-17 | Text-level work on the megakernels | PPSA21564 world map | knocking all four out: +0.1 ms; workaround removal on them only: +0.9 ms | Not on the critical path in that scene |
| 2026-11-17 | Dropping `coherent` everywhere | offline, layer 6 | megakernels -0.3 to -1.8 % instructions; one giant pixel shader +27 % | Already default where it is exact and pays |
| 2026-11-17 | A 1 ms pad relay | PPSA21564 | scripted stick input stopped working | Reverted to 4 ms |
| 2026-11-17 | A partial low-resolution patch | PPSA21564 | flicker | One of two size sites patched: a 720p pass against a 1080p depth buffer |
| 2026-11-17 | A watchdog that exits the process as the stop for a runaway shader | design, others' incident reports | committed GPU work keeps running after exit | Only loops that end by themselves stop it |

Still open, built but without a title number (do not quote them as wins): fewer and cheaper barriers in the
driver, vectorised loads in NIR, mip skip, the texture reuse tracker, the lazy collector settle, MetalFX in place of
the game's upscaler, per-shader NaN-guard removal on the megakernels.

Secondary leads from these results (not yet tried or still open):

- Narrow the destination side of Kyty's barriers (later work waits only for what it consumes), then let vertex work of
  one pass overlap fragment work of the previous one. In Sky Garden vertex 16.4, fragment 15.2 and compute 13.3 ms per
  frame never overlap.
- Shadow casters draw as many indices as the G-buffer (about 12 million each per frame): a separate LOD or culling
  choice for the shadow pass may pay where the general LOD bias did not.
- Compute megakernels: direct MSL generation (one 2.0 ms kernel ran in 0.30 ms, bit-identical, offline) and a lower
  Metal optimisation level for very large shaders.
- Fast shader math per shader instead of globally (an allow list of shaders whose output does not change).
- A correct selective collector settle (retire only buffers whose last GPU use and pending readbacks have completed).
- Near-field density: grass and hedge pipelines are the heaviest geometry; no global density knob was found, so this
  needs per-material work.

## 3. What we learned about measuring

| Date | Observation | Evidence |
|---|---|---|
| 2026-11-15 | Identical runs differ by up to 3 fps | the same settings: 34.0 and 37.1 fps; gameplay p50 20.7 and 23.3 ms |
| 2026-11-15 | Every number needs its scene | a morning of "37 fps" was the menu: the scripted press landed in the logos |
| 2026-11-15 | A cold cache is not a regression | arms measured at 0.88 and 0.05 fps after a driver change without warm-up |
| 2026-11-15 | A profile row is not a saving | the 4.2 ms clear; 36.4 ms "GPU" in a 28 ms frame under timestamp profiling |
| 2026-11-15 | Estimate the ceiling before building | five CPU-side fixes built for a title whose CPU-side ceiling was about 2 fps |
| 2026-11-15 | A fix that shifts odds looks like a fix | a setting made 2 of 2 runs bright; later runs went dark again. Five runs minimum |
| 2026-11-16 | A replay hash can pass while the picture is broken | two replay frames identical, the title's gameplay blown out |
| 2026-11-16 | Name a phase from frame dumps | "world map steady state" was the loading screen before it |
| 2026-11-17 | The machine can invalidate everything | two hours of 0.6-1.5 fps on every stack: battery with Low Power Mode, then another GPU client taking 240-750 ms per second. The driver's own busy figure still said 100 % |
| 2026-11-17 | A three-frame sample is not a measurement | "30.5 ms without the megakernels" became +0.1 ms over 48 presents |
| 2026-11-17 | State the baseline of a delta | a census found -19.5 % for something already in the default build |
| 2026-11-17 | A bimodal result has a cause | 36 fps "once" and 23 fps "since" was the game's own resolution controller, not noise |
| 2026-11-17 | "Black" may be "not yet" | two "black" levels showed their picture 66 s after the first frame, once memory let them live that long |
| 2026-11-17 | Frame logs of a guard exit are truncated | fps of runs that ended with exit code 86 was not measurable |

## 4. What we learned about working with many agents on one machine

| Date | Observation | What happened | Practice now |
|---|---|---|---|
| 2026-11-15 | Killing by name kills other people's work | `pkill -f <binary>` in three runners ended other agents' queued runs; the results read as crashes. A `kill` on a script found by name hit another agent's script with the same file name | Record PIDs at launch, kill only those; give helper scripts agent-specific names |
| 2026-11-15 | Editing a script in place breaks jobs that are reading it | queued runs died with syntax errors; one printed a false "the run modified the game dump" | Write a new file, `mv` it over |
| 2026-11-15 | Cleanups delete things that are in use | a deleted settings overlay made 19 minutes of runs dark | Search the scripts for references before deleting; keep a never-delete list |
| 2026-11-15 | A guard that cannot parse its input fails silent | a `ps` column that macOS lacks: empty process list, four title runs at once | After touching a guard, prove it fires |
| 2026-11-15 | A non-FIFO lock starves someone | runs waited 40 minutes and timed out | FIFO tickets; priorities only for a bounded window |
| 2026-11-16 | Too much at once reboots the machine | six agents resumed together: WindowServer watchdog | One agent start per two minutes; one GPU process |
| 2026-11-16 | Agents waiting on long jobs burn their context | an agent sat in wait loops at 260k tokens during a 5-minute retranslation; others re-sent the same report | Stop an agent that only waits; relaunch a fresh one from a short handoff. One report per agent |
| 2026-11-16 | Performance-class runs starve builds | back-to-back quiet-mode runs paused every build for hours | Performance class only for frame times that will be quoted |
| 2026-11-16 | One shared notes file works, if lines are dated and attributed | every agent appended one-line status with commit, switches and numbers | Keep it; it is the source of this page |
| 2026-11-17 | Gates that grep free text fire on the wrong line | scripts waited for the words "BASELINE OK"; a status line that merely *mentioned* those words released three measurement chains onto a contended GPU. Their results were invalid | A gate matches an anchored pattern with the author and a token nobody writes in prose (`^<date> <time> main: GPU-HEALTHY`), or a flag file |
| 2026-11-17 | A canonical runner must be one file | results stopped being comparable whenever agents ran private copies with drifting defaults; an A/B driver that hard-coded an old default measured the wrong thing | One canonical runner; variants pass overrides to it; a change of canonical is announced with the old way to select the previous one |
| 2026-11-17 | Hand-offs beat long sessions | work stopped for machine reasons resumed from a one-page handoff: state, commits, what is built, what is measured, exact commands, what to watch for | Write the handoff when you stop, not when asked |
| 2026-11-17 | Integration branches need their own A/B | five features were merged and compared against the previous canonical in one interleaved series; that is where the 2.0x was confirmed and where one arm crashed | Merge, then A/B per scene before changing the canonical |
| 2026-11-17 | Research first pays for itself | a review of another project's history gave the honest-clock idea, the barrier audit and the loop cap in one morning | Read prior art for the mechanism, credit it, do not copy unlicensed code |
| 2026-11-17 | Commit identity is a guardrail, not a habit | histories contained commits under a personal identity | Identity comes from git configuration and hooks; never from flags or environment ([AGENTS.md](../AGENTS.md), "Git") |

## 5. The short version

1. Ask what bounds the frame in *this* scene before choosing work: GPU resolution, GPU shaders, CPU in the back
   end, memory, or the machine. The five have different levers.
2. Anything the guest can read steers the game. Fake clocks cost a factor of two.
3. Never GPU timestamp queries on macOS; never trust a process exit to stop GPU work; bound every loop.
4. One run per arm proves nothing; a profile row is not a saving; estimate the ceiling first.
5. Better MSL is worth at most about 16 % of GPU time on the measured scene. The rest is resolution, barriers and
   draw count.
6. Memory limits are findings, not obstacles. The two biggest memory wins came from asking what the 3 GiB *was*.
7. Write the negative result down. Half of this page is the reason the other half exists.
