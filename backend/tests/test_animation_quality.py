"""Animation quality: retarget maths, two-bone IK and settling, planted foot paths (pure Python), the library
retarget in Blender on a small synthetic Mixamo skeleton (skipped without Blender or the library), and the rig
stage's wiring of clips_source with a fake context."""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac.blender import gait
from vgac.stages import rig

ROOT = Path(__file__).resolve().parents[2]
BLENDER = Path(os.environ.get("VGAC_TEST_BLENDER", "F:/Tools/blender-5.2.3-windows-x64/blender.exe"))
UAL1_GLB = ROOT / "data/animations/ual1/Animation Library[Standard]/Godot/AnimationLibrary_Godot_Standard.glb"
UAL1_FBX = ROOT / "data/animations/ual1/Animation Library[Standard]/Unity/AnimationLibrary_Unity_Standard.fbx"


def _rand_q(rng):
    return gait.q_normalize(tuple(rng.gauss(0, 1) for _ in range(4)))


def _close_q(a, b, tol=1e-4):
    return gait.q_angle_deg(a, b) < tol


# ---- rest-pose compensation -----------------------------------------------------------------------------------

def test_compensation_maps_reference_to_reference_and_keeps_the_world_turn():
    rng = random.Random(3)
    for _ in range(50):
        s_ref, t_ref, turn = _rand_q(rng), _rand_q(rng), _rand_q(rng)
        # In the reference pose the target sits in its own reference pose.
        assert _close_q(gait.compensate(s_ref, s_ref, t_ref), t_ref)
        # A source bone turned by `turn` (world) away from its reference gives a target turned by the same turn.
        s = gait.q_mul(turn, s_ref)
        t = gait.compensate(s, s_ref, t_ref)
        assert _close_q(gait.q_mul(t, gait.q_conj(t_ref)), turn)


def test_compensation_makes_segments_point_the_same_way_whatever_the_bone_roll():
    """Two skeletons whose upper arm points along +X in the T-pose reference but with different bone rolls and rest
    poses: after the retarget the target segment points where the source segment points."""
    rng = random.Random(7)
    seg_local_s, seg_local_t = (0.0, 1.0, 0.0), (0.0, 1.0, 0.0)  # Blender bones point along local +Y
    for _ in range(30):
        s_ref = gait.q_mul(gait.q_from_to((0, 1, 0), (1, 0, 0)), gait.q_axis_angle((0, 1, 0), rng.uniform(-3, 3)))
        t_ref = gait.q_mul(gait.q_from_to((0, 1, 0), (1, 0, 0)), gait.q_axis_angle((0, 1, 0), rng.uniform(-3, 3)))
        turn = _rand_q(rng)
        s = gait.q_mul(turn, s_ref)
        t = gait.compensate(s, s_ref, t_ref)
        ds, dt = gait.q_rotate(s, seg_local_s), gait.q_rotate(t, seg_local_t)
        assert gait.v_len(gait.v_sub(ds, dt)) < 1e-9


def test_tpose_turns_level_the_arms_straighten_the_legs_and_carry_the_fingers():
    a_pose_arm = gait.v_norm((0.35, 0.0, -0.94))
    dirs = {"LeftArm": a_pose_arm, "LeftForeArm": gait.v_norm((0.2, -0.1, -0.97)), "LeftHand": (0.0, 0.0, -1.0),
            "LeftUpLeg": gait.v_norm((0.12, 0.02, -0.99)), "LeftLeg": gait.v_norm((0.0, 0.05, -1.0)),
            "LeftFoot": gait.v_norm((0.3, -0.9, -0.5)), "Spine": (0.0, 0.0, 1.0)}
    parents = {"Hips": None, "Spine": "Hips", "LeftShoulder": "Spine", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm",
               "LeftHand": "LeftForeArm", "LeftHandIndex1": "LeftHand", "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg",
               "LeftFoot": "LeftLeg", "LeftToeBase": "LeftFoot"}
    turns = gait.tpose_turns(dirs, parents)
    for bone, want in (("LeftArm", (1, 0, 0)), ("LeftForeArm", (1, 0, 0)), ("LeftHand", (1, 0, 0)),
                       ("LeftUpLeg", (0, 0, -1)), ("LeftLeg", (0, 0, -1))):
        got = gait.q_rotate(turns[bone], dirs[bone])
        assert gait.v_len(gait.v_sub(got, want)) < 1e-9, bone
    foot = gait.q_rotate(turns["LeftFoot"], dirs["LeftFoot"])
    assert abs(foot[0]) < 1e-9 and foot[1] < 0 and foot[2] == pytest.approx(dirs["LeftFoot"][2] / gait.v_len(dirs["LeftFoot"]), abs=0.2)
    assert turns["LeftHandIndex1"] == turns["LeftHand"] and turns["LeftToeBase"] == turns["LeftFoot"]
    assert turns["Spine"] == (1.0, 0.0, 0.0, 0.0) and turns["LeftShoulder"] == (1.0, 0.0, 0.0, 0.0)


