#version 450
#extension GL_ARB_shader_viewport_layer_array : require
// Layered rendering without a mesh or geometry stage: one instance per layer (shaderOutputLayer).
layout(location = 0) flat out float vLayer;
layout(push_constant) uniform PC { vec4 color; } pc;
void main() {
    gl_Position = vec4((gl_VertexIndex & 1) != 0 ? 3.0 : -1.0, (gl_VertexIndex & 2) != 0 ? 3.0 : -1.0, 0.5, 1.0);
    vLayer = float(gl_InstanceIndex);
    gl_Layer = gl_InstanceIndex;
}
