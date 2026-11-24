"""Arm pose (WP-17): each built-in upper arm drops from its measured rest angle to a target angle from straight down.
Plain-Python arithmetic first (blender/gait.py), the rig setting's schema, then one Blender run on a synthetic
humanoid whose arms rest raised 102 degrees, skipped without Blender (VGAC_TEST_BLENDER)."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import pytest

from tests.machine_tools import blender as machine_blender
from vgac import reference
from vgac.blender import gait
from vgac.stages import rig

ROOT = Path(__file__).resolve().parents[2]
BLENDER = machine_blender() or Path("blender-not-found")
PROCANIM = ROOT / "backend" / "vgac" / "blender" / "procanim.py"


def _vec_from_down_angle(deg: float, x: float = 1.0) -> tuple[float, float, float]:
    """A unit direction deg degrees from straight down, leaning out to +x (x = 1) or -x (x = -1)."""
    r = math.radians(deg)
    return (x * math.sin(r), 0.0, -math.cos(r))


# ---- the arithmetic -----------------------------------------------------------------------------------------------


def test_targets_are_the_plan_values_and_the_choices_order_close_relaxed_open():
    assert gait.ARM_TARGET_DEG == {"idle": 24.0, "walk": 23.0, "run": 35.0, "turn": 22.0}
    assert gait.ARM_POSE_SHIFT_DEG == {"close": -8.0, "relaxed": 0.0, "open": 10.0}
    assert gait.ARM_POSE_DEFAULT == "relaxed"
    for clip in gait.ARM_TARGET_DEG:
        close, relaxed, opened = (gait.arm_target_deg(clip, c) for c in ("close", "relaxed", "open"))
        assert close < relaxed < opened
        assert relaxed == gait.ARM_TARGET_DEG[clip]
        # The same rest arm drops least for Open and most for Close.
        drops = [gait.arm_drop_deg(100.0, clip, c) for c in ("close", "relaxed", "open")]
        assert drops[0] > drops[1] > drops[2]


def test_a_rest_angle_is_measured_from_straight_down():
    assert gait.rest_arm_angle((0, 0, 0), (0, 0, -1)) == pytest.approx(0.0, abs=1e-9)
    assert gait.rest_arm_angle((0, 0, 0), (1, 0, 0)) == pytest.approx(90.0)
    assert gait.rest_arm_angle((0, 0, 0), (0, 0, 1)) == pytest.approx(180.0)
    # A raised arm: 102 degrees from straight down, a little above horizontal.
    assert gait.rest_arm_angle((0, 0, 0), _vec_from_down_angle(102.0)) == pytest.approx(102.0)


def test_the_drop_lands_the_arm_on_its_target_in_the_side_plane():
    up, forward = (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)
    arm = _vec_from_down_angle(102.0)                     # a left arm resting 102 degrees from straight down
    drop = gait.arm_drop_deg(102.0, "walk", "relaxed")    # 79 degrees to reach 23
    assert drop == pytest.approx(79.0)
    axis = gait.v_norm(gait.v_cross(arm, up))             # the drop turns about the axis square to the arm and up
    after = gait.q_rotate(gait.q_axis_angle(axis, math.radians(-drop)), arm)
    assert gait.angle_from_down(after) == pytest.approx(23.0, abs=1e-6)
    assert gait.angle_from_down(arm) == pytest.approx(102.0, abs=1e-6)
    assert gait.v_dot(after, forward) == pytest.approx(0.0, abs=1e-9)  # the drop does not swing the arm forward


def test_built_in_walk_on_a_102_degree_arm_averages_under_40_degrees():
    """The acceptance case: rest arm 102 degrees from straight down; the walk drops it to 23 and swings it 20 degrees each
    way about the arm's swing axis. Measured here on the vectors, then as the mean over the cycle."""
    up, forward = (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)
    rest = _vec_from_down_angle(102.0)
    drop = gait.arm_drop_deg(gait.rest_arm_angle((0, 0, 0), rest), "walk", "relaxed")
    dropped = gait.q_rotate(gait.q_axis_angle(gait.v_norm(gait.v_cross(rest, up)), math.radians(-drop)), rest)
    swing_axis = gait.v_norm(gait.v_cross(dropped, forward))
    n = 64
    angles = []
    for i in range(n):
        phi = gait.WALK_ARM_SWING_DEG * math.cos(2 * math.pi * i / n)
        angles.append(gait.angle_from_down(gait.q_rotate(gait.q_axis_angle(swing_axis, math.radians(phi)), dropped)))
    mean = sum(angles) / n
    assert mean < 40.0
    assert mean == pytest.approx(gait.walk_arm_mean_deg("walk", "relaxed", samples=n), abs=1e-6)
    print(f"walk arm mean angle from straight down for a 102 degree rest arm: {mean:.2f}")


