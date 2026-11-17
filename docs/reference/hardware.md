# Hardware

Everything in this repository was tuned on one machine. This page says which numbers depend on the machine, how
they are derived for another one, what is known to be at risk there, and how to add a machine to the tested list.

`scripts/nps5 doctor` (its hardware part alone: `python3 scripts/nps5lib/hw.py doctor`) prints this Mac's class, the derived
limits and a verdict. `hw.py --hw m1-16g doctor` prints the same for a machine you do not have.

## Supported hardware

| Machine | Status | Notes |
|---|---|---|
| M4 Pro (10 P + 4 E cores), 24 GB, macOS 26.6, MacBook Pro (`m4pro-24g`) | **tested**: the development machine; every number in these pages | [games/](../../games/README.md) lists what runs |
| M3 / M4 family, 24 GB or more, macOS 26 | untested, expected to work | same GPU feature level; limits scale with RAM, compile workers with cores |
| any Apple silicon Mac with 16 GB | **experimental**, untested | memory limit 12629 MiB; ASTRO BOT levels peak at 16.3-17.8 GiB on 24 GB, so its profile carries a 4096 MiB GPU budget and mip skip 2 for `ram<=16`; expect exit code 86 in levels |
| M1 / M2 family (GPU g13 / g14) | untested, known risks below | |
| M5 family (GPU g17) | untested | `tools/agxdis` refuses g17 (decoders cover g13-g16); everything else is expected to follow M4 |
| MacBook Air (no fan) | as its chip and RAM, but frame times are not comparable | throttles under sustained load; no perf-class numbers from it |
| less than 16 GB, Intel Macs, macOS 25 or earlier | not supported | |

Minimum macOS is 26: KosmicKrisp selects a device by `MTLGPUFamilyMetal4` and compiles through the Metal 4 compiler
and archives (`MTL4Compiler`, `MTL4Archive`); there is no fallback path. One texture feature is used only from macOS
27 (`@available`), MetalFX upscaling is optional and off by default.

### Known risks on untested hardware

- **Compute kernels above 384 threads on M1/M2.** On g13/g14 a compute pipeline whose thread group is larger than
  384 runs nothing, silently, unless the pipeline states its thread count (found by NXNative, commit 529cf68c: 73 of
  255 kernels in one title). KosmicKrisp sets `maxTotalThreadsPerThreadgroup` to the shader's local size on every
  compute and mesh pipeline (`bridge/mtl_compiler.m`), which is the same remedy at the pipeline descriptor. Nobody
  has run it on an M1/M2: a black or half-lit picture there is this until shown otherwise.
- **Features of the newest GPU family.** Depth bounds exist only on the newest family; profiles set
  `kk_fake_depth_bounds=true` for every machine. Other family differences have not been surveyed.
- **16 GB.** The limit is a third lower and the game is not smaller. The hardware rows are derived, not measured.
- **4-6 performance cores.** The back end's command processor, recorder and spin-waiting draw-prep workers
  (`KYTY_DRAW_PREP_SPIN_US=10`) were tuned with 10 performance cores free. Compile workers are reduced by
  `games/hw-defaults.tsv`; the spin settings are not, because nobody measured them. `KYTY_CPU_RESERVE=cp` in the
  preset is Windows CPU-set code and does nothing on macOS.
- **No fan.** Sustained load throttles after a few minutes; a benchmark window measures the thermal state.
- **AGX disassembly** (`tools/agxdis`): g13-g16. On g17 the X-ray layer 6 is absent, nothing else depends on it.
- **A newer macOS.** Compiled caches are per OS build; the first run after an update compiles again.

## What depends on the machine, and how it is derived

One module holds the formulas: `scripts/nps5lib/hw.py`. On the development machine each formula gives exactly the
hand-tuned value (`tests/test_hw.py` holds that), so nothing changed there.

| Value | Formula | 24 GB, 10+4 cores | 16 GB, 4+4 cores | Where it is used |
|---|---|---|---|---|
| Memory self-limit `NPS5_SELF_MEM_LIMIT_MB` | 37/48 of RAM | 18944 | 12629 | `scripts/nps5-env.sh`; `nps5 run` and `game.py --env` refuse more |
| Guard kill `NPS5_GUARD_RUN_CAP_MB` | 5/6 of RAM | 20480 | 13653 | `host/machine_guard.sh` |
| Guard fast polling `NPS5_GUARD_FAST_MB` | 3/4 of RAM | 18432 | 12288 | `host/machine_guard.sh` |
| Guard swap kill and start hold `NPS5_GUARD_SWAP_MB`, `NPS5_GUARD_HOLD_SWAP_MB` | 1/2 of RAM | 12288 | 8192 | `host/machine_guard.sh` |
| Start hold load, perf class `NPS5_GUARD_HOLD_LOAD` | P cores + E cores / 2, at least 4 | 12 | 6 | `host/machine_guard.sh` |
| Start hold load, func class `NPS5_FUNC_HOLD_LOAD` | twice that | 24 | 12 | the run queue of `nps5 run` (with `NPS5_GUARD_HOLD_SWAP_MB`) |
| Build jobs `NPS5_BUILD_JOBS` | 2/5 of the P cores, 2 to 4 | 4 | 2 | `scripts/build.sh` |
| GPU budget default | 1/4 of RAM, at most 6144 (7168 reached the limit on 24 GB) | 6144 | 4096 | a starting value for profiles; ASTRO BOT's rows use it |
| Pipeline journal workers `KYTY_PIPELINE_JOURNAL_THREADS` | 2/5 of the P cores, 2 to 4 | 4 (code default) | 2 | `games/hw-defaults.tsv` |
| Pipeline skip workers `KYTY_PIPELINE_SKIP_THREADS` | 3/10 of the P cores, 1 to 3 | 3 (code default) | 1 | `games/hw-defaults.tsv` |