def test_bone_map_reads_both_library_namings_and_reports_missing_fingers():
    ual1 = ["root", "DEF-hips", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-neck", "DEF-head",
            "DEF-shoulder.L", "DEF-upper_arm.L", "DEF-forearm.L", "DEF-hand.L", "DEF-f_index.01.L", "DEF-thumb.02.L",
            "DEF-thigh.R", "DEF-shin.R", "DEF-foot.R", "DEF-toe.R"]
    ue = ["root", "pelvis", "spine_01", "upperarm_l", "lowerarm_l", "hand_l", "index_01_l", "index_04_leaf_l", "ball_r", "calf_r"]
    assert [gait.library_to_mixamo(n) for n in ual1] == [None, "Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftShoulder",
                                                        "LeftArm", "LeftForeArm", "LeftHand", "LeftHandIndex1", "LeftHandThumb2",
                                                        "RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"]
    assert [gait.library_to_mixamo(n) for n in ue] == [None, "Hips", "Spine", "LeftArm", "LeftForeArm", "LeftHand",
                                                      "LeftHandIndex1", None, "RightToeBase", "RightLeg"]
    target = ["mixamorig:Hips", "mixamorig:LeftArm", "mixamorig:LeftHandMiddle1", "mixamorig:Extra", "Armature"]
    m, missing = gait.bone_map(ual1, target)
    assert m == {"mixamorig:Hips": "DEF-hips", "mixamorig:LeftArm": "DEF-upper_arm.L"}
    assert missing == ["LeftHandMiddle1"]  # a finger the library lacks; non-Mixamo bones are not listed


def test_clip_spec_round_trip():
    rows = gait.parse_clip_spec("idle=Idle_Loop,run=Jog_Fwd_Loop|Run_Loop,jump")
    assert rows == [("idle", ["Idle_Loop"]), ("run", ["Jog_Fwd_Loop", "Run_Loop"]), ("jump", ["jump"])]
    assert gait.parse_clip_spec(gait.clip_spec_text(rows)) == rows
    assert gait.parse_clip_spec([{"name": "walk", "from": ["Walk_Loop"]}, {"name": "hit", "from": "Hit_Chest"}]) == \
        [("walk", ["Walk_Loop"]), ("hit", ["Hit_Chest"])]


def test_contact_segments_join_a_run_that_wraps_round_a_loop():
    flags = [True, True, False, False, True, True, True]  # last frame repeats the first
    segs = gait.contact_segments(flags, cyclic=True)
    assert sorted(map(sorted, segs)) == [[0, 1, 4, 5, 6]]
    assert gait.contact_segments(flags, cyclic=False) == [[0, 1], [4, 5, 6]]
    assert gait.contact_weight(0.0, 0.03, 0.07) == 1.0 and gait.contact_weight(0.1, 0.03, 0.07) == 0.0


# ---- two-bone IK and settling ----------------------------------------------------------------------------------

