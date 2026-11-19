"""The finger-turn cap in the retarget (blender/retarget.py --finger-cap, gait.cap_turn) and the rig step's setting."""
from __future__ import annotations

import json
import math
import subprocess
import textwrap
from pathlib import Path

import pytest

import machine_tools
from vgac.blender import gait
from vgac.stages import rig

BLENDER = machine_tools.blender()
UAL1_GLB = Path(__file__).resolve().parents[2] / "data" / "animations" / "ual1" / "Universal Animation Library[Standard]" / "Unreal-Godot" / "UAL1_Standard.glb"
needs = pytest.mark.skipif(BLENDER is None or not UAL1_GLB.is_file(), reason="needs Blender and the UAL1 clip library")


def _q(axis, deg):
    h = math.radians(deg) / 2
    n = math.sqrt(sum(a * a for a in axis))
    return (math.cos(h),) + tuple(math.sin(h) * a / n for a in axis)


def _angle(q):
    return math.degrees(2 * math.acos(min(1.0, abs(q[0]))))


def test_cap_turn_never_exceeds_the_cap_and_keeps_the_direction():
    cap = math.radians(50)
    for deg in (10, 49, 50, 51, 77, 120, 179):
        q = gait.cap_turn(_q((0.3, 1.0, -0.2), deg), cap)
        assert _angle(q) <= 50 + 1e-6
        if deg > 50:
            assert abs(_angle(q) - 50) < 1e-6
        # same axis as the input
        ref = _q((0.3, 1.0, -0.2), min(deg, 50))
        assert all(abs(a - b) < 1e-6 for a, b in zip(q, ref))


def test_cap_turn_leaves_a_small_turn_alone_whichever_sign_the_quaternion_has():
    q = _q((0, 0, 1), 20)
    neg = tuple(-v for v in q)  # the same turn written the other way
    for x in (q, neg):
        out = gait.cap_turn(x, math.radians(50))
        assert abs(_angle(out) - 20) < 1e-6
    # a turn of -77 degrees is capped to -50, not flipped or opened the other way
    out = gait.cap_turn(_q((0, 1, 0), -77), math.radians(50))
    assert abs(_angle(out) - 50) < 1e-6
    ref = _q((0, 1, 0), -50)
    assert all(abs(a - b) < 1e-6 for a, b in zip(out, ref)) or all(abs(a + b) < 1e-6 for a, b in zip(out, ref))


def test_the_setting_reads_degrees_and_off():
    assert rig.finger_cap_of({}) == rig.FINGER_CAP_DEFAULT == 40.0
    assert rig.finger_cap_of({"finger_cap": 65}) == 65.0
    assert rig.finger_cap_of({"finger_cap": 0}) is None and rig.finger_cap_of({"finger_cap": False}) is None
    assert rig.finger_cap_of({"finger_cap": "x"}) == rig.FINGER_CAP_DEFAULT
    assert {"punch", "attack", "Punch_", "Sword_", "Pistol_"} <= set(rig.FINGER_CAP_EXCEPT)


MEASURE = textwrap.dedent('''
    import bpy, json, sys, math
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath={glb!r})
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    ad = arm.animation_data
    for tr in list(ad.nla_tracks):
        ad.nla_tracks.remove(tr)
    out = {{}}
    for clip in ("idle", "punch"):
        act = bpy.data.actions[clip]
        ad.action = act
        if hasattr(ad, "action_slot") and act.slots:
            ad.action_slot = act.slots[0]
        bpy.context.scene.frame_set(10)
        out[clip] = {{pb.name: [round(x, 5) for x in pb.matrix_basis.to_quaternion()] for pb in arm.pose.bones}}
    open({out!r}, "w").write(json.dumps(out))
''')


def _retarget(tmp_path, tag, *extra):
    import test_animation_quality as taq

    build = tmp_path / "build.py"
    src = tmp_path / "synthetic.glb"
    if not src.is_file():
        build.write_text(taq._BUILD, encoding="utf-8")
        subprocess.run([str(BLENDER), "-b", "--python", str(build), "--", str(src)], check=True, capture_output=True, timeout=300)
    out = tmp_path / f"anim_{tag}.glb"
    script = Path(rig.__file__).resolve().parents[1] / "blender" / "retarget.py"
    subprocess.run([str(BLENDER), "-b", "--python", str(script), "--", str(src), str(out), "--library", str(UAL1_GLB),
                    "--clips", "idle=Idle_Loop,punch=Punch_Jab", "--loops", "idle", *extra], check=True, capture_output=True, timeout=600)
    res = tmp_path / f"m_{tag}.json"
    m = tmp_path / f"measure_{tag}.py"
    m.write_text(MEASURE.format(glb=str(out), out=str(res)), encoding="utf-8")
    subprocess.run([str(BLENDER), "-b", "--python", str(m)], check=True, capture_output=True, timeout=300)
    return json.loads(res.read_text())


@needs
def test_cap_limits_a_finger_joint_leaves_other_bones_and_exempt_clips_alone_and_off_is_today(tmp_path):
    today = _retarget(tmp_path, "today")
    off = _retarget(tmp_path, "off", "--finger-cap", "0")
    capped = _retarget(tmp_path, "cap", "--finger-cap", "30", "--finger-cap-except", "Punch_")
    finger = "mixamorig:LeftHandIndex1"
    assert _angle(today["idle"][finger]) > 60  # the library's own curl
    assert off == today  # cap off reproduces today's frames exactly
    assert _angle(capped["idle"][finger]) <= 31  # a capped joint never exceeds the cap
    assert _angle(capped["punch"][finger]) > 60 and capped["punch"] == today["punch"]  # an exempt action prefix keeps the library's curl
    others = [n for n in today["idle"] if "HandIndex" not in n]
    assert all(capped["idle"][n] == today["idle"][n] for n in others)  # non-finger bones untouched
