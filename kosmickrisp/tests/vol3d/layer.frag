#version 450
layout(location = 0) flat in float vLayer;
layout(location = 0) out vec4 color;
layout(push_constant) uniform PC { vec4 color; } pc;
void main() { color = vec4(pc.color.r, vLayer / 255.0, pc.color.b, 1.0); }
