"""GLSL for the fold effects. Each effect is an analytic inverse map from an
output pixel back into the captured desktop, sampling the mip chain for blur.
Coordinates: uv in [0,1], v measured upward from the hinge (bottom edge)."""

# Value of the `effect` uniform for each effect, as dispatched in main().
EFFECT_INDEX = {"duo": 0, "iris": 1, "flat": 2, "ghost": 3,
                "card": 4, "genie": 5, "fold": 6, "glass": 7,
                "cube": 8, "curl": 9, "blinds": 10, "doorway": 11,
                "reflect": 12, "stack": 13, "twirl": 14, "ripple": 15,
                "aura": 16, "island": 17, "prism": 18, "mosaic": 19}

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
uniform int effect;        // see EFFECT_INDEX
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

// ------------------------------------------------ shared by the effects below
// Blurred sample that is never sharper than the mapping's own minification,
// so content squeezed by an effect does not shimmer. Call it from uniform
// control flow: it uses screen-space derivatives of st.
vec3 look(vec2 st, float sigma) {
    vec2 size = vec2(textureSize(desktop, 0));
    vec2 dx = dFdx(st) * size, dy = dFdy(st) * size;
    float minify = 0.5 * log2(max(1.0, max(dot(dx, dx), dot(dy, dy))));
    float px = sigma * size.y;
    float lod = max(minify, 0.5 * log2(1.0 + px * px * 2.4));
    vec2 o = vec2(sigma / aspect, sigma) * 0.6;
    vec2 t = vec2(st.x, 1.0 - st.y);
    vec3 c = textureLod(desktop, t, lod).rgb * 0.36;
    c += textureLod(desktop, t + vec2( o.x,  o.y), lod).rgb * 0.16;
    c += textureLod(desktop, t + vec2(-o.x,  o.y), lod).rgb * 0.16;
    c += textureLod(desktop, t + vec2( o.x, -o.y), lod).rgb * 0.16;
    c += textureLod(desktop, t + vec2(-o.x, -o.y), lod).rgb * 0.16;
    return c;
}

vec3 saturate3(vec3 c, float amount) {
    return mix(vec3(dot(c, vec3(0.2126, 0.7152, 0.0722))), c, amount);
}

// What lies behind a shrinking desktop: the desktop itself, receding, heavily
// blurred, more saturated and dimmed, like the wallpaper behind Mission
// Control. Exactly the desktop at p = 0.
vec3 backdrop(vec2 q, float p) {
    vec2 st = 0.5 + (q - 0.5) / (1.0 + 0.08 * p);
    float sigma = (0.35 + 0.65 * clamp(blur, 0.0, 1.0)) * 0.06 * smoothstep(0.0, 0.5, p);
    vec3 c = saturate3(soft(st, sigma), 1.0 + 0.4 * p);
    vec2 r = (q - 0.5) * vec2(aspect, 1.0);
    float vignette = 1.0 - 0.45 * p * smoothstep(0.2, 1.1, length(r));
    return c * (1.0 - 0.5 * p) * vignette;
}

// Distance to a rounded rectangle with continuous ("squircle") corners, as
// Apple draws them: a superellipse in the corner instead of a circular arc.
float squircle(vec2 p, vec2 halfSize, float radius) {
    vec2 d = abs(p) - halfSize + radius;
    vec2 c = max(d, 0.0);
    float corner = pow(pow(c.x, 4.0) + pow(c.y, 4.0), 0.25);
    return corner + min(max(d.x, d.y), 0.0) - radius;
}

// ----------------------------------------------------------------- Card
// iOS closing an app: the desktop becomes a card with continuous corners, a
// hairline rim and a soft shadow, settling over a blurred, dimmed copy of
// itself.
vec4 card(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    float scale = 1.0 - 0.36 * p;
    vec2 center = vec2(0.5, 0.5 + 0.035 * p);
    vec2 P = vec2((q.x - center.x) * aspect, q.y - center.y);
    vec2 halfSize = 0.5 * scale * vec2(aspect, 1.0);
    float radius = 0.065 * smoothstep(0.0, 0.45, p);
    float d = squircle(P, halfSize, radius);
    vec2 st = 0.5 + (q - center) / scale;

    float sigma = clamp(blur, 0.0, 1.0) * 0.004 * defocus;          // the card stays crisp
    vec3 front = look(st, sigma) * (1.0 - clamp(shadow, 0.0, 1.0) * 0.10 * p);
    float aa = max(fwidth(d), 1e-5);
    float rim = smoothstep(-2.5 * aa, -0.5 * aa, d) * (1.0 - smoothstep(-0.5 * aa, 0.5 * aa, d));
    front = mix(front, vec3(1.0), rim * 0.22 * smoothstep(0.0, 0.3, p));

    // A key shadow below the card plus a wide ambient one.
    float key = squircle(P + vec2(0.0, 0.03 * p), halfSize, radius);
    float sh = 0.45 * exp(-max(key, 0.0) / (0.012 + 0.03 * p))
             + 0.30 * exp(-max(d, 0.0) / (0.05 + 0.10 * p));
    vec3 back = backdrop(q, p) * (1.0 - clamp(shadow, 0.0, 1.0) * p * min(sh, 0.85));

    float inside = 1.0 - smoothstep(-aa, aa, d);
    inside = mix(1.0, inside, smoothstep(0.0, 0.02, p));
    return vec4(mix(back, front, inside), 1.0);
}

