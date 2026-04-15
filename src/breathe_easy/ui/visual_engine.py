import numpy as np
from PyQt5.QtWidgets import QOpenGLWidget
from PyQt5.QtCore import QTimer
import moderngl

# "Journey" aesthetic fragment shader. 
# Warm golds, ambers, deep blues. Particles/curves floating.
FRAGMENT_SHADER = """
#version 330

uniform float u_time;
uniform vec2 u_resolution;
uniform float u_breath;      // 0.0 (exhale) to 1.0 (inhale)
uniform float u_coherence;   // 0.0 to 1.0
uniform float u_sync;        // 0.0 to 1.0 (Player's sync to target)

out vec4 fragColor;

// Basic 2D noise
float hash(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

float noise(vec2 x) {
    vec2 p = floor(x);
    vec2 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(p + vec2(0.0, 0.0)), hash(p + vec2(1.0, 0.0)), f.x),
               mix(hash(p + vec2(0.0, 1.0)), hash(p + vec2(1.0, 1.0)), f.x), f.y);
}

// FBM
float fbm(vec2 x) {
    float v = 0.0;
    float a = 0.5;
    vec2 shift = vec2(100.0);
    mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
    for (int i = 0; i < 5; ++i) {
        v += a * noise(x);
        x = rot * x * 2.0 + shift;
        a *= 0.5;
    }
    return v;
}

void main() {
    // Perfectly centered UV mapping solving the aspect ratio drift
    vec2 uv = (gl_FragCoord.xy - 0.5 * u_resolution.xy) / min(u_resolution.x, u_resolution.y);
    uv *= 2.0;

    // Movement speeds driven by breath and time
    float flow_speed = u_time * (0.2 + u_breath * 0.3);
    
    // Create flowing waves pushing laterally and vertically
    vec2 p = vec2(uv.x + u_time * 0.1, uv.y + flow_speed);
    
    // Distort space with FBM
    float chaos = mix(2.5, 0.5, u_coherence); // High coherence = smoother waves
    float d = fbm(p * chaos);
    
    // Rhythmic bioluminescent swells
    float wave = sin(uv.y * 5.0 + d * 5.0 - u_time * 2.0);
    float glow = smoothstep(0.3, 0.9, wave);
    
    // A central glowing bioluminescent core that expands heavily on inhale
    float r = length(uv);
    float orb = smoothstep(0.8 + u_breath * 0.6, 0.1, r);
    glow = max(glow, orb * (0.4 + u_breath * 0.6));

    // Aesthetics: Deep abyss blue base
    vec3 baseCol = vec3(0.01, 0.04, 0.12); 
    // Cyan oceanic bioluminescence
    vec3 midCol = vec3(0.0, 0.6, 0.8);    
    // Golden/Amber highlights for peak Zen
    vec3 peakCol = vec3(1.0, 0.8, 0.2);   
    
    // Mix based on breath & coherence
    vec3 finalCol = mix(baseCol, midCol, glow * (0.3 + u_coherence * 0.7));
    // As coherence goes up, inject the amber/gold vibes into the peaks
    finalCol = mix(finalCol, peakCol, pow(glow, 2.0) * u_breath * u_coherence); 
    
    // Gameplay Sync Reward: Intense bioluminescent particles appear the closer you sync
    float dust = smoothstep(0.96, 1.0, noise(uv * 50.0 + vec2(0.0, u_time * 2.0)));
    float dust_brightness = pow(u_sync, 2.0) * 3.0; // Grows exponentially when highly synced
    finalCol += mix(midCol, peakCol, u_breath) * dust * dust_brightness;

    // Vignette
    float vig = 1.0 - smoothstep(0.8, 2.0, length(uv));
    finalCol *= vig;

    fragColor = vec4(finalCol, 1.0);
}
"""

VERTEX_SHADER = """
#version 330
in vec2 in_vert;
void main() {
    gl_Position = vec4(in_vert, 0.0, 1.0);
}
"""

class VisualEngineWidget(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctx = None
        self.prog = None
        self.vbo = None
        self.vao = None
        
        self.u_breath = 0.0
        self.u_coherence = 0.5
        self.u_sync = 0.0
        self.u_time = 0.0
        
        # Frame timer ~ 60 FPS
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(16)

    def set_biofeedback(self, breath: float, coherence: float, sync: float):
        self.u_breath = max(0.0, min(1.0, breath))
        self.u_coherence = max(0.0, min(1.0, coherence))
        self.u_sync = max(0.0, min(1.0, sync))

    def update_frame(self):
        self.u_time += 0.016
        self.update() # triggers paintGL

    def initializeGL(self):
        try:
            # Attach to the existing QOpenGLWidget context
            self.ctx = moderngl.create_context()
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

            self.prog = self.ctx.program(
                vertex_shader=VERTEX_SHADER,
                fragment_shader=FRAGMENT_SHADER
            )

            # Full-screen quad
            vertices = np.array([
                -1.0, -1.0,
                 1.0, -1.0,
                -1.0,  1.0,
                -1.0,  1.0,
                 1.0, -1.0,
                 1.0,  1.0,
            ], dtype='f4')

            self.vbo = self.ctx.buffer(vertices)
            self.vao = self.ctx.vertex_array(self.prog, [(self.vbo, '2f', 'in_vert')])
            print("ModernGL Initialized successfully.")
        except Exception as e:
            print(f"Error initializing ModernGL: {e}")

    def paintGL(self):
        if not self.ctx or not self.prog:
            return

        try:
            fbo_id = self.defaultFramebufferObject()
            fbo = self.ctx.detect_framebuffer(fbo_id)
            fbo.use()
            
            fbo.clear(0.01, 0.04, 0.12, 1.0) # Match shader base color

            # High-DPI physical pixels for u_resolution
            ratio = self.devicePixelRatioF()
            pw = self.width() * ratio
            ph = self.height() * ratio

            # Uniform updates
            if 'u_time' in self.prog:
                self.prog['u_time'].value = self.u_time
            if 'u_resolution' in self.prog:
                self.prog['u_resolution'].value = (pw, ph)
            if 'u_breath' in self.prog:
                self.prog['u_breath'].value = self.u_breath
            if 'u_coherence' in self.prog:
                self.prog['u_coherence'].value = self.u_coherence
            if 'u_sync' in self.prog:
                self.prog['u_sync'].value = self.u_sync

            self.vao.render(moderngl.TRIANGLES)
        except Exception as e:
            if int(self.u_time * 100) % 100 == 0:
                print(f"Error in paintGL: {e}")

    def resizeGL(self, w, h):
        if self.ctx:
            # High-DPI physical pixel fix
            ratio = self.devicePixelRatioF()
            pw = int(w * ratio)
            ph = int(h * ratio)
            self.ctx.viewport = (0, 0, pw, ph)

