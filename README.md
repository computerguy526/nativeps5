# nativeps5

Play your own PlayStation 5 games natively on Apple silicon Macs. The game's x86-64 code is translated ahead of time into a native arm64 macOS program, linked against reimplemented system libraries and drawn through Vulkan on Metal.

It is not an emulator: there is no JIT and no interpreter. Nothing from any game is in this repository or in its component repositories, and the project is not affiliated with Sony Interactive Entertainment or any game publisher. "PlayStation" and "PS5" are trademarks of their owners.

[Status and limitations](#status-and-limitations) · [Games](#games) · [How it fits together](#how-it-fits-together) · [Requirements](#requirements) · [How to play](#how-to-play) · [For developers](#for-developers) · [Acknowledgements](#acknowledgements) · [License](#license)

## Status and limitations

- **Early preview.** One game fully supported, ASTRO BOT version 01.007, tested on one Mac (MacBook Pro, M4 Pro, 24 GB, macOS 26). Apple silicon only.
- **You need your own legally dumped copy** of the game; nothing from any game is here or in the components.
- **Speed (v1.0.3, MetalFX 720p default, MacBook Pro M4 Pro):** world map about 30 fps (25.6 fps in v1.0 at native 1080p), about 38 fps at the crash site after the landing (median of the game's own frame-rate readings over a playtest). v1.0.3 also: `hoover_beach` 27.0 fps, Sky Garden 14.6 fps (one run each). **v1.1:** Sky Garden about 17 fps (our Metal compute kernels about +11 %, the selective buffer-collector settle about +1 fps more). v1.0 at native 1080p on the same Mac: `hoover_beach` about 20 fps, Sky Garden about 13-16 fps. The GPU back end is the limit and is being worked on. For native 1080p set `NPS5_ASTRO_UPSCALING=off` (or pick off in the settings dialog).
- **MetalFX 720p upscaling is the default:** the 3D scene renders at 1280x720 and Apple MetalFX (spatial) scales it up; menus and text stay sharp, and v1.0.3 fixes the blocky squares on dust, glass and particles (the light-tile grid now follows the render size). Spatial MetalFX has no anti-aliasing of its own, so edges look more jagged than at native 1080p. For native 1080p set `NPS5_ASTRO_UPSCALING=off`; for 900p set `NPS5_ASTRO_UPSCALING=metalfx900` (or use the settings dialog).
- **Shaders:** a cache prepare step builds the game's shaders before the first run; it can take several minutes and shows its progress in the terminal only. The first visit to a level that is not in the shipped shader list may still compile some shaders (short stutters).
- **Our own Metal shaders (v1.1):** 68 of ASTRO BOT's compute kernels run as Metal code translated directly from the PS5 shaders instead of going through KosmicKrisp's SPIR-V path. The kernels are generated on your Mac from your own dump during `nps5 cache prepare` (the repository holds only a list of hashes of the verified kernels) and only when they match the verified ones byte for byte; anything else keeps KosmicKrisp's kernel. Opt out with `NPS5_METAL_SHADERS=0`. Since v1.1.1 also 53 verified graphics (vertex+fragment) pairs, generated and checked the same way. Fast math stays off.
- **Buffer collector (v1.1):** the selective settle (`KYTY_GC_SETTLE_SELECTIVE`) is on by default; `KYTY_GC_SETTLE_SELECTIVE=0` restores the v1.0.3 behaviour.
- **Saves:** fixed in v1.0.1: a save could crash the game on load (the save-data transfer mount opened the game's own save). Saves are still early-preview data: keep a backup of your save folder.
- **v1.4.0 caveats:** ASTRO BOT: entering "Apes on the Loose" right after other levels can end the game with exit code 86 (the memory self-limit) on 24 GB Macs: restart and enter the level from the world map (a fix is in progress). The first start after installing or updating can crash once at the end of the loading card (an Apple Metal bug hit when MetalFX starts during the shader-archive save): start the game again (fixed in the next version). The first start after updating rebuilds the shader cache behind the loading card (several minutes on ASTRO BOT). Catching the ape very quickly at the end of "Apes on the Loose" can stop the level from finishing: let it run for a moment first.
- **Crashes:** the world-map bus error (SIGBUS) that ended about 9 % of world-map runs during development (3 of about 35) is fixed: a file read into guest memory failed with EFAULT, and the read retry that fixes it has been in every release since v1.0.0 (0 of 50 title-to-world-map runs since; v1.0.2 extends it to `readv` and `fread`). Closing the window exits at once; since v1.0.3 it first writes the Vulkan driver pipeline cache (up to about 3 s).
- **Progress:** in Sky Garden the flower-pod switch does not pop up and the pull bar is already out, so that puzzle cannot be finished. It also happens without our Metal kernels; under investigation.
- **Picture:** Astro's visor cracks could flicker now and then in the crash-landing close-up (v1.1 adds a buffer snapshot for it; clean in the v1.1 checks). PlayStation symbols look garbled in the warp from the world map into a level. The `hoover_beach` fluid read-fault storm is mitigated in the profile.
- **Controller:** DualSense, DualShock 4, Switch Pro and Xbox pads through SDL; DualSense haptics; motion controls need a pad with a gyro.
- **No online features and no trophies.**

Details and causes: [ASTRO BOT known issues](games/PPSA21564/README.md#known-issues).

## Games

Each game's note records the tested version and Mac, frame rates, settings and known issues.

| Game | Title ID | Version | Status |
|---|---|---|---|
| [ASTRO BOT](games/PPSA21564/README.md) | `PPSA21564` | 01.007 | Boots; the title screen, the world map and every level tried show a picture, and the controller works. v1.0.3 (MetalFX 720p default): about 30 fps on the world map and 38 fps at the crash site on an M4 Pro; v1.0 at native 1080p: 20-26 fps on the world map and early levels, 12-16 fps in the heaviest levels. Early preview: see [Status and limitations](#status-and-limitations). |
| God of War Sons of Sparta | `PPSA28997` | 01.008 | Bring-up (Unity IL2CPP player, `nativeps5-recomp` v1.2.4, Kyty back end): boots, plays the intro (press Cross to skip it), the title menu, a new game and the first level (the Cyclops fight) in 5-minute scripted runs on an M5 Pro. Audio and a real-controller session are not verified. Experimental; no game profile folder yet. See the v1.2.4 entry of [CHANGELOG.md](CHANGELOG.md). |
| [SAROS](games/PPSA07631/README.md) | `PPSA07631` | 01.005.002 | **Not playable on v1.3.x** (Unreal Engine 5.5 line, Kyty back end). It boots, but stalls at about 1 fps with the hand shaders on and misses the background with them off. v1.4 verification run pending (no claim yet). Notes and the bring-up settings: its note. |
| [Cronos: The New Dawn](games/PPSA24096/README.md) | `PPSA24096` | 01.010.000 | **Bring-up only, not supported.** Boots to the menu and into the game with a warm cache; no gameplay frame rate measured, no profile yet. See its note. |
| [A Game About Digging A Hole](games/PPSA30552/README.md) | `PPSA30552` | not recorded | Second bring-up target, on the AnyPS5 back end; boots, and the menu, intro cutscene and gameplay run. Not finished; see its note. |

Frame rates are below the console's in most scenes. A 16 GB Mac will not run an ASTRO BOT level. [games/README.md](games/README.md) explains the per-game profiles.

## How it fits together

One clone of this repository is all you fetch by hand. `scripts/fetch-components.sh` then clones the three component repositories at the commits pinned in [components.lock](components.lock); you never clone them yourself.

| Component | What it does | Based on | Licence |
|---|---|---|---|
| `ps5recomp` ([nativeps5-recomp](https://github.com/computerguy526/nativeps5-recomp)) | the x86-64 to arm64 translator, the runtime and the packager (`nps5 build`) | PS4Recomp | GPL-2.0 |
| `kyty` ([nativeps5-kyty](https://github.com/computerguy526/nativeps5-kyty)) | the GPU back end, KytyPS5's renderer built as `kyty_gpu.dylib` for arm64 macOS | KytyPS5, with the AstroHybrid `kyty-hybrid` series | GPL-2.0 |
| `anyps5` ([nativeps5-anyps5](https://github.com/computerguy526/nativeps5-anyps5)) | the PS5 system libraries (kernel, libc, pad, audio and about 1,300 other exports) | AnyPS5 | GPL-2.0 |
| `kosmickrisp/` (in this repository) | a patch series on Mesa's KosmicKrisp, the Vulkan-on-Metal driver; built from a pinned Mesa commit | Mesa | MIT |

## Requirements

- An Apple silicon Mac with macOS 26 or later (Metal 4). Verified on a MacBook Pro with an M4 Pro and 24 GB of RAM, macOS 26.6. Macs with 24 GB or more should work; 16 GB is experimental; M1 and M2 are untested ([hardware](docs/reference/hardware.md)).
- Xcode 26 or newer, or at least the Command Line Tools; the shader tools need Xcode's `metal` compiler (`xcodebuild -downloadComponent MetalToolchain`).
- [Homebrew](https://brew.sh) with the packages listed in [BUILDING.md](BUILDING.md) (git, go, cmake, ninja, meson, llvm, python and others), and an arm64 Python 3 for the Mesa build.
- About 7 GB of free disk for the sources and build trees, and tens of GB more for one title's translated code and caches, on a disk outside iCloud-synced folders.
- **Your own, legally dumped copy of a game you own**, decrypted and extracted as it sits on the console. No game data is distributed here and none is accepted. How you dump your console is outside this project. Keep the dump read-only; nothing here writes to it.

## How to play

From a fresh Mac to a running game. The steps are CPU only until the last one. The full commands, times and what was verified are in [BUILDING.md](BUILDING.md); this is the short path for ASTRO BOT.

1. **Install the tools** (once): Xcode or the Command Line Tools, Homebrew, and the `brew install` line from [BUILDING.md](BUILDING.md), step 0.
2. **Get the project.** One clone, then one script that fetches and pins the three components:

   ```sh
   mkdir nativeps5-ws && cd nativeps5-ws
   git clone https://github.com/computerguy526/nativeps5 && cd nativeps5
   cp nps5.conf.example nps5.conf       # set NPS5_DUMPS_DIR to the folder that holds your dump
   scripts/fetch-components.sh          # clones ps5recomp, kyty and anyps5 next to this checkout
   ```

3. **Check the Mac.** After building the translator (next step), `scripts/nps5 doctor` lists what the toolkit still misses; `python3 scripts/nps5lib/hw.py doctor` prints this Mac's class and limits.
4. **Build the components** (about 40 minutes in total on the tested Mac):

   ```sh
   . scripts/nps5-env.sh                # in every new shell
   scripts/build.sh recomp
   scripts/build.sh kosmickrisp
   scripts/build.sh anyps5
   scripts/build.sh kyty
   ```

5. **Translate your dump** (about 11 minutes and several GB of scratch for ASTRO BOT):

   ```sh
   ../ps5recomp/bin/nps5 build "$NPS5_DUMPS_DIR/<your dump folder>" -o "$NPS5_SCRATCH/apps" -j 4
   cp titles/example.env titles/PPSA21564.env      # set BIN (the `native` program nps5 build printed), APP0 (your dump), GPU=kyty
   ```

6. **Prepare the shader cache** from the pipeline list shipped in `games/PPSA21564/` (the list holds keys and hashes only, no game code). It compiles the game's listed pipelines once (2,367 in the list; 1,259 of 1,260 built in about 90 seconds on an M4 Pro for the earlier, smaller list), so the first visit to a scene does not stutter:

   ```sh
   scripts/nps5 cache prepare PPSA21564
   scripts/nps5 cache status PPSA21564    # says whether the caches match the current build
   ```

7. **Run.** Keep `ENV="NPS5_PERF=2"` in `titles/PPSA21564.env`. A binary you translated yourself needs `--patch dr`. A run without `--scene` starts the game normally. `nps5 run` is a test runner: a run is silent, capped at 90 seconds (180 with `NPS5_RUN_REASON=<why>`) and stopped early on a black or stalled picture (a stall limit is 45 s, 150 s while an `--until scene:` target is pending; `--until ...,stall:N,black:N` overrides). To play, add `--play`: sound on, no early stop, up to two hours:

   ```sh
   scripts/nps5 run PPSA21564 --patch dr --play
   ```

   `--scene worldmap` skips the intro; `--dry-run` prints the environment and command and starts nothing. The profile's canonical settings are in [games/PPSA21564/README.md](games/PPSA21564/README.md). It also lists the optional binary patches, including `nt`, which always takes the non-tiled lighting path and skips the tiled-lighting compute kernels; that changes the picture and has not been reviewed.
8. **Quit** by closing the window. This exits at once, without the renderer teardown; since v1.0.3 the Vulkan driver pipeline cache is written first (up to about 3 s), and the pipeline journals are written as they grow. Only one title runs at a time, and `nps5 run` refuses to start while another GPU process is running.

### Save disk space: keep the game folder compressed

An extracted game is large (ASTRO BOT: about 160 GB) and mostly textures and models, which compress well. On an APFS
disk macOS can store files compressed and still hand every program the original bytes, so nothing else changes:

```sh
scripts/nps5 data compress "<your game folder>"            # dry run: what would be done, an estimate, free space
scripts/nps5 data compress "<your game folder>" --apply    # do it (safe to stop and run again)
scripts/nps5 data status "<your game folder>"              # what it saved
scripts/nps5 data decompress "<your game folder>" --apply  # undo
```

On the development machine 32 GB of ASTRO BOT data (22,000 files) went to 9.6 GB in about six minutes on two
cores, and reading compressed data costs about one CPU-second per GB. The folder must be on APFS (a Mac's internal disk, or an external disk formatted as APFS); exFAT
and network disks are refused with a message. Each file is written to a temporary file, read back and compared by
SHA-256, and only then renamed over the original, so a crash or a power cut leaves every file either as it was or
complete. Symbolic links, hard-linked files, tiny files and files that do not shrink are left alone. It changes how
your copy is stored, never its content (permissions, dates, extended attributes and flags are kept). Name the game
folder itself: a whole disk, your home folder, ~/Library and system folders are refused, because other programs write
to files there. Files written in the last minute (a fresh copy, a decompress) are left for a second run a
minute later: the command says NOT DONE YET and exits with code 3 (0 = done, 1 = some files failed and are unchanged,
2 = the folder cannot be used). Details: `scripts/nps5 data --help`.

`nps5 run` sets a memory limit from your RAM, silences audio by default and scrubs the environment. These rules exist because breaking them has rebooted the development machine; read "Running safely" in [AGENTS.md](AGENTS.md) before doing GPU work of your own.

If a step fails for you, that is a bug in [BUILDING.md](BUILDING.md): open an issue with the command and its output.

**Troubleshooting.** `nps5 doctor` checks the machine. After a run that stopped, crashed or looks wrong, `nps5 log --last` shows the log without the noise and ends with a SUMMARY block (verdict, fps, compiles, stalls, crashes, settings), and `nps5 bug-report` writes one zip of redacted text (log, versions, commits, settings, crash report) to attach to an issue; `--dry-run` lists what would go in. It never includes game files. See [docs/reference/logging.md](docs/reference/logging.md). For a stall, a slow run or a run ended by a guard, also turn on the flight recorder (below).

### Opt-in (v1.2.1, off by default)

- **ProMotion frame cap** (`KYTY_FPS_AUTOCAP=promotion`, with the default `KYTY_FPS_LIMIT_MILLI=auto`): on an adaptive
  (ProMotion) screen the cap becomes the highest 120/n (60, 40, 30, 24 fps) the scene holds, so frames land on whole
  refreshes. Without it, adaptive screens stay uncapped as before.
- **Fullscreen on a chosen display** (`NPS5_WINDOW_SCREEN=builtin|main|<index>` together with fullscreen): the window opens
  on that display and goes fullscreen there. Without the variable, fullscreen opens as before.

### Flight recorder (off by default)

`KYTY_FLIGHTREC=1` turns on a small recorder in the Kyty back end (about 13 MB of memory, a ring write per event) that keeps the
last seconds of submissions, compile and file events (files as hashes), memory samples and counters. It writes a dump when
something goes wrong (GPU watchdog, a frame or present stall over 2 s, the memory limit, a crash, SIGTERM) and at exit. A dump holds
ids, sizes, hashes and timings, no game bytes and no paths. `nps5 run` puts it in `<run dir>/flightrec` as `<tag>.<n>.<why>.flightrec`
(otherwise `./flightrec`); the run logs are in the same run folder (`run.err`, `run.summary.txt`). Attach the `.flightrec` file and
the `nps5 bug-report` zip to your issue, or send them to us; we decode them ourselves. `KYTY_MEMLOG=1` adds a once-a-second memory line.

### Game speed (v1.2, ps5recomp)

ASTRO BOT advances its game time by a fixed step per frame, read from one block that ps5recomp rewrites from the measured
frame rate (`framerate_block` in `games/PPSA21564/game.tsv`). Up to v1.1.2 the estimate was an average of per-frame rates,
which reads too high when frames arrive unevenly (pairs of fast frames, then a long one), and a load freeze could hold it
at 60 fps for 30-50 s while a level streamed in: the game then ran at about half speed (Sky Garden: 47-54 % of real time,
30-33 % in the first minute). v1.2 averages the frame period instead and caps the load freeze at 8 s
(`NPS5_FRAMERATE_LOAD_MAX_MS`, `0` = no cap, the old behaviour): 98 % of real time (92 % in the first minute) on the
same Sky Garden route. The `[framerate]` line every 5 s shows the estimate and why it is frozen
(`frozen -|video|phase|vfs[ streaming]`).

### Options on by default (v1.1.2 - v1.2.4)

The ASTRO BOT profile (`games/PPSA21564/game.tsv`) turns these on. Each has an off switch: pass it with
`nps5 run --env NAME=VALUE` or put it on the `ENV=` line of `titles/<id>.env` (both win over the profile).
- **Physics steps that fit a slower frame rate** (v1.2.1, ps5recomp `[physsub]`, needs `nps5 build` again): the game steps
  its physics once per frame with the frame time, which below 30 fps is three times the step it was tuned for. ps5recomp
  now splits the Havok world step into steps of at most 1/60 s (at most 4 per frame), clamps the cloth step to 1/60 s,
  and splits the rope-bridge spring steps to at most 1/600 s (at most 8 per call). This fixes the crumpled pool membrane
  in Sky Garden, the crash at the "feel the wall" gate (Havok particles) and the missing rope bridge after it. The
  functions are named by address and sha256 in `profiles/funcrepl/PPSA21564-physsub.json` of nativeps5-recomp: another
  build of the game is refused by `nps5 build`. Off (no rebuild needed): `NPS5_PHYS_SUBSTEP=0` (world + cloth),
  `NPS5_PHYS_SUBSTEP_WORLD=0`, `NPS5_PHYS_SUBSTEP_CLOTH=off`, `NPS5_PHYS_SPRING=0`; `NPS5_PHYS_SUBSTEP_LOG=1` and
  `NPS5_PHYS_SPRING_LOG=1` print `[physsub]` lines.
  v1.2.2: after a split world step the frame's real dt is written back, which stops the leaves flickering (off: `NPS5_PHYS_SUBSTEP_DTFIX=0`; needs `nps5 build` again).
- **Automatic render scale** (v1.2.2, ps5recomp `render_scale` setting + `[rscale]` controller, needs `nps5 build` again): the 3D
  scene renders at 720p and drops to 540p while the frame time stays over a budget of 33 ms (30 fps), then returns to 720p when
  there is headroom (hysteresis, at least 20 s between switches). It uses the game's own dynamic-resolution pipeline sets, so a
  switch is seamless; MetalFX upscales either size and menus and text stay sharp. On a MacBook Pro M4 Pro, Sky Garden runs at
  540p (about 6 % shorter frames, a softer picture); a Mac whose GPU holds 30 fps at 720p stays at 720p. `NPS5_RENDER=720`
  (or `patch.render_scale=p720`) = 720p always, as in v1.2.1; `NPS5_RENDER=540` = 540p always; `NPS5_RENDER_LEVELS=
  <level>=auto|720|540,...,*=<mode>` per level; `NPS5_RENDER_TARGET_MS=<ms>` another budget. One `[rscale]` line per switch.
- **Four Kyty options** (v1.2.2, set by the ASTRO BOT profile; off: `--env NAME=0`; exact in our checks, no frame-time change measured):
  `KYTY_SUBRES_CONTAINS` and `KYTY_DEPTH_GROW_RECREATE` (upstream KytyPS5 #2410 image subresources compared by containment, #2417
  depth targets narrower than the request are recreated); `KYTY_ALIAS_REBUILD_PRESERVE_TIGHT` (a partial render-target rebuild
  moves only the blocks it owns; `=verify` compares both moves on the GPU); `KYTY_COMPUTE_IMAGE_COPY` (a guest compute pass proven
  to copy a whole surface becomes a native image copy; `verify-kernel` / `verify` compare against the guest's own result).
- **Wet Astro without a yellow/red tint or stripes** (v1.2.3, `KYTY_GBCOPY_REMAP=auto`): the game copies its G-buffer into an
  image allocated at the native render size (1920x1080, or 2432x1368 for the second dynamic-resolution level), while Upscaling
  720p/900p and the 540p render scale make the G-buffer smaller; the wet decals then read the copy at the wrong place. Kyty now
  gives that copy the scaled size, taken from the translated program's own Upscaling / Render scale settings (one `[decalfix]`
  line per size at start). A stopgap until the copy's size is patched in the game profile. Off: `KYTY_GBCOPY_REMAP=0`.
- **Stuck shader build and close guards** (v1.2.3): a draw waits at most 8 s for a pipeline build that never returns from the
  driver, then is skipped until the build finishes (`KYTY_PIPELINE_TAKE_TIMEOUT_MS=8000`), and closing the window exits
  within 5 s even if the pipeline cache save is stuck (`KYTY_CLOSE_SAVE_TIMEOUT_MS=5000`). `0` = wait for ever (the old
  behaviour). With the flight recorder on, its dumps also backtrace every thread (`KYTY_FLIGHTREC_ALLTHREADS=1`).
- **Write faults without a GPU wait** (v1.2.3, `KYTY_FAULT_HOST_READBACK=1`): when the CPU writes to bytes the GPU produced and
  every GPU writer of them has finished, Kyty copies them on the host instead of waiting for the whole GPU backlog. Fewer CPU
  stalls on these faults; no frame-rate change was measured. Off: `KYTY_FAULT_HOST_READBACK=0`.
- **Banner text layout** (v1.2.1, ps5recomp libSceFont): `sceFontGetHorizontalLayout` returns the face bounding box, as the
  PS5 font driver does, so banners such as BOT RESCUED no longer lose the tops of their capitals. Old values:
  `NPS5_FONT_LAYOUT=hhea`.
- **Command-processor lead bound** (v1.2.1, `KYTY_CP_LEAD_MAX_MS=16`): Kyty's command processor runs at most 16 ms ahead
  of the GPU (the wait is bounded and never fatal). Off: `KYTY_CP_LEAD_MAX_MS=0`.
- **GPU threads at real-time priority** (v1.4, `NPS5_QOS_GPU=rt`, ASTRO BOT only): the threads that feed the GPU run at a real-time priority class.
  Measured on an M5 Pro in Sky Garden (earlier v1.4 development libraries): mean frame time 41.7 / 38.9 / 41.1 ms in an A/B/A (-6.1 %), game speed 98-99 %.
  Side effect: the guest's fault handling per frame rose from about 5 to about 25 ms (the frame time is still lower). **Release-candidate re-measurement: PENDING.**
  Off: `--env NPS5_QOS_GPU=off`.
- **Depth compression kept** (v1.4, `KYTY_DEPTH_FEEDBACK_USAGE=0`, ASTRO BOT on Kyty): depth images are created without the attachment feedback-loop usage, so KosmicKrisp keeps Metal's lossless depth compression
  (about -2 ms GPU per frame in the rpass A/B/A on an M4 Pro; **not yet measured on the shipped composition on an M5**). Off: `--env KYTY_DEPTH_FEEDBACK_USAGE=1`.
- **Metal shaders follow what you play** (v1.4, `NPS5_METAL_REFRESH=1`): our Metal kernels and pairs are generated from the programs your own cache has translated. A
  fresh `nps5 cache prepare` knows only the programs of the shipped list (on an M5 Pro: 30 of 70 kernels and 67 of 218 pairs); programs compiled while you play join `programs.bin`, and when it has grown
  `nps5 run` regenerates the Metal rows after the title has exited (CPU only, bounded to 5 minutes, one line in the run output). Off: `NPS5_METAL_REFRESH=0`; by hand: `nps5 cache metal PPSA21564`.
- **Faster Metal shaders per GPU class** (v1.2.1 - v1.2.4): the Metal lists carry rows for M4-family (`g16`, 130 graphics rows
  incl. 5 mesh pipelines + 2 kernels), M5-family (`g17`, 46 incl. 2 mesh) and M6-family (`g18`, 125 incl. 2 mesh + 2 kernels) GPUs,
  used only on that class; other Macs keep the shared rows (32 graphics pairs + 69 kernels). Mesh rows (v1.2.3) need KosmicKrisp
  patches 0029/0030; `nps5` sets `KK_MSL_OVERRIDE_MESH=1` only when a mesh row was generated (`NPS5_MESH_ROWS=0` = skip them). Since v1.2.2 the rows match on every macOS and GPU (canonical KosmicKrisp keys, KosmicKrisp patch 0027;
  `NPS5_METAL_CANON=0` = off). `NPS5_METAL_SHADERS=0` turns all of our shaders off.
- **Ray-tracing load hold and budget guard** (`KYTY_RT_LOAD_HOLD=1`, `KYTY_RT_BUDGET_GUARD=1`): Kyty's software ray tracing
  waits while a level streams in (5 s after a level opens, and while the game reads files fast), and skips its dispatches
  for a moment when they run out of their BVH node budget (a BVH that is not valid yet). This removes the long GPU stalls (and the GPU watchdog stops they could cause) at level entry.
  The lighting it feeds catches up a moment later. Off: `KYTY_RT_LOAD_HOLD=0`, `KYTY_RT_BUDGET_GUARD=0`
  (`KYTY_RT_LOAD_HOLD_PHASE=0` keeps only the file-read trigger).
- **Quiet cache saves during level loads** (`KYTY_SAVE_LOAD_QUIET_S=20`): the periodic pipeline/program cache saves wait
  until 20 s after a level load. KosmicKrisp's own pipeline archive is written at exit instead of a few seconds into a
  level (`KK_PIPELINE_ARCHIVE_FLUSH_N=4096`, `KK_PIPELINE_ARCHIVE_FLUSH_S=1800`). **Caveat:** if the game crashes, the
  pipelines it compiled in that session are not in the archive and compile again next time (the Metal library cache and
  Kyty's caches are still saved). Off: `KYTY_SAVE_LOAD_QUIET_S=0`, and `KK_PIPELINE_ARCHIVE_FLUSH_N=32
  KK_PIPELINE_ARCHIVE_FLUSH_S=10` for the old archive timing. `nps5 cache prepare` always uses the old timing.
- **Display-aware frame cap** (`KYTY_FPS_LIMIT_MILLI=auto`): on a fixed-refresh display of R Hz the game is capped at R/n
  (n >= 2, picked from the frame rate it holds, e.g. 37.5 fps on a 75 Hz monitor), never below 30 fps: a game that cannot
  hold R/n >= 30 runs uncapped (`KYTY_FPS_AUTOCAP_MIN` sets that floor). On an adaptive display (ProMotion) there is no cap. Frames are still presented as soon as they are ready (no vsync queue). The choice is logged as
  `[autocap] screen <name> <R> Hz fixed|adaptive -> cap <x> fps`. A number (`KYTY_FPS_LIMIT_MILLI=30`) sets a fixed cap;
  `KYTY_FPS_LIMIT_MILLI=0` turns the cap off.
- **Learned fault classes** (`KYTY_FAULT_CLASS=1`, `KYTY_CLEAN_PAGE=1`): guest pages that the CPU reads every frame from
  GPU output stay readable (the read sees bytes up to a few frames old, as served reads already did), and a store to a
  page whose GPU bytes are already published no longer waits for the GPU. In Sky Garden the fault handler time fell from
  about 57 to 21 ms per frame. Off: `KYTY_FAULT_CLASS=0 KYTY_CLEAN_PAGE=0`.
- **GPU accuracy low** (`KYTY_GPU_ACCURACY=low`): fast math for pixel and compute shaders, except the ones listed in
  `games/PPSA21564/fast-math-deny.txt` (guest shader hashes only): those are the shaders our own Metal translations replace,
  so they keep exact math and their verified texts. About 1 ms per frame faster; a very slight flicker on a few rocks up
  close was seen in play. `nps5 cache prepare` builds this tier, so the first start is warm. Exact math everywhere:
  `KYTY_GPU_ACCURACY=high`; put it on the `ENV=` line of `titles/<id>.env` and run `nps5 cache prepare` again, which then
  prepares that tier (with `--env` alone its pipelines compile on first use). `normal` is an in-between tier (fast math outside an Inf/NaN-sensitive set).

- **Less memory** (v1.1.2, every Mac): `KYTY_TEX_MIP_SKIP=2` (large textures start two mip levels down; it needs
  `KYTY_TEXTURE_RESIDENT_TRIM=1`, which the profile sets) and `KYTY_BDA_PAGETABLE_MB=64` (a smaller GPU address table).
  Without them Sky Garden peaked at 17.9-19.1 GB on a 24 GB Mac, at the 18.9 GB memory limit `nps5 run` sets. Off:
  `KYTY_TEX_MIP_SKIP=0` (full texture detail), `KYTY_BDA_PAGETABLE_MB=0` (the whole table).

- **Partial render-target rebuild** (v1.2, `KYTY_ALIAS_REBUILD_PARTIAL=1`): when the game writes into part of a render
  target that another image owns, Kyty now rebuilds only the blocks the target does not own (and skips blocks whose
  bytes already match), instead of tiling the whole target out and back. In Sky Garden the tiler traffic fell from
  about 2.4 to 1.8-2.0 GB/s; frame times were the same or slightly better (n=3 A/B), no picture differences. Off:
  `KYTY_ALIAS_REBUILD_PARTIAL=0`.

The flight recorder (below) and the rest of memory trim (`KYTY_MEMTRIM_BIG_WIDTH`) stay off by default.

### Memory and GPU options (off by default, v1.1.1)

Opt-in Kyty switches for Macs short on memory; pass them with `nps5 run --env NAME=VALUE` or the `ENV=` line of
`titles/<id>.env`. Each is off unless you set it.
- `KYTY_MEMTRIM_BIG_WIDTH=2400` (v1.1.2: `KYTY_BDA_PAGETABLE_MB=64` is now a default): the large off-resolution
  render targets get their own Metal heap so their memory goes back to the system when they are retired.
- `KYTY_TEX_MIP_SKIP=1` or `2` (needs `KYTY_TEXTURE_RESIDENT_TRIM=1`, which the ASTRO BOT profile sets): drops the
  finest mip level(s) of large textures; in Sky Garden texture memory fell from about 1.9 GB to 0.2 GB with `2`, at the
  cost of texture detail. The ASTRO BOT profile uses `2` on every Mac since v1.1.2.
- `KYTY_DEPTH_FEEDBACK_USAGE=0`: lets KosmicKrisp keep Metal's lossless depth compression (about 2 ms per frame faster in
  ASTRO BOT).

## Save disk space: pack a title

Big game containers can take tens of GB. `scripts/gamepack.py` shrinks them with macOS's transparent (APFS) compression, which cannot handle a single file over 2 GiB, so it stores each big file `<name>` as a folder `<name>.nps5seg/` of 1 GiB compressed pieces plus a `manifest.json`. **No setting is needed to play a packed title:** when a game opens a file that is missing but has a valid `<name>.nps5seg/` next to it, the runtime serves it as the original single file (same size, same bytes). A plain file always wins; `NPS5_VFS_SEGFILES=0` switches the feature off. If the manifest does not match its pieces, the runtime logs it once and treats the file as missing.

```sh
python3 scripts/gamepack.py pack --dry-run <title folder>   # what would be packed and what it saves; changes nothing
python3 scripts/gamepack.py pack <title folder>             # pack (quit the game first)
python3 scripts/gamepack.py verify <title folder>           # re-check every piece against the manifest
python3 scripts/gamepack.py unpack <title folder>           # get the original files back, bit for bit
```

Safe by default: `pack` checks free space, byte-compares the packed pieces against the original, and deletes the original only after that passes (`--keep` leaves it in place). It refuses to run while a game is running or the file is open. `python3 scripts/gamepack.py --help` lists every option.

## For developers

- [BUILDING.md](BUILDING.md): the build, with the table of what was verified from fresh clones.
- [AGENTS.md](AGENTS.md): rules for running things without taking the machine down; for people and coding agents.
- [CONTRIBUTING.md](CONTRIBUTING.md): how a change gets in, what evidence a PR needs, and the rule that no game data is accepted.
- [docs/README.md](docs/README.md): the map of the documentation; [docs/reference/](docs/reference/README.md) covers architecture, frame rate, rendering, shaders, memory, machine safety and profiling. [docs/learnings.md](docs/learnings.md) and [docs/playbook.md](docs/playbook.md) record what was tried, what worked and what failed.
- Per-game profiles, pipeline lists and benchmark routes: [games/](games/README.md). Commands: `nps5 doctor | build | patch | run | log | bug-report | cache | bench | regress | analyze | report`.
- Before every commit run `scripts/check.sh` (game data, secrets, personal paths); `scripts/install-hooks.sh` makes it a pre-commit hook.

**AI assistance.** Pull requests written by hand or with a coding agent are welcome on the same terms. Fill in the "AI assistance" line of the pull request template for every PR, name the agent if you used one, and confirm that a person read the diff and the evidence. A person decides what gets in. Details in [CONTRIBUTING.md](CONTRIBUTING.md).

Questions and bugs: open an issue or a discussion on this repository.

## Share your shader list

> **No game content is ever shared.** A shader list holds *references* only: hashes that identify a shader and the pipeline state it was drawn with. It contains no shader code, no game files, no textures or audio, and nothing that can rebuild any part of the game. Every list is checked against your own copy of the game to prove that no 64-byte piece of its code is inside, before you can send it.

Every first visit to a scene compiles pipelines and stutters; the shipped list (`games/<title id>/pipelines.kyplst`) lets `nps5 cache prepare` build them before you play. It only knows the scenes somebody has visited, so **your play helps everyone**: the pipelines you hit that the list lacks become the next version's list, and the next player's first visit does not stutter.

```sh
scripts/nps5 cache share PPSA21564        # after you played with nps5 run; add --pr to open the pull request for you
```

It reads the pipeline journals your runs left in the scratch folder, compares them with the shipped list for **your game version**, says how many shaders (vertex / pixel / compute), permutations and pipelines are new, and writes your list (references only) to `games/PPSA21564/contrib/<date>-<hash>.kyplst`. If nothing is new it says thanks and writes nothing. Then it prints the exact steps (fork, branch, `gh pr create`, or "attach the two files to a **Shader list** issue" if you do not use git).

**Privacy.** The file holds keys, hashes and pipeline state only: no shader code, no game files, no user name, paths or machine serial (the header has the title, the app version, the nps5 commit and the machine class such as `m4pro-24g`). `nps5 cache share` proves that against your own copy of the game (`.verified` record) and runs the repository's list rules before it offers the file. Lists are per game version; a different version of the game gets its own list. Details: [games/README.md](games/README.md#share-your-list).

## Acknowledgements

This project is built on other people's work ([NOTICE](NOTICE) has the licence texts and the full list; each component repository carries its own):

- **[PS4Recomp](https://github.com/wie-project/PS4Recomp)** (GPL-2.0) is the static recompiler this project grew from: the x86-64 to native translator and the runtime that runs the result. `nativeps5-recomp` is a fork of it, and its history is kept.
- **[KytyPS5](https://github.com/Jetsku/KytyPS5)** (GPL-2.0; Kyty's original code by InoriRus is MIT) provides the PS5 GPU renderer and shader recompiler. `nativeps5-kyty` builds it as `kyty_gpu.dylib` for arm64 macOS.
- **[AstroHybrid](https://github.com/imnotsharingmyname/pcportastrobot)**, folder `Source/kyty-hybrid`: a series of 36 patches on KytyPS5 (the GPU bridge that lets Kyty's renderer run inside a natively translated program, and the tuning preset). The series is a derivative of GPL-2.0 KytyPS5 and `nativeps5-kyty` carries it with its authorship. Running Kyty's renderer inside a native program is that author's idea. The rest of that repository has no licence, so nothing else was copied; it was read for ideas only.
- **[AnyPS5](https://github.com/boykopovar/AnyPS5)** (GPL-2.0) provides the PS5 system libraries (libkernel, libc, pad, audio and about 1,300 other exports) and the AGC graphics driver. `nativeps5-anyps5` builds the arm64 macOS host side.
- **[Mesa](https://gitlab.freedesktop.org/mesa/mesa) and KosmicKrisp** (MIT) are the Vulkan-on-Metal driver every picture goes through. This repository carries a patch series on a pinned Mesa commit (pipeline archive, mesh shaders, shader trims) and no Mesa source.
- **Libraries the Kyty and AnyPS5 builds pull in as submodules**, each under its own licence: [SDL3](https://github.com/libsdl-org/SDL), [SPIRV-Tools](https://github.com/KhronosGroup/SPIRV-Tools), [SPIRV-Headers](https://github.com/KhronosGroup/SPIRV-Headers), [Vulkan-Headers](https://github.com/KhronosGroup/Vulkan-Headers), [glslang](https://github.com/KhronosGroup/glslang) (AnyPS5 build), [VulkanMemoryAllocator](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator), [xxHash](https://github.com/Cyan4973/xxHash), [zstd](https://github.com/facebook/zstd), [FreeType](https://github.com/freetype/freetype), [Dear ImGui](https://github.com/ocornut/imgui), [fmt](https://github.com/fmtlib/fmt) and [spdlog](https://github.com/gabime/spdlog), [LibAtrac9](https://github.com/shadps4-emu/ext-LibAtrac9) and others listed in the `.gitmodules` of each repository. The build tools (Go, CMake, Meson, Python with mako, pyyaml and packaging) are used, not distributed.
- **[NXNative](https://github.com/seedds/nxnative)** (MIT): read as a reference for running console games natively on Apple platforms. A few conventions were reused with attribution (see [NOTICE](NOTICE)); the contribution rules and this README follow the shape of its.

Thanks also to the projects whose research this work leans on and that are credited in [NOTICE](NOTICE): FEX-Emu, RPCS3, ARMSX2/3 and shadPS4.

## License

GPL-2.0-only ([LICENSE](LICENSE)) for this repository and for the forks, which keep the licence of their upstream. The KosmicKrisp patches in `kosmickrisp/` are MIT, as Mesa. Third-party code keeps its own licence.
