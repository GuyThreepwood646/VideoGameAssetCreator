"""Hair as its own material (vgac/hairmat.py, docs/HAIR-RESEARCH.md 3.2 (b)): the triangle split and its fallback, the
budget rule, the flow map, the GLB extension, the hand-off's file lists and manifest, and the check sentences."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from vgac import face, guidance, hair, hairmat
from vgac.stages import export
from vgac.stages import hair_detail as hd

from test_hair_detail import HAIR_RGB, SKIN_RGB, cap, centres


def dome_texture(mesh: dict[str, np.ndarray], T: int = 128, hairline_y: float = 0.03, scarf: bool = False) -> np.ndarray:
    """Hair above hairline_y (on the dome), skin below; scarf: a band of hair-coloured cloth turned grey in spots."""
    base = np.zeros((T, T, 3))
    reg = hair.texel_region(mesh, T, np.ones(len(mesh["faces"]), dtype=bool))
    y = reg["pos"][:, 1]
    col = np.where((y >= hairline_y)[:, None], np.array(HAIR_RGB), np.array(SKIN_RGB))
    if scarf:
        speck = (np.sin(reg["pos"][:, 0] * 900) > 0.2) & (y >= hairline_y)
        col[speck] = (0.3, 0.3, 0.32)
    base[reg["ys"], reg["xs"]] = col
    return base


def hair_tris(mesh: dict[str, np.ndarray], hairline_y: float = 0.03) -> np.ndarray:
    """Every triangle with a corner above the hairline: the ones across it straddle."""
    return (mesh["verts"][mesh["faces"]][:, :, 1] >= hairline_y).any(axis=1)


# --- 1. which triangles ------------------------------------------------------------------------------------------------


def test_triangles_across_the_hairline_stay_with_the_body():
    mesh = cap(n=24, top_deg=85)
    base = dome_texture(mesh)
    tris = hair_tris(mesh)
    sel = hairmat.choose(mesh, tris, base, centres(), 0, 1, None, 0.9)
    assert sel["use"] and sel["reason"] is None
    m = sel["measures"]
    assert m["straddling"] > 0 and m["material_triangles"] == int(sel["tris"].sum())
    # a triangle with skin texels in it never moves; the ones wholly above the hairline do
    v = mesh["verts"][mesh["faces"]][:, :, 1]
    across = tris & (v.min(axis=1) < 0.03)
    assert not (sel["tris"] & across).any()
    assert sel["tris"][(v.min(axis=1) > 0.05)].mean() > 0.9
    assert (sel["ring"] == (tris & ~sel["tris"])).all()


def test_the_face_keeps_its_triangles():
    mesh = cap(n=24, top_deg=85)
    T = 128
    base = dome_texture(mesh, T)
    protect = np.zeros((T, T))
    protect[: T // 2] = 1.0  # half the texture belongs to the face
    sel = hairmat.choose(mesh, hair_tris(mesh), base, centres(), 0, 1, protect, 0.9)
    uvp = face.uv_pixels(mesh["uv"], T)[mesh["faces"]].mean(axis=1)
    assert not sel["tris"][uvp[:, 1] < T // 2 - 2].any()
    assert sel["measures"]["at_face"] > 0


def test_a_doubtful_split_falls_back_to_one_material():
    mesh = cap(n=24, top_deg=85)
    tris = hair_tris(mesh)
    low = hairmat.choose(mesh, tris, dome_texture(mesh), centres(), 0, 1, None, 0.5)  # hair close to the cloak's colour
    assert not low["use"] and low["reason"] == "doubtful"
    scarf = hairmat.choose(mesh, tris, dome_texture(mesh, scarf=True), centres(), 0, 1, None, 0.9)  # grey cloth grown into it
    assert scarf["measures"]["hair_colour_share"] < 0.95
    assert scarf["confidence"] < 0.9


def test_too_little_hair_all_through_is_not_worth_a_material():
    mesh = cap(n=24, top_deg=85)
    T = 128
    protect = np.ones((T, T))  # the face's region covers nearly all of it (a fringe over a small cap)
    protect[:, : T // 8] = 0.0
    sel = hairmat.choose(mesh, hair_tris(mesh), dome_texture(mesh, T), centres(), 0, 1, protect, 0.95)
    assert not sel["use"] and sel["reason"] == "too_little"
    for r in hairmat.REASONS:
        assert hairmat.reason_words(r) and "_" not in hairmat.reason_words(r)


# --- 2. which assets -----------------------------------------------------------------------------------------------------


def test_budget_rule_pc_and_showcase_not_phone():
    pc, show, phone = {"texture": 2048}, {"texture": 4096}, {"texture": 1024}
    assert hairmat.applies("humanoid", "pc_mid", pc, {}) is None
    assert hairmat.applies("humanoid", "high", show, {}) is None
    assert hairmat.applies("humanoid", "mobile_low", phone, {}) == "phone"
    assert hairmat.applies("humanoid", "mobile_low", phone, {"hair_material": "on"}) is None  # the person's choice wins
    assert hairmat.applies("humanoid", "pc_mid", pc, {"hair_material": "off"}) == "off"
    assert hairmat.applies("humanoid", "pc_mid", pc, {"hair_material": False}) == "off"
    assert hairmat.applies("quadruped", "pc_mid", pc, {}) == "not_category"
    assert hairmat.hair_texture_size(pc) == 1024 and hairmat.hair_texture_size(show) == 2048 and hairmat.hair_texture_size(phone) == 1024
    assert hairmat.option({}) == "auto" and hairmat.option({"hair_material": "nonsense"}) == "auto"


# --- 3. the hair's own texture ----------------------------------------------------------------------------------------------


def test_layout_file_reads_back_in_gltf_coordinates(tmp_path: Path):
    row = [0.0, -0.2, 1.7] + [0.0, -0.2, 1.7, 0.01, -0.2, 1.7, 0.0, -0.21, 1.71] + [0.1, 0.2, 0.3, 0.2, 0.1, 0.4]
    p = tmp_path / "hair_layout.json"
    p.write_text(json.dumps({"tex": 1024, "box": [[0, 0, 0], [1, 1, 2]], "faces": [row]}))
    lay = hairmat.read_layout(p)
    assert lay["tex"] == 1024
    assert np.allclose(lay["centres"][0], [0.0, 1.7, 0.2])  # Blender (x, y, z) Z up -> glTF (x, z, -y)
    sub = hairmat.submesh(lay)
    assert sub["faces"].shape == (1, 3) and np.allclose(sub["uv"][2], [0.1, 0.4])


def test_flow_map_follows_the_strands_in_the_texture_directions():
    mesh = cap(n=16, top_deg=70)
    T = 64
    reg = hair.texel_region(mesh, T, np.ones(len(mesh["faces"]), dtype=bool))
    # strands running along increasing u (the texture's width) everywhere
    flow = hair.tangent(reg["tu"], reg["nrm"])
    fm = hairmat.flow_map(mesh, T, reg["ys"], reg["xs"], flow)
    assert fm.shape == (T, T, 4) and fm.dtype == np.uint8
    r = fm[reg["ys"], reg["xs"], 0].astype(float) / 255 * 2 - 1
    g = fm[reg["ys"], reg["xs"], 1].astype(float) / 255 * 2 - 1
    assert np.median(r) > 0.95 and abs(np.median(g)) < 0.1
    gl = hairmat.gl_flow(fm)
    assert gl.shape == (T, T, 3) and (gl[..., 1].astype(int) == 255 - fm[..., 1].astype(int)).all()
    rot = hairmat.rotate_flow(fm)
    assert np.median(rot[reg["ys"], reg["xs"], 1]) > 240  # turned a quarter: now along v


# --- 4. the GLB --------------------------------------------------------------------------------------------------------------


def tiny_glb(path: Path, names: list[str]) -> None:
    png = path.with_suffix(".png")
    Image.new("RGB", (2, 2), (200, 50, 20)).save(png)
    img = png.read_bytes()
    pad = img + b"\x00" * ((4 - len(img) % 4) % 4)
    gltf = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(pad)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(img)}],
            "images": [{"bufferView": 0, "mimeType": "image/png"}], "samplers": [{}], "textures": [{"source": 0, "sampler": 0}],
            "materials": [{"name": n, "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}} for n in names]}
    hairmat._write_glb(path, gltf, pad)


def test_glb_gets_anisotropy_on_the_hair_material_only(tmp_path: Path):
    glb = tmp_path / "a.glb"
    tiny_glb(glb, ["Knight_Body", "Knight_Hair"])
    flow = tmp_path / "flow.png"
    Image.new("RGB", (4, 4), (255, 128, 255)).save(flow)
    assert hairmat.glb_add_anisotropy(glb, flow, rotation=hairmat.anisotropy_rotation())
    assert hairmat.glb_add_anisotropy(glb, flow)  # again: replaced, still one extension name
    gltf, bin_data = hairmat._read_glb(glb)
    body, hairm = gltf["materials"]
    assert "extensions" not in body
    ext = hairm["extensions"]["KHR_materials_anisotropy"]
    assert ext["anisotropyStrength"] == pytest.approx(hairmat.ANISOTROPY_STRENGTH)
    assert gltf["extensionsUsed"].count("KHR_materials_anisotropy") == 1
    view = gltf["bufferViews"][gltf["images"][gltf["textures"][ext["anisotropyTexture"]["index"]]["source"]]["bufferView"]]
    assert bin_data[view["byteOffset"]:view["byteOffset"] + 8] == b"\x89PNG\r\n\x1a\n"
    assert hairmat.glb_materials(glb) == ["Knight_Body", "Knight_Hair"]
    one = tmp_path / "b.glb"
    tiny_glb(one, ["Knight_mat"])
    assert not hairmat.glb_add_anisotropy(one, flow)


# --- the hand-off ---------------------------------------------------------------------------------------------------------------


def test_pack_args_carry_the_hair_only_when_there_is_one():
    look = {"shading": "smooth", "texture_filter": "smooth"}
    plain = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["fbx", "glb"], look)
    assert "--hair-layout" not in plain
    a = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["fbx", "glb"], look,
                         hair={"layout": "l.json", "base_color": "hb.png", "normal": "hn.png", "orm": "ho.png"})
    i = a.index("--hair-layout")
    assert a[i + 1] == "l.json" and a[a.index("--hair-base-color") + 1] == "hb.png"
    assert a[a.index("--hair-normal") + 1] == "hn.png" and a[a.index("--hair-orm") + 1] == "ho.png"
    assert "--hair-layout" not in export.pack_args("in.glb", "out", "n", [], "none", ["glb"], look, hair={"layout": "l.json"})


def test_hand_off_lists_and_delivers_the_hair_files():
    for f in ("hair_base_color.png", "hair_normal.png", "hair_ao.png", "hair_flow.png"):
        assert f in export.HANDOFF_TEXTURES
    for k in ("hair_base_color", "hair_normal", "hair_roughness", "hair_ao", "hair_flow", "hair_metallic_smoothness", "hair_orm", "readme"):
        assert k in export.DELIVER_KEYS
    assert set(export.HAIR_KEYS) <= set(export.DELIVER_KEYS)


def test_shader_travels_with_the_importer(tmp_path: Path):
    src = tmp_path / "src" / "VgacHair.shader"
    src.parent.mkdir()
    src.write_text("// VgacHair Version 2\nShader \"VGAC/Hair\" {}\n")
    dest = tmp_path / "proj" / export.SHADER_REL
    assert export.sync_shader(src, dest)["action"] == "copied" and dest.is_file()
    assert export.sync_shader(src, dest)["action"] == "current"
    dest.write_text("// VgacHair Version 1\n")
    assert export.sync_shader(src, dest) == {"action": "updated", "version": 2, "previous": 1}
    dest.write_text("// VgacHair Version 5\n")
    assert export.sync_shader(src, dest)["action"] == "kept_newer"
    real = Path(__file__).resolve().parents[2] / "unity-sandbox" / export.SHADER_REL
    assert export.shader_version(real) is not None
    assert int(export.importer_version(Path(__file__).resolve().parents[2] / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs")) >= 13


def fake_export_sc(tmp_path: Path, prep_hm: dict, export_m: dict, files: dict[str, Path]) -> SimpleNamespace:
    prep_v = SimpleNamespace(metrics={"hair_material": prep_hm})
    version = SimpleNamespace(metrics=export_m)
    return SimpleNamespace(version=version, stage_version=lambda s: prep_v if s == "prep" else None,
                           file=lambda k: files.get(k), dir=tmp_path)


def test_manifest_names_the_hair_material_and_its_files(tmp_path: Path):
    files = {}
    for k in export.HAIR_KEYS + export.HAIR_PACK_KEYS:
        p = tmp_path / f"{k}.png"
        Image.new("RGB", (8, 8)).save(p)
        files[k] = p
    sc = fake_export_sc(tmp_path, {"use": True, "tex": 1024, "texels_per_cm": 21.5, "texels_per_cm_body_texture": 6.8},
                        {"hair_material": {"ok": True, "slots": ["K_Body", "K_Hair"], "faces": 1500, "lod_triangles": {"LOD0": 1500},
                                           "draws_added": 1, "anisotropy_glbs": ["K.glb"]}}, files)
    block = export._hair_manifest(sc, {"hairMaterial": "Assets/VGAC/K/K_Hair.mat", "hairShader": "VGAC/Hair"})
    assert block["materials"] == ["K_Body", "K_Hair"] and block["texels_per_cm"] == 21.5 and block["draw_calls_added"] == 1
    assert block["flow_map"] == "hair_flow.png" and block["files"]["hair_orm"] == "hair_orm.png"
    assert block["unity"]["shader"] == "VGAC/Hair" and "not verified" in block["unreal"] and "not verified" in block["godot"]
    none = fake_export_sc(tmp_path, {"use": False}, {"hair_material": None}, files)
    assert export._hair_manifest(none, None) is None
    text = export.hair_readme("K", {"tex": 1024}, block["files"])
    assert "Unreal" in text and "Godot" in text and "Shading Model to Hair" in text and "Anisotropy" in text
    assert "Not tried here" in text


def test_shipped_check_wants_two_materials(tmp_path: Path):
    glb = tmp_path / "k.glb"
    tiny_glb(glb, ["K_Body", "K_Hair"])
    m = {"hair_material": {"planned": True, "ok": True, "slots": ["K_Body", "K_Hair"]}, "unity_report": {"ok": True, "hairMaterial": "x", "hairShader": "VGAC/Hair"}}
    (c,) = export.hair_shipped_checks(m, glb)
    assert c["passed"] and "VGAC/Hair" in c["help"] and "one more draw" in c["help"].lower()
    one = tmp_path / "o.glb"
    tiny_glb(one, ["K_mat"])
    (bad,) = export.hair_shipped_checks(m, one)
    assert not bad["passed"] and bad["fix"]["stage"] == "export"
    (no_unity,) = export.hair_shipped_checks({**m, "unity_report": {"ok": True}}, glb)
    assert not no_unity["passed"] and "Unity" in no_unity["help"]
    assert export.hair_shipped_checks({"hair_material": None}, glb) == []


# --- the check sentences -----------------------------------------------------------------------------------------------------------


def test_material_check_sentences():
    used = {"hair_detail": {"ran": True, "texels_per_cm_before": 6.8, "texels_per_cm_after": 6.8},
            "hair_material": {"applies": True, "use": True, "tex": 1024, "texels_per_cm": 21.5, "texels_per_cm_body_texture": 6.8,
                              "body_texels_per_cm": 6.7, "body_texels_per_cm_before": 6.6}}
    checks = hd.hair_checks(used)
    names = [c["name"] for c in checks]
    assert names == ["hair_matches_picture", "hair_material"]
    matches, mat = checks
    assert "21.5 now" in matches["help"]  # the hair's texels are those of its own texture
    assert mat["passed"] and "1024 px texture" in mat["help"] and "21.5" in mat["help"] and "one more draw call" in mat["help"]
    assert "6.7 texels per cm (6.6 before)" in mat["help"]
    fell = hd.material_checks({"hair_material": {"applies": True, "use": False, "reason": "doubtful"}})
    assert fell[0]["passed"] and "was not used" in fell[0]["help"] and "confidence" in fell[0]["help"] and "{" not in fell[0]["help"]
    err = hd.material_checks({"hair_material": {"applies": True, "use": False, "reason": "error"}})
    assert not err[0]["passed"] and err[0]["fix"]["params"] == {"hair_material": "on"}
    for na in ({"applies": False, "reason": "phone"}, {"applies": False, "reason": "off"}, {}):
        assert hd.material_checks({"hair_material": na}) == []
    assert "hair_material" in guidance.CHECK_GUIDE and "hair_material_shipped" in guidance.CHECK_GUIDE


# --- round 2: one set of 3D fields for both textures --------------------------------------------------------------------


def test_paint_tris_writes_only_the_body_band_and_returns_fields():
    from test_hair_detail import maps as dome_maps

    mesh = cap(n=24, top_deg=85)
    T = 128
    mp = dome_maps(T)
    tris = np.ones(len(mesh["faces"]), dtype=bool)
    band = mesh["verts"][mesh["faces"]][:, :, 1].min(axis=1) < 0.03  # the lowest rows stay in the body texture
    r = hair.paint_hair(mesh, mp, tris, centres(), 0, 1, seed=3, paint_tris=band, strand_cell=hair.STRAND_CELL)
    uvp = face.uv_pixels(mesh["uv"], T)[mesh["faces"]].mean(axis=1)
    changed = np.abs(r["maps"]["base_color"] - mp["base_color"]).sum(axis=2) > 1e-6
    reg = hair.texel_region(mesh, T, tris)
    on_band = band[reg["tri"]]
    assert changed[reg["ys"][on_band], reg["xs"][on_band]].mean() > 0.5
    assert not changed[reg["ys"][~on_band], reg["xs"][~on_band]].any()
    f = r["fields"]
    assert {"grid", "F", "tip", "root", "crown", "s_cell"} <= set(f)
    assert r["measures"]["texels_written"] == int(on_band.sum()) or r["measures"]["texels_written"] <= int(on_band.sum())
    # the other texture reads the same roots and tips at the same places
    tip = hair.read_field(f["tip"], reg["pos"])
    assert tip.shape == (len(reg["pos"]),) and (tip >= 0).all() and (tip <= 1).all()
    r2 = hair.paint_hair(mesh, mp, tris, centres(), 0, 1, seed=3, fields=f, strand_cell=hair.STRAND_CELL)
    assert r2["measures"]["strand_mm"] == pytest.approx(1000 * hair.STRAND_CELL, abs=0.01)
    assert r2["measures"]["painted"]


def test_metal_goes_on_hair_inside_the_face_region_too():
    from test_hair_detail import maps as dome_maps

    mesh = cap(n=24, top_deg=85)
    T = 128
    mp = dome_maps(T)
    protect = np.ones((T, T))
    r = hair.paint_hair(mesh, mp, np.ones(len(mesh["faces"]), dtype=bool), centres(), 0, 1, seed=3, protect=protect)
    assert np.array_equal(r["maps"]["base_color"], mp["base_color"])  # the face's colours are kept
    assert (r["maps"]["metallic"][r["mask"]] < 0.01).all()  # but no hair texel keeps the 3D step's metal


def test_streamlines_from_cells_follow_the_exact_ones():
    rng = np.random.default_rng(0)
    pos = rng.uniform(-0.05, 0.05, (4000, 3))
    flow = np.tile(np.array([0.0, -1.0, 0.2]), (len(pos), 1))
    grid, F = hair.smooth_flow(pos, flow)
    a = hair.streamlines(pos, grid, F, 0.0025, 6)
    b = hair.streamlines_cells(pos, grid, F, 0.0025, 6)
    assert max(float(np.abs(x - y).max()) for x, y in zip(a[0], b[0])) < 0.002


def test_into_body_carries_the_hair_texture_onto_its_triangles_only():
    mesh = cap(n=16, top_deg=80)
    T = 128
    f = mesh["faces"]
    sel = mesh["verts"][f][:, :, 1].min(axis=1) > 0.05
    layout = {"tex": 64, "centres": mesh["verts"][f[sel]].mean(axis=1), "corners": mesh["verts"][f[sel]], "uv": mesh["uv"][f[sel]]}
    body = {"base_color": np.full((T, T, 3), 0.5), "metallic": np.ones((T, T)), "roughness": np.full((T, T), 0.2)}
    hair_maps = {"base_color": np.tile(np.array([0.1, 0.2, 0.9]), (64, 64, 1)), "roughness": np.full((64, 64), 0.6)}
    out = hairmat.into_body(mesh, body, sel, layout, hair_maps, 20.0, 10.0)
    reg_in = hair.texel_region(mesh, T, sel)
    reg_out = hair.texel_region(mesh, T, ~sel)
    assert np.allclose(out["base_color"][reg_in["ys"], reg_in["xs"]], [0.1, 0.2, 0.9], atol=0.02)
    assert (out["metallic"][reg_in["ys"], reg_in["xs"]] == 0).all()
    assert np.allclose(out["roughness"][reg_in["ys"], reg_in["xs"]], 0.6, atol=0.02)
    only_out = ~np.isin(np.arange(len(reg_out["ys"])), [])  # texels of the other triangles keep the body's values
    ys, xs = reg_out["ys"][only_out], reg_out["xs"][only_out]
    keep = ~np.zeros((T, T), dtype=bool)
    keep[reg_in["ys"], reg_in["xs"]] = False
    k = keep[ys, xs]
    assert np.allclose(out["base_color"][ys[k], xs[k]], 0.5)