def test_two_bone_ik_reaches_keeps_lengths_and_bends_toward_the_pole():
    rng = random.Random(11)
    for _ in range(200):
        root = tuple(rng.uniform(-1, 1) for _ in range(3))
        l1, l2 = rng.uniform(0.2, 0.6), rng.uniform(0.2, 0.6)
        d = gait.v_norm(tuple(rng.gauss(0, 1) for _ in range(3)))
        dist = rng.uniform(abs(l1 - l2) + 0.3 * min(l1, l2), 0.97 * (l1 + l2))
        target = gait.v_add(root, gait.v_scale(d, dist))
        pole = gait.v_add(root, tuple(rng.gauss(0, 1) for _ in range(3)))
        mid, end, reached = gait.two_bone_ik(root, target, l1, l2, pole, max_bend_deg=179.0)
        assert reached and gait.v_len(gait.v_sub(end, target)) < 1e-9
        assert gait.v_len(gait.v_sub(mid, root)) == pytest.approx(l1, abs=1e-9)
        assert gait.v_len(gait.v_sub(end, mid)) == pytest.approx(l2, abs=1e-9)
        # The middle joint lies on the pole's side of the root-to-target line.
        axis = gait.v_norm(gait.v_sub(target, root))
        side = lambda p: gait.v_sub(gait.v_sub(p, root), gait.v_scale(axis, gait.v_dot(gait.v_sub(p, root), axis)))  # noqa: E731
        if gait.v_len(side(pole)) > 1e-3:
            assert gait.v_dot(side(mid), side(pole)) > 0


def test_two_bone_ik_clamps_unreachable_and_too_close_targets():
    mid, end, reached = gait.two_bone_ik((0, 0, 1), (0, 0, -5), 0.5, 0.5, (0, -1, 0.5))
    assert not reached and end == pytest.approx((0, 0, 0)) and gait.bend_deg((0, 0, 1), mid, end) == pytest.approx(0, abs=1e-6)
    mid, end, reached = gait.two_bone_ik((0, 0, 1), (0, 0, 1.0001), 0.5, 0.5, (0, -1, 0.5), max_bend_deg=150)
    assert not reached and gait.bend_deg((0, 0, 1), mid, end) == pytest.approx(150, abs=1e-6)
    mid, end, _ = gait.two_bone_ik((0, 0, 1), (0, 0.2, 0.3), 0.45, 0.45, (0, -1, 0.6), min_bend_deg=20)
    assert gait.bend_deg((0, 0, 1), mid, end) >= 20 - 1e-6


def test_ground_target_rests_on_the_ground_within_reach_and_beside_the_body():
    shoulder, hand = (0.2, 0.0, 0.25), (0.15, 0.3, 0.6)  # hand up in the air over the chest
    t = gait.ground_target(shoulder, hand, 0.03, 0.6, outward=(1, 0, 0), min_out=0.2)
    assert t[2] == 0.03
    assert t[0] - shoulder[0] >= 0.2 - 1e-9 or math.hypot(t[0] - shoulder[0], t[1] - shoulder[1]) == pytest.approx(
        math.sqrt(0.6 ** 2 - (0.03 - 0.25) ** 2))
    assert math.dist(shoulder, t) <= 0.6 + 1e-9
    # A limb end that already lies far out is pulled in until the root can reach it.
    far = gait.ground_target((0, 0, 0.3), (2.0, 0, 0.3), 0.0, 0.5)
    assert math.dist((0, 0, 0.3), far) == pytest.approx(0.5)


# ---- planted feet --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run,duty", [(False, 0.6), (True, 0.4)])
def test_foot_path_is_planted_at_constant_speed_and_periodic(run, duty):
    stride, lift = 0.6, 0.3 if run else 0.1
    n = 1000
    pts = [gait.foot_path(i / n, duty, stride, lift, run=run) for i in range(n + 1)]
    assert pts[0] == pytest.approx(pts[-1], abs=1e-6)
    stance = [p for i, p in enumerate(pts[:-1]) if i / n < duty]
    assert all(p[1] == 0.0 for p in stance)
    speeds = {round((b[0] - a[0]) * n, 6) for a, b in zip(stance, stance[1:])}
    assert len(speeds) == 1 and next(iter(speeds)) == pytest.approx(-stride / duty, rel=1e-6)
    assert all(p[1] >= 0 for p in pts)
    for a, b in zip(pts, pts[1:]):
        assert abs(b[0] - a[0]) < 0.02 and abs(b[1] - a[1]) < 0.02 and abs(b[2] - a[2]) < 1.0
    peak = max(p[1] for p in pts)
    assert peak == pytest.approx(lift, rel=0.25)


# ---- retarget in Blender on a synthetic Mixamo skeleton -------------------------------------------------------

