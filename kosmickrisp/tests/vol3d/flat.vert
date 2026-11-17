#version 450
// Unlayered vertex draw (pass A): one full-target triangle.
layout(location = 0) flat out float vLayer;
layout(push_constant) uniform PC { vec4 color; } pc;
void main() {
    gl_Position = vec4((gl_VertexIndex & 1) != 0 ? 3.0 : -1.0, (gl_VertexIndex & 2) != 0 ? 3.0 : -1.0, 0.5, 1.0);
    vLayer = 0.0;
}
