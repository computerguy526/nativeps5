# Notes for agents

nativeps5 statically recompiles PS5 games to native arm64 macOS programs: a translator and runtime (`ps5recomp`),
system libraries with an AGC graphics driver and an RDNA to SPIR-V shader recompiler (`anyps5`, the nativeps5-anyps5 fork: BUILDING.md step 5), an alternative GPU
back end (`kyty`), and Mesa KosmicKrisp (Vulkan on Metal) with the patch series in `kosmickrisp/`. It is not an
emulator and must not become a JIT. [README.md](README.md) is the overview, [BUILDING.md](BUILDING.md) the build,
[CONTRIBUTING.md](CONTRIBUTING.md) what a pull request needs, [docs/README.md](docs/README.md) the map of the
documentation. Each component has its own `AGENTS.md`; its rules apply inside it in addition to these.

These rules apply to every agent and every person. They are short because the reasons are written elsewhere:
each safety rule has its incident in [docs/reference/machine-safety.md](docs/reference/machine-safety.md).

## Where to start

| Task | Read first |
|---|---|
| Any performance or picture idea | [docs/learnings.md](docs/learnings.md), then [docs/playbook.md](docs/playbook.md): search for the idea before writing code; skip what is listed as tried and failed |
| "Why is this scene slow" | [docs/reference/architecture.md](docs/reference/architecture.md), section 5 (what bounds a frame), then [profiling](docs/reference/profiling.md) |
| A crash, an exit code, a `[RED]` line, a wrong picture | [docs/agents/symptoms.md](docs/agents/symptoms.md): search it for the log text or what you see |
| Frame rate, pacing, resolution | [docs/reference/frame-rate.md](docs/reference/frame-rate.md) |
| Rendering | [docs/reference/rendering.md](docs/reference/rendering.md) |
| A shader that is slow or wrong | [docs/reference/shaders.md](docs/reference/shaders.md), then `tools/xray/README.md`, `tools/agxdis/README.md` (offline, no title run) |
| Memory | [docs/reference/memory.md](docs/reference/memory.md) |
| Work on one title | its profile: `games/<title id>/README.md`, then [games/README.md](games/README.md) for what each file is |
| An environment switch | [docs/env_switches.tsv](docs/env_switches.tsv) |
| Build something | [BUILDING.md](BUILDING.md), `scripts/build.sh` |
| Run a title | `scripts/run-title.sh` and "Running safely" below |
| A KosmicKrisp change | `kosmickrisp/patches` (one file per change), `kosmickrisp/tests`, `tools/shaderlab/gate.sh` |
| How to scope, measure, hand off and report | [docs/agents/README.md](docs/agents/README.md) |
| Checking a change before a pull request | [CONTRIBUTING.md](CONTRIBUTING.md), "A pull request" |

## Where a new finding goes, once

- A fact about one title (scenes, pass sizes, measurements, why its configuration needs a switch, status): that
  title's note, `games/<title id>/README.md`, in the section it belongs to, marked current or resolved. Its
  constants go into `game.tsv`, a patch of its translated binary into `patches.py`, a measured route into
  `bench.tsv`.
- A mechanism that would hold for another title: an entry in `docs/reference/<subsystem>.md` (symptom, cause, fix,
  found on), a row in [docs/agents/symptoms.md](docs/agents/symptoms.md) for the text someone will search for, and a
  link under the game note's "Found on this game".
