"""blender/sleeve_skin.py (WP-79): the sleeve skin pass, its numpy parts, a small real Blender run, and the rig step's call."""
from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import textwrap

import numpy as np
import pytest

import machine_tools
from vgac.blender import sleeve_skin as sk
from vgac.stages import rig

BLENDER = machine_tools.blender()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not set up on this machine")

NAMES = ["mixamorig:Hips", "mixamorig:LeftUpLeg", "mixamorig:LeftLeg", "mixamorig:LeftArm", "mixamorig:LeftForeArm",
         "mixamorig:Extra_ForeArmTwist_L1", "mixamorig:LeftHand", "mixamorig:LeftHandIndex1", "Extra_16", "mixamorig:Spine"]


def test_bones_are_sorted_into_forearm_arm_and_leg_groups():
    kinds = [sk.bone_kind(n) for n in NAMES]
    assert kinds == [None, "leg", "leg", "arm", "fore", "fore", "fore", "fore", None, None]
    assert sk.bone_kind("mixamorig:RightShoulder") == "arm" and sk.bone_kind("mixamorig:RightToeBase") == "leg"
    assert sk.bone_kind("mixamorig:Spring_hair_1") is None


def test_leg_weight_on_arm_skin_goes_to_its_strongest_arm_bone_and_rows_stay_whole():
    cols = sk.kind_columns(NAMES)
    W = np.zeros((3, len(NAMES)))
    W[0, [4, 1]] = [0.78, 0.22]  # sleeve with a thigh weight: forearm 0.78
    W[1, [1, 0]] = [0.7, 0.3]    # skirt: no arm weight, untouched
    W[2, [3, 4, 2]] = [0.4, 0.3, 0.3]  # arm skin (0.7 arm chain) with calf weight: strongest arm bone is the upper arm
    W2, n = sk.hand_over_leg_weight(W, cols)
    assert n == 2
    assert W2[0, 4] == pytest.approx(1.0) and W2[0, 1] == 0.0
    assert np.allclose(W2[1], W[1])
    assert W2[2, 3] == pytest.approx(0.7) and W2[2, 2] == 0.0
    assert np.allclose(W2.sum(axis=1), W.sum(axis=1))


def test_skirt_skin_gives_back_forearm_weight_it_took_from_a_sleeve_but_upper_arm_weight_stays():
    cols = sk.kind_columns(NAMES)
    W = np.zeros((4, len(NAMES)))
    W[0, [1, 4]] = [0.62, 0.38]    # hem: 62 per cent thigh, 38 per cent forearm -> all thigh
    W[1, [1, 3]] = [0.62, 0.38]    # the same with upper arm weight: arm chain share is 0.38, but only forearm weight moves
    W[2, [9, 4]] = [0.9, 0.1]      # spine skin: no leg weight, left alone
    W[3, [1, 4]] = [0.999, 0.001]  # a trace under FORE_NOISE: left alone
    W2, n = sk.hand_over_fore_weight(W, cols)
    assert n == 1
    assert W2[0, 1] == pytest.approx(1.0) and W2[0, 4] == 0.0
    assert np.allclose(W2[1], W[1]) and np.allclose(W2[2], W[2]) and np.allclose(W2[3], W[3])


def test_shares_and_influence_limit():
    cols = sk.kind_columns(NAMES)
    W = np.zeros((2, len(NAMES)))
    W[0, [4, 3]] = [0.6, 0.4]
    W[1, [1]] = [1.0]
    assert sk.fore_share(W, cols) == pytest.approx([0.6, 0.0])
    assert sk.arm_chain_share(W, cols) == pytest.approx([1.0, 0.0])
    row = sk.limit_influences({0: 0.4, 1: 0.3, 2: 0.2, 3: 0.06, 4: 0.04})
    assert len(row) == 4 and sum(row.values()) == pytest.approx(1.0) and 4 not in row


# --- the script, for real ----------------------------------------------------------------------------------------

_MAKE = textwrap.dedent('''
    import bpy, sys
    out = sys.argv[sys.argv.index("--") + 1]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_d = bpy.data.armatures.new("A"); arm = bpy.data.objects.new("Armature", arm_d); bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm; bpy.ops.object.mode_set(mode="EDIT")
    for name, head, tail in (("mixamorig:LeftForeArm", (0, 0, 1.0), (0, 0, 0.6)), ("mixamorig:LeftUpLeg", (0.1, 0, 0.9), (0.1, 0, 0.5)),
                             ("mixamorig:LeftArm", (0, 0, 1.4), (0, 0, 1.0))):
        b = arm_d.edit_bones.new(name); b.head = head; b.tail = tail
    bpy.ops.object.mode_set(mode="OBJECT")
    # a strip of forearm skin (rows 0 and 1, z 0.70 and 0.71) welded to a strip of thigh skin (row 2, z 0.695)
    xs = [0.0, 0.01, 0.02, 0.03]
    verts = [(x, 0, 0.71) for x in xs] + [(x, 0, 0.70) for x in xs] + [(x, 0, 0.695) for x in xs] + [(x, 0, 0.685) for x in xs]
    faces = []
    for r in range(3):
        for i in range(3):
            a, b, c, d = r * 4 + i, r * 4 + i + 1, (r + 1) * 4 + i, (r + 1) * 4 + i + 1
            faces += [(a, b, c), (b, d, c)]
    me = bpy.data.meshes.new("M"); me.from_pydata(verts, [], faces); me.update()
    o = bpy.data.objects.new("Body", me); bpy.context.collection.objects.link(o); o.parent = arm
    gf = o.vertex_groups.new(name="mixamorig:LeftForeArm"); gl = o.vertex_groups.new(name="mixamorig:LeftUpLeg")
    for i in range(0, 8):
        gf.add([i], 1.0, "REPLACE")
    for i in range(8, 16):
        gl.add([i], 1.0, "REPLACE")
    gl.add([4], 0.3, "REPLACE"); gf.add([4], 0.7, "REPLACE")  # one forearm vertex with thigh weight
    mod = o.modifiers.new("Armature", "ARMATURE"); mod.object = arm
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
''')

