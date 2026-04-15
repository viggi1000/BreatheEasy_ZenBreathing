"""
GLSL shaders for the ZenBreathing visual engine.

Three themes:
  ocean  -- underwater looking UP at the wave surface; caustics; bioluminescent orbs
  aurora -- northern-lights breathing curtains
  orb    -- Journey-style central glowing sphere in void; orbiting particles

Visual feedback architecture:
  u_sync      → bioluminescence intensity, color warmth (reward)
  u_coherence → water clarity, orb count, surface position (progression)
  u_target    → wave/orb/aurora movement (guide)
  u_breath    → user phase (minimal use — avoid penalizing lag)
"""

VERTEX_SHADER = """
#version 330

in vec2 in_position;
out vec2 v_uv;

void main() {
    v_uv = in_position * 0.5 + 0.5;
    gl_Position = vec4(in_position, 0.0, 1.0);
}
"""

# ===================================================================
#  OCEAN -- Underwater looking UP at wave surface
#
#  Dynamic improvements:
#   - Water clarity increases with coherence (world opens up)
#   - Color temperature shifts cold blue → warm teal with sync
#   - Guide ring is BRIGHT during training, fades at high sync
#   - Orb brightness scales with coherence (emergent life)
#   - Surface position drops at high coherence (surfacing metaphor)
# ===================================================================

