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
  setting), `SETTINGS` (cuts the recompiler translates as a runtime patch point: cut -> (point key, marker string);
  a binary carrying the marker reports the cut as `setting` instead of a mismatch) and a lineage-shift table used only
  for binaries linked without symbols. `scripts/nps5lib/patch.py`
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
- **`contrib/<date>-<hash>.kyplst`** and its **`.verified`** (optional, short-lived): lists shared by players with
  `nps5 cache share`, waiting to be folded into `pipelines.kyplst`; **`pipelines.contrib.tsv`**: one provenance row
  per folded contribution. See "Share your list".
- **`metal-shaders.tsv`** (optional, v1.1): KEYS ONLY of the compute kernels verified with our own Metal translation
  (Kyty's MSL back end) instead of KosmicKrisp's. Each row: program digest, specialization key, KosmicKrisp MSL key, sha256
  of our MSL, local size, evidence tag. No shader code: `nps5 cache prepare` generates the kernels on the player's Mac from
  the player's dump and keeps one only when it hashes to the listed sha256 (scripts/nps5lib/metal.py; `NPS5_METAL_SHADERS=0`
  turns it off). `scripts/check.sh` accepts the file only as hashes, header lines and prose comments.
- **`metal-gfx-pairs.tsv`** (optional, v1.1): the same for graphics, VERIFIED (vertex, fragment) PAIRS. Each row: program digests and
  specialization keys of both stages, KosmicKrisp's MSL key of both stages, sha256 of both of our texts, variant, and three short
  number fields the emitter needs (descriptor offsets, vertex attributes, colour mask), and since format 2 (v1.1.1) the
  blend column (KosmicKrisp blends in the fragment shader: numbers per colour attachment) and the program record's push-data
  cursor after a spec key (`<spec>:<cursor>`). No shader code: `nps5 cache prepare` generates both stages on the player's
  Mac and keeps the pair only when BOTH texts hash to the listed sha256. A list with only the header lines means no graphics
  step and KosmicKrisp's own pipelines. `scripts/check.sh` accepts the file only in that form, and a list with pair rows only
  with its `metal-gfx-pairs.tsv.verified` record (sha256 of the list + result line, like `pipelines.kyplst.verified`).
- **Per-GPU-class rows** (v1.2.1; keys format 2, pairs format 3): each row also names the emitter mode (`kyty_msl_dump --mode`),
  the GPU class (`*` every Mac, `g16` M4 family, `g18` M6 family; class = the GPU generation from the IORegistry + the macOS
  major), the pass rule and the verdict. `nps5 cache prepare` uses the most specific row for your Mac, then `*`, then
  KosmicKrisp. A class row ships only when it was validated on every pipeline of its key AND timed faster on that class.
  A list with class rows needs its `.verified` record (`metal-shaders.tsv.verified`, `metal-gfx-pairs.tsv.verified`) with the
  per-class reproduction counts.
- **`fast-math-deny.txt`** (optional, v1.1.2): 64-bit guest shader hashes, one per line, plus comments; nothing else
  (`scripts/check.sh`). `nps5 run` passes it as `KYTY_FAST_MATH_DENY` on the Kyty back end: these shaders keep exact math
  at every `KYTY_GPU_ACCURACY`, which keeps KosmicKrisp's keys (and the verified texts) of the shaders the Metal lists replace.
