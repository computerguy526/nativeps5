#include <metal_stdlib>
using namespace metal;
kernel void k(device uchar* b [[buffer(0)]], device float* o [[buffer(1)]], uint i [[thread_position_in_grid]]) {
  device uchar* p = b + i * 16;
  float4 a = float4(*(coherent device packed_float4*)(p));
  o[i] = a.x + a.y * 2.0 + a.z * 3.0 + a.w * 5.0;
}