- A measured idea, win or loss: one dated row in [docs/learnings.md](docs/learnings.md).
- A new environment switch: `scripts/gen_env_switches.py ps5recomp=<dir> anyps5=<dir> kyty=<dir>
  kosmickrisp=<mesa>/src/kosmickrisp` (without the component folders only this repository's rows are refreshed),
  then its meaning in `docs/env_switches.tsv`.
- Link to a finding instead of repeating it. This file changes only when a rule does.

## Game data: never

- The dump is the user's own copy, outside every checkout, read-only. Never modify it, never copy from it into a
  checkout, never send any of it anywhere (not to a web service, not into an issue, not into a prompt for another
  tool).
- Everything derived from a title (translated code, shaders in any form, pipeline and shader journals, compiled
  caches and archives, frame dumps, guest memory, PGO profiles) lives under `NPS5_SCRATCH` and is never committed.
  Title IDs, addresses, hashes, counts and timings are fine in code, docs and PRs.
- **One exception, mechanically checked: the pipeline list of a game profile**, `games/<title id>/pipelines.kyplst`.
  It names shaders by offset and hash in the player's own executable and holds permutation keys and pipeline
  state: no code, no translator output. It is committed only together with `pipelines.kyplst.verified`, the record
  of the no-game-code proof that `scripts/verify-pipeline-list.sh` runs against your own copy of the game;
  `scripts/check.sh` rejects a list without a matching record, a list with a shader binary or shader text in any
  section, and every other game-derived file in a profile ([games/README.md](games/README.md)). The compiled
  caches built from a list (`scripts/prepare-pipelines.sh`) stay local.
- Everything game-specific belongs in the game's profile, `games/<title id>/`. Code reads the profile; it does not
  hard-code a game.
- A patch to a translated binary is described (guest address, instruction before and after, game build); the
  patched binary and the original bytes are never shared.
- Run `scripts/check.sh` before every commit and `scripts/check.sh --range <base>..HEAD --tests` before a PR (it
  checks every commit of the range, so a file added and removed again is still found; `--history` checks all). Do not
  work around a finding by renaming a file or adding it to `.nps5-check-allow`; the allow list is only for fixtures
  built from source in the same folder, with a comment that says so.
- No secrets, no personal data: no home-folder paths (use `$NPS5_ROOT`), no user or machine names, no e-mail
  addresses of people who did not ask to be credited.

## Running safely

These rules come from incidents on the development Mac: a kernel panic, WindowServer watchdog reboots, memory
exhaustion. `scripts/run-title.sh` enforces the ones it can.

- **One GPU-using process at a time**, on the whole machine. A title run, a frame or shader replay, the KosmicKrisp
  tests, a GPU timing harness and `vulkaninfo` all count. Before starting GPU work run
  `pgrep -fl "native|agc_frame_replay|kk_spv|spvpipe|harness"` and wait if anything is listed. Title runs go through
  `scripts/run-title.sh`, which holds a lock; never start a title binary directly.
- **No Metal counter heaps.** Never enable GPU timestamp queries or counter-heap profiling on a title
  (`APS5_PROFILE_PASSES`, `APS5_PROFILE_GPU`, `KYTY_EOP_TIMESTAMPS=gpu`, a KosmicKrisp build with timestamp queries
  in the frame loop, any Vulkan query pool of timestamps): it has panicked the kernel. GPU time comes from commit
  feedback, completion times, per-process GPU time from the IOKit registry, or an Xcode Metal System Trace
  ([profiling](docs/reference/profiling.md)). `[RED] gpu timestamp profiling OFF` or `too many heaps` in a log
  means stop and report.
- **GPU work outlives the process.** A committed command buffer keeps running after `_exit` and `kill -9`. Killing a
  run does not free the GPU. So: every loop, wait and retry you add on the GPU side has a bound; the compute loop
  cap (`KK_LOOP_CAP`) and the GPU watchdog (`KK_GPU_WATCHDOG_S`) stay on; an offline GPU test has an iteration cap
  in the shader and a kill timeout of 60 s or less. `[RED] loop cap hit` is a finding to report, not a cap to raise.
- **The memory self-limit is not yours to raise.** `NPS5_SELF_MEM_LIMIT_MB` is 37/48 of physical RAM
  (`scripts/nps5-env.sh`; 18944 on the 24 GB development Mac). A scene that hits it (exit code 86) is a memory bug
  to report, not a limit to lift. Do not start a run while macOS reports memory pressure.
- **No screenshots and no screen recording.** Never use `screencapture`, screen-recording APIs or anything that
  captures the user's screen. Pictures come only from the program's own frame dumps (`APS5_DUMP_FRAMES`,
  `KYTY_DUMP_FRAMES`), sparse (every 600th frame or fewer unless a short window needs more), written to
  `NPS5_SCRATCH`.
- **Short runs.** 90 s by default; longer (up to 180 s) only with `NPS5_RUN_REASON` saying why. `nps5 run --play` (up to two hours, sound on, no early stop) is for a person playing at the keyboard; agents and scripts never use it. Launch title runs
  in the background and wait for them; a tool timeout kills a foreground run mid-frame.
- **Kill only what you started.** Record the PID of the native child, not only its wrapper, and kill that PID.
  Never `pkill`, never kill by name, never `SIGSTOP`.
- **Never edit in place a script that a queued or running job executes.** Write a new file and `mv` it over.
- **Builds are polite.** At most 4 jobs at nice 5 (`scripts/build.sh` does this); no builds during a run whose
  frame times you measure. Start or resume at most one agent every two minutes.
- **Never run x86 code natively**, and keep test audio silent (the default environment).
- **Disk.** Keep 8 GB free; clone work directories with `cp -c -R` (copy on write) and delete your clones and build
  directories when done. Before deleting a directory you did not create, search the scripts for references to it.
- **Windows.** A game window opens small in the top-left of the built-in display and never takes focus from the user.

## Measuring

- **One canonical runner.** `scripts/run-title.sh` with the title's environment file is the current configuration.
  A variant is that runner plus overrides, never a private copy with other defaults. Name the commit of every
  component and the overrides of each arm.
- **Check the machine first**: AC power and no Low Power Mode (`pmset -g batt`), no other process holding the GPU,
  no memory pressure, low load. A whole morning of runs at 1 fps came from this.
- **Performance class or functional class.** Frame times are quoted only from runs on a quiet machine. Boot,
  picture and memory checks run under load and their frame times are not quoted.
- **A/B.** Identical runs differ by up to 3 fps. Compare with interleaved runs (A B B A), or an in-run switch on a
  static scene; one run per side proves nothing. Say which scene (menu, cutscene, level) every number is from: they
  differ by a factor of two. Check from frame dumps that the run reached the scene.
- After any KosmicKrisp or back-end source change: your own copy of the work directory, one warm-up run, the
  offline pipeline prewarm, and only then measure. A cold pipeline cache looks like a regression.
- **Evidence.** Report frame time p50 and p95, fps, peak footprint, the number and order of runs, the scene, the
  commit of every component, and the picture of both arms. For an intermittent bug, at least five runs. For a
  codegen change, the exactness test and the shaderlab gate. A profile row is not a saving: confirm by removing the
  thing. The full table is in [docs/agents/README.md](docs/agents/README.md), section 5.
- A gate that waits for a go-ahead matches an anchored, attributed pattern or a flag file, never free text.

## Changing code

- New behaviour is off by default behind an environment switch, unless it is proven neutral or better on the title
  and on the gates.
- Prefer general fixes to title-specific rules; find the root cause before changing code; no stubs that return
  made-up success, no special-casing a value seen in one title's trace. Anything the guest can read (a clock, a
  counter, a statistic) is an input to the game: do not fake it without knowing what the game does with it.
