"""Hair matte: when the hair pass does not run but the finder's outline found the hair, its texels get a
matte roughness and nothing else changes (vgac/hair.py matte_hair, vgac/stages/hair_detail.py matte_skipped)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
from PIL import Image

from vgac import hair
from vgac.stages import hair_detail as hd

T = 64


def two_quads() -> dict[str, np.ndarray]:
    """Two quads side by side in UV: the left one is faces 0 and 1, the right one faces 2 and 3."""
    uv = np.array([[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0],
                   [0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    verts = np.zeros((8, 3))
    return {"uv": uv, "faces": faces, "verts": verts}


def test_matte_sets_only_the_chosen_triangles_to_matte():
    mesh = two_quads()
    rough = np.full((T, T), 0.3)
    tris = np.array([True, True, False, False])  # the left quad
    new, n = hair.matte_hair(mesh, tris, rough)
    assert n > 0
    assert new.shape == rough.shape
    left = hair.tri_mask(mesh, tris, T)
    assert np.all(new[left] == hair.MATTE_ROUGHNESS)
    assert np.all(new[~left] == 0.3)  # the right quad and the gutter are as they were


def test_matte_with_no_hair_changes_nothing():
    mesh = two_quads()
    rough = np.linspace(0.0, 1.0, T * T).reshape(T, T)
    new, n = hair.matte_hair(mesh, np.zeros(4, dtype=bool), rough)
    assert n == 0
    assert np.array_equal(new, rough)


def test_matte_does_not_touch_the_input_map():
    mesh = two_quads()
    rough = np.full((T, T), 0.2)
    hair.matte_hair(mesh, np.array([True, True, False, False]), rough)
    assert np.all(rough == 0.2)


class _Ctx:
    """The one attribute matte_skipped reads from the stage context: its work folder."""

    def __init__(self, folder: Path):
        self.dir = folder


def test_matte_skipped_writes_only_a_roughness_map(tmp_path, monkeypatch):
    mesh = two_quads()
    rough_png = tmp_path / "roughness.png"
    Image.fromarray(np.full((T, T), 77, dtype=np.uint8), "L").save(rough_png)
    monkeypatch.setattr(hd.face, "load_mesh", lambda glb: mesh)
    tris = np.array([True, True, False, False])
    res = asyncio.run(hd.matte_skipped(_Ctx(tmp_path), tmp_path / "prep.glb", {"roughness": rough_png}, tris))
    assert res["record"]["applied"] is True
    assert res["record"]["texels"] == int(hair.tri_mask(mesh, tris, T).sum())
    out = np.asarray(Image.open(res["files"]["roughness"]).convert("L"))
    left = hair.tri_mask(mesh, tris, T)
    assert np.all(out[left] == 255)
    assert np.all(out[~left] == 77)


def test_matte_skipped_without_a_roughness_map_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(hd.face, "load_mesh", lambda glb: two_quads())
    res = asyncio.run(hd.matte_skipped(_Ctx(tmp_path), tmp_path / "prep.glb", {}, np.array([True, True, False, False])))
    assert res["files"] == {}
    assert res["record"]["applied"] is False


def test_matte_skipped_with_too_few_texels_does_nothing(tmp_path, monkeypatch):
    mesh = two_quads()
    rough_png = tmp_path / "roughness.png"
    Image.fromarray(np.full((T, T), 77, dtype=np.uint8), "L").save(rough_png)
    monkeypatch.setattr(hd.face, "load_mesh", lambda glb: mesh)
    res = asyncio.run(hd.matte_skipped(_Ctx(tmp_path), tmp_path / "prep.glb", {"roughness": rough_png},
                                       np.zeros(4, dtype=bool)))
    assert res["files"] == {}
    assert res["record"]["reason"] == "too_few_texels"
