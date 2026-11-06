"""Hair from the library, lane 3 (docs/HAIR-LIBRARY-PLAN.md section 6): binding the hair to the skeleton, its spring
chains, the hand-off (pack, export, manifest), the items' body tree and the Unity importer arguments. The numpy parts
run without Blender; the Blender side is proven offline by tools/hair_rig_check.py.

The first group of tests pins OFF IS UNCHANGED: with no library hair on the lineage every argument list, file, metric
and check is what it was before the feature."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vgac import springs as springs_mod
from vgac.blender import hairbind, itemfit
from vgac.blender import springs as bsprings
from vgac.stages import export
from vgac.stages import rig

FIXTURE = Path(__file__).parent / "data" / "hairlib_fixture"
SANDBOX = Path(__file__).resolve().parents[2] / "unity-sandbox" / "Assets"
LOOK = {"shading": "smooth", "texture_filter": "smooth"}


def fixture_npz():
    if not (FIXTURE / "hair_mesh.npz").is_file():
        pytest.skip("lane 1 fixture missing")
    return np.load(FIXTURE / "hair_mesh.npz")


def fixture_parts():
    return json.loads((FIXTURE / "hair_parts.json").read_text(encoding="utf-8"))


# --- OFF IS UNCHANGED ---------------------------------------------------------------------------------------------------


def _ver(stage, metrics=None, params=None, files=None):
    return SimpleNamespace(stage=stage, metrics=metrics or {}, params=params or {}, files=files or {}, id=stage + "-1")


def test_off_rig_finds_no_library_hair_and_binds_nothing(tmp_path):
    prep = _ver("prep", {"triangles": 19000, "hair_material": {"use": True}})
    version = SimpleNamespace(metrics={}, params={}, files={})
    calls = []

    async def blender(*a, **k):
        calls.append(a)

    sc = SimpleNamespace(stage_version=lambda s: prep if s == "prep" else None, version=version, dir=tmp_path,
                         settings=SimpleNamespace(blender_timeout_seconds=10), blender=blender)
    assert rig.library_hair(sc) is None
    anim = tmp_path / "anim.glb"
    anim.write_bytes(b"glTF")
    asyncio.run(rig.hair_bind(sc, anim))
    assert calls == [] and version.metrics == {} and version.files == {}
    assert not (tmp_path / "anim_body.glb").exists()
    # no hair_bound check without metrics.hair_bind
    assert rig.hair_bound_check({"springs": {"made": False}}) is None


def test_off_springs_args_unchanged(tmp_path):
    a = springs_mod.blender_args(Path("a.glb"), Path("o.glb"), tmp_path, None, None, None, None)
    b = springs_mod.blender_args(Path("a.glb"), Path("o.glb"), tmp_path, None, None, None, None, library_hair=None)
    assert a == b and "--hair-object" not in [str(x) for x in a]
    assert a == [Path("a.glb"), Path("o.glb"), "--report", tmp_path / "springs_report.json", "--doc", tmp_path / "springs.json",
                 "--pictures", tmp_path / "springs_pictures", "--fps", 24]


def test_off_springs_metrics_have_no_library_key():
    rep = {"chains": [{"name": "Spring_Cape_01", "kind": "Cape", "parent": "Spine2", "bones": ["a", "b"], "part": {"length": 0.5}}]}
    m = springs_mod.metrics(rep, True, None)
    assert "library" not in m["chains"][0]


def test_off_pack_args_unchanged_and_layout_hair_unchanged():
    plain = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["fbx", "glb"], LOOK)
    assert not any(str(x).startswith("--hair") for x in plain)
    layout = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["fbx", "glb"], LOOK,
                              hair={"layout": "l.json", "base_color": "h.png", "normal": "hn.png", "orm": None})
    assert layout[-6:] == ["--hair-layout", "l.json", "--hair-base-color", "h.png", "--hair-normal", "hn.png"]
    assert "--hair-object" not in layout


def test_off_export_hair_inputs_and_unity_args(tmp_path):
    prep = _ver("prep", {"hair_material": {"use": False}})
    rig_v = _ver("rig", {"bones": 30})
    sc = SimpleNamespace(stage_version=lambda s: {"prep": prep, "rig": rig_v}.get(s), dir=tmp_path,
                         category={"rig_engine": "skintoken_mixamo"}, version=SimpleNamespace(metrics={}, files={}),
                         settings=SimpleNamespace(clips_libraries={}), asset=SimpleNamespace(category="humanoid"))
    assert export.library_hair(sc) is None and export.hair_inputs(sc) == {}
    (tmp_path / "hair_base_color.png").write_bytes(b"x")
    sc.version.metrics = {"hair_material": {"ok": True, "planned": True}}
    args, _, _ = export._unity_args(sc, {}, "n")
    assert args["-vgacHair"] == "on" and "-vgacHairCutout" not in args and "-vgacHairTwoSided" not in args


def test_off_unity_metrics_have_no_cutout_keys():
    m = export.unity_metrics({"ok": True, "hairCutout": False, "hairTwoSided": False})
    assert "hairCutout" not in m and "hairTwoSided" not in m
    m = export.unity_metrics({"ok": True, "hairCutout": True})
    assert m["hairCutout"] is True


def test_off_hair_shipped_vars_have_no_source(tmp_path):
    c = export.hair_shipped_checks({"hair_material": {"planned": True, "ok": False, "error": "x"}}, None)
    assert "source" not in c[0]["vars"]
    c = export.hair_shipped_checks({"hair_material": {"planned": True, "ok": False, "error": "x", "source": "library"}}, None)
    assert c[0]["vars"]["source"] == "library"


def test_off_hair_shader_untouched_and_cutout_separate():
    hair = (SANDBOX / "VgacShaders" / "VgacHair.shader").read_text(encoding="utf-8")
    assert "VgacHair Version 2" in hair and "HairCutout" not in hair and "alphatest" not in hair
    cut = (SANDBOX / "VgacShaders" / "VgacHairCutout.shader").read_text(encoding="utf-8")
    for needle in ('Shader "VGAC/HairCutout"', "alphatest:_Cutoff", "Cull [_Cull]", "VFACE", "addshadow", '"Queue" = "AlphaTest"',
                   "StrandSpec", "_FlowMap"):
        assert needle in cut, needle
    assert export.cutout_shader_version(SANDBOX / "VgacShaders" / "VgacHairCutout.shader") == 1
    assert export.shader_version(SANDBOX / "VgacShaders" / "VgacHairCutout.shader") is None  # never mistaken for VgacHair
    meta = (SANDBOX / "VgacShaders" / "VgacHairCutout.shader.meta").read_text(encoding="utf-8")
    assert "guid:" in meta and "ShaderImporter" in meta


def test_importer_version_17_with_cutout_arguments():
    cs = SANDBOX / "Editor" / "VgacImport.cs"
    assert export.importer_version(cs) == 17
    text = cs.read_text(encoding="utf-8")
    for needle in ("-vgacHairCutout", "-vgacHairTwoSided", "HairCutoutShaderName", "mipMapsPreserveCoverage", "alphaTestReferenceValue",
                   "public bool hairCutout", "public bool hairTwoSided"):
        assert needle in text, needle


# --- binding weights (hairbind.py) -----------------------------------------------------------------------------------


def test_nape_share_is_zero_above_the_neck_and_one_a_blend_below():
    s = hairbind.nape_share([1.60, 1.50, 1.45, 1.40, 1.30], 1.50, 0.10)
    assert s[0] == 0 and s[1] == 0 and 0 < s[2] < 1 and s[3] == pytest.approx(1.0) and s[4] == 1.0
    assert abs(s[2] - 0.5) < 1e-9  # smoothstep half way


def test_bind_weights_head_above_blend_below_hanging_to_head():
    V = np.array([[0, 0, 1.6], [0, 0.05, 1.45], [0, 0.05, 1.30], [0, 0.1, 1.30]])
    role = np.array([0, 0, 1, 2])
    near = [{}, {"Neck": 0.7, "Spine2": 0.3}, {"Spine2": 1.0}, {"Spine2": 1.0}]
    w, st = hairbind.bind_weights(V, role, 1.5, "Head", near, 0.10)
    assert w[0] == {"Head": 1.0}
    assert set(w[1]) == {"Head", "Neck", "Spine2"} and w[1]["Head"] == pytest.approx(0.5)
    assert sum(w[1].values()) == pytest.approx(1.0)
    assert w[2] == {"Spine2": 1.0}  # the cap below the blend follows the chest
    assert w[3] == {"Head": 1.0}  # hanging group: Head, the spring chain takes over
    assert st["nape_blend_verts"] == 2 and st["groups"] == [0] and st["rest_share"] == 0.5
    assert hairbind.bound_share(w, ["Head", "Neck", "Spine2"]) == 1.0
    assert hairbind.bound_share(w, ["Head"]) == 0.5


def test_bind_weights_without_body_weights_stay_on_the_head():
    w, st = hairbind.bind_weights(np.array([[0, 0, 1.3]]), np.array([0]), 1.5, "Head", [{}], 0.1)
    assert w == [{"Head": 1.0}] and st["nape_blend_verts"] == 0


def test_nape_filter_keeps_only_the_nape_bones():
    roles = {"mixamorig:Neck": "Neck", "mixamorig:LeftArm": "LeftArm", "mixamorig:Spine2": "Spine2"}
    assert hairbind.nape_filter({"mixamorig:Neck": 0.5, "mixamorig:LeftArm": 0.5}, roles) == {"mixamorig:Neck": 1.0}
    assert hairbind.nape_filter({"mixamorig:LeftArm": 1.0}, roles) == {}


def test_limit_normalise_keeps_four():
    w = hairbind.limit_normalise({"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.05, "e": 0.05})
    assert len(w) == 4 and sum(w.values()) == pytest.approx(1.0) and "a" in w


def test_box_fit_restores_a_scaled_shifted_copy():
    rng = np.random.default_rng(1)
    P = rng.normal(size=(200, 3))
    Q = P * 1.7 + np.array([0.2, -0.1, 0.0])
    Q[:, 2] -= Q[:, 2].min()
    s, sh = hairbind.box_fit(P.min(0), P.max(0), Q.min(0), Q.max(0))
    assert np.allclose(P * s + sh, Q, atol=1e-9)


# --- position matching ------------------------------------------------------------------------------------------------


def test_roles_found_by_position_after_reorder_split_and_scale():
    nz = fixture_npz()
    N = hairbind.gltf_to_blender(nz["verts"])
    role = np.asarray(nz["role"])
    rng = np.random.default_rng(3)
    order = rng.permutation(len(N))
    dup = order[:300]  # the glTF export splits vertices along seams: copies at the same place
    V = np.vstack([N[order], N[dup]]) * 1.69 + np.array([0.0, 0.003, 0.85])
    want = np.concatenate([role[order], role[dup]])
    got, worst = hairbind.roles_by_position(V, N, role)
    assert np.array_equal(got, want) and worst < 1e-5
    got2 = bsprings.match_roles(V, N, role)
    assert np.array_equal(got2, want)


def test_match_positions_misses_far_points():
    idx, d = hairbind.match_positions(np.array([[0, 0, 0], [5, 5, 5]]), np.array([[0, 0, 1e-5]]), 1e-3)
    assert idx.tolist() == [0, -1] and np.isinf(d[1])


# --- spring chains for the library's hanging groups ---------------------------------------------------------------------


def test_library_chains_follow_the_group_path_root_first():
    nz = fixture_npz()
    parts_doc = fixture_parts()
    N = hairbind.gltf_to_blender(nz["verts"]) * 1.7
    role = np.asarray(nz["role"])
    s, sh = bsprings.box_map(hairbind.gltf_to_blender(nz["verts"]), N)
    assert s == pytest.approx(1.7)
    parts, used = bsprings.library_parts(parts_doc["groups"], N, role, "mixamorig:Head", s, sh)
    assert len(parts) == 1 and parts[0]["parent"] == "mixamorig:Head" and parts[0]["library"] and parts[0]["kind"] == "Hair"
    J = np.array(parts[0]["columns"][0])
    path = hairbind.gltf_to_blender(parts_doc["groups"][0]["path"]) * 1.7
    assert 2 <= len(J) - 1 <= 4 and used == len(J) - 1
    assert np.allclose(J[0], path[0], atol=1e-4) and np.allclose(J[-1], path[-1], atol=1e-4)  # root first, to the tip
    assert parts[0]["length"] == pytest.approx(parts_doc["groups"][0]["length_m"] * 1.7, rel=0.05)
    assert parts[0]["verts"] == int(np.sum(role == 2))
    assert 0.02 <= parts[0]["thickness"] <= 0.1


def test_library_chain_bone_count_by_length_and_cap():
    V = np.array([[0, 0.1, 1.6 - 0.01 * i] for i in range(50)])
    role = np.full(50, 2)
    for length, nb in ((0.1, 2), (0.3, 3), (0.5, 4)):
        g = [{"k": 0, "kind": "Hair", "path": [[0, 1.6, -0.1], [0, 1.6 - length, -0.1]]}]
        parts, used = bsprings.library_parts(g, V, role, "Head")
        assert used == nb and len(parts[0]["columns"][0]) == nb + 1
    g = [{"k": 0, "path": [[0, 1.6, -0.1], [0, 1.1, -0.1]]}]
    assert bsprings.library_parts(g, V, role, "Head", max_bones=3) == ([], 0)  # over the cap: no chain


def test_library_chain_without_a_path_uses_the_group_points():
    V = np.array([[0, 0.1, 1.6 - 0.01 * i] for i in range(30)])
    parts, used = bsprings.library_parts([{"k": 0, "path": []}], V, np.full(30, 2), "Head")
    J = np.array(parts[0]["columns"][0])
    assert J[0][2] > J[-1][2] and used == 3


def test_library_chain_skips_groups_with_no_vertices():
    parts, used = bsprings.library_parts([{"k": 1, "path": [[0, 1, 0], [0, 0.8, 0]]}], np.zeros((3, 3)), np.zeros(3), "Head")
    assert parts == [] and used == 0


def test_find_parts_cap_and_names_continue_after_the_library_chains():
    # a cap of 0 leaves the body nothing; names continue from the library counter
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1.8]], float)
    F = np.array([[0, 1, 2], [0, 1, 3]])
    bones = [{"name": "Hips", "head": [0, 0, 1.0], "tail": [0, 0, 1.2], "parent": None}]
    out = bsprings.find_parts(V, F, bones, max_bones=0, counter={"Hair": 1})
    assert out["parts"] == []


def test_library_rests_reason_and_metrics_flag():
    assert springs_mod.REASONS["library_rests"] == "the hairstyle rests on the head, so it moves with the head"
    m = springs_mod.metrics({"chains": [], "reason": "library_rests"}, True, None)
    assert m["reason"] == "library_rests" and not m["made"]
    v = springs_mod.check_values(m)
    assert v["ok"] and v["vars"]["why"].startswith("the hairstyle rests")
    rep = {"chains": [{"name": "Spring_Hair_01", "kind": "Hair", "parent": "Head", "bones": ["a", "b", "c"], "library": True,
                       "part": {"length": 0.22}}]}
    assert springs_mod.metrics(rep, True, None)["chains"][0]["library"] is True


def test_springs_args_with_library_hair(tmp_path):
    parts = tmp_path / "hair_parts.json"
    parts.write_text("{}")
    a = springs_mod.blender_args(Path("a.glb"), Path("o.glb"), tmp_path, None, None, None, None,
                                 library_hair=("VGAC_Hair", Path("h.npz"), parts))
    assert a[-6:] == ["--hair-object", "VGAC_Hair", "--hair-mesh", Path("h.npz"), "--hair-parts", parts]
    b = springs_mod.blender_args(Path("a.glb"), Path("o.glb"), tmp_path, None, None, None, None,
                                 library_hair=("VGAC_Hair", Path("h.npz"), tmp_path / "missing.json"))
    assert b[-4:] == ["--hair-object", "VGAC_Hair", "--hair-mesh", Path("h.npz")]


# --- rig: the check and the donor -------------------------------------------------------------------------------------


def test_hair_bound_check():
    c = rig.hair_bound_check({"hair_bind": {"ok": True, "bound_share": 1.0, "groups": 1},
                              "springs": {"chains": [{"library": True}, {"kind": "Cape"}]}})
    assert c["id"] == "hair_bound" and c["ok"] and c["value"] == 1.0 and c["limit"] == 1.0
    assert c["vars"] == {"groups_swaying": 1, "rest_on_head": False}
    c = rig.hair_bound_check({"hair_bind": {"ok": True, "bound_share": 0.8, "groups": 0}})
    assert not c["ok"] and c["vars"]["rest_on_head"] is True
    c = rig.hair_bound_check({"hair_bind": {"ok": False, "bound_share": 1.0, "error": "x"}})
    assert not c["ok"] and c["value"] == 0.0


def test_hair_bind_metrics_from_report():
    m = rig.hair_bind_metrics({"ok": True, "verts": 10, "rest_share": 0.9, "nape_blend_verts": 3, "groups": [0, 1],
                               "bound_share": 1.0, "follow_error_m": 0.0}, 2.5)
    assert m == {"ok": True, "verts": 10, "rest_share": 0.9, "nape_blend_verts": 3, "groups": 2, "bound_share": 1.0,
                 "follow_error_m": 0.0, "seconds": 2.5, "error": None}
    assert rig.hair_bind_metrics({}, 1.0, error="boom")["ok"] is False


def test_library_hair_found_on_the_clean_up_version(tmp_path, monkeypatch):
    npz = tmp_path / "hair_mesh.npz"
    npz.write_bytes(b"x")
    prep = _ver("prep", {"hair_library": {"attached": True, "kind": "cards"}, "body_glb_hash": "abc"},
                files={"hair_mesh": "hair_mesh.npz"})
    monkeypatch.setattr(rig.library, "version_file", lambda v, k, s=None: npz if k == "hair_mesh" else None)
    sc = SimpleNamespace(stage_version=lambda s: prep, settings=None)
    lib = rig.library_hair(sc)
    assert lib["mesh"] == npz and lib["body_glb_hash"] == "abc" and lib["parts"] is None
    prep.metrics["hair_library"]["attached"] = False
    assert rig.library_hair(sc) is None


def test_same_moves_for_the_donor():
    cat = {"clips": ["idle", "walk"], "motion": "biped"}
    assert rig._same_moves(cat, {}, {"moves": ["idle", "walk"], "clips_source": None})
    # the params a done rig wrote back (the defaults) still match an Adjust that set nothing
    assert rig._same_moves(cat, {}, {"moves": ["idle", "walk"], "clips_source": "auto", "motion": "biped"})
    assert not rig._same_moves(cat, {}, {"clips_source": "procedural"})
    assert not rig._same_moves(cat, {"added_moves": ["x"]}, {})
    assert not rig._same_moves(cat, {"moves": ["idle"]}, {})


def test_find_donor_needs_a_hash_and_a_database():
    sc = SimpleNamespace(db=None, asset=SimpleNamespace(id="a"), version=SimpleNamespace(id="v"))
    assert rig.find_donor(sc, "Mixamo", 4, None) is None
    assert rig.find_donor(sc, "Mixamo", 4, "abc") is None


def test_anim_files_carry_the_hair_files_on_a_moves_remake():
    assert "anim_body" in rig.ANIM_FILES and "hair_bind" in rig.ANIM_FILES and "hair_bind" in rig._ANIM_METRICS


# --- hand-off: pack and importer arguments, manifest ----------------------------------------------------------------------


def test_pack_args_library_branch():
    hair = {"library": {"kind": "cards", "alpha": "cutout", "two_sided": True}, "mesh": "hair_mesh.npz",
            "base_color": "hb.png", "normal": None, "orm": "ho.png", "layout": None}
    a = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["fbx", "glb"], LOOK, hair=hair)
    i = a.index("--hair-object")
    assert a[i:] == ["--hair-object", "VGAC_Hair", "--hair-mesh", "hair_mesh.npz", "--hair-base-color", "hb.png",
                     "--hair-orm", "ho.png", "--hair-kind", "cards", "--hair-alpha", "cutout", "--hair-two-sided"]
    assert "--hair-layout" not in a
    shell = dict(hair, library={"kind": "shell", "alpha": "opaque", "two_sided": False})
    b = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["glb"], LOOK, hair=shell)
    assert b[b.index("--hair-kind") + 1] == "shell" and "--hair-alpha" not in b and "--hair-two-sided" not in b


def _lib_sc(tmp_path, bound=True):
    prep_dir = tmp_path / "prep"
    prep_dir.mkdir(parents=True)
    for k in ("hair_mesh.npz", "hair_base_color.png", "hair_roughness.png", "hair_flow.png", "hair_normal.png"):
        (prep_dir / k).write_bytes(b"x")
    prep = _ver("prep", {"hair_library": {"attached": True, "kind": "cards", "alpha": "cutout", "two_sided": True, "tex": 1024,
                                          "credit": {"id": "mh_ponytail01", "name": "Ponytail01", "author": "MakeHuman",
                                                     "licence": "CC0-1.0", "source_url": "http://example"}}})
    rig_v = _ver("rig", {"bones": 40, "hair_bind": {"ok": bound}, "springs": {"chains": [{"name": "Spring_Hair_01", "library": True},
                                                                                         {"name": "Spring_Cape_01"}]}})
    out = tmp_path / "export"
    out.mkdir(parents=True)
    files = {"hair_mesh": prep_dir / "hair_mesh.npz", "hair_base_color": prep_dir / "hair_base_color.png",
             "hair_roughness": prep_dir / "hair_roughness.png", "hair_flow": prep_dir / "hair_flow.png",
             "hair_normal": prep_dir / "hair_normal.png"}

    def copy_in(src, name):
        d = out / name
        d.write_bytes(Path(src).read_bytes())
        return d

    return SimpleNamespace(stage_version=lambda s: {"prep": prep, "rig": rig_v}.get(s),
                           lineage_file=lambda key, stages=None: files.get(key), copy_in=copy_in, dir=out,
                           version=SimpleNamespace(metrics={}, files={}), category={"rig_engine": "skintoken_mixamo"},
                           settings=SimpleNamespace(clips_libraries={}), asset=SimpleNamespace(category="humanoid"))


def test_export_hair_inputs_library_branch(tmp_path):
    sc = _lib_sc(tmp_path)
    got = export.hair_inputs(sc)
    assert set(got) == {"hair_mesh", "hair_base_color", "hair_roughness", "hair_flow", "hair_normal"}
    assert got["hair_mesh"].name == "hair_mesh.npz" and "hair_layout" not in got
    assert "hair_normal" not in export.hair_inputs(sc, keep_normal=False)
    # not bound in the rig step: no library hand-off, and the fused-hair branch finds no layout either
    assert export.hair_inputs(_lib_sc(tmp_path / "b", bound=False)) == {}


def test_unity_args_library_flags(tmp_path):
    sc = _lib_sc(tmp_path)
    (sc.dir / "hair_base_color.png").write_bytes(b"x")
    sc.version.metrics = {"hair_material": {"ok": True, "source": "library", "alpha": "cutout", "two_sided": True}}
    args, _, _ = export._unity_args(sc, {}, "n")
    assert args["-vgacHair"] == "on" and args["-vgacHairCutout"] == "on" and args["-vgacHairTwoSided"] == "on"
    sc.version.metrics = {"hair_material": {"ok": True, "source": "library", "alpha": "opaque", "two_sided": False}}
    args, _, _ = export._unity_args(sc, {}, "n")
    assert args["-vgacHair"] == "on" and "-vgacHairCutout" not in args and "-vgacHairTwoSided" not in args


def test_library_manifest_block_and_readme(tmp_path):
    sc = _lib_sc(tmp_path)
    m = {"ok": True, "source": "library", "slots": ["n_Body", "n_Hair"], "alpha": "cutout", "two_sided": True, "faces": 5051,
         "lod_triangles": {"LOD0": 5051, "LOD2": 252}, "lods_body": {"LOD0": 14672}, "draws_added": 1, "anisotropy_glbs": [],
         "cap_faces": 252}
    sc.version.metrics = {"hair_material": m}
    sc.file = lambda k: None
    block = export._hair_manifest(sc, {"hairMaterial": "Assets/x.mat", "hairShader": "VGAC/HairCutout", "hairCutout": True,
                                       "hairTwoSided": True})
    assert block["source"] == "library" and block["style"] == {"id": "mh_ponytail01", "name": "Ponytail01", "author": "MakeHuman",
                                                              "licence": "CC0-1.0", "source_url": "http://example"}
    assert block["alpha"] == "cutout" and block["two_sided"] is True and block["hanging_groups"] == ["Spring_Hair_01"]
    assert block["unity"]["shader"] == "VGAC/HairCutout" and block["cap_triangles"] == 252
    lines = export.library_readme_lines(sc.stage_version("prep").metrics["hair_library"], m)
    assert "Ponytail01 by MakeHuman, licence CC0-1.0, from http://example." in lines[1]
    assert any("Alpha Scissor" in x for x in lines) and any("two-sided" in x for x in lines)


def test_export_lod_checks_measure_the_body():
    from vgac import quality

    rep = {"lods": {"LOD0": 19723, "LOD1": 12387, "LOD2": 3920}, "lods_body": {"LOD0": 14672, "LOD1": 7336, "LOD2": 3668}}
    whole = quality.lod_checks(rep, [0.5, 0.25])
    body = quality.lod_checks({**rep, "lods": rep["lods_body"]}, [0.5, 0.25])
    assert not all(c["ok"] for c in whole) and all(c["ok"] for c in body)


def test_sync_cutout_shader(tmp_path):
    src = tmp_path / "src.shader"
    src.write_text("// VgacHairCutout Version 2\n")
    dest = tmp_path / "p" / "Assets" / "VgacShaders" / "VgacHairCutout.shader"
    assert export.sync_cutout_shader(src, dest)["action"] == "copied"
    assert export.sync_cutout_shader(src, dest)["action"] == "current"
    dest.write_text("// VgacHairCutout Version 1\n")
    assert export.sync_cutout_shader(src, dest)["action"] == "updated"
    assert export.sync_cutout_shader(tmp_path / "none", dest)["action"] == "no_source"


# --- items: the body tree leaves out the library hair -------------------------------------------------------------------


def test_items_body_tree_leaves_out_library_hair_only():
    polys = [((0, 1, 2), 0), ((2, 3, 4), 1), ((4, 5, 6), 0)]
    assert itemfit.body_polygons(polys, [{}, {"vgac_hair": "library"}]) == [(0, 1, 2), (4, 5, 6)]
    # today's hair slot (no mark) stays in the body
    assert itemfit.body_polygons(polys, [None, {}]) == [(0, 1, 2), (2, 3, 4), (4, 5, 6)]
    assert itemfit.is_library_hair({"vgac_hair": "library"}) and not itemfit.is_library_hair(None)
    assert not itemfit.is_library_hair({"vgac_hair": "model"})


# --- library hair hangs (springs.py simulate "hang"; VgacSpringBones Version 2) -------------------------------------------

REST = [[0, 0.10, 1.60], [0, 0.13, 1.52], [0, 0.15, 1.44], [0, 0.16, 1.37]]  # a ponytail out back and down (Blender)


def _bow(deg):
    """The head's rotation from rest when it bows forward by deg (its up turns toward the front, -Y)."""
    a = np.radians(deg) / 2
    return np.array([np.cos(a), np.sin(a), 0.0, 0.0])


