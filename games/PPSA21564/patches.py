# ASTRO BOT (PPSA21564) 01.007: patches of OUR translated arm64 binary, never of the dump. DATA ONLY.
#
# `scripts/nps5lib/patch.py` reads this file with ast.literal_eval (nothing here is executed; no function, no
# arithmetic) and applies it to a COPY of a translated binary:
#   patch.py PPSA21564 --list | --check <native> | <native> dr,nt[,lowres=1280x720] [-o COPY] | --docs
# Rules it enforces: the binary's embedded build ID (nps5_input_sha256) must match BUILDS; every site's word must
# be its original or its new value, else NOTHING is written (fail closed); the copy is re-signed ad hoc and gets a
# <copy>.patches.json sidecar.
#
# A site is (guest function entry, byte offset inside the translated function, original word, new word, note).
# The translated binary keeps one symbol _fn_<guest entry> per guest function (NPS5_KEEP_SYMS=1), and the offset
# from that symbol is the same in every link of the same build (checked on 5 links), so no per-link address is kept.
# A parametrised new word is (base word, [(parameter, kind, shift), ...]): base | (value << shift), kind 'f32hi' =
# upper 16 bits of the float32 of the parameter (low 16 bits must be zero), 'u16' = the parameter itself (< 65536).

BUILDS = {'01.007': '70d673f7c43edb18'}

CUTS = {'01.007': {
    'dr': [(0x7818870, 0x304, 0x2b1001ce, 0x6a1f03ee, 'adds w14,w14,w16 -> ands w14,wzr,wzr')],
    'mode': [(0x77bceb0, 0x05c, 0x994180f4, 0x2a1f03f4, 'ldapur w20,[x7,#0x18] -> mov w20,wzr')],
    'nt': [(0x77eb850, 0x350, 0x54012ca0, 0x14000965, 'b.eq L -> b L')],
    'gi': [(0x751d090, 0x058, 0x38bfc0f3, 0x2a1f03f3, 'ldaprb w19,[x7] -> mov w19,wzr')],
}}

# lowres WxH (1600x900 = "dr900", 1280x720 = "dr720"). BOTH functions are required: site 1 alone left one pass
# small against a 1080p depth buffer and the picture flickered. The first three words of each site replace a load
# of the level width as a float (the game derives its texture LOD bias from it) by an immediate; the last two set
# the {width, height} pair. The game's own temporal upscaler still outputs 2432x1368.
PARAM_CUTS = {'01.007': {
    'lowres': {'param': 'WxH', 'sites': [
        (0x77be670, 0x1d4, 0xd29f8390, (0x52a00010, [('W', 'f32hi', 5)]), 'mov x16,#0xfc1c -> movz w16,#<float hi of W>,lsl#16'),
        (0x77be670, 0x1d8, 0xf2a10e90, 0x1e270200, 'movk x16,#0x874,lsl#16 -> fmov s0,w16'),
        (0x77be670, 0x1dc, 0xbc706b80, 0xd503201f, 'ldr s0,[x28,x16] -> nop'),
        (0x77be670, 0x1fc, 0xd280f016, (0xd2800016, [('W', 'u16', 5)]), 'mov x22,#1920 -> mov x22,#W'),
        (0x77be670, 0x200, 0xf2c08716, (0xf2c00016, [('H', 'u16', 5)]), 'movk x22,#1080,lsl#32 -> movk x22,#H,lsl#32'),
        (0x74cef60, 0x2bf4, 0xd29f8390, (0x52a00010, [('W', 'f32hi', 5)]), 'mov x16,#0xfc1c -> movz w16,#<float hi of W>,lsl#16'),
        (0x74cef60, 0x2bf8, 0xf2a10e90, 0x1e270200, 'movk x16,#0x874,lsl#16 -> fmov s0,w16'),
        (0x74cef60, 0x2bfc, 0xbc706b80, 0xd503201f, 'ldr s0,[x28,x16] -> nop'),
        (0x74cef60, 0x2c08, 0xd280f015, (0xd2800015, [('W', 'u16', 5)]), 'mov x21,#1920 -> mov x21,#W'),
        (0x74cef60, 0x2c0c, 0xf2c08715, (0xf2c00015, [('H', 'u16', 5)]), 'movk x21,#1080,lsl#32 -> movk x21,#H,lsl#32'),
    ]},
}}

