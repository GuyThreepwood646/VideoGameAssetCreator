"""blender/finger_rigid.py: rigid finger segments (straight chains, joints at the anatomical fractions, each segment's skin on its own bone with a
short blend, the first bone stopping at the knuckle) and the rig step's call."""
from __future__ import annotations

import asyncio
import json
import subprocess
import textwrap

import numpy as np
import pytest

import machine_tools
from vgac.blender import finger_rigid as fr
from vgac.stages import rig

BLENDER = machine_tools.blender()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not set up on this machine")


# --- numpy parts -----------------------------------------------------------------------------------------------


def test_joints_sit_at_the_anatomical_fractions_on_a_straight_line():
    pts = fr.joint_points((0, 0, 0), (0.10, 0, 0), fr.FRACTIONS)
    heads = [h[0] for h, _ in pts]
    assert [round(x, 3) for x in heads] == [0.0, 0.045, 0.075] and abs(pts[2][1][0] - 0.10) < 1e-9
    assert all(np.allclose(pts[i][1], pts[i + 1][0]) for i in range(2))  # the chain is joined end to end
    thumb = fr.joint_points((0, 0, 0), (0.10, 0, 0), fr.THUMB_FRACTIONS)
    assert [round(h[0], 3) for h, _ in thumb] == [0.0, 0.04, 0.073]


def test_each_segment_is_on_its_own_bone_with_a_short_blend_at_each_joint():
    band = fr.BAND_MM / 1000
    j1, j2 = 0.045, 0.075
    for t in np.linspace(-0.02, 0.14, 81):
        sh = fr.segment_shares(t, j1, j2, band)
        assert abs(sum(sh) - 1) < 1e-9 and all(x >= -1e-12 for x in sh)
    # well inside a segment the whole share is on that segment's bone, and the blend is over by half a band either side of the joint
    assert fr.segment_shares(0.02, j1, j2, band)[0] == pytest.approx(1.0)
    assert fr.segment_shares(0.06, j1, j2, band)[1] == pytest.approx(1.0)
    assert fr.segment_shares(0.10, j1, j2, band)[2] == pytest.approx(1.0)
    assert fr.segment_shares(j1 - band / 2 - 1e-6, j1, j2, band)[0] == pytest.approx(1.0)
    assert fr.segment_shares(j1 + band / 2 + 1e-6, j1, j2, band)[1] == pytest.approx(1.0)
    # the blend is a few millimetres, not a ramp across the segment (a segment is about 3 cm)
    assert band <= 0.006 + 1e-12 and fr.BAND_MM >= 6.0  # 6 mm and not less: at 4 mm the M1 hand tore at a 40 degree curl


def test_the_first_bone_stops_at_the_knuckle():
    k = fr.KNUCKLE_MM / 1000
    assert fr.knuckle_share(-0.02, k) == 0.0 and fr.knuckle_share(0.02, k) == 1.0
    assert 0.0 < fr.knuckle_share(0.0, k) < 1.0


def test_new_weights_move_skin_only_between_a_fingers_own_bones_and_the_palm():
    ids, hand, other = (10, 11, 12), 1, 30  # the finger's three bones, the palm bone, another finger's bone
    ws = {10: 0.5, 11: 0.2, other: 0.2, 5: 0.1}
    for t in (-0.03, -0.002, 0.0, 0.02, 0.046, 0.06, 0.12):
        new = fr.new_weights(ws, ids, hand, t, 0.045, 0.075, 0.006, 0.006)
        assert set(new) <= set(ids) | {hand, other, 5}
        assert abs(sum(new.values()) - 1) < 1e-9 and len(new) <= 4
    # behind the knuckle all the finger's mass goes to the palm bone; the other finger's share is unchanged
    behind = fr.new_weights(ws, ids, hand, -0.03, 0.045, 0.075, 0.006, 0.006)
    assert behind[hand] == pytest.approx(0.7) and behind[other] == pytest.approx(0.2) and not any(behind.get(i, 0) for i in ids)


def test_four_influences_at_most_and_normalised_when_the_palm_is_added():
    ws = {10: 0.3, 11: 0.1, 1: 0.2, 20: 0.2, 21: 0.2}
    new = fr.new_weights(ws, (10, 11, 12), 1, -0.001, 0.045, 0.075, 0.006, 0.006)
    assert len(new) <= 4 and abs(sum(new.values()) - 1) < 1e-9


# --- the rig step's call -----------------------------------------------------------------------------------------


def test_finger_rigid_runs_after_the_finger_build_and_before_the_palm_turn_and_everything_that_reads_the_skeleton():
    import inspect

    src = inspect.getsource(rig._rig)
    at = src.index("_finger_rigid(sc, rig_glb, params)")
    assert src.index("_fingers(sc, rig_glb, params)") < at < src.index("_palm_down(sc, rig_glb, params)")
    for later in ("sc.register(\"glb\"", "_inspect(sc", "fit_hitboxes(sc", "_animate(sc"):
        assert at < src.index(later), later