def _sim(hang, bow, travel=None, loop=False):
    frames = [{"root": np.array(REST[0], float), "q": _bow(bow)} for _ in range(48)]
    st = dict(bsprings.SETTINGS["Hair"], **({"hang": hang} if hang is not None else {}))
    return bsprings.simulate(frames, {"rest": REST, "radius": 0.02}, st, 24, None, travel, loop=loop)["joints"]


def test_hang_zero_or_absent_is_the_old_behaviour():
    for bow in (0, 40):
        a, b = _sim(None, bow), _sim(0.0, bow)
        assert np.array_equal(a, b)
    assert "hang" not in bsprings.SETTINGS["Hair"] and all("hang" not in v for v in bsprings.SETTINGS.values())


def test_library_hair_hangs_when_the_head_bows_and_trails_in_a_run():
    h = bsprings.LIBRARY_HAIR_HANG
    still = bsprings.hang_degrees(_sim(h, 40)[-10:])[0]
    old = bsprings.hang_degrees(_sim(None, 40)[-10:])[0]
    assert still <= 20 and old > still + 10
    run = _sim(h, 40, travel=np.array([0.0, -3.5, 0.0]))
    tip = run[-1, -1] - run[-1, 0]
    assert bsprings.hang_degrees(run[-10:])[0] > still + 10 and tip[1] > 0.05  # trails behind (+Y) in a run