// ---------------------------------------------------------------- Genie
// macOS minimizing a window, into the hinge: the sides bend into an S-curved
// funnel whose mouth is at the bottom centre, then the desktop slides down
// through it and pours away.
vec4 genie(vec2 q) {
    float t = clamp(progress, 0.0, 1.0);
    float bend = smoothstep(0.0, 0.5, t);
    float slide = 1.04 * pow(clamp((t - 0.15) / 0.8, 0.0, 1.0), 1.6);
    const float mouth = 0.045;                           // half-width at the hinge
    float shape = smoothstep(-0.05, 0.95, q.y);
    float halfWidth = mix(0.5, mix(mouth, 0.5, shape), bend);
    vec2 st = vec2(0.5 + (q.x - 0.5) * 0.5 / halfWidth, q.y + slide);

    float sigma = clamp(blur, 0.0, 1.0) * 0.012 * defocus;
    vec3 col = look(st, sigma);
    // Squeezed rows turn away from the light, like a twisting ribbon.
    float squeeze = 1.0 - halfWidth / 0.5;
    col *= 1.0 - clamp(shadow, 0.0, 1.0) * 0.45 * squeeze * squeeze;

    vec2 fw = fwidth(st) + 1e-5;
    float m = smoothstep(-fw.x, fw.x, st.x) * (1.0 - smoothstep(1.0 - fw.x, 1.0 + fw.x, st.x))
            * (1.0 - smoothstep(1.0 - fw.y, 1.0 + fw.y, st.y));
    m = mix(1.0, m, smoothstep(0.0, 0.02, t));
    return vec4(mix(backdrop(q, t), col, m), 1.0);
}

// ----------------------------------------------------------------- Fold
// The upper half folds down over the lower half along the middle, like a page
// or a tiny laptop lid, in perspective. Past edge-on you see its frosted back.
vec4 fold(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    float th = PI * p;
    float sn = sin(th), cs = cos(th);
    const float D = 2.4;                        // viewer distance, screen heights
    float y = q.y - 0.5;                        // from the crease
    float den = D * cs + y * sn;
    float h = y * D / (abs(den) < 1e-5 ? 1e-5 : den);    // height on the folding half
    float k = D / max(D - h * sn, 1e-3);                 // perspective scale there
    vec2 st = vec2(0.5 + (q.x - 0.5) / k, 0.5 + h);

    float light = clamp(shadow, 0.0, 1.0);
    vec3 front = look(st, 0.010 * clamp(blur, 0.0, 1.0) * defocus);
    vec3 rear = look(st, (0.35 + 0.65 * clamp(blur, 0.0, 1.0)) * 0.045);
    front *= mix(1.0, 0.45 + 0.55 * max(cs, 0.0), light);
    // The back is a milky, frosted sheet; it brightens as it lies flat.
    rear = mix(saturate3(rear, 1.3), vec3(0.95), 0.22)
         * mix(1.0, 0.45 + 0.55 * max(-cs, 0.0), light);
    rear += 0.10 * (1.0 - h / 0.5) * max(-cs, 0.0);           // sheen toward the crease
    vec3 panel = cs > 0.0 ? front : rear;

    // Below the crease: the desktop, shadowed by the half folding over it.
    float reach = 0.05 + 0.35 * p;
    float ao = light * 0.6 * pow(sin(0.5 * th), 2.0) * exp(-max(0.0, -y) / reach);
    // Once past edge-on, the free edge hovers over the desktop and casts a
    // shadow, tight as it lands and soft while it is still high.
    float edge = 0.5 * cs * D / (D - 0.5 * sn);
    float drop = light * 0.9 * sqrt(max(-cs, 0.0)) * exp(-max(0.0, edge - y) / (0.015 + 0.08 * sn));
    vec3 below = sharp(q) * (1.0 - ao) * (1.0 - drop * step(y, edge));
    vec3 base = y < 0.0 ? below : backdrop(q, p) * 0.55;

    vec2 fw = vec2(fwidth(st.x), fwidth(h)) + 1e-5;
    float m = step(0.0, h) * (1.0 - smoothstep(0.5 - fw.y, 0.5 + fw.y, h))
            * smoothstep(-fw.x, fw.x, st.x) * (1.0 - smoothstep(1.0 - fw.x, 1.0 + fw.x, st.x))
            * step(1e-3, abs(den));
    // A hairline catches the light along the free edge, as on a sheet of glass.
    float lip = smoothstep(0.5 - 3.0 * fw.y, 0.5 - fw.y, h) * max(-cs, 0.0);
    panel = mix(panel, vec3(1.0), 0.35 * lip);
    vec3 c = mix(base, panel, m);
    c += vec3(0.18) * sn * exp(-abs(y) / 0.0025) * light;   // soft highlight on the crease
    return vec4(p < 1e-5 ? sharp(q) : c, 1.0);
}

