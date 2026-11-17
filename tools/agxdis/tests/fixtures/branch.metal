// if/else with device stores on both sides (cannot become a select): EXPECT >= 1 branch, 0 loops
#include <metal_stdlib>
using namespace metal;
kernel void k(device const uint* a [[buffer(0)]], device float* o [[buffer(1)]], device float* p [[buffer(2)]], uint3 gid [[thread_position_in_grid]]) {
    uint n = a[gid.x];
    if (n & 1u) { o[gid.x] = sqrt(float(n)); }
    else        { p[n & 255u] = float(n) * 3.0f; }
}
