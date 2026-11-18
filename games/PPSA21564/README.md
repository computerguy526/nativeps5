# ASTRO BOT (`PPSA21564`)

Game note: what the profile needs and why, status per scene, how to reach a scene, patches, known issues, what is
open. No game data is here and none is accepted; you need your own dump ([CONTRIBUTING.md](../../CONTRIBUTING.md)).
Mechanisms that hold for other titles are in [docs/reference](../../docs/reference/README.md) and are only linked
from here.

- **Version:** 01.007 (`pipeline_list_app_ver` 01.007.000). Addresses are guest addresses of that build; confirm
  each by disassembly on your own build before relying on it.
- **Engine and graphics:** Team Asobi's own engine; AGC, compute-heavy (tiled-lighting megakernels), mesh shaders,
  its own temporal upscaler and dynamic resolution; video through the system video player.
- **Back end:** `kyty` (`GPU=kyty` in `titles/PPSA21564.env`). The `anyps5` back end runs the title 3-4 times
  slower ([architecture](../../docs/reference/architecture.md), section 3).
- **Tested:** version 01.007 on a MacBook Pro Mac16,8 (M4 Pro, 24 GB), macOS 26.6, at the component commits of
  [components.lock](../../components.lock) (first public release). Frame time at that release, paired runs, p50:
  world map 36.0 ms (about 28 fps), `hoover_beach` 41.1 ms (about 24 fps; p95 122 ms, bimodal between runs).
  v1 (native 1080p, hitch fixes on) vs the first release's defaults, ABBA (two runs each per scene), 60-110 s window,
  machine not quiet (load 11-17): world map p50 36.1 vs 37.0 ms (p95 37.4 vs 38.8; about 27.7 vs 27.0 fps);
  `hoover_beach` p50 44.1 vs 44.5 ms, p95 62 vs 210 ms (the first release stalls about 200 ms in roughly 30 % of the
  frames in both of its runs; with the hitch fixes 0-2 frames per run take over 100 ms).
  Settings: the preset plus the canonical rows below, the `dr` patch (the profile's default also lists `nt`, see
  Patches), shader caches prepared from the shipped pipeline list.
- **Known issues:** MetalFX 720p/900p upscaling is optional and off by default (the block artefacts are fixed in v1.1; edges are
  more jagged than native); PlayStation symbols look garbled in the warp from the world map
  into a level; closing the window exits at once without flushing the shader caches. Details in "Known issues"
  below.
- **Status as of 2026-11-17** on a Mac16,8 (M4 Pro, 24 GB), macOS 26.6: boots; title, world map and every level
  tried show a picture; controller works; 12-47 fps depending on the scene. [validated.tsv](validated.tsv) has the
  component commits.
- **Profile files:** [game.tsv](game.tsv) (constants, environment deltas, binary lineage, dynamic-resolution facts),
  [patches.py](patches.py) (patches of the translated binary), [bench.tsv](bench.tsv) (routes),
  [validated.tsv](validated.tsv), [pipelines.kyplst](pipelines.kyplst) with its proof record
  ([games/README.md](../README.md) says what each is).

## Status

Not finished. It boots, the title, world map and every level tried show a picture, and the controller works. Frame
rates are below the console's everywhere except the title.

