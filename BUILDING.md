# Building from source

From a clean Apple silicon Mac to the built toolchain. Every step is CPU only until "Run". Each step says whether
it was followed literally from fresh clones in a clean directory ("What was verified" at the end has the table with
times). If a step fails for you, that is a bug in this page: open an issue with the command and its output.

**Four repositories** make up the project: this one, the translator and runtime
([nativeps5-recomp](https://github.com/computerguy526/nativeps5-recomp)), the Kyty GPU back end
([nativeps5-kyty](https://github.com/computerguy526/nativeps5-kyty)) and the PS5 system libraries
([nativeps5-anyps5](https://github.com/computerguy526/nativeps5-anyps5)). Steps 1 to 5 fetch and build all of it;
a title run needs every piece. The Kyty back end replaces the graphics exports only: libkernel, libc, pad, audio
and about 1,300 other exports come from the `anyps5` libraries.

## 0. Prerequisites

- An Apple silicon Mac, macOS 26 or later. 24 GB of RAM is the development machine; the scripts size their limits
  from your RAM (`scripts/nps5-env.sh`).
- About 7 GB of free disk for sources and build trees (Mesa 0.5 GB, Kyty with submodules 2.2 GB, AnyPS5 with its
  history and build 1.5 GB), and tens of GB
  for a title's work files, outside iCloud-synced folders.
- Xcode (the full app, for the `metal` compiler used by the shader tools) or at least the Command Line Tools
  (`xcode-select --install`) for everything else. With Xcode 26 the Metal toolchain is a separate download:
  `xcodebuild -downloadComponent MetalToolchain`.
- [Homebrew](https://brew.sh), then:

  ```sh
  brew install git go cmake ninja meson bison llvm coreutils python sdl3 glslang spirv-tools \
               vulkan-loader vulkan-headers vulkan-tools freetype ffmpeg nasm sdl2-compat zstd
  ```

  `coreutils` gives `gtimeout` (the run scripts), `llvm` and `bison` are for Mesa, `sdl3` and `glslang`
  (`glslangValidator`) for the Kyty library, `sdl2-compat zstd glslang` for the AnyPS5 libraries,
  `freetype ffmpeg` for the translator's runtime, `nasm` only for the FEX differential tests.
- Python packages for the Mesa build, in a venv (nothing is installed system-wide):

  ```sh
  python3 -m venv ~/nps5-venv && ~/nps5-venv/bin/pip install mako pyyaml packaging
  export KK_VENV=~/nps5-venv
  ```

## 1. Lay out the workspace

```sh
mkdir nativeps5-ws && cd nativeps5-ws
git clone https://github.com/computerguy526/nativeps5 nativeps5
cd nativeps5
cp nps5.conf.example nps5.conf        # then set NPS5_DUMPS_DIR; the rest has defaults
scripts/install-hooks.sh              # optional: scripts/check.sh as the pre-commit hook
scripts/fetch-components.sh           # clones ../ps5recomp, ../kyty and ../anyps5 at the commits in components.lock
```

The workspace root (`NPS5_ROOT`) is the folder that holds this checkout and the components: `nativeps5-ws/` with
`nativeps5/`, `ps5recomp/`, `kyty/`, `anyps5/`, and later `build/` and `scratch/`. `. scripts/nps5-env.sh` prints nothing and
exports the layout; `env | grep ^NPS5_` shows it. No script contains an absolute path: everything is relative to
`NPS5_ROOT` or comes from `nps5.conf`. `NPS5_FORK_BASE=<URL prefix>` makes `fetch-components.sh` clone from a mirror
or from your own forks.

## 2. The translator: `ps5recomp`

```sh
. scripts/nps5-env.sh                  # exports NPS5_ROOT and the toolkit paths; needed in every new shell
scripts/build.sh recomp               # go build ./cmd/ps4-recomp ./cmd/nps5 -> ps5recomp/bin/
(cd ../ps5recomp && go test ./pkg/diag/ ./pkg/elfloader/ ./pkg/disasm/)   # quick unit tests; `go test ./pkg/...` is the full set (about 6 minutes)
../ps5recomp/bin/nps5 doctor          # lists what the toolkit still misses on this Mac
```

`nps5 doctor` warns about the KosmicKrisp ICD and the AnyPS5 libraries until steps 3 and 5 are done. Go modules are vendored. `ps5recomp/go.mod` asks
for Go 1.27.1: an older Homebrew Go downloads that toolchain on the first build (network, about 70 MB).
Tests that need upstream's sample ELF or a PS5 payload SDK skip when those are absent (`ps5recomp/README.md`).

## 3. KosmicKrisp (Mesa, Vulkan on Metal) with the patch series

```sh
scripts/build.sh kosmickrisp
```

This runs `kosmickrisp/build-kosmickrisp.sh --arch arm64 --work $NPS5_KK_WORK`: a shallow clone of Mesa at
`kosmickrisp/MESA_COMMIT`, `kosmickrisp/patches/*.patch` applied in order (each after `git apply --check`), a meson
build of only the `vulkan_kosmickrisp` and `kosmickrisp_mesa_icd` targets, and an install into
`$NPS5_KK_WORK/install-arm64`. It prints the ICD json; `scripts/nps5-env.sh` exports the same path as `NPS5_KK_ICD`.

```sh
VK_DRIVER_FILES=$NPS5_KK_ICD vulkaninfo --summary     # expect driverName = KosmicKrisp (this opens a GPU device)
```

`kosmickrisp/EXPECTED_TREE` is the git tree hash of the patched source the maintainers build from, without the
build script's stamp file `.nps5-patches`. To compare, in `$NPS5_KK_WORK/mesa`:
`git add -A . && git rm -q --cached .nps5-patches && git write-tree` (then `git reset -q` to leave the index as it was).

## 4. Kyty GPU library

```sh
scripts/build.sh kyty                 # -> ../kyty/build-arm64/kyty_gpu.dylib (NPS5_KYTY_GPU_LIB points there by default)
```

`git submodule update --init --recursive --depth 1` (about 200 MB), then a Release build for arm64 with
`-DKYTY_BUILD_LAUNCHER=OFF` of the one target `kyty_gpu`. It links Homebrew's shared SDL3. Run titles with the
preset in `titles/kyty-preset.env`: the code defaults are the slow path.

## 5. AnyPS5 host libraries

```sh
scripts/build.sh anyps5               # -> ../anyps5/build-arm64/core/libs/libs/unpatched/ (NPS5_ANYPS5_BUILD points at build-arm64 by default)
```

Seven submodules (`--depth 1`; glslang only for its commit, which stamps the shader cache version), then a Release build with `-DAPS5_HOST_ARM64=ON` of the targets `libSceAgc
libSceAgcDriver libkernel libc nps5_gpu`. The result is five images: `libkernel.prx`, `libc.prx`, `libSceAgc.prx`,
`libSceAgcDriver.prx` and `libnps5_gpu.dylib` (the bridge the translated program calls; it also carries the host
side of the Kyty back end). `nps5 build` links a title against them and every title run loads them, with either
back end. Source `scripts/nps5-env.sh` again afterwards: it exports `NPS5_ANYPS5_BUILD` once the build exists.
[Upstream AnyPS5](https://github.com/boykopovar/AnyPS5) is x86-64 Windows and Linux only; only the arm64 macOS
host libraries are built from this fork.

## 6. Prepare your own dump

You need a decrypted, extracted folder of a game you own, as it sits on the console: `eboot.bin`, `sce_sys/`,
`sce_module/` and the game's data. How you dump your console is outside this project, and nobody here can give you
game files. Rules:

- Keep dumps under `NPS5_DUMPS_DIR`, outside every git checkout. Make the folder read-only
  (`chmod -R a-w "<dump>"`); nothing in this project writes to it.
- Everything derived from a dump (translated code, shader and pipeline caches, frame dumps, logs) goes to
  `NPS5_SCRATCH`, never into a checkout. `scripts/check.sh` refuses such files in a commit.

## 7. Translate a title (needs your dump)

```sh
../ps5recomp/bin/nps5 build "$NPS5_DUMPS_DIR/<title folder>" -o "$NPS5_SCRATCH/apps" -j 4
```

`nps5 build` translates `eboot.bin` in-process, compiles the runtime, links the native program and assembles a
`.app` (`ps5recomp/docs/m3/packaging.md`: stages, profiles, the toolkit variables `NPS5_ANYPS5_BUILD`,
`NPS5_KK_PREFIX=$NPS5_KK_WORK/install-arm64`, `NPS5_VULKAN_LIB`). A build profile in `ps5recomp/profiles/<title id>.json`
is picked up by title id (`--profile <name>` overrides it). Source `scripts/nps5-env.sh` first so the toolkit
variables are set. The translated native program is written to `~/Library/Application Support/nativeps5/<title id>/build/native`
(about 650 MB for a large title); the link step reports a handful of `STUBBED ... unresolved symbol` lines for the GPU
bridge, which is expected. Translation of a large title takes about 11 minutes and several GB of scratch
(verified below with ASTRO BOT, 4 jobs).

## 8. Run (needs your dump and a GPU)

```sh
cp titles/example.env titles/<TITLE ID>.env      # set BIN (the translated native program), APP0 (your dump), GPU=kyty
scripts/nps5 run <TITLE ID> --dry-run            # prints the environment, the binary and the command; runs nothing
scripts/nps5 run <TITLE ID> --scene title --until first-present+10 --class func
scripts/nps5 run <TITLE ID> --scene title --until pic+10 --class func   # until a real picture (not a black or loading frame)
```

Keep the example's `ENV="NPS5_PERF=2"` line in `titles/<TITLE ID>.env`. A binary you translated yourself is not one of the
profile's recorded lineages, so its default binary patches (`patches_default` in `games/<id>/game.tsv`) cannot be found
by their bytes: `nps5 run` refuses with exit 78 and names the patch. Pass `--patch dr` (or another subset the run reports
as found) to run it; patches the binary does not carry are then listed as "not applied" and the title runs without them.

Logs, frame dumps and `run.json` go to `$NPS5_SCRATCH/runs/<TITLE ID>/<tag>`. `--until first-present+10` stops the
run ten seconds after the first frame is presented; look for a picture in the frame dumps. A first run has a cold
shader cache, so give it `--secs 180` and `NPS5_RUN_REASON="<why>"` (runs are capped at 90 s without a reason).

`nps5 run` allows one title run at a time, refuses to start while another GPU-using process is running (exit 75; the
check also matches other `native_*` processes), sets a memory self-limit of 37/48 of RAM (18944 MiB on 24 GB),
refuses GPU counter-heap profiling, scrubs the environment and silences audio.
Read [AGENTS.md](AGENTS.md), "Running safely", before starting GPU work: these rules exist because breaking them
has rebooted the development machine.

For unattended sessions, `host/machine_guard.sh` watches load, memory pressure and title processes. It was written
for the maintainers' workspace layout (`tools-host/`, `work/guard/`) and takes its root from `NPS5_ROOT`; treat it as
reference until it is adapted to this layout.

## 9. Offline shader tools (no title run)

- `tools/shaderlab/gate.sh <KosmicKrisp install dir>`: the shader gate for KosmicKrisp and recompiler changes. It
  needs a shader corpus collected from your own title run (it is game-derived, so none is shipped) and a baseline you
  record with `--baseline` on the unchanged tree.
- `tools/xray/xray.sh <shader>`: one shader through every layer (SPIR-V, NIR, MSL, AIR, AGX statistics);
  `tools/xray/tests/run_tests.sh` uses only the hand-written fixtures.
- `tools/agxdis/build.sh` then `tools/agxdis/agxdis.sh <file.metal>`: AGX instruction statistics of a compiled
  Metal shader. It clones the BSD-licensed `applegpu` disassemblers; the GPL-3.0 `agx-re` tables are opt-in
  (`AGXDIS_NO_AGXRE=0`) and are never redistributed with this project.

## What was verified

From fresh clones of the four repositories in an empty directory, with a scrubbed environment (`env -i`, PATH =
Homebrew + system, an empty HOME, so cold Go and pip caches), at nice 10 with 4 jobs, while other work ran on the
machine (2026-11-17, M4 Pro 24 GB, macOS 26). Components: recomp 26faf6d (the lock is now b0e579b: crash-report fixes, frame-rate block freeze, video pacing, review fixes, tests), Kyty 433734b (the lock is now 8ac0796: a test-only switch, documentation),
AnyPS5 4696b46 (the lock is now 585a803: documentation)); the repositories were cloned from local copies because the GitHub URLs did not exist yet.

| Step | Result | Time |
|---|---|---|
| 0: venv with mako, pyyaml, packaging | ok | 9 s |
| 1: clone, `nps5.conf`, hook, `fetch-components.sh` | ok | 2 s |
| 2: `scripts/build.sh recomp`, `go test ./pkg/diag/ ./pkg/elfloader/ ./pkg/disasm/`, `nps5 doctor` | ok | 28 s |
| 3: `scripts/build.sh kosmickrisp`: Mesa at the pin, 25 patches, configure, compile, install | ok; source tree = `EXPECTED_TREE` | 49 s (warm Mesa download) |
| 5: `scripts/build.sh anyps5`: seven submodules, configure, build of five targets | ok | 702 s |
| 4: `scripts/build.sh kyty`: submodules, configure, compile, link | ok | 1436 s while other builds and runs shared the machine |
| 4: Kyty CPU-only unit tests (`.nps5-check-tests` there, incl. load card, hitch switches, sequencer) | ok | seconds |
| `scripts/check.sh --tree` in all four checkouts, `--tree --tests` in this repository | clean, 0 failures | 65 s |
| 7: `nps5 build "<ASTRO BOT dump>"` | ok: translated, expected stub lines, `.app` assembled | 528 s |
| 8: `scripts/nps5 run PPSA21564 --scene title --until first-present+10 --class func --patch dr` (cold shader cache) | DONE_UNTIL: first present at 2 s, stopped at 12 s | 16 s |
| 8: the same with `--until pic+10` | DONE_UNTIL: first picture (the intro video) at 12 s | 29 s |

Found and fixed on the way: without frame dumps the watcher could not see a Kyty present (`--until` always ended
TIMEOUT although the title rendered); `nps5 run` now asks the Kyty library for frame dumps when a target is set.

Not verified: `host/` and `tools/` scripts in this layout (they still assume the maintainers' private folder names),
`go test ./pkg/...` in full from the clean clone (it passes in the maintainers' tree in about 6 minutes; one
timing-sensitive test, `TestHostAccessHooks`, failed once under load and passed three times alone), `vulkaninfo` with the
built ICD (opens a GPU device), a warm-cache or longer run, and the binary patches (`dr`, `nt`) on a self-translated binary.
