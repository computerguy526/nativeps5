# Changelog

Changes across the four repositories (this one, `nativeps5-recomp`, `nativeps5-kyty`, `nativeps5-anyps5`); the
pinned commits are in [components.lock](components.lock).

## v1.4 (release candidate, not released)

Draft: every number below was measured on a release-candidate build (M5 Pro, ASTRO BOT, the profile settings `nps5 run` composes); numbers marked PENDING are not claims yet.

**Update:** all of `nativeps5-kyty`, `nativeps5-recomp` and `nativeps5-anyps5` move to `v1.4.0` (see [components.lock](components.lock)). Pull, check out the tags,
rebuild (`scripts/build.sh`) and **rebuild KosmicKrisp** (`scripts/build.sh kosmickrisp`: patch 0041, `nps5_kk_archive_flush`, is new; 41 patches), then
start the title. The first start on a v1.3.x cache prepares it once behind the loading card (see below); later starts are warm.

- **Astro Bot: an upgraded shader cache is prepared once, then stays warm.** A cache left by v1.3.x holds entries for the old shader code
  that only take room (the old replay limit stopped the pipeline replay after 85 builds and left 4,772 pipelines to compile in play). v1.4 stamps
  the driver cache with the shader-code version. The first start on an older cache sets it aside, shows the loading card, and rebuilds it from the
  pipeline list until it is "prepared". Measured on a clone of a v1.3.x play cache (cold OS Metal cache): three runs, each cut at 175 s, reached
  "prepared" (0 failed, 0 beyond the cache limit), the set-aside copy was removed, and the next start was warm: 0 pipelines compiled in play and 2,207 libraries
  loaded in 0.4 s. The driver cache cap is 1,024 MB with a replay limit of 896 MB. `KYTY_CACHE_UPGRADE=0` turns the upgrade off. A cache is
  re-prepared only when the shader code or a switch that changes it changes (render scale, MetalFX, frame cap, window and QoS settings do not).
- **Astro Bot: pipeline list replaced** by a list of 1,090 shaders, 1,457 permutations and 2,456 pipelines (the 4,892 entries of the
  v1.3.1 list were pairs that differ only in state the device decides; 2,446 were unique). Keys and hashes only; its no-game-code record is
  `games/PPSA21564/pipelines.kyplst.verified`. On the release candidate the pre-build replays 1,505 shader permutations (0 skipped, 0 failed) and 2,431 pipelines (0 failed).
- **Astro Bot: 25 more Metal rows for skinned-character and foliage shaders** (`g16` 12, `g17` 7, `g18` 6; graphics list 455 to 480 rows). Each is exact against KosmicKrisp on every pipeline of its key and was timed faster on that GPU class in every run (B/A 0.75-0.99 on the class; the numbers are in the list). They take effect after `nps5 cache prepare` regenerates the Metal rows from your own program cache. PENDING: in-game frame time on the shipped composition (the rows are per shader pair, not an end-to-end claim).
- **Astro Bot: 110 Metal rows removed again** (graphics list 480 to 370 rows). A check of every row against the real-input level captures (not only Sky Garden) found output differences beyond the row's rule on 109 of them (76 on a real captured snapshot, 33 on sampled inputs only) and one row (`g18`) that is no faster than KosmicKrisp. Those shaders keep KosmicKrisp's own code, as before the rows existed. The 25 rows of the line above are 21 now. Hashes: `tests/data/valfix_dropped.tsv`.
- **Astro Bot: `NPS5_QOS_GPU=rt` is on by default** (the GPU-feeding threads at real-time priority). Sky Garden on an M5 Pro, release candidate, warm cache:
  mean frame time 36.9-37.2 ms at about 100 % game speed with the shipped settings. A/B/A of the switch alone: 36.5 / 39.0 / 37.9 ms (with / `NPS5_QOS_GPU=off` / with;
  -4.6 % against off on the mean of the two). Off with `--env NPS5_QOS_GPU=off`. PENDING: rerun on the final release libraries.
