# Rendering

Entries are symptom, cause, fix, where found. The switches named here are listed in
[env_switches.tsv](../env_switches.tsv). Shader translation has its own page ([shaders.md](shaders.md)).

## Black triangles in the G-buffer (per-vertex fragment inputs)

**Symptom.** Black triangles over lit geometry in a level; they were already in the G-buffer normal targets, so not
lighting, NaN or fast-math.

**Cause.** 55 of 106 pixel shaders seen read the *raw vertices* of "custom interpolation" parameters: packed 16-bit
fields that the shader unpacks and interpolates itself. KosmicKrisp has no per-vertex fragment input, and the
fallback handed those reads the host-interpolated float of the packed bits, which is garbage wherever the three
vertices differ.

**Fix.** Two were built. A generated pass-through geometry stage was correct but did not fit the memory limit in a
level. The one in use (`KYTY_PS_PER_VERTEX=unpack`) needs no geometry stage: the vertex shader exports the unpacked
fields as smooth floats through parameter aliases and the pixel shader reads those. Offline on the shader journal:
861 raw reads became 1,848 field reads in 54 pixel shaders, all variants valid SPIR-V. After it, colours were within
1-3 % of a console capture on the checked scenes.

Found on: PPSA21564.

## Barriers are the only ordering in Metal 4

**Finding (source audit, counts from a run).** KosmicKrisp is a Metal 4 driver: there is no hazard tracking, no
per-draw resource registration, and one bindless argument table. Ordering is only what the driver encodes:

1. every encoder ends with a full barrier (all stages -> all stages), so passes never overlap;
2. every barrier of every kind asks for resource-alias visibility, the expensive kind;
3. the back end sends about one barrier per compute dispatch (8,180 for 8,301 dispatches in one run; 92 % with
   write scopes) and each becomes a serialising barrier inside the compute encoder.

That is why the sum of commit spans is only 0-6 % above GPU busy time, and why one scene pays the full 10.8 ms for a
chain of nine dispatches and two passes that another scene hides behind other work.

**State.** `KK_BARRIER_OPT` (device-only visibility, skipping barriers with no write scope) is built and passes the
shader gate with identical MSL; it has **no frame-time number yet**. Treat it as untested.

**Measured dead ends on the same driver.** Merging render passes by deferring the encoder end: 0-1 merges per
second of 1,120 renderings, nothing to merge. Narrowing the render-encoder end barrier: +0.8 %, noise. Turning every
attachment load into "don't care" as a diagnostic: noise, so attachment loads cost nothing measurable. On PPSA30552,
turning clears into render-pass load operations was neutral, and the 4.2 ms the per-pass profile attributed to the
clear was serialisation caused by the profiler.

Found on: PPSA21564, PPSA30552.

## Whole mip chains for textures that are partly resident

See [memory.md](memory.md). The rendering side: `KYTY_TEXTURE_RESIDENT_TRIM=1` creates the native image of a
partly resident texture from its resident levels only. `KYTY_TEX_MIP_SKIP` (floor the first level of large sampled
2D textures) is built on top of it and unmeasured.

## Render targets that share guest memory

**Symptom.** Hundreds of megabytes per frame of copies between images; a "world map" that was really the loading
screen in front of it.

**Cause.** Transient render-target pools reuse the same guest memory under other tile modes, element sizes and
pitches (22 of 22 overlapping pairs had no view relation). Each reuse looks like aliasing to a cache keyed by guest
address. On PPSA30552 a menu blur widget's 4K targets shared memory with the 720p scene targets: about 146 MiB of
aliasing copies per frame.

