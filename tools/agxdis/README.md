# agxdis — SHADER X-RAY layer 6: the final Apple GPU (AGX) machine code

`agxdis.sh <MSL file | .metallib | binary archive>` prints the disassembly of what Apple's
Metal compiler finally emits for a shader, plus one grep-able `STATS` line per function
(instruction count, registers, spills, device load/store widths, texture ops, loops/branches).

OFFLINE ONLY. It compiles pipelines into an `MTLBinaryArchive` (or runs `xcrun metal` +
`xcrun metal-tt`) and reads the result. Nothing is drawn or dispatched; no GPU counters.

## Usage

    work/agxdis/agxdis.sh shader.metal                 # this Mac's GPU (M4 Pro = applegpu_g16s)
    work/agxdis/agxdis.sh shader.metal --stats-only    # only the STATS lines
    work/agxdis/agxdis.sh shader.metal --arch applegpu_g13g   # other GPU, fully offline (metal-tt)
    work/agxdis/agxdis.sh lib.metallib -f main_entrypoint
    work/agxdis/agxdis.sh archive.bin                  # MTLBinaryArchive / metal-tt output
    work/agxdis/agxdis.sh code.raw --raw --isa g15     # raw bytes of one function
    work/agxdis/tests/run_tests.sh                     # 237 checks, ~40 s

Options: `-f NAME` (entry point, repeatable), `--arch applegpu_gNNx`, `--format FMT` (pixel
format assumed for float4 colour outputs), `--prolog` (also the uniform prolog function),
`--json FILE|-`, `--keep FILE` (save the archive), `--strict`, `--raw --isa g13|g15`.
First run builds `build/getbin` + `build/metal-archive-extractor` and clones `ext/` (`build.sh`).

A fragment-only MSL file (the shape of the dumps in `work/mslover/dump_h`) gets a pass-through
vertex stage appended automatically (matching `[[user(..)]]` outputs); that stage is hidden.

Helper: `build/getbin [-f fn] [--color N=FMT] [--depth] -o out.bin <msl|metallib>` = MSL in,
binary archive out. `getbin --list file` prints the entry points, `getbin --info` the device.

### Listing

    ; compute main (_agc.main, applegpu_g16s, isa g15)
        0:  4ca01006          get_sr       0, r4, sr32 (thread_position_in_grid.x)
        e:  6700440000802000… device_load  5, i32, xyzw, r0_r1_r2_r3, u0_u1, r0.discard, lsl 2, …

Column after the offset: ` ` fully decoded, `~` name and length known but operands not decoded,
`?` unknown instruction (length is a heuristic), `!` decoding stopped.

### STATS fields

| field | meaning |
|---|---|
| `stage`, `fn` | vertex/fragment/compute; `main`, `prolog`, or a helper symbol |
| `gpu`, `isa` | archive architecture; decoder family (`g13` = G13/G14, `g15` = G15/G16) |
| `bytes`, `instr` | code size and instruction count up to the final `stop` |
| `gpr`, `gpr_max` | distinct 32-bit general registers referenced, highest index |
| `ureg`, `creg` | distinct uniform registers, coordinate registers (G15+) |
| `stack_ld`, `stack_st` | stack **memory** loads/stores (local arrays; on G13 also register spills) |
| `spill_ld`, `spill_st`, `spill_slots` | G15+: reloads from / writes to stack-slot registers (`stN`), distinct slots |
| `spill` | 1 if any of the five above is non-zero |
| `dload`, `dload_bytes`, `dload_w` | device loads: count, bytes, histogram by width (`i32x4:1` = one load of 4×32 bit) |
| `dstore…` | same for device stores |
| `tgload`, `tgstore`, `atomic` | threadgroup-memory ops, atomic memory ops |
| `tex_sample`, `tex_load`, `tex_store`, `tex_other` | MSL `sample()`, `read()`, `write()`; texture ops of an undecoded form |
| `loops` | distinct backward jump targets |
| `if`, `else`, `jmp`, `call` | counts of those instructions |
| `partial` | `~` instructions |
| `unknown` | `?` instructions |
| `unsure` | decoded, but the decoder flags unexpected bits (`.todo`); their operands are not used for register stats |
| `trunc`, `complete` | bytes not decoded; `complete=0` means the stats are a lower bound |