- Upstream fixes taken (neutral in the Astro A/B): memory log no longer prints clamped buffer ranges (KytyPS5 #2540), `V_CMPX_NGE_F16` is decoded (#2532).
  The IPO/PGO build switches exist and are off by default.

**Known issues (v1.4):**
- With `NPS5_QOS_GPU=rt` the guest fault cost per frame is higher (about 8-13 ms against about 2 ms in the Sky Garden A/B/A); the frame time is still lower.
- KosmicKrisp patch 0041: a failed archive write reports success, so with `KYTY_PREPARE_FLUSH_SYNC=1` the fallback wait is skipped; the switch stays off.
- Closing the window can hit the 5-second save timeout, leaving about 20 Metal archive pipelines unflushed (they compile again next time).
- The first start after an update is longer (the one-time cache preparation).
- `KYTY_SAROS_PROGRAM_CACHE=1`: per-launch cost not measured.

- **SAROS (`PPSA07631`): pending SAROS verification run.** The v1.4 Kyty carries the generic indirect-image path, the Nanite raw raster
  parameters and the shader-bundle compaction, and `nps5 run PPSA07631` now starts (the preset's `KYTY_EOP_TIMESTAMPS=gpu` is overridden to
  `cbtime`, as for ASTRO BOT; before, the safety check refused the run) and refuses to run without the Unreal console-variable list
  `NPS5_UECVAR_LIST` (without it the profile's cvar fixes silently did not apply). Whether SAROS is playable on v1.4, and at what frame
  rate, is not yet verified: the status in the README and the game note stays "not playable" until a run on the merged tip says otherwise.
- **Cronos: The New Dawn (`PPSA24096`)** had the same refusal and gets the same override.
- **Switches removed from the docs:** `KYTY_SAROS_HS_DYNIDX`, `KYTY_SAROS_ASYNC_CS` and `KYTY_SAROS_ASYNC_CS_WAIT_MS` do not exist in v1.4.
  `KYTY_SAROS_PROGRAM_CACHE=1` (hand-shader and Nanite-raw programs kept in the program cache; default off, so those programs recompile on
  every launch) works again. `KYTY_HTILE_PREFIX_REBIND=1` gates an upstream depth-array change (default off until an ASTRO and SAROS
  regression has run).

## v1.3.1 (hotfix)

Everything in v1.3.0. An ASTRO BOT fix for the shader pre-build, a larger pipeline list and honest SAROS text. **Update:** `nativeps5-kyty` moves to the new tag
(`v1.3.1`; see [components.lock](components.lock)), `nativeps5-recomp` and `nativeps5-anyps5` stay at v1.3.0 and v1.1.2: pull, check out the Kyty tag, rebuild Kyty
(`scripts/build.sh kyty`), and run `nps5 cache prepare PPSA21564` once. No new translation of the title is needed.

- **Astro Bot: `nps5 cache prepare` builds the whole list again (Kyty).** On the v1.3.0 Kyty a prepare built only about 300 of the list's pipelines: the list stores each shader permutation with its resource
  specialization, and v1.3.0 changed that encoding (an extra field per image slot), so 560 of 1,382 permutations were skipped and 4,678 pipelines found
  no shader modules; the shaders then compiled during play. Kyty now reads the old layout, re-encodes it in the current one, and logs the skip reasons.
  The same prepare on a fresh cache now compiles 1,185 permutations (197 already present), skips none, and builds 4,844 of the 4,892 listed pipelines
  (14 already known, 34 whose shader module is not published, 0 failed).
- **SAROS (`PPSA07631`) is not playable on v1.3.x, and the v1.3.0 text that said it was is withdrawn.** With the hand shaders on it stalls at about 1 fps; with them off the
  background is missing. No switch combination on the v1.3.0 Kyty fixes both, so v1.3.1 changes no SAROS setting. Support is coming in v1.4. The SAROS note and the
  README Games table now say so (the "about 19 fps" figure came from development builds).
- **Astro Bot: pipeline list extended** with the play sessions since v1.3.0 and the all-levels warm-up recordings: 1,382 shader permutations (1,086 shaders) and 4,892 pipelines (v1.3.0 shipped 1,161 permutations /
  2,381 pipelines). Keys and hashes only, with its no-game-code record (`games/PPSA21564/pipelines.kyplst.verified`). `nps5 cache prepare PPSA21564` builds
  about 4,850 of them (plus 123 compute pipelines) before the first start, so far fewer shaders compile while you play. The first prepare is longer than before
  (it builds the whole list, not a few hundred pipelines); later prepares reuse what is built.

**Known issues** (added to the v1.3.0 list)
- SAROS (`PPSA07631`) is not playable on v1.3.x (stalls at about 1 fps with the hand shaders, missing background without them); support is coming in v1.4.
- A rare crash at startup inside the game's own static initialisation (`fn_64ed360`, a null-guard fault in its logging setup). It was also seen on
  builds before v1.3.0, so it is not a v1.3 regression; the trigger is unknown. Relaunch: it does not repeat on the next start.
- The Metal compiler-service crash on a fully cold shader cache (ASTRO BOT) is still possible: run `nps5 cache prepare PPSA21564` first, or relaunch.

## v1.3.0 (2026-11-24)

One line for every title. The development branches that ran SAROS (`PPSA07631`, Unreal Engine 5.5) are now part of the release line, so
Astro Bot, God of War Sons of Sparta and SAROS build from the same four repositories and the same KosmicKrisp. Everything in the v1.2.5
candidate below is included. **Update:** all three component repositories move to new tags (see [components.lock](components.lock)): pull,
check out the tags, rebuild everything (`scripts/build.sh recomp`, `kyty`, `anyps5`, `kosmickrisp`: patches 0031-0040 are new, 40 patches,
`EXPECTED_TREE` ae17f099). **The first start after the update rebuilds the shaders once** (Kyty's shader recompiler sources changed, so the
program cache and the pipeline cache of every title get a new key); run `nps5 cache prepare <id>` or expect one longer first launch.
- **SAROS (`PPSA07631`, experimental) boots and plays from the release line.** Kyty decodes the flat-pointer shader operands UE5.5 uses
  (scalar sources `0xEB`-`0xEE`), lowers 64-bit atomics, and carries the SAROS hand shaders for its bindless lighting passes
  (`KYTY_SAROS_HANDSHADERS`; the `HS_DYNIDX` canonical-group switch of that release was not carried into v1.4), the Nanite raster parameter path (`KYTY_SAROS_NANITE_RAW`), the
  shader-bundle compaction (`KYTY_BUNDLE_COMPACT`) and the command-processor switches found by the SAROS experiments. The recompiler has
  the packed-code discovery (`NPS5_SEED_AFTER_UD2`), `VFMADDSUB`/`VFMSUBADD`, and the SDK 11 shims the title imports. KosmicKrisp gains
  opt-in 64-bit image and buffer atomics (`NPS5_KK_IMAGE_INT64`, `NPS5_KK_BUFFER_INT64`), the switch tree (`NPS5_KK_SWITCH_TREE`) and the
  residency lock. Everything is off unless the `PPSA07631` profile (or the environment) sets it.
- **No behaviour change for other titles by default.** Every title-specific switch reads as off when unset; a build gate
  (`tests/one/run_one_tests.sh` in `nativeps5-kyty`) checks the switch defaults and that the library exports every graphics entry Astro
  Bot, SAROS and Sons of Sparta import. POSIX `pthread_exit` takes the full thread-exit path only with `NPS5_PTHREAD_EXIT_SCE=1` (set by
  the `PPSA07631` profile).
- **MetalFX for Unreal titles (opt-in, experimental).** `KYTY_METALFX_UE=log|fsr3` places MetalFX temporal in the engine's third-party
  upscaler slot; `KYTY_METALFX_PASS=<hash>:blit` uses MetalFX spatial at a full-screen upscale draw. Off by default.
- **Astro Bot: flower jitter fixed (physics).** The world step is clamped instead of split, and the per-object dt clamp covers plants, springs,
  ropes and soft bodies. On by default for `PPSA21564` (profile `env`: `NPS5_PHYS_SUBSTEP_WORLD_CLAMP=1`, `NPS5_PHYS_DTCLAMP_OBJ=all`); the code
  stays off for every other title. The clamp table (`profiles/funcrepl/PPSA21564-physsub.json` in `nativeps5-recomp`) wraps six verified
  physics functions (BigLeafPlant, SpringBone, ApRope, ApRopeB, ApSoftBody, ApSoftBodyBall) and a test keeps table and runtime slots in step;
  programs must be translated again (`nps5 build`) to get it.
- **gamepack: pack a title to save disk space.** `scripts/gamepack.py` packs a title's large files; packed titles are detected automatically
  by the runtime (`NPS5_VFS_SEGFILES=0` forces it off). See the README, "Save disk space: pack a title".
- **SAROS profile completed.** `profiles/PPSA07631.json` now sets `KYTY_SAROS_HANDSHADERS`, `KYTY_SAROS_NANITE_RAW` and `KYTY_BUNDLE_COMPACT` (the test
  runners used to pass them) and keeps the approved `r.LightFunctionQuality=1` look. New game notes: [SAROS](games/PPSA07631/README.md) (setup, settings,
  known issues) and [Cronos](games/PPSA24096/README.md) (bring-up status only: boots, warm cache, no profile, the pipeline-list tool cannot list
  Unreal shaders, footprint guard 18432 MiB). The README Games table lists both.
- **Mip-count clamp keeps the guest layout (Kyty, `KYTY_CLAMP_MIP_TO_EXTENT`, on by default).** A texture declaring more mip levels than its size
  allows is clamped on the host image and views only; the declared layout (slice stride of array and 3D textures) is kept. The first version moved
  the stride and is the likely cause of the SAROS fireball artefacts. Legal textures (all of ASTRO BOT's) are unchanged.
- **Three upstream KytyPS5 fixes, credited to Jetsku** (authorship kept): D16 depth copy, MSAA resolve extent, R8/R16 CMASK clear; with unit tests.
- **New reference page [large games](docs/reference/large-games.md):** the packed-file layer (`<file>.nps5seg/`, automatic), `nps5 data compress`,
  cache compression, the memory guard and which trims are unsafe for which title. `docs/env_switches.tsv` regenerated for this line.
- **Shader library cache repair no longer fails silently on large caches (AnyPS5 `tools/kk-metallib/build_metallib.sh`).** With more than about 8000 collected
  shader texts the file glob overflowed the argument limit (exit 126, nothing built); it now uses `find | xargs -0`. `nps5 cache prepare` uses its own
  `scripts/kk-libcache-build.sh` and was not affected.
- **Builds target macOS 26.0.** `scripts/build.sh` and `kosmickrisp/build-kosmickrisp.sh` set `MACOSX_DEPLOYMENT_TARGET=26.0` unless you set it, and
  fail when a built binary needs a newer macOS (`scripts/check-minos.sh`). A KosmicKrisp built on macOS 27 without it dropped an
  `@available(macOS 27)` guard and crashed on macOS 26 (`-[MTLTextureViewDescriptor setMinLOD:]`).
- **Optional shader-cache compression.** `NPS5_CACHE_COMPRESS=1` makes `nps5 cache prepare` APFS-compress the finished cache (about 2.5x smaller; off by default).
- **AnyPS5: upstream fixes** (13 shader-recompiler fixes for the legacy AnyPS5 GPU path, and the macOS libc arena hint placement, which
  every title uses). The AnyPS5-path pipeline journal is rebuilt once.
- Diagnostics (all off by default): compute and draw probes, bind logs, raw target and BC dumps, the guest-write journal, the input
  profile capture, `NPS5_CRASH_REGDUMP`, `NPS5_TRACE_JOIN`, `NPS5_VFS_RANGELOG`, `NPS5_VFS_HOLE_CHECK`.

**Known issues**
- A fully cold shader cache can crash inside Apple's Metal compiler service while thousands of pipelines build at once (ASTRO BOT, seen at
  about 30 s and 140 s on a fresh cache). Relaunch the title; it was not seen with a warm cache.
- SAROS is experimental and slow (about 19 fps), and stutters while a cold shader cache fills.
- Cronos: The New Dawn (`PPSA24096`) is in bring-up and not supported.
- No online features, no trophies.

## v1.2.5 (not released; part of v1.3.0)

Everything in v1.2.4. Small, switched-off-by-default additions for Unreal Engine 5 titles (SAROS, `PPSA07631`, still experimental), one
Astro Bot crash fix and one display-buffer fix. **Update:** `nativeps5-recomp`, `nativeps5-kyty` and `nativeps5-anyps5` move to the new tags (see
[components.lock](components.lock)): pull, check out the tags in `../ps5recomp`, `../kyty` and `../anyps5`, rebuild (`scripts/build.sh recomp`,
`kyty`, `anyps5`). Astro Bot needs no new `nps5 cache prepare`: no shader translation changed.

- **Astro Bot: no more crash in the space flight (AnyPS5).** A draw whose index count is larger than its `MAX_SIZE` field was refused as an
  invalid packet and the whole submission was dropped; Astro Bot 01.007 then exited (code 134, about 1 run in 8, around the 10-minute mark of
  the space flight). The real hardware simply draws `MAX_SIZE` indices; the driver now does the same (unit test added).
- **Display buffers with any clear colour (Kyty, from upstream KytyPS5 `7d0c350e`).** A compressed (DCC) display buffer is no longer rejected
  because its register clear colour is not zero.
- **SAROS integer render targets (opt-in).** `KYTY_PS_UINT32_TARGETS=1` now also covers signed-integer (SINT) colour targets; still off by default.
- **SAROS command-processor waits (opt-in).** `KYTY_PREDICATION_NO_DRAIN=2` skips waiting for the GPU before a predicated draw when nothing can
  still write the predicate; `KYTY_DCC_KNOWN_FILL_ALIASED=1` lets a recorded DCC fill of an aliased image keep the image's contents (`=verify`
  checks it). New wait counters (`gpuwait.unmap`, `gpuwait.predication`, `gpuwait.gds`) split the old `gpuwait.misc` line of the CP report.
  Both switches are off unless set; the `PPSA07631` profile sets them.
- **Video playback on VideoToolbox (opt-in, `NPS5_AVPLAYER_VT=1|2`).** The `AvPlayer` module can decode H.264 movies on the Apple video decoder
  (`2` maps the frame without a copy), with a software fallback after three decode errors in a row or when a software frame appears. Off by
  default; the `PPSA07631` profile sets `2`. No other title uses it.
- **Faster guest-write fault test (opt-in, `NPS5_SHADOW_FAULT_FAST=1`).** Checks the faulting instruction before scanning the 8192-slot shadow
  registry. Off by default.
- **`PPSA07631` profile** (the only profile touched): the runtime environment found by the SAROS experiments (`KYTY_FAULT_AHEAD_*`, GDS end-of-pipe
  deferral, eager readback pages, the switches above, and Unreal console variables set through `NPS5_UECVAR_SET`: Nanite persistent-thread
  culling and ray tracing off, Nanite raster on the vertex path). `nps5 uecvar` now lists float console variables as float (they were shown as
  integers).
- SAROS itself is still not playable from the release line: its shader decoding, hand shaders (`HS_DYNIDX`) and boot support live on development
  branches; the `PPSA07631` profile and the switches above only take effect together with them.
- Not in this release: the SAROS hand-shader work (`HS_DYNIDX`), the `FAULT_HOST_READBACK` port (already in v1.2.3.1), the KosmicKrisp switch tree,
  the larger AnyPS5 recompiler bundle (it invalidates the pipeline journal).

## v1.2.4 (2026-11-24)

Everything in v1.2.3.1. More Metal shader rows for Astro Bot (the skinned-vertex "bone" shaders and two foliage pairs) and a first
bring-up of a second kind of title: God of War Sons of Sparta (`PPSA28997`, a Unity IL2CPP player). **Update:** `nativeps5-recomp` and
`nativeps5-kyty` move to `v1.2.4` (anyps5 stays at `v1.1.1`): pull, check out `v1.2.4` in `../ps5recomp` and `../kyty`, rebuild the recompiler
(`scripts/build.sh recomp`) and Kyty (`scripts/build.sh kyty`), then run `nps5 cache prepare PPSA21564` once (new Metal rows). Astro Bot
needs no new translation: the recompiler changes are switched on only by the Unity profile, and Astro Bot's output is unchanged without it.

- **More Metal rows (Astro Bot, `PPSA21564`).** The graphics list grows from 395 to 455 rows (64 added, 4 replaced by faster ones), every
  row regenerated text-exact by `kyty_msl_dump` of the v1.2.4 Kyty (`nps5 cache metal --reproduce`: 528 of 528 over both lists: shared `*`
  36 graphics pairs + 69 kernels, M4 family `g16` 176 graphics + 2 kernels, M5 family `g17` 44, M6 family `g18` 199 + 2 kernels). New by group:
  17 skinned-vertex ("bone") rows for `g17` (5) and `g18` (12) with the emitter modes `fix6+vsperf` (+`lsel`); 4 `g18` rows replaced by the
  faster `fix6+vsperf` mode (B/A 0.93 -> 0.86-0.88); 19 `g16` bone rows within the approved edge-word rule (at most 32 differing words
  per large render target; vertex outputs within 1e-3); 8 + 14 `g16` exact bone rows; 2 `g16` foliage forward rows (`fix6+lsel`). A row is
  listed only where the text was verified and timed faster at least twice (cloud M6 for `g18`, M5 Pro for `g17`, laptop M4 Pro for `g16`);
  the per-shader B/A (microbenchmark) of the new rows is 0.66-0.99, median about 0.9 (one edge-word row has no recorded B/A, 0.000; its two M4 runs measured its vertex stage 167 -> 92 us). These are per-shader timings, not a frame-rate promise.
- **God of War Sons of Sparta (`PPSA28997`, experimental).** With `nativeps5-recomp` v1.2.4 and Kyty v1.2.4 the title boots, plays the intro
  (press Cross to skip it), reaches the title menu, a new game and the first level (the Cyclops fight); 5-minute scripted runs on an M5 Pro were
  clean at about 90 fps uncapped; the release smoke run of v1.2.4 itself (170 s, M5 Pro) reached the Cyclops level with no faults at
  about 115 fps average (p50 8.0 ms, p99 14.6 ms). Everything the Unity player needed is behind the new engine profile `profiles/engines/unity-ps5.json`
  (`"base": "unity-ps5"` in `profiles/PPSA28997.json`), so other titles are unaffected: reservation-aware `VirtualQuery`, a PRX entry
  that gets `(argc, argv, NULL)`, `_umtx_op` WAIT/WAKE, real thread stack bounds for the garbage collector, the PS5 `AvPlayer` stream-info,
  seek and looping-back warning, offline `sce::Np::CppWebApi`/save-data/audio-context answers, a socket-option map for FMOD, and a
  fault-livelock verdict for Unity's graphics worker, and the `sceKernelGetOperationMode` and `_sceLibcInternalThreadAtexit` shims
  (found by the release smoke run: without them the title stops at boot). Kyty gains 37 graphics NIDs in its route table (indexes 101-137; 0-100 are
  unchanged). Known: without a button press the Mega Cat Studios logo holds (Cross skips it); the first seconds of a start show
  1-1.5 s load hitches (guest-side asset loading); audio and a real-controller session are not verified; no shader cache is shipped.
- **SAROS character materials (opt-in, `KYTY_PS_UINT32_TARGETS=1`, default off).** A pixel export of raw 32-bit dwords to an integer (UINT)
  colour target was declared as a float output, which is undefined; Unreal Engine 5's virtual-texture page table (R32G32B32A32Uint) received
  garbage, so every virtual-texture material sampled one texel (the flat "clay" character in SAROS). With the switch such exports use the
  emitter's existing uint4 output (the first 8 changed draws are logged as `[uint32targets]`). Nothing changes without it, and no profile sets it.
- **Profiles can build on an engine profile** (`"base": "<name>"`, `profiles/engines/<name>.json`): the title's keys win, env maps merge.
- Quieter: the per-lookup unwind log line of `sceKernelGetModuleInfoForUnwind` is opt-in (`NPS5_UNWIND_LOG=1`).
- Tests: `avloop_unit` tolerates two passes in flight on a loaded host (it failed one run in three under load).

## v1.2.3.1 (hotfix, 2026-11-23)

Only `nativeps5-kyty` changes (`v1.2.3` to `v1.2.3.1`); `nativeps5-recomp` stays at `v1.2.3` and anyps5 at `v1.1.1`. Fixes a rare crash at
startup, before the first frame (`condition (!m_command.IsInvalid())` at `commandScheduler.cpp:623`): the blank frame the vblank thread
draws before the game opens video out and the loading card thread both recorded on the same command scheduler, and could overlap. The
blank frame now takes the present lock first. **Update:** check out `v1.2.3.1` in `../kyty` and run `scripts/build.sh kyty`; no translate
and no cache step (shader caches stay valid). Workaround without updating: `KYTY_LOADCARD=0`. The components lock check now accepts
four-part release tags.

## v1.2.3 (2026-11-23)

Everything in v1.2.2. Fixes for the crash when entering Sky Garden (and other level transitions), the yellow/red tint on wet Astro,
wood sinking into water, a rare freeze when a shader build never returns, and closing the game hanging; more Metal shader rows, now
also for mesh pipelines. **Update:** nativeps5-recomp and nativeps5-kyty move to `v1.2.3` (anyps5 stays at v1.1.1): pull, check out
`v1.2.3` in `../ps5recomp` and `../kyty`, rebuild the recompiler (`scripts/build.sh recomp`), **translate the title again**
(`nps5 build`: the file-read fix and the physics defaults are in the runtime linked into the translated program), rebuild KosmicKrisp
(`scripts/build.sh kosmickrisp`: patches 0029 and 0030 are new, 30 patches, `EXPECTED_TREE` 5238cb05) and Kyty (`scripts/build.sh kyty`),
then run `nps5 cache prepare PPSA21564` once (new Kyty, new Metal rows, bigger pipeline list).

- **Level-transition crash fixed (file reads, nativeps5-recomp, needs `nps5 build` again).** Entering Sky Garden could crash in the
  level loader (`RoomLoad`), sometimes after a few "texture without a name" lines. The cause was a short file read: the game reloads a
  160 MB model file into a buffer whose pages Kyty's write watch was still closing, the runtime's file read gave up after 64 retries of
  the kernel copy, and the game accepted the incomplete buffer and parsed stale bytes. A read now finishes through a 4 MiB host buffer
  when the retries run out, so the game always gets the whole file (`[kio] ... read through a host buffer` in the log when it happens;
  `NPS5_KIO_BOUNCE=0` = the old behaviour, for A/B only). Confirmed in play sessions: the Sky Garden entry that crashed before loads.
  The two earlier guards for the same crash stay as a second line: a model whose texture has no name no longer crashes a level
  transition, and the Havok world lock is taken again per physics sub-step (fixes `Mutex.cpp:418` asserts).
- **No more yellow/red tint (or stripes) on wet Astro (Kyty, on by default, `KYTY_GBCOPY_REMAP=auto`).** The game copies its G-buffer
  into an image allocated at the native render size (1920x1080, or 2432x1368 for the second dynamic-resolution level), while Upscaling
  720p/900p and the 540p render scale make the G-buffer itself smaller; the wet and other decals then read the copy at the wrong
  place. Kyty now gives that copy the scaled size, taken from the translated program's own Upscaling / Render scale settings (one
  `[decalfix]` line per size at start; nothing changes with Upscaling off and Render scale 720p). Checked by eye in play sessions at
  540p and 720p: no tint, no stripes, the wet darkening and sheen remain. A stopgap until the copy's size is patched in the game
  profile. Off: `KYTY_GBCOPY_REMAP=0`.