_BUILD = r'''
import bpy, sys
out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
J = {"Hips": (0, 0, 1.0), "Spine": (0, 0, 1.1), "Spine1": (0, 0, 1.2), "Spine2": (0, 0, 1.3), "Neck": (0, 0, 1.45),
     "Head": (0, 0, 1.55), "HeadTop": (0, 0, 1.75)}
P = {"Spine": "Hips", "Spine1": "Spine", "Spine2": "Spine1", "Neck": "Spine2", "Head": "Neck"}
for s, x in (("Left", 1), ("Right", -1)):
    J.update({s + "Shoulder": (0.05 * x, 0, 1.42), s + "Arm": (0.18 * x, 0, 1.40), s + "ForeArm": (0.27 * x, 0, 1.16),
              s + "Hand": (0.33 * x, 0, 0.93), s + "HandIndex1": (0.35 * x, -0.02, 0.84),
              s + "UpLeg": (0.1 * x, 0, 0.95), s + "Leg": (0.11 * x, 0, 0.52), s + "Foot": (0.11 * x, 0.02, 0.1),
              s + "ToeBase": (0.11 * x, -0.12, 0.02)})
    P.update({s + "Shoulder": "Spine2", s + "Arm": s + "Shoulder", s + "ForeArm": s + "Arm", s + "Hand": s + "ForeArm",
              s + "HandIndex1": s + "Hand", s + "UpLeg": "Hips", s + "Leg": s + "UpLeg", s + "Foot": s + "Leg",
              s + "ToeBase": s + "Foot"})
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
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 1.0))
cube = bpy.context.active_object
cube.parent = ob
mod = cube.modifiers.new("arm", "ARMATURE")
mod.object = ob
g = cube.vertex_groups.new(name="mixamorig:Hips")
g.add([v.index for v in cube.data.vertices], 1.0, "REPLACE")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
print("BUILD_OK")
'''


@pytest.mark.skipif(not (BLENDER.is_file() and UAL1_GLB.is_file()), reason="needs Blender and the UAL1 clip library")
def test_retarget_runs_on_a_synthetic_mixamo_skeleton(tmp_path):
    build = tmp_path / "build.py"
    build.write_text(_BUILD, encoding="utf-8")
    src = tmp_path / "synthetic.glb"
    subprocess.run([str(BLENDER), "-b", "--python", str(build), "--", str(src)], check=True, capture_output=True, timeout=300)
    assert src.is_file()
    out, report = tmp_path / "anim.glb", tmp_path / "anim_retarget.json"
    script = Path(rig.__file__).resolve().parents[1] / "blender" / "retarget.py"
    subprocess.run([str(BLENDER), "-b", "--python", str(script), "--", str(src), str(out), "--library", str(UAL1_GLB),
                    "--clips", "idle=Idle_Loop,walk=Walk_Loop,death=Death01,nothing=Not_A_Clip", "--loops", "idle,walk",
                    "--report", str(report)], check=True, capture_output=True, timeout=600)
    rep = json.loads(report.read_text())
    assert rep["ok"] and rep["library_clips"] == ["idle", "walk", "death"] and rep["missing_clips"] == ["nothing"]
    assert "LeftHandMiddle1" not in rep["bone_map"] and "LeftHandIndex1" in rep["bone_map"]
    for clip, det in rep["detail"].items():
        # Standing clips move the arms by the compensation alone: their segments point exactly where the library's
        # do. In the death the hands rest on the ground, so the arm IK may move them a little.
        for bone in ("LeftArm", "LeftForeArm", "RightArm", "RightForeArm"):
            assert det["segment_angle"][bone]["max_deg"] < (1.0 if clip != "death" else 45.0), (clip, bone)
        # Standing clips: the legs follow within the few degrees the foot clean-up needs. Lying down, the body is
        # lifted off the ground where its torso would sink, and the planted feet are reached again by the leg IK.
        for bone in ("LeftUpLeg", "LeftLeg", "RightUpLeg", "RightLeg"):
            assert det["segment_angle"][bone]["max_deg"] < (20.0 if clip != "death" else 90.0), (clip, bone)
        if clip != "death":
            assert det["inverted_knee_frames"] == 0
    side = json.loads((tmp_path / "anim_clips.json").read_text())
    rows = {c["name"]: c for c in side["clips"]}
    assert set(rows) == {"idle", "walk", "death"} and all(c["source"] == "library" for c in rows.values())
    assert rows["walk"]["loop"] and not rows["death"]["loop"] and rows["walk"]["seconds"] > 0.5
    assert out.is_file() and out.stat().st_size > 1000


