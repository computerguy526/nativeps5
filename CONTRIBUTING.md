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

**Shader lists.** Share yours with `scripts/nps5 cache share <title id>` (games/README.md, "Share your list"): a PR may add
only `games/<title id>/contrib/<date>-<hash>.kyplst` plus its `.verified` (made by that command, from your own
copy of the game and your own runs), and nothing else; the maintainer folds it into `pipelines.kyplst`. Rules: keys,
hashes and pipeline state only; one title and one app version per file (the header says which; another version
gets its own list); the `.verified` must belong to exactly those bytes; nothing from the game and no personal
detail in the file or the PR text; a list made by an agent needs the AI line as for any PR. A file that fails
`scripts/check.sh` or the proof is not merged and its branch is deleted. People without git attach the two files to a
"Shader list" issue.

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
| System libraries, AGC driver, shader recompiler | `anyps5` | - |
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

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`; no secrets, game data or GPU.
Each check is one command locally:

| Job | What | Locally |
|---|---|---|
| check | game-data / secrets / personal-path check of every tracked file, plus the unit tests (macOS) | `scripts/check.sh --tree --tests` |
| privacy | the same check on the commits of the PR, commit e-mails must be GitHub noreply, no personal path or e-mail in added lines | `BASE=origin/main TIP=HEAD scripts/ci/privacy.sh` (= `scripts/check.sh --range origin/main..HEAD && scripts/ci/check_identity.sh origin/main..HEAD`) |
| lint | `ruff` (bug-finding rules, `ruff.toml`) | `pip install ruff && ruff check .` |
| lint | `shellcheck -S warning` (known findings in `.shellcheckrc`) | `git ls-files -z '*.sh' \| xargs -0 shellcheck -S warning` |
| lint | `components.lock` format, pipeline list records, internal markdown links | `python3 scripts/ci/check_components_lock.py`, `scripts/ci/check_kyplst_records.sh`, `python3 scripts/ci/check_md_links.py` |
| workflows | workflow syntax | `actionlint` |

Not in CI, because they need a game dump, a GPU or a Metal 4 Mac: the regression gates, the shader gate, the title runs, and
`scripts/verify-pipeline-list.sh` (a maintainer reruns the proof). A PR needs green CI; it does not replace the evidence.
`.editorconfig` sets the basics (UTF-8, LF, final newline).

## Reporting a problem

Run `nps5 bug-report` (or `nps5 bug-report <run>` for an earlier run) and attach the zip it writes to the issue. It
collects, from the run you just made: the text log trimmed to its warnings, errors and one sample of each repeated line,
`run.json`, `summary.txt` and `crash.txt` when there are any, `nps5 doctor`, the commit of each component and whether it has
local changes, the settings in effect, the macOS and Xcode versions and the connected controllers. It lists the files before it
writes them (`--dry-run` stops there), redacts your home folder, user and computer name, e-mail addresses and serial numbers,
and reads nothing but text from an allow-list: no game files, shaders, dumps, journals, caches or saves. Read the zip before
you attach it. [docs/reference/logging.md](docs/reference/logging.md) has the details.

Add what the zip cannot know: the title ID and version, where in the title it happens, the steps and how often. Without a
run (a build that fails, a tool that crashes) paste the command and its output, with your home folder removed from paths.
Never attach game files, caches or dumps; a frame picture is fine when it shows the problem.