Exit codes: 0 ok · 2 usage · 3 input unreadable/empty/garbage · 4 shader compile error ·
5 no usable entry point · 6 pipeline/archive failure · 7 no Metal device · 8 unsupported GPU ·
9 decode incomplete (or any `~`/`?` with `--strict`) · 10 not a shader archive · 11 build/tool missing.

## GPU generations

This Mac: Apple M4 Pro, kext `AGXAcceleratorG16X`, archive arch `applegpu_g16s` (cpusubtype 467).

| generation | chips | ISA | decoder | status here |
|---|---|---|---|---|
| G13, G14 | M1, M2 families | "G13" | dougallj/applegpu `main` | works; tested through `--arch applegpu_g13g/g14g` |
| G15, G16 | M3, M4 families | new encoding since G15 | TellowKrinkle/applegpu branch `M3` | works on this Mac; see coverage below |
| G17 | M5 / A18-A19 | not verified | — | refused with exit 8 (raw code sizes are printed); `--isa g15` forces a try |

The upstream applegpu `main` cannot decode G15+ (on this Mac's code it prints
`<disassembly failed>` and then raises); the `M3` branch can. `xcrun metal-tt -arch applegpu_g16s` produces byte-identical code to
the device compiler for every fixture compared (test "device == metal-tt").

## Findings (all reproducible with the fixtures)

**Does Apple's compiler merge adjacent scalar device loads into vector loads? Yes for typed
pointer/array indexing, no for integer-address loads.**

| fixture | source | AGX result on G16 (`scalar4`, `gap4`, `kkstyle4` also checked on G13 and G15: same) |
|---|---|---|
| `scalar4` | `a[i] a[i+1] a[i+2] a[i+3]` (float) | **1** load `i32, xyzw` — identical width to `vec4` |
| `vec4` | one `float4` load | 1 load `i32, xyzw` |
| `scalar8` | 8 adjacent `uint` | 2 loads `xyzw` (4 components is the maximum) |
| `ushort4` | 4 adjacent `ushort` | 1 load `i16, xyzw` |
| `twobuf` | 2+2 from two buffers | 2 loads `xy` |
| `gap4` | `a[i] a[i+2] a[i+4] a[i+6]` | 4 scalar loads |
| `alias4` | loads interleaved with stores to another buffer | 1 `xyzw` load + 1 `xyzw` store (buffers are treated as not aliasing) |
| `store4` | 4 adjacent scalar stores | 1 store `xyzw` |
| `kkstyle4`, `kkstyle4c` | `*(device uint*)(base + N)`, N = 0,4,8,12 (translator style; also `coherent`) | **4 scalar loads — not merged** |
| `kkstyle4if` | same, 2 of the 4 inside an `if` | 4 scalar loads |
| `kkstyle4v` | one `packed_uint4` load through the integer address | 1 load `xyzw` |

The merge is done by the AGX backend, not the front end: the AIR for `scalar4`
(`xcrun metal -S -emit-llvm`) still has four `load float`. For the integer-address form the
backend instead precomputes each `base + N` into its own uniform register pair and loads
4 times. In 33 real dumped functions (`work/mslover/dump_h`, 30 files) 3068 of 3322 device
loads are single 32-bit loads. So vectorising loads in the translator (KK_MSL_OPT_VEC_LOADS)
does change the final code.

**Spills.** `regspill` (600 live values): G16 uses exactly 96 GPRs (r0..r95), then 695 stack-slot
registers (`mov stN, r…` / `get_stack r…, stN`): 2026 reloads + 1107 slot writes in 6961
instructions. G13 uses 128 GPRs, then stack memory. A local array (`spill`) goes to stack memory
on both. In the sampled real shaders at least 3.4 % of all instructions are slot reloads/writes;
megakernel `f076f1328fc3c57c` uses all 96 GPRs and has 3381 reloads + 821 slot writes (310
slots) in 49 410 instructions.