| Scene (launch arguments) | Frame time, fps | Internal resolution | Draws per frame | Peak footprint | Bound by | Picture |
|---|---|---|---|---|---|---|
| title | 21.1 ms, 47 fps | 1080p (game's own dynamic resolution) | 261 | 12.2 GiB | GPU | correct |
| world map (`-skipIntro`) | 44.1 ms, 23 fps | 1080p scene, upscale to 2432x1368, UI at 4K | 643 | 15.6 GiB | GPU, 99 % busy | correct |
| `hoover_beach` | 50.5 ms, 20 fps | 1080p, 2048^2 shadows | 1,735 | 16.5-17.3 GiB | GPU, with a CPU floor of about 30 ms behind it | correct |
| `underwater_aerial_garden` | 83.6 ms, 12 fps | 1080p | 4,947 | 17.4 GiB | CPU: back-end command processor and draw preparation | correct, except the clip panel (below) |
| `shuriken_ninja_castle` | 76.6 ms, 13 fps | 1080p | 5,615 | 16.8 GiB | CPU, as above | correct |
| `ice_iceberg` | 66.8 ms, 15 fps (p95 129) | | | 17.8 GiB | not profiled | correct |
| `volcano_mountain` | 57.4 ms, 17 fps | | | 18.0 GiB | not profiled | very dark; not compared with a console capture |
| `hub_crashsite` | 50.9 ms, 20 fps | | | 18.5 GiB | not profiled | correct |
| `enemy_gorilla` | 61.4 ms, 16 fps | | | 17.4 GiB | not profiled | corridor correct; the fight itself not reached |
| `pot_treasure_cave` | not measured | | | 16.7 GiB | | correct |
| `booster_magic_lamp` | 71.5 ms, 14 fps (p95 162) | | | 17.8 GiB | not profiled | appears 66 s after the first frame |
| `rocket_space_station` | 76.1 ms, 13 fps (p95 151) | | | 17.7 GiB | not profiled | appears 67 s after the first frame |

Sources and caveats: the first five rows are from one interleaved comparison on 2026-11-17 (canonical
configuration below, healthy machine); the level rows from `ice_iceberg` down are single functional-class runs from
earlier the same day, before the completion clock and the read-path fix, and are likely pessimistic. Peak footprints
in the first five rows are from separate runs of the same scenes. Level frame
times are for frames after the picture appears. Levels are entered with `-skipIntro -lvl <name>` and a scripted pad;
nobody has checked play beyond the start of a level. Not tested: boss fights, speed-run and mini levels.

Timing of a level run: the level opens 24-30 s after start, the picture appears 40-66 s after the first frame.
Runs need 180 s (with `NPS5_RUN_REASON`); a run that ends less than 45 s after the first present proves nothing
about black.

## Canonical configuration and why

Start from `titles/kyty-preset.env`. On top of it:

| Setting | Why | Evidence |
|---|---|---|
| `KYTY_EOP_TIMESTAMPS=cbtime` (the preset says `gpu`) | `gpu` uses timestamp queries, which are Metal counter heaps and have panicked the kernel; `record` makes the game's dynamic resolution choose 4K | title 42.7 -> 21.1 ms, world map 82.2 -> 44.1 ms ([frame-rate](../../docs/reference/frame-rate.md)) |
| `KYTY_PS_PER_VERTEX=unpack` | per-vertex fragment inputs do not exist on Metal; without it, black triangles | [rendering](../../docs/reference/rendering.md) |
| `KYTY_TEXTURE_RESIDENT_TRIM=1` | four of eight levels exceed the memory limit at level load without it | [memory](../../docs/reference/memory.md) |
| `NPS5_KYTY_ALIAS_TRIM_MS=1000` | alias mappings double-count the footprint | peak 19.7-20.6 -> 16.2 GiB |
| `KYTY_VRAM_BUDGET_MB=6144` | the GPU layer needs a budget on a 24 GB machine | |
| `KYTY_READBACK_EAGER_PAGES=256` | fault storm in levels with the default 64 | 1,250-1,700 -> 8-77 ms per second in the fault handler |
| `KYTY_PRESENT_ABS_VBLANK=1` (default) | 16.7 ms steps in frame times | stdev 6.4-9.7 -> 1.1-3.6 ms |
| `KYTY_PAD_RELAY_US=4000` (default) | 1000 breaks scripted stick input | |
| `KYTY_PIPELINE_CACHE_SAVE_ON_TERM=1`, `KK_PIPELINE_ARCHIVE_DIR`, `KK_METAL_CACHE_ID` under `NPS5_SCRATCH` | timed runs otherwise lose what they compiled | warm world map: Metal compile time 25.0 -> 0.42 s |
| `KK_MSL_OPT` unset (default 1039) | result-preserving shader trims | -3 to -4 ms on the world map |
| `KYTY_LOD_STATS_MODE=gpu` (preset) | the game's texture streamer reads these statistics | do not change without a memory and picture comparison |

The memory limit stays at 37/48 of RAM. Level peaks are 0.5-1.5 GiB below it on a 24 GB Mac; a 16 GB Mac will not
run a level.

## Patches (`dr`, `nt`, `mode`, `gi`, `lowres`)

Small patches to the *translated* binary (a copy of your own build, re-signed ad hoc; never the dump). Each is one
to ten arm64 instructions. They live in [patches.py](patches.py): per patch the goal, the guest site, why it is
game-bound, its review state, and the cuts (host address in a reference lineage, the original word asserted, the
new word). A host address shifts with every relink; `game.tsv` lists the shift of each known lineage (`0x82f0` for
the canonical `kyty6`). For a new build of the binary, locate the sites by their instruction context (96 bytes
matched for all sites last time) and add a lineage row.

    games/PPSA21564/patches.py --list
    games/PPSA21564/patches.py <copy of your binary> dr kyty6          # then: codesign -f -s - <copy>
    games/PPSA21564/patches.py <copy> dr,lowres kyty6 1280x720

| Patch | Effect | Measured | Verdict |
|---|---|---|---|
| `dr` | locks the game's dynamic resolution at its 1080p level | with record-time clocks: world map 80.5 -> 45.3 ms, title unchanged, first level unchanged | Reviewed. Was the default while the clocks were wrong. With `cbtime` the game reaches 1080p by itself; kept in the canonical binary because it is harmless there |
| `mode` | forces the single 1080p scene-parameter set (mode argument at guest `0x77bcecc`) | title 43 -> 34 ms (2 of 2); world map 45 -> 97 ms (n=1) | Reviewed. Not a default: title benchmarking only |
| `nt` | always takes the non-tiled lighting renderer (guest `0x77eb993`), which skips the tiled-lighting compute megakernels | comparison was running when this was written | Unreviewed. Changes the lighting, so the picture is the user's decision |
| `gi` | stores 0 for the GI probe flag (guest `0x751d0b3`) | not measured on the current stack | Unreviewed; listed so nobody rediscovers it |
| `lowres WxH` | dynamic-resolution level 0 at 1600x900 or 1280x720; the game's own temporal upscaler brings it to 2432x1368 | world map 35.4 ms at 720p, steady picture (n=1) | Superseded by the run-time `upscaling` setting (below). Works only with **both** size sites (guest functions `0x77be670` and `0x74cef60`); one site alone flickers. At 720p and 900p the intro cutscene and the crash site show blocky squares and jagged edges on moving objects and dust (with and without MetalFX): the game never renders below 1080p by itself and some passes keep a 1080p-derived size. Not a default |

Rules for a new patch: measure it on the title, the world map and at least one level before proposing it as a
default; give the guest address, the instruction before and after, the game build, and its review state; assert
the original word. The asserted instruction words are the only bytes of the translated program in this
repository; never add more than the patched instructions, and never ship a patched binary.

The dynamic-resolution controller is guest function `0x7818870`: down when the summed GPU timers exceed 16.67 ms,
up after 31 updates with room, levels 1080p / 1368p / 1872p / 2160p, timers read three frames back.

## Settings

Run-time settings of the translated binary (settings dialog `NPS5_SETTINGS_DIALOG=1`, the settings file, or
`--env`):

| Setting | Options | Default | Notes |
|---|---|---|---|
| `fast_lighting` (`NPS5_ASTRO_FAST_LIGHTING`) | on, off | on | on = the non-tiled lighting path (the `nt` patch as a setting) |
| `upscaling` (`NPS5_ASTRO_UPSCALING`) | `off`, `metalfx900`, `metalfx720` | `off` | `metalfx900`/`metalfx720` render the scene at 900p/720p and scale it with Apple MetalFX (spatial). Optional: the blocky squares are fixed in v1.1, edges are more jagged than native, little fps gain (see `lowres` above); needs the `dr` patch |

## Known issues

| Issue | Where | Cause | State |
|---|---|---|---|
| Tutorial clip panel smeared, green stepped bottom | any level that shows a tutorial clip (880x880 clips; seen at the landing in `underwater_aerial_garden`) | the video-player HLE used a 64-pixel row pitch; the game copies frames into a texture of pitch 1024 ([rendering](../../docs/reference/rendering.md)) | Fix built (pitch rounded up to 256, crops reported); reproduced offline; **not yet confirmed in a run**. Needs a relink of the title, which shifts the patch addresses |
| PlayStation symbols during the warp from the world map into a level look garbled | world map -> level transition | not diagnosed; no frame dump of the warp exists, because `-lvl` runs skip it | Open. Needs a scripted world map -> level run with dense dumps; the draw filter and target dumps exist for it |
| Levels with about 5,000 draws per frame at 12-13 fps | `underwater_aerial_garden`, `shuriken_ninja_castle` | CPU-bound in the back end: per-draw commit cost and a buffer collector that waits for the GPU after every submission ([frame-rate](../../docs/reference/frame-rate.md), "CPU-bound levels") | Read path fixed (104 -> 84 ms together with the completion clock). Open: commit cost per run start (11-14.5 us), one 480x270 effect pass at 211-218 us per draw, the lazy collector settle |
| First level pays 10.8 ms for one commit | `hoover_beach` | nine serial compute dispatches (five small, four megakernels at the 96-register cap with 3.3-4.4 k spills each) and two passes, serialised by barriers | Open. Knock-out to separate shader cost from barrier cost not done |
| 66 s of black before some levels | `booster_magic_lamp`, `rocket_space_station` | loading; not a rendering fault | Pad scripts must wait for it |
| Cold start of a new level compiles for 2-15 s | every first visit without prepared caches | 14-99 pipeline compiles | `scripts/prepare-pipelines.sh PPSA21564` builds the shipped list (1,596 pipelines, 893 shader permutations; about 70 s for the pipelines once the shader libraries are built). A level that is not in the list still compiles on its first visit (short stutters). The prepare shows its progress in the terminal only |
| `volcano_mountain` is very dark | | unknown; may be right | Compare with a console capture |
| Title frame time is bimodal with record-time clocks | title | the dynamic-resolution feedback loop | Gone with `cbtime`; do not use 36-40 fps figures from old notes as a baseline |
| One run of the title crashed (guest bus error) in the integration comparison | title | not investigated, not rerun | Open |
| ~~About 9 % of world-map runs end in a guest bus error (SIGBUS)~~ | world map, 2-3 s after `ui_world_map_galaxy_scan_dlc1` opens | a file read into a guest page failed with EFAULT (the back end protected the page again before the kernel copied), so a 0x6b0-byte animation blob was never written and the loader followed a garbage offset | Fixed: the read retry of the crash-site hub row below, in every release since v1.0.0; 0 of 50 title-to-world-map runs since (3 of about 35 before). v1.0.2 also repeats `readv` and `fread` |
| ~~MetalFX upscaling at 720p/900p shows blocky squares~~ and jagged edges on moving objects and dust | intro cutscene (ship, alien, floor), crash site sand; with and without MetalFX | the game never renders below 1080p by itself; some passes keep a 1080p-derived size or tile grid when the level-0 size is lowered | Blocks fixed in v1.1 (light-tile grid follows the render size, `KYTY_ASTRO_TILE_GRID=auto`); jagged edges remain. `upscaling` is off by default; the three DRS-level size helpers (guest `0x77d75f0`, `0x78395f0`, `0x7839680`) and the 1920x1080 initialisers were ruled out |
| ~~A save written in some situations crashes the game when it is loaded~~ | - | Fixed in v1.0.1: the save-data transfer mount opened the game's own save, so its XML parser read it from the wrong offset | Update to v1.0.1 or later (the `nativeps5-recomp` commit pinned in components.lock) |
| All in-game text missing (menu captions, "PRESS ANY BUTTON", copyright) | everywhere | `sceFontGetRenderCharGlyphMetrics` and the rest of the glyph API returned an error, so the layout dropped every character | Fixed (libSceFont glyph API) |
| Intro video jitter; crash when a video loops | studio logo / intro, looping clips in the hub | frame selection against the clock; codec flush from the demuxer thread | Fixed (frame selection; flush on the decoder thread with a marker packet) |
| Crash on the way into the crash-site hub (file read returns EFAULT) | `hub_crashsite`, world map | a file read into a guest page the back end had just protected: the kernel returns EFAULT instead of faulting, so the game got an error for a valid buffer | Fixed (the read opens the range again and repeats) |
| Flower and turtle effects flicker | some places in levels | late depth test after instrumented (`GET_LOD_STATS`) pixel shaders misses the pre-pass depth | Fixed: early depth/stencil tests for those shaders (`KYTY_LOD_STATS_EARLY_TESTS`, default on; `hoover_beach` burst frames gone). Needs a fresh shader-cache prepare |
| Closing the window exits at once, without the renderer teardown and the on-exit cache flush | any run | the window-close path | Pipelines compiled since the last periodic cache save are lost |
| `hoover_beach` has a fluid-simulation read-fault storm in about half the runs | `hoover_beach` | page-fault handling for the game's fluid simulation | `KYTY_READ_FAULT_PUBLISHED_FRAME_BURST=48` is on in the profile (game.tsv): the ~340 ms stalls went from 3-33 per minute to 0-1.3 |

## How to reach a scene

- Arguments: none (title), `-skipIntro` (world map), `-skipIntro -lvl <name>` (a level). Level names are the folder
  names under the title's level data. [bench.tsv](bench.tsv) has the measured routes with their pad scripts and
  windows.
- First: `scripts/prepare-pipelines.sh PPSA21564` builds the compiled caches for 1,596 pipelines from
  [pipelines.kyplst](pipelines.kyplst) and your own dump, so first visits do not stutter on compiles
  ([games/README.md](../README.md), "Pipeline lists").
- Pictures: `KYTY_DUMP_FRAMES=<n>` with `_EVERY` and `_FROM`; for a level start at present 1,300 or later and dump
  every 60-400 presents. Dumps are game data and stay under `NPS5_SCRATCH`.
- Statistics: `KYTY_CP_GAP_STATS=1 KYTY_CP_PASS_STATS=1` for where the frame goes and the pass sizes (which also
  shows the resolution the game settled on); `KYTY_VRAM_STATS` for memory by kind.
- Run classes, lengths and the checks before a measured run: [profiling](../../docs/reference/profiling.md).

## Found on this game

Mechanisms first seen here, written up once in the reference:
[the game's GPU clock decides its resolution](../../docs/reference/frame-rate.md),
[presents stepped in 16.7 ms units](../../docs/reference/frame-rate.md),
[CPU-bound levels](../../docs/reference/frame-rate.md),
[per-vertex fragment inputs](../../docs/reference/rendering.md),
[barriers](../../docs/reference/rendering.md),
[video pitch](../../docs/reference/rendering.md),
[whole mip chains](../../docs/reference/memory.md),
[alias double counting](../../docs/reference/memory.md),
[identity mapping is impossible](../../docs/reference/architecture.md),
[the shader findings](../../docs/reference/shaders.md),
[the loop cap and the guards](../../docs/reference/machine-safety.md).

## Open, ranked

1. CPU cost per draw in the back end (the 12-13 fps levels).
2. Barriers in the driver: built, gate passes, no frame-time number.
3. The first level's serial megakernel commit: knock-out, then register pressure or the non-tiled path.
4. MetalFX at a lower internal resolution: works (the `upscaling` setting), but 720p/900p show block artefacts
   because some passes keep a 1080p-derived size; find those sizes, then make 720p the default.
5. Memory headroom in levels: mip skip, retiring stale 4K targets.
6. The two picture issues above.