- **Wood no longer sinks (physics, needs `nps5 build` again):** world sub-steps are at most 1/25 s and the world lock is held across
  them by default (the "feel the wall" gate stays fixed).
- **A rare freeze when a shader build never returns is fixed (Kyty, on by default).** A draw now waits at most 8 s for a pipeline
  build that is stuck in the driver, then is skipped until the build finishes (`KYTY_PIPELINE_TAKE_TIMEOUT_MS=8000`; seen once, in a
  long session under heavy memory pressure).
- **Closing the game always exits (Kyty, on by default).** The pipeline cache save at close gives up after 5 s instead of waiting for
  ever (`KYTY_CLOSE_SAVE_TIMEOUT_MS=5000`). Both guards: `0` = the old behaviour. With the flight recorder on, its dumps now backtrace
  every thread (`KYTY_FLIGHTREC_ALLTHREADS=1`).
- **Write faults without a GPU wait (Kyty, on by default, `KYTY_FAULT_HOST_READBACK=1`).** When the CPU writes to bytes the GPU produced
  and every GPU writer of them has finished, Kyty copies them on the host instead of waiting for the whole GPU backlog. In one Snowy
  Canyon A/B the time the command processor spent in these drains fell from 7.7-10.9 s to 3.3 s per 10 s, but reads of GPU-written pages
  waited longer and the frame rate did not change (within run-to-run noise). Fewer CPU stalls on these faults; no frame-rate gain was
  measured. Off: `KYTY_FAULT_HOST_READBACK=0`.
- **More Metal rows, now also for mesh pipelines.** The lists carry 468 rows, every one regenerated text-exact by `kyty_msl_dump`
  of the v1.2.3 Kyty (`nps5 cache metal --reproduce`: 468 of 468): shared rows (`*`, every Mac) 36 graphics pairs + 69 kernels,
  M4 family `g16` 133 graphics rows (5 of them mesh pipelines) + 2 kernels, M5 family `g17` 39 graphics rows (2 mesh), M6 family
  `g18` 187 graphics rows (2 mesh) + 2 kernels. New: 50 g16 rows from an overnight M4 Pro timing pass (8 of them faster replacements),
  the G-buffer pairs 47d07a80 (g16, g18), the forward pair 3742e841/c3e52ec8, the fullscreen / post / foliage-forward pairs 705290fd,
  8d47e44f, 9b8c69da, 69ca3b3c (per class), two lighting kernels on g16, and 9 mesh-pipeline rows (`mesh-twin`: our mesh-stage text plus
  the twin fragment shader; KosmicKrisp patch 0029 lets a mesh pipeline be replaced, 0030 gives a replacement the device's mesh
  scratch for its LDS). As before, a class row is listed only where the text was verified exact (or within a rule the maintainers
  approved) on captured inputs and timed faster on a Mac of that class. The timings are per shader (microbenchmarks); a title A/B of
  the mesh rows was within run-to-run noise, so the rows are for coverage, not a frame-rate promise.
- **Pipeline list: 1,161 permutations / 2,381 pipelines** (v1.2.2: 989 / 1,752), folded from play sessions and level captures on the
  M4 Pro and an M5 Pro (keys and hashes only, `pipelines.kyplst.verified`): fewer shaders compile while you play after `cache prepare`.
- **KosmicKrisp: 30 patches** (0029 mesh-pipeline replacement, 0030 mesh scratch for replacements).
- Known: SAROS is not playable yet. Two shader sets that the maintainers play with (foliage forward rows and Astro's skinned-vertex
  rewrite) are not in the public lists: their texts are not made by the public Kyty generator yet (planned for v1.2.4).

## v1.2.2 (2026-11-21)

Everything in v1.2.1. Metal shaders now match on every Mac, a bigger list of faster Metal shaders (M4, M5 and M6 families),
an automatic 720p / 540p render scale, a fix for flickering leaves, and four more Kyty options on by default. **Update:** nativeps5-recomp and nativeps5-kyty move to
`v1.2.2` (anyps5 stays at v1.1.1): pull, check out `v1.2.2` in `../ps5recomp` and `../kyty`, rebuild the recompiler
(`scripts/build.sh recomp`), **translate the title again** (`nps5 build`: the render-scale controller is in the runtime that is
linked into the translated program), rebuild KosmicKrisp (`scripts/build.sh kosmickrisp`: patches 0027 and 0028 are new) and
Kyty (`scripts/build.sh kyty`), then run `nps5 cache prepare PPSA21564` once (new Kyty, new Metal rows).

- **Leaves no longer flicker (physics, on by default, needs `nps5 build` again):** v1.2.1's world step split (the fix for the crash
  at the "feel the wall" gate) left the LAST sub-step's dt / n and n / dt as the world's step dt and in the Havok solver info, so
  code that reads them once per frame (the leaves and other debris) saw one step of dt / n for a frame that advanced dt, and the
  leaves flickered. After the last sub-step the shim now writes the frame's dt and 1 / dt back (three parameters in
  `profiles/funcrepl/PPSA21564-physsub.json`). Checked by eye in play sessions (leaves clean, the gate opens, no crash, 26 thousand
  split world calls in one session). Off: `NPS5_PHYS_SUBSTEP_DTFIX=0`; `[physsub] world dtfix on` is in the log.
