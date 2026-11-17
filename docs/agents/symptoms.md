# Symptom index

Search this page for the text in your log or for what you see. Each row leads to the cause and to the page that
explains it. Written by hand; add a row when you add a reference entry.

## Exit codes and `[RED]` lines

| What you see | Meaning | Go to |
|---|---|---|
| exit code 86, `[RED]` self memory limit | footprint passed `NPS5_SELF_MEM_LIMIT_MB`. A memory finding, not a crash; never raise the limit | [memory](../reference/memory.md) |
| exit code 70, `GPU WATCHDOG` | a committed command buffer did not complete in time, **or** a false alarm from an old guard build (unsigned time subtraction) | [machine-safety](../reference/machine-safety.md), "The guards" |
| exit code 72 | 32 out-of-memory command-buffer failures in 60 s; the first one dumped driver memory by owner | [machine-safety](../reference/machine-safety.md) |
| exit code 73 | Metal allocated size above the residency guard's limit | [machine-safety](../reference/machine-safety.md) |
| exit code 74 | Metal queue error "access revoked" | [machine-safety](../reference/machine-safety.md) |
| exit code 75 from `run-title.sh` | another title run holds the lock, or another GPU process is running | wait; do not remove the lock |
| exit code 78 from `run-title.sh` | the run was refused: too long without `NPS5_RUN_REASON`, a raised memory limit, or a GPU profiling switch | [AGENTS.md](../../AGENTS.md), "Running safely" |
| exit code 124 | the run reached its time cap; normal for a timed run | |
| exit code 137, or "terminated by signal 15" two seconds in | something killed the run: the machine guard (read its log first) or another agent's kill by name | [learnings](../learnings.md), section 4 |
| `[RED] loop cap hit: submission .., kernel .., loop site ..` | a compute kernel spent its loop budget and ended itself. A real finding; report the kernel hash | [machine-safety](../reference/machine-safety.md) |
| `[RED] gpu timestamp profiling OFF`, `too many heaps already allocated` | Metal counter heaps are in use or exhausted. Stop the run and report; this family panicked the kernel | [machine-safety](../reference/machine-safety.md), "Incidents" |
| `translator_spawn_failed` | a function missed by static analysis needed translating and the translator's build folder was gone | [architecture](../reference/architecture.md), section 1 |
| `not readable` crash in a frame replay or title | a read-trap race (fixed); on old builds intermittent under load | [playbook](../playbook.md), row 6 |
| the title aborts at start with pipeline statistics enabled | `APS5_PIPELINE_STATS` needs an extension KosmicKrisp lacks | do not use it |

## Frame rate

| What you see | Cause | Go to |
|---|---|---|
| about 1 fps on every stack, GPU "100 % busy", nothing wrong in the log | battery with Low Power Mode, or another process (often WindowServer) holding the GPU | [profiling](../reference/profiling.md), checklist |
| the same scene at 36 fps once and 23 fps since; bimodal title frame time | the game's dynamic resolution fed by record-time clocks | [frame-rate](../reference/frame-rate.md) |
| main passes at 3840x2160 in the pass statistics although the window is 1080p | same, or a scene-parameter resolution mode | [frame-rate](../reference/frame-rate.md) |
| frame times in steps of 16.7 ms | the present thread waits for its own 60 Hz tick | [frame-rate](../reference/frame-rate.md) |
| 10 fps with about 5,000 draws per frame; lower resolution changes nothing | CPU-bound in the back end: draw preparation and commit | [frame-rate](../reference/frame-rate.md), "CPU-bound levels" |
| large `headwait` in the command-processor statistics | host CPU in draw-preparation workers (guest reads through a lock) | [frame-rate](../reference/frame-rate.md) |
| large `gpuwait` with the GPU not busy | a collector settling readbacks after every submission | [frame-rate](../reference/frame-rate.md) |
| large `fault`, or hundreds of ms per second in the fault handler | loads from GPU-owned pages; neighbours of watched pages; TSO load forms | [architecture](../reference/architecture.md), section 2 |
| 0.05-1 fps right after changing the driver or a back end | cold pipeline cache: the cache key changed | [profiling](../reference/profiling.md); warm up and prewarm |
| stalls of seconds at a new level, then fine | first-visit pipeline compiles | pipeline archive and prewarm; [games/PPSA21564](../../games/PPSA21564/README.md) |
| a profile says a pass costs N ms; removing it changes nothing | the profiler serialised the GPU | [profiling](../reference/profiling.md) |
| 400-700 ms frames after turning on a codegen bit | not isolated (bit 64 of `KK_MSL_OPT`, one run) | [shaders](../reference/shaders.md); check offline first |
| CPU time fell, fps did not | the removed work was a busy wait for the GPU | [learnings](../learnings.md), section 2 |
| a level is slow and one commit costs 10 ms | a serial chain of compute dispatches behind barriers | [rendering](../reference/rendering.md), "Barriers" |