def test_hip_clamp_keeps_the_arm_out_of_the_hips_and_above_the_minimum():
    # A narrow chest: the hand would cross the hip line at a small angle, so the hold is raised to the angle that clears it.
    clear = gait.hip_clear_min_deg(shoulder_out=0.02, arm_len=0.3, hip_half=0.2)
    assert clear == pytest.approx(math.degrees(math.asin(0.18 / 0.3)))
    assert gait.arm_hold_deg("turn", "close", 0.02, 0.3, 0.2) == pytest.approx(clear)
    assert gait.arm_hold_deg("turn", "close", 0.02, 0.3, 0.2) > gait.arm_target_deg("turn", "close")
    # A shoulder already outside the hips needs no clamp.
    assert gait.hip_clear_min_deg(shoulder_out=0.25, arm_len=0.3, hip_half=0.2) == 0.0
    # Targets never fall below the minimum angle, however far the breath or the setting pulls them.
    assert gait.arm_hold_deg("turn", "close", extra=-30.0) == pytest.approx(gait.ARM_MIN_DEG)
    assert gait.arm_hold_deg("idle", "close") == pytest.approx(16.0)
    # Without the skeleton's widths only the minimum applies.
    assert gait.arm_hold_deg("walk", "relaxed") == pytest.approx(23.0)


def test_unknown_choice_and_clip_raise():
    with pytest.raises(ValueError):
        gait.arm_pose_choice("wide")
    assert gait.arm_pose_choice(None) == "relaxed" and gait.arm_pose_choice("") == "relaxed"
    with pytest.raises(ValueError):
        gait.arm_target_deg("attack", "relaxed")


# ---- the setting ----------------------------------------------------------------------------------------------------


def test_the_schema_offers_close_relaxed_and_open_on_humanoids_only_and_refuses_anything_else():
    entry = next(p for p in reference.STAGE_SCHEMAS["rig"] if p["name"] == "arm_pose")
    assert entry["options"] == ["close", "relaxed", "open"] and entry["default"] == "relaxed"
    assert entry["categories"] == ["humanoid"] and entry["label"] == "Arm pose"
    for choice in ("close", "relaxed", "open"):
        assert reference.validate_params("rig", {"arm_pose": choice})["arm_pose"] == choice
    with pytest.raises(ValueError):
        reference.validate_params("rig", {"arm_pose": "wide"})
    with pytest.raises(ValueError):
        rig.gait_arm_pose({"arm_pose": "wide"})
    assert rig.gait_arm_pose({}) == "relaxed"


def test_changing_the_arm_pose_remakes_only_the_moves():
    assert "arm_pose" in rig.MOVES_PARAMS
    assert rig.reuse_change({"arm_pose": "relaxed"}, {"arm_pose": "open"}) == {"moves"}


# ---- the Blender run ------------------------------------------------------------------------------------------------