// ---------------------------------------------------------------- Glass
// Liquid glass rising from the hinge: the desktop disappears under a frosted
// glass body whose surface bends what is behind it, splits colour at the
// rim, catches a specular line and ripples as the lid moves.
vec4 glass(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    float x = q.x * aspect;
    float calm = smoothstep(0.0, 0.12, p) * (1.0 - smoothstep(0.85, 1.0, p));
    float wave = calm * (0.011 * sin(x * 6.0 + p * 11.0) + 0.006 * sin(x * 13.0 - p * 17.0 + 1.3));
    float slope = calm * (0.066 * cos(x * 6.0 + p * 11.0) + 0.078 * cos(x * 13.0 - p * 17.0 + 1.3));
    float level = -0.08 + 1.2 * p;
    float d = q.y - level - wave;               // < 0: under the glass
    float dn = min(d, 0.0);                     // keeps exp() finite above it

    // Refraction: the rounded rim squeezes in what lies beyond the surface,
    // like the edge of a thick lens, with red, green and blue bent apart.
    const float rimWidth = 0.08;
    float r = clamp(-d / rimWidth, 0.0, 1.0);
    float bend = (1.0 - r) * (1.0 - r) * step(d, 0.0);
    vec2 dir = normalize(vec2(-slope * 0.6, 1.0));
    vec2 pull = dir * 0.05 * bend;
    float frost = (0.3 + 0.7 * clamp(blur, 0.0, 1.0)) * 0.032 * smoothstep(-0.02, -0.18, dn);
    vec3 under = vec3(look(q + pull * 0.92, frost).r,
                      look(q + pull, frost).g,
                      look(q + pull * 1.08, frost).b);
    under = saturate3(under, 1.35);
    under = mix(under, vec3(1.0), 0.09 + 0.08 * exp(dn / 0.05));       // milky, lighter near the top
    under *= 1.0 - clamp(shadow, 0.0, 1.0) * 0.18 * smoothstep(0.0, -0.9, dn);
    // A specular line on the surface, brightest where the ripple faces the light.
    float lit = 0.55 + 0.45 * clamp(-slope * 4.0 + 0.4, -1.0, 1.0);
    float line = (dn + 0.0035) / 0.0022;
    under += vec3(0.75) * lit * lit * exp(-line * line);
    under += vec3(0.12) * exp(dn / 0.012);

    float aa = fwidth(d) + 1e-5;
    return vec4(mix(under, sharp(q), smoothstep(-aa, aa, d)), 1.0);
}

// --------------------------------------------- more helpers for the effects below
vec2 rot2(vec2 v, float a) {
    float s = sin(a), c = cos(a);
    return vec2(c * v.x - s * v.y, s * v.x + c * v.y);
}

// Anti-aliased mask for st inside the unit square. Call it from uniform
// control flow: it uses screen-space derivatives.
float inside01(vec2 st) {
    vec2 fw = fwidth(st) + 1e-5;
    vec2 m = smoothstep(-fw, fw, st) * (1.0 - smoothstep(1.0 - fw, 1.0 + fw, st));
    return m.x * m.y;
}

// Distance to a rounded rectangle with circular corners.
float roundBox(vec2 p, vec2 halfSize, float radius) {
    vec2 d = abs(p) - halfSize + radius;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - radius;
}

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float pixel() { return 1.0 / float(textureSize(desktop, 0).y); }   // one pixel, in screen heights

