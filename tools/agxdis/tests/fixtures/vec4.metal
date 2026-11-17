// one float4 device load; reference for scalar4
#include <metal_stdlib>
using namespace metal;
kernel void k(device const float4* a [[buffer(0)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    float4 v = a[gid.x];
    o[gid.x] = v.x * v.y + v.z * v.w;
}