- **Metal shaders now match on every macOS and GPU.** KosmicKrisp's generated MSL, which the override keys hash, differs between
  macOS 26 and 27 and between GPU families (M4 and M5/M6); v1.2.1's replacements therefore matched only on macOS 26 with an M3/M4
  (on an M6 with macOS 27: 0 of 97 graphics pairs and 0 of 71 kernels were found). KosmicKrisp patch 0027 (`KK_MSL_CANON_KEY`)
  also keys every shader by the text a macOS 26 / GPU family 9 Mac writes (the Mac the lists were made on); `nps5` turns it on by
  default (`NPS5_METAL_CANON=0` = off), and `cache prepare` prints how many listed kernels and pairs match on this Mac. A no-op on
  a Mac that already writes the canonical text.
- **More and faster Metal rows, per GPU class.** The lists now carry 402 rows, every one regenerated text-exact by the public
  `kyty_msl_dump` of Kyty v1.2.2 (`nps5 cache metal --reproduce`: 402 of 402): shared rows (`*`) 36 graphics pairs + 69 kernels,
  M4 family `g16` 82 graphics rows, M5 family `g17` 34 graphics rows (new), M6 family `g18` 179 graphics rows + 2 kernels. New
  are depth-only vertex rows, the foliage rows (vertex-load grouping and constant-load modes), a prepass row and the
  fullscreen-pass rows. A class row is listed only where the text was verified exact on the game's captured inputs and timed
  faster on a Mac of that class. Coverage of Sky Garden's measured GPU time per second (700.7 ms/s of shader time):
  g16 51.8 % covered, 50.1 % of it by a text timed faster (before the last g16 rows: 41.1 % / 39.4 %); g18 45.6 % / 31.6 %; g17 25.2 % / 8.3 %
  (first rows; the shared rows were not timed on an M5). In a frame-time test an earlier 60-key g16 candidate of this list made no
  measurable difference (ABA, moving Sky Garden, 55.1 / 51.8 / 50.6 ms mean, within the run-to-run spread): the list is for
  coverage and correctness, not a frame-rate promise.
- **M5-family Macs (GPU g17) are recognised:** `nps5` picks the `g17` rows only on an M5 (tested on a cloud M5 Pro, macOS 27.0,
  where the canonical keys match as on the M4 Pro, 24 of 24 in a smoke test).
- **Automatic render scale (720p / 540p):** nativeps5-recomp's ASTRO BOT patch profile gets a `render_scale` setting (`auto`
  default, `p540`, `p720` = v1.2.1) and the runtime a controller that switches the game's own dynamic-resolution level from the
  frame time (540p while frames take longer than 33 ms, 720p with headroom; hysteresis, 20 s dwell). New patch-profile site
  operations `override` and `data32` (live settings). `NPS5_RENDER=auto|720|540`, `NPS5_RENDER_LEVELS`, `NPS5_RENDER_TARGET_MS`.
  Needs `nps5 build` again. Measured on an M4 Pro (same binary, warm, moving Sky Garden): 720p 53.5 / 56.4 ms mean frame time
  vs auto 50.3 / 46.3 ms, p99 88.5 / 96.5 vs 82.1 / 77.3 ms, game speed 92 / 86 vs 99 / 100 %; no stutter at 6 forced switches
  (the worst frame within 2 s of a switch 63-117 ms vs 60-168 ms in the 4 s before), no shader compile or render-target
  allocation at a switch. A `[rscale]` line per switch (with the worst frame around it) is in the log.
- KosmicKrisp patch 0028 (upstream Mesa 0db4e29): allocations beyond 95 % of the recommended working set fail with
  `VK_ERROR_OUT_OF_DEVICE_MEMORY` instead of a Metal kernel panic on macOS 26 (workaround 20).