_READ = textwrap.dedent('''
    import bpy, sys, json
    sys.path.insert(0, sys.argv[sys.argv.index("--") + 2])
    import _common
    _common.open_glb(sys.argv[sys.argv.index("--") + 1])
    o = next(m for m in bpy.context.scene.objects if m.type == "MESH")
    names = {g.index: g.name for g in o.vertex_groups}
    rows = [{names[g.group].split(":")[-1]: round(g.weight, 3) for g in v.groups} for v in o.data.vertices]
    edges = [[e.vertices[0], e.vertices[1]] for e in o.data.edges]
    print("RESULT" + json.dumps({"rows": rows, "edges": edges, "co": [[round(c, 4) for c in v.co] for v in o.data.vertices]}))
''')


# --- the rig step's call -----------------------------------------------------------------------------------------


def test_the_sleeve_pass_runs_after_the_palm_turn_and_before_the_worn_region_and_the_clean_up_rerun_keeps_it():
    src = inspect.getsource(rig._rig)
    at = src.index("_sleeve_skin(sc, rig_glb)")
    assert src.index("_palm_down(sc, rig_glb, params)") < at < src.index("_worn_region(sc, rig_glb, params)")
    for later in ("sc.register(\"glb\"", "_inspect(sc", "fit_hitboxes(sc", "_animate(sc"):
        assert at < src.index(later), later
    again = inspect.getsource(rig._fix_weights_again)
    assert again.index("shutil.move") < again.index("_sleeve_skin(sc, rig_glb)")


class _FakeSC:
    def __init__(self, tmp_path, report):
        self.dir, self.report = tmp_path, report
        self.metrics = {}
        self.settings = type("S", (), {"blender_timeout_seconds": 5})()

    async def blender(self, script, args, log_name=None, timeout=None):
        assert script == "sleeve_skin.py"
        args[1].write_bytes(b"NEW")
        args[args.index("--report") + 1].write_text(json.dumps(self.report), encoding="utf-8")

    def set_metrics(self, **kw):
        self.metrics.update(kw)


def _step(tmp_path, report):
    glb = tmp_path / "rig.glb"
    glb.write_bytes(b"OLD")
    sc = _FakeSC(tmp_path, report)
    asyncio.run(rig._sleeve_skin(sc, glb))
    return sc, glb


def test_a_changed_rig_replaces_the_old_one_and_the_old_one_is_kept(tmp_path):
    sc, glb = _step(tmp_path, {"ok": True, "changed": 1, "leg_weight_vertices": 400})
    assert glb.read_bytes() == b"NEW" and (tmp_path / "rig_before_sleeve_skin.glb").read_bytes() == b"OLD"
    assert sc.metrics["sleeve_skin"] == {"ok": True, "changed": 1, "leg_weight_vertices": 400}


def test_nothing_changed_or_not_run_leaves_the_rig_as_it_was(tmp_path):
    sc, glb = _step(tmp_path, {"ok": True, "changed": 0, "leg_weight_vertices": 0})
    assert glb.read_bytes() == b"OLD" and not (tmp_path / "rig_before_sleeve_skin.glb").exists() and sc.metrics["sleeve_skin"]["ok"]
    sc2, glb2 = _step(tmp_path, {"ok": False, "reason": "no forearm bones"})
    assert glb2.read_bytes() == b"OLD" and sc2.metrics["sleeve_skin"] == {"ok": False, "reason": "no forearm bones"}


@needs_blender
def test_only_weights_change_and_no_vertex_is_added(tmp_path):
    src, out, rep = tmp_path / "in.glb", tmp_path / "out.glb", tmp_path / "rep.json"
    (tmp_path / "make.py").write_text(_MAKE, encoding="utf-8")
    (tmp_path / "read.py").write_text(_READ, encoding="utf-8")
    run = lambda *a: subprocess.run([str(BLENDER), "-b", "--threads", "2", *a], capture_output=True, text=True, timeout=300)  # noqa: E731
    run("--python", str(tmp_path / "make.py"), "--", str(src))
    run("--python", inspect.getsourcefile(sk), "--", str(src), str(out), "--report", str(rep))
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["ok"] and data["changed"] == 1 and data["leg_weight_vertices"] >= 1
    r = run("--python", str(tmp_path / "read.py"), "--", str(out), str(__import__("pathlib").Path(inspect.getsourcefile(sk)).parent))
    res = json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT"))[len("RESULT"):])
    assert len(res["rows"]) == 16
