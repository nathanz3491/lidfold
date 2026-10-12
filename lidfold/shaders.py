"""GLSL for the fold effects. Each effect is an analytic inverse map from an
output pixel back into the captured desktop, sampling the mip chain for blur.
Coordinates: uv in [0,1], v measured upward from the hinge (bottom edge)."""

VERTEX = """
#version 330
out vec2 uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

FRAGMENT = """
#version 330
uniform sampler2D desktop;
uniform float progress;    // Mac Duo progress 0..1 (eased)
uniform float defocus;     // 0..1, physical defocus curve
uniform float tilt;        // radians, flat effect
uniform float anchor;      // radians, ghost: lid rotation away from the held plane
uniform float reference;   // radians, ghost: plane the desktop is held on
uniform float perspective; // 0..1, Mac Duo default 0.7
uniform float blur;        // user strength 0..1
uniform float shadow;      // user strength 0..1
uniform float aspect;      // width / height
uniform int effect;        // 0 duo, 1 iris, 2 flat, 3 ghost
in vec2 uv;
out vec4 color;

const float PI = 3.14159265;

// Desktop texture rows are stored top-first, so flip v when sampling.
vec3 sharp(vec2 st) { return texture(desktop, vec2(st.x, 1.0 - st.y)).rgb; }

// sigma as a fraction of screen height -> mip level, smoothed with 5 taps so
// the box-filtered mips do not show blocks.
vec3 soft(vec2 st, float sigma) {
    if (sigma <= 0.0) return sharp(st);
    float px = sigma * float(textureSize(desktop, 0).y);
    float lod = 0.5 * log2(1.0 + px * px * 2.4);
    vec2 o = vec2(sigma / aspect, sigma) * 0.6;
    vec2 t = vec2(st.x, 1.0 - st.y);
    vec3 c = textureLod(desktop, t, lod).rgb * 0.36;
    c += textureLod(desktop, t + vec2( o.x,  o.y), lod).rgb * 0.16;
    c += textureLod(desktop, t + vec2(-o.x,  o.y), lod).rgb * 0.16;
    c += textureLod(desktop, t + vec2( o.x, -o.y), lod).rgb * 0.16;
    c += textureLod(desktop, t + vec2(-o.x, -o.y), lod).rgb * 0.16;
    return c;
}

// ------------------------------------------------------------------ Duo
// Port of Mac Duo's default "Duo" (github.com/DhananjayBhosale/MacDuo,
// MIT). Its own note: "The physical lid already supplies the camera's
// trapezoid." No 3D projection: the image only *expands* around the
// bottom-centre hinge, more toward the top, so icons swell and upper content
// leaves through the top. It never shrinks. Dark feathered edges, blur
// growing with height, a little shading, then the global dissolve.
vec4 duo(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    if (p < 0.00001) return vec4(sharp(q), 1.0);
    float height = q.y;
    float expansion = 1.0 + p * (0.12 + mix(0.30, 0.66, clamp(perspective, 0.0, 1.0)) * height);
    vec2 st = vec2(0.5 + (q.x - 0.5) / expansion, height / expansion);

    // Sigma as a fraction of image height; the hinge stays more focused.
    float spread = 0.12 + 0.88 * pow(height, 1.15);
    float sigma = clamp(blur, 0.0, 1.0) * 0.052 * defocus * spread;
    vec3 col = soft(st, sigma);

    float softness = clamp(blur, 0.0, 1.0);
    float topWidth = p * (0.075 + 0.15 * softness) + 1.5 * sigma;
    float sideWidth = (p * (0.055 + 0.12 * softness) + 1.5 * sigma) / aspect;
    float bottomWidth = p * (0.012 + 0.025 * softness) + 0.5 * sigma;
    float mask = smoothstep(0.0, topWidth, 1.0 - height) * smoothstep(0.0, bottomWidth, height)
               * smoothstep(0.0, sideWidth, q.x) * smoothstep(0.0, sideWidth, 1.0 - q.x);
    float shade = 1.0 - clamp(shadow, 0.0, 1.0) * 0.12 * p * p * height;
    return vec4(col * mask * shade, 1.0);
}

