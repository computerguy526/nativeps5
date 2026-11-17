#include <metal_stdlib>
using namespace metal;
kernel void k(device uchar* b [[buffer(0)]], device float* o [[buffer(1)]], uint i [[thread_position_in_grid]]) {
  device uchar* p = b + i * 64;
  float4 a = *(device float4*)(p);
  float s0 = *(device float*)(p + 16);
  float s1 = *(device float*)(p + 32);
  uint u = *(device uint*)(p + 48);
  o[i] = a.x + a.y * 2.0 + a.z * 3.0 + a.w * 5.0 + s0 * 7.0 + s1 * 11.0 + float(u);
}
