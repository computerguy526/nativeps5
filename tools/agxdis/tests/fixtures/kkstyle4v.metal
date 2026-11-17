// translator style, already vectorised in the source: ONE packed_uint4 load through an integer address (what KK_MSL_OPT_VEC_LOADS emits); reference for kkstyle4
#include <metal_stdlib>
using namespace metal;
struct Buffer { uint64_t contents[1]; };
kernel void k(constant Buffer &buf0 [[buffer(0)]], device uint* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    ulong base = buf0.contents[0] + ulong(gid.x) * 16ul;
    uint4 v = uint4(*(device packed_uint4*)(base));
    o[gid.x] = (v.x ^ v.y) + (v.z ^ v.w);
}
