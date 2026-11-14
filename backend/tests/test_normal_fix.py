"""The 3D step's normal map (bump texture) the right way up (vgac/normalfix.py, docs/KNOWN-FAULTS-RESEARCH.md 1): the
green flip at download (once, never twice), the marker, the clean-up step's repair of older 3D-step versions on copies,
the normal_map_upright check and its sentences, and skin never metal (vgac/skinmetal.py)."""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from vgac import guidance, normalfix, skinmetal
from vgac.stages import prep as prep_stage

trimesh = pytest.importorskip("trimesh")


def _gltf(path: Path) -> tuple[dict, bytes]:
    data = Path(path).read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    g = json.loads(data[20:20 + n])
    bl = struct.unpack_from("<I", data, 20 + n)[0]
    return g, data[28 + n: 28 + n + bl]


def _image(path: Path, which: str) -> np.ndarray:
    import io

    g, b = _gltf(path)
    m = g["materials"][0]
    ref = m["normalTexture"] if which == "normal" else m["pbrMetallicRoughness"]["baseColorTexture"]
    bv = g["bufferViews"][g["images"][g["textures"][ref["index"]]["source"]]["bufferView"]]
    o = bv.get("byteOffset", 0)
    return np.asarray(Image.open(io.BytesIO(b[o:o + bv["byteLength"]])).convert("RGB"))


def _glb(path: Path, normal: np.ndarray | None, base: np.ndarray) -> Path:
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], float)
    f = np.array([[0, 2, 1], [0, 3, 2]])
    uv = np.array([[0.05, 0.95], [0.95, 0.95], [0.95, 0.05], [0.05, 0.05]])
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(base),
                                              normalTexture=Image.fromarray(normal) if normal is not None else None)
    trimesh.Trimesh(v, f, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False).export(path)
    return path


def _maps(seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (32, 32, 3), dtype=np.uint8), rng.integers(0, 255, (32, 32, 3), dtype=np.uint8)


# --- the flip -------------------------------------------------------------------------------------------------------


def test_flip_green_png_inverts_green_only_and_twice_is_the_original(tmp_path):
    nm, _ = _maps()
    p = tmp_path / "normal.png"
    Image.fromarray(nm).save(p)
    normalfix.flip_green_png(p, tmp_path / "once.png")
    once = np.asarray(Image.open(tmp_path / "once.png").convert("RGB"))
    assert (once[..., 0] == nm[..., 0]).all() and (once[..., 2] == nm[..., 2]).all()
    assert (once[..., 1] == 255 - nm[..., 1]).all()
    normalfix.flip_green_png(tmp_path / "once.png")  # in place
    assert (np.asarray(Image.open(tmp_path / "once.png").convert("RGB")) == nm).all()


def test_glb_flip_changes_the_normal_image_only(tmp_path):
    nm, base = _maps()
    glb = _glb(tmp_path / "m.glb", nm, base)
    assert normalfix.glb_flip_normal_green(glb) == 1
    got = _image(glb, "normal")
    assert (got[..., 1] == 255 - nm[..., 1]).all() and (got[..., [0, 2]] == nm[..., [0, 2]]).all()
    assert (_image(glb, "base") == base).all()
    import trimesh as tm

    assert len(tm.load(str(glb), force="mesh").faces) == 2  # still a valid GLB


def test_glb_flip_to_a_copy_leaves_the_source_and_a_glb_without_normal_is_left_alone(tmp_path):
    nm, base = _maps()
    glb = _glb(tmp_path / "m.glb", nm, base)
    before = glb.read_bytes()
    assert normalfix.glb_flip_normal_green(glb, tmp_path / "copy.glb") == 1
    assert glb.read_bytes() == before
    plain = _glb(tmp_path / "plain.glb", None, base)
    raw = plain.read_bytes()
    assert normalfix.glb_flip_normal_green(plain) == 0 and plain.read_bytes() == raw


def test_marker_respected_and_only_trellis_with_a_normal_map_needs_it():
    assert normalfix.needs_fix({"engine": "trellis"}, {}, True)
    assert normalfix.needs_fix({}, {}, True)  # TRELLIS is the default builder
    assert not normalfix.needs_fix({"engine": "trellis"}, {"normal_fix": normalfix.NORMAL_FIX}, True)
    assert not normalfix.needs_fix({"engine": "trellis"}, {}, False)
    assert not normalfix.needs_fix({"engine": "hy3d"}, {}, True)
    assert not normalfix.needs_fix({"engine": "material"}, {}, True)


