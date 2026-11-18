# Changelog

Changes across the four repositories (this one, `nativeps5-recomp`, `nativeps5-kyty`, `nativeps5-anyps5`); the
pinned commits are in [components.lock](components.lock).

## v1.1 (unreleased, candidate)

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
- Performance (MacBook Pro M4 Pro, MetalFX 720p default): Sky Garden (`underwater_aerial_garden`) about 17 fps (frame
  time about 59-60 ms; v1.0.3: 14.6 fps, p50 65.7 ms, one run). v1.0.3, one run each: world map 29.9 fps,
  `hoover_beach` 27.0 fps (p50 32.9 ms).
- Known issue: in Sky Garden the flower-pod switch does not pop up and the pull bar is already out, so that puzzle
  cannot be finished. It happens without our Metal kernels too (not caused by v1.1); under investigation.

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