- **`promotions.tsv`** (optional, profdrift; [../docs/promotion.md](../docs/promotion.md)): the register of validated or rejected words, `title<TAB>word=value<TAB>verdict<TAB>evidence<TAB>decided<TAB>status<TAB>owner[<TAB>note]`; `python3 -I scripts/nps5lib/profdrift.py check <id>` compares it with the composed profile. Names, values and tags only.
- **`metal-guests.tsv`** (optional, ovguard): `format<TAB>nps5-metal-guests<TAB>1`, then `cs|fs|vs<TAB><KosmicKrisp key><TAB><guest shader hash>` rows, hashes only.
  The invariant `scripts/nps5lib/ovguard.py check` enforces (CI, `.nps5-check-tests`): while the profile gives shaders fast math (`KYTY_GPU_ACCURACY` normal or low,
  `KYTY_FAST_MATH>=1`; the vertex stage only with `KYTY_FAST_MATH_VS=1` or `KYTY_FAST_MATH>=3`), every override row's guest hash is in `fast-math-deny.txt`, because fast
  math changes the SPIR-V and so the key the Metal override is looked up under (a miss logs nothing). Make the sidecar with `ovguard.py sidecar-xf games/<id> <kyty_msl_dump
  --programs ... --xf-keys output>`; `check --strict` (the release gate) also fails on a missing sidecar and on codegen words no deny list covers. `nps5 run` refuses a
  `KYTY_FAST_MATH_DENY` that is unreadable or empty under fast math (`KYTY_FAST_MATH_DENY_OPTIONAL=1` accepts it on purpose).
