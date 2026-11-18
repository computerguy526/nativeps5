# Changelog

Changes across the four repositories (this one, `nativeps5-recomp`, `nativeps5-kyty`, `nativeps5-anyps5`); the
pinned commits are in [components.lock](components.lock).

## Unreleased

- In-game UI text renders again (menu captions, "PRESS ANY BUTTON", copyright): the libSceFont glyph API the game uses
  is implemented instead of returning an error.
- ASTRO BOT `upscaling` setting (`off` default, `metalfx900`, `metalfx720`): renders the scene at 900p/720p and scales
  it with Apple MetalFX (spatial) inside KosmicKrisp (patches 0021-0022). Experimental: both lower sizes show block
  artefacts on moving objects (some game passes keep a 1080p size), so the default stays native 1080p.
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
