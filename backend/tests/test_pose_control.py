"""The humanoid step 1 picture is drawn to a fixed A-pose skeleton by the pose control model (vgac/pose_control.py)."""

from __future__ import annotations

import numpy as np

from vgac import pose_control, reference
from vgac.graphs import concept


def test_skeleton_is_an_a_pose_inside_the_middle_four_fifths():
    pts = pose_control.points(1024, 1024)
    assert len(pts) == 18
    ys = [y for _, y in pts]
    assert min(ys) >= 0.1 * 1024 - 1 and max(ys) <= 0.9 * 1024 + 1
    for a, b in ((pts[2], pts[3]), (pts[3], pts[4]), (pts[5], pts[6]), (pts[6], pts[7])):
        angle = np.degrees(np.arctan2(abs(b[0] - a[0]), b[1] - a[1]))
        assert abs(angle - pose_control.ARM_ANGLE) < 1
    assert pts[10][0] < pts[8][0] < 512 < pts[11][0] < pts[13][0]  # the feet are wider apart than the hips


def test_skeleton_picture_is_coloured_sticks_on_black():
    im = np.asarray(pose_control.skeleton(512, 512))
    assert im.shape == (512, 512, 3)
    lit = im.max(axis=2) > 0
    assert 0.002 < lit.mean() < 0.05
    assert not lit[:40].any() and not lit[-40:].any()  # empty above the head and below the feet


def test_humanoid_asks_for_pose_control_and_other_categories_do_not():
    assert pose_control.control_spec(reference.category("humanoid")) == {"strength": 0.8, "end": 1.0}
    for c in reference.categories():
        if c["id"] != "humanoid":
            assert pose_control.control_spec(c) is None, c["id"]


def test_graph_with_control_patches_the_sampler_model_and_without_is_unchanged():
    plain = concept.zimage_graph("a knight", 1, "p")
    assert concept.concept_graph("zturbo", "a knight", 1, "p") == plain
    g = concept.concept_graph("zturbo", "a knight", 1, "p", control={"image": "s.png", "strength": 0.7, "end": 0.6})
    assert g["6"]["inputs"]["model"] == ["pc", 0]
    pc = g["pc"]["inputs"]
    assert pc["model"] == ["2", 0] and pc["strength"] == 0.7 and pc["end_percent"] == 0.6 and g["pc_img"]["inputs"]["image"] == "s.png"
    assert g["pc_patch"]["inputs"]["name"] == concept.POSE_PATCH
    assert {k: v for k, v in g.items() if k not in ("pc", "pc_img", "pc_patch", "6")} == {k: v for k, v in plain.items() if k != "6"}


def _stage(tmp_path, category):
    import asyncio
    from types import SimpleNamespace

    from vgac.stages import concept as stage

    sent, params_set, used = [], {}, []

    async def stage_input(path, name):
        sent.append((path.name, name))
        return name

    sc = SimpleNamespace(category=reference.category(category), asset=SimpleNamespace(held_item=None, equipment=None, parts=[]), dir=tmp_path,
                         version=SimpleNamespace(id="abcdef123456"), stage_input=stage_input,
                         set_params=lambda **k: params_set.update(k), use_model=used.append)

    def run(model="zturbo", params=None):
        return asyncio.run(stage._pose_control(sc, model, params or {}, 512, 512))
    return run, sent, params_set, used


def test_stage_stages_the_skeleton_only_for_the_zturbo_humanoid_picture(tmp_path):
    run, sent, params_set, used = _stage(tmp_path, "humanoid")
    got = run()
    assert got == {"image": "vgac_abcdef12_pose_control.png", "strength": 0.8, "end": 1.0}
    assert sent == [("pose_control.png", "vgac_abcdef12_pose_control.png")] and params_set["pose_control"]["strength"] == 0.8
    assert used == ["pose_control"]
    assert run(model="anima") is None and run(model="zbase") is None and run(params={"readiness_block": False}) is None
    run, sent, *_ = _stage(tmp_path, "prop_rigid")
    assert run() is None and sent == []
