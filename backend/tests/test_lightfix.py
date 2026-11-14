"""The light pass after painting (vgac/lightfix.py, docs/AFTER-TEST-CHANGES.md C4), on a standing cylinder with its
own texture layout: the measure sees light and not material, soft light is left alone without writing a file, and the
correction keeps hue, black and the brightest texels."""

from __future__ import annotations

import math

import numpy as np
import pytest
import trimesh
from PIL import Image

from vgac import face, guidance, imaging, lightfix
from vgac.stages import prep as prep_stage

T = 256
SKIN = np.array([0.80, 0.60, 0.50])
MUSTARD = np.array([0.72, 0.55, 0.12])


def _cylinder(n: int = 48, m: int = 48, r: float = 0.15, height: float = 1.7) -> dict[str, np.ndarray]:
    th = np.linspace(0, 2 * math.pi, n + 1)
    hs = np.linspace(0, height, m + 1)
    verts, uv = [], []
    for i, hh in enumerate(hs):
        for j, t in enumerate(th):
            verts.append((r * math.sin(t), hh, r * math.cos(t)))
            uv.append((0.05 + 0.9 * j / n, 0.05 + 0.9 * i / m))
    faces = []
    for i in range(m):
        for j in range(n):
            a, b = i * (n + 1) + j, i * (n + 1) + j + 1
            c, d = a + n + 1, b + n + 1
            faces += [(a, b, d), (a, d, c)]
    v, f = np.asarray(verts, float), np.asarray(faces, np.int64)
    return {"verts": v, "faces": f, "uv": np.asarray(uv, float), "vnormals": face.smooth_normals(v, f)}


def _paint(surf: lightfix.Surface, colour_of) -> np.ndarray:
    """A texture whose texels take colour_of(pos, nrm, h) (sRGB), grown into the gutter."""
    h = surf.X[:, 3]
    col = colour_of(surf.atlas.pos, surf.atlas.nrm, h)
    img = np.zeros((surf.T * surf.T, 3))
    img[surf.atlas.idx] = col
    return imaging.dilate(img.reshape(surf.T, surf.T, 3), surf.atlas.cover, 6)


def _lit(albedo_srgb: np.ndarray, nrm: np.ndarray, h: np.ndarray, hard: bool) -> np.ndarray:
    """Albedo under a light from one side and from above, falling off down the body (hard) or nearly even (soft)."""
    ldir = np.array([0.8, 0.3, 0.5]) / np.linalg.norm([0.8, 0.3, 0.5])
    lam = np.clip(nrm @ ldir, 0, 1)
    light = (0.12 + 0.88 * lam) * (0.25 + 0.75 * h) if hard else 0.93 + 0.07 * lam
    return lightfix.enc(lightfix.lin(albedo_srgb) * light[:, None])


def _two_materials(pos, nrm, h):
    return np.where((h > 0.5)[:, None], SKIN[None], MUSTARD[None])


@pytest.fixture(scope="module")
def surf():
    return lightfix.Surface(_cylinder(), T)


def test_measure_sees_light_not_material(surf):
    even = lightfix.measure(surf, _paint(surf, lambda p, n, h: np.repeat(SKIN[None], len(h), 0)))
    two = lightfix.measure(surf, _paint(surf, _two_materials))
    hard = lightfix.measure(surf, _paint(surf, lambda p, n, h: _lit(_two_materials(p, n, h), n, h, True)))
    soft = lightfix.measure(surf, _paint(surf, lambda p, n, h: _lit(_two_materials(p, n, h), n, h, False)))
    assert even["measured"] and even["patch_stops"] < 0.05
    # a sharp change of material (another hue) is an edge, not light
    assert two["patch_stops"] < 0.05 and two["whole_stops"] < 0.1
    assert hard["patch_stops"] > lightfix.GATE[1] and hard["whole_stops"] > 2.0
    assert soft["patch_stops"] < lightfix.GATE[0]
    assert hard["parts"]["height"] > 0.5 and hard["parts"]["side"] > 0.5


def test_strength_gate():
    assert lightfix.strength_of(0.0) == 0.0 and lightfix.strength_of(lightfix.GATE[0]) == 0.0
    assert lightfix.strength_of(lightfix.GATE[1]) == lightfix.MAX_STRENGTH == lightfix.strength_of(3.0)
    assert 0 < lightfix.strength_of(sum(lightfix.GATE) / 2) < lightfix.MAX_STRENGTH


