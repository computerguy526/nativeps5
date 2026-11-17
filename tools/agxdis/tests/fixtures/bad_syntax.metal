// deliberately broken MSL: EXPECT exit 4 and the compiler's message
#include <metal_stdlib>
using namespace metal;
kernel void k(device float* o [[buffer(0)]], uint3 gid [[thread_position_in_grid]]) {
    o[gid.x] = this_identifier_does_not_exist + 1.0f;
}