# A patch that needs another one: lowres rewrites the level-0 case, which only exists once dr locks level 0.
REQUIRES = {'lowres': ['dr']}

# Fallback for a binary linked WITHOUT symbols: host address of each guest function in the 'base' lineage, plus the
# shift of every known relink (the same rows as `lineage` in game.tsv). Only used with --lineage NAME.
LINEAGES = {'base': 0x0, 'ab2': 0xcc0, 'kyty3': 0x7760, 'kyty5': 0x7d50, 'kyty6': 0x82f0, 'kyty6_pb': 0x86d8}
FALLBACK_FUNCS = {
    0x7818870: 0x1115f6c34, 0x77bceb0: 0x1114291f8, 0x77eb850: 0x11153fe10, 0x751d090: 0x110cecb48,
    0x77be670: 0x11143b340, 0x74cef60: 0x110bb8118,
}

# now/goal = binding level (host > engine > game > build). review = 'reviewed' or 'unreviewed'. checked = evidence.
# setting (optional) = the runtime setting that replaces the patch once it exists; state 'pending' until then.
BINDING = [
    {'what': 'dr', 'purpose': 'frame rate: lock dynamic resolution at level 0 (1920x1080)', 'now': 'build', 'goal': 'host',
     'guest': 'fn 0x7818870 (DRS controller), level = step + base at 0x7818971',
     'why': 'superseded by the honest GPU clock (KYTY_EOP_TIMESTAMPS=cbtime), with which the game reaches 1080p by itself; kept in the canonical binary, harmless there',
     'review': 'reviewed',
     'checked': 'world map 80.5 -> 45.3 ms with record-time clocks; title and first level unchanged'},
    {'what': 'mode', 'purpose': 'one 1080p scene-parameter set instead of five (resolution mode argument = 0)', 'now': 'build', 'goal': 'build',
     'guest': 'fn 0x77bceb0 (scene-parameter set builder), mode argument load at 0x77bcecc',
     'why': 'title benchmarking only: not a default',
     'review': 'reviewed',
     'checked': 'title 43 -> 34 ms (2 of 2); world map 45 -> 97 ms (n=1), so never global'},
    {'what': 'nt', 'purpose': 'always take the non-tiled lighting renderer (skips the tiled-lighting compute megakernels)', 'now': 'build', 'goal': 'game',
     'guest': 'fn 0x77eb850 (lighting stage selection), test/je at 0x77eb993',
     'why': 'changes the lighting path, so the picture is the user\'s decision; becomes a user setting through a recompiler patch point',
     'review': 'reviewed',
     'checked': 'world map 45.1 -> 37.4 ms on dr+nt (canonical Kyty binary since 2026-11-17); neon outlines slightly dimmer',
     'setting': {'name': 'nt', 'state': 'pending'}},
    {'what': 'gi', 'purpose': 'store 0 for the GI probe flag', 'now': 'build', 'goal': 'build',
     'guest': 'fn 0x751d090 (GI probe flag copy), byte load at 0x751d0b3',
     'why': 'game-bound',
     'review': 'unreviewed',
     'checked': 'effect on picture and frame time not measured on the current stack'},
    {'what': 'lowres', 'purpose': 'dynamic-resolution level 0 at WxH instead of 1920x1080', 'now': 'build', 'goal': 'build',
     'guest': 'fn 0x77be670 and fn 0x74cef60 (scene parameters constructor, level-0 case)',
     'why': 'input for an upscaler experiment, not a default; apply on top of dr',
     'review': 'unreviewed',
     'checked': '1280x720: world map 35.4 ms, steady picture (n=1). 1600x900 unreviewed'},
]