_BUILD = r'''
import math, sys
import bpy

out = sys.argv[sys.argv.index("--") + 1]
ANG = float(sys.argv[sys.argv.index("--") + 2])
bpy.ops.wm.read_factory_settings(use_empty=True)
J = {"Hips": (0, 0, 0.95), "Spine": (0, 0, 1.05), "Spine1": (0, 0, 1.2), "Spine2": (0, 0, 1.35), "Neck": (0, 0, 1.5),
     "Head": (0, 0, 1.55), "HeadTop": (0, 0, 1.75)}
P = {"Spine": "Hips", "Spine1": "Spine", "Spine2": "Spine1", "Neck": "Spine2", "Head": "Neck"}
r = math.radians(ANG)
for s, x in (("Left", 1), ("Right", -1)):
    A = (0.18 * x, 0, 1.40)
    E = (A[0] + 0.27 * math.sin(r) * x, 0, A[2] - 0.27 * math.cos(r))
    H = (E[0] + 0.25 * math.sin(r) * x, 0, E[2] - 0.25 * math.cos(r))
    J.update({s + "Shoulder": (0.05 * x, 0, 1.42), s + "Arm": A, s + "ForeArm": E, s + "Hand": H,
              s + "UpLeg": (0.1 * x, 0, 0.95), s + "Leg": (0.11 * x, 0, 0.52), s + "Foot": (0.11 * x, 0.02, 0.1),
              s + "ToeBase": (0.11 * x, -0.12, 0.02)})
    P.update({s + "Shoulder": "Spine2", s + "Arm": s + "Shoulder", s + "ForeArm": s + "Arm", s + "Hand": s + "ForeArm",
              s + "UpLeg": "Hips", s + "Leg": s + "UpLeg", s + "Foot": s + "Leg", s + "ToeBase": s + "Foot"})
kids = {}
for c, p in P.items():
    kids.setdefault(p, []).append(c)
ad = bpy.data.armatures.new("Armature")
ob = bpy.data.objects.new("Armature", ad)
bpy.context.scene.collection.objects.link(ob)
bpy.context.view_layer.objects.active = ob
bpy.ops.object.mode_set(mode="EDIT")
eb = {}
for n in ["Hips"] + list(P):
    b = ad.edit_bones.new("mixamorig:" + n)
    b.head = J[n]
    ch = kids.get(n)
    tail = J[ch[0]] if ch else (J["HeadTop"] if n == "Head" else tuple(h + d for h, d in zip(J[n], (0, -0.05, 0))))
    if n == "Hips":
        tail = J["Spine"]
    b.tail = tail
    eb[n] = b
for c, p in P.items():
    eb[c].parent = eb[p]
bpy.ops.object.mode_set(mode="OBJECT")
# A body box that spans the whole figure (procanim reads the height and the arm band from the mesh bounds).
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 0.95))
cube = bpy.context.active_object
cube.scale = (1.0, 1.0, 9.0)
cube.parent = ob
mod = cube.modifiers.new("arm", "ARMATURE")
mod.object = ob
g = cube.vertex_groups.new(name="mixamorig:Hips")
g.add([v.index for v in cube.data.vertices], 1.0, "REPLACE")
for s, x in (("Left", 1), ("Right", -1)):
    bpy.ops.mesh.primitive_cube_add(size=0.08, location=(0.11 * x, -0.04, 0.04))
    fc = bpy.context.active_object
    fc.parent = ob
    fm = fc.modifiers.new("arm", "ARMATURE")
    fm.object = ob
    fg = fc.vertex_groups.new(name="mixamorig:" + s + "Foot")
    fg.add([v.index for v in fc.data.vertices], 1.0, "REPLACE")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
print("BUILD_OK")
'''


