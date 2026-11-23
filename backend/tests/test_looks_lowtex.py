"""Retro 64 and Low-poly settings from the look research (docs/research/looks/retro64-lowpoly.md, local).

Paint standard D1: nothing may change for any other look. tests/data/looks_golden.json was written by
tests/make_looks_golden.py on master's code (commit 2493e44): the recipe digest and every step 3 and step 4 decision this
branch touches, for realistic_pbr, painted, cel, clay and pixel, every category and platform. The branch must give the
same. The Pixel art look was removed later; its rows went with it and no other row changed.
The crisp texture fields (surface texture_filter, palette_colors, pixel_snap; the engine section) were removed after
that: the file was written again, and against the earlier one only those fields and the recipe id differ."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from vgac import headtris, looktex, reference
from vgac.stages import face_detail, mesh as mesh_stage, prep

HERE = Path(__file__).resolve().parent


def test_other_looks_unchanged_against_master():
    import make_looks_golden

    golden = json.loads((HERE / "data" / "looks_golden.json").read_text(encoding="utf-8"))
    now = json.loads(json.dumps(make_looks_golden.decisions(), sort_keys=True, default=str))
    assert set(now) == set(golden)
    for key in golden:
        assert now[key] == golden[key], key


# --- the recipes --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("platform,triangles", [("mobile_low", 2000), ("pc_mid", 3000), ("high", 3000)])
def test_retro64_triangle_cap_and_bake_size(platform, triangles):
    r = reference.recipe("retro64", "humanoid", platform)
    assert r["mesh"]["triangles"] == triangles  # 0.4 of the budget, never over 3,000 (High was 24,000)
    assert r["mesh"]["triangle_cap"] == 3000
    assert r["mesh"]["texture"] == 256 and r["mesh"]["bake_texture"] == 1024
    assert r["surface"]["face_layout"] is True
    assert "colour_per_face" not in r["surface"] and r["surface"]["shading"] == "smooth"


@pytest.mark.parametrize("platform,triangles", [("mobile_low", 1250), ("pc_mid", 1500), ("high", 1500)])
def test_lowpoly_triangle_cap_and_flat_colour(platform, triangles):
    r = reference.recipe("lowpoly_flat", "humanoid", platform)
    assert r["mesh"]["triangles"] == triangles and r["mesh"]["triangle_cap"] == 1500
    assert "bake_texture" not in r["mesh"] and r["mesh"]["texture"] == 512
    assert r["surface"]["colour_per_face"] is True and r["surface"]["face_keeps_texture"] is True
    assert r["surface"]["shading"] == "flat" and "face_layout" not in r["surface"]


def test_cap_keeps_the_floor_and_bake_never_over_the_platform():
    assert reference.recipe("lowpoly_flat", "prop_rigid", "mobile_low")["mesh"]["triangles"] == 300
    for plat in ("mobile_low", "pc_mid", "high"):
        m = reference.recipe("retro64", "prop_rigid", plat)["mesh"]
        assert m["triangles"] <= 3000 and m["bake_texture"] <= m["budget_texture"]


def test_retro64_lock_words_are_the_looks_own():
    s = reference.style("retro64")
    assert " ".join(s["lock"]["prompt_block"].split()) == " ".join(s["prompt_block"].split())
    assert "chunky low-polygon shapes" not in s["lock"]["prompt_block"]


# --- step 3 -------------------------------------------------------------------------------------------------------


def test_step3_bakes_retro64_at_1024_with_head_room():
    rec = reference.recipe("retro64", "humanoid", "pc_mid")
    t = mesh_stage.resolve_targets(rec, {}, reference.budget("humanoid", "pc_mid"), "humanoid")
    assert t["texture_size"] == 1024 and t["texture_source"] == "recipe"
    assert t["head_room"] == headtris.HEAD_ROOM and t["target_faces"] > 3000
    assert mesh_stage.resolve_targets(rec, {"texture_size": 512}, reference.budget("humanoid", "pc_mid"))["texture_size"] == 512


def test_step3_texture_check_judges_the_bake_size():
    rec = reference.recipe("retro64", "humanoid", "pc_mid")
    budget = reference.budget("humanoid", "pc_mid")
    assert mesh_stage.texture_target(rec, budget, baked=True)[0] == 1024
    assert mesh_stage.texture_target(rec, budget)[0] == 256  # what step 6 checks: the shipped size


def test_step3_lowpoly_unchanged_size_no_head_room():
    rec = reference.recipe("lowpoly_flat", "humanoid", "pc_mid")
    t = mesh_stage.resolve_targets(rec, {}, reference.budget("humanoid", "pc_mid"), "humanoid")
    assert t["texture_size"] == 512 and "head_room" not in t


def test_head_weighting_retro64_only():
    assert headtris.applies(reference.recipe("retro64", "humanoid", "pc_mid"), "humanoid")
    assert not headtris.applies(reference.recipe("retro64", "quadruped", "pc_mid"), "quadruped")
    assert not headtris.applies(reference.recipe("lowpoly_flat", "humanoid", "pc_mid"), "humanoid")  # flat shading


# --- step 4 decisions ---------------------------------------------------------------------------------------------


def test_late_shrink_only_for_a_bake_size():
    retro = reference.recipe("retro64", "humanoid", "pc_mid")
    look = prep.texture_look(retro)
    assert prep.late_cap_of(retro, look, 1024) == 256
    assert prep.late_cap_of(retro, look, 256) is None  # a model baked at the shipped size (made before this)
    for style in ("lowpoly_flat", "realistic_pbr", "painted"):
        rec = reference.recipe(style, "humanoid", "pc_mid")
        assert prep.late_cap_of(rec, prep.texture_look(rec), 4096) is None


def test_face_layout_for_retro64_characters_only():
    retro = reference.recipe("retro64", "humanoid", "pc_mid")
    assert face_detail.layout_only_on(retro, "humanoid")
    assert not face_detail.layout_only_on(retro, "quadruped")
    for style in ("lowpoly_flat", "realistic_pbr", "painted", "cel", "clay"):
        assert not face_detail.layout_only_on(reference.recipe(style, "humanoid", "pc_mid"), "humanoid")
    # the face paint pass itself still stands back for the look's 256 px texture
    assert face_detail.skip_reason("humanoid", retro, {}, True) == "style"


# --- looktex ------------------------------------------------------------------------------------------------------


def _quad_mesh() -> dict[str, np.ndarray]:
    """Two triangles side by side in the texture: the left half and the right half of the layout."""
    verts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0], [2, 1, 0]], dtype=np.float64)
    uv = np.array([[0.02, 0.02], [0.48, 0.02], [0.48, 0.98], [0.02, 0.98], [0.98, 0.02], [0.98, 0.98]])
    faces = np.array([[0, 1, 2], [1, 4, 5]], dtype=np.int64)
    return {"verts": verts, "faces": faces, "uv": uv}


def test_colour_per_face_makes_each_triangle_one_colour():
    m = _quad_mesh()
    T = 64
    yy, xx = np.mgrid[0:T, 0:T] / (T - 1)
    base = np.stack([xx, yy, 0.5 * np.ones_like(xx)], axis=2)  # a gradient: many colours per triangle
    r = looktex.colour_per_face(base, m, detail=False)
    tri = looktex.triangle_map(m, T)
    for t in (0, 1):
        texels = r["base_color"][tri == t]
        assert len(texels) > 20 and np.ptp(texels, axis=0).max() < 1e-9
    assert not np.allclose(r["base_color"][tri == 0][0], r["base_color"][tri == 1][0])
    # a kept triangle keeps its texels
    k = looktex.colour_per_face(base, m, keep=np.array([1]), detail=False)
    assert np.allclose(k["base_color"][tri == 1], base[tri == 1]) and k["kept"] == int((tri == 1).sum())


def _box(lo, hi):
    import trimesh

    b = trimesh.creation.box(bounds=[lo, hi])
    return np.asarray(b.vertices, dtype=np.float64), np.asarray(b.faces, dtype=np.int64)


def test_front_of_head_without_the_finder_takes_the_front_of_the_top():
    bv, bf = _box([-0.3, 0.0, -0.15], [0.3, 1.2, 0.15])     # body
    hv, hf = _box([-0.2, 1.2, -0.2], [0.2, 1.6, 0.2])       # head on top
    verts = np.vstack([bv, hv])
    faces = np.vstack([bf, hf + len(bv)])
    m = {"verts": verts, "faces": faces, "uv": np.zeros((len(verts), 2))}
    r = looktex.front_of_head(m, None, 0.0)
    assert r["how"] == "front of the head" and len(r["tris"])
    c = verts[faces[r["tris"]]].mean(axis=1)
    n = np.cross(verts[faces[r["tris"], 1]] - verts[faces[r["tris"], 0]], verts[faces[r["tris"], 2]] - verts[faces[r["tris"], 0]])
    assert (c[:, 1] >= 1.6 - looktex.FRONT_SHARE[1] * 1.6 - 1e-9).all() and (n[:, 2] > 0).all()
    # seen from a quarter turn, the side of the head counts as its front
    side = looktex.front_of_head(m, None, 90.0)
    ns = np.cross(verts[faces[side["tris"], 1]] - verts[faces[side["tris"], 0]], verts[faces[side["tris"], 2]] - verts[faces[side["tris"], 0]])
    assert len(side["tris"]) and (ns[:, 0] > 0).all() and math.isclose(len(side["tris"]), len(r["tris"]), rel_tol=1.0)


def test_shrink_by_island_never_mixes_neighbouring_pieces():
    from vgac import imaging

    # two pieces one texel apart at 64 (four at 256): red on the left, blue on the right
    uv = np.array([[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0], [0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    uvm = {"uv": uv, "faces": faces, "island": np.array([0, 0, 1, 1]), "up": np.zeros(4)}
    S = 256
    base = np.zeros((S, S, 3), np.float32)
    base[:, : S // 2] = (1, 0, 0)
    base[:, S // 2:] = (0, 0, 1)
    small = imaging.shrink_by_island(base, uvm, 64)
    lab = imaging.rasterise_uv(uvm, 64)["labels"]
    red, blue = lab == lab[32, 5], lab == lab[32, 60]
    assert np.allclose(small[red], (1, 0, 0)) and np.allclose(small[blue], (0, 0, 1))
    # the plain area average for a size that does not divide
    assert imaging.shrink_by_island(base, uvm, 100).shape == (100, 100, 3)


def _dense_box(lo, hi, size=0.03):
    import trimesh

    b = trimesh.creation.box(bounds=[lo, hi])
    v, f = trimesh.remesh.subdivide_to_size(b.vertices, b.faces, max_edge=size)
    return np.asarray(v, dtype=np.float64), np.asarray(f, dtype=np.int64)


def _figure_with_neck(neck_half: float = 0.03):
    """A body, a thin neck and a head: L1 Farmer's shape (an A pose, so no slice is SHOULDER_JUMP times the head)."""
    parts = [_dense_box([-0.3, 0.0, -0.15], [0.3, 1.2, 0.15]),            # body
             _dense_box([-neck_half, 1.2, -neck_half], [neck_half, 1.3, neck_half], 0.01),  # neck
             _dense_box([-0.2, 1.3, -0.2], [0.2, 1.7, 0.2])]                # head
    verts, faces, n = [], [], 0
    for v, f in parts:
        verts.append(v); faces.append(f + n); n += len(v)
    verts = np.vstack(verts)
    return {"verts": verts, "faces": np.vstack(faces), "uv": np.zeros((len(verts), 2))}


