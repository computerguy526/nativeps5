# Machine safety

This project can take a Mac down: not the process, the machine. Every rule in [AGENTS.md](../../AGENTS.md),
"Running safely", comes from an incident below. Read the incidents; the rules then explain themselves.

## Incidents

| Date | What happened | Cause | Rule that came out of it |
|---|---|---|---|
| 2026-11-14 21:47 | WindowServer userspace watchdog timeout, reboot | A title at 9.4 GB footprint was swapping while a "quiet" script had sent `SIGSTOP` to Metal processes; a hung driver test (a lock loop in a SIMD group, about 8 minutes on the GPU) contributed | `SIGSTOP` is banned. GPU tests need iteration caps in the shader and a hard kill timeout of 60 s or less. The guard measures footprint, not RSS |
| 2026-11-16 09:26 | Kernel panic in the GPU driver family (kernel data abort); the panicked task was our title | Metal counter heaps: the title exhausted GPU timestamp counter heaps ("too many heaps already allocated", 200+ failures) with the machine at load 29, 5 GB swap, the title near its memory cap. **No profiling switch was set**: the stack allocated timestamp queries on every run of that title by default | No Metal counter sample buffers, counter heaps, timestamp queries or query pools on a title. Ever |
| 2026-11-16 23:49 | WindowServer watchdog reboot | Six agents resumed at once: GPU-heavy title runs, offline Metal harnesses and builds together. The spin report shows WindowServer's own command-queue thread blocked inside the GPU driver: the shared GPU was not serving it | One GPU-using process at a time. Start or resume agents at most one every two minutes. Builds at 4 jobs, nice 5 |
| 2026-11-17 08:00-09:50 | Every run at 0.6-1.5 fps for almost two hours, on every stack | First the Mac was on battery in Low Power Mode; after that WindowServer still took 240-750 ms of GPU time per second with no game running. A reboot cleared it | Check power source and other GPU clients before a measured run ([profiling.md](profiling.md)) |

Other people's reports matter here too: NXNative recorded seven reboots from one runaway compute dispatch
(their issue 2664).

## Committed GPU work survives the process

On macOS a committed Metal command buffer keeps running after its process exits. After `_exit` and after
`kill -9` the GPU stays at 100 %, the process hangs in exit, and WindowServer dies minutes later. A flag raised
from the CPU does not stop it either (NXNative measured 2 of 24 running dispatches observing the store).

Consequences:

- A watchdog that kills the process is a diagnosis, not a stop. It prevents *new* work and tells you which
  submission was running.
- **The only real stop is a loop that ends by itself.** KosmicKrisp therefore caps loop iterations in compute
  shaders (`KK_LOOP_CAP`, 262,144 condition checks per kernel invocation, on by default, a port of NXNative's
  design):
  - one shared counter for all loops of a kernel that contain no barrier;
  - a loop that contains a thread-group or SIMD-group barrier has its own counter and never reads the abort word,
    because the threads of a group must not leave a barrier loop on different iterations;
  - an abort word per command buffer, written only by the GPU, polled every 1,024 checks by non-barrier loops;
  - after completion the host logs `[RED] loop cap hit: submission <n>, kernel <hash>, loop site <i> of <N>`.
  The value is part of the pipeline cache key.
- **Not bounded today:** loops in vertex, fragment and mesh shaders, loops in the driver's own compute passes, and
  indirect draw counts. Indirect dispatch counts above 2^22 thread groups dispatch nothing
  (`KK_DISPATCH_CLAMP`).

## The guards (KosmicKrisp safety patches)

| Guard | Switch | Default | Exit code |
|---|---|---|---|
| GPU watchdog: a committed command buffer not complete after N seconds stops submissions and prints the last 16 commits and the back end's last 24 submissions | `KK_GPU_WATCHDOG_S` | 4 s | 70 |
| Dispatch clamp | `KK_DISPATCH_CLAMP` | 4,194,304 groups | - |
| Queue error "access revoked": stop submitting, raise every waiter's event from the CPU so no thread stays blocked | `KK_QUEUE_ERROR_EXIT` | on | 74 |
| 32 out-of-memory command-buffer failures in 60 s; the first one dumps driver memory by owner | `KK_OOM_MAX`, `KK_OOM_WINDOW_S` | 32, 60 s | 72 |
| Residency guard: Metal allocated size above 0.9 of the recommended working set before a commit | `KK_WIRED_GUARD`, `KK_WIRED_LIMIT_MB` | on | 73 |
| Compute loop cap | `KK_LOOP_CAP` | 262,144 | - |
| Memory self limit (in the runtime and both back ends) | `NPS5_SELF_MEM_LIMIT_MB` | 37/48 of RAM | 86 |

**A guard is code and has bugs.** The first watchdog computed `now - start` with unsigned numbers and took `now`
before scanning: a commit registered during the scan looked 2^64 ns old and the process exited with code 70 while
frames were flowing at 17 ms. Until a guard has run clean on every reference scene, an exit code from it is a
question, not evidence. Test a guard with a deliberate trigger (`KK_TEST_OOM`, `KK_TEST_GPU_LOST`), and test that it
stays silent.

The same happened to the machine guard script: it used a `ps` column that macOS does not have, so its process list
was always empty and its limits never fired; four title runs ran at once before anyone noticed. A guard that cannot
parse its input fails silent. After touching a guard, prove it fires.

## The rules, with reasons

1. **One GPU-using process at a time.** A title run, a frame or shader replay, the driver tests, a GPU timing
   harness and `vulkaninfo` all count. Check with `pgrep` before starting; title runs take a lock.
2. **No Metal counter heaps.** For GPU time use what is safe: commit feedback (GPU start and end per command buffer,
   `KK_GPU_BUSY_LOG`), completion times on a timeline semaphore, per-process GPU time from the IOKit registry, or an
   Xcode Metal System Trace. See [profiling.md](profiling.md).
3. **The memory self limit is not raised.** [memory.md](memory.md).
4. **Short runs** (90 s; up to 180 s with a stated reason) started in the background. A tool timeout that kills a
   foreground run kills it mid-frame.
5. **Kill only your own process IDs.** Never by name or pattern: `pkill -f <binary>` in one runner killed other
   agents' queued runs, repeatedly, and the results looked like crashes. Record the PID of the native child, not
   only of its wrapper; a guard that kills the wrapper leaves the title running as an orphan.
6. **Never `SIGSTOP`** a process that holds GPU work.
7. **No screenshots, no screen recording.** A capture of "the game window region" caught the user's other windows
   when the game window was not up yet. Pictures come from the program's own frame dumps.
8. **No in-place edits of a script that a queued job will execute.** Bash reads scripts incrementally; an edit
   produced syntax errors in queued runs and one false "the run modified the game dump" alarm. Write a new file and
   `mv` it over the old one.
9. **Every new GPU-side loop, wait or retry has a bound**, and every offline GPU test has an iteration cap in the
   shader and a kill timeout.
10. **AC power, no Low Power Mode**, for any run whose numbers you will quote.

## When something goes wrong

- `[RED] gpu timestamp profiling OFF`, `too many heaps already allocated`: stop the run, report; do not retry.
- `[RED] loop cap hit`: a real finding. Report the kernel hash and loop site; do not raise the cap to make it go
  away.
- Exit codes 70-74 and 86: see the table. Say which guard, attach the flight summary from the log.
- The machine becomes sluggish during a run: stop your own run by PID and say so. Do not start a second process to
  investigate.
- After a reboot: run one canonical reference scene and compare with the known frame time before trusting any
  number.
