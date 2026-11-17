// 8 adjacent scalar uint loads a[i..i+7]
#include <metal_stdlib>
using namespace metal;
kernel void k(device const uint* a [[buffer(0)]], device uint* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    uint i = gid.x * 8;
    o[gid.x] = (a[i] ^ a[i + 1]) + (a[i + 2] ^ a[i + 3]) + (a[i + 4] ^ a[i + 5]) + (a[i + 6] ^ a[i + 7]);
}