// ----------------------------------------------------------------- Cube
// The macOS user-switching cube, turned on its side: the desktop is the
// front of a cube that rolls forward and down, backing away as it turns,
// until the top face, a frosted copy, faces you.
vec4 cube(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    float th = 0.5 * PI * p;
    float s = sin(th), c = cos(th);
    const float D = 2.6;                                     // viewer distance, screen heights
    vec3 eye = vec3(0.5 * aspect, 0.5, D);
    vec3 ray = vec3(q.x * aspect, q.y, 0.0) - eye;
    vec3 center = vec3(0.5 * aspect, 0.5, -0.5 - 0.8 * sin(2.0 * th));
    vec3 nFront = vec3(0.0, -s, c);                          // also the top face's depth axis
    vec3 nTop = vec3(0.0, c, s);                             // also the front face's up axis
    float fFront = dot(ray, nFront), fTop = dot(ray, nTop);
    vec3 hitF = eye + ray * (dot(center + 0.5 * nFront - eye, nFront) / min(fFront, -1e-5));
    vec3 hitT = eye + ray * (dot(center + 0.5 * nTop - eye, nTop) / min(fTop, -1e-5));
    vec2 uvF = vec2((hitF.x - center.x) / aspect + 0.5, dot(hitF - center, nTop) + 0.5);
    vec2 uvT = vec2((hitT.x - center.x) / aspect + 0.5, 0.5 - dot(hitT - center, nFront));
    // A convex cube: at most one face that faces you covers any pixel.
    float mF = inside01(uvF) * step(fFront, -1e-4);
    float mT = inside01(uvT) * step(fTop, -1e-4);

    float light = clamp(shadow, 0.0, 1.0);
    float kF = D / max(D - hitF.z, 1e-3);
    vec3 front = soft(uvF, clamp(blur, 0.0, 1.0) * 0.006 * defocus + 0.5 * max(0.0, 1.0 / kF - 1.0) * pixel());
    front *= mix(1.0, 0.4 + 0.6 * c, light);
    vec3 top = saturate3(soft(uvT, (0.35 + 0.65 * clamp(blur, 0.0, 1.0)) * 0.04), 1.2) * 0.7 + 0.04;
    top *= mix(1.0, 0.4 + 0.6 * s, light);
    vec3 col = mix(mix(backdrop(q, p) * 0.3, top, mT), front, mF);
    return vec4(p < 1e-5 ? sharp(q) : col, 1.0);
}

// ----------------------------------------------------------------- Curl
// A page turn, as in Apple Books: the desktop peels up from the bottom
// right corner around a cylinder, shows its pale back, and turns away.
vec4 curl(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    vec2 P = vec2(q.x * aspect, q.y);
    vec2 dir = normalize(vec2(1.0, -0.55));                  // toward the lifting corner
    const float R = 0.085;                                   // curl radius, screen heights
    float axis = mix(dot(vec2(aspect, 0.0), dir) + R, dot(vec2(0.0, 1.0), dir) - R, p);
    float d = dot(P, dir) - axis;                            // > 0: past the curl axis
    float x = clamp(d / R, -1.0, 1.0);
    // The page point under each layer, by its arc length a past the axis:
    // the flipped-over flat part, then the roll's upper and lower halves.
    vec2 unit = vec2(aspect, 1.0);
    vec2 stBack = (P + dir * (PI * R - 2.0 * d)) / unit;
    float phiUp = PI - asin(x), phiLow = asin(x);
    vec2 stUp = (P + dir * (R * phiUp - d)) / unit;
    vec2 stLow = (P + dir * (R * phiLow - d)) / unit;
    bool flatPart = d < 0.0, roll = d >= 0.0 && d <= R;
    bool inBack = all(greaterThanEqual(stBack, vec2(0.0))) && all(lessThanEqual(stBack, vec2(1.0)));
    bool inUp = all(greaterThanEqual(stUp, vec2(0.0))) && all(lessThanEqual(stUp, vec2(1.0)));
    bool inLow = all(greaterThanEqual(stLow, vec2(0.0))) && all(lessThanEqual(stLow, vec2(1.0)));
    int part = 4;                     // 0 back (flat), 1 back (roll), 2 front (roll), 3 front, 4 none
    if (flatPart && inBack) part = 0;
    else if (roll && inUp) part = 1;
    else if (roll && inLow) part = 2;
    else if (flatPart) part = 3;

    vec2 st = part == 0 ? stBack : part == 1 ? stUp : part == 2 ? stLow : q;
    vec3 tex = soft(st, part < 2 ? 0.02 : clamp(blur, 0.0, 1.0) * 0.004 * defocus);
    float light = clamp(shadow, 0.0, 1.0);
    vec3 paper = mix(saturate3(tex, 0.5), vec3(0.9), 0.72);   // pale, the desktop faintly through it
    vec3 col;
    if (part == 0) col = paper * (1.0 - 0.18 * light * exp(d / 0.05));
    else if (part == 1) col = paper * mix(1.0, 0.72 - 0.28 * cos(phiUp), light);
    else if (part == 2) col = tex * mix(1.0, 0.55 + 0.45 * cos(phiLow), light)
                            + vec3(0.16) * pow(max(0.0, sin(2.0 * phiLow)), 8.0) * light;
    else if (part == 3) col = tex * (1.0 - light * 0.45 * exp(d / 0.04) * smoothstep(0.0, 0.05, p));
    else col = backdrop(q, p) * 0.5 * (1.0 - light * 0.5 * exp(-(d - R) / 0.06));
    return vec4(col, 1.0);
}

