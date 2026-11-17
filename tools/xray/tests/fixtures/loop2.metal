#include <metal_stdlib>
using namespace metal;
kernel void k(device uchar* b [[buffer(0)]], device float* o [[buffer(1)]], constant uint& n [[buffer(2)]], uint i [[thread_position_in_grid]]) {
  float r = 0.0;
  for (uint j = 0; j < n; j++) {
    r += *(device float*)(b + (i + j) * 4);
  }
  for (uint j = 0; j < n; j++) {
    r += *(device float*)(b + (i + j) * 8);
  }
  o[i] = r;
}
