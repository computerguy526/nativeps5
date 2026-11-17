#version 450
// A vertex pipeline drawn between mesh draws in the same render pass (pipeline switches).
layout(location = 0) out vec4 vColor;
void main() {
    const vec2 p = vec2(gl_VertexIndex == 1 ? 1.0 : 0.0, gl_VertexIndex == 2 ? 1.0 : 0.0);
    gl_Position = vec4(-1.0 + p * 0.25, 0.1, 1.0);
    vColor = vec4(1.0, 1.0, 1.0, 1.0);
}