// --------------------------------------------------------------- Blinds
// Venetian blinds closing: the desktop is cut into slats that turn
// edge-on, those by the hinge first, each lit across its face.
vec4 blinds(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    const float N = 9.0;
    float h = 1.0 / N;
    float i = min(floor(q.y * N), N - 1.0);
    float c = (i + 0.5) * h;
    float lp = clamp(p * 1.4 - 0.4 * i / (N - 1.0), 0.0, 1.0);
    float th = 0.49 * PI * lp * lp * (3.0 - 2.0 * lp);
    float ct = cos(th), sn = sin(th);
    float t = (q.y - c) / ct;                                // position across the slat
    vec2 st = vec2(q.x, c + t);
    float aa = pixel() / ct;
    float on = 1.0 - smoothstep(0.5 * h - aa, 0.5 * h + aa, abs(t));
    on = mix(1.0, on, smoothstep(0.0, 0.05, lp));
    float light = clamp(shadow, 0.0, 1.0);
    vec3 col = soft(st, clamp(blur, 0.0, 1.0) * 0.005 * defocus);
    col *= mix(1.0, 0.45 + 0.55 * ct, light) * (1.0 + 0.25 * sn * (t / (0.5 * h)) * light);
    // The gap, shaded just beyond each slat's edge.
    float beyond = abs(q.y - c) - 0.5 * h * ct;
    vec3 gap = backdrop(q, p) * 0.4 * (1.0 - 0.6 * light * exp(-max(beyond, 0.0) / 0.012));
    return vec4(mix(gap, col, on), 1.0);
}

// -------------------------------------------------------------- Doorway
// Keynote's Doorway: the desktop splits down the middle and both halves
// swing toward you on hinges at the sides, opening onto the dark.
vec4 doorway(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    float th = radians(86.0) * p;
    float sn = sin(th), cs = cos(th);
    const float D = 2.2;
    float cx = 0.5 * aspect;
    float X = q.x * aspect;
    bool left = X < cx;
    float xs = left ? X : aspect - X;                        // screen distance from this door's hinge
    float den = D * cs + (xs - cx) * sn;
    float u = xs * D / max(den, 1e-4);                       // door point, from its hinge
    float k = D / max(D - u * sn, 1e-3);                     // perspective scale there
    float ys = 0.5 + (q.y - 0.5) / k;
    vec2 st = vec2((left ? u : aspect - u) / aspect, ys);
    float light = clamp(shadow, 0.0, 1.0);
    vec3 door = soft(st, clamp(blur, 0.0, 1.0) * 0.006 * defocus);
    door *= mix(1.0, 0.4 + 0.6 * cs, light) * (1.0 - 0.25 * light * sn * u / cx);
    float fu = fwidth(u) + 1e-5, fy = fwidth(ys) + 1e-5;
    float m = (1.0 - smoothstep(cx - fu, cx + fu, u)) * smoothstep(-fy, fy, ys)
            * (1.0 - smoothstep(1.0 - fy, 1.0 + fy, ys)) * step(1e-4, den);
    m = mix(1.0, m, smoothstep(0.0, 0.02, p));
    // Behind: the dark, with a little light coming through the opening.
    float glow = 0.12 * p * exp(-pow((X - cx) / (0.05 + 0.5 * p), 2.0));
    return vec4(mix(backdrop(q, p) * 0.35 + vec3(glow), door, m), 1.0);
}

