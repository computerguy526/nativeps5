// 4 adjacent scalar loads, each separated by a store through a possibly-aliasing pointer
#include <metal_stdlib>
using namespace metal;
kernel void k(device float* a [[buffer(0)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    uint i = gid.x * 4;
    float x = a[i];     o[i] = x;
    float y = a[i + 1]; o[i + 1] = x * y;
    float z = a[i + 2]; o[i + 2] = y + z;
    float w = a[i + 3]; o[i + 3] = z * w;
}