class _FakeSC:
    def __init__(self, tmp_path, report):
        self.dir, self.report = tmp_path, report
        self.metrics, self.params, self.registered = {}, {}, {}
        self.settings = type("S", (), {"blender_timeout_seconds": 5})()

    async def blender(self, script, args, log_name=None, timeout=None):
        assert script == "finger_rigid.py"
        out, rep = args[1], args[args.index("--report") + 1]
        out.write_bytes(b"NEW")
        rep.write_text(json.dumps(self.report), encoding="utf-8")

    def set_metrics(self, **kw):
        self.metrics.update(kw)

    def set_params(self, **kw):
        self.params.update(kw)

    def register(self, key, path):
        self.registered[key] = path


def _step(tmp_path, report, params=None):
    glb = tmp_path / "rig.glb"
    glb.write_bytes(b"OLD")
    sc = _FakeSC(tmp_path, report)
    asyncio.run(rig._finger_rigid(sc, glb, params or {}))
    return sc, glb


def test_the_step_replaces_the_rig_when_a_finger_was_straightened_and_records_it(tmp_path):
    sc, glb = _step(tmp_path, {"Left": {"straightened": ["Thumb", "Index"], "skipped": {"Pinky": "no chain"}, "verts": 900}, "Right": {"straightened": [], "skipped": {}, "verts": 0}})
    assert glb.read_bytes() == b"NEW" and (tmp_path / "rig_before_finger_rigid.glb").read_bytes() == b"OLD"
    assert sc.metrics["finger_rigid"]["Left"]["straightened"] == ["Thumb", "Index"] and "finger_rigid" in sc.registered


def test_nothing_straightened_or_the_setting_off_leaves_the_rig_exactly_as_it_was(tmp_path):
    sc, glb = _step(tmp_path, {"Left": {"straightened": [], "skipped": {}, "verts": 0}, "Right": {"straightened": [], "skipped": {}, "verts": 0}})
    assert glb.read_bytes() == b"OLD"
    sc2, glb2 = _step(tmp_path, {"Left": {"straightened": ["Thumb"]}}, {"finger_rigid": False})
    assert glb2.read_bytes() == b"OLD" and "finger_rigid" not in sc2.metrics and sc2.params["finger_rigid"] is False


# --- Blender: a palm-forward hand whose fingers carry their own skin ------------------------------------------------


def _scene():
    import test_palm_down as tpd

    src = tpd.SCENE
    # the finger boxes carry the skin of their own chain, bone by bone along the finger, like SkinToken's weights
    old = '    else: grp = "mixamorig:LeftHand"'
    assert old in src
    src = src.replace(old, '''    elif v.co.x > 0.09:
        fname = {{0.03: "Index", 0.01: "Middle", -0.01: "Ring", -0.03: "Pinky"}}[min((0.03, 0.01, -0.01, -0.03), key=lambda z: abs(z - v.co.z))]
        ln = {{"Index": 0.17, "Middle": 0.19, "Ring": 0.18, "Pinky": 0.15}}[fname]
        k = min(2, int((v.co.x - 0.09) / max(ln - 0.09, 1e-6) * 3))
        grp = "mixamorig:LeftHand%s%d" % (fname, k + 1)
    else: grp = "mixamorig:LeftHand"''')
    return tpd, src


