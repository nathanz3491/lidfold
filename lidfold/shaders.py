"""GLSL for the fold effects. Each effect is an analytic inverse map from an
output pixel back into the captured desktop, sampling the mip chain for blur.
Coordinates: uv in [0,1], v measured upward from the hinge (bottom edge)."""

# Value of the `effect` uniform for each effect, as dispatched in main().
EFFECT_INDEX = {"duo": 0, "iris": 1, "flat": 2, "ghost": 3,
                "card": 4, "genie": 5, "fold": 6, "glass": 7}

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
uniform int effect;        // 0 duo, 1 iris, 2 flat, 3 ghost, 4 card, 5 genie, 6 fold, 7 glass
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

void main() {
    vec4 c = effect == 1 ? iris(uv) : effect == 2 ? angled(uv, tilt)
           : effect == 3 ? ghost(uv) : effect == 4 ? card(uv)
           : effect == 5 ? genie(uv) : effect == 6 ? fold(uv)
           : effect == 7 ? glass(uv) : duo(uv);
    c.rgb *= 1.0 - smoothstep(0.86, 1.0, progress);   // dissolve at the end
    color = vec4(c.rgb, 1.0);
}
"""
