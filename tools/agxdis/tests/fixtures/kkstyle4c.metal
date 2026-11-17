// as kkstyle4 but the pointers are "coherent device" (how the megakernels read shared memory)
#include <metal_stdlib>
using namespace metal;
struct Buffer { uint64_t contents[1]; };
kernel void k(constant Buffer &buf0 [[buffer(0)]], device uint* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    ulong base = buf0.contents[0] + ulong(gid.x) * 16ul;
    uint a = *(coherent device uint*)(base);
    uint b = *(coherent device uint*)(base + 4ul);
    uint c = *(coherent device uint*)(base + 8ul);
    uint d = *(coherent device uint*)(base + 12ul);
    o[gid.x] = (a ^ b) + (c ^ d);
}