def test_whole_front_stops_at_the_neck_and_the_retro_path_does_not():
    m = _figure_with_neck()
    v, f = m["verts"], m["faces"]
    low = looktex.front_geometry(m, 0.0, neck=True)
    old = looktex.front_geometry(m, 0.0)
    c_low, c_old = v[f[low]].mean(axis=1), v[f[old]].mean(axis=1)
    assert (c_low[:, 1] > 1.2).all()                        # nothing of the body below the neck
    assert (c_old[:, 1] < 1.2).any()                        # the old band ran on into the body
    assert looktex.front_of_head(m, None, 0.0, whole_front=True)["tris"].tolist() == low.tolist()
    assert looktex.front_of_head(m, None, 0.0)["tris"].tolist() == old.tolist()   # Retro 64's face layout: unchanged


def test_a_figure_without_a_neck_keeps_the_band_to_the_shoulders():
    m = _figure_with_neck(neck_half=0.19)                   # a beard or a thick neck: no slice under half the head
    assert looktex.front_geometry(m, 0.0, neck=True).tolist() == looktex.front_geometry(m, 0.0).tolist()


def test_whole_front_keeps_the_finders_region_together_with_the_front_of_the_head(monkeypatch):
    m = _figure_with_neck()
    finder = np.arange(10, 20)                              # the finder's own region (on L1: the front of the hair)
    monkeypatch.setattr(looktex.face, "render_view", lambda *a, **k: None)
    monkeypatch.setattr(looktex.face, "find_face_checked", lambda *a, **k: {"found": True, "region": None})
    monkeypatch.setattr(looktex.face, "face_triangles", lambda *a, **k: finder)
    tex = np.zeros((8, 8, 3))
    alone = looktex.front_of_head(m, tex, 0.0)
    assert alone["how"] == "face finder" and alone["tris"].tolist() == finder.tolist()   # Retro 64: as before
    both = looktex.front_of_head(m, tex, 0.0, whole_front=True)
    assert both["how"] == "face finder and front of the head"
    assert set(finder) <= set(both["tris"]) and set(looktex.front_geometry(m, 0.0, neck=True)) <= set(both["tris"])


