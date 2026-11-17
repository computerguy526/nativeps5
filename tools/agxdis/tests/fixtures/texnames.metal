// compute kernel with ONE filtered sample (sampler + explicit level) and NO read: EXPECT tex_sample=1 tex_load=0 (proves the sample/read naming, see README "Texture ops")
#include <metal_stdlib>
using namespace metal;
kernel void k(texture2d<float> t [[texture(0)]], sampler s [[sampler(0)]], device float4* o [[buffer(0)]], uint3 g [[thread_position_in_grid]]) {
    o[g.x] = t.sample(s, float2(g.xy) * 0.01f, level(0));
}