- **Fewer shader compiles in game:** pipeline list refreshed from a 31-minute play session of the clean-room build: 962 -> 989 shader permutations, 1,739 -> 1,752 pipelines (920 shaders). The list holds keys and hashes only; its no-game-code record is `games/PPSA21564/pipelines.kyplst.verified`.
- **Four Kyty options are on by default in the ASTRO BOT profile (each off with `NAME=0`; with them off Kyty behaves as in v1.2.1, the
  shader fingerprint is unchanged).** They were on in a 31-minute play session of the clean-room build ("flying, very responsive"
  was the verdict); exact in our checks, but none changed the frame time we measured in Sky Garden, so no speed-up is claimed.
  `KYTY_SUBRES_CONTAINS` and `KYTY_DEPTH_GROW_RECREATE` (ports of upstream KytyPS5 #2410 and #2417): ABA, moving Sky Garden,
  52.7 / 52.1 / 54.6 ms mean. `KYTY_ALIAS_REBUILD_PRESERVE_TIGHT` (with `KYTY_ALIAS_REBUILD_PARTIAL=1`, `=verify` compares with the
  whole-segment move on the GPU): a partial render-target rebuild moves only the blocks it owns, 14 % fewer tiler bytes;
  bit-exact in 300 unit cases and 1,793 GPU compares (0 mismatches); 49.9 / 56.8 vs 50.1 ms. `KYTY_COMPUTE_IMAGE_COPY`
  (`verify-kernel`, `verify` compare): a guest compute pass proven to be a whole-surface copy becomes a native image copy (tiler
  traffic 1,925 -> 1,337 MB/s, -31 %); exact on 473 kernel and 289 image GPU compares; 49.8 / 49.9 vs 48.6 / 50.1 ms. Both keep
  the old path for everything they cannot prove. They are set by `games/PPSA21564/game.tsv` (the profile), so other titles are
  unchanged.
- Emitter modes for the list rows (`kyty_msl_dump --mode`): `vgroup`, `cload` (vertex load grouping / constant loads), `lcont`,
  `blim`, `gexit` (loop and guard tails). Off = byte-identical text; the list rows name the modes they need.
- `nps5 data compress` was also tested on a cloud Mac with an M5 Pro (macOS 27.0, Python 3.9): a 159.8 GB game folder to
  49.4 GB on disk in 8.4 minutes (88,624 files, 0 failed), SHA-256 read-back of 14 files equal to the original.
- Clean room (public flow from fresh clones of the tagged sources, 24 GB M4 Pro): fresh clones of the tagged sources on a 24 GB M4 Pro (recomp, anyps5, Kyty and KosmicKrisp built from the pins; the KosmicKrisp source tree equals `kosmickrisp/EXPECTED_TREE`), `nps5 build`, `nps5 cache prepare` (537 s the first time, 201 s warm), then title -> hub -> Sky Garden on a recorded pad route for 300 s with the window closed by the test hook: exit 0, no crash, no GPU watchdog, 0 live shader compiles. Metal: 68 of 70 kernels and 65 of 198 listed pairs match on this M4 Pro (g16 class); 69 kernel and 71 graphics overrides were in use during the route. Render scale: 2 automatic switches (720p -> 540p -> 720p), `[physsub] world dtfix on`, the four Kyty options on. Frame time over the route: p50 58.8 fps, p95 38.2 ms (cold-ish first minutes included). A separate 31-minute play session with all options on had no visual issue reported ("flying, very responsive") and a few slow-downs while the system was swapping (peak footprint 16.7 GB, swap up to 8.3 GB). That session ended in a crash on a level transition (the game's room loader reads a null name; see Known issues).
- Known issue: a rare crash on some level transitions / asset loads (the game's room loader reads a null name, `Signal 10` on the asset-loader thread; seen in one 31-minute session and in earlier versions; a fix is in progress).
- Checks: the pinned-list tests accept a `-dev` suffix in a list's emitter tag; the list header records Kyty `5091f4bb` (v1.2.2).

## v1.2.1 (2026-11-21)

Everything in v1.2. Physics that holds up below 30 fps, banner text, a bounded command-processor lead, faster Metal
shaders on M4- and M6-family GPUs, a larger pipeline list and `nps5 data`. **Update:** nativeps5-recomp and
nativeps5-kyty move to `v1.2.1` (anyps5 stays at v1.1.1): pull, check out `v1.2.1` in `../ps5recomp` and `../kyty`,
rebuild the recompiler (`scripts/build.sh recomp`), **translate the title again** (`nps5 build`: the physics and font
fixes are in the runtime linked into the translated program, and the profile now passes the physics table), rebuild
Kyty (`scripts/build.sh kyty`), then run `nps5 cache prepare PPSA21564` once (new Kyty, new list, new Metal rows).

- **Physics sub-steps** (ps5recomp `[physsub]`, on by default for ASTRO BOT): with real-time game speed the game hands its
  physics the real frame time, 43-50 ms in Sky Garden, three times the step it was tuned for. Three things broke:
  the rainbow pool membrane crumpled (ApCloth step), the Havok particle solver ran away to infinity and crashed the game
  at the "feel the wall" gate (3 of 3 sessions without the fix), and the rope bridge after the gate vanished (the game's
  own mass-spring bridge, 10 explicit steps per frame, diverges above a frame time of about 42-45 ms in a model of it).
  The runtime now wraps those step functions ("wrap" entries of a `-hle-replace` table: the guest body stays and the shim
  calls it): the Havok world step is split into steps of at most 1/60 s (at most 4), the cloth step is clamped to 1/60 s,
  and the JungleBridge / SuspentionBridge spring steps are split to at most 1/600 s (at most 8 per call). Checked in play
  sessions: membrane correct, the gate opens with no crash, the bridge is there (0 non-finite nodes). The table
  (`profiles/funcrepl/PPSA21564-physsub.json`, addresses + sha256) is passed by `profiles/PPSA21564.json`; another build
  of the game is refused. The translation stamp now covers the `-hle-replace` table. Off: `NPS5_PHYS_SUBSTEP=0`,
  `NPS5_PHYS_SUBSTEP_WORLD=0`, `NPS5_PHYS_SUBSTEP_CLOTH=off`, `NPS5_PHYS_SPRING=0`. Tests `tools/physsub_unit.sh`,
  `tools/physsub_test.sh`, `pkg/emitter/funcwrap_test.go`.
- **Banner text** (ps5recomp libSceFont): `sceFontGetHorizontalLayout` returned the hhea ascender (0.82 em for the level-name
  font) where the PS5 driver returns the face bounding box (1.274 em); the game places banner baselines from it, so the
  tops of the capitals were cut (BOT RESCUED). Now the bbox layout, as the PS5 FreeType driver. `NPS5_FONT_LAYOUT=hhea` =
  old. Unit test `tests/font/font_glyph_unit.c` section 10.
- **Command-processor lead bound** (Kyty `KYTY_CP_LEAD_MAX_MS`, 16 in the ASTRO BOT profile, `0` off): the CP waits when
  its oldest unfinished submit is older than 16 ms. The wait is bounded (timeout = bound + 50 ms; a stuck tick is
  abandoned and never fatal). Sky Garden, moving route, BEST stack (2 clean runs with against 3 without): mean frame
  time 51.3 / 49.7 ms against 53.7-54.4, p90 62.9 / 60.7 against 67.5-69.3 ms, frames over 70 ms 97 / 68 against
  141-171, game speed 96-97 % against 91-92 %. Test `tests/CpLeadBoundTests.cpp` (407,344 checks).
- **Metal shaders per GPU class** (keys list format 2, pairs list format 3; Kyty `kyty_msl_dump --mode`): rows may name an
  emitter mode and a GPU class (`g16` = M4 family, `g18` = M6 family; class = IORegistry GPU generation + macOS major).
  `nps5 cache prepare` takes the most specific row for the Mac, then `*`, then KosmicKrisp. A class row ships only where
  it was validated against KosmicKrisp on every pipeline of its key AND timed faster on that class (g16 on an M4 Pro,
  g18 on a cloud M6). v1.2.1: g16 47 graphics rows, g18 83 graphics rows + 2 compute rows, the shared `*` rows of v1.2
  unchanged (69 compute rows, 36 graphics rows). The emitter modes are off unless a row names one, so `*` texts are byte-for-byte
  the v1.2 texts.
- **ProMotion frame cap** (Kyty, opt-in `KYTY_FPS_AUTOCAP=promotion`): on an adaptive screen, the highest 120/n cap the
  scene sustains (p90 hysteresis, probe backoff). Default unchanged (no cap on ProMotion). Tests `tests/AutoCapTests.cpp`.
- **Fullscreen on a chosen display** (Kyty, opt-in `NPS5_WINDOW_SCREEN=builtin|main|<index>`): the window is created
  windowed, moved to that display and made fullscreen there. Without the variable fullscreen is unchanged.
- **Pipeline list refreshed** from play sessions since v1.2: 949 -> 962 permutations, 1,608 -> 1,739 pipelines
  (893 shaders), merged with the v1.2 list. Keys and hashes only, with its no-game-code record.
- Clean-room check (public flow on a 24 GB M4 Pro: all components built from the v1.2.1 sources, `nps5 build`,
  `nps5 cache prepare` 312 s, GPU class g16: 68 kernels + 73 graphics pairs; `nps5 cache metal --reproduce` 237 of 237
  rows of every class): recorded route title -> hub -> level, 240 s, window closed by the test hook: rc 0, no crash or
  GPU watchdog, 0 shader compiles in play, game speed 89 % (last 30 s 99 %), peak footprint 16.5 GB, world step split in
  5,399 of 6,728 calls. Direct start in the bridge level (spring_punch_sky_aztec), 120 s: bridge spring steps up to 5 ms
  split, 0 non-finite nodes, no crash. The gate and the bridge themselves were checked in play sessions, not by the route.
- `nps5 data compress|status|decompress <folder>`: stores a game folder with APFS transparent compression (LZFSE,
  the kernel decompresses on read; the game and every tool see identical files). About 3x smaller for ASTRO BOT data.
  Dry run by default with an estimate from a sample; `--apply` works file by file: temporary file, flush, SHA-256
  read-back, unchanged-original check, atomic rename. Resumable, idempotent, refuses non-APFS volumes, leaves symbolic
  links, hard links, tiny and incompressible files alone; `decompress` undoes it. Python standard library plus the
  system's own libcompression; no new dependency. Streams each file (memory: one compressed file per worker, at
  most 1 GB of files in flight), keeps the creation date, refuses volume roots, the home folder, ~/Library, system
  folders and read-only volumes (in any letter case, through links or firmlinks); files written in the last
  minute are left for a re-run and reported. Tests: `tests/test_data.py` (round trip at the chunk and xattr edges, metadata,
  killed process and Ctrl-C in the middle, a file changing underneath, out of space mid-file on a small APFS disk
  image, a FAT disk image refused, refusal cases). Exit codes: 0 done, 1 some files failed (unchanged), 2 the folder cannot
  be used, 3 NOT DONE YET (files changed in the last minute were left; run it again in a minute).

## v1.2 (2026-11-20)

Everything in v1.1.2. The game now runs at real speed, Kyty tiles less render-target memory back and forth, and the shipped
pipeline list is larger. **Update:** nativeps5-recomp and nativeps5-kyty move to `v1.2` (anyps5 stays at v1.1.1): rebuild
the recompiler (`scripts/build.sh recomp`) and **translate the title again** (`nps5 build`, about 11 minutes: the fix is in
the runtime that is linked into the translated program), rebuild Kyty (`scripts/build.sh kyty`), then run
`nps5 cache prepare` once: the cache is keyed by the component commits and the list, so v1.2 prepares a new one (about
6 minutes on an M4 Pro; the Metal library store is reused, so almost nothing compiles from source).

- **Game speed = real time** (ps5recomp `nps5_framerate_logic.h`). ASTRO BOT advances a fixed step of game time per frame,
  read from a block that the runtime rewrites from the measured frame rate. The old estimate was an exponential average of
  per-frame rates 1/dt: with uneven frames (pairs of ~16 ms frames, then 80-105 ms gaps) that mean reads 15-23 fps above the
  real rate, so the game ran slow. And the file-read load freeze (+2 s per busy second, no limit) held the block at 60 for
  30-50 s while a slow Sky Garden streamed in. Now the average is over the frame PERIOD (one interval counts at most
  max(3 periods, 125 ms), so a compile stall does not cause a rewrite), and the file-read freeze lasts at most
  `NPS5_FRAMERATE_LOAD_MAX_MS` (default 8000; 0 = the old unlimited freeze) per run of busy seconds, after which streaming
  is ignored until 5 quiet seconds. The clamp, hysteresis, warm-up and the 5 s level-open freeze are unchanged. Sky Garden
  route (same stack, A/B): game speed 47-54 % -> 98 % of real time (first 60 s 30-33 % -> 92 %); frame time mean
  52 -> 42 ms, p95 76 -> 67 ms. The `[framerate]` line ends `frozen -|video|phase|vfs[ streaming]`. Unit test
  `tests/framerate/framerate_unit.c` (flip intervals from recorded sessions).
- **Partial render-target rebuild** (Kyty `KYTY_ALIAS_REBUILD_PARTIAL=1`, on in the ASTRO BOT profile; `=0` turns it off): a
  render target that is partly owned by another image and modified through a buffer was tiled out in full and detiled
  back in full (Sky Garden: a 2432x1368 target, about 1 GB/s of the 2.4 GB/s tiler traffic). Kyty now refreshes only the
  blocks the target does not own and skips blocks whose bytes already match (exact: segments aligned to the tile layout's
  row/column periods). Sky Garden tiler traffic 2.4 -> 1.8-2.0 GB/s; frame time the same to slightly better (A/B n=3:
  p50 18.1 -> 17.5 ms), memory the same, no picture differences. Unit test `owned_rebuild_plan_tests`. Opt-in diagnostics
  that came with it: `KYTY_TILER_TRACE=N` (tiler pass trace), `KYTY_FAULT_PROCESS_VEC=1` (vectorised fault parser, off).
- **Pipeline list refreshed** from the play-test and route sessions since v1.1.2: 920 -> 949 shader permutations,
  1,604 -> 1,608 pipelines (886 shaders). Keys and hashes only, with its no-game-code record.
- **Our Metal shaders:** the 68 compute kernels and 32 graphics pairs (36 rows) of v1.1.2 keep their texts (the lists now
  name the v1.2 Kyty). Five G-buffer pairs that differed from KosmicKrisp by 1 f16 ulp on 1-3 of 8192 words under a newer
  real-input profile are recorded in `metal-gfx-pairs.tsv.verified` under a stated near-exact rule (stored precision,
  <= 1 f16 ulp; everything else bit-exact).
- Clean-room check (public flow: `nps5 build` with the v1.2 recompiler, Kyty v1.2, `nps5 cache prepare`, then title -> hub ->
  Sky Garden on a recorded pad route, 24 GB M4 Pro): prepare 949/949 shaders, Metal 68 of 68 kernels and 36 of 36 graphics
  rows; game speed 89 % of real time over the run (82 % in the first minute, 100 % in the last 30 s); 0 live shader
  compiles at the peak; no GPU watchdog, no RT budget-guard trip, no crash; footprint peak 17.0 GB (limit 18.9 GB).
- Known issues: two long frames in that run (9.6 s while the world map loaded, 6.7 s in Sky Garden while the system was
  swapping about 9.8 GB; the test machine was shared). Sky Garden on a 24 GB Mac still runs close to the memory limit.
  Our faster shader set (lighting kernels, faster G-buffer/prepass/cloud pairs) is planned for v1.2.1.

## v1.1.2 (2026-11-19)

Everything in v1.1.1, with new Kyty features and memory settings on by default in the ASTRO BOT profile (each has an off switch, README
"Options on by default"). Needs a fresh shader-cache prepare (`nps5 cache prepare`): the GPU accuracy tier changes the
shader cache keys. The 68 compute kernels and the 31 graphics pairs of v1.1.1 keep their texts; their `emitter` line names the v1.1.2 Kyty.

- **One more Metal graphics pair: 32 (36 rows).** The cloud pair (KosmicKrisp keys 1548c37d / 604b473f, about 20 ms of
  GPU time per second where it draws) passed the same GPU differential test on both of its pipelines with the v1.1.2
  texts, on the game's own captured inputs (core seeds, mutant caught, out-of-bounds profile MATCH, NaN profile matched
  within the loop cap; its real-vertex draw was not testable, as for most pairs). `nps5 cache prepare` prints
  "Metal graphics: N of 36"; the cloud pair needs KosmicKrisp's vertex text of that pipeline in the library cache, so it
  can appear only after a session that reached the scene.

- **Ray-tracing load hold + budget guard** (Kyty, `KYTY_RT_LOAD_HOLD`, `KYTY_RT_BUDGET_GUARD`, default on): Kyty's software
  ray-tracing kernel ran a burst over a BVH that was not built yet at level entry (Sky Garden: up to 784k lanes running to
  the 8192-test node budget, a 0.6-1.2 s GPU stall and, by correlation, the GPU watchdog stops seen at level entry). The
  hold skips those dispatches for 5 s after a level opens (the runtime's level phase, so also on a direct level boot) and
  while the game reads files at >= 48 MB/s (+1.5 s), at most 60 s counted from the last level open (a 30 s budget
  that started with the hub load ran out during the next level's load in play and the GPU watchdog ended the session) (`KYTY_RT_LOAD_HOLD_PHASE=0`: file reads
  only); the guard skips them for 0.5-8 s after a dispatch exhausted at least 1024 lanes. In play (file-read trigger):
  0 budget exhaustions, 0 guard trips, level-entry command-buffer time shifts 99-246 ms instead of 632 ms; the lighting the kernel feeds catches up a moment later. Unit test `rt_budget_guard_tests`.
- **Quiet cache saves during level loads** (Kyty `KYTY_SAVE_LOAD_QUIET_S=20`; KosmicKrisp `KK_PIPELINE_ARCHIVE_FLUSH_N=4096`
  `_S=1800`): periodic pipeline/program cache saves (54-360 ms each, plus a ~285 MB transient) no longer land in a level
  transition, and KosmicKrisp's pipeline archive is written at exit instead of a few seconds into a level (a 454 ms archive
  serialize had coincided with a 426 ms frame). Caveat: a crash loses that session's new archive entries (they compile
  again next time; Kyty's caches and the Metal library cache are still saved). `nps5 cache prepare` keeps the old archive
  timing.
- **Display-aware frame cap** (Kyty `KYTY_FPS_LIMIT_MILLI=auto`, new fractional `KYTY_FPS_LIMIT_MILLI=<fps>`): on a fixed
  refresh display of R Hz the cap is R/n (n >= 2, from the frame rate the game holds: steps down after 2 s, probes up
  after 5 s with back-off, headroom judged from the frames' GPU work time), never below 30 fps (`KYTY_FPS_AUTOCAP_MIN`;
  below it the game runs uncapped: a 75 Hz monitor had stepped down to 15 fps), none on adaptive (ProMotion) displays; presents stay immediate. Logged as `[autocap] ...`.
  Unit test `tests/AutoCapTests.cpp`.
- **Learned fault classes** (Kyty `KYTY_FAULT_CLASS=1`, `KYTY_CLEAN_PAGE=1`): a guest page the CPU read-faults in 3 guest
  frames becomes read-open (no fault, no burst wait; bytes up to ~3 frames old, as served loads already returned); a page
  whose stores keep waiting for GPU bytes is closed again for 600+ frames. A store to a page whose GPU bytes are already
  published skips the drain. Sky Garden: fault handler 57 -> 21 ms per frame, burst waits 566 -> 2 per 10 s (fps change
  inside run noise). Brings the read-open page mechanism, the faster fault path (shared protect lock, lock-free dirty
  queries) and the 15-bit write-watcher count with it (all other switches of those stay off). Unit tests
  `tests/FaultClassTests.cpp`, `page_manager_tests --many-watchers`.
- **GPU accuracy low** (Kyty `KYTY_GPU_ACCURACY=high|normal|low`, `KYTY_FAST_MATH_DENY`, `KYTY_FAST_MATH_ALLOW`): `low` gives
  pixel and compute shaders fast math except the guest shaders in `games/PPSA21564/fast-math-deny.txt` (new profile file,
  64-bit guest shader hashes only; `scripts/check.sh` accepts nothing else in it): the 66 compute and 20 fragment shaders
  our Metal translations replace keep exact math, so KosmicKrisp's keys for them and our verified texts are those of the
  high tier. `normal` is selective precision (fast outside an Inf/NaN-sensitive set). About -0.9 ms per frame (n=1);
  a very slight flicker on a few close rocks was seen in play. `nps5 run` passes the deny list on the kyty back end.
  `nps5 cache prepare` prepares the tier the profile sets.
- **Less memory on every Mac** (profile): `KYTY_TEX_MIP_SKIP=2` (was 16 GB Macs only) and `KYTY_BDA_PAGETABLE_MB=64` (was
  opt-in). On a 24 GB Mac Sky Garden peaked at 17.9-19.1 GB with v1.1.1 and with the v1.1.2 defaults alike (footprint,
  mostly compressed memory and swap), at the 18.9 GB self memory limit `nps5 run` sets; runs ended with exit code 86.
  Mip skip saves about 0.8-1.3 GB there, the smaller GPU address table about 0.45 GB. Off: `KYTY_TEX_MIP_SKIP=0`,
  `KYTY_BDA_PAGETABLE_MB=0`.
- `nps5 run --until scene:<name>+N` (and `log:`/`open:`/`marker:`) no longer kills a healthy run as EARLY_STALL at about
  50 s. The watcher counted only present counters as progress, and the Kyty back end has none (only frame dumps), so a run
  with a pending scene marker stalled 45 s after its first frame. A new frame dump is now progress, and while a marker
  target is pending and no present counter exists the default stall limit is 150 s. An explicit `--until
  ...,stall:N,black:N` (or a profile `stall_fail_s`) is still used as written; a frozen present counter still stalls at 45 s.
- Kyty: flight recorder monitor gap no longer wraps when a heartbeat stamp is newer than the monitor's clock read (false
  `mainhang` dumps); the recorder stays off by default. KYTY_MEMTRIM_BIG_WIDTH stays opt-in.
