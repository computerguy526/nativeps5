# Architecture

How a PS5 title becomes a native arm64 macOS program here, layer by layer, and where each layer's findings are
written down. Numbers are from the development Mac (M4 Pro, 24 GB, macOS 26) unless stated.

```
PS5 title (x86-64 ELF modules, your own dump, read-only)
   |  ps5recomp: static x86-64 -> arm64 translation, ahead of time
   v
native arm64 binary  + runtime (HLE of the system libraries, threads, files, pad, audio, video)
   |  guest/host memory seam: guest address + constant bias, write and access watches
   v
GPU back end (one of two, chosen per title)
   anyps5: AGC driver + RDNA -> SPIR-V shader recompiler          (bring-up target PPSA30552)
   kyty:   Kyty command processor + renderer as kyty_gpu.dylib    (bring-up target PPSA21564)
   |  Vulkan
   v
KosmicKrisp (Mesa): Vulkan on Metal 4. SPIR-V -> NIR -> MSL; pipeline archive
   |  Metal 4
   v
Apple GPU (AGX). MSL -> AIR -> AGX machine code is Apple's compiler; we can read its output (tools/agxdis)
```

## 1. Static recompilation (`ps5recomp`)

- The game's x86-64 code is translated once, before the run. There is no interpreter and no JIT, and the project
  must not grow one. A function the static analysis missed is translated by the same translator on first use and
  cached on disk; the translator therefore has to be reachable at run time (a run once failed with
  `translator_spawn_failed` because its build folder had been cleaned).
- x86 has total store order; arm64 does not. The translator emits TSO-preserving memory accesses unless it can prove
  an access is thread-private. The sound version costs +18 % host instructions on the larger title and +9.7 % CPU on
  a small one. It replaced a faster lowering that produced 13.6 million forbidden outcomes in a litmus test. Speed
  work on the translator starts from soundness, not the other way round.
- The runtime reimplements the system libraries at the call boundary (HLE). Small shims matter: a lean `malloc+free`
  went from 13.4 to 6.8 ns, the process clock from 16.1 to 2.9 ns.
- A translated binary is tied to the translator version and to the build of the game. Patches that address the
  translated code by host address need a per-binary shift ([games/PPSA21564](../../games/PPSA21564/README.md),
  "Patches").

## 2. The guest/host memory seam

The GPU back end reads and watches guest memory constantly, so how guest addresses map to host addresses decides a
large part of the CPU cost.

**Identity mapping is impossible on arm64 macOS.** Probed with fixed, non-overwriting mappings: the guest image
base `0x400000` lies inside the mandatory 4 GiB `__PAGEZERO`; the guest dynamic zone at `0x2_0000_0000` is taken by
the dyld shared cache; `0x10_0000_0000` is taken by a GPU carve-out, and that is exactly where PPSA21564 places its
fixed 3.2 GB direct-memory block.

**Rule: host = guest + one constant bias (2^40), one contiguous view of the whole guest space.** Translation is an
add, never a lookup, and null stays null. The GPU back end keeps *guest* addresses as its keys everywhere (command
words, texture and target bases, labels, tracker pages, hashes, values written back to the guest) and adds the bias
only at the dereference.

**The back end never calls `mprotect`.** It asks the host to watch a guest range (`protect_guest`), and the host
applies that to the biased view. Two consequences of the platform:

| Fact | Consequence | What was done |
|---|---|---|
| Host pages are 16 KiB, the guest tracker works in 4 KiB pages | An access to an unwatched 4 KiB page faults when a watched neighbour keeps the 16 KiB host page closed | The host carries out the trapped load or store on an alias mapping (about 3 us) without calling the library. 86 instruction forms are tested against the CPU |
| The translator's TSO loads are `LDAPR`/`LDAPUR`/`STLUR` forms, not plain loads | "Not a plain load" faults could not be emulated and cost 10-17 ms each | Emulator coverage for those forms |
| A Metal no-copy buffer made over a range with one read-only page silently drops GPU writes for the whole buffer | Wrong pictures with no error | Every page of a host import must be writable while the import is made |
| A back-end thread reading through the guest view of a page it watches itself faults on its own watch | 10 ms drains, 10-21 ms per frame on the command-processor thread | Back-end threads read through the alias, not the guest view |

