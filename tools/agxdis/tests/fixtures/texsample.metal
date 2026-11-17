// fragment shader with ONE texture sample; vertex shader passes through: EXPECT fragment tex_sample=1, vertex 0
#include <metal_stdlib>
using namespace metal;
struct V { float4 pos [[position]]; float2 uv; };
vertex V vs(uint vid [[vertex_id]]) {
    V o; o.uv = float2(float(vid & 1u), float(vid >> 1)); o.pos = float4(o.uv * 2.0f - 1.0f, 0.0f, 1.0f); return o;
}
fragment float4 fs(V in [[stage_in]], texture2d<float> t [[texture(0)]], sampler s [[sampler(0)]]) {
    return t.sample(s, in.uv);
}
