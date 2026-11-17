#version 450
// Unlayered vertex draw (pass A) that reads three vertex buffers (bindings 0, 1, 2 with strides 16, 20, 24): one full-target
// triangle. KosmicKrisp keeps the binding strides in the same root-table words a mesh shader reads as its base workgroup id.
layout(location = 0) in vec4 a0;
layout(location = 1) in vec4 a1;
layout(location = 2) in vec4 a2;
layout(location = 0) flat out float vLayer;
layout(push_constant) uniform PC { vec4 color; } pc;
void main() {
    gl_Position = vec4((gl_VertexIndex & 1) != 0 ? 3.0 : -1.0, (gl_VertexIndex & 2) != 0 ? 3.0 : -1.0, 0.5, 1.0) + 0.0 * (a0 + a1 + a2);
    vLayer = 0.0;
}
