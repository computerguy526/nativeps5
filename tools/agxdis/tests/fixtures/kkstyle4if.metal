// as kkstyle4 but two of the four adjacent loads sit inside a data-dependent if (different basic block)
#include <metal_stdlib>
using namespace metal;
struct Buffer { uint64_t contents[1]; };
kernel void k(constant Buffer &buf0 [[buffer(0)]], device uint* o [[buffer(1)]], uint3 gid [[thread_position_in_grid]]) {
    ulong base = buf0.contents[0] + ulong(gid.x) * 16ul;
    uint a = *(device uint*)(base);
    uint b = *(device uint*)(base + 4ul);
    uint r = a ^ b;
    if (a & 1u) {
        uint c = *(device uint*)(base + 8ul);
        uint d = *(device uint*)(base + 12ul);
        r += c ^ d;
        o[gid.x + 1u] = r;
    }
    o[gid.x] = r;
}