- **`emitter`** line of both Metal lists: the Kyty commit and release tag the hashes were made with (components.lock pins
  the same commit). When texts do not reproduce, `nps5 cache prepare` names the Kyty it found and exits 3.

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
2. `scripts/check.sh` accepts a `.kyplst` only as `games/<title id>/pipelines.kyplst` or `games/<title id>/contrib/<date>-<8 hex>.kyplst`, only if it parses (known
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
the KosmicKrisp pipeline archive and a private OS Metal cache. With KosmicKrisp patch 0020 it runs twice: pass 1 collects every shader's MSL (`KK_LIBCACHE_DIR=<cache>/kk-libcache`), `scripts/kk-libcache-build.sh` builds it into Metal libraries, pass 2 prepares again from an empty cache with those libraries in place (a pipeline archived from a source-compiled library never hits when the shader is later loaded from a prebuilt one); runs then load libraries in 0.3 ms instead of compiling them (`--single-pass` skips pass 2). After pass 2 the prepare makes two short normal starts (warm starts): the window's own helper pipelines (present and overlay blits, two kernels) exist only once a real window opens, so no list holds them; start 1 compiles them and collects their MSL, the library build follows, start 2 archives them from the libraries. Without them the player's first two starts compile 5-6 of these while the window opens (`--no-warm` skips them).

The prepare's window shows the loading card ("PREPARING SHADERS X / T") from its first frame until the pass ends, and
the terminal prints the same count every few seconds (`prepare: pass 1 of 2: Pipeline replay: 512/1544 (33%) ...`); the
Metal library build between the passes has no window and says so. `nps5 run` uses the prepared cache of the current
stack by itself (cache folder, Metal cache id, the list, the launch phase). Without a prepared cache the same launch
phase builds the whole list on that start, behind the card, and `nps5 run` says so before it starts; the window never
sits blank (`NPS5_RUN_SHADER_CACHE=0` or an explicit `cache=` arm turns this off; `KYTY_PIPELINE_LIST_PREPARE_CARD=0`
is the old prepare without a card).

**Nothing compiles while you play.** A normal run on the Kyty back end starts with a launch phase behind the loading
card ("PREPARING SHADERS X / T"): every pipeline of the list and of your own journal is created again before the game
starts (`KYTY_PIPELINE_REPLAY_ALL=1`), together with the renderer's own tile/detile helper pipelines
(`KYTY_HELPER_PREWARM=1`; they are in no list, so the prepare builds them too). On a prepared cache each one is a
cache hit (ASTRO BOT: 1,526 pipelines in about 3 s); a pipeline the caches lost since (an OS update, an archive
segment that was never written, a session closed before the last save) compiles there instead of at its first draw.
The prepare waits for KosmicKrisp's archive writer before it ends, so the pipelines compiled last are kept. Compile
workers (prepare, launch phase, the metallib build) number the logical cores minus 4, at least 2
(`NPS5_COMPILE_THREADS=<n>` overrides), and run at utility QoS so the Mac, the sound and the game stay responsive.
What still compiles in a session: shaders and pipelines of places nobody has recorded yet; they are in your journal
afterwards (`nps5 cache share`), and compile at launch from then on. `nps5 cache check` runs with the launch phase.

The game is held, not the window: the card counts "PREPARING SHADERS X / T" (a count, never a time) and the game's
first draw waits until X == T (`KYTY_PIPELINE_REPLAY_ALL_HOLD=0` lets the phase run in the background instead).
The settings dialog (hold Option at launch) has **Shader preparation at launch**: *All shaders before the game*
(default, `shader_prepare=all`) or *Next scene first, the rest in the background* (`shader_prepare=scene`;
`NPS5_SHADER_PREPARE` overrides). With *scene* the hold covers the pipelines recorded in the scene the last session
ended in (remembered in `<cache>/<title id>.pipelines.last_scene`; a `-lvl` start names it itself), those recorded
before any scene opened and the profile's `shader_prepare_always` scenes (menus, world map, hubs); the others are
built behind the game on the same low-priority workers. Recorded pipelines carry the scene they were first needed in
(`nps5_vfs_current_scene`, profile keys `scene_open_key` / `scene_tag_skip`). A start whose scene has no recorded
pipeline falls back to *all*.

The scene tags travel with the list: a recording (`nps5 cache record`) made with a runtime that names the current scene
writes the tag into each journal record, `kyty_pipeline_list export` keeps it and `merge` unions the tags of all
recordings; `nps5 cache merge` prints them ("scene tags: 94 pipeline tag(s) in 3 scene(s): ..."). A list without tags
(recorded by an older runtime) still works; *scene* then prepares everything, like *all*.

Six helper pipelines (the window's present and overlay blits, two compute kernels) only exist once a window is up, so
the prepare passes cannot build them; the two warm starts at the end of `nps5 cache prepare` do. After a prepare with
`--no-warm` the first starts compile them while the window opens (about half a second, before the first frame); later
starts take them from the caches.

### Share your list

`scripts/nps5 cache share <title id> [--from DIR ...] [--pr]` turns the pipeline journals of your own runs into a contribution:

1. It finds the journals under `<scratch>/cache/<title id>/` (or the `--from` folders), exports each against your copy of the game and merges them (`kyty_pipeline_list export` / `merge`).
2. It compares with the shipped `pipelines.kyplst` and prints your list, the shipped list and **NEW** in the `.verified` record's wording: `shaders N (vertex, pixel, compute), permutations N, pipelines N`. Vertex includes what Kyty treats as primitive / NGG vertex stages; a pipeline entry is its stages, the Vulkan state and the layout signature. Zero new: nothing is written.
3. A list is for **one title and one app version** (`title` and `version` in its header, e.g. `PPSA21564 01.007.000`). Your journals must be from the version the shipped list is for; another version is refused and gets its own list.
4. It writes `games/<title id>/contrib/<date>-<8 hex>.kyplst` and runs the no-game-code proof on it (the verifier reads your game and the journals; `.verified` next to it) and the rules of `scripts/check.sh` (one zstd frame per section, size cap, no payload, record for these bytes). The header names the title, version, nps5 commit and machine class only. Nothing it prints or writes contains your user name, home or game paths.
5. It prints the next steps and the text for the pull request or the "Shader list" issue. `--pr` runs `gh repo fork`, `git switch -c`, `git add` / `commit`, `git push` to your fork and `gh pr create` after showing the plan and asking `[y/N]`; without a yes nothing is pushed or created, and it needs `gh auth login`.

The contribution is your own merged list, so entries the shipped list already has are inside it; the NEW line counts the difference, and the maintainer merge removes the overlap.

**Maintainers.** After checking a contribution (rerun `scripts/verify-pipeline-list.sh` on it with your game copy), fold it in:
`scripts/nps5 cache merge <title id> --contrib games/<title id>/contrib/*.kyplst --write`. It refuses a file without a valid `.verified` or for another title or app version, merges (duplicates are dropped), writes `pipelines.kyplst`, removes its `.verified` (run `nps5 cache verify-list <title id>` and commit the new record), and appends one row per contribution to `pipelines.contrib.tsv` (date, hash prefix, the contribution's own counts; no names). `git rm` the folded `contrib/` files in the same commit; folding the same file again adds no second row.

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