def test_colour_per_face_keeps_a_triangle_with_painted_detail_and_flattens_a_noisy_one():
    m = _quad_mesh()
    T = 128
    tri = looktex.triangle_map(m, T)
    rng = np.random.default_rng(1)
    base = np.full((T, T, 3), 0.6)
    base += rng.normal(0, 0.01, base.shape)             # triangle 1: near one colour, noise only
    ys, xs = np.where(tri == 0)
    cy, cx = int(ys.mean()), int(xs.mean())
    window = np.zeros((T, T), dtype=bool)
    window[cy - 12:cy + 12, cx - 12:cx + 12] = True          # triangle 0: a dark window in the middle
    base[window & (tri == 0)] = 0.1
    r = looktex.colour_per_face(base, m)
    assert r["detailed_faces"] == 1 and r["kept_faces"] == 1
    inner = (tri == 0) & window
    assert np.allclose(r["base_color"][inner], base[inner])                       # the window is still there
    flat = r["base_color"][tri == 1]
    assert np.ptp(flat, axis=0).max() < 1e-9                                     # the noisy triangle is one colour
    old = looktex.colour_per_face(base, m, detail=False)
    assert np.ptp(old["base_color"][tri == 0], axis=0).max() < 1e-9               # without the test the window is erased


def test_detail_does_not_unflatten_a_keep_list_and_keep_still_wins():
    m = _quad_mesh()
    base = np.full((128, 128, 3), 0.5)
    k = looktex.colour_per_face(base, m, keep=np.array([0]))
    tri = looktex.triangle_map(m, 128)
    assert k["kept_faces"] == 1 and k["detailed_faces"] == 0
    assert np.allclose(k["base_color"][tri == 0], base[tri == 0])
