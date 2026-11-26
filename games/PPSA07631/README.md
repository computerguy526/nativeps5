# SAROS (`PPSA07631`)

Game note. No game data is here and none is accepted; you need your own dump ([CONTRIBUTING.md](../../CONTRIBUTING.md)).
**Status (v1.4.2): builds with `nps5 build` and starts; played, plays well; frame rate PENDING; warm shader list (650 shaders, 222 pipelines) since v1.4.2.** v1.4.0 (and v1.3.x) could not build SAROS: the profile had no translate settings, so the translator stopped on unsupported instructions, the packed-code discovery was missing, and from a folder of links the translator linked none of the game's modules. v1.4.1 fixes all three in `nativeps5-recomp`. Check of the v1.4.1 player path (M5 Pro, `nps5 build` from the dump and from a folder of links, then `nps5 run`, three 150 s runs with the v1.4.0 libraries): all three ended with verdict DONE_FIXED, 0 unimplemented-function calls, motion works on a virtual pad, 0 live shader compiles at the peak on a warm cache, peak footprint 13.0 to 13.8 GiB, and the last frame dump shows the title's animated background. A user played SAROS in-level on a v1.4.1 build (M5 Pro, DualSense): it plays well. No frame rate is claimed. The v1.4.0 release-candidate sessions (13.7 and 14.5 GB) used a development binary. No frame-rate number is claimed: the figures below from development builds are not v1.4 measurements.
**Was not playable on v1.3.x:** with the hand shaders on the title stalled at about 1 fps; with them off the background was missing. v1.4 carries the generic indirect-image path, the Nanite raw raster parameters, the shader-bundle compaction and the player profile in [game.tsv](game.tsv) (MetalFX temporal 720p to 4K, the 32-bit integer target fix, the Unreal cvar settings).

- **Version:** 01.005.002 (SDK 11.00), eboot Build ID `6d383b3837ff06b8`.
- **Engine and graphics:** Unreal Engine 5.5 line (Nanite, Lumen, virtual shadow maps, TSR, MegaLights and Substrate present); AGC; the
  title outputs 3840x2160 from a scene rendered at about 720p.
- **Back end:** `kyty` (KytyPS5's renderer on KosmicKrisp), with the KosmicKrisp patches 0031-0040 (64-bit image and buffer atomics,
  the switch tree, the residency lock).
- **Status:** see the top of this note. Earlier development builds booted, played the intro and gameplay with a controller at
  about 19 fps at a 720p scene on an M5 Pro with a 15-16 GB footprint; these are development-build figures, not v1.4 measurements. A 16 GB Mac cannot run it.
- **Profile files:** [game.tsv](game.tsv). The title's runtime switches live in the translator profile `profiles/PPSA07631.json` of
  `nativeps5-recomp` (they are baked into the translated program). No `patches.py`, no pipeline list (see "Shader cache").

## Set it up (v1.4)

**Needs v1.4.1 or later** (`nativeps5-recomp` v1.4.1). With v1.4.0, step 2 does not give a working program.

1. **Pack the dump first** (79 GB instead of 153 GB; the largest `.ucas` is 64 GB and APFS cannot compress a file over 2 GiB by
   itself): [disk-space](../../docs/reference/disk-space.md). The runtime reads the packed copy with no setting
   ([large games](../../docs/reference/large-games.md)).
2. **Translate the dump with `nps5 build`.** `nps5 build` picks the profile by title ID. The profile translates with
   `-packed-layout` (the title packs its functions without alignment) and `-allow-unsupported` (29 MMX and absolute-address forms the
   translator does not lower; none was hit in the checked runs). You can translate straight from the dump: the translator leaves a dump tool's
   `fakelib/` folder out by itself, and it also finds the game's modules (`sce_module/`, `prx/`) through a folder of links. Do not use
   `-exclude-module` for `libSceAmpr`: it binds the system library to empty stubs and the game stops at its first file read.
   **Check the build log:** its `HLE imports` line must say `146 linked PRX` and `53 unresolved`. `0 linked PRX` means the game's own libc was
   not found, and the run then logs `Called unimplemented function '_Iswctype'` and stays black.
   Measured on an M5 Pro: about seven minutes (681,000 functions); the program is 776 MB.