def test_hang_loop_seam_stays_exact():
    frames = [{"root": np.array(REST[0], float) + np.array([0, 0, 0.02 * np.sin(2 * np.pi * i / 24)]), "q": _bow(30)}
              for i in range(25)]
    st = dict(bsprings.SETTINGS["Hair"], hang=bsprings.LIBRARY_HAIR_HANG)
    J = bsprings.simulate(frames, {"rest": REST, "radius": 0.02}, st, 24, None, None, loop=True)["joints"]
    assert bsprings.seam_error(J) < 1e-9


def test_hang_is_pushed_out_of_the_hit_boxes():
    # a hit box (the upper back) right under where the hanging tail would go
    cap = [(np.array([0, 0.14, 1.30]), np.array([0, 0.14, 1.50]), 0.06)]
    frames = [{"root": np.array(REST[0], float), "q": _bow(40)} for _ in range(48)]
    st = dict(bsprings.SETTINGS["Hair"], hang=bsprings.LIBRARY_HAIR_HANG)
    allow = bsprings.collider_allowance(REST, cap, 0.02)
    cols = [[(a, b, r) for (a, b, _), r in zip(cap, allow)] for _ in frames]
    J = bsprings.simulate(frames, {"rest": REST, "radius": 0.02}, st, 24, cols, None)["joints"]
    assert bsprings.deepest_beyond_rest(J, cols, allow) <= springs_mod.INTO_BODY_MAX_M