// -------------------------------------------------------------- Reflect
// An Apple product shot: the desktop becomes a card standing on a black,
// glossy floor, its reflection fading below it.
vec4 reflection(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    float scale = 1.0 - 0.44 * p;
    float floorY = 0.30 * p;
    vec2 P = vec2((q.x - 0.5) * aspect, q.y);
    vec2 halfSize = 0.5 * scale * vec2(aspect, 1.0);
    vec2 center = vec2(0.0, floorY + halfSize.y);
    float radius = 0.022 * smoothstep(0.0, 0.4, p);
    float d = roundBox(P - center, halfSize, radius);
    vec2 st = vec2(0.5 + P.x / (aspect * scale), (q.y - floorY) / scale);
    vec3 front = look(st, clamp(blur, 0.0, 1.0) * 0.004 * defocus);
    // The mirror image: the card flipped about the floor line.
    float below = floorY - q.y;
    float dm = roundBox(vec2(P.x, floorY + below) - center, halfSize, radius);
    vec2 stm = vec2(st.x, below / scale);
    vec3 mirror = soft(stm, 0.004 + 0.03 * clamp(below / (2.0 * halfSize.y), 0.0, 1.0));
    float fade = 0.36 * exp(-max(below, 0.0) / (0.12 * scale)) * step(0.0, below);
    // The studio: near black, a soft pool of light behind the card, a darker floor.
    vec2 r = (q - vec2(0.5, center.y)) * vec2(aspect * 0.6, 1.0);
    vec3 studio = vec3(0.13, 0.13, 0.14) * exp(-dot(r, r) * 2.2);
    studio *= mix(0.5, 1.0, smoothstep(floorY - 0.002, floorY + 0.002, q.y));
    vec3 bg = mix(backdrop(q, p), studio, smoothstep(0.0, 0.5, p));
    float aa = max(fwidth(d), 1e-5);
    float card = mix(1.0, 1.0 - smoothstep(-aa, aa, d), smoothstep(0.0, 0.02, p));
    float mirrorMask = 1.0 - smoothstep(-aa, aa, dm);
    vec3 c = mix(bg, mirror, fade * mirrorMask);
    return vec4(mix(c, front, card), 1.0);
}

// ---------------------------------------------------------------- Stack
// Time Machine: the desktop backs away into a stack of earlier copies,
// each further back and higher, so it peeks over the one in front, against
// a field of stars.
vec4 stack(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    const float D = 2.0;
    vec2 P = vec2((q.x - 0.5) * aspect, q.y - 0.5);
    vec2 st = vec2(0.0);
    float copy = -1.0, edge = 0.0, sc = 1.0;
    for (int k = 0; k < 7; k++) {                            // front to back; the first hit wins
        float s = D / (D + 2.4 * p + 0.8 * float(k));
        vec2 local = (P - vec2(0.0, 0.3 * float(k) * s)) / s;   // climbing 0.3 per copy
        vec2 uv = vec2(local.x / aspect + 0.5, local.y + 0.5);
        vec2 e2 = min(uv, 1.0 - uv) * vec2(aspect, 1.0) * s; // to this copy's edge, screen heights
        float e = min(e2.x, e2.y);
        if (copy < 0.0 && e >= 0.0) { st = uv; copy = float(k); edge = e; sc = s; }
    }
    float light = clamp(shadow, 0.0, 1.0);
    float sigma = clamp(blur, 0.0, 1.0) * (0.004 * defocus + 0.006 * max(copy, 0.0))
                + 0.5 * (1.0 / sc - 1.0) * pixel();
    vec3 col = soft(st, sigma);
    col *= copy < 0.5 ? 1.0 - 0.2 * p * light : 0.55 * pow(0.8, copy);
    col = mix(col, col * vec3(0.85, 0.93, 1.12), min(copy, 1.0) * 0.6);   // older copies cooler
    float rim = (1.0 - smoothstep(0.0, 2.0 * pixel(), edge)) * smoothstep(0.0, 0.1, p);
    col = mix(col, vec3(0.75), 0.35 * rim);
    // Space: deep blue-black, a faint nebula of the desktop, scattered stars.
    vec2 g = q * vec2(aspect, 1.0) * 110.0;
    vec2 cell = floor(g);
    vec2 offset = fract(g) - 0.5 - (vec2(hash12(cell + 3.1), hash12(cell + 5.7)) - 0.5) * 0.6;
    float star = step(0.965, hash12(cell)) * smoothstep(0.09, 0.0, length(offset)) * (0.4 + 0.6 * hash12(cell + 9.0));
    vec3 space = vec3(0.01, 0.015, 0.035) + backdrop(q, p) * 0.12 + vec3(star) * 0.9 * smoothstep(0.1, 0.5, p);
    float m = copy >= 0.0 ? smoothstep(0.0, 1.5 * pixel(), edge) : 0.0;
    m = mix(1.0, m, smoothstep(0.0, 0.02, p));
    return vec4(mix(space, col, m), 1.0);
}

// ---------------------------------------------------------------- Twirl
// Keynote's Twirl: the desktop spins into a vortex at the centre and
// shrinks away into it.
vec4 twirl(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    vec2 P = vec2((q.x - 0.5) * aspect, q.y - 0.5);
    float r = length(P);
    float scale = 1.0 - 0.6 * p * p;
    float angle = 4.5 * p * pow(max(0.0, 1.0 - r / 1.2), 2.0);
    vec2 R = rot2(P, -angle) / scale;
    vec2 st = vec2(R.x / aspect + 0.5, R.y + 0.5);
    vec3 col = look(st, clamp(blur, 0.0, 1.0) * 0.012 * defocus);
    col *= 1.0 - clamp(shadow, 0.0, 1.0) * 0.3 * p * exp(-r / 0.25);   // a darker eye
    float m = mix(1.0, inside01(st), smoothstep(0.0, 0.02, p));
    return vec4(mix(backdrop(q, p) * 0.45, col, m), 1.0);
}

