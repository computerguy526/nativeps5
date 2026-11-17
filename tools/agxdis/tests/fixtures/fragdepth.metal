// fragment-only with a [[depth(any)]] output and one colour: needs a depth attachment + stub vertex stage; EXPECT exit 0, one fragment STATS line
#include <metal_stdlib>
using namespace metal;
struct FragmentIn { float2 uv [[user(uv)]]; };
struct FragmentOut { float4 c [[color(0)]]; float d [[depth(any)]]; };
fragment FragmentOut fs(FragmentIn in [[stage_in]]) { FragmentOut o; o.c = float4(in.uv, 0, 1); o.d = in.uv.x; return o; }
