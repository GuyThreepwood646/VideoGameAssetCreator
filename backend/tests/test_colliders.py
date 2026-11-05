"""G16 with G35, G17 and G20 part one (docs/GREAT-ASSETS-PLAN.md 3.4, 3.8, 3.13): collision shapes chosen and built in
the backend, the export checks on them, and what the Unity importer is asked to do."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vgac import colliders as C
from vgac.stages import export

REPO = Path(__file__).resolve().parents[2]
IMPORTER = REPO / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"


# --- test shapes (Blender frame, Z up, metres) -------------------------------------------------------------------------

def box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    c = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)
    f = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4], [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2],
                  [1, 3, 7], [1, 7, 5]])
    return lo + c * (hi - lo), f


def join(*parts):
    vs, fs, o = [], [], 0
    for v, f in parts:
        vs.append(v)
        fs.append(f + o)
        o += len(v)
    return np.concatenate(vs), np.concatenate(fs)


def arch():  # 3 m wide, 0.4 m deep, 3 m tall, with a 1.4 by 2.2 m doorway
    return join(box([-1.5, -0.2, 0], [-0.7, 0.2, 3]), box([0.7, -0.2, 0], [1.5, 0.2, 3]), box([-0.7, -0.2, 2.2], [0.7, 0.2, 3]))


def chair():
    legs = [box([x, y, 0], [x + 0.05, y + 0.05, 0.45]) for x in (-0.25, 0.2) for y in (-0.25, 0.2)]
    return join(*legs, box([-0.25, -0.25, 0.45], [0.25, 0.25, 0.5]), box([-0.25, 0.2, 0.5], [0.25, 0.25, 1.0]))


def pyramid():  # tapered sides: rays past the slopes are outside, not openings
    v = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0], [0, 0, 1.5]], float)
    f = np.array([[0, 2, 1], [0, 3, 2], [0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    return v, f


# --- limits and the choice by category ---------------------------------------------------------------------------------

def test_part_caps_by_category_platform_and_override():
    assert C.max_parts("prop_rigid", "pc_mid") == 8
    assert C.max_parts("environment", "high") == 16
    assert C.max_parts("vehicle", "pc_mid") == 6
    assert C.max_parts("prop_rigid", "mobile_low") == 4 and C.max_parts("environment", "mobile_low") == 8
    assert C.max_parts("vehicle", "mobile_low") == 3
    assert C.max_parts("prop_rigid", "pc_mid", 12) == 12 and C.max_parts("prop_rigid", "pc_mid", 99) == 32
    assert C.max_parts("prop_rigid", "pc_mid", "") == 8


def test_modes_normalise():
    assert C.normalise_mode(None) == "auto" and C.normalise_mode("") == "auto"
    assert C.normalise_mode("Decompose") == "decompose" and C.normalise_mode("bogus") == "auto"


def test_auto_by_category():
    solid = {"solid_ratio": 0.9, "hull_openings": {"ok": True}}
    hollow = {"solid_ratio": 0.4, "hull_openings": {"ok": True}}
    blocked = {"solid_ratio": 0.9, "hull_openings": {"ok": False}}
    assert C.choose("environment", "auto", solid) == "mesh"
    assert C.choose("vehicle", "auto", solid) == "decompose"
    assert C.choose("foliage", "auto", None) == "trunk"
    for cat in C.RIGGED:
        assert C.choose(cat, "auto", None) == "hull"
    assert C.choose("prop_rigid", "auto", solid) == "primitive"
    assert C.choose("prop_rigid", "auto", hollow) == "decompose"
    assert C.choose("prop_articulated", "auto", blocked) == "decompose"
    assert C.choose("prop_rigid", "hull", hollow) == "hull"  # an explicit choice wins


# --- measurements ------------------------------------------------------------------------------------------------------

def test_solid_crate_has_no_openings_and_takes_a_box():
    v, f = box([-0.3, -0.3, 0], [0.3, 0.3, 0.6])
    a = C.analyse(v, f)
    assert a["solid_ratio"] > 0.9
    assert a["hull_openings"]["openings"] == 0 and a["hull_openings"]["ok"]
    assert C.choose("prop_rigid", "auto", a) == "primitive"
    prims = C.fit_primitives(v, a["grid"], a["hull"])
    assert [p["kind"] for p in prims] == ["box", "cylinder", "capsule", "sphere"]
    assert prims[0]["accepted"] and prims[0]["cover"] > 0.99 and prims[0]["size"] < 1.1


def test_sloping_sides_are_not_openings():
    v, f = pyramid()
    assert C.analyse(v, f)["hull_openings"]["openings"] == 0


def test_arch_doorway_is_an_opening_one_hull_fills():
    v, f = arch()
    a = C.analyse(v, f)
    op = a["hull_openings"]
    assert op["openings"] == 1  # seen front to back; side to side the pillars hide it
    assert not op["ok"] and op["blocked_fraction"] > 0.9 and op["blocked_openings"] == 1
    assert C.choose("prop_rigid", "auto", a) == "decompose"
    # The exact shape keeps it open.
    assert C.openings_blocked(a["surf"], None, a["found"])["ok"]


def test_chair_is_hollow():
    v, f = chair()
    a = C.analyse(v, f)
    assert a["solid_ratio"] < C.HOLLOW_RATIO
    assert a["hull_openings"]["openings"] >= 2 and not a["hull_openings"]["ok"]


def test_coacd_keeps_the_arch_open_within_unity_limits():
    v, f = arch()
    a = C.analyse(v, f)
    hulls = C.decompose(v, f, 8)
    assert 2 <= len(hulls) <= 8
    assert all(len(hf) <= C.MAX_HULL_TRIANGLES for _, hf in hulls)
    parts = [{"v": hv, "f": hf} for hv, hf in hulls]
    op = C.openings_blocked(a["surf"], C._mask_of(a["grid"], parts), a["found"])
    assert op["ok"], op


def test_primitive_meshes_are_small_and_closed():
    for p in ({"kind": "box", "center": [0, 0, 0.5], "size_m": [1, 2, 1]},
              {"kind": "cylinder", "center": [0, 0, 0.5], "radius": 0.4, "height": 1.0},
              {"kind": "capsule", "center": [0, 0, 1], "radius": 0.3, "height": 2.0, "axis": 2},
              {"kind": "capsule", "center": [0, 0, 0.3], "radius": 0.3, "height": 2.0, "axis": 0},
              {"kind": "sphere", "center": [0, 0, 0], "radius": 0.5}):
        v, f = C.primitive_mesh(p)
        assert len(f) <= C.MAX_HULL_TRIANGLES
        edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        assert (counts == 2).all(), p["kind"]  # every edge shared by two triangles: closed
    v, _ = C.primitive_mesh({"kind": "capsule", "center": [0, 0, 0], "radius": 0.3, "height": 2.0, "axis": 0})
    assert np.isclose(np.ptp(v[:, 0]), 2.0) and np.isclose(np.ptp(v[:, 2]), 0.6)


def test_trunk_capsule_is_the_bottom_of_a_tree():
    v, f = join(box([-0.15, -0.15, 0], [0.15, 0.15, 2.0]), box([-1, -1, 1.6], [1, 1, 3.5]))
    cap = C.trunk_capsule(v)
    assert cap["kind"] == "capsule" and cap["axis"] == 2
    assert cap["height"] <= 0.4 * 3.5 + 1e-6
    assert 0.1 < cap["radius"] < 0.3


def test_obj_header_and_parts(tmp_path):
    v, f = box([0, 0, 0], [1, 1, 1])
    C.write_obj(tmp_path / "c.obj", [{"name": "a_collider_00", "kind": "box", "v": v, "f": f}], (v.min(0), v.max(0)))
    text = (tmp_path / "c.obj").read_text()
    assert "# vgac_bbox 0.000000 0.000000 0.000000 1.000000 1.000000 1.000000" in text
    assert "o a_collider_00" in text and "# vgac_kind box" in text
    assert text.count("\nv ") == 8 and text.count("\nf ") == 12


def test_build_writes_a_box_for_a_crate(tmp_path):
    import trimesh

    v, f = box([-0.3, -0.3, 0], [0.3, 0.3, 0.6])
    glb = tmp_path / "crate.glb"
    trimesh.Trimesh(np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1), f, process=False).export(glb)  # glTF is Y up
    plan = C.build(glb, tmp_path, "crate", "prop_rigid", "auto", "pc_mid")
    assert plan["chosen"] == "primitive" and [p["kind"] for p in plan["parts"]] == ["box"]
    assert Path(plan["obj"]).is_file() and plan["openings"]["ok"]
    assert np.allclose(plan["parts"][0]["shape"]["size_m"], [0.6, 0.6, 0.6], atol=1e-4)
    none = C.build(glb, tmp_path, "crate", "prop_rigid", "none", "pc_mid")
    assert none["chosen"] == "none" and not none["parts"] and "openings" not in none


def test_build_gives_a_tree_a_trunk_capsule(tmp_path):
    import trimesh

    v, f = join(box([-0.15, -0.15, 0], [0.15, 0.15, 2.0]), box([-1, -1, 1.6], [1, 1, 3.5]))
    glb = tmp_path / "tree.glb"
    trimesh.Trimesh(np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1), f, process=False).export(glb)
    plan = C.build(glb, tmp_path, "tree", "foliage", "auto", "pc_mid")
    assert plan["chosen"] == "trunk" and [p["kind"] for p in plan["parts"]] == ["capsule"]
    assert plan["parts"][0]["triangles"] <= C.MAX_HULL_TRIANGLES and "openings" not in plan


# --- export checks -----------------------------------------------------------------------------------------------------

def _ids(checks):
    return {c["id"]: c for c in checks}


def test_collider_checks_for_several_shapes():
    report = {"collider_mode": "parts", "collider_count": 3, "collider_parts": [
        {"name": "a_collider_00", "kind": "hull", "triangles": 236, "vertices": 120},
        {"name": "a_collider_01", "kind": "hull", "triangles": 300, "vertices": 152},
        {"name": "a_collider_02", "kind": "hull", "triangles": 40, "vertices": 22}]}
    plan = {"chosen": "decompose", "cap": 2, "openings": {"ok": False, "openings": 2, "blocked_openings": 1, "blocked_fraction": 0.4}}
    c = _ids(export.collider_checks(report, plan, "prop_rigid"))
    assert c["collider_present"]["ok"] and c["collider_present"]["severity"] == "block"
    assert not c["collider_triangles"]["ok"] and c["collider_triangles"]["value"] == 300 and c["collider_triangles"]["limit"] == 255
    assert not c["collider_count"]["ok"] and c["collider_count"]["value"] == 3 and c["collider_count"]["vars"]["vertices"] == 294
    assert not c["openings_clear"]["ok"] and c["openings_clear"]["value"] == 1 and c["openings_clear"]["vars"]["blocked"] == "40%"
    # Characters are not measured for openings.
    assert "openings_clear" not in _ids(export.collider_checks(report, plan, "humanoid"))


def test_collider_checks_exact_shape_none_and_legacy():
    mesh = {"collider_mode": "mesh", "collider_parts": [{"name": "a_collider_mesh", "kind": "mesh", "triangles": 4000, "vertices": 2100}]}
    c = _ids(export.collider_checks(mesh, {"chosen": "mesh", "openings": {"ok": True, "openings": 1, "blocked_openings": 0}}, "environment"))
    assert set(c) == {"collider_present", "openings_clear"} and c["openings_clear"]["ok"]
    assert export.collider_checks({"collider_mode": "none", "collider_parts": []}, {"chosen": "none"}, "prop_rigid") == []
    assert export.collider_checks({"collider_verts": 0}, {}, "prop_rigid", "none") == []
    legacy = _ids(export.collider_checks({"collider_verts": 54}, {}, "prop_rigid", "hull"))
    assert set(legacy) == {"collider_present"} and legacy["collider_present"]["ok"]


# --- what the importer is asked to do ----------------------------------------------------------------------------------

def _sc(tmp_path, category):
    return SimpleNamespace(dir=tmp_path, asset=SimpleNamespace(category=category), category={}, settings=SimpleNamespace(clips_libraries={}),
                           stage_version=lambda s: None)


def test_unity_args_carry_category_and_colliders(tmp_path):
    (tmp_path / "colliders.json").write_text("{}")
    args, flags, _ = export._unity_args(_sc(tmp_path, "environment"), {}, "wall")
    assert args["-vgacCategory"] == "environment" and Path(args["-vgacColliders"]).name == "colliders.json"
    assert "-vgacHumanoid" not in flags


def test_lighting_manifest_by_category():
    env = export._lighting_manifest(SimpleNamespace(asset=SimpleNamespace(category="environment")),
                                    {"secondaryUV": True, "secondaryUVOnMesh": True, "staticFlags": ["ContributeGI", "BatchingStatic"]})
    assert env["lightmap_uv"]["generated"] and env["lightmap_uv"]["static_flags"] == ["ContributeGI", "BatchingStatic"]
    tree = export._lighting_manifest(SimpleNamespace(asset=SimpleNamespace(category="foliage")), {"lodFadeMode": "CrossFade", "lodFadeWidth": 0.25})
    assert "lightmap_uv" not in tree and tree["lod_fade"]["mode"] == "CrossFade"
    assert export._lighting_manifest(SimpleNamespace(asset=SimpleNamespace(category="humanoid")), {}) is None


def test_importer_version_5_and_features():
    text = IMPORTER.read_text(encoding="utf-8")
    assert export.importer_version(IMPORTER) == 5
    for needle in ("generateSecondaryUV", "ModelImporterSecondaryUVMarginMethod.Calculate", "LODFadeMode.CrossFade",
                   "StaticEditorFlags.ContributeGI", '"Colliders"', "BoxCollider", "-vgacColliders", "-vgacCategory"):
        assert needle in text, needle
