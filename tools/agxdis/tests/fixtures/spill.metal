// 512-float local array, written and read at data-dependent indices: cannot live in registers, MUST use stack memory
#include <metal_stdlib>
using namespace metal;
kernel void k(device const uint* a [[buffer(0)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    float arr[512];
    uint n = a[gid.x];
    for (uint j = 0; j < 512; j++) arr[j] = float(j ^ n);
    for (uint j = 0; j < (n & 63u); j++) arr[a[gid.x + j + 1] & 511u] += arr[(a[gid.x + j + 2] * 7u) & 511u];
    o[gid.x] = arr[n & 511u] + arr[(n >> 9) & 511u];
}