- Known issues: an occasional hitch at Sky Garden level entry, where the software ray tracing briefly runs before its
  BVH is valid; the RT budget guard stops it within 0.5-1 s (no GPU watchdog in testing). A rare crash of the game's
  asset loader shortly after entering Sky Garden (2 of 13 test runs) is under investigation.

## v1.1.1 (2026-11-19)

Everything in v1.1, plus our own Metal translation for 31 graphics (vertex + fragment) shader pairs of ASTRO BOT. Needs
a fresh shader-cache prepare (`nps5 cache prepare`). Every component is pinned by release tag and commit.

- **Our Metal shaders (graphics).** `games/PPSA21564/metal-gfx-pairs.tsv` now lists 31 verified vertex + fragment pairs
  (35 rows: some pairs serve several pipelines). As with the compute kernels, nothing derived from the game is shipped:
  the list holds hashes and small layout numbers, `nps5 cache prepare` generates both stages on your Mac from your dump
  ("Metal graphics: N of 35" in the terminal), keeps a pair only when both texts are byte-identical to the verified ones,
  and compiles them under the progress card in pass 2. KosmicKrisp swaps a pipeline by the keys of its own vertex and
  fragment shader, so one pair runs on every pipeline that has those keys: a pair is listed only when it passed a GPU
  differential test against KosmicKrisp's own pair on EVERY such pipeline (two input seeds, a deliberately broken variant
  must be caught, determinism, a NaN/inf/denormal input profile, shrunk guest buffers for out-of-bounds reads, unforced
  vertex positions). 22 more candidate pairs are left out: 2 differ on some of their pipelines, 2 were not testable, and
  18 differ only on the NaN/inf input profile. 10 of the 31 have the exact texts of two full play sessions with no visual
  issue (title, ship, world map, save cards, Sky Garden); the other 21 differ from those only by the out-of-bounds fix
  below. KosmicKrisp keeps its own pipeline when a replacement fails to compile. Opt out with `NPS5_METAL_SHADERS=0`.
  A later `nps5 cache prepare` keeps the pairs: KosmicKrisp's vertex texts they link to are kept next to the metallib
  store (`<cache>.kk-vs-keep`), because a prepare that loads stored metallibs no longer writes them.
- **Kyty MSL back end (graphics):** the emitter changes these texts depend on: raw-vertex pixel reads unpacked as on the
  host path, KosmicKrisp's float model for pixel shaders, KosmicKrisp's in-shader blend and write mask, dword-aligned
  buffer base offsets, graphics permutations at their stored push-data cursor, IMAGE_SAMPLE_D with the descriptor LOD bias,
  and a single-lane vertex stage (KosmicKrisp has no vertex-stage subgroups; real cross-lane ops there could loop
  forever). A raw 32-bit x2/x3/x4 buffer access whose whole vector is out of range now checks bounds per dword (the
  in-range dwords load and store, as on the GPU and in KosmicKrisp) instead of returning zeros / dropping the store.
  The 68 compute kernels are byte-identical to v1.1. New `msl_core_tests` target (65 checks), and
  `kyty_msl_dump` now builds on its own (`--target kyty_msl_dump` on a fresh build directory failed in v1.1).
- **Pairs list format 2:** a blend column and an optional push-data cursor after a spec key (`<spec>:<cursor>`; one
  program and specialization can be stored several times with different cursors). Format 1 lists still load. A list
  with pair rows needs its `metal-gfx-pairs.tsv.verified` record (`scripts/check.sh`).
- **Lists tied to the generator:** the `emitter` line of both Metal lists names the Kyty commit and release tag the
  hashes were made with. When a prepare cannot reproduce the verified texts it says which Kyty it found and which one
  the list needs, and exits with code 3 (the cache still works; the skipped shaders stay KosmicKrisp's).
- **Recompiler (directory listings):** the v1.1 symlink-type cache scanned its entries linearly, so listing a very large
  symlinked folder took seconds (46k entries: 3.0 s, under a lock that blocked other listings) and a dangling link stayed
  hidden after its target appeared. It is now hash-indexed (53 ms for the same folder), failed lookups are not cached,
  and folders changed within the file-time granularity are not cached. Real dumps were not affected.
- **Fixed a rare freeze (spinning wheel, controller dead) when a gamepad connects at startup or reconnects.** Kyty's
  pad relay thread held SDL's joystick lock while waiting for the controller lock, and the main thread did the reverse
  when a pad was added. The relay now reads the controller first, and setting up a newly connected pad no longer holds
  the controller lock across SDL calls (with a deadlock test).
- **Kyty fixes from review:** the pipeline-cache saves at window close and SIGTERM run at normal priority, and a
  periodic save skipped as unchanged still writes the known-pipeline list; `KYTY_MAD_MODE=fused` with the relaxed pixel
  prelude no longer aborts; the MSL binding model is built per emission; in-shader blending reads the destination only for
  render targets the shader declares (none of these changes a listed shader text); the per-dword bounds fallback
  computes each dword's offset in 64 bits (it could wrap near 4 GiB; 21 of the 31 pairs' texts change, all re-verified).
- **`nps5 run` / `nps5 cache prepare` no longer refuse a binary from recompiler v1.1 (rc 78).** The ASTRO BOT profile asks
  for the `dr nt` patches, but recompiler v1.1 builds `nt` (and `lowres`) in as runtime patch points (`fast_lighting`,
  `upscaling`), so the old site check saw an unknown word and refused. `games/<id>/patches.py` now names those points
  (`SETTINGS`); a binary that carries one reports the cut as `setting` (provided at runtime, nothing written) in
  `nps5 patch --check`, `nps5 patch` and `nps5 run`. Binaries without the patch points are checked as before.
- **Fixes from review:** `scripts/fetch-components.sh` verifies a tag before the checkout exists (a refused clone
  no longer stays behind to pass a second run); the 1800 s run ceiling applies only to the prepare passes.
- **Pins:** components.lock pins `v1.1.1` in all three component repositories (AnyPS5 unchanged from v1.1, recomp with
  the listing fix, Kyty with the commits above); KosmicKrisp unchanged (same pin and patches). `kosmickrisp/EXPECTED_TREE` now holds the
  tree that the BUILDING.md command prints (without the stamp file; the v1.1 value included the stamp that patch 0002
  creates, so the documented comparison could not match).
- **Opt-in memory and GPU switches (Kyty, all off by default):** `KYTY_MEMTRIM_BIG_WIDTH=<n>` (dedicated Metal heaps
  for render targets at least n pixels wide, so retiring one returns its memory; use 2400 with
  `KYTY_BDA_PAGETABLE_MB=64`), `KYTY_DEPTH_FEEDBACK_USAGE=0` (depth images without the feedback-loop usage, so
  KosmicKrisp keeps lossless depth compression, about -2 ms per frame), and experimental `KYTY_MEMTRIM_STALE_FRAMES`,
  `KYTY_MEMTRIM_ALIAS_FRAMES`, `KYTY_VMA_BLOCK_MB`. `KYTY_TEX_MIP_SKIP` (in Kyty since v1.0.3; the ASTRO BOT profile
  uses 2 on 16 GB Macs) is now documented. README: "Memory and GPU options".
