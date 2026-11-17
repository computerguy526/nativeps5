# Memory

The development Mac has 24 GB shared between CPU and GPU. PPSA21564 runs 0.5-1.5 GiB under the limit in levels, so
memory is a first-class constraint, not housekeeping.

## The self limit

- The process ends itself with exit code 86 and a `[RED]` line when its footprint passes `NPS5_SELF_MEM_LIMIT_MB`.
  The value is 37/48 of physical RAM (18944 on a 24 GB Mac; `scripts/nps5-env.sh` computes it,
  `scripts/run-title.sh` refuses a larger one). Every machine-dependent
  limit and the rows for smaller machines are in [hardware.md](hardware.md).
- **It is never raised to make a scene pass.** It exists because a title at 9-20 GB that starts swapping takes the
  window server down with it ([machine-safety.md](machine-safety.md)). A scene that hits it is a memory bug: report
  it with the footprint timeline.
- Measure `phys_footprint`, not RSS. RSS once showed 6.5 GB for a process whose footprint was 18-20 GB.
- Exit code 86 is not a crash and not a black screen. Two levels reported as "black" were slow starters that died at
  the limit before their first picture, which comes about 66 s after the first frame.

## Level-load spike: whole mip chains

**Symptom.** Four of eight levels ended with exit code 86 during level load, 50-90 s into the run. Footprint went
11.5 -> 15.4 GiB in two seconds when the level started.

**Cause.** Textures. The game streams: at level start about 1,000 textures have only their coarse levels resident.
The back end uploaded only those levels but created the native image with the whole chain, and Metal commits a
texture's whole storage at the first write to any level (probe: 32 unwritten 4096^2 block-compressed chains cost
nothing; with levels 2 and up written, all 683 MiB). KosmicKrisp has no sparse-residency images. Result: 2.9-3.2 GiB
"partially resident, in levels no view samples".

**What it was not** (each checked): the 4K render-target set (350-414 MiB; all stale targets together 0.5-0.7 GiB),
compile memory or the pipeline archive (malloc flat through the jump, mapped files 0 B of footprint), staging
buffers (fixed sizes), or the budget (everything was in use within the last frame, so the collector only churned).

**Fix.** `KYTY_TEXTURE_RESIDENT_TRIM=1`: the native image of a partly resident texture holds only the resident
levels, and grows when more arrive. Peaks 16.6-16.8 GiB on the levels that used to die; frame rate unchanged (19.1
against 19.1 fps, one run each). It is in the canonical environment.

Found on: PPSA21564.

## Double counting through alias mappings

**Symptom.** Footprint 19.7-20.6 GiB in a level whose guest and GPU memory did not add up to that.

**Cause.** macOS counts a page in the footprint once **per mapping**. One GiB touched through the guest view and
through a remapped alias is 2,049 MiB of footprint, and 1,025 again when one view is made inaccessible. The memory
seam's always-writable alias views ([architecture.md](architecture.md)) therefore double-counted every guest page
the GPU back end read or wrote through them.

**Fix.** A periodic alias trim (`NPS5_KYTY_ALIAS_TRIM_MS`): peak 16.2 GiB instead of 19.7-20.6, about 4 ms per trim
pass, frame time unchanged. Its first version waited at a blocking gate and hung after 22 passes, because a
resolution in flight waited for a thread held at the gate; the gate now skips the pass when there is no quiet
instant within 250 ms.

Found on: PPSA21564.

## GPU budget and collectors

- The GPU layer needs a budget (`KYTY_VRAM_BUDGET_MB`, 6144 in the canonical environment). Without one on PPSA30552
  about 17 GB of a 17-20 GB footprint was the GPU layer (a 12 GB Vulkan heap); a budget plus a settings overlay
  brought it to 8.6-8.8 GB.
- A collector that runs when memory is above its trigger runs *always* in a level, where usage sits above the
  budget with nothing old to free. One that waits for the GPU first costs frame time
  ([frame-rate.md](frame-rate.md), "CPU-bound levels").
- A texture freed and recreated within 120 frames is churn; a reuse tracker that raises the age floor is built
  behind `KYTY_TEXTURE_GC_REUSE=1`, default off, unmeasured in a title.
- Render targets the GPU wrote and nothing used for 1,000 frames are never retired today. That is the 0.5-0.7 GiB a
  resolution lock can save, no more.

## Mip skip

`KYTY_TEX_MIP_SKIP=1|2|3` floors the first level of large sampled 2D textures (longest side at least
`KYTY_TEX_MIP_SKIP_MIN`), on top of the resident trim. Level arithmetic is unit-tested (4.7 million checks). No
title measurement yet; the ceiling in steady state was estimated at about 905 MiB for one level.

## Disk is memory too

- Each clone of a work directory is 4-8 GB; clone with `cp -c -R` (copy on write). `du` overstates clones.
- Shader caches pile up per version of the recompiler and of the driver. Prune by version, not by age.
- Frame dumps: sparse (every 600th frame or fewer). A runner that latched onto another run's process once dumped
  9.7 GB of frames from boot.
- Keep 8 GB free; the machine guard warns below that and cleans below 15 GB.
- Before deleting a folder you did not create, search the scripts for references to it. A cleanup once removed a
  settings overlay that every run mounted; every run for 19 minutes was dark and had to be redone.
