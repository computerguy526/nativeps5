# Game profiles

Each game the project knows has a folder `games/<title id>/`, named after the title ID: ASTRO BOT's is
`PPSA21564`. **Everything game-specific belongs here. Code reads the profile; it does not hard-code a game.** A
per-game branch in a back end, a constant in a runner, an address in a script: each of those is a row in a profile
instead. Prefer a general fix to a profile row; use a row when the cause is known and lies in the game.

No game data is in a profile and none is accepted ([CONTRIBUTING.md](../CONTRIBUTING.md), "Never game data"):
title IDs, versions, addresses, hashes, keys, counts and timings only. `scripts/check.sh` allows exactly the files
below in a profile folder and rejects everything else there.

The folder holds:

- **`README.md`**: the game's note, headed `<Game name> (`<title id>`)`: version, engine and graphics, the current
  status with its date and machine, what the profile needs and why, how to reach a scene, patches, known issues,
  what is open, and "Found on this game" (links to the reference entries first seen here).
- **`game.tsv`**: the game's constants, one `key<TAB>value` row each, comments on their own lines: identity and
  version; the back end and the environment deltas on top of the preset (`env` rows; `env_forbidden` for values
  that must never be set); launch arguments; memory facts; the lineage of the translated binary with the host
  address shift of each build (`lineage` rows); facts about the game's own mechanisms that tools and people need
  (dynamic resolution, resolution sites, timing of a run); the pipeline list's version and counts.
- **`patches.py`** (optional, DATA ONLY: literals, read with `ast.literal_eval`, never executed): patches of the
  *translated* binary, never of the dump. `BUILDS` (build ID prefix per version), `CUTS` / `PARAM_CUTS` (sites:
  guest function entry, byte offset in the translated function, the original word **asserted**, the new word, a
  note; anchored by the `_fn_0x<entry>` symbol, so no per-link address), `REQUIRES`, `BINDING` (per patch: purpose,
  binding now/goal, `guest`, `why`, `review`, `checked`, optional `setting` for a patch that becomes a runtime
  setting) and a lineage-shift table used only for binaries linked without symbols. `scripts/nps5lib/patch.py`
  (`--list`, `--check`, apply to a copy, `--docs`) checks the embedded build ID, refuses on any mismatch, re-signs
  the copy and writes a `.patches.json` sidecar. The asserted words are the only bytes of a translated program in
  this repository; nothing else of a binary is ever added.
- **`scenes.tsv`** (optional): the scenes a run can name (`nps5 run <id> --scene <name>`), one per line: name,
  launch arguments, pad script (a file in `pad/` or an inline script), until (the target that ends the run, see
  `scripts/nps5lib/watch.py`), seconds (`<s>` or `<s>/<max>`), save name, description. `level:*` is a template row;
  `{name}` in it is filled from `level:<name>`.
- **`pad/<name>.vpad`** (optional): pad scripts, input timings only.
- **`bench.tsv`** (optional): benchmark routes for `nps5 bench`, one per line: name, scene (a `scenes.tsv` name),
  measured window (`marker:<m>+<s>`, `present:<scene>+<n>` or `<a>s..<b>s`), run class (`perf` or `func`),
  description. A frame time is quoted with the name of its route.
- **`smoke.tsv`** (optional): regression routes for `nps5 regress`, one per line: name, scene, mode (`quick`, `full`
  or `quick,full`), reach (`<until spec>@<seconds>`), `mem_max_mib` (peak footprint), `stall_ms_max` (largest frame
  once steady), description.
- **`validated.tsv`** (optional): the component commits this game was last green on, one row per mode and machine
  class, the latest of each: `mode<TAB>kyty=<commit> kk=<commit> ps5recomp=<commit> lineage=<name>+<patches>
  date=<UTC day> class=<machine class> machine=<model> macos=<version>`; a title on the AnyPS5 back end has
  `anyps5=<commit>` in place of `kyty=`. `kk=` reads `<Mesa pin>+<hash of the applied series>` when KosmicKrisp was
  built from `kosmickrisp/` here ([hardware.md](../docs/reference/hardware.md), "Adding a machine"). Written by
  `nps5 regress check --record`, only for a run in which every route was green and every rule could be checked,
  from committed component sources; a red result leaves the rows as they were. `game.py check` reads the rows. It
  holds no game data; commit it with the pull request that changes it.
- **`pipelines.kyplst`** (optional) and **`pipelines.kyplst.verified`**: the pipeline list and the record of its
  no-game-code proof. See below.

## Pipeline lists

A first visit to a scene compiles its pipelines: 14-99 compiles and 2-15 s of stalls per new level on ASTRO BOT.
A committed list of *which* pipelines the game needs lets everyone build them before playing.

