# Cronos: The New Dawn (`PPSA24096`)

Game note. No game data is here and none is accepted; you need your own dump ([CONTRIBUTING.md](../../CONTRIBUTING.md)).
**Bring-up only: not a supported title in v1.3.0.** This note records where it stands so nobody has to guess.

- **Version:** 01.010.000 (SDK 10.00), eboot Build ID `badcaa3cd064868f`; dump about 35 GB.
- **Engine and graphics:** Unreal Engine 5.5 line, the same generation as SAROS (cvar table within a few names of SAROS's; MegaLights and
  Substrate present). Bink 2 movies are decoded in the game's own code, so no AvPlayer is involved. Wwise 2023.1 audio.
- **Back end:** `kyty`. It runs on the SAROS stack: its imports are a subset of SAROS's, and nothing Cronos-specific was needed outside
  what SAROS already brought. There is **no translator profile** (`profiles/PPSA24096.json` does not exist yet) and no pipeline list.
- **Status as of 2026-11-24** (an M5 Pro, 24 GB): the title translates, boots, shows the "press a button" menu, passes sign-in, the
  agreement screen and loads into the game with a controller. With a warm shader cache a 150 s run starts with no live shader compiles
  (a startup pre-compile of 695 permutations takes under a second), renders the menu at 5.4 ms p50, and was still loading at the end of
  the run (the frame rate falls from 383 to 18 fps while data loads). **No gameplay frame rate has been measured**, and the release
  line has not been smoke-run on it ("Cronos boot smoke": not done).
- **Memory:** the footprint guard for this title is 18432 MiB (the 24 GB limit is 18944); it was raised once by the owner from 17408
  after a cold-cache session reached 17.8 GB at about 8.5 minutes and was stopped by the guard (exit code 86). It is never raised to
  make a scene pass. A warm-cache menu run sits at 9.1 GB.

## Open

- **The pipeline-list tool does not support Unreal shader locations.** `nps5 cache record`/the list tool store the offset of each
  shader code in the executable; Unreal ships its shaders in the game's containers, so a list built from a Cronos journal is empty
  ("located 0 of 646 shader codes"). SAROS has the same limit. The tool needs a format that keys on the recorded program
  and shader-journal entries; until then the cache only warms by playing, and no before/after cache measurement exists.
- **Render-thread stall:** with a cold cache, draws wait on pipeline builds and the engine aborts (`GameThread timed out waiting for
  RenderThread after 120 s`). Warm the cache with repeated runs of one cache folder.
- One compute kernel hits the code generator's loop cap (a development guard, logged `RED`) and gives wrong results for that kernel.
- Memory at level start: guest memory about 9.7 GB (not recoverable), GPU about 6.3 GB. Opt-in reductions to try, in order:
  `KYTY_TEXTURE_RESIDENT_TRIM=1`, `KYTY_TEX_MIP_SKIP=2` (softer textures), `KYTY_MEMTRIM_BIG_WIDTH=2400`, `NPS5_KYTY_ALIAS_TRIM_MS=1000`.
  **Do not use `KYTY_BDA_PAGETABLE_MB=64` or the `MEMTRIM` bundle**: Cronos buffer addresses reach about 201 GiB, above what a 64 MB table covers.
- A real controller session works through `NPS5_PAD_NET` (a controller on another Mac); audio is not verified.

## Setting it up (for developers)

Same as [SAROS](../PPSA07631/README.md#set-it-up): pack the dump first, translate from a view without emulator artefacts, `GPU=kyty`,
the SAROS profile's KosmicKrisp 64-bit atomics (`NPS5_KK_IMAGE_INT64=1 NPS5_KK_BUFFER_INT64=1 NPS5_KK_SWITCH_TREE=1 NPS5_KK_RESIDENCY_LOCK=1`),
`NPS5_PTHREAD_EXIT_SCE=1`, and the Nanite, ray-tracing and mesh-raster cvars off. Press Cross at the first screens; scroll the agreement
with the stick and accept it.

**Unreal console-variable list (optional, recommended).** The profile sets the render-fence cvar (`g.TimeoutForBlockOnRenderFence`, so a
cold first start that compiles the warm list does not trip the 120 s fence timeout), but the game only honours it when it has the list
of the engine's cvar slots, which is made from your own eboot (the same two steps as SAROS):

    uecvar xref -o x.json <your eboot.bin>
    uecvar dumplist x.json > titles/PPSA24096.list

then add `NPS5_UECVAR_LIST=<full path of that list>` to `titles/PPSA24096.env` (or pass `--env NPS5_UECVAR_LIST=...`). Without it Cronos
still runs: `nps5 run` prints one warning that the fence cvar is inert, and a cold first start may hit the fence timeout.
