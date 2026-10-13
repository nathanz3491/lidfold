"""Render the fold shader offscreen and check the duo projection's math."""
import math

import moderngl
import numpy as np
import pytest

from lidfold import shaders
from lidfold.fold import PERSPECTIVE

W, H = 256, 160


@pytest.fixture(scope="module")
def render():
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:                       # no GPU / driver in CI
        pytest.skip(f"no OpenGL context: {exc}")
    prog = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.FRAGMENT)
    vao = ctx.vertex_array(prog, [])
    rng = np.random.default_rng(0)
    src = rng.integers(0, 255, (H, W, 4), dtype=np.uint8)
    tex = ctx.texture((W, H), 4, src.tobytes())
    tex.swizzle = "BGRA"
    tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
    fbo = ctx.simple_framebuffer((W, H))
    fbo.use()
    for name, value in {"desktop": 0, "aspect": W / H, "blur": 0.0, "shadow": 0.0, "effect": 0,
                        "reference": math.radians(85), "perspective": PERSPECTIVE,
                        "progress": 0.0, "defocus": 0.0, "tilt": 0.0, "anchor": 0.0}.items():
        prog[name] = value

    def draw(effect, progress, anchor_deg=0.0):
        prog["effect"] = effect
        prog["progress"] = progress
        prog["anchor"] = math.radians(anchor_deg)
        tex.use(0)
        vao.render(moderngl.TRIANGLES, vertices=3)
        return np.frombuffer(fbo.read(components=3), np.uint8).reshape(H, W, 3)[::-1]

    expected = src[..., [2, 1, 0]]                 # BGRA texture -> RGB
    return draw, expected


def test_duo_and_ghost_are_exact_desktop_at_rest(render):
    draw, expected = render
    assert np.mean(draw(0, 0.0) == expected) > 0.99
    assert np.mean(draw(3, 0.0, 0.0) == expected) > 0.99


def test_duo_expands_never_shrinks(render):
    # Mac Duo's Duo magnifies around the hinge: the bottom-centre pixel still
    # shows bottom-centre content, and the image fills the frame interior.
    draw, expected = render
    out = draw(0, 0.5).astype(int)
    assert out[H // 2, W // 2].sum() > 0
    inner = out[H // 4: 3 * H // 4, W // 4: 3 * W // 4]
    assert (inner.sum(axis=2) > 0).mean() > 0.95


def test_ghost_crops_through_the_top_never_shrinks(render):
    draw, _ = render
    out = draw(3, 0.3, 40.0).astype(int)
    # The anchored desktop is magnified past the panel edges: no black
    # surround appears inside the panel (shrinking would leave one).
    assert (out[H // 8: 7 * H // 8, W // 8: 7 * W // 8].sum(axis=2) > 0).mean() > 0.95


NEW_EFFECTS = {"card": 4, "genie": 5, "fold": 6, "glass": 7, "cube": 8, "curl": 9, "blinds": 10,
               "doorway": 11, "reflect": 12, "stack": 13, "twirl": 14, "ripple": 15, "aura": 16,
               "island": 17, "prism": 18, "mosaic": 19}


def test_every_effect_has_a_shader_index():
    from lidfold.config import EFFECT_NAMES
    assert set(EFFECT_NAMES) == set(shaders.EFFECT_INDEX)
    assert all(shaders.EFFECT_INDEX[name] == index for name, index in NEW_EFFECTS.items())


@pytest.mark.parametrize("effect", NEW_EFFECTS.values(), ids=NEW_EFFECTS.keys())
def test_new_effects_are_exact_desktop_at_rest(render, effect):
    draw, expected = render
    assert np.mean(draw(effect, 0.0) == expected) > 0.99


@pytest.mark.parametrize("effect", NEW_EFFECTS.values(), ids=NEW_EFFECTS.keys())
def test_new_effects_move_the_desktop_without_blanking_it(render, effect):
    draw, expected = render
    out = draw(effect, 0.5).astype(int)
    assert np.mean(out == expected) < 0.6                  # visibly changed
    assert out.mean() > 0.3 * expected.mean()              # not a black (NaN) screen


def test_glass_leaves_the_desktop_above_its_surface(render):
    # Regression: exp() of the height above the surface overflowed, and the
    # mix with the desktop turned the whole upper screen black.
    draw, expected = render
    out = draw(NEW_EFFECTS["glass"], 0.3)
    top = slice(0, int(H * 0.4))                           # image rows are top-first
    assert np.mean(out[top] == expected[top]) > 0.99
