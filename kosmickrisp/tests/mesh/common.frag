#version 450
#extension GL_EXT_mesh_shader : require
layout(location = 0) in vec4 vColor;
layout(location = 1) perprimitiveEXT in vec4 pColor;
layout(location = 0) out vec4 color;
void main() { color = vec4(vColor.rgb * 0.5 + pColor.rgb * 0.5, 1.0); }
