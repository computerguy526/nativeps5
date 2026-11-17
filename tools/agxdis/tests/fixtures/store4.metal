// 4 adjacent scalar device float stores o[i..i+3]
#include <metal_stdlib>
using namespace metal;
kernel void k(device const float4* a [[buffer(0)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    float4 v = a[gid.x];
    uint i = gid.x * 4;
    o[i] = v.x + 1.0f; o[i + 1] = v.y * 2.0f; o[i + 2] = v.z - 3.0f; o[i + 3] = v.w * v.x;
}
