# nativeps5

Play your own PlayStation 5 games natively on Apple silicon Macs. The game's x86-64 code is translated ahead of time into a native arm64 macOS program, linked against reimplemented system libraries and drawn through Vulkan on Metal.

It is not an emulator: there is no JIT and no interpreter. Nothing from any game is in this repository or in its component repositories, and the project is not affiliated with Sony Interactive Entertainment or any game publisher. "PlayStation" and "PS5" are trademarks of their owners.

[Games](#games) · [How it fits together](#how-it-fits-together) · [Requirements](#requirements) · [How to play](#how-to-play) · [For developers](#for-developers) · [Acknowledgements](#acknowledgements) · [License](#license)

## Games

Each game's note records the tested version and Mac, frame rates, settings and known issues.

| Game | Title ID | Version | Status |
|---|---|---|---|
| [ASTRO BOT](games/PPSA21564/README.md) | `PPSA21564` | 01.007 | Boots; the title screen, the world map and every level tried show a picture, and the controller works. About 28 fps on the world map and 24 fps in the first level on an M4 Pro; 12 to 47 fps depending on the scene. Not finished: about 9 % of world-map runs crash, some effects flicker, nobody has played beyond the start of a level. |
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
   cp titles/example.env titles/PPSA21564.env      # set BIN (your translated program), APP0 (your dump), GPU=kyty
   ```

6. **Prepare the shader cache** from the pipeline list shipped in `games/PPSA21564/` (the list holds keys and hashes only, no game code). It compiles the game's 1,260 pipelines once, about 90 seconds on an M4 Pro, so the first visit to a scene does not stutter:

   ```sh
   scripts/nps5 cache prepare PPSA21564
   scripts/nps5 cache status PPSA21564    # says whether the caches match the current build
   ```

7. **Run.** Keep `ENV="NPS5_PERF=2"` in `titles/PPSA21564.env`. A binary you translated yourself needs `--patch dr`. A run without `--scene` starts the game normally; a run is capped at 90 seconds unless you give a reason:

   ```sh
   NPS5_RUN_REASON="playing" scripts/nps5 run PPSA21564 --patch dr --secs 3600
   ```

   `--scene worldmap` skips the intro; `--dry-run` prints the environment and command and starts nothing. The profile's canonical settings are in [games/PPSA21564/README.md](games/PPSA21564/README.md). It also lists the optional binary patches, including `nt`, which always takes the non-tiled lighting path and skips the tiled-lighting compute kernels; that changes the picture and has not been reviewed.
8. **Quit** by closing the window. This exits at once, without flushing the shader caches, so pipelines compiled since the last periodic save are lost. Only one title runs at a time, and `nps5 run` refuses to start while another GPU process is running.

`nps5 run` sets a memory limit from your RAM, silences audio by default and scrubs the environment. These rules exist because breaking them has rebooted the development machine; read "Running safely" in [AGENTS.md](AGENTS.md) before doing GPU work of your own.

If a step fails for you, that is a bug in [BUILDING.md](BUILDING.md): open an issue with the command and its output.

## For developers

- [BUILDING.md](BUILDING.md): the build, with the table of what was verified from fresh clones.
- [AGENTS.md](AGENTS.md): rules for running things without taking the machine down; for people and coding agents.
- [CONTRIBUTING.md](CONTRIBUTING.md): how a change gets in, what evidence a PR needs, and the rule that no game data is accepted.
- [docs/README.md](docs/README.md): the map of the documentation; [docs/reference/](docs/reference/README.md) covers architecture, frame rate, rendering, shaders, memory, machine safety and profiling. [docs/learnings.md](docs/learnings.md) and [docs/playbook.md](docs/playbook.md) record what was tried, what worked and what failed.
- Per-game profiles, pipeline lists and benchmark routes: [games/](games/README.md). Commands: `nps5 doctor | build | patch | run | cache | bench | regress | analyze | report`.
- Before every commit run `scripts/check.sh` (game data, secrets, personal paths); `scripts/install-hooks.sh` makes it a pre-commit hook.

**AI assistance.** Pull requests written by hand or with a coding agent are welcome on the same terms. Fill in the "AI assistance" line of the pull request template for every PR, name the agent if you used one, and confirm that a person read the diff and the evidence. A person decides what gets in. Details in [CONTRIBUTING.md](CONTRIBUTING.md).

Questions and bugs: open an issue or a discussion on this repository.

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
