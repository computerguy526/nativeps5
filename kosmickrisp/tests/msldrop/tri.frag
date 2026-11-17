#version 450
layout(location = 0) out vec4 color;
void main() { color = vec4(fract(gl_FragCoord.xy / 16.0), 0.5, 1.0); }