def test_fix_download_flips_both_once_and_records_the_marker(tmp_path):
    nm, base = _maps()
    glb = _glb(tmp_path / "model.glb", nm, base)
    png = tmp_path / "normal.png"
    Image.fromarray(nm).save(png)
    m = normalfix.fix_download(glb, png)
    assert m == {"normal_fix": normalfix.NORMAL_FIX, "normal_fix_glb_images": 1}
    assert (np.asarray(Image.open(png).convert("RGB"))[..., 1] == 255 - nm[..., 1]).all()
    assert (_image(glb, "normal")[..., 1] == 255 - nm[..., 1]).all()
    assert not normalfix.needs_fix({"engine": "trellis"}, m, True)  # a marked version is never flipped again


# --- the clean-up step on an older 3D-step version ----------------------------------------------------------------


class _SC:
    def __init__(self, d: Path, mesh_dir: Path):
        self.dir = d
        self.mesh_dir = mesh_dir
        self.metrics: dict = {}

    def set_metrics(self, **kw):
        self.metrics.update(kw)

    def lineage_file(self, key, stages):
        p = self.mesh_dir / ("model.glb" if key == "glb" else f"{key}.png")
        return p if p.is_file() else None


def _mesh_version(tmp_path: Path, metrics: dict) -> tuple[SimpleNamespace, Path, Path, np.ndarray]:
    nm, base = _maps()
    md = tmp_path / "mesh"
    md.mkdir()
    glb = _glb(md / "model.glb", nm, base)
    png = md / "normal.png"
    Image.fromarray(nm).save(png)
    return SimpleNamespace(params={"engine": "trellis"}, metrics=metrics), glb, png, nm


def test_prep_repairs_an_older_version_on_copies_and_never_touches_the_stored_files(tmp_path):
    mv, glb, png, nm = _mesh_version(tmp_path, {})
    stored = (glb.read_bytes(), png.read_bytes())
    pd = tmp_path / "prep"
    pd.mkdir()
    sc = _SC(pd, glb.parent)
    rep = asyncio.run(prep_stage.repair_normal(sc, mv, glb, {"normal": png}, True))
    assert rep is not None and rep["glb"].parent == pd / prep_stage.NORMAL_FIX_DIR
    assert (np.asarray(Image.open(rep["normal"]).convert("RGB"))[..., 1] == 255 - nm[..., 1]).all()
    assert (_image(rep["glb"], "normal")[..., 1] == 255 - nm[..., 1]).all()
    assert (glb.read_bytes(), png.read_bytes()) == stored
    assert sc.metrics["normal_repaired"] is True
    assert prep_stage.bake_inputs(sc) == (rep["glb"], rep["normal"])


def test_prep_leaves_a_marked_version_and_a_look_without_bumps_alone(tmp_path):
    mv, glb, png, _ = _mesh_version(tmp_path, {"normal_fix": normalfix.NORMAL_FIX})
    pd = tmp_path / "prep"
    pd.mkdir()
    sc = _SC(pd, glb.parent)
    assert asyncio.run(prep_stage.repair_normal(sc, mv, glb, {"normal": png}, True)) is None
    old = SimpleNamespace(params={"engine": "trellis"}, metrics={})
    assert asyncio.run(prep_stage.repair_normal(sc, old, glb, {"normal": png}, False)) is None
    assert asyncio.run(prep_stage.repair_normal(sc, old, glb, {}, True)) is None
    assert not (pd / prep_stage.NORMAL_FIX_DIR).exists() and not sc.metrics
    assert prep_stage.bake_inputs(sc) == (glb, png)


# --- the check -------------------------------------------------------------------------------------------------------