# ---- rig stage wiring ------------------------------------------------------------------------------------------

def _lib_dir(tmp_path):
    root = tmp_path / "ual1" / "Animation Library[Standard]"
    (root / "Unity").mkdir(parents=True)
    (root / "Godot").mkdir()
    (root / "License.txt").write_text("CC0")
    fbx = root / "Unity" / "lib.fbx"
    fbx.write_bytes(b"fbx")
    (root / "Godot" / "lib.glb").write_bytes(b"glb")
    return fbx


class FakeSC:
    def __init__(self, tmp_path, category, libs, fail_retarget=False):
        self.dir = tmp_path / "ver"
        self.dir.mkdir()
        self.category = category
        self.asset = SimpleNamespace(category=category.get("id", "humanoid"))
        self.settings = SimpleNamespace(clips_libraries=libs, blender_timeout_seconds=60)
        self.version = SimpleNamespace(metrics={}, params={})
        self.files, self.calls = {}, []
        self.fail_retarget = fail_retarget

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    def register(self, key, path):
        self.files[key] = Path(path)

    def file(self, key):
        return self.files.get(key)

    def progress(self, *a, **k):
        pass

    async def blender(self, script, args, log_name=None, timeout=None):
        self.calls.append((script, [str(a) for a in args]))
        if script == "procanim.py":
            Path(args[1]).write_bytes(b"glb")
            (self.dir / "anim_anim.json").write_text(json.dumps({"kind": "biped", "motion": "biped", "clips": ["idle", "walk", "turn_left", "death"],
                                                                 "missing_clips": []}))
            (self.dir / "anim_clips.json").write_text(json.dumps({"clips": []}))
        elif script == "retarget.py":
            rep = Path(args[args.index("--report") + 1])
            if self.fail_retarget:
                rep.write_text(json.dumps({"ok": False, "error": "bones not found on the character or in the library: ['Head']"}))
                raise RuntimeError("blender exited 2")
            spec = gait.parse_clip_spec(args[args.index("--clips") + 1])
            lib = [n for n, _ in spec if n != "roll"]
            rep.write_text(json.dumps({"ok": True, "clips": lib + ["turn_left"], "library_clips": lib, "procedural_clips": ["turn_left"],
                                       "missing_clips": ["roll"], "leg_ratio": 1.04, "missing_bones": [], "seconds": 3.6,
                                       "detail": {"walk": {"segment_angle": {"LeftArm": {"max_deg": 0.0}, "LeftUpLeg": {"max_deg": 5.5}}}}}))
        elif script == "render_anim.py":
            d = Path(args[1])
            d.mkdir(parents=True, exist_ok=True)
            (d / "frames.json").write_text(json.dumps({"clips": {}, "frames": 6}))
        return ""


HUMANOID = {"id": "humanoid", "rig_engine": "skintoken_mixamo", "motion": "legs", "clips": ["idle", "walk", "turn_left", "death"],
            "library_clips": [{"name": "idle", "from": ["Idle_Loop"], "move": "idle", "loop": True},
                              {"name": "walk", "from": ["Walk_Loop"], "move": "walk", "loop": True},
                              {"name": "death", "from": ["Death01"], "move": "death", "loop": False},
                              {"name": "roll", "from": ["Roll"], "move": "roll", "loop": False}]}


def test_library_file_prefers_the_gltf_copy_of_the_download(tmp_path):
    fbx = _lib_dir(tmp_path)
    assert rig.library_file(fbx).name == "lib.glb"
    assert rig.library_file(tmp_path / "missing" / "x.fbx") is None
    assert rig.library_file(None) is None


def test_library_rows_follow_the_moves_knob():
    assert [r["name"] for r in rig.library_rows(HUMANOID, None)] == ["idle", "walk", "death", "roll"]
    assert [r["name"] for r in rig.library_rows(HUMANOID, ["walk", "death", "turn"])] == ["walk", "death"]
    assert rig.clips_source({}) == "auto" and rig.clips_source({"clips_source": "Library"}) == "library"
    assert rig.clips_source({"clips_source": "nonsense"}) == "auto"


