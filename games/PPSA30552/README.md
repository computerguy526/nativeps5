# A Game About Digging A Hole (`PPSA30552`)

Game note. No game data is here and none is accepted; you need your own dump
([CONTRIBUTING.md](../../CONTRIBUTING.md)). This title was the first bring-up target, and its row-by-row history
(over a hundred measured ideas) is [docs/playbook.md](../../docs/playbook.md); this note is the summary and the
profile.

- **Version:** not recorded in the notes this was written from. Record it in `game.tsv` on the next run.
- **Engine and graphics:** Unreal Engine 5; AGC; UI (Slate) composited at 4K over a 1280x720 scene.
- **Back end:** `anyps5` (AGC driver, RDNA to SPIR-V shader recompiler), KosmicKrisp.
- **Status as of 2026-11-15/16** on an M4 Pro, 24 GB: boots; menu, intro cutscene and gameplay run. Not finished.
- **Profile files:** [game.tsv](game.tsv), [bench.tsv](bench.tsv), [validated.tsv](validated.tsv) (no row yet). No
  `patches.py` (the title needs no binary patch) and no pipeline list yet (its pipeline journal is a different
  format and stays local).

## Status

| Scene | Frame time, fps | Bound by | Notes |
|---|---|---|---|
| menu | 26.8 ms, 37 fps; about 20 ms, 48-50 fps with the low-quality menu blur | GPU, 94 % busy | the blur override changes the look (sharp backdrop) |
| intro cutscene | 16.6-17.5 ms, 57-60 fps | GPU | |
| gameplay (short route) | 19-21 ms, about 50 fps (single runs; identical runs differ by 2.6 ms) | GPU | |

Footprint 8.6-8.8 GB. All frame times are from a quiet machine; under load the same build measures 26-29 fps in the
menu.

## What the profile needs and why

| Setting | Why |
|---|---|
| settings overlay mounted over `/app0` | without it the title runs its default heavy lighting path: dark and several times slower. Check the log for the "mounted" line before reading any number |
| scene resolution 1280x720 | the title hard-codes a 3840x2160 display buffer; the scene targets are what can shrink |
| a GPU budget | 17 of 17-20 GB of footprint was the GPU layer before it |
| `kk_fake_depth_bounds=true` | depth bounds are reported but ignored on M1-M4 ([rendering](../../docs/reference/rendering.md)); 7 pipelines fail to replay without it |
| `Slate.ForceBackgroundBlurLowQualityOverride=1` | one menu widget made two 4K passes and 146 MiB of copies per frame: -9.5 ms |

## How to reach a scene

Routes are in [bench.tsv](bench.tsv). The default pad script never leaves the menu: for a whole morning every
"37 fps" figure was the menu. A press before about 15 s lands in the logos. Gameplay needs a save and a recorded
route; a static scene with no input stays dark, and a 0.2 s stick nudge fixes that.

## Known issues

| Issue | Cause | State |
|---|---|---|
| Dark, dim or washed-out gameplay; brightness pops on camera movement | the tonemap table draw only took effect when its render pass was first in its command buffer ([rendering](../../docs/reference/rendering.md)) | fixed, on by default; judge a run from frame dumps |
| Crash 50-60 s into the intro on old binaries | translator: a gather's index register missing from vector liveness (guest `0x331c760+0x4a68`) | fixed in the translator; retranslate |
| A set of seven parallel-rendering console variables hangs 3 s after start | not bisected | open; single variables work |
| Hitches in gameplay | first-visit pipeline compiles (I/O and allocation were ruled out) | warm the journal with the gameplay route |
| GPU-bound at 94 %: CPU-side work is capped at about 2 fps | | see [learnings](../../docs/learnings.md), section 2, before starting CPU-side work |

## Found on this game

[The game slept on the wrong clock](../../docs/reference/frame-rate.md),
[depth bounds](../../docs/reference/rendering.md),
[a draw that only works first in its command buffer](../../docs/reference/rendering.md),
[render targets that share guest memory](../../docs/reference/rendering.md),
[a profile row is not a saving](../../docs/reference/profiling.md),
and most of the "did not work" table in [learnings](../../docs/learnings.md).

## Open

Remaining GPU time: the 4K composite passes, a 16384^2 virtual shadow map (1.47 ms per frame profiled, spikes to
51 ms), storage copies, shader workarounds ([shaders](../../docs/reference/shaders.md)). The ranked list is in
[playbook.md](../../docs/playbook.md), section 5.
