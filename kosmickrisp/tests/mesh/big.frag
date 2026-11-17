#version 450
#extension GL_EXT_mesh_shader : require
layout(location = 0) in vec4 vColor;
layout(location = 1) perprimitiveEXT in vec4 pColor;
layout(location = 2) in vec4 extra0;
layout(location = 3) in vec4 extra1;
layout(location = 0) out vec4 color;
void main() { color = vec4(vColor.rg * extra0.x + extra1.xy, pColor.b, 1.0); }
