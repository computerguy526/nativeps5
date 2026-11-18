# Shaders

The longest translation chain in the project, and the one where the most ideas turned out not to matter. Every
finding here was made offline or with in-run switching; none needed GPU timestamp queries.

## The chain

| Layer | Form | Who makes it | How to look at it |
|---|---|---|---|
| 1 | PS5 shader bytecode (RDNA2 instructions) | the game | the back end's disassembly dump |
| 2 | SPIR-V | the back end's shader recompiler | `spirv-dis` |
| 3 | NIR | Mesa, inside KosmicKrisp | NIR dump hook |
| 4 | MSL text | KosmicKrisp (`nir_to_msl`) | MSL dump; statistics per shader |
| 5 | AIR (LLVM IR) | Apple's compiler | `xcrun metal -S -emit-llvm` |
| 6 | AGX machine code | Apple's compiler | `tools/agxdis` |

`tools/xray` takes one shader through all layers offline and prints one summary line; `tools/agxdis` gives, for the
GPU in this Mac, instruction count, registers used, spill slots, device-load widths, texture operations and loops.
`tools/shaderlab/gate.sh` compiles a corpus through a KosmicKrisp install and compares against a baseline; it is the
gate for every KosmicKrisp and recompiler change. All three need your own corpus: shaders in any form are game data
and are never committed.

Layer 6 is where arguments end. Several ideas looked right at layer 4 and wrong at layer 6, and the reverse.

## What the corpus looks like

One title, 6,181 unique SPIR-V modules (4,277 pixel, 1,357 vertex, 547 compute):

| Stage | MSL bytes p50 / p99 / max | AGX instructions (estimate from measured ratios) |
|---|---|---|
| vertex | 93 k / 486 k / 771 k | |
| pixel | 864 k / 4.30 M / 6.62 M | p50 2.7 k, p90 9.1 k, p99 24.5 k for the whole corpus |
| compute | 147 k / 6.30 M / 8.39 M | |

The giants are tiled-lighting compute "megakernels" (40-49 k instructions, 6-7 MB of MSL, 128 threads per group,
6-7 loops, about 57 atomic bit-mask writes to device memory) and pixel shaders with 212-296 LOD-statistics sites
(20-31 k instructions). 93 modules above 20 k instructions hold 9 % of all instructions; the 1,609 between 5 k and
10 k hold 43 %. The cost is spread, not concentrated.

## Findings

### MSL text size is not cost

Indentation alone is 44 % of all pixel-shader MSL bytes and 57 % of a megakernel. Metal's compile time does not
depend on it. Do not optimise text size.

### KosmicKrisp is not the bloat at machine level

The same megakernel through SPIRV-Cross is 27 % bigger (61.7 k against 48.7 k instructions). The size comes from
the recompiled shader itself: a register file of hundreds of scalars zero-filled per lane, wave operations mapped
to SIMD-group shuffles, and the workarounds below.

### The 96-register cap and spills

The GPU has 96 general registers per thread; beyond that the compiler spills to stack slots. 15 of 48 hot shaders
sit at the cap; each megakernel carries 3.3-4.4 k spill operations. Removing workarounds shaves 0-10 % of their
instructions and leaves the register count at 96 and the spills about unchanged. Register pressure is a code-shape
problem (live state per thread), not something a text pass fixes. Removing the zero-fill of the register file
changes instructions by 0.1 %.

### Workarounds cost instructions; removing them is the ceiling

KosmicKrisp guards against Metal compiler bugs: coherent device loads (a store-to-load miscompile), NaN guards on
float compares, safe-math wrappers. In-run switching between MSL sets for the same pipelines, GPU-bound scene, GPU
busy time per frame:

| MSL set | World map | Exact? |
|---|---|---|
| generated, no trims | 49.7 ms | |
| result-preserving trims and rewrites | 44.2 ms (-11 %) | yes |
| + coherent, NaN guards and safe-math removed | 41.4 ms (-16 %) | no, needs a picture check |
| + early fragment tests for discarding shaders | 42.1 ms | no gain |
| + fast-math | 41.5 ms | no gain |