**Texture ops.** On G15/G16 the M3 branch names are the reverse of MSL: its `texture_load` is
emitted for MSL `sample()` and its `texture_sample` for MSL `read()` (checked with compute and
fragment `sample`, `sample(level)`, `read`, `read(lod)`). STATS use the MSL meaning and the
listing adds `; = MSL sample()` / `read()`.

## Decode coverage and limits

- Sample of 30 real dumps (33 functions, 172 653 decoded instructions): 98.5 % fully decoded,
  1.5 % `~`, 0.01 % `?`, 3.5 % `unsure`. 31 functions decode to the end; 2 fragment shaders
  (`01647a8f3fcfe8cd`, `80c5ca7230417281`) lose sync and stop early (exit 9, `complete=0`,
  39 128 bytes not decoded).
- Not operand-decoded on G15/G16: stack-memory load/store, vertex output store, fragment
  varying interpolation (`iter`), the fragment epilogue (tile store), atomics, `simd_*`
  reductions, a float-ALU variant (`falu_111`), 20-byte texture forms (`tex_other`).
  They are counted but contribute no registers, so `gpr` can be slightly low.
- The length of `falu_111` (8 bytes + extension words) is my own rule from 28 instances in one
  real shader plus the fixtures; agx-re supplies names/lengths the M3 branch lacks. Both are
  marked `~`. A wrong length can desynchronise the stream without a `!`; rising `unsure`
  /`unknown` counts or odd registers are the sign.
- Fragment code depends on pipeline state that is not in the MSL (attachment formats, sample
  count, blending). agxdis assumes RGBA8Unorm for float4 outputs (`--format` to change),
  narrower/integer formats for other outputs, Depth32Float if the shader writes depth. Only the
  epilogue (pack + tile store) is affected.
- One entry point per stage per run; pick with `-f`. A fragment-only **metallib** cannot get the
  stub vertex stage (no source to scan).
- Exact instruction-count assertions (`scalar4` = 7 instr / 58 bytes) hold for macOS 26.6.2 /
  Metal toolchain 27.2 and will flag a compiler change.
- No timing: this shows what code runs, not how long it takes.

## Tests

`tests/run_tests.sh` (237 checks, exit 0 = all pass): 15 parser unit tests
(`tests/test_stats.py`, synthetic input); 26 fixtures in `tests/fixtures/` with known properties
on the device route and through metal-tt for G13/G14/G15/G16; STATS against an independent
recount of the listing; determinism (same input → byte-identical output, device ×3, offline ×2,
JSON); MSL = metallib = archive = raw input; device = metal-tt; over 20 error cases (missing/empty/
garbage input, compile error, no entry point, unknown `-f`, bogus arch, G17, unknown cpusubtype,
foreign Mach-O, truncated archive, undecodable raw bytes, `--strict`).

## Licence

| component | source | commit | licence |
|---|---|---|---|
| `ext/applegpu` (G13 decoder, archive extractor source) | github.com/dougallj/applegpu | 28dd804 | BSD-3-Clause |
| `ext/applegpu-m3` (G15/G16 decoder) | github.com/TellowKrinkle/applegpu, branch `M3` | dc396ba | BSD-3-Clause |
| `ext/agx-re` (`tools/agx-isa`, optional name/length fallback) | github.com/ADevWithAnIdea/agx-re | c677ff9 | GPL-3.0 (code), CC-BY-NC-SA-4.0 (docs) |

The clones are unmodified and only imported/executed at run time; nothing is pushed. agx-re is
GPL-3.0: keep it out of anything that gets distributed (`AGXDIS_NO_AGXRE=1` runs without it;
instructions it named then show as `?` or stop the decode). `agxdis.py`, `getbin.m`,
the tests and fixtures are ours.
