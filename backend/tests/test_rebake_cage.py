"""The re-bake cage distance per category (PASS_MARK_2: humanoid cage at half the share of the diagonal, every other
category at today's share), and that no environment variable changes it.

The pure rule is tested without Blender. The Blender tests run prep.py on a small textured box and read the cage and the
maximum ray distance it reports: the ratio cage / max ray is 0.25 for a humanoid and 0.5 for any other category, whatever
the size of the model, so the check does not depend on the model's scale. On master the category is not passed to the
bake at all, so the humanoid test fails there with a 0.5 ratio.
"""

from __future__ import annotations

import json
import os
import subprocess

import numpy as np
import pytest

from test_great_assets_laneb import BACKEND, _blender

approx = pytest.approx


# --- the pure rule ------------------------------------------------------------------------


def test_humanoid_cage_is_half_the_share_of_the_diagonal():
    from vgac.blender import rebake_cage

    assert rebake_cage.cage_extrusion_for("humanoid", 2.0) == approx(0.005 * 2.0)


@pytest.mark.parametrize("category", ["prop_rigid", "environment", "vehicle", "creature", None, ""])
def test_other_categories_keep_the_share_of_the_diagonal(category):
    from vgac.blender import rebake_cage

    assert rebake_cage.cage_extrusion_for(category, 2.0) == approx(0.01 * 2.0)


# --- the bake, on Blender -----------------------------------------------------------------


def _textured_box(path):
    import trimesh
    from PIL import Image

    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    box.unmerge_vertices()
    faces = len(box.faces)
    uv = np.tile(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]), (faces, 1))
    img = Image.fromarray((np.random.default_rng(3).random((64, 64, 3)) * 255).astype("uint8"))
    box.visual = trimesh.visual.TextureVisuals(uv=uv, image=img)
    box.export(path)


def _maps(tmp_path):
    from PIL import Image

    rng = np.random.default_rng(4)
    out = {}
    specs = {"base_color": ("RGB", (120, 90, 60)), "metallic": ("L", 0), "roughness": ("L", 200),
             "ao": ("L", 255), "normal": ("RGB", (128, 128, 255))}
    for key, (mode, value) in specs.items():
        arr = (rng.random((64, 64, 3)) * 255).astype("uint8") if key == "base_color" else None
        if arr is not None:
            im = Image.fromarray(arr, "RGB")
        elif mode == "RGB":
            im = Image.new("RGB", (64, 64), value)
        else:
            im = Image.new("L", (64, 64), value)
        p = tmp_path / f"{key}.png"
        im.save(p)
        out[key] = str(p)
    maps = tmp_path / "maps.json"
    maps.write_text(json.dumps(out), encoding="utf-8")
    return maps


def _bake_report(tmp_path, category, extra_env=None):
    exe = _blender()
    if not exe:
        pytest.skip("Blender not installed")
    pytest.importorskip("trimesh")
    run = tmp_path / category.replace("/", "_") if category else tmp_path / "none"
    run.mkdir(parents=True, exist_ok=True)
    src = run / "box.glb"
    _textured_box(src)
    out = run / "out.glb"
    script = BACKEND / "vgac" / "blender" / "prep.py"
    args = [exe, "-b", "--factory-startup", "--python", str(script), "--", str(src), str(out), "box",
            "--maps", str(_maps(run)), "--tex", "64", "--rebake", "always"]
    if category is not None:
        args += ["--category", category]
    env = dict(os.environ)
    env.pop("VGAC_REBAKE_OPTS", None)
    if extra_env:
        env.update(extra_env)
    subprocess.run(args, check=True, capture_output=True, timeout=600, env=env)
    report = json.loads((run / "out_report.json").read_text())
    assert report["rebaked"] is True
    return report


def _cage_share_of_max_ray(report):
    return report["cage_extrusion_m"] / report["max_ray_distance_m"]


def test_blender_humanoid_bake_uses_the_half_cage(tmp_path):
    r = _bake_report(tmp_path, "humanoid")
    assert _cage_share_of_max_ray(r) == approx(0.25, rel=0.01)


@pytest.mark.parametrize("category", ["prop_rigid", "environment"])
def test_blender_other_categories_keep_today_s_cage(tmp_path, category):
    r = _bake_report(tmp_path, category)
    assert _cage_share_of_max_ray(r) == approx(0.5, rel=0.01)


def test_no_environment_variable_changes_the_cage(tmp_path):
    base = _bake_report(tmp_path / "base", "humanoid")
    forced = _bake_report(tmp_path / "forced", "humanoid",
                          extra_env={"VGAC_REBAKE_OPTS": json.dumps({"cage_extrusion": 0.5, "oversample": 2})})
    assert forced["cage_extrusion_m"] == base["cage_extrusion_m"]
    assert forced["max_ray_distance_m"] == base["max_ray_distance_m"]
    assert _cage_share_of_max_ray(forced) == approx(0.25, rel=0.01)