// --------------------------------------------------------------- Ripple
// Keynote's Droplet: a drop lands in the middle and a ring of ripples runs
// outward, leaving the desktop behind it sunk under dark, frosted water.
vec4 ripple(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    vec2 P = vec2((q.x - 0.5) * aspect, q.y - 0.5);
    float r = length(P) + 1e-5;
    vec2 n = P / r;
    float ring = 1.3 * p;                                    // radius of the ring of waves
    float env = exp(-pow((r - ring) / 0.14, 2.0)) * smoothstep(0.0, 0.08, p);
    float phase = 55.0 * (r - ring);
    float height = sin(phase) * env;
    float slant = cos(phase) * env;
    vec2 st = q - vec2(n.x / aspect, n.y) * 0.016 * height;
    float pool = smoothstep(ring - 0.02, ring - 0.3, r) * smoothstep(0.0, 0.1, p);
    float sigma = (0.3 + 0.7 * clamp(blur, 0.0, 1.0)) * 0.02 * pool + clamp(blur, 0.0, 1.0) * 0.003 * defocus;
    vec3 col = look(st, sigma);
    float light = clamp(shadow, 0.0, 1.0);
    col *= 1.0 - light * 0.5 * pool;
    float facing = dot(n, normalize(vec2(-0.5, 0.8)));       // light from the upper left
    col += vec3(0.20) * max(0.0, slant * facing) * (0.5 + 0.5 * light);
    col -= vec3(0.10) * max(0.0, -slant * facing) * light;
    return vec4(max(col, 0.0), 1.0);
}

// ----------------------------------------------------------------- Aura
// The glow Apple Intelligence puts around the screen: soft bands of
// lavender, pink, blue, coral and peach flow around the edge and spread
// inward as the desktop dims.
vec3 auraColor(float h) {
    h = fract(h) * 5.0;
    float f = fract(h);
    int i = int(floor(h));
    vec3 c0 = vec3(0.74, 0.51, 0.95), c1 = vec3(0.96, 0.73, 0.92), c2 = vec3(0.55, 0.62, 1.0),
         c3 = vec3(1.0, 0.40, 0.47), c4 = vec3(1.0, 0.73, 0.44);
    vec3 a = i == 0 ? c0 : i == 1 ? c1 : i == 2 ? c2 : i == 3 ? c3 : c4;
    vec3 b = i == 0 ? c1 : i == 1 ? c2 : i == 2 ? c3 : i == 3 ? c4 : c0;
    return mix(a, b, smoothstep(0.0, 1.0, f));
}

vec4 aura(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    vec2 P = vec2(q.x * aspect, q.y);
    float e = min(min(P.x, aspect - P.x), min(P.y, 1.0 - P.y));   // to the nearest edge
    vec2 C = P - vec2(0.5 * aspect, 0.5);
    float around = atan(C.y, C.x) / (2.0 * PI) + 0.5;
    float wobble = 0.5 + 0.5 * sin(around * 2.0 * PI * 3.0 + p * 9.0);
    float width = (0.012 + 0.17 * p) * (0.75 + 0.5 * wobble);
    vec3 tint = auraColor(around + 0.6 * p);
    float on = smoothstep(0.0, 0.08, p);
    vec3 col = soft(q, clamp(blur, 0.0, 1.0) * 0.012 * p) * (1.0 - 0.45 * p);
    col = 1.0 - (1.0 - col) * (1.0 - tint * exp(-e / width) * 0.85 * on);   // screen blend
    col += tint * 0.6 * exp(-e / 0.004) * on;                                 // a bright inner rim
    return vec4(min(col, 1.0), 1.0);
}