3. **Create `titles/PPSA07631.env`** from `titles/example.env`: `BIN` (the program `nps5 build` printed), `APP0` (your dump), `GPU=kyty`.
4. **The Unreal console-variable list (required).** The profile's cvar settings (below) and `NPS5_TITLE_RESOLUTION` need the title's cvar
   list (`NPS5_UECVAR_LIST`) made from your own eboot. `nps5 run PPSA07631` refuses to start (exit 78) when it is missing or not a file:
   without it the runtime only logs `need NPS5_UECVAR_LIST` and sets nothing, which showed as flicker, stalls and about 120 ms frames.
   Recipe from `docs/m3/uecvar.md` in `nativeps5-recomp`: `uecvar xref -o x.json <eboot.bin>` then `uecvar dumplist x.json > saros.list`,
   and put `NPS5_UECVAR_LIST=<path>` in the env file. (The recipe is documented, but no one has re-run it from a clean checkout for this
   release: if the list comes out empty, open an issue.)
5. **Run.** `scripts/nps5 run PPSA07631 --play`. The first minute is boot and intro (press Cross to skip). **The first start with an
   empty shader cache opens and closes a small warm-up window by itself; that is not a crash.** The title compiles most of its shaders
   while you play the first time: expect stutter for several minutes, and a first run that is slow to reach its first level. Quit with
   the window's close button; the caches are saved on the way out.

## What the profile needs and why

| Setting | Why |
|---|---|
| `KYTY_SAROS_HANDSHADERS=1` | marks the bindless-lighting kernels (8 listed shader hashes): extra logging, and their own program-cache key with `KYTY_SAROS_PROGRAM_CACHE=1`. Up to v1.3 it also opened them to the indirect-image path; in v1.4 the generic indirect-image path does that |
| `KYTY_SAROS_NANITE_RAW=1` | Nanite hardware raster pixel shaders find their triangle from raw vertex masks; without it each pixel of a cluster is shaded with another triangle (a mosaic of normals and colour) |
| `KYTY_BUNDLE_COMPACT=1` | compacts the shader bundles of the command processor |
| `NPS5_KK_IMAGE_INT64`, `NPS5_KK_BUFFER_INT64`, `NPS5_KK_SWITCH_TREE`, `NPS5_KK_RESIDENCY_LOCK` | KosmicKrisp 64-bit atomics (Nanite visibility buffer), the switch tree and the residency lock |
| `KYTY_PS_UINT32_TARGETS=1` | integer (UINT, SINT) colour targets: the streaming-virtual-texture page table was empty without it and the character was flat grey |
| `KYTY_PREDICATION_NO_DRAIN=2`, `KYTY_DCC_KNOWN_FILL_ALIASED=1`, `KYTY_GDS_EOP_MODE=defer`, `KYTY_FAULT_*`, `KYTY_READBACK_EAGER_PAGES`, `NPS5_SHADOW_FAULT_FAST` | command-processor waits and write-fault costs |
| `NPS5_PTHREAD_EXIT_SCE=1` | the title's threads leave through the console thread-exit path |
| `NPS5_AVPLAYER_VT=2` | movies decode on the Apple video decoder |
| `NPS5_UECVAR_SET`: Nanite compute culling, ray tracing, primitive and mesh-shader raster off; `r.LightFunctionQuality=1` | what a Mac cannot afford, and the lighting look: `r.LightFunctionQuality=1` gives the console's darker, moodier light at no measured cost (scene luminance 88 -> 41, console 21; an owner playtest approved it) |
| `KYTY_VRAM_BUDGET_MB=8000` | GPU memory planning |
| footprint guard `NPS5_SELF_MEM_LIMIT_MB=17408` (`game.tsv`) | the title stays at 15-16 GB; the guard is never raised to make a scene pass |