@pytest.mark.skipif(not (BLENDER.is_file() and PROCANIM.is_file()), reason="needs Blender (VGAC_TEST_BLENDER)")
def test_procanim_walk_on_a_102_degree_rest_arm_measures_under_40_degrees(tmp_path):
    build = tmp_path / "build.py"
    build.write_text(_BUILD, encoding="utf-8")
    src = tmp_path / "raised.glb"
    subprocess.run([str(BLENDER), "-b", "--python", str(build), "--", str(src), "102"], check=True, capture_output=True, timeout=300)
    assert src.is_file()
    out = tmp_path / "anim.glb"
    subprocess.run([str(BLENDER), "-b", "--python", str(PROCANIM), "--", str(src), str(out), "--motion", "biped",
                    "--clips", "idle,walk,run,turn_left", "--arm-pose", "relaxed"], check=True, capture_output=True, timeout=600)
    rep = json.loads((tmp_path / "anim_anim.json").read_text(encoding="utf-8"))
    pose = rep["arm_pose"]
    assert pose["choice"] == "relaxed" and len(pose["arms"]) == 2
    assert {"idle", "walk", "run", "turn"} <= set(pose["arms"][0]["holds"])
    assert set(rep["clips"]) >= {"idle", "walk", "run", "turn_left"}
    for arm in pose["arms"]:
        assert arm["rest_deg"] == pytest.approx(102.0, abs=0.5)
        assert arm["holds"]["walk"] == pytest.approx(23.0, abs=0.5)
    measured = pose["walk_measured"]
    assert set(measured) == {"left", "right"}
    for side, m in measured.items():
        print(f"walk upper arm ({side}) mean {m['mean_deg']} deg, min {m['min_deg']}, max {m['max_deg']}")
        assert m["mean_deg"] < 40.0


# ---- the run target is the cycle mean, and the neutral pose clip (WP-17 follow-ups) -------------------------------------


@pytest.mark.parametrize("choice", ["close", "relaxed", "open"])
def test_the_run_cycle_averages_its_target(choice):
    """The run swings the upper arm about its hold (gait.RUN_ARM_SWING_*); the hold is solved so the cycle's mean
    lands on the run target. Close is held at the 10-degree floor, where its target mean is still reached within 1."""
    hold = gait.arm_hold_deg("run", choice)
    target = gait.arm_target_deg("run", choice)
    assert gait.run_arm_mean_deg(hold) == pytest.approx(target, abs=1.0)
    assert hold >= gait.ARM_MIN_DEG


def test_the_run_swing_range_is_kept():
    """The solve moves the hold, not the swing: the swing constants stay as they were (bias 8, amplitude 38 degrees),
    and the hold it finds is the one whose cycle mean is the target."""
    assert (gait.RUN_ARM_SWING_BIAS_DEG, gait.RUN_ARM_SWING_DEG) == (8.0, 38.0)
    hold = gait.arm_hold_deg("run")
    assert gait.run_hold_for_mean(gait.ARM_TARGET_DEG["run"]) == pytest.approx(hold, abs=1e-6)
    lo = gait.swung_arm_angle(hold, gait.RUN_ARM_SWING_BIAS_DEG - gait.RUN_ARM_SWING_DEG)
    hi = gait.swung_arm_angle(hold, gait.RUN_ARM_SWING_BIAS_DEG + gait.RUN_ARM_SWING_DEG)
    assert hi - lo > 10.0


def test_the_neutral_pose_clip_is_one_key_and_not_looping():
    assert gait.clip_frames(gait.NEUTRAL_CLIP, "biped", 24) == (0, 0)
    rows = {r["name"]: r for r in gait.clip_table(["idle", gait.NEUTRAL_CLIP], "biped", 24)}
    neutral = rows[gait.NEUTRAL_CLIP]
    assert neutral["loop"] is False and neutral["frame_start"] == 0 and neutral["frame_end"] == 0
    assert neutral["frames"] == 1 and neutral["motion_frames"] == 0
    assert rows["idle"]["loop"] is True


def test_the_neutral_pose_setting_is_off_by_default_and_humanoid_only():
    p = next(p for p in reference.STAGE_SCHEMAS["rig"] if p["name"] == "neutral_pose")
    assert p["type"] == "boolean" and p["default"] is False and p["categories"] == ["humanoid"]


def test_a_lone_wing_candidate_on_a_biped_is_not_a_wing():
    """The boy's right arm (rest 102 degrees) was taken as the only wing, so its wing moves folded that arm alone."""
    src = (ROOT / "backend" / "vgac" / "blender" / "procanim.py").read_text(encoding="utf-8")
    assert "if len(wing_info) < 2:" in src  # the lone-candidate drop now applies to every body (rule L), biped included
