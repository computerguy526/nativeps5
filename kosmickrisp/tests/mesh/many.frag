#version 450
layout(location = 0, component = 0) in float v0;
layout(location = 24, component = 3) in float v99;
layout(location = 0) out vec4 color;
void main() { color = vec4(v0 / 99.0, v99 / 99.0, 0.0, 1.0); }
