// fragment shader with THREE texture samples from 3 textures: EXPECT fragment tex_sample=3
#include <metal_stdlib>
using namespace metal;
struct V { float4 pos [[position]]; float2 uv; };
vertex V vs(uint vid [[vertex_id]]) {
    V o; o.uv = float2(float(vid & 1u), float(vid >> 1)); o.pos = float4(o.uv * 2.0f - 1.0f, 0.0f, 1.0f); return o;
}
fragment float4 fs(V in [[stage_in]], texture2d<float> t0 [[texture(0)]], texture2d<float> t1 [[texture(1)]], texture2d<float> t2 [[texture(2)]], sampler s [[sampler(0)]]) {
    return t0.sample(s, in.uv) * t1.sample(s, in.uv * 2.0f) + t2.sample(s, in.uv.yx);
}