def test_animate_retargets_library_clips_for_a_humanoid(tmp_path):
    sc = FakeSC(tmp_path, HUMANOID, {"ual1": _lib_dir(tmp_path)})
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {}))
    scripts = [c[0] for c in sc.calls]
    assert scripts == ["procanim.py", "retarget.py", "render_anim.py"]
    args = sc.calls[1][1]
    assert args[0] == args[1] == str(sc.dir / "anim.glb")
    assert args[args.index("--library") + 1].endswith("lib.glb")
    assert args[args.index("--loops") + 1] == "idle,walk"
    m = sc.version.metrics
    assert m["clips_source"] == "library" and m["library_clips"] == ["idle", "walk", "death"]
    assert m["procedural_clips"] == ["turn_left"] and m["missing_clips"] == ["roll"]
    assert m["retarget_seconds"] >= 0 and m["retarget"]["faithful_max_deg"] == 5.5 and m["retarget_error"] is None
    assert sc.version.params["clips_source"] == "auto" and sc.files["anim_retarget"].name == "anim_retarget.json"


def test_animate_keeps_the_procedural_clips_when_asked_or_when_the_retarget_fails(tmp_path):
    sc = FakeSC(tmp_path, HUMANOID, {"ual1": _lib_dir(tmp_path)})
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {"clips_source": "procedural"}))
    assert [c[0] for c in sc.calls] == ["procanim.py", "render_anim.py"]
    assert sc.version.metrics["clips_source"] == "procedural" and sc.version.metrics["library_skipped"] == "built-in moves chosen"

    (tmp_path / "b").mkdir()
    sc2 = FakeSC(tmp_path / "b", HUMANOID, {"ual1": _lib_dir(tmp_path / "b")}, fail_retarget=True)
    asyncio.run(rig._animate(sc2, tmp_path / "rig.glb", {"clips_source": "library"}))
    m = sc2.version.metrics
    assert m["clips_source"] == "procedural" and m["clips"] == ["idle", "walk", "turn_left", "death"]
    assert "bones not found" in m["retarget_error"] and m["library_clips"] == []


def test_animate_leaves_non_humanoids_and_missing_libraries_to_the_procedural_clips(tmp_path):
    quad = {"id": "quadruped", "rig_engine": "skintoken", "motion": "legs", "clips": ["idle", "walk"]}
    sc = FakeSC(tmp_path, quad, {"ual1": _lib_dir(tmp_path)})
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {}))
    assert "retarget.py" not in [c[0] for c in sc.calls] and sc.version.metrics["clips_source"] == "procedural"
    (tmp_path / "h").mkdir()
    sc2 = FakeSC(tmp_path / "h", HUMANOID, {})
    asyncio.run(rig._animate(sc2, tmp_path / "rig.glb", {}))
    assert "retarget.py" not in [c[0] for c in sc2.calls] and "not found" in sc2.version.metrics["library_skipped"]


def test_judge_flags_a_failed_library_retarget(tmp_path, monkeypatch):
    monkeypatch.setattr(rig.quality, "rig_checks", lambda *a, **k: [])
    sc = FakeSC(tmp_path, HUMANOID, {})
    sc.version.metrics.update(clips=["idle"], missing_clips=[], anim_glb=True, library_clips=[], library_skipped=None,
                              retarget_error="bones not found")
    asyncio.run(rig.judge(sc))
    checks = {c["id"]: c for c in sc.version.metrics["checks"]}
    assert checks["library_clips"]["ok"] is False and checks["clips"]["ok"] is True
    sc.version.metrics.update(library_clips=["idle", "walk"], retarget_error=None)
    asyncio.run(rig.judge(sc))
    assert {c["id"]: c for c in sc.version.metrics["checks"]}["library_clips"]["ok"] is True
    sc.version.metrics.update(library_clips=[], library_skipped="built-in moves chosen")
    asyncio.run(rig.judge(sc))
    assert "library_clips" not in {c["id"] for c in sc.version.metrics["checks"]}