def _baked_sphere(tmp_path: Path, size: int = 1024) -> tuple[Path, Path]:
    """An icosphere with every triangle its own texture piece, turned at random in the texture, and a normal map baked
    the right way up (OpenGL) from a bumpy surface over it."""
    from vgac import face, hair

    ico = trimesh.creation.icosphere(subdivisions=2)
    f0 = np.asarray(ico.faces)
    verts = np.asarray(ico.vertices)[f0.reshape(-1)]
    faces = np.arange(len(verts)).reshape(-1, 3)
    rng = np.random.default_rng(3)
    cols = int(np.ceil(np.sqrt(len(faces))))
    cell = 1.0 / cols
    uv = np.zeros((len(verts), 2))
    for i in range(len(faces)):
        cx, cy = (i % cols + 0.5) * cell, (i // cols + 0.5) * cell
        a = rng.uniform(0, 2 * np.pi)
        for k in range(3):
            ang = a + k * 2 * np.pi / 3
            uv[3 * i + k] = (cx + 0.36 * cell * np.cos(ang), cy + 0.36 * cell * np.sin(ang) * (1 if k != 1 else 1))
    glb = tmp_path / "sphere.glb"
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.new("RGB", (8, 8), (200, 160, 140)))
    trimesh.Trimesh(verts, faces, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False).export(glb)
    mesh = face.load_mesh(glb)
    uvp = face.uv_pixels(mesh["uv"], size)
    ras = face.rasterise(uvp, np.zeros(len(uvp)), mesh["faces"], size)
    cov = ras["tri"] >= 0
    p = face.interpolate(mesh["verts"], mesh["faces"], ras)
    n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)
    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    high = p / np.maximum(np.linalg.norm(p, axis=-1, keepdims=True), 1e-12) + 0.35 * np.stack(
        [np.sin(7 * p[..., 1]), np.sin(7 * p[..., 2]), np.sin(7 * p[..., 0])], -1)
    high /= np.maximum(np.linalg.norm(high, axis=-1, keepdims=True), 1e-12)
    tu, tv = hair.uv_jacobian(mesh, size)
    t = np.where(cov, ras["tri"], 0)
    ax = tu[t] - (tu[t] * n).sum(-1, keepdims=True) * n
    ax /= np.maximum(np.linalg.norm(ax, axis=-1, keepdims=True), 1e-12)
    up = tv[t] - (tv[t] * n).sum(-1, keepdims=True) * n - (tv[t] * ax).sum(-1, keepdims=True) * ax
    up /= np.maximum(np.linalg.norm(up, axis=-1, keepdims=True), 1e-12)
    ts = np.stack([(high * ax).sum(-1), (high * up).sum(-1), (high * n).sum(-1)], -1)
    img = np.where(cov[..., None], ts * 0.5 + 0.5, np.array([0.5, 0.5, 1.0]))
    # grow each piece a few texels into its gutter, as the 3D step does
    for _ in range(4):
        grow = face.dilate_mask(cov, 1) & ~cov
        ys, xs = np.nonzero(grow)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            yy, xx = np.clip(ys + dy, 0, size - 1), np.clip(xs + dx, 0, size - 1)
            ok = cov[yy, xx] & grow[ys, xs]
            img[ys[ok], xs[ok]] = img[yy[ok], xx[ok]]
            grow[ys[ok], xs[ok]] = False
            cov = cov.copy()
            cov[ys[ok], xs[ok]] = True
    png = tmp_path / "normal.png"
    Image.fromarray((np.clip(img, 0, 1) * 255).round().astype(np.uint8)).save(png)
    return glb, png


def test_upright_tells_a_right_map_from_an_upside_down_one(tmp_path):
    glb, png = _baked_sphere(tmp_path)
    right = normalfix.upright(glb, png)
    assert right is not None and right["seams"] > 100
    assert not right["upside_down"] and right["ratio"] < 0.8
    normalfix.flip_green_png(png, tmp_path / "wrong.png")
    wrong = normalfix.upright(glb, tmp_path / "wrong.png")
    assert wrong["upside_down"] and wrong["ratio"] > normalfix.UPSIDE_DOWN_RATIO
    assert normalfix.check(right, "mesh")["passed"] and not normalfix.check(wrong, "mesh")["passed"]
    assert normalfix.check(None, "mesh") is None


def test_check_sentences_and_fixes_per_step():
    ok = normalfix.check({"ratio": 0.57, "upside_down": False}, "mesh")
    assert ok["severity"] == "warn" and "right way up" in ok["help"] and "fix" not in ok
    bad = normalfix.check({"ratio": 1.77, "upside_down": True}, "mesh")
    assert bad["help"].startswith("The normal map (bump texture) is upside down")
    assert bad["fix"]["stage"] == "mesh" and bad["fix_label"] == "Build the 3D model again"
    bad_prep = normalfix.check({"ratio": 1.7, "upside_down": True}, "prep")
    assert bad_prep["fix"]["stage"] == "prep" and bad_prep["fix_label"] == "Clean up again"
    assert "Cleaning up again" in bad_prep["help"]
    assert "normal_map_upright" in guidance.CHECK_GUIDE