## Picture

| What you see | Cause | Go to |
|---|---|---|
| black triangles over lit geometry | per-vertex fragment inputs read as interpolated packed bits | [rendering](../reference/rendering.md) |
| black for 40-66 s after the first frame of a level, then a picture | loading; or the run ended first (often with exit code 86) | [games/PPSA21564](../../games/PPSA21564/README.md) |
| a small video panel smeared, green stepped bottom | video frame row pitch | [rendering](../reference/rendering.md) |
| dark, dim or washed-out gameplay, brightness pops on camera movement (PPSA30552) | the tonemap table draw only works first in its command buffer | [rendering](../reference/rendering.md) |
| a dark scene with default heavy lighting features (PPSA30552) | the settings overlay was not mounted | [playbook](../playbook.md), section 4 |
| wrong shadow cascades | depth bounds reported but ignored on M1-M4 | [rendering](../reference/rendering.md) |
| flicker after a low-resolution patch | one of two size sites patched | [games/PPSA21564](../../games/PPSA21564/README.md), "Patches" |
| pop-in on a cold cache | draws skipped until their pipeline is built | [learnings](../learnings.md), section 2 |
| blurry overlay text | no HiDPI drawable | [rendering](../reference/rendering.md) |
| textures stuck at low mips, or memory thrash at mip 0 | LOD statistics mode | [rendering](../reference/rendering.md) |
| GPU writes through a no-copy buffer never arrive | one page of the range was read-only when the buffer was made | [architecture](../reference/architecture.md), section 2 |
| the frame-replay hash is identical but the title looks wrong | the replay does not cover when storage results reach guest memory | [rendering](../reference/rendering.md), the warning |

## Memory and disk

| What you see | Cause | Go to |
|---|---|---|
| footprint jumps 4 GiB in two seconds at level start | whole mip chains for partly resident textures | [memory](../reference/memory.md) |
| footprint far above guest plus GPU memory | pages counted once per mapping (alias views) | [memory](../reference/memory.md) |
| RSS looks fine, the machine swaps | RSS hides the footprint | [memory](../reference/memory.md) |
| disk fills during a run | dense frame dumps, or a runner watching the wrong process | [memory](../reference/memory.md), "Disk" |
| `du` says a clone is 8 GB | copy-on-write clones share blocks | |

## Process

| What you see | Cause | Go to |
|---|---|---|
| queued runs die with a bash syntax error at a random line | the script was edited in place while queued | [machine-safety](../reference/machine-safety.md), rule 8 |
| a chain started although nobody gave the go-ahead | its gate matched free text in the notes | [agents/README](README.md), section 4 |
| a run's results are another run's | the reader took the oldest (or newest) folder, or tags differed only by case | [profiling](../reference/profiling.md) |
| builds never finish | back-to-back performance-class runs pause them | [profiling](../reference/profiling.md), "Two classes of run" |
| an injected dylib breaks the run before the title starts | the variable reaches every process of the runner, the timeout tool first | [playbook](../playbook.md), section 4 |
| a pre-commit hook rejects your commit | the identity or the game-data check; fix the cause, never `--no-verify` | [AGENTS.md](../../AGENTS.md), "Git" |
