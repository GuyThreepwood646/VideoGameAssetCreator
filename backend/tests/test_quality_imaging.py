import json

import numpy as np
from PIL import Image

from vgac import imaging, quality
from vgac.graphs.material import derive_maps, make_seamless, seam_error
from vgac.stages.export import lod_list, parse_lods


def _rgba(path, w=64, h=64, box=(16, 8, 48, 56), colour=(200, 50, 50)):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            im.putpixel((x, y), colour + (255,))
    im.save(path)
    return path


def test_silhouette_iou_and_alignment(tmp_path):
    a = _rgba(tmp_path / "a.png")
    b = _rgba(tmp_path / "b.png", box=(10, 10, 54, 54))
    sa, sb = imaging.silhouette(a, 64), imaging.silhouette(b, 64)
    assert imaging.iou(sa, sa) == 1.0
    assert 0.0 < imaging.iou(sa, sb) <= 1.0
    refs = {"front": sa, "left": sb, "back": sa, "right": sb}
    rendered = {yaw: (sa if yaw in (0, 180) else sb) for yaw in range(0, 360, 15)}
    best = imaging.best_alignment(refs, rendered)
    assert best["mean"] == 1.0 and best["offset"] == 0
    stats = imaging.alpha_bbox(a)
    assert stats["width"] == 32 and stats["height"] == 48 and not stats["empty"]
    assert imaging.mirror_iou(a, a) > 0.99


def test_histograms_and_masks(tmp_path):
    a = _rgba(tmp_path / "a.png")
    b = _rgba(tmp_path / "b.png", colour=(50, 50, 200))
    assert imaging.histogram_distance(imaging.colour_histogram(a), imaging.colour_histogram(a)) == 0.0
    assert imaging.histogram_distance(imaging.colour_histogram(a), imaging.colour_histogram(b)) > 0.9
    white = imaging.white_composite(a)
    assert Image.open(white).getpixel((0, 0)) == (255, 255, 255)
    mask = Image.new("L", (64, 64), 0)
    for x in range(32, 64):
        for y in range(64):
            mask.putpixel((x, y), 255)
    mask.save(tmp_path / "m.png")
    out = imaging.apply_mask(a, b, tmp_path / "m.png", tmp_path / "out.png")
    o = Image.open(out)
    assert o.getpixel((20, 20))[:3] == (200, 50, 50) and o.getpixel((40, 20))[:3] == (50, 50, 200)
    sheet = imaging.contact_sheet([a, b], tmp_path / "sheet.png", cell=32, columns=2)
    assert Image.open(sheet).size == (64, 48)


def test_quality_checks_shape():
    report = {"lods": {"LOD0": 20000, "LOD1": 10000, "LOD2": 5000, "LOD3": 2000}, "collider_verts": 54, "non_manifold_edges": 0,
              "boundary_edges": 0, "dims_m": [0.8, 0.4, 1.8], "lod_ratios": [0.5, 0.25, 0.1]}
    checks = quality.geometry_checks(report, 20000, 1.8) + quality.lod_checks(report, [0.5, 0.25, 0.1]) + quality.collider_checks(report)
    for c in checks:
        assert set(c) >= {"name", "passed", "message", "id", "ok", "value", "limit"}
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]
    bad = quality.geometry_checks({**report, "non_manifold_edges": 200, "lods": {"LOD0": 30000}}, 20000, 1.8)
    failed = {c["name"] for c in bad if not c["passed"]}
    assert failed == {"triangle_budget", "manifold"}
    rig = quality.rig_checks({"bones": 31, "roots": ["root"], "verts": 100, "weighted_verts": 100, "max_influences": 4, "unnormalised_verts": 0}, True)
    assert all(c["passed"] for c in rig)
    assert quality.rig_checks({"bones": 0}, False)[0]["passed"]
    summary = quality.summarise(bad)
    assert summary["gate_ok"] is False and set(summary["checks_failed"]) == failed


def test_texture_checks(tmp_path):
    Image.new("RGB", (2048, 2048)).save(tmp_path / "base_color.png")
    Image.new("RGB", (1000, 2048)).save(tmp_path / "normal.png")
    checks = quality.texture_checks({"base_color": tmp_path / "base_color.png", "normal": tmp_path / "normal.png"}, 2048)
    by = {c["name"]: c["passed"] for c in checks}
    assert by["texture_base_color_pow2"] and by["texture_base_color_square"] and by["texture_base_color_budget"]
    assert not by["texture_normal_pow2"] and not by["texture_normal_square"]


def test_material_seamless_and_maps(tmp_path):
    rng = np.random.default_rng(0)
    arr = (rng.random((128, 128, 3)) * 255).astype(np.uint8)
    arr[:, :64] = (arr[:, :64] // 2) + 100  # a visible left-right difference so the raw image has a seam
    Image.fromarray(arr).save(tmp_path / "raw.png")
    raw_err = seam_error(tmp_path / "raw.png")
    base = make_seamless(tmp_path / "raw.png", tmp_path / "base_color.png")
    assert seam_error(base) < raw_err
    maps = derive_maps(base, tmp_path, preview_size=256)
    for key in ("height", "normal", "roughness", "metallic", "tile_preview"):
        assert maps[key].is_file()
    assert Image.open(maps["tile_preview"]).size == (256, 256)
    assert Image.open(maps["normal"]).mode == "RGB"


def test_lod_param_parsing():
    assert parse_lods("1.0, 0.5, 0.25, 0.1") == [0.5, 0.25, 0.1]
    assert parse_lods([0.5, 0.25]) == [0.5, 0.25]
    assert parse_lods("") is None and parse_lods(None) is None
    lods = lod_list({"LOD0": 100, "LOD1": 50, "LOD2": 25})
    assert lods == [{"level": 0, "triangles": 100, "screen_height": 0.6}, {"level": 1, "triangles": 50, "screen_height": 0.3}, {"level": 2, "triangles": 25, "screen_height": 0.12}]


def test_sprite_sheet(tmp_path):
    frames = [_rgba(tmp_path / f"f{i}.png") for i in range(3)]
    png, js = imaging.sprite_sheet(frames, tmp_path / "s.png", tmp_path / "s.json", 32, ["a", "b", "c"])
    atlas = json.loads(js.read_text())
    assert atlas["frames"]["c"] == {"x": 64, "y": 0, "w": 32, "h": 32}
    assert Image.open(png).size == (96, 32)
