// 2 adjacent loads from buffer A and 2 adjacent from buffer B
#include <metal_stdlib>
using namespace metal;
kernel void k(device const float* a [[buffer(0)]], device const float* b [[buffer(2)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    uint i = gid.x * 2;
    o[gid.x] = a[i] * a[i + 1] + b[i] * b[i + 1];
}