def test_correction_evens_the_material_and_keeps_hue_black_bright_and_hair(surf):
    def colour(p, n, h):
        alb = _two_materials(p, n, h)
        alb = np.where((h < 0.2)[:, None], np.array([0.03, 0.03, 0.035])[None], alb)  # black shoes and trousers hem
        return _lit(alb, n, h, True)

    base = _paint(surf, colour)
    # an eye-white patch, already over the bright limit
    white = (surf.X[:, 3] > 0.9) & (surf.atlas.nrm[:, 0] > 0.7)
    img = base.reshape(-1, 3)
    img[surf.atlas.idx[white]] = 0.97
    base = img.reshape(T, T, 3)
    m = lightfix.measure(surf, base)
    keep = (surf.X[:, 3] > 0.8) & (surf.atlas.nrm[:, 2] < -0.5)  # "hair" at the back of the head
    g, held = lightfix.gain_field(surf, m, lightfix.strength_of(m["patch_stops"]), keep)
    assert np.all(g[keep] == 1.0)
    # no face found: every band is centred on itself
    g2, held2 = lightfix.gain_field(surf, {**m, "work": None}, lightfix.strength_of(m["patch_stops"]), keep)
    assert held2["face_band"] is None and np.isfinite(g2).all()
    assert lightfix.DOWN_MIN - 1e-6 <= g.min() and g.max() <= lightfix.UP_MAX + 1e-6
    G = np.ones(T * T)
    G[surf.atlas.idx] = g
    out = lightfix.apply_gain(base, G.reshape(T, T))
    new = out.reshape(-1, 3)[surf.atlas.idx]
    old = base.reshape(-1, 3)[surf.atlas.idx]
    band = lightfix.bands(m["lab"])
    assert lightfix.unevenness(surf, new, band) < lightfix.unevenness(surf, old, band) - lightfix.KEEP_MIN_GAIN
    lab_o, lab_n = face.to_lab(old[None])[0], face.to_lab(new[None])[0]
    coloured = np.hypot(lab_o[:, 1], lab_o[:, 2]) > 10
    dh = np.degrees(np.abs((np.arctan2(lab_o[:, 2], lab_o[:, 1]) - np.arctan2(lab_n[:, 2], lab_n[:, 1]) + np.pi) % (2 * np.pi) - np.pi))
    assert np.percentile(dh[coloured], 99) < 8.0
    black = lab_o[:, 0] < lightfix.BLACK_L[0]
    assert black.any() and np.all(lab_n[black, 0] <= lab_o[black, 0] + 1.0)
    assert np.all(new[white].max(-1) <= old[white].max(-1) + 1e-6)  # eye whites never brightened
    # brightening stops half way to the limit: nothing is pressed flat against it
    lim = lightfix.lin(np.array(lightfix.BRIGHT_MAX))
    lo, ln = lightfix.lin(old).max(-1), lightfix.lin(new).max(-1)
    under = lo < lim
    assert np.all(ln[under] <= lo[under] + 0.5 * (lim - lo[under]) + 1e-6)


def test_a_correction_that_does_not_even_the_colours_is_not_kept(tmp_path, surf, monkeypatch):
    monkeypatch.setattr(lightfix, "WORK", T)
    mesh = _cylinder()
    hard = _paint(surf, lambda p, n, h: _lit(_two_materials(p, n, h), n, h, True))
    glb = _glb(tmp_path / "m.glb", mesh, hard)
    imaging.write_map(hard, tmp_path / "hard.png")
    monkeypatch.setattr(lightfix, "gain_field", lambda s_, m_, st, keep=None: (np.exp(0.3 * m_["S"]), {"black_held": 0, "hair_held": 0, "limited": 0}))
    r = lightfix.run(glb, tmp_path / "hard.png", tmp_path / "out")
    lf = r["metrics"]["light_fix"]
    assert lf["rejected"] and not lf["applied"] and r["files"] == {} and lf["left"][0] == "not_even"
    assert lf["uneven_after"] > lf["uneven_before"] - lightfix.KEEP_MIN_GAIN
    assert not list((tmp_path / "out").glob("*.png"))