- Foundation code (memory accounting, ownership, code generation) must cost near nothing when off: no allocation or
  formatting on hot paths, measured CPU time per frame on versus off.
- A guard is code: test that it fires (a deliberate trigger) and that it stays silent on the reference scenes.
- Gates before a PR: the component's unit tests; `ps5recomp/tools/regress.sh` (calc3d frame 0 exact, syobon,
  greenhollow >= 98.6 %) with a private `REGRESS_DIR`; for KosmicKrisp and shader-recompiler changes the shaderlab
  gate (`tools/shaderlab/gate.sh <KosmicKrisp install>`); for AnyPS5 rendering changes the frame-replay hash, and
  frame dumps of a real run when the change affects when results reach guest memory.
- Third-party code keeps its licence. Do not copy from a repository without a licence (ideas only), and do not
  bring GPL-3.0 code into these GPL-2.0-only trees. Credit what you reuse in `NOTICE`.

## Git

- Work on your own branch or worktree. Commit only when asked; never force-push; never push to an upstream.
- **Identity comes from git's configuration, not from you.** Commit with whatever identity the repository's
  configuration and hooks provide. Never set `user.name` or `user.email` in a repository, never pass `--author` or
  `-c user.*`, never set `GIT_AUTHOR_*` or `GIT_COMMITTER_*`, never use `--no-verify`, never switch the active `gh`
  account. If a hook rejects a commit, fix the cause or stop and ask. Outside contributors commit under their own
  public identity; do not put anyone else's name or address in a commit.
- Do not commit third-party submodule contents or symlinks into `3rdparty/`.
- Commit messages: `area: summary`, then the symptom, the cause, the fix and what was measured, and a
  `Co-Authored-By:` trailer naming the agent.

## Reporting

- One report per piece of work: the verdict (win default-on, win opt-in, neutral, loss, broke something,
  unconfirmed), the numbers, the commits and switches, what you did not check, and where you wrote it down.
  "Unconfirmed" is an acceptable verdict; a win from one run is not.
- When you stop for any reason, leave a one-page handoff: state, commits, what is built and not measured, the exact
  commands for what is left, what to watch for ([docs/agents/README.md](docs/agents/README.md), section 7).
- Do not idle in a wait loop on a long job: arm the chain, write the handoff, stop.
- When you finish: your row in [docs/learnings.md](docs/learnings.md) (and, for PPSA30552, the dense row in
  [docs/playbook.md](docs/playbook.md)), including negative results, so the next agent does not repeat them.
