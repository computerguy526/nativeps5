// two sequential data-dependent loops: EXPECT exactly 2 loops
#include <metal_stdlib>
using namespace metal;
kernel void k(device const uint* a [[buffer(0)]], device float* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    uint n = a[gid.x];
    float s = 0.0f;
    for (uint j = 0; j < n; j++) s = s * 1.0001f + float(a[gid.x + j]);
    o[gid.x] = s;
    uint m = a[gid.x + 1];
    uint t = 1;
    for (uint j = 0; j < m; j++) t = t * 33u + a[gid.x + j * 3u];
    o[gid.x + 1] = float(t);
}