// ---------------------------------------------------------------- Ghost
// Port of Mac Duo's "Ghost": a stationary viewer's ray through the tilting
// panel is intersected with the plane the desktop is held on, so the desktop
// appears anchored there. The viewer is fixed relative to the keyboard, at
// 1.6 screen heights up and mix(2.6, 1.6, perspective) out. Like Mac Duo, it
// magnifies and crops through the top; it never shrinks.
vec4 ghost(vec2 q) {
    float height = q.y;
    float viewerDistance = mix(2.6, 1.6, clamp(perspective, 0.0, 1.0));
    float ref = clamp(reference, radians(90.0), radians(140.0));
    float eyeY = 1.6 * sin(ref) + viewerDistance * cos(ref);
    float eyeZ = viewerDistance * sin(ref) - 1.6 * cos(ref);
    float angle = clamp(anchor, 0.0, radians(85.0));
    float depth = height * sin(angle);
    float rayScale = eyeZ / (eyeZ - depth);
    vec2 st = vec2(0.5 + (q.x - 0.5) * rayScale, eyeY + (height * cos(angle) - eyeY) * rayScale);
    // Ease the first ~1.7 degrees without a pixel jump from exact passthrough.
    float onset = clamp(angle / 0.03, 0.0, 1.0);
    onset = onset * onset * onset * (onset * (onset * 6.0 - 15.0) + 10.0);
    st = mix(q, st, onset);

    float focus = pow(clamp(defocus, 0.0, 1.0), 1.25);
    float sigma = clamp(blur, 0.0, 1.0) * 0.045 * focus * pow(height, 1.65);
    sigma = max(sigma, 0.5 * max(0.0, rayScale - 1.0) / float(textureSize(desktop, 0).y));
    vec3 col = soft(st, sigma);

    vec2 feather = max(vec2(3.0 * sigma / aspect, 3.0 * sigma), fwidth(st)) + 1e-5;
    vec2 border = smoothstep(-feather, feather, st) * (1.0 - smoothstep(1.0 - feather, 1.0 + feather, st));
    float edge = mix(1.0, border.x * border.y, smoothstep(0.0, 0.025, angle));
    float shade = 1.0 - clamp(shadow, 0.0, 1.0) * 0.10 * focus * height;
    return vec4(col * shade * edge, 1.0);
}

// ----------------------------------------------------------------- Flat
// The original lidfold look: the desktop tips back by `ang` (growing toward
// 80 deg) as seen from straight in front of the panel, so it lies down
// toward the keyboard.
vec4 angled(vec2 q, float ang) {
    float D = 1.7;                       // viewer distance, in screen heights
    float s = sin(ang), c = cos(ang);
    float den = D * c - (q.y - 0.5) * s;
    if (den <= 1e-4) return vec4(0.0);
    float v = q.y * D / den;             // height on the sheet
    float k = D / (D + v * s);           // perspective scale at that height
    float u = 0.5 + (q.x - 0.5) / k;
    vec2 st = vec2(u, v);

    float sigma = clamp(blur, 0.0, 1.0) * 0.07 * pow(defocus, 1.1) * (0.35 + 0.65 * pow(v, 1.3));
    // Tilt minifies fine detail; a touch of prefilter avoids shimmer.
    sigma = max(sigma, 0.4 * max(0.0, 1.0 / k - 1.0) / float(textureSize(desktop, 0).y));
    vec3 col = soft(st, sigma);

    float fx = max(fwidth(st.x), 2.5 * sigma / aspect) + 1e-4;
    float fy = max(fwidth(st.y), 2.5 * sigma) + 1e-4;
    float edge = smoothstep(-fx, fx, st.x) * (1.0 - smoothstep(1.0 - fx, 1.0 + fx, st.x))
               * (1.0 - smoothstep(1.0 - fy, 1.0 + fy, st.y)) * step(0.0, st.y);
    edge = mix(1.0, edge, smoothstep(0.0, 0.02, ang));
    float shade = 1.0 - clamp(shadow, 0.0, 1.0) * 0.35 * progress * v;
    return vec4(col * shade * edge, 1.0);
}

// ----------------------------------------------------------------- Iris
// Eight straight blades close on a point just above the hinge.
vec4 iris(vec2 q) {
    vec2 p = vec2((q.x - 0.5) * aspect, q.y - 0.14);
    float aperture = 1.25 * pow(1.0 - progress, 1.3);
    float twist = progress * 0.35;
    float pitch = 2.0 * PI / 8.0;
    float outside = -1e9; int owner = 0;
    for (int i = 0; i < 8; i++) {
        float phi = float(i) * pitch + twist;
        float e = p.x * cos(phi) + p.y * sin(phi) - aperture;
        if (e > outside) { outside = e; owner = i; }
    }
    float inside = max(0.0, -outside);
    float sigma = clamp(blur, 0.0, 1.0) * 0.03 * defocus * exp(-inside / 0.08);
    vec3 col = soft(q, sigma);
    col *= 1.0 - clamp(shadow, 0.0, 1.0) * 0.6 * exp(-inside / 0.04) * progress;
    float blade = 0.10 + 0.05 * float(owner % 3) + 0.08 * clamp(outside / 0.4, 0.0, 1.0);
    float aa = fwidth(outside) + 1e-4;
    col = mix(col, vec3(blade), smoothstep(-aa, aa, outside));
    return vec4(col, 1.0);
}

void main() {
    vec4 c = effect == 1 ? iris(uv) : effect == 2 ? angled(uv, tilt)
           : effect == 3 ? ghost(uv) : duo(uv);
    c.rgb *= 1.0 - smoothstep(0.86, 1.0, progress);   // dissolve at the end
    color = vec4(c.rgb, 1.0);
}
"""
