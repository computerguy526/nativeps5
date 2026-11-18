# nativeps5

Play your own PlayStation 5 games natively on Apple silicon Macs. The game's x86-64 code is translated ahead of time into a native arm64 macOS program, linked against reimplemented system libraries and drawn through Vulkan on Metal.

It is not an emulator: there is no JIT and no interpreter. Nothing from any game is in this repository or in its component repositories, and the project is not affiliated with Sony Interactive Entertainment or any game publisher. "PlayStation" and "PS5" are trademarks of their owners.

[Status and limitations](#status-and-limitations) · [Games](#games) · [How it fits together](#how-it-fits-together) · [Requirements](#requirements) · [How to play](#how-to-play) · [For developers](#for-developers) · [Acknowledgements](#acknowledgements) · [License](#license)

## Status and limitations

- **Early preview.** One game, ASTRO BOT version 01.007, tested on one Mac (MacBook Pro, M4 Pro, 24 GB, macOS 26). Apple silicon only.
- **You need your own legally dumped copy** of the game; nothing from any game is here or in the components.
- **Speed (v1.0.3, MetalFX 720p default, MacBook Pro M4 Pro):** world map about 30 fps (25.6 fps in v1.0 at native 1080p), about 38 fps at the crash site after the landing (median of the game's own frame-rate readings over a playtest). v1.0 at native 1080p on the same Mac: `hoover_beach` about 20 fps, Sky Garden about 13-16 fps; per-scene v1.0.3 numbers will follow. The GPU back end is the limit and is being worked on. For native 1080p set `NPS5_ASTRO_UPSCALING=off` (or pick off in the settings dialog).
- **MetalFX 720p upscaling is the default:** the 3D scene renders at 1280x720 and Apple MetalFX (spatial) scales it up; menus and text stay sharp, and v1.0.3 fixes the blocky squares on dust, glass and particles (the light-tile grid now follows the render size). Spatial MetalFX has no anti-aliasing of its own, so edges look more jagged than at native 1080p. For native 1080p set `NPS5_ASTRO_UPSCALING=off`; for 900p set `NPS5_ASTRO_UPSCALING=metalfx900` (or use the settings dialog).
- **Shaders:** a cache prepare step builds the game's shaders before the first run; it can take several minutes and shows its progress in the terminal only. The first visit to a level that is not in the shipped shader list may still compile some shaders (short stutters).
- **Our own Metal shaders (v1.1):** 68 of ASTRO BOT's compute kernels run as Metal code translated directly from the PS5 shaders instead of going through KosmicKrisp's SPIR-V path. The kernels are generated on your Mac from your own dump during `nps5 cache prepare` (the repository holds only a list of hashes of the verified kernels) and only when they match the verified ones byte for byte; anything else keeps KosmicKrisp's kernel. Opt out with `NPS5_METAL_SHADERS=0`.
- **Saves:** fixed in v1.0.1: a save could crash the game on load (the save-data transfer mount opened the game's own save). Saves are still early-preview data: keep a backup of your save folder.
- **Crashes:** the world-map bus error (SIGBUS) that ended about 9 % of world-map runs during development (3 of about 35) is fixed: a file read into guest memory failed with EFAULT, and the read retry that fixes it has been in every release since v1.0.0 (0 of 50 title-to-world-map runs since; v1.0.2 extends it to `readv` and `fread`). Closing the window exits at once; since v1.0.3 it first writes the Vulkan driver pipeline cache (up to about 3 s).
- **Picture:** Astro's visor cracks can flicker now and then in the crash-landing close-up (v1.1 adds a buffer-snapshot hardening for it). PlayStation symbols look garbled in the warp from the world map into a level. The `hoover_beach` fluid read-fault storm is mitigated in the profile.
- **Controller:** DualSense, DualShock 4, Switch Pro and Xbox pads through SDL; DualSense haptics; motion controls need a pad with a gyro.
- **No online features and no trophies.**

Details and causes: [ASTRO BOT known issues](games/PPSA21564/README.md#known-issues).

## Games

Each game's note records the tested version and Mac, frame rates, settings and known issues.

| Game | Title ID | Version | Status |
|---|---|---|---|
| [ASTRO BOT](games/PPSA21564/README.md) | `PPSA21564` | 01.007 | Boots; the title screen, the world map and every level tried show a picture, and the controller works. v1.0.3 (MetalFX 720p default): about 30 fps on the world map and 38 fps at the crash site on an M4 Pro; v1.0 at native 1080p: 20-26 fps on the world map and early levels, 12-16 fps in the heaviest levels. Early preview: see [Status and limitations](#status-and-limitations). |
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

6. **Prepare the shader cache** from the pipeline list shipped in `games/PPSA21564/` (the list holds keys and hashes only, no game code). It compiles the game's listed pipelines once (1,596 in the list; 1,259 of 1,260 built in about 90 seconds on an M4 Pro for the earlier, smaller list), so the first visit to a scene does not stutter:

   ```sh
   scripts/nps5 cache prepare PPSA21564
   scripts/nps5 cache status PPSA21564    # says whether the caches match the current build
   ```

7. **Run.** Keep `ENV="NPS5_PERF=2"` in `titles/PPSA21564.env`. A binary you translated yourself needs `--patch dr`. A run without `--scene` starts the game normally. `nps5 run` is a test runner: a run is silent, capped at 90 seconds (180 with `NPS5_RUN_REASON=<why>`) and stopped early on a black or stalled picture. To play, add `--play`: sound on, no early stop, up to two hours:

   ```sh
   scripts/nps5 run PPSA21564 --patch dr --play
   ```

   `--scene worldmap` skips the intro; `--dry-run` prints the environment and command and starts nothing. The profile's canonical settings are in [games/PPSA21564/README.md](games/PPSA21564/README.md). It also lists the optional binary patches, including `nt`, which always takes the non-tiled lighting path and skips the tiled-lighting compute kernels; that changes the picture and has not been reviewed.
8. **Quit** by closing the window. This exits at once, without the renderer teardown; since v1.0.3 the Vulkan driver pipeline cache is written first (up to about 3 s), and the pipeline journals are written as they grow. Only one title runs at a time, and `nps5 run` refuses to start while another GPU process is running.

`nps5 run` sets a memory limit from your RAM, silences audio by default and scrubs the environment. These rules exist because breaking them has rebooted the development machine; read "Running safely" in [AGENTS.md](AGENTS.md) before doing GPU work of your own.

If a step fails for you, that is a bug in [BUILDING.md](BUILDING.md): open an issue with the command and its output.

**Troubleshooting.** `nps5 doctor` checks the machine. After a run that stopped, crashed or looks wrong, `nps5 log --last` shows the log without the noise and ends with a SUMMARY block (verdict, fps, compiles, stalls, crashes, settings), and `nps5 bug-report` writes one zip of redacted text (log, versions, commits, settings, crash report) to attach to an issue; `--dry-run` lists what would go in. It never includes game files. See [docs/reference/logging.md](docs/reference/logging.md).

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