def test_springs_doc_writes_hang_only_for_hanging_chains():
    base = {"name": "Spring_Cape_01", "kind": "Cape", "parent": "Spine2", "bones": ["a"], "joints": [[0, 0, 1], [0, 0, 0.5]],
            "radius": 0.02, "settings": dict(bsprings.SETTINGS["Cape"]), "confidence": 0.9}
    hair = dict(base, name="Spring_Hair_01", kind="Hair", settings=dict(bsprings.SETTINGS["Hair"], hang=0.7))
    doc = bsprings.springs_doc([base, hair], None, 1.7)
    assert "hang" not in doc["chains"][0] and doc["chains"][1]["hang"] == 0.7


def test_slerp_dir():
    a, b = np.array([1.0, 0, 0]), np.array([0, 0, -1.0])
    m = bsprings.slerp_dir(a, b, 0.5)
    assert np.allclose(m, np.array([1, 0, -1]) / np.sqrt(2))
    assert np.allclose(bsprings.slerp_dir(a, b, 0.0), a) and np.allclose(bsprings.slerp_dir(a, b, 1.0), b)
    assert np.isclose(np.linalg.norm(bsprings.slerp_dir(a, -a, 0.3)), 1.0)


def test_unity_spring_script_reads_hang_and_keeps_version_one_behaviour():
    cs = (SANDBOX / "VgacScripts" / "VgacSpringBones.cs").read_text(encoding="utf-8")
    assert export.spring_script_version(SANDBOX / "VgacScripts" / "VgacSpringBones.cs") == 2
    assert "hang = 0f" in cs and "if (c.hang > 0f)" in cs
    assert "else gEff = g - Q * c.gravityAcross[k] * c.gravity * scale;" in cs  # the Version 1 line, unchanged
