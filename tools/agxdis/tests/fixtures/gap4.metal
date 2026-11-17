// 4 scalar loads with gaps a[i], a[i+2], a[i+4], a[i+6]: not adjacent, cannot be one 16-byte load
#include <metal_stdlib>
using namespace metal;
kernel void k(device const float* a [[buffer(0)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    uint i = gid.x * 8;
    o[gid.x] = a[i] * a[i + 2] + a[i + 4] * a[i + 6];
}