// --------------------------------------------------------------- Island
// The Dynamic Island: the desktop squeezes up into a black pill at the top
// centre, over a blurred, darkening desktop.
vec4 island(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    vec2 P = vec2((q.x - 0.5) * aspect, q.y);
    float m = smoothstep(0.0, 0.8, p);                       // done before the final fade
    vec2 pillHalf = vec2(0.19, 0.036);
    vec2 halfSize = mix(vec2(0.5 * aspect, 0.5), pillHalf, m);
    vec2 center = mix(vec2(0.0, 0.5), vec2(0.0, 0.94), m);
    float radius = mix(0.0, pillHalf.y, smoothstep(0.0, 0.6, m));
    radius = min(radius, min(halfSize.x, halfSize.y));
    float d = roundBox(P - center, halfSize, radius);
    vec2 st = (P - center) / (2.0 * halfSize) + 0.5;         // the content squeezes in
    vec3 content = look(st, clamp(blur, 0.0, 1.0) * 0.01 * defocus);
    content = mix(content, vec3(0.0), smoothstep(0.25, 0.85, m));   // the island is black
    float aa = max(fwidth(d), 1e-5);
    float inside = mix(1.0, 1.0 - smoothstep(-aa, aa, d), smoothstep(0.0, 0.02, p));
    // A faint rim, so the black pill reads against a dark desktop.
    float rim = smoothstep(-2.5 * aa, -0.5 * aa, d) * (1.0 - smoothstep(-0.5 * aa, 0.5 * aa, d));
    vec3 c = mix(backdrop(q, p) * (1.0 - 0.55 * p), content, inside);
    c += vec3(0.22) * rim * smoothstep(0.4, 0.9, m);
    return vec4(c, 1.0);
}

// ---------------------------------------------------------------- Prism
// Keynote's Color Planes: red, green and blue pull apart in three
// directions and fade on black.
vec4 prism(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    vec2 C = vec2((q.x - 0.5) * aspect, q.y - 0.5);
    float spread = 0.11 * p;
    float sigma = clamp(blur, 0.0, 1.0) * 0.01 * defocus;
    vec2 unit = vec2(aspect, 1.0);
    vec2 stR = (C / (1.0 + 0.10 * p) - vec2(0.0, 1.0) * spread) / unit + 0.5;
    vec2 stG = (C / (1.0 + 0.16 * p) - vec2(-0.866, -0.5) * spread) / unit + 0.5;
    vec2 stB = (C / (1.0 + 0.22 * p) - vec2(0.866, -0.5) * spread) / unit + 0.5;
    float on = smoothstep(0.0, 0.02, p);
    vec3 col = vec3(look(stR, sigma).r * mix(1.0, inside01(stR), on),
                    look(stG, sigma).g * mix(1.0, inside01(stG), on),
                    look(stB, sigma).b * mix(1.0, inside01(stB), on));
    return vec4(col * (1.0 - 0.35 * p), 1.0);
}

// --------------------------------------------------------------- Mosaic
// The desktop breaks into rounded tiles that spin and shrink away in a
// wave rising from the hinge.
vec4 mosaic(vec2 q) {
    float p = clamp(progress, 0.0, 1.0);
    const float rows = 9.0;
    float cell = 1.0 / rows;
    vec2 P = vec2((q.x - 0.5) * aspect, q.y);
    vec2 id = floor(P / cell);
    vec2 cc = (id + 0.5) * cell;                             // tile centre
    float dist = length(cc * vec2(0.6, 1.0));                // from the hinge's centre
    float lp = clamp(p * 1.7 - dist * 0.55, 0.0, 1.0);
    float e = lp * lp * (3.0 - 2.0 * lp);
    float scale = max(1.0 - e, 1e-3);
    float spin = 0.6 * e * (mod(id.x + id.y, 2.0) * 2.0 - 1.0);
    vec2 L = rot2(P - cc, -spin) / scale;
    float d = roundBox(L, vec2(0.5 * cell), 0.22 * cell * smoothstep(0.0, 0.3, e));
    vec2 st = vec2((cc.x + L.x) / aspect + 0.5, cc.y + L.y);
    vec3 col = soft(st, clamp(blur, 0.0, 1.0) * 0.004 * defocus) * (1.0 - 0.5 * e * clamp(shadow, 0.0, 1.0));
    float aa = 1.2 * pixel() / scale;
    float inside = (1.0 - smoothstep(-aa, aa, d)) * step(1e-3, 1.0 - e);
    inside = mix(1.0, inside, smoothstep(0.0, 0.05, e));
    return vec4(mix(backdrop(q, p) * 0.45, col, inside), 1.0);
}

vec4 render(vec2 q) {
    switch (effect) {
        case 1: return iris(q);
        case 2: return angled(q, tilt);
        case 3: return ghost(q);
        case 4: return card(q);
        case 5: return genie(q);
        case 6: return fold(q);
        case 7: return glass(q);
        case 8: return cube(q);
        case 9: return curl(q);
        case 10: return blinds(q);
        case 11: return doorway(q);
        case 12: return reflection(q);
        case 13: return stack(q);
        case 14: return twirl(q);
        case 15: return ripple(q);
        case 16: return aura(q);
        case 17: return island(q);
        case 18: return prism(q);
        case 19: return mosaic(q);
        default: return duo(q);
    }
}

void main() {
    vec4 c = render(uv);
    c.rgb *= 1.0 - smoothstep(0.86, 1.0, progress);   // dissolve at the end
    color = vec4(c.rgb, 1.0);
}
"""