The order limit < kill < RAM and fast polling < limit holds for every RAM size (tested from 8 to 512 GB). The
memory-pressure part of the start hold (level 2, "warn") and KosmicKrisp's wired-memory guard (0.9 of the device's
`recommendedMaxWorkingSetSize`) already follow the machine. The environment wins over every derived value;
`GUARD_*` variables win in the guard.

Not derived, and why: the guest's direct memory size and the host import size (the game's own requirement), the
draw-prep spin times (unmeasured elsewhere), the AnyPS5 back end's worker counts (their defaults were not
surveyed), run lengths and loading-time expectations (a slower machine needs `NPS5_RUN_REASON` for longer runs).

## Machine class and conditional rows

The machine class is `<chip>-<RAM>g`: `m4pro-24g`, `m1-16g`, `m3max-64g`. The GPU family is the AGX generation
(`g13` M1, `g14` M2, `g15` M3, `g16` M4, `g17` M5): it is what compiled GPU code depends on.

A profile's `env` rows, and the rows of `games/hw-defaults.tsv` that apply to every title, may be conditional on
the machine ([games/schema.tsv](../../games/schema.tsv)):

| Condition | Holds when |
|---|---|
| `hw=m4pro-24g`, `hw=m4pro`, `hw!=...` | the machine class, or the chip at any memory size, matches (does not match) |
| `gpufam=g13`, `gpufam!=g16` | the GPU family matches (does not match) |
| `ram<=16`, `ram>=32` | RAM in GB |
| `cores<=6`, `cores>=10` | performance cores |
| `os>=27`, `os<=26` | macOS major version |

`games/game.py <id> --env` uses this Mac (`--hw auto`), `--hw none` ignores hardware, `--hw m1-16g` shows another
machine; `--explain` names the row behind every word. A condition whose input is unknown never holds, so a profile
without hardware rows, or a machine that cannot be identified, gets exactly the unconditional rows. A row for
hardware nobody has run must say `untested` in its reason.

```
env	KYTY_VRAM_BUDGET_MB=6144	gpu=kyty	24 GB Mac (measured)
env	KYTY_VRAM_BUDGET_MB=4096	gpu=kyty,ram<=16	untested: 16 GB Mac, a quarter of RAM
```

## Compiled caches are per GPU family and OS build

Compiled GPU code is valid for one GPU generation and one OS build. `hw.py cache-key` prints the pair
(`g16-25G83`); `scripts/nps5-env.sh` exports it as `NPS5_HW_CACHE_KEY`. Where each cache gets its identity:

| Cache | Identity | Set where |
|---|---|---|
| OS Metal cache (functions, libraries) | the bundle id `KK_METAL_CACHE_ID`; below it the OS keeps one folder per GPU architecture and discards entries of another OS build | the profile's `env KK_METAL_CACHE_ID=...` row. Use `com.nativeps5.<back end>.<title id>.{gpufam}.{osbuild}`: `game.py` fills the braces. ASTRO BOT keeps the unkeyed name on `m4pro-24g` only (its warm cache) and the keyed one everywhere else |
| KosmicKrisp pipeline archive (`KK_PIPELINE_ARCHIVE_DIR`, `KK_PIPELINE_ARCHIVE`) | stamp = hash of format, KosmicKrisp version and commit, OS build (`kern.osversion`), Metal device name; a segment with another stamp is not opened | `kk_archive_stamp` in `bridge/mtl_compiler.m` (patch 0009). The device name is finer than the GPU family, so nothing was added |
| Vulkan pipeline cache, program cache, journals under the work dir | the work dir's `cache/.hwkey` stamp | `nps5 run` (`keep_cache_per_hardware` in `scripts/nps5lib/run.py`): caches stamped with another key are set aside as `cache.<old key>` (never deleted, restored when that key returns) |
| Prepared-cache directory and Metal cache id of `nps5 cache` (planned) | the stack key must hash `NPS5_HW_CACHE_KEY` together with the list and component commits | not built yet; this is the requirement |

A committed pipeline list (`pipelines.kyplst`) holds keys and state only and is the same for every machine; the
compiled result is not.

## Adding a machine

1. `scripts/nps5 doctor`. Fix what it calls unsupported; note what it calls untested.
2. Build and translate as in [BUILDING.md](../../BUILDING.md). `python3 games/game.py <id> --env --explain` shows
   which hardware rows your machine gets.
3. Run the title's scenes func-class first (`scripts/nps5 run <id> --scene <name>`; `run.json` records the machine
   class and cache key). Exit code 86 is the memory limit: report the
   footprint timeline, do not raise the limit. If a derived row is wrong for your machine, change the row (or add
   one for your class, `hw=<class>`, after the general rows) and say what you measured in its reason.
4. On AC power and a quiet machine, record the benchmark routes: `nps5 bench <id> --record` appends a row with your
   machine class to `games/<id>/validated.tsv` (until that command is in this tree, write the row by hand in the
   format the file's header gives: `class=<machine class> machine=<model> macos=<version>`).
5. In the pull request: the doctor output, the validated row, the rows you changed with `untested` removed only
   where you measured, and a line for the table at the top of this page. Frame times from a fanless machine or from
   battery power are not accepted as benchmarks; "boots, picture checked" still is worth a row.

Found on: PPSA21564 (limits, budgets, mip skip), PPSA30552 (guard and start-hold thresholds).
