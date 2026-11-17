// 4 adjacent scalar device float loads a[i..i+3]; EXPECT merged into 1 xyzw i32 device_load (measured)
#include <metal_stdlib>
using namespace metal;
kernel void k(device const float* a [[buffer(0)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    uint i = gid.x * 4;
    o[gid.x] = a[i] * a[i + 1] + a[i + 2] * a[i + 3];
}
