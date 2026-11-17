// valid MSL without any vertex/fragment/kernel entry point: EXPECT exit 5
#include <metal_stdlib>
using namespace metal;
static float helper(float x) { return x * 2.0f; }