# --- skin is never metal -------------------------------------------------------------------------------------------


def test_metal_bodies_keep_their_metal():
    assert skinmetal.metal_body("A rusty robot knight")
    assert skinmetal.metal_body("a golem made of iron") and skinmetal.metal_body("a statue of a king")
    assert skinmetal.metal_body("a warrior with bronze skin")
    assert not skinmetal.metal_body("A stylized fantasy knight in steel plate armour with a metal collar")
    assert not skinmetal.metal_body(None)


def test_clear_skips_and_never_raises(tmp_path):
    r = skinmetal.clear(tmp_path / "none.glb", {}, "quadruped", "a wolf", tmp_path)
    assert r == {"files": {}, "metrics": {"skin_metal": {"ran": False, "skipped": "not a humanoid", "seconds": r["metrics"]["skin_metal"]["seconds"]}}}
    assert skinmetal.clear(tmp_path / "none.glb", {}, "humanoid", "a robot", tmp_path)["metrics"]["skin_metal"]["skipped"].startswith("the description")
    bad = skinmetal.clear(tmp_path / "none.glb", {"metallic": tmp_path / "x.png", "base_color": tmp_path / "y.png"}, "humanoid", "a knight", tmp_path)
    assert bad["files"] == {} and bad["metrics"]["skin_metal"]["ran"] is False


def test_clear_zeroes_the_skin_texels_and_keeps_the_rest(tmp_path, monkeypatch):
    nm, base = _maps()
    glb = _glb(tmp_path / "m.glb", nm, base)
    met = np.full((32, 32), 200, np.uint8)
    Image.fromarray(met, "L").save(tmp_path / "metallic.png")
    Image.fromarray(base).save(tmp_path / "base_color.png")
    ys, xs = np.array([10, 10, 11]), np.array([5, 6, 5])
    region = {"covered": np.zeros((32, 32), bool)}
    none = np.zeros(0, dtype=np.int64)
    monkeypatch.setattr(skinmetal, "skin_texels", lambda mesh, b, T, body=False: {
        "ys": ys, "xs": xs, "face_texels": 2, "skin_lab": [60, 15, 20], "region": region, "head_texels": 3, "body_texels": 0,
        "body_pieces": 0, "body_small_pieces": 0, "left_ys": none, "left_xs": none, "covered": None})
    # armour in the description: only the head and neck are cleared (a map that is metal everywhere would otherwise be wiped)
    r = skinmetal.clear(glb, {"metallic": tmp_path / "metallic.png", "base_color": tmp_path / "base_color.png"}, "humanoid",
                        "a knight in steel plate armour", tmp_path / "skin")
    out = np.asarray(Image.open(r["files"]["metallic"]))
    assert (out[ys, xs] == 0).all()
    keep = np.ones((32, 32), bool)
    keep[ys, xs] = False
    keep &= ~(np.abs(np.arange(32)[:, None] - 10.5) <= 4) | ~(np.abs(np.arange(32)[None, :] - 5.5) <= 4)  # the spill ring
    assert (out[keep] == 200).all()
    rec = r["metrics"]["skin_metal"]
    assert rec["ran"] and rec["skin_texels"] == 3 and rec["metal_before"] == pytest.approx(200 / 255, abs=0.01) and rec["metal_after"] == 0.0
    assert (np.asarray(Image.open(tmp_path / "metallic.png")) == 200).all()  # the input map is never changed


def test_upright_check_measures_once_and_keeps_the_measure(tmp_path, monkeypatch):
    from vgac.stages import mesh as mesh_stage

    glb, png = _baked_sphere(tmp_path)
    metrics: dict = {}
    sc = SimpleNamespace(version=SimpleNamespace(metrics=metrics), set_metrics=lambda **kw: metrics.update(kw))
    c = asyncio.run(mesh_stage.upright_check(sc, "mesh", glb, png))
    assert c["passed"] and metrics["normal_upright"]["ratio"] < 1
    monkeypatch.setattr(normalfix, "upright", lambda *a, **k: pytest.fail("measured twice"))
    assert asyncio.run(mesh_stage.upright_check(sc, "mesh", glb, png))["passed"]
    assert asyncio.run(mesh_stage.upright_check(SimpleNamespace(version=SimpleNamespace(metrics={}), set_metrics=lambda **kw: None),
                                                "mesh", glb, None)) is None
