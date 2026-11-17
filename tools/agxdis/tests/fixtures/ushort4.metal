// 4 adjacent scalar ushort loads
#include <metal_stdlib>
using namespace metal;
kernel void k(device const ushort* a [[buffer(0)]], device uint* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    uint i = gid.x * 4;
    o[gid.x] = uint(a[i]) * uint(a[i + 1]) + uint(a[i + 2]) * uint(a[i + 3]);
}
