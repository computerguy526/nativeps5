# nativeps5

Static recompilation of PlayStation 5 games to native arm64 macOS programs. The game's x86-64 code is translated
ahead of time (no JIT, no interpreter), linked against reimplemented system libraries, and drawn through Vulkan on
Metal (Mesa KosmicKrisp).

**No game data is distributed here, and none is accepted.** You need your own, legally dumped copy of a game you
own. Nothing in this repository or its component repositories contains game executables, assets, shaders, keys,
firmware or anything extracted from them, and pull requests that add any are closed ([CONTRIBUTING.md](CONTRIBUTING.md)).
This project is not affiliated with or endorsed by Sony Interactive Entertainment or any game publisher.
"PlayStation" and "PS5" are trademarks of their owners.

## What is here

This repository is the entry point: build instructions, contribution rules, the safety and check scripts, the game
profiles, the KosmicKrisp patch series and offline shader tools. The code lives in two component repositories, each
a fork that keeps its upstream's licence ([NOTICE](NOTICE), [components.lock](components.lock)):

| Component | What it does | Upstream | Licence |
|---|---|---|---|
| [`ps5recomp`](https://github.com/computerguy526/nativeps5-recomp) | x86-64 to arm64 translator, runtime, packager (`nps5 build`, `nps5 play`), regression gates | wie-project/PS4Recomp | GPL-2.0 |
| [`kyty`](https://github.com/computerguy526/nativeps5-kyty) | the GPU back end as a library (`kyty_gpu.dylib`): KytyPS5's renderer on arm64 macOS | Jetsku/KytyPS5 with the AstroHybrid `kyty-hybrid` series | GPL-2.0 |
| `kosmickrisp/` (here) | patch series on Mesa's Vulkan-on-Metal driver: pipeline archive, mesh shaders, MSL optimisations | Mesa | MIT |
| `host/` (here) | machine guard, run queue helpers, disk cleanup | - | GPL-2.0 |
| `tools/` (here) | offline shader tools: shaderlab gate, shader x-ray, AGX disassembly statistics | - | GPL-2.0 |

**Not public yet: the PS5 system libraries.** A title run also loads the arm64 host libraries of the maintainers'
AnyPS5 fork (libkernel, libc, pad, audio and about 1,300 other exports; with the second back end also its AGC
graphics driver). That fork is not published. Everything here builds and tests without it, and the tools say so
when it is missing, but a PS5 title cannot be linked or run from the public repositories alone today
([BUILDING.md](BUILDING.md), step 6). The Kyty back end replaces the graphics exports only.

## Status

Two titles are the bring-up targets (title IDs PPSA30552 and PPSA21564); neither is finished. Expect crashes, missing
pictures and frame rates below the console's. Status per scene is in the game notes ([games/](games/README.md));
[docs/learnings.md](docs/learnings.md) records what worked, what was tried and failed, and what the work taught, and
[docs/playbook.md](docs/playbook.md) is the full measured table for the first title.

Supported hosts: Apple silicon Macs with macOS 26 or later (Metal 4). Development happens on one machine (M4 Pro,
24 GB); limits and worker counts are derived from RAM and core count for the others, 16 GB is experimental and
M1/M2 are untested: [docs/reference/hardware.md](docs/reference/hardware.md), `python3 scripts/nps5lib/hw.py doctor`.

## Start here

- Build from source: [BUILDING.md](BUILDING.md)
- Contribute, by hand or with a coding agent: [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md)
- How it works and what is known: [docs/README.md](docs/README.md) (map), [docs/reference/](docs/reference/README.md)
  (architecture, frame rate, rendering, shaders, memory, machine safety, profiling), [docs/learnings.md](docs/learnings.md)
- Something in a log you do not understand: [docs/agents/symptoms.md](docs/agents/symptoms.md)
- Per-game profiles (constants, patches, routes, pipeline list): [games/](games/README.md)
- Before every commit: `scripts/check.sh` (game data, secrets, personal paths); `scripts/install-hooks.sh` makes it a
  pre-commit hook

## Licence

GPL-2.0-only, except `kosmickrisp/` (MIT, as Mesa). Third-party work and the projects this one learned from are
credited in [NOTICE](NOTICE).