So **about 16 % of GPU time is the most that better MSL can buy on this scene**, and 11 % is available without
changing results. That bounds what a direct-Metal renderer could gain from shader quality alone. The first group of
trims is the default (`KK_MSL_OPT`, below): 57.6 -> 53.7 ms on the same scene with an earlier build.

### The megakernels are not on the critical path (in one scene) and are it (in another)

Knocking out all four megakernels changed the world map's frame by +0.1 ms: their 11 ms of commit time overlaps
other work. All hot pixel shaders together were worth 17 ms; no single shader more than 1.2 ms. An earlier
three-frame sample had suggested 30.5 ms without the megakernels; it did not reproduce over 48 presents.
In the first level a different set of four megakernels sits in a serial chain of nine dispatches behind barriers
([rendering.md](rendering.md)) and the scene pays the full 10.8 ms. "Is this shader expensive" has no answer without
the scene and the commit it runs in.

### Apple merges typed adjacent loads, not integer-address loads

Apple's compiler merges `a[i]..a[i+3]` on a typed array into one four-wide load. It does not merge KosmicKrisp's
style of load, `*(device uint*)(base + N)`, coherent or not: 3,068 of 3,322 device loads in real hot shaders are
single 32-bit loads. So vectorising loads has to happen before MSL, in NIR (`nir_opt_load_store_vectorize` on
global loads), and it has to run before the pass that wraps loads in selects. Built, exact on 52.4 million GPU
checks, not yet measured in a title.

### Coherent loads: where removing them helps and where it hurts

`coherent` blocks merging. Dropping it in shaders that never write device memory is exact and is already in the
default; after that the hot pixel shaders contain no coherent loads at all, so further "coherent off" work gives
them nothing. In the megakernels (atomics only, no plain stores) dropping coherence per address base merges loads
(device loads -36 to -38 %, vector loads 133 -> 247) for 0.3-1.8 % fewer instructions: small. On one giant pixel
shader the same switch made the code 27 % *larger*.

A census that measured "coherent off = -19.5 % instructions" had used the untrimmed MSL as its baseline; against the
shipped default most of it was already there. State the baseline of every delta.

### Constant wave reductions and NaN guards (exact fixes)

Metal cannot fold a wave reduction of a constant, so blocks guarded by `ballot(false) != 0` were compiled and
tested: folding them gave giant pixel shaders -16 % to -65 % instructions. NaN guards are 9 % of a megakernel;
dropping them where the operand is provably not NaN, and joining the rest with `&` instead of `&&`, gave megakernels
-1.2 to -4.0 % and pixel shaders -4.9 %. Exactness test: 463.9 million GPU checks, no mismatch, with a negative
control. In-game numbers were pending when this was written.

### LOD statistics inflate pixel shaders

The texture streamer's statistics are written by the pixel shaders themselves (1,422 atomics in one shader). They
cannot simply be dropped: the game reads them ([rendering.md](rendering.md)).

### Smaller facts

- The recompiled shaders of the Unreal Engine title contain no 16-bit float arithmetic (0 of 31,356 instructions),
  only pack operations: an fp16 pass-through has nothing to pass.
- Merging buffer loads in the recompiler without the matching KosmicKrisp change made the MSL 0.85 % larger: the
  driver scalarised them again.
- One opt-in bit (branch-free guarded loads) produced 400-700 ms frames in its only run, minutes before a machine
  reboot. Cause not isolated. Check a new codegen bit with an offline GPU timing run before a title run.
- Mesh shaders and stream-out vertex shaders cannot be decoded by `agxdis` yet; the hottest world-map shader is one.

## Our Metal kernels (v1.1)

