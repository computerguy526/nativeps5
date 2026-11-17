# Contributing

Issues and pull requests are welcome, written by hand or with a coding agent. This page says how a change gets in;
[AGENTS.md](AGENTS.md) has the rules for running things on a Mac without taking it down, and [BUILDING.md](BUILDING.md)
the build.

## Never game data

Never commit, attach or link game files or anything extracted or derived from them: executables and modules
(`eboot.bin`, `.prx`, `.sprx`, SELF/ELF images), assets, the game's shaders in any form (PS5 bytecode, SPIR-V, MSL,
AIR, metallibs), shader and pipeline journals, shader and pipeline caches and archives, frame captures, guest
memory dumps, PGO profiles recorded on a title, keys, firmware. Text that only *describes* a title is fine: title IDs, versions,
function addresses and offsets, hashes, counts, timings, log lines.

One kind of title-derived file is accepted, because it holds no code and removes first-visit stutter for everyone:
a game profile's pipeline list, `games/<title id>/pipelines.kyplst` (shader offsets and hashes in the player's own
executable, permutation keys, pipeline state). It comes with `pipelines.kyplst.verified`, the record of the
no-game-code proof (`scripts/verify-pipeline-list.sh <list> <your dump>`); a maintainer reruns the proof before
taking a list. Compiled caches built from it stay on your machine ([games/README.md](games/README.md)).

`scripts/check.sh` enforces this mechanically (file types, file magic, paths, size) together with secrets and
personal paths. Run it before every commit (`scripts/install-hooks.sh` installs it as the pre-commit hook) and
paste its last line into the PR. A PR that contains game data is closed, and its branch has to be deleted, not
amended: the data would stay in the history.

## Before you start

- Read [AGENTS.md](AGENTS.md) and search [docs/learnings.md](docs/learnings.md) and
  [docs/playbook.md](docs/playbook.md) for the idea: they list what was measured, what worked, and what was tried
  and failed. Do not resubmit a failed idea without new evidence.
- Find out what bounds the frame in the scene you care about before choosing what to change
  ([docs/reference/architecture.md](docs/reference/architecture.md), section 5).
- A change of more than about 1,000 lines, a new platform, or a second implementation of an existing path: open an
  issue first and agree on the scope.
- Prefer a fix that holds for every title to a title-specific rule, and fix a bug at its cause. A workaround (a
  stub, a skipped call, a clamp, a longer timeout) is acceptable only when the cause is known and cannot be fixed
  where it is; say so in the code and in the PR.

## Where a change goes

| Change | Repository | Its own rules |
|---|---|---|
| Translator, runtime, HLE, packaging, regression gates | `ps5recomp` | `AGENTS.md` there |
| System libraries, AGC driver, shader recompiler | `anyps5` (not public: no pull requests possible yet; [BUILDING.md](BUILDING.md), step 6) | - |
| Kyty GPU back end | `kyty` | `README.md` there |
| KosmicKrisp | `kosmickrisp/patches` here: one patch file per change, on top of the series | Mesa's coding style; a patch that is general should also go to Mesa upstream |
| Safety scripts, shader tools, build and run scripts, docs | this repository | this page |

A change that spans repositories is one PR per repository, each linking the others.

Documentation is part of the change: a finding about one title goes into its note under `games/`, a mechanism into
`docs/reference/`, a measured idea (win or loss) into `docs/learnings.md`, a new environment switch into
`docs/env_switches.tsv` (`scripts/gen_env_switches.py`). Write it once and link ([docs/README.md](docs/README.md)).

## A pull request

- **One change per PR**, off the default branch, rebased rather than merged.
- **Commits** say what changed and why in plain sentences: symptom, cause, fix, what was measured.
- **Default off.** New behaviour sits behind an environment switch that defaults to off, unless the evidence shows it
  is neutral or better on the title and on the gates.
- **Evidence**, in the PR text (the template lists it):
  1. `scripts/check.sh --range <base>..HEAD --tests` clean.
  2. Unit tests of the component you touched.
  3. The regression gates (`ps5recomp/tools/regress.sh`: calc3d frame 0 exact, syobon, greenhollow >= 98.6 %), which
     run on free homebrew and need no commercial game.
  4. For KosmicKrisp or shader-recompiler changes: the shaderlab gate against your own corpus, and the
     `kosmickrisp/tests` that apply.
  5. For a speed, memory or picture claim: before and after on the same machine, scene and build. Frame time p50
     and p95 and fps from `perf_frames.csv`, as paired or interleaved runs (identical runs differ by up to 3 fps, so
     one run per side proves nothing); peak memory; and a frame-dump comparison (which frames, the difference
     metric, what the eye sees). State the title ID and version, the scene, run length and number of runs.
  6. What you did not check.
- **Agent-written PRs** are welcome on the same terms. Say which agent, and confirm that a person read the diff and
  the evidence. An agent must follow [AGENTS.md](AGENTS.md); a PR whose evidence was produced by breaking the run
  rules (two GPU processes, a raised memory limit, counter-heap profiling) is not accepted.
- **The "AI assistance" line of the pull request template is required** for every PR, with or without an agent.
  The maintainers' own agents may review an outside PR automatically and comment on it; they never merge. A person
  decides what gets in.

## How changes get in

A maintainer reviews every PR and reruns the gates. A PR is often taken in as a squashed or reworked commit that
credits its author (`Co-authored-by`), and is then closed rather than merged; that is expected. By contributing you
agree that your change is licensed under the licence of the repository it goes into (GPL-2.0-only; MIT for
`kosmickrisp/`).

## Reporting a problem

Open an issue with: the commit of each component, the title ID and version, the Mac model, RAM and macOS version,
the command and environment you ran, and the text log. Remove your home folder from paths. Never attach game files,
caches or dumps; a frame picture is fine when it shows the problem.