- `docs/env_switches.tsv`: the new emitter switches (`KYTY_MSL_KK_BLEND`, `KYTY_MSL_GFX_MATH`, `KYTY_MSL_GFX_FTZ`,
  `KYTY_MSL_VS_SINGLE_LANE`, `KYTY_MSL_PERM_CURSOR`) and the missing `KYTY_FLIGHTREC_DIR` row for `nps5 run`.

## v1.1 (2026-11-18)

Everything in v1.0.3, plus our own Metal compute kernels and the selective buffer-collector settle on by default. Needs a
fresh shader-cache prepare (`nps5 cache prepare`). Fast math stays off, as in v1.0.3.

- **Our Metal shaders (compute).** For the compute kernels listed in `games/<title id>/metal-shaders.tsv`, KosmicKrisp
  runs a kernel translated straight from the PS5 shader to Metal by Kyty's new MSL back end instead of its own
  SPIR-V -> NIR -> MSL kernel. Nothing derived from a game is shipped: the list holds keys only (hashes), and
  `nps5 cache prepare` generates the kernels on your Mac from your own dump between its two passes ("Metal shaders:
  N/T" in the terminal), keeps a kernel only when it is byte-identical to the one that was verified (GPU differential
  test against KosmicKrisp's kernel on the same inputs, benchmark, in-game smoke and play test), and compiles them
  once under the progress card in pass 2. Normal starts load them as prebuilt Metal libraries in the background;
  KosmicKrisp's own kernel runs until a replacement is ready and whenever one fails. ASTRO BOT: 68 compute kernels;
  Sky Garden about +11 % (16.2 against 14.4-14.7 fps, p50 62.3 against 66.1-67.3 ms, frames over 100 ms 5-6 -> 0,
  A-B-A on v1.0.3). Opt out with `NPS5_METAL_SHADERS=0`. `nps5 cache status` shows how many are in use;
  `nps5 cache metal <id>` regenerates them. KosmicKrisp patches 0023-0025 (`KK_MSL_OVERRIDE_DIR`,
  `KK_MSL_OVERRIDE_ASYNC`, compile counts for the progress card); new tool `kyty_msl_dump` in the Kyty fork.
- **Faster wave64 votes in the Metal kernels.** The MSL back end now takes one subgroup vote over both halves of a
  wave64 (`any(a) || any(b)` = `any(a || b)`; `KYTY_MSL_VOTE_MERGE=0` restores the old text), which makes 4 more kernels
  faster than KosmicKrisp's (68 in all) and 16 others faster than before.
- **Buffer collector, selective settle on by default** (`KYTY_GC_SETTLE_SELECTIVE`, default 1; `=0` restores the v1.0.3
  behaviour). A collection pass no longer waits for the GPU to finish every pending readback; it skips the buffers those
  readbacks cover and looks at them again later. In Sky Garden the command-processor thread's settle waits drop from
  about 20 to about 6 ms per frame (about -4 ms per frame, +1 fps). v1.0.3 kept it off because of a flicker on Astro's
  helmet; that case was a stale per-frame constant buffer, which the buffer snapshots below now copy, and with both on
  the crash-site flicker checks (300 consecutive frames: sky, ridges, rocks, visor) and the play test are clean.
  `KYTY_GC_SIDE_SETTLE_LAZY`/`_LAZY_CRITICAL` have no effect while it is on.
- **Buffer snapshots.** Small read-only buffer bindings that the GPU has not written are copied at bind time, like those
  the CPU changed (`KYTY_SNAPSHOT_CLEAN_READS`, default on; `=0` restores the v1.0.3 behaviour). Aimed at the
  intermittent visor flicker in the crash-landing close-up.
- **Gentler shader prepare.** The shader precompile workers run at utility priority, and the replay workers and the
  compute prewarm share one fan-out of driver pipeline builds (`NPS5_COMPILE_GATE=0` lifts it): a prepare took 285
  instead of 333 s and the load average peaked at about 27 instead of 84 (one measurement), so the Mac stays usable.
- **Our Metal shaders (graphics): mechanism only, no pairs yet.** The same path for render pipelines exists
  (`games/<title id>/metal-gfx-pairs.tsv`, KosmicKrisp patch 0026: a pipeline is swapped only when BOTH of its stage keys
  match a listed vertex+fragment pair; generation keeps a pair only when both texts are byte-identical to the verified
  ones), but the ASTRO BOT list is **empty** in v1.1: no graphics pair has passed verification yet, so every render
  pipeline is KosmicKrisp's own, as in v1.0.3. The Kyty MSL back end gets the graphics-stage pieces (per-invocation LDS,
  integer f32 to f16 round-toward-zero).
- **Faster `nps5 cache prepare`.** Compiled Metal libraries are kept in a content-addressed store (`NPS5_METALLIB_STORE`),
  so the first pass only compiles shaders that are new; the library builder no longer shells out per file (biggest-first,
  `NPS5_BUILD_QOS` to pick the QoS); `nps5 cache metal` reuses unchanged MSL texts and the vertex-text lookup hashes
  each file once; the second pass is skipped
  when the first pass loaded everything from libraries and there is nothing new to generate (`NPS5_PREPARE_KEEP_PASS1=0`
  restores it). A second prepare on an unchanged cache is much shorter. `nps5 cache prepare` also works again for the
  1800 s ceiling (it was refused by the 180 s run cap, rc 78, in v1.0.3).
- **Fewer periodic stalls from the pipeline-cache saver.** The saver skips a rewrite when nothing changed, backs off,
  defers while frames are slow and runs at background I/O priority (`KYTY_PIPELINE_CACHE_SAVE_LEGACY=1` restores the old
  behaviour). `KYTY_MEMLOG=1` prints a 1 Hz `[mem]` line (footprint, resident, swap) and `[slowframe]` lines.
- **Flight recorder (opt-in).** `KYTY_FLIGHTREC=1` turns on a light, bounded recorder (about 13 MB of rings, nothing on the hot
  path but a ring write) that keeps the last seconds of submissions, compile and file events and counters and writes a dump
  when something goes wrong (GPU watchdog, a frame or present stall over 2 s, the memory limit, a crash, SIGTERM). A dump
  holds ids, sizes, hashes and timings only, no game bytes. `nps5 run` puts it in `<run dir>/flightrec`; `=2` records every
  draw. Off by default; attach a dump (`<run dir>/flightrec`) and the `nps5 bug-report` zip to an issue, we decode them locally (README, Flight recorder).
- **Symlinked dump folders.** `getdents`/`getdirentries` report a symlink's target type (file or folder) and hide dangling
  links, so a dump folder that is a tree of symlinks lists correctly (nps5's own translation does not build one; it
  matters for hand-assembled dumps).
- **Recompiler lowering fixes** (found by a differential fuzzer of the lifted instructions against Rosetta): `VPHMINPOSUW`
  with the destination equal to the source returned index 0, `VPERMILPD ymm, imm` selected the wrong upper lane, and
  `VRCPSS` read a clobbered source. Takes effect after the title is translated again (a fresh `scripts/run-title.sh` translation).
- `KYTY_BDA_PAGETABLE_MB=<n>` (off by default) sizes the dense BDA page table; 64 saves up to about 450 MiB of footprint on
  ASTRO BOT when the dense table is in use (its GPU addresses end at 67 GiB).
- **Pinned builds.** `components.lock` pins each component by annotated tag (`v1.1` in all three repositories) and the commit that tag must point at; `scripts/fetch-components.sh` fetches the tag and refuses it if it points anywhere else, so a moved tag cannot change what you build.
- Performance (MacBook Pro M4 Pro, MetalFX 720p default): Sky Garden (`underwater_aerial_garden`) about 17 fps (frame
  time about 59-60 ms; v1.0.3: 14.6 fps, p50 65.7 ms, one run). v1.0.3, one run each: world map 29.9 fps,
  `hoover_beach` 27.0 fps (p50 32.9 ms).
- MetalFX 720p upscaling stays the default (720p render, upscaled to the window); `NPS5_ASTRO_UPSCALING=off` gives
  native 1080p.
- Sky Garden flower-pod switch and pull bar: the "switch does not pop up / pull bar already out" report came from a
  test setup whose game folder was a tree of symlinks, so the game found no level joint animations in its directory
  listings; the directory-listing fix above resolves it, and a dump folder of real files was never affected.
- Known issue: the level-title font draws K, Y and N with flat tops, slightly different from the console; under
  investigation.

## v1.0.3 (2026-11-18)

Needs a fresh shader-cache prepare (`nps5 cache prepare`) if you prepared with a v1.0.3 candidate that had fast math on.

- Fast math is **off** (`KYTY_FAST_MATH` default 0, the v1 shaders). It is opt-in with `KYTY_FAST_MATH=1`
  (pixel and compute shaders drop the IEEE signed-zero/Inf/NaN preservation and contraction limits, so KosmicKrisp
  compiles them with Metal fast math; vertex shaders and the position math stay exact), but it gives a faint 1-frame-in-3 brightness flicker on the sky, far ridges, rocks and Astro's visor at
  the crash site, so it is not the default. A shader cache prepared without it is the normal one; with `=1` the
  shaders compile live. Making it safe per shader is planned for a later release.
- Buffer collector, selective settle (`KYTY_GC_SETTLE_SELECTIVE=1`): under memory pressure it skips buffers that still
  have readbacks in flight instead of waiting for the GPU (Sky Garden with the budget forced low: 73-75 -> 62 ms per
  frame). **Off by default in v1.0.3:** with the video memory budget forced to 4096 MB it let a flicker through at the
  top left of Astro's helmet after the crash landing (it can reuse a buffer whose readback or GPU write is still in
  flight). Known issue until the collector is reworked.
- MetalFX 720p upscaling is now the default (user decision after a playtest). Opt out with `NPS5_ASTRO_UPSCALING=off` (native 1080p) or pick `metalfx900`. The blocky squares on dust, glass and particles are gone. The game takes the
  light-tile grid of its forward-lit transparents from a 1080p table while the light culling pass sizes its grid
  from the real target; the `metalfx720`/`metalfx900` options now set `KYTY_ASTRO_TILE_GRID=auto` (grid =
  ceil(MetalFX input / 16 px)), which corrects the table. Checked at 720p on the intro cutscene dumps (logos, title
  screen, warp tunnel, ship, asteroid field): no 16x16 block pattern. `NPS5_ASTRO_UPSCALING` defaults to `metalfx720`.
  Frame rates: see "Performance" below.
- Gentler rumble on controllers that are not a DualSense (the game's haptics audio drives the two motors): the envelope
  now has a deadband, a curve and a smooth release and is scaled to half strength, where it used to reach full strength
  at half the signal (`NPS5_RUMBLE_SCALE=0.5`, `NPS5_RUMBLE_FLOOR=0.08`, `NPS5_RUMBLE_GAMMA=1.6`, `NPS5_RUMBLE_DECAY=0.8`;
  `NPS5_RUMBLE_SCALE=0` turns it off). DualSense HD haptics are unchanged.
- Tooling: a scripted pad no longer also relays the real pad's input in the Kyty host (`KYTY_PAD_RELAY_WITH_SCRIPT=1`
  restores that), and a pad script accepts `shake=<seconds>` (10 Hz accelerometer/gyro shake, at most 30 s) for motion tests.
