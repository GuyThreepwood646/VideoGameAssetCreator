"""Rig body plan (WP-19, PREREG in bakeoff/rigdetect/PREREG.md): procanim counts legs by bone geometry and weights.

Planted case 1: a low pole bone with no vertex weights, far from the ankles, is a helper, not a leg: the rig stays a
two-legged biped. Planted case 2: a real extra leg pair (weighted geometry, reaching the ground) adds two legs.
The planted rigs are built from the WP-72 Knight_Male reference (set VGAC_WP72_KNIGHT to its .gltf path). One Blender
run per rig; skipped without Blender or without the reference file. Scratch files go under pytest's --basetemp.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.machine_tools import blender as machine_blender
from tests.test_arm_pose import _BUILD

ROOT = Path(__file__).resolve().parents[2]
BLENDER = machine_blender() or Path("blender-not-found")
PROCANIM = ROOT / "backend" / "vgac" / "blender" / "procanim.py"
MAKE_PLANTED = ROOT / "bakeoff" / "rigdetect" / "make_planted.py"
KNIGHT = Path(os.environ.get("VGAC_WP72_KNIGHT", "")) if os.environ.get("VGAC_WP72_KNIGHT") else None

needs = pytest.mark.skipif(
    not (BLENDER.is_file() and PROCANIM.is_file() and MAKE_PLANTED.is_file() and KNIGHT and KNIGHT.is_file()),
    reason="needs Blender (VGAC_TEST_BLENDER) and the WP-72 Knight_Male reference (VGAC_WP72_KNIGHT)")


def _env() -> dict:
    env = dict(os.environ)
    env["VGAC_REPO"] = str(ROOT)
    env["OMP_NUM_THREADS"] = "2"
    return env


def _planted(tmp_path: Path) -> Path:
    out = tmp_path / "planted"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(MAKE_PLANTED), "--", str(KNIGHT), str(out)],
                   check=True, capture_output=True, timeout=300, env=_env())
    return out


def _read(tmp_path: Path, rig: Path, tag: str) -> dict:
    out = tmp_path / f"{tag}.glb"
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(PROCANIM), "--", str(rig), str(out), "24",
                    "--clips", "idle"], check=True, capture_output=True, timeout=600, env=_env())
    return json.loads((tmp_path / f"{tag}_anim.json").read_text(encoding="utf-8"))


@needs
def test_planted_unweighted_pole_bone_is_not_a_leg(tmp_path):
    rig = _planted(tmp_path) / "planted_pole.glb"
    r = _read(tmp_path, rig, "pole")
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2
    assert "PoleLow" not in [leg["leaf"] for leg in r["legs"]]


@needs
def test_planted_real_extra_leg_pair_adds_two_legs(tmp_path):
    rig = _planted(tmp_path) / "planted_legs.glb"
    r = _read(tmp_path, rig, "legs")
    assert r["kind"] == "quadruped"
    assert len(r["legs"]) == 4
    assert {"ExtraLowerLeg.L", "ExtraLowerLeg.R"} <= {leg["leaf"] for leg in r["legs"]}


@needs
def test_planted_weighted_leg_with_unweighted_toe_leaves_is_still_a_leg(tmp_path):
    rig = _planted(tmp_path) / "planted_toes.glb"
    r = _read(tmp_path, rig, "toes")
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2
    assert {"Toe_End.L", "Toe_End.R"} == {leg["leaf"] for leg in r["legs"]}


@needs
def test_wp72_knight_male_reads_as_a_biped_with_two_legs(tmp_path):
    r = _read(tmp_path, KNIGHT, "knight")
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2


# ---- self-contained planted cases (no env var, no outside file) -------------------------------------------------------
# The synthetic arm rig (tests/test_arm_pose.py _BUILD) is the base: a cube body spanning the figure, weighted feet
# whose toe leaves carry no weights. The planted bones are injected into its build script, as test_arm_chain_end.py does.

_EDIT_MARK = 'bpy.ops.object.mode_set(mode="OBJECT")\n# A body box'
_EXPORT_MARK = 'bpy.ops.object.select_all(action="SELECT")'
assert _BUILD.count(_EDIT_MARK) == 1 and _BUILD.count(_EXPORT_MARK) == 1

# Planted case 1: an unweighted pole bone hung off the root (Hips), far from the feet.
_POLE_EDIT = '''
p_ = ad.edit_bones.new("mixamorig:PoleLow")
p_.head = (0.4, -0.6, 0.25)
p_.tail = (0.4, -0.6, 0.5)
p_.parent = eb["Hips"]
'''

# Planted case 2: a weighted extra leg pair off the Hips: the thigh bones in edit mode, one weighted cube per shin
# (its vertex group is the shin bone, as the feet cubes are) added in object mode.
_LEGS_EDIT = '''
for s_, x_ in (("L", 1), ("R", -1)):
    up_ = ad.edit_bones.new("mixamorig:ExtraUpLeg." + s_)
    up_.head = (0.1 * x_, 0.0, 0.95)
    up_.tail = (0.4 * x_, 0.0, 0.5)
    up_.parent = eb["Hips"]
    lo_ = ad.edit_bones.new("mixamorig:ExtraLeg." + s_)
    lo_.head = (0.4 * x_, 0.0, 0.5)
    lo_.tail = (0.4 * x_, 0.0, 0.05)
    lo_.parent = up_
'''
_LEGS_OBJ = '''
for s_, x_ in (("L", 1), ("R", -1)):
    bpy.ops.mesh.primitive_cube_add(size=0.08, location=(0.4 * x_, -0.04, 0.04))
    lc_ = bpy.context.active_object
    lc_.parent = ob
    lm_ = lc_.modifiers.new("arm", "ARMATURE")
    lm_.object = ob
    lg_ = lc_.vertex_groups.new(name="mixamorig:ExtraLeg." + s_)
    lg_.add([v.index for v in lc_.data.vertices], 1.0, "REPLACE")
'''


def _variant(edit_extra: str = "", obj_extra: str = "") -> str:
    src = _BUILD
    if edit_extra:
        src = src.replace(_EDIT_MARK, edit_extra + _EDIT_MARK, 1)
    if obj_extra:
        src = src.replace(_EXPORT_MARK, obj_extra + _EXPORT_MARK, 1)
    return src


def _self_read(tmp_path: Path, tag: str, script: str) -> dict:
    build = tmp_path / f"{tag}_build.py"
    build.write_text(script, encoding="utf-8")
    rig = tmp_path / f"{tag}.glb"
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(build), "--", str(rig), "102"],
                   check=True, capture_output=True, timeout=300, env=_env())
    assert rig.is_file()
    return _read(tmp_path, rig, tag)


_needs_blender = pytest.mark.skipif(not (BLENDER.is_file() and PROCANIM.is_file()),
                                    reason="needs Blender (VGAC_TEST_BLENDER)")


@_needs_blender
def test_self_contained_planted_pole_bone_is_not_a_leg(tmp_path):
    r = _self_read(tmp_path, "pole", _variant(edit_extra=_POLE_EDIT))
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2
    assert "mixamorig:PoleLow" not in [leg["leaf"] for leg in r["legs"]]


@_needs_blender
def test_self_contained_weighted_extra_leg_pair_adds_two_legs(tmp_path):
    r = _self_read(tmp_path, "legs", _variant(edit_extra=_LEGS_EDIT, obj_extra=_LEGS_OBJ))
    assert r["kind"] == "quadruped"
    assert len(r["legs"]) == 4
    assert {"mixamorig:ExtraLeg.L", "mixamorig:ExtraLeg.R"} <= {leg["leaf"] for leg in r["legs"]}


@_needs_blender
def test_self_contained_weighted_leg_with_unweighted_toe_leaves_is_still_a_leg(tmp_path):
    r = _self_read(tmp_path, "toes", _variant())
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2
    assert {"mixamorig:LeftToeBase", "mixamorig:RightToeBase"} == {leg["leaf"] for leg in r["legs"]}