**Fix.** No general one. For the blur, the low-quality override of the widget (-9.5 ms in two of two interleaved
pairs, with a visible change: a sharp backdrop instead of a soft one, so it was the user's decision). A storage
alias mode for the pool reuse exists behind a switch, default off (see the warning). Name a phase from frame dumps
before measuring it.

**Warning.** A change to when storage results reach guest memory once passed the frame-replay hash on two frames
while the title's picture was blown out. Gate such changes on frame dumps of a real run against an "off" run.

Found on: PPSA21564, PPSA30552.

## LOD statistics feed the game's texture streamer

**Symptom.** Textures decay to low mips, or every texture stays at mip 0 and memory thrashes.

**Cause.** The game reads back per-texture LOD statistics that its pixel shaders write, and streams textures by
them. Returning zeros makes textures decay; the legacy mode pins everything at mip 0. Writing real statistics costs
shader size: the giant pixel shaders have 212-296 LOD-statistics sites each ([shaders.md](shaders.md)).

**State.** `KYTY_LOD_STATS_MODE=gpu` is the preset. A comparison of the modes was running when this was written;
do not change the mode without a memory and picture comparison.

Found on: PPSA21564.

## Depth bounds reported but ignored

**Symptom.** Wrong shadow cascades.

**Cause.** The driver reported the depth-bounds feature, but Metal has depth bounds only on the newest GPU family;
on M1-M4 the test was silently skipped.

**Fix.** Emulation in the fragment shader, on by default. Frame time equal within noise.

Found on: PPSA30552.

## A draw that only works first in its command buffer

**Symptom.** Gameplay came up dark, dim or washed out in 7 of 9 runs; brightness "popped" when the camera moved.

**Cause.** The tonemap lookup-table draw took effect only when its render pass was the first of its command buffer,
so the table kept whatever fade or grade it last landed with. Exposure, lighting, resolution, depth bounds and two
merges were suspected and ruled out first.

**Lesson.** A setting that made 2 of 2 runs bright was declared a fix and was not one. An intermittent picture bug
needs at least five runs before it is called fixed.

Found on: PPSA30552.

## Video frames smeared, green at the bottom

**Symptom.** Small tutorial clips showed horizontal smear and a green stepped bottom edge; the 3840-wide logo video
was always correct.

**Cause.** Not the GPU back end. The video-player HLE laid out 880-wide frames with a 64-pixel row pitch (896) and
no crop; the game copies `pitch * height * 3 / 2` bytes into a texture whose GPU pitch is 1024. Widths that are a
multiple of 256 happened to work. An offline simulation of the two pitches reproduced the frame dump exactly, before
any run.

**Fix.** For PS5 images the pitch is the width rounded up to 256, with the crop offsets reported and the padding
filled with the edge pixel (`NPS5_AVPLAYER_PITCH_ALIGN` overrides).

Found on: PPSA21564.

## Blurry overlay text

**Cause.** The window was created without the high-pixel-density flag, so the swapchain was in points and the
compositor magnified it 2x; the text was a 13-pixel bitmap font.

**Fix.** A HiDPI drawable and an outline font. Frame dumps change size with it: compare content, not dimensions.

## Where an upscaler goes

A scaler at present has nothing to upscale unless the display buffer shrinks, and then it scales the HUD too (the
"720p plus MetalFX is too blurry" result on PPSA30552). PPSA21564 already has its own temporal upscale pass before
the HUD (scene at the DRS size -> anti-aliasing -> temporal upscale to 2432x1368 -> post -> UI and composite at 4K).
Replacing that one pass with MetalFX is the right place; the hook is built and **not validated**: the pass matcher
has not engaged on this game build. A lower internal resolution with the game's own upscaler works: all scene passes
at 1280x720 gave 35.4 ms on the world map with a steady picture.

A partial resolution patch is worse than none: changing one of two size sites left one pass at 720p against a 1080p
depth buffer and the picture flickered.

## Picture diagnostics

All off by default, one boolean test per draw when off: a draw filter by shader hash, target size or address, or
bound texture (`KYTY_DRAW_FILTER`), a per-draw log of every bound image (`KYTY_DRAW_LOG`), a per-pass menu of hashes
(`KYTY_PASS_LOG`), a knock-out (`KYTY_DRAW_SKIP=1`), and dumps of cached colour targets (`KYTY_DUMP_TARGETS`).
Pictures come only from these dumps, never from screenshots ([machine-safety.md](machine-safety.md)).
