# Reference

What is known about each subsystem, written once. An entry is a symptom, its cause, the fix and where it was found;
general pages start with the mechanism. Numbers are from the development Mac (M4 Pro, 24 GB, macOS 26) and name
their scene.

| Page | Covers |
|---|---|
| [architecture.md](architecture.md) | the layers from the PS5 binary to the Apple GPU: static recompilation, the guest/host memory seam, the two GPU back ends, KosmicKrisp, and what bounds a frame |
| [frame-rate.md](frame-rate.md) | the game's dynamic resolution and its GPU clock, present pacing, frame caps and latency, CPU-bound levels, pacing ideas that did nothing |
| [rendering.md](rendering.md) | per-vertex fragment inputs, barriers in Metal 4, aliased render targets, LOD statistics, depth bounds, video frames, upscaler placement, picture diagnostics |
| [shaders.md](shaders.md) | the chain from PS5 bytecode to AGX machine code, the tools that read each layer, what the corpus looks like, what shader work is worth, the `KK_MSL_OPT` bits |
| [memory.md](memory.md) | the self limit, level-load texture residency, alias double counting, budgets and collectors, mip skip, disk |
| [machine-safety.md](machine-safety.md) | the incidents (kernel panic, WindowServer watchdog reboots), why GPU work survives the process, loop caps and guards, the rules and their reasons |
| [hardware.md](hardware.md) | supported and untested machines, what is derived from RAM and core count, machine classes and conditional profile rows, per-machine cache identities, how to add a machine |
| [logging.md](logging.md) | the `[subsystem] LEVEL` line convention, `NPS5_LOG`, reading a noisy log (`nps5 log`), the run-end SUMMARY block, and `nps5 bug-report` (one redacted zip for an issue) |
| [profiling.md](profiling.md) | the checklist before a measured run, run classes, comparing two things, where the frame goes on the CPU and GPU without timestamp queries |
| [../env_switches.tsv](../env_switches.tsv) | every environment switch the components read: name, component, meaning (hand-written for the ones these pages use), source file |

## Conventions

- A switch named on these pages that is missing from `env_switches.tsv` lives on a branch that is not integrated
  yet. The table is generated (`scripts/gen_env_switches.py`), and regenerating keeps the hand-written meanings.
- "Built, not measured" means exactly that. Do not quote it as a win; do measure it.
- A fact about one title belongs in its game note ([games/](../../games/README.md)); these pages hold what would be
  true for another title. Each entry ends with the title it was found on.
- Dated observations and negative results are collected in [../learnings.md](../learnings.md); the row-by-row
  history of PPSA30552 is [../playbook.md](../playbook.md).
- Component-specific internals (the translator's lowering, the AGC driver's caches, the Kyty command processor)
  are documented in the component repositories.

## Adding an entry

Symptom (what the log or the picture shows, in words someone would search for), cause (the mechanism, with the
measurement that proved it), fix (what changed, the switch and its default), found on (title ID and scene). If the
entry replaces an earlier belief, say what the earlier belief was: the wrong turns are the most reused part of
these pages.