Measured on PPSA21564: a level whose worker threads sample more GPU-written pages than the eager-readback set holds
spent 1,250-1,700 ms per second in the fault handler; a larger set (64 -> 256 pages) brought that to 8-77 ms per
second. The fault path is the first thing to look at when a level is slow and the GPU is not busy.

A second read path showed the same class of cost: every recorded guest read in the draw-preparation workers went
through a reader-writer lock and two map lookups ([frame-rate.md](frame-rate.md), "CPU-bound levels").

## 3. GPU back ends

Two back ends exist because they were measured against each other on the same title in the same session:

| PPSA21564 scene | `kyty` | `anyps5` |
|---|---|---|
| title | p50 34.3 ms (28.8 fps), 11.5 GiB | p50 109.2 ms (8.0 fps), 16.5 GiB |
| world map | 57-66 ms (16 fps) | 223.9 ms (4.2 fps), 17.2 GiB |
| first level | 63.7-64.5 ms (about 14 fps) | 330-730 ms |

`anyps5` remains the back end of PPSA30552 (an Unreal Engine title), where most of the pass, cache and pacing work
in [docs/playbook.md](../playbook.md) was done. The Kyty back end is loaded as a dylib through a small C ABI
(pointer mode "guest", host callbacks for memory watches, faults and host information). What it needed to start on
KosmicKrisp: three device features made optional at device selection (image-view min LOD, 64-bit buffer atomics,
non-solid fill), a lowering for 64-bit buffer atomics, and a replacement for per-vertex fragment inputs
([rendering.md](rendering.md)).

## 4. KosmicKrisp (Vulkan on Metal 4)

- Metal 4 has no hazard tracking: ordering is only what the driver encodes as barriers. There is no per-draw
  resource registration; one residency set belongs to the queue. Bindings are bindless: one argument table with
  three buffer addresses, everything else reached by GPU address. Advice written for Metal 3 argument buffers does
  not apply ([rendering.md](rendering.md), "Barriers").
- Shaders go SPIR-V -> NIR -> MSL text -> Apple's compiler. The MSL generator carries workarounds for Metal
  compiler bugs; they cost instructions and are the main shader lever ([shaders.md](shaders.md)).
- Compiled pipelines persist in a segmented Metal 4 archive plus a private OS Metal cache. Warm start of one scene:
  Metal compile time 25.0 -> 0.42 s, compiles 46 -> 1, time in frames over 250 ms 12.0 -> 0.3 s.
- The patch series is in `kosmickrisp/patches`, one file per change, on the Mesa commit in
  `kosmickrisp/MESA_COMMIT`.

## 5. What bounds a frame

Read this before choosing what to work on; the answer differs by scene ([profiling.md](profiling.md) has the
method):

| Bound | Sign | Where it was found | Lever |
|---|---|---|---|
| GPU, resolution | GPU busy 95-99 %, the command processor mostly waits for the GPU | title, world map | the game's own dynamic resolution ([frame-rate.md](frame-rate.md)) |
| GPU, shaders | same, at the lowest resolution | world map, first level | MSL workarounds ([shaders.md](shaders.md)) |
| CPU, draw preparation | 5,000 draws per frame, the command processor waits for its workers, not the GPU | the 10 fps levels | read path, per-draw commit cost |
| Memory | exit code 86 at level load | 4 of 8 levels | texture residency ([memory.md](memory.md)) |
| The machine | 1 fps with everything "correct" | any | battery, another GPU client ([profiling.md](profiling.md)) |