Kyty has a second shader back end, `recompiler/backend/msl/`, that emits MSL directly from Kyty's IR (the same
front end as the SPIR-V path), in KosmicKrisp's binding convention ("kk-bda": root table at `buffer(0)`, sampler
table at `buffer(1)`, Kyty's descriptor set 0 in KosmicKrisp's layout). KosmicKrisp patch 0023 lets a run replace an
application compute kernel: `KK_MSL_OVERRIDE_DIR=<dir>` holds `<b3>.metal`, where `<b3>` is the first 16 hex digits
of the BLAKE3 of KosmicKrisp's own MSL for that kernel; the file starts with
`// kk-mslinject v1 local_size X Y Z entry NAME math safe|relaxed|fast`. KosmicKrisp still builds its own pipeline,
so a replacement that fails to compile changes nothing; `KK_MSL_OVERRIDE_ASYNC` (patch 0024, default 1) compiles
replacements on a background queue and keeps KosmicKrisp's kernel until the replacement is published;
`KK_MSL_OVERRIDE_PERIOD=<s>` alternates replacement and original inside one run (A/B); `KK_MSL_OVERRIDE_LOG=1`
logs the key of every compute kernel. Which kernels are replaced is decided per title by the keys list
`games/<id>/metal-shaders.tsv` and generated locally by `nps5 cache prepare` (scripts/nps5lib/metal.py; games/README.md).
Rules for a row: bit-identical (or within 4 ulp for the documented relaxed-contraction class) to KosmicKrisp's kernel on
seeded inputs in the differential harness, not slower, and part of a set that passed an in-game smoke and a play test.
Ballots and votes use `simd_or` of lane bits (an `if`-converted `simd_ballot` sees all lanes on macOS 26), and LDS reads
after LDS writes without a barrier get a simdgroup fence (Apple GPUs do not run 32 lanes in lockstep).

## `KK_MSL_OPT` bits

A bit mask; the value is part of the pipeline cache key, so changing it means a cold cache. The default is 1039.

| Value | Meaning | State |
|---|---|---|
| 1 | NaN guard on float compares only for operands that can be NaN (not constants, integer conversions, or twice on one operand) | default, exact |
| 2 | safe math-mode block only around float comparisons | default, exact |
| 4 | coherent device pointers only in shaders that write device memory | default, exact |
| 8 | multiply guard (keeps Metal from folding NaN * 0 to 0) only when a constant factor has a zero or non-finite component | default, exact |
| 16 | early fragment tests for pixel shaders that write device memory and cannot discard; failed fragments then skip their device writes, which changes what statistics writes count | opt-in, no gain measured |
| 32 | the same for shaders that can discard. NOT result-preserving: a discarded fragment still writes depth and stencil | opt-in, no gain measured |
| 64 | guarded device loads as a select instead of a branch | opt-in, exact; see the warning above |
| 128 | coherent only in shaders with plain device stores; a plain load after the shader's own atomic write to the same address is then unprotected | opt-in, no gain measured |
| 256 | one math-mode block per run of statements instead of one per statement | opt-in; megakernel MSL -23 % bytes, GPU time equal |
| 512 | coherent per load: a load stays non-coherent when none of its address bases is stored to by the shader. Limit: one buffer bound through two descriptors counts as two bases | opt-in; see "Coherent loads" |
| 1024 | native wave reductions (`simd_sum`, `simd_min`, ...) instead of shuffle chains; exact for every active-lane set (9.9 million checks) | default |
| 2048 - 524288 | exact-codegen, load-vectorising and big-shader passes | on experiment branches, not in the patch series yet |

## Method that worked

1. Rank by a measured weight (commit milliseconds per second of the commits a shader appears in; an upper bound,
   shared by all shaders of a commit).
2. Decide attribution by knock-out (empty bodies) in an in-run comparison, not by profile rows.
3. Try a variant as MSL text first (override by shader hash, switched live at pipeline bind): no rebuild of the
   driver, interleaved baseline phases.
4. Only then implement it in the generator, behind a bit, with an exactness test on the GPU and the shaderlab gate.
5. Measure in a title last, with a fresh Metal cache per arm.