- Fixes from the v1.0.3 review: the last-scene memory (`shader_prepare=scene`) is also written when the window is
  closed, so it no longer goes stale on a warm cache; an explicit `KYTY_ASTRO_TILE_GRID` is bounded (at most 240x135,
  the game's 2160p entry; bad values are refused) and `auto` no longer polls forever on an input above 3840 px;
  `KYTY_PAD_RELAY_WITH_SCRIPT` takes effect only with `=1`; a pad-script `shake=` at the end of a script still plays
  and ends instead of leaving its first motion sample applied.
- Performance (MacBook Pro M4 Pro, MetalFX 720p default): world map 29.9 fps (p50 33.5 ms, one 60 s run), about
  38 fps at the crash site after the landing (median of the game's own frame-rate readings over a playtest). v1.0 at native 1080p on the same Mac: world map 25.6 fps,
  `hoover_beach` 20.1 fps, Sky Garden about 13-16 fps. `NPS5_ASTRO_UPSCALING=off` gives native 1080p.
- Known issue: Astro's visor cracks can flicker now and then in the crash-landing close-up. It was there before
  v1.0.3 and is not the fast-math flicker; a fix is planned for v1.1.
- Carried over from v1.0.2 (no new change): file reads into guest memory (`readv`, `fread`) repeat when the page was
  protected again before the kernel copied (EFAULT), as `read`/`pread` already did; the world-map bus error is closed.
- Fault handling switches, off by default: `KYTY_HOSTED_RETRIED_WRITES=1` (guest write faults on GPU-owned pages take
  the side path instead of draining the command processor) and `KYTY_READ_RELAX=<n>` (served read-watched pages are not
  re-watched until the next GPU write). Together `hoover_beach` p50 45.9 -> 37.9 ms, 20.1 -> 22.9 fps (two runs each, ABBA);
  not yet validated on every scene.
- No compiles at run time on a prepared cache: a launch phase behind the loading card creates every known pipeline
  before the game starts (`KYTY_PIPELINE_REPLAY_ALL=1`, on in the preset) and builds the renderer's tile/detile
  helper pipelines (`KYTY_HELPER_PREWARM=1`); a prepare now records those helpers and waits for KosmicKrisp's
  archive writer before it ends (it used to lose the last ~30 pipelines it compiled); closing the window saves the
  driver pipeline cache first. Compile workers = logical cores - 4 (min 2, `NPS5_COMPILE_THREADS`) at utility QoS.
  The launch phase holds the game's first draw, not the window, so the card counts to X == T
  (`KYTY_PIPELINE_REPLAY_ALL_HOLD=0`: in the background). Setting **Shader preparation at launch**: all (default) or
  next scene first (`shader_prepare=all|scene`, `NPS5_SHADER_PREPARE`); recordings tag each pipeline with its scene
  and the list carries the tags (`nps5 cache merge` prints them).
  A prepare-only start shows the same card for the whole pass (`KYTY_PIPELINE_LIST_PREPARE_CARD=0`: no card), and
  `nps5 cache prepare` prints the count in the terminal; `nps5 run` uses the prepared cache of the stack by itself and,
  without one, builds the list in the launch phase behind the card (`NPS5_RUN_SHADER_CACHE=0` turns that off).
  `nps5 cache prepare` ends with two short normal starts that build the window's own helper pipelines (present /
  overlay blits, two kernels: in no list), so the player's first starts compile none of them (`--no-warm` skips them).

## Unreleased

## v1.0.2 (2026-11-18)

- Runtime: `readv` and `fread` into guest memory are repeated after an EFAULT from a page the back end protected again
  (the savedata and libc `fread` paths and the `readv` system call), as `read`, `pread` and `preadv` already were; the
  file or stream position is put back first. Unit-tested (`TestHostAccessHooks`). Runtime only: no retranslate, no
  shader-cache re-prepare.
- ASTRO BOT known issue closed: the world-map bus error (SIGBUS, about 9 % of world-map runs during development) was
  that EFAULT read race, already fixed by the read retry in v1.0.0; 0 of 50 title-to-world-map runs since.
- `docs/learnings.md`: the 2026-11-18 ASTRO BOT optimisation experiments that did not win, with reasons and leads.

## v1.0.1 (2026-11-18)

- ASTRO BOT shipped pipeline list refreshed from play testing: 1,596 pipelines (was 1,544), 893 shader permutations (was 878). References and keys only, no game code; proof record regenerated.
- v1.0.1 hotfix: loading a save no longer crashes the game depending on its content. `sceSaveDataTransferringMount` opened the game's own save memory, which the title then parsed as the other title's save (XML parser, wrong offset); it now opens only `<save root>/transfer/<titleId>/user_<id>/<dir>` read-only and returns NOT_FOUND when absent, like the console (a title id that is not letters and digits is a parameter error; read-only mounts no longer take a save icon). `APS5_SAVEDATA_TRANSFER_FALLBACK=self` restores the old behaviour. A new game still creates its save. Runtime only: no retranslate, no shader-cache re-prepare.
- In-game UI text renders again (menu captions, "PRESS ANY BUTTON", copyright): the libSceFont glyph API the game uses
  is implemented instead of returning an error.
- ASTRO BOT `upscaling` setting (`off` default, `metalfx900`, `metalfx720`): renders the scene at 900p/720p and scales
  it with Apple MetalFX (spatial) inside KosmicKrisp (patches 0021-0022). Experimental: both lower sizes showed block
  artefacts on moving objects (fixed in v1.0.3, see above), so the default stays native 1080p.
- ASTRO BOT profile: `KYTY_READ_FAULT_PUBLISHED_FRAME_BURST=48` (removes the ~340 ms `hoover_beach` stalls),
  `KYTY_OCCLUSION_QUERY_DRAW_SKIP=1` and `NPS5_VFS_DIRCACHE=1` are on.
- Flower/turtle flicker fixed: early depth/stencil tests for instrumented `GET_LOD_STATS` pixel shaders
  (`KYTY_LOD_STATS_EARLY_TESTS`, default on; needs a fresh shader-cache prepare).
- Controller: haptics (DualSense HD vibration through its audio device, rumble on other pads; `NPS5_HAPTICS=0` off)
  and motion sensors (gyro/accelerometer reach the game); the video player loops without the old flush race;
  shared log line convention (`NPS5_LOG=subsys:level`).
- `nps5 bug-report`: one zip of redacted text (trimmed log, doctor, commits, settings, versions, crash report) for an issue, with an allow-list, a printed file list and `--dry-run`. `nps5 log` reads a noisy log (condensed, rate limited, by level) and `nps5 run` prints and saves a SUMMARY block. The `[subsystem] LEVEL` line convention is in `docs/reference/logging.md`. The issue form asks for the zip.

## First public release (2026-11-17)

First publication. History of the forks: upstream history as is, then this project's work as a few squashed
commits, then the changes below as separate commits.

### Kyty back end (`nativeps5-kyty`)
- Loading card: a full-screen card ("PREPARING SHADERS X / T", "LOADING... N s") covers the opening load only: it
  stops for good at the first frame that is not fully black and never shows over a playing video (studio logo).
  Later loads show just the small bottom-left "compiling" badge; `KYTY_LOADCARD_MIDGAME=1` (off by default)
  brings the full card back after a stall of `KYTY_LOADCARD_GAP_S` (4 s). Frame dumps never change the screen.
- Two hitch switches, both off by default: `KYTY_READ_FAULT_PUBLISHED_FRAME_BURST=<n>` (per-page, per-frame
  limit for served read faults) and `KYTY_OCCLUSION_QUERY_DRAW_SKIP=1` (skip query-only draws when occlusion
  results are synthetic).
- From KytyPS5 upstream: descriptor sets are allocated one per call after a failed batch allocation (7eded28c);
  an attachment-less render area is bounded by the draw's scissors (pull request #108); fewer optional submission
  boundaries inside rendering instances and WAIT_FLIP_DONE submits only when the queue suspends (pull request #107,
  `KYTY_PASS_SUBMIT_DEFER`, default on: world map p50 37.0 -> 36.0 ms, a level 42.1 -> 41.1 ms in paired runs).

### Runtime and translator (`nativeps5-recomp`)
- Lifter: 32-bit SHLD/SHRD by a CL count of 0 zero-extends the destination (PS4Recomp 27e12b0).
- VFS: optional per-directory listing cache for case-insensitive lookups (`NPS5_VFS_DIRCACHE=1`, default off).
- Crash reports always finish: a second thread that fails while a crash is being reported (typically an abort
  during library teardown) waits for the report instead of ending the process as a "recursive crash"; the run
  bundle's summary and manifest are written before teardown starts and a failure during teardown afterwards is
  a plain exit 1; the alternate signal stacks are guard-paged mappings (256 KiB per thread, 512 KiB main) so a
  crash on top of a running fault handler no longer overflows them; the pc map dump has its own time bound.
- Frame-rate block (`NPS5_FRAMERATE_BLOCK`): frozen while a video plays and while a level loads, longer average
  and a 5 % / 1 s hysteresis (`NPS5_FRAMERATE_LEGACY=1` = the old behaviour).
- Video (sceAvPlayer): frames are shown by their timestamps in every sync mode (a title that sets "no sync" played
  its clip as fast as it decoded), late frames are skipped, the last fetched buffers are kept away from the decoder
  while the game still copies them, 4 decode threads, a looping clip does not wait for its old timestamps
  (`NPS5_AVPLAYER_SYNC_LEGACY=1` = the old behaviour).
- Review fixes: only the thread that reports a crash arms the process alarm (a second failing thread cut the pc
  map dump's own bound short); the frame-rate tuning switches (`NPS5_FRAMERATE_EMA`, `_HYST_PCT`, `_HYST_MS`,
  `_WARM`) are range-checked; the directory listing cache frees an oversized listing.
- The check list also runs the host access hook test (a file read into guest memory is repeated when a watched
  page was closed again before the kernel copied; without it ASTRO BOT's first hub crashed while loading).
- Review fixes (APR command buffers, raw host writes refused, crash reports without home paths, patch points bound
  to the build they name, seam fences) and the pak scan without a stat per file, already in the base commits.

### Host libraries (`nativeps5-anyps5`)
- From AnyPS5 upstream: six macOS host fixes (errno across the first runtime_error, FreeType symbols hidden,
  terminate handler, pipes without pipe2, compact unwind cursor info, no reconnect after a dropped connection).
- `sceAudioPropagationSourceCalculateAudioPaths` succeeds with zero paths (a game stopped on 0x8A700001).

### This repository
- `nps5 run --play`: a session for a person at the keyboard (sound on, no early stop by the watcher, up to two
  hours); every other safety rule stays. Plain `nps5 run` remains the short, silent test run.
- KosmicKrisp patch series 0001-0020 (review fixes 0016-0019, persistent library cache 0020); `nps5 cache
  prepare` is two-pass so the pipeline archive is keyed on the offline-built libraries.
- `scripts/check.sh` reads git objects, checks each commit of a range, closes the evasions a review found.
- Offline tool READMEs use the public paths; local tool data lives in the git-ignored `work/`.