OCEAN_FRAGMENT_SHADER = """
#version 330

uniform vec2  u_resolution;
uniform float u_time;
uniform float u_breath;
uniform float u_coherence;
uniform float u_energy;
uniform float u_target_phase;
uniform float u_sync;
uniform sampler2D u_prev_frame;

in  vec2 v_uv;
out vec4 fragColor;

// ---- Gradient noise ----
vec2 hash2(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
}
float gnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(dot(hash2(i),           f),
                   dot(hash2(i+vec2(1,0)), f-vec2(1,0)), u.x),
               mix(dot(hash2(i+vec2(0,1)), f-vec2(0,1)),
                   dot(hash2(i+vec2(1,1)), f-vec2(1,1)), u.x), u.y);
}
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2 rot = mat2(0.8,0.6,-0.6,0.8);
    for (int i = 0; i < 5; i++) { v += a*gnoise(p); p = rot*p*2.1; a *= 0.5; }
    return v;
}

// ---- Caustics ----
float caustic(vec2 p, float t) {
    float c = sin(p.x*4.0 + t*0.5 + sin(p.y*2.5 + t*0.35)*1.8);
    c      += sin(p.y*3.5 - t*0.4 + sin(p.x*2.0 - t*0.28)*1.4);
    c      += sin((p.x+p.y)*2.8 + t*0.22) * 0.6;
    c      += sin((p.x-p.y)*1.9 - t*0.17 + sin(p.x*3.0)*0.8) * 0.4;
    return c * 0.25;
}

// ---- Bioluminescent orbs ----
float bioOrbs(vec2 uv, float t, float sync, float breath, float coh) {
    float glow = 0.0;
    // Coherence drives orb brightness and effective count
    float orb_intensity = 0.3 + coh * 2.0;  // 0.3 at 0% → 2.3 at 100%
    
    for (int k = 0; k < 8; k++) {
        float fk = float(k);
        float rx = fract(sin(fk * 127.1 + 1.3) * 43758.5) * 2.2 - 1.1;
        float ry = fract(sin(fk * 311.7 + 2.7) * 43758.5) * 2.0 - 1.0;
        float rs = fract(sin(fk *  93.7 + 4.1) * 28571.3);
        float rr = fract(sin(fk * 183.3 + 5.9) * 13579.2);
        float rb = fract(sin(fk * 247.1 + 7.3) * 43758.5);

        float ox = rx + sin(t * 0.08 + fk * 1.8) * 0.12;
        float oy = fract(ry * 0.5 + 0.5 + t * (0.008 + rs * 0.012)) * 2.0 - 1.0;

        float orb_r = 0.05 + rr * 0.09;
        float d = length(uv - vec2(ox, oy));
        float f = exp(-d*d / (orb_r * orb_r));
        float core  = exp(-d*d / (orb_r*orb_r*0.15));
        float alpha = rb * (0.35 + sync * 0.65) * (0.5 + breath * 0.5) * orb_intensity;
        glow += (f * 0.6 + core * 0.4) * alpha;
    }
    return glow;
}

void main() {
    vec2 uv = v_uv * 2.0 - 1.0;
    float aspect = u_resolution.x / u_resolution.y;
    uv.x *= aspect;

    float t      = u_time;
    float breath = u_breath;
    float coh    = u_coherence;
    float sync   = u_sync;
    float dist   = length(uv);

    // ============================================================
    //  1. WAVE SURFACE
    //  Surface drops as coherence rises (surfacing metaphor)
    // ============================================================
    float surface_base = 0.60 - coh * 0.15;  // 0.60 → 0.45 at max coherence
    float surface_y = surface_base + u_target_phase * 0.28;

    float wave1 = sin(uv.x * 2.8 + t * 0.28 + u_target_phase * 1.4) * 0.07;
    float wave2 = sin(uv.x * 4.8 - t * 0.18 + sin(t * 0.10) * 1.8) * 0.035;
    float wave3 = fbm(vec2(uv.x * 1.6 + t * 0.03, t * 0.10)) * 0.045;
    float wave4 = sin(uv.x * 7.0 + t * 0.55 + sin(uv.x * 2.5) * 1.2) * 0.018;
    float surface = surface_y + wave1 + wave2 + wave3 + wave4;

    float depth      = surface - uv.y;
    float aboveWater = smoothstep(-0.025, 0.025, depth);

    // ============================================================
    //  2. LIGHT + CAUSTICS
    //  Water clarity increases with coherence
    // ============================================================
    float clarity = 0.55 + coh * 0.45;  // 0.55 → 1.0

    float light = mix(exp(-max(0.0, -depth) * 1.0),
                      exp(-depth * (0.65 / clarity)),  // Less fog at high clarity
                      aboveWater);

    vec2 caustic_uv = uv * 1.8 + vec2(t * 0.06, u_target_phase * 0.35);
    float caust = caustic(caustic_uv, t * 0.50) * 0.5 + 0.5;
    caust = pow(caust, 2.2);
    float caustic_int = caust * light * (0.10 + sync * 0.32) * clarity;

    float ray1 = exp(-pow((uv.x - 0.35 * sin(t * 0.09)) * 2.8, 2.0)) * 0.28;
    float ray2 = exp(-pow((uv.x + 0.42 * cos(t * 0.07)) * 2.3, 2.0)) * 0.20;
    float ray3 = exp(-pow((uv.x - 0.1  + sin(t * 0.13) * 0.2) * 3.5, 2.0)) * 0.15;
    float rays  = (ray1 + ray2 + ray3) * light * aboveWater * (0.20 + u_target_phase * 0.5);

    // ============================================================
    //  3. BASE COLOR (depth gradient with color temp shift)
    //  Cold blue → warm teal as sync improves
    // ============================================================
    vec3 deep_water    = mix(vec3(0.0, 0.008, 0.028), vec3(0.0, 0.020, 0.032), sync);
    vec3 mid_water     = mix(vec3(0.0, 0.030, 0.090), vec3(0.0, 0.050, 0.095), sync);
    vec3 near_surface  = mix(vec3(0.01, 0.065, 0.160), vec3(0.02, 0.085, 0.150), sync);
    vec3 surface_light = mix(vec3(0.04, 0.130, 0.280), vec3(0.06, 0.160, 0.260), sync);

    float depthFrac = clamp(depth * (1.2 / clarity), 0.0, 1.0);  // Clarity affects depth vis
    vec3 color = mix(near_surface, deep_water, depthFrac);
    color = mix(color, surface_light, (1.0 - depthFrac) * light * 0.55);

    // Apply caustics + god-rays
    color += vec3(0.04, 0.16, 0.32) * caustic_int;
    color += vec3(0.07, 0.14, 0.22) * rays;

    // Brightness boost from clarity
    color *= (0.85 + clarity * 0.15);

    // ============================================================
    //  4. BIOLUMINESCENT ORBS (coherence-driven intensity)
    // ============================================================
    float orb = bioOrbs(uv, t, sync, breath, coh);
    vec3 orb_col = mix(vec3(0.0, 0.15, 0.28),
                       vec3(0.05, 0.65, 0.55),
                       sync);
    // Add golden highlights at very high sync
    orb_col = mix(orb_col, vec3(0.45, 0.55, 0.30), max(0.0, sync - 0.7) * 3.0);
    color += orb_col * orb * aboveWater;

    // Sparkle particles
    vec2 pid = floor(uv * 50.0 + vec2(0.0, t * 0.25));
    float pr  = fract(sin(dot(pid, vec2(127.1, 311.7))) * 43758.5);
    float pt  = fract(sin(dot(pid, vec2(269.5, 183.3))) * 43758.5);
    float ptw = sin(t * 2.0 + pt * 6.283) * 0.5 + 0.5;
    float sparkle = step(0.97 - sync * 0.04, pr) * ptw * sync * breath;
    color += vec3(0.1, 0.6, 0.55) * sparkle * aboveWater * 0.8;

    // ============================================================
    //  5. SURFACE HIGHLIGHT
    // ============================================================
    float surfDist  = abs(uv.y - surface);
    float surfLine  = exp(-surfDist * surfDist * 600.0);
    surfLine *= 0.18 + sync * 0.18 + breath * 0.12;
    color += vec3(0.08, 0.28, 0.45) * surfLine;

    // ============================================================
    //  6. PACING GUIDE RING
    //  BRIGHT during training (low sync), fades as sync improves
    // ============================================================
    float gR  = 0.18 + u_target_phase * 0.18;
    float gDist = abs(dist - gR);
    float ring = exp(-gDist * gDist * 350.0) * 0.25;  // Much brighter (was 0.09)
    ring *= max(0.08, 1.0 - sync * 0.92);  // Strong visibility at low sync
    color += vec3(0.06, 0.22, 0.45) * ring;

    // ============================================================
    //  7. FEEDBACK TRAILS
    // ============================================================
    vec2 fb = v_uv;
    fb.y += 0.0012 * (breath - 0.5);
    fb   += vec2(sin(t * 0.28), cos(t * 0.22)) * 0.0006;
    vec3 prev = texture(u_prev_frame, clamp(fb, 0.001, 0.999)).rgb;
    float fade = 0.86 + coh * 0.07 + sync * 0.03;
    color = max(color, prev * fade);

    // ============================================================
    //  8. FINISH
    // ============================================================
    float vig = 1.0 - dot(uv * (0.30 - coh * 0.08), uv * (0.30 - coh * 0.08));
    color *= smoothstep(0.0, 1.0, vig);

    float grain = (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898,78.233)))*43758.5)-0.5)*0.007;
    color += grain;

    color = pow(max(color, 0.0), vec3(0.95));
    fragColor = vec4(color, 1.0);
}
"""