**What a list holds** (format `KYPLST1`, defined in the `kyty` fork's `pipelineList.h`): for each shader, where
its code sits in the player's own executable (byte offset and size) and strong hashes of that code; the program
source identity (stage, user-data counts, static state, stage input layout); permutations (push-data cursor,
encoded resource specialization); graphics pipelines (their stages as permutation references, the Vulkan state
words with module hashes cleared, the layout signature) and the scenes each was recorded in. **Keys, hashes and
state. No game code and no translator output**: no PS5 shader bytecode, no SPIR-V, no MSL, no AIR, no Metal
binary. The back end reads a shader's code from your copy of the game and uses it only when the hashes match.

**How that is enforced.**

1. `scripts/verify-pipeline-list.sh games/<title id>/pipelines.kyplst <your dump>` runs the no-game-code proof: it
   reads the code of every listed shader from your copy of the game and fails if any 64-byte window of it occurs
   in the file or in any decompressed section. On success it writes `pipelines.kyplst.verified` (the list's
   SHA-256, the result line, counts, date). It uses no GPU.
2. `scripts/check.sh` accepts a `.kyplst` only as `games/<title id>/pipelines.kyplst`, only if it parses (known
   sections, sizes in bounds, nothing after the last section, the title ID of its folder), only if no decompressed
   section contains a shader binary or shader text (SPIR-V, Metal library, LLVM bitcode, ELF/SELF, MSL or AIR
   text) or nested compressed data, only if every section's stored bytes are exactly one zstd frame (a second or
   skippable frame could carry a payload the decoder never shows), and only with a `.verified` record whose hash
   matches these exact bytes. A changed list without a new proof fails.
3. The record is an attestation by whoever ran the proof; the check cannot rerun it without the game. A maintainer
   reruns step 1 before a list is taken.

**How to use a list.** `scripts/nps5 cache prepare <title id>` (`scripts/prepare-pipelines.sh <title id>` is the
same call) starts the title once in prepare mode: every
permutation and pipeline is built through KosmicKrisp from your own dump, and the process exits before the game
runs (ASTRO BOT: 1,259 of 1,260 built in 85 s on an M4 Pro). It fills the Vulkan driver cache, the program cache,
the KosmicKrisp pipeline archive and a private OS Metal cache. With KosmicKrisp patch 0020 it runs twice: pass 1 collects every shader's MSL (`KK_LIBCACHE_DIR=<cache>/kk-libcache`), `scripts/kk-libcache-build.sh` builds it into Metal libraries, pass 2 prepares again from an empty cache with those libraries in place (a pipeline archived from a source-compiled library never hits when the shader is later loaded from a prebuilt one); runs then load libraries in 0.3 ms instead of compiling them (`--single-pass` skips pass 2).

**What stays out, always** (git-ignored, generated per checkout, under `NPS5_SCRATCH`): those compiled caches and
archives, Metal binaries, driver caches, shader and pipeline *journals* (they carry code), MSL / SPIR-V / AIR
dumps, frame dumps. `scripts/check.sh` rejects them by type, by content and by place.

**How to make or extend a list.** Record first visits of each scene into an empty cache with the pipeline journal
on, export and merge the journals with the fork's `kyty_pipeline_list` tool (the journals themselves stay local),
run the proof, commit the list with its record, and update the counts in `game.tsv`. A list names permutations, not
SPIR-V, so it survives most translator changes; after a KosmicKrisp change that alters MSL only the local caches
need rebuilding (`prepare` again).

## Games

| Game | Title ID | Version | Engine | Back end | Status (2026-11-17, M4 Pro 24 GB) |
|---|---|---|---|---|---|
| [ASTRO BOT](PPSA21564/README.md) | `PPSA21564` | 01.007 | Team Asobi in-house | `kyty` | boots; title 47 fps, world map 23 fps, levels 12-20 fps; not finished |
| [A Game About Digging A Hole](PPSA30552/README.md) | `PPSA30552` | not recorded | Unreal Engine 5 | `anyps5` | boots; menu, intro and gameplay run at 37-60 fps; not finished |

No title is "supported". Both are bring-up targets on one 24 GB Mac.

## Where a finding goes

- A fact about one title: its note, in the section it belongs to, marked current or resolved; its constants in
  `game.tsv`.
- A mechanism that would hold for another title: an entry in `docs/reference/<subsystem>.md` (symptom, cause, fix,
  found on), linked from the note's "Found on this game".
- A measured idea, win or loss: one row in [docs/learnings.md](../docs/learnings.md).
- Write a finding once and link to it.

## Adding a game

1. `titles/<title id>.env` (git-ignored; start from `titles/example.env`): your translated binary, your dump, the
   back end.
2. `games/<title id>/README.md` and `game.tsv`, following ASTRO BOT's; the other files as they come.
3. A row in the table above.
4. No screenshots of commercial games in the repository; a frame picture may be attached to an issue when it shows
   a problem.
