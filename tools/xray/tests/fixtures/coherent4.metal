#include <metal_stdlib>
using namespace metal;
kernel void k(device uchar* b [[buffer(0)]], device float* o [[buffer(1)]], uint i [[thread_position_in_grid]]) {
  device uchar* p = b + i * 16;
  float a0 = *(coherent device float*)(p + 0);
  float a1 = *(coherent device float*)(p + 4);
  float a2 = *(coherent device float*)(p + 8);
  float a3 = *(coherent device float*)(p + 12);
  o[i] = a0 + a1 * 2.0 + a2 * 3.0 + a3 * 5.0;
}