# ===================================================================
#  AURORA BOREALIS
# ===================================================================

AURORA_FRAGMENT_SHADER = """
#version 330

uniform vec2  u_resolution;
uniform float u_time;
uniform float u_breath;
uniform float u_coherence;
uniform float u_energy;
uniform float u_target_phase;
uniform float u_sync;
uniform sampler2D u_prev_frame;

in  vec2 v_uv;
out vec4 fragColor;

vec2 hash2(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
}
float gnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f*f*(3.0-2.0*f);
    return mix(mix(dot(hash2(i),           f),
                   dot(hash2(i+vec2(1,0)), f-vec2(1,0)), u.x),
               mix(dot(hash2(i+vec2(0,1)), f-vec2(0,1)),
                   dot(hash2(i+vec2(1,1)), f-vec2(1,1)), u.x), u.y);
}
float fbm(vec2 p) {
    float v=0.0, a=0.5;
    mat2 rot=mat2(0.8,0.6,-0.6,0.8);
    for(int i=0;i<5;i++){v+=a*gnoise(p);p=rot*p*2.0;a*=0.5;}
    return v;
}

void main() {
    vec2 uv = v_uv * 2.0 - 1.0;
    float aspect = u_resolution.x / u_resolution.y;
    uv.x *= aspect;

    float t=u_time, breath=u_breath, coh=u_coherence, sync=u_sync;

    float ribbon1 = sin(uv.x*5.0 + sin(uv.x*2.0+t*0.35)*1.8 + t*0.2);
    float ribbon2 = sin(uv.x*3.5 + cos(uv.x*1.5+t*0.25)*1.2 + t*0.15+2.0);
    float ribbon3 = sin(uv.x*7.0 + sin(t*0.18)*2.0 + breath*3.0)*sync;
    float ribbon  = (ribbon1 + ribbon2*0.6 + ribbon3*0.3)*0.45;

    float y_shift = breath*0.6-0.1;
    float curtain = smoothstep(-0.3,0.8, uv.y+y_shift+ribbon*0.3);
    curtain      *= smoothstep(1.2,0.4,  uv.y+y_shift);
    float detail  = fbm(vec2(uv.x*3.0, uv.y*5.0+t*0.3+breath*0.5))*0.5+0.5;
    curtain      *= mix(detail, 1.0, sync*0.7);

    // Color: shifts warmer with sync (green → cyan → gold hints)
    vec3 green=vec3(0.05,0.85,0.15), cyan=vec3(0.0,0.65,0.80);
    vec3 violet=vec3(0.40,0.05,0.75), sky=vec3(0.0,0.0,0.04);
    vec3 white=vec3(0.8,0.9,1.0);
    vec3 warm_gold = vec3(0.85, 0.70, 0.30);

    float cshift = sin(t*0.08+uv.x*2.0)*0.5+0.5;
    vec3 aurora  = mix(mix(green,cyan,cshift), violet, coh*0.35+u_energy*0.15);
    // Warm gold tint at high sync
    aurora = mix(aurora, warm_gold, max(0.0, sync - 0.6) * 0.5);
    
    float intensity = curtain*(0.5+breath*0.5)*(0.4+sync*0.6);
    // Brightness boost from coherence
    intensity *= (0.85 + coh * 0.3);
    
    vec3 color = mix(sky, aurora, intensity);
    color = mix(color, white, pow(intensity,3.0)*sync*0.25);

    vec2 sid=floor(uv*80.0);
    float star=fract(sin(dot(sid,vec2(127.1,311.7)))*43758.5);
    star=step(0.995,star)*(1.0-intensity*0.8)*0.7;
    float twinkle=sin(t*2.0+star*100.0)*0.3+0.7;
    color+=vec3(0.8,0.85,1.0)*star*twinkle;

    vec2 fb=v_uv;
    fb.y+=0.002*(breath-0.5);
    fb.x+=0.0005*sin(t*0.3)*sync;
    vec3 prev=texture(u_prev_frame,clamp(fb,0.001,0.999)).rgb;
    color=max(color,prev*(0.84+coh*0.08+sync*0.03));

    // Guide band — brighter during training
    float guide_y=-0.3+u_target_phase*0.6;
    float band=exp(-(uv.y-guide_y)*(uv.y-guide_y)*200.0)*0.12;
    band*=max(0.08,1.0-sync*0.85);
    color+=cyan*band*(0.3+coh*0.7);

    float vig=1.0-dot(uv*0.3,uv*0.3);
    color*=smoothstep(0.0,1.0,vig);

    float grain=(fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5)-0.5)*0.008;
    color+=grain;
    color=pow(max(color,0.0),vec3(0.95));
    fragColor=vec4(color,1.0);
}
"""