**Scene resolution.** Not in the profile: it needs the cvar list. The tested configuration sets
`r.ScreenPercentage=33.34`, `r.DynamicRes.MinScreenPercentage=33.34`, `r.DynamicRes.MaxScreenPercentage=33.34` and
`r.SecondaryScreenPercentage.GameViewport=100` through `NPS5_UECVAR_SET` (the title's own TSR upscales to 4K), or `NPS5_TITLE_RESOLUTION=1280x720`.
Set `NPS5_UECVAR_SET_FOR=100000` so the values are kept for the whole run (the default stops after 120 s). Also set the game's
scalability settings to 0 (`sg.*Quality=0` in the game user settings). Heavier shadows cost: cascaded +11-19 ms, virtual shadow maps
+34 ms and the footprint guard.

**Optional, off by default (all experimental):**

- `KYTY_SAROS_PROGRAM_CACHE=1`: keeps the hand-shader and Nanite-raw programs in the persistent program cache (the switch state is in the
  key). Off by default, so those programs (the Nanite mesh programs among them) recompile on every launch; the saving has not been measured on v1.4.
- **MetalFX in Unreal's third-party upscaler slot** (`KYTY_METALFX=temporal KYTY_METALFX_UE=fsr3 KYTY_METALFX_UE_SKIP=1
  KYTY_METALFX_MAX_IN_W=1920 KYTY_METALFX_JITTER_SIGN=-1 KYTY_METALFX_DEPTH_REVERSED=1` with the FSR3 cvars
  `r.FidelityFX.FSR3.Enabled=1`, `...CreateReactiveMask=0`, `...QualityMode=4`, `r.DynamicRes.OperationMode=0`). Owner verdicts on the
  tested arms: 560p scene to 4K "looks good, very responsive"; 720p scene to 4K had a spawn-point flicker and fireball artefacts.
  Fireball artefacts were traced to the mip-layout clamp, fixed in this release; the check with MetalFX on is still open.

## Shader cache

There is no shipped pipeline list for SAROS: the game's shaders live in its Unreal containers, not in the executable, so the list
tool cannot locate them. Instead the profile gives the title one stable cache folder per player (`cache kind=journal`):
`<scratch>/cache/PPSA07631/kyty-player`. The caches build while you play and are saved every five minutes and on the way out: driver
cache, program cache, KosmicKrisp archive, Metal libraries, the pipeline journal and the shader journal. **The first session compiles
shaders while you play, as before (stutter for several minutes). Later sessions load these caches and start warm.** Quit with the
window's close button so the caches are written. A warm cache of a developer machine held about 12,400 Metal libraries, 173 archive
segments and 2,930 program records; it is rebuilt on yours. To start over, delete that folder (`nps5 run` makes it again).
Limits: session 1 is as cold as before; a folder made by another machine or another build does not transfer. The first start after a
v1.3 update rebuilds every title's cache once (the shader recompiler changed). Optional cache compression:
[disk-space](../../docs/reference/disk-space.md).

## Known issues

- Slow: about 19 fps in gameplay at a 720p scene (see Status). The command processor is the limit (bind and synchronisation cost per
  draw); ray tracing is off.
- The first minutes of a cold cache stutter (several runs warm it; keep using the same cache folder).
- The engine path that indexes past a direct-memory base occasionally aborts early in a run (a guest data abort at engine init, seen
  a few times on development builds, not reproduced on demand). Run again.
- A rare crash at startup in the game's own static initialisation (`fn_64ed360`, a null-guard fault in its logging setup; also seen on builds
  before v1.3.0, trigger unknown). Relaunch: it does not repeat on the next start.
- The clay-like flat look of the main character (empty streaming-virtual-texture page table) is fixed by `KYTY_PS_UINT32_TARGETS=1`,
  which the profile sets; if the character is flat grey the switch is not reaching the game.
- A decal pixel shader is known to render wrongly (open).
- Frame dump and probe tools exist for development only and are off.
- Audio, saves and trophies: not verified on this release.
- **SAROS has a warm shader list since v1.4.2 (650 shaders, 222 pipelines; hashes only, the code comes from your own game through the game's decoder).** `nps5 cache prepare` compiles it before the first play (459 s in the check on an M5 Pro); two warm starts after it compiled 0 shaders live. Shaders that are not on the list still compile while you play and are kept in your per-player cache. Frame rates were not measured.

## Found on this game

[memory](../../docs/reference/memory.md): never raise the guard; whole mip chains. [large games](../../docs/reference/large-games.md).