def _glb(path, mesh, tex: np.ndarray):
    tm = trimesh.Trimesh(mesh["verts"], mesh["faces"], process=False,
                         visual=trimesh.visual.TextureVisuals(uv=mesh["uv"], image=Image.fromarray((tex * 255).round().astype(np.uint8))))
    tm.export(path)
    return path


def test_run_writes_nothing_for_soft_light_and_corrects_hard_light(tmp_path, surf, monkeypatch):
    monkeypatch.setattr(lightfix, "WORK", T)
    mesh = _cylinder()
    soft = _paint(surf, lambda p, n, h: _lit(_two_materials(p, n, h), n, h, False))
    hard = _paint(surf, lambda p, n, h: _lit(_two_materials(p, n, h), n, h, True))
    glb = _glb(tmp_path / "m.glb", mesh, soft)
    for name, tex in (("soft", soft), ("hard", hard)):
        imaging.write_map(tex, tmp_path / f"{name}.png")
    r = lightfix.run(glb, tmp_path / "soft.png", tmp_path / "out_soft")
    lf = r["metrics"]["light_fix"]
    assert lf["measured"] and lf["strength"] == 0 and not lf["applied"] and r["files"] == {}
    assert not (tmp_path / "out_soft").exists()  # nothing written at all
    r = lightfix.run(glb, tmp_path / "hard.png", tmp_path / "out_hard")
    lf = r["metrics"]["light_fix"]
    assert lf["applied"] and lf["uneven_after"] < lf["uneven_before"] and set(r["files"]) == {"base_color", "base_color_lit"}
    assert np.array_equal(np.asarray(Image.open(r["files"]["base_color_lit"])), np.asarray(Image.open(tmp_path / "hard.png")))
    # a model with no texture layout is reported, never raised
    assert lightfix.run(tmp_path / "missing.glb", tmp_path / "hard.png", tmp_path / "x")["metrics"]["light_fix"]["ran"] is False


def _metrics(**over):
    m = {"export_mesh_hash": "a", "bake_mesh_hash": "a", "bake_match": 1.0, "rebaked": False, "texture_padding_px": 8,
         "texture_padding_target_px": 8, "atlas_used": 0.57, "light_score": 0.0, "delight_mode": "remove", "delight_applied": False,
         "delight_deferred": True, "pbr": {"albedo_out": 0.0, "metal_mixed": 0.0}, "pbr_fix_applied": False}
    m.update(over)
    return m


def _light(**lf):
    by = {c["name"]: c for c in prep_stage.surface_checks(_metrics(light_fix=lf))}
    return guidance.describe_check(by["baked_light"])


def test_check_after_painting_reads_the_light_found():
    soft = _light(ran=True, measured=True, patch_before=0.2, patch_after=0.2, applied=False, left=[])
    assert soft["passed"] and soft["value"] == 0.2 and soft["help"].startswith("No strong light from the picture") and "fix" not in soft
    done = _light(ran=True, measured=True, patch_before=1.2, whole_before=5.7, applied=True, uneven_before=3.5, uneven_after=3.15,
                  left=["black", "cast_shadows", "limit"])
    assert not done["passed"] and done["value"] == 1.2 and "1.2 stops across one material, 5.7 across the figure" in done["help"]
    assert "evened out by 10%" in done["help"] and "sharp shadow edges" in done["help"] and "black and very dark" in done["help"]
    assert "base_color_lit.png" in done["help"] and "Delight to Keep" in done["help"]
    assert done["fix"] == {"stage": "prep", "params": {"delight": "keep"}, "label": "Keep the picture's light"}
    rejected = _light(ran=True, measured=True, patch_before=1.1, applied=False, rejected=True, left=["not_even"])
    assert not rejected["passed"] and "tried and not kept" in rejected["help"] and "fix" not in rejected
    weak = _light(ran=True, measured=True, patch_before=0.65, applied=False, left=[])
    assert not weak["passed"] and "Too little to remove safely" in weak["help"] and "fix" not in weak
    unmeasured = _light(ran=False, measured=False, reason="too little coloured surface to tell light from colour")
    assert not unmeasured["passed"] and unmeasured["help"].startswith("Not measured") and "fix" not in unmeasured
    # without the deferred flag the old score decides, as before
    old = {c["name"]: c for c in prep_stage.surface_checks(_metrics(delight_deferred=False, light_score=0.05))}["baked_light"]
    assert old["passed"] and old["value"] == 0.05