MEASURE = textwrap.dedent('''
    import bpy, json, sys, math, os
    import numpy as np
    from mathutils import Vector, Quaternion
    sys.path.insert(0, {blender_dir!r})
    import _common
    _common.open_glb({glb!r})
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    mesh = max((o for o in bpy.context.scene.objects if o.type == "MESH"), key=lambda o: len(o.data.vertices))
    P = "mixamorig:LeftHand"
    gname = {{g.index: g.name for g in mesh.vertex_groups}}
    def finger(n):
        for f in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
            if n.startswith(P + f): return f
        return None
    out = {{"weights": {{}}, "max_infl": 0, "sum_min": 9, "sum_max": 0, "chains": {{}}}}
    for v in mesh.data.vertices:
        ws = {{gname[g.group]: g.weight for g in v.groups if g.weight > 0}}
        key = "%.3f,%.3f,%.3f" % tuple(round(c, 3) + 0.0 for c in v.co)  # vertices are matched by position: the glTF round trip splits and renumbers them
        out["weights"][key] = sorted(set(out["weights"].get(key, [])) | {{finger(n) for n in ws if finger(n)}})
        out["max_infl"] = max(out["max_infl"], len(ws)); t = sum(ws.values()); out["sum_min"] = min(out["sum_min"], t); out["sum_max"] = max(out["sum_max"], t)
    for f in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
        bs = [arm.data.bones.get(P + f + str(i)) for i in (1, 2, 3)]
        if all(bs):
            # glTF stores joints, not bone tails, so the last bone's end is made up on import: only the three heads are compared
            h = [np.array(b.head_local) for b in bs]
            line = h[2] - h[0]; line /= np.linalg.norm(line)
            off = float(np.linalg.norm((h[1] - h[0]) - ((h[1] - h[0]) @ line) * line))
            out["chains"][f] = {{"off_line_mm": off * 1000, "ratio": float(np.linalg.norm(h[1] - h[0]) / np.linalg.norm(h[2] - h[1]))}}
    # curl every finger joint 40 degrees toward the palm (the palm faces -y here: rotate about -z) and look for torn or collapsed skin
    AW = arm.matrix_world
    def coords():
        ev = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh()
        c = np.array([np.array(v.co) for v in m.vertices]); ev.to_mesh_clear(); return c
    for b in arm.pose.bones: b.rotation_mode = "QUATERNION"
    bpy.context.view_layer.update(); rest = coords()
    for f in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
        for i in (1, 2, 3):
            nm = P + f + str(i)
            if nm in arm.pose.bones:
                ax = (AW.to_3x3() @ arm.data.bones[nm].matrix_local.to_3x3()).inverted() @ Vector((0, 0, -1))
                arm.pose.bones[nm].rotation_quaternion = Quaternion(ax.normalized(), math.radians(40))
    bpy.context.view_layer.update(); cur = coords()
    hand = [v.index for v in mesh.data.vertices if any(finger(gname[g.group]) for g in v.groups)]
    hs = set(hand)
    stretch = [np.linalg.norm(cur[e.vertices[0]] - cur[e.vertices[1]]) / max(np.linalg.norm(rest[e.vertices[0]] - rest[e.vertices[1]]), 1e-6)
               for e in mesh.data.edges if e.vertices[0] in hs and e.vertices[1] in hs]
    def area(c, vs): return 0.5 * np.linalg.norm(np.cross(c[vs[1]] - c[vs[0]], c[vs[2]] - c[vs[0]]))
    tris = [p.vertices for p in mesh.data.polygons if len(p.vertices) == 3 and all(v in hs for v in p.vertices)]
    ratio = [area(cur, t) / max(area(rest, t), 1e-12) for t in tris]
    out["curl"] = {{"max_stretch": float(max(stretch)), "collapsed": int(sum(1 for r in ratio if r < 0.02)), "faces": len(ratio)}}
    open({out!r}, "w").write(json.dumps(out))
''')


def _blender(tmp_path, text, name, *args):
    script = tmp_path / name
    script.write_text(text, encoding="utf-8")
    subprocess.run([str(BLENDER), "-b", "--threads", "2", "--python", str(script), "--", *map(str, args)], check=True, capture_output=True, timeout=300)


def _measure(tmp_path, glb, tag):
    out = tmp_path / f"m_{tag}.json"
    _blender(tmp_path, MEASURE.format(blender_dir=str(fr.__file__).rsplit("finger_rigid.py", 1)[0], glb=str(glb), out=str(out)), f"measure_{tag}.py")
    return json.loads(out.read_text())


@needs_blender
def test_a_hand_with_its_own_finger_skin_is_made_rigid_without_moving_skin_between_fingers(tmp_path):
    tpd, scene = _scene()
    src = tmp_path / "hand.glb"
    _blender(tmp_path, scene.format(out=str(src)), "scene.py")
    before = _measure(tmp_path, src, "before")
    out, rep = tmp_path / "rigid.glb", tmp_path / "rigid.json"
    subprocess.run([str(BLENDER), "-b", "--threads", "2", "--python", str(fr.__file__), "--", str(src), str(out), "--report", str(rep)], check=True, capture_output=True, timeout=300)
    r = json.loads(rep.read_text())
    assert sorted(r["Left"]["straightened"]) == ["Index", "Middle", "Pinky", "Ring"] or len(r["Left"]["straightened"]) >= 4
    after = _measure(tmp_path, out, "after")
    # no vertex gains weight on a different finger than it had
    assert len(after["weights"]) > 500
    for key, fingers in after["weights"].items():
        near = [k for k in before["weights"] if all(abs(float(a) - float(b)) < 0.0015 for a, b in zip(k.split(","), key.split(",")))]
        assert near and set(fingers) <= set().union(*[set(before["weights"][k]) for k in near]), (key, fingers)
    assert after["max_infl"] <= 4 and abs(after["sum_min"] - 1) < 1e-3 and abs(after["sum_max"] - 1) < 1e-3
    # every chain is straight with its first two bones in the ratio of the anatomical fractions (0.45 to 0.30; thumb 0.40 to 0.33)
    for f, c in after["chains"].items():
        assert c["off_line_mm"] < 0.5, (f, c)
        want = 0.40 / 0.33 if f == "Thumb" else 0.45 / 0.30  # first bone over second bone
        assert abs(c["ratio"] - want) / want < 0.03, (f, c)
    # a 40 degree curl tears nothing (the M1 hand tore at a narrower blend)
    assert after["curl"]["collapsed"] == 0 and after["curl"]["max_stretch"] < 6.0, after["curl"]
