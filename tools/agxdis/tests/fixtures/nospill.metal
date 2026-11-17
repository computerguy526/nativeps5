// straight-line arithmetic, no arrays: EXPECT zero stack use
#include <metal_stdlib>
using namespace metal;
kernel void k(device const float4* a [[buffer(0)]], device float4* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    float4 v = a[gid.x];
    o[gid.x] = v * v.yzwx + v.zwxy;
}