# ===================================================================
#  ORB -- Journey-style central glowing sphere in dark void
# ===================================================================

ORB_FRAGMENT_SHADER = """
#version 330

uniform vec2  u_resolution;
uniform float u_time;
uniform float u_breath;
uniform float u_coherence;
uniform float u_energy;
uniform float u_target_phase;
uniform float u_sync;
uniform sampler2D u_prev_frame;

in  vec2 v_uv;
out vec4 fragColor;

float hash(float n) { return fract(sin(n)*43758.5453123); }

void main() {
    vec2 uv = v_uv * 2.0 - 1.0;
    float aspect = u_resolution.x / u_resolution.y;
    uv.x *= aspect;

    float t      = u_time;
    float breath = u_breath;
    float coh    = u_coherence;
    float sync   = u_sync;
    float dist   = length(uv);

    // ============================================================
    //  1. ORB CORE
    // ============================================================
    float orb_r    = 0.14 + u_target_phase * 0.10 + sync * 0.04;
    float orb_glow = exp(-dist*dist / (orb_r*orb_r));
    float core_r   = orb_r * 0.45;
    float core_glow= exp(-dist*dist / (core_r*core_r));
    float halo_r   = orb_r * (2.0 + coh * 2.5);
    float halo     = exp(-dist*dist / (halo_r*halo_r)) * 0.28 * coh;
    float ring_d   = abs(dist - orb_r * 0.85);
    float ring     = exp(-ring_d*ring_d * 60.0) * 0.35 * sync;

    // Color: cold blue → teal → warm gold as sync rises
    vec3 cold = vec3(0.05, 0.20, 0.80);
    vec3 teal = vec3(0.00, 0.70, 0.65);
    vec3 warm = vec3(0.95, 0.85, 0.45);
    vec3 orb_col = mix(mix(cold, teal, sync * 1.5), warm, max(0.0, sync - 0.5) * 2.0);

    // ============================================================
    //  2. BACKGROUND VOID
    // ============================================================
    vec3 color = vec3(0.0, 0.003, 0.010);
    color += orb_col * exp(-dist * 1.2) * 0.06 * (0.4 + sync * 0.6);
    color += orb_col * orb_glow * (0.7 + sync * 0.3);
    color += vec3(0.85, 0.92, 1.0) * core_glow * (0.5 + sync * 0.5);
    color += orb_col * halo;
    color += mix(vec3(0.5,0.8,1.0), warm, sync) * ring;

    // ============================================================
    //  3. ORBITING PARTICLES (coherence-modulated count/brightness)
    // ============================================================
    float particle_intensity = 0.5 + coh * 1.5;  // Brighter at high coherence
    for (int i = 0; i < 15; i++) {
        float fi = float(i);
        float seed_a = hash(fi * 1.37 + 0.5);
        float seed_r = hash(fi * 2.71 + 1.3);
        float seed_s = hash(fi * 3.14 + 2.7);
        float seed_b = hash(fi * 4.67 + 4.1);

        float orbit_r   = 0.22 + seed_r * 0.55;
        float base_speed= 0.08 + seed_s * 0.20;
        float speed     = base_speed * (1.0 + sync * 0.8);
        float angle     = t * speed + seed_a * 6.2832;
        angle  += sin(t * 0.25 + fi * 1.7) * 0.25;
        orbit_r+= sin(t * 0.18 + fi * 2.3) * 0.04;

        vec2 ppos  = vec2(cos(angle), sin(angle)) * orbit_r;
        float pdist= length(uv - ppos);
        float psize= 0.007 + seed_b * 0.014;
        float pglow= exp(-pdist*pdist / (psize*psize));

        float palpha = (0.3 + seed_b * 0.7) * (0.2 + sync * 0.8)
                     * (0.4 + breath * 0.6) * particle_intensity;
        color += orb_col * pglow * palpha * 1.2;
    }

    // ============================================================
    //  4. AMBIENT DUST
    // ============================================================
    for (int j = 0; j < 25; j++) {
        float fj = float(j);
        float hx = hash(fj * 1.13 + 0.7) * 3.0 - 1.5;
        float hy = hash(fj * 2.41 + 1.9) * 2.4 - 1.2;
        float hs = hash(fj * 3.77 + 3.3);

        vec2 dp = vec2(hx + sin(t * 0.04 + fj * 0.6) * 0.06,
                       fract((hy + 1.2) / 2.4 + t * (0.003 + hs*0.004)) * 2.4 - 1.2);
        float dd = length(uv - dp);
        float dg = exp(-dd*dd * 1200.0) * hs * 0.35;
        color += mix(vec3(0.3,0.5,0.9), warm, sync*0.4) * dg * 0.25;
    }

    // ============================================================
    //  5. PACING GUIDE RING — brighter during training
    // ============================================================
    float gR   = 0.28 + u_target_phase * 0.22;
    float gDist= abs(dist - gR);
    float gring= exp(-gDist*gDist * 350.0) * 0.18 * max(0.08, 1.0-sync*0.92);
    color += orb_col * gring;

    // Secondary coherence ring
    float cring = exp(-pow(dist - orb_r * 1.6, 2.0) * 80.0) * 0.06 * coh;
    color += orb_col * cring;

    // ============================================================
    //  6. FEEDBACK
    // ============================================================
    vec2 fb  = v_uv;
    vec2 dir = uv / max(dist, 0.01);
    fb += dir * (-0.0008) * (1.0 - dist);
    fb += vec2(sin(t*0.15), cos(t*0.12)) * 0.0003;
    vec3 prev = texture(u_prev_frame, clamp(fb, 0.001, 0.999)).rgb;
    color = max(color, prev * (0.80 + coh*0.10 + sync*0.05));

    // ============================================================
    //  7. FINISH
    // ============================================================
    float vig = 1.0 - dot(uv*0.28, uv*0.28);
    color *= smoothstep(0.0, 1.0, vig);

    float grain=(fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5)-0.5)*0.005;
    color += grain;
    color = pow(max(color, 0.0), vec3(0.90));
    fragColor = vec4(color, 1.0);
}
"""

# ===================================================================
#  BLIT -- simple texture-to-screen pass
# ===================================================================

BLIT_FRAGMENT_SHADER = """
#version 330

uniform sampler2D u_texture;
in  vec2 v_uv;
out vec4 fragColor;

void main() {
    fragColor = texture(u_texture, v_uv);
}
"""
