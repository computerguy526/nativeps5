// compute kernel: 1 texture read + 1 texture write: EXPECT tex_load=1 tex_store=1
#include <metal_stdlib>
using namespace metal;
kernel void k(texture2d<float, access::read> src [[texture(0)]], texture2d<float, access::write> dst [[texture(1)]], uint3 gid [[thread_position_in_grid]]) {
    float4 v = src.read(gid.xy);
    dst.write(v.bgra * 0.5f, gid.xy);
}
