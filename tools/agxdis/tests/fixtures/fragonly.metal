// fragment-ONLY file in the shape of dumped translator output (stage_in with [[user(..)]], two colour outputs, no vertex function): agxdis must add its own vertex stage and report exactly one (fragment) function with 1 sample
#include <metal_stdlib>
using namespace metal;
struct FragmentIn {
    float4 vary_00 [[user(vary_00)]] ;
    float2 vary_01 [[user(vary_01)]] ;
    uint vary_02 [[user(vary_02), flat]] ;
};
struct FragmentOut {
    float4 color_0 [[color(0)]];
    uint4 color_1 [[color(1)]];
};
fragment FragmentOut main_entrypoint(float4 gl_FragCoord [[position]], FragmentIn in [[stage_in]],
                                     texture2d<float> t [[texture(0)]], sampler s [[sampler(0)]]) {
    FragmentOut out = {};
    out.color_0 = t.sample(s, in.vary_01) * in.vary_00;
    out.color_1 = uint4(in.vary_02, uint(gl_FragCoord.x), 0u, 1u);
    return out;
}
