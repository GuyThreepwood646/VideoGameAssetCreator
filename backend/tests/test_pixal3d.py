"""The Pixal3D four-view builder in step 3 (graphs/pixal3d.py, vgac/pixal3d.py, stages/mesh.py) and the painting option
that leaves the two three-quarter pictures out (srcproject.QUARTER_PARAM, stages/face_detail.side_pictures)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from vgac import pixal3d, reference, srcproject
from vgac.graphs.pixal3d import FOV_NODE, MOGE_MODEL, PIXAL3D_ENCODER, PIXAL3D_MODEL, PIXAL3D_VIEWS, pixal3d_graph
from vgac.graphs.trellis import IMAGE_NODES, convert_graph
from vgac.stages import face_detail
from vgac.stages import mesh as mesh_stage

NAMES = {v: f"{v}.png" for v in PIXAL3D_VIEWS}


# --- the graph -------------------------------------------------------------------------------------------------------


def test_graph_wires_four_views_and_the_front_pictures_own_camera_angle():
    g = pixal3d_graph(NAMES, "p/x", target_faces=30000, tex=2048, upsample=1536, seed=7, padding=16)
    cond = g["cond"]
    assert cond["class_type"] == "Pixal3DMultiViewConditioning"
    assert cond["inputs"] == {"clip_vision_model": ["cv", 0], "fov": ["fov", 0], "front": ["crop", 0],
                              "left": ["crop_left", 0], "back": ["crop_back", 0], "right": ["crop_right", 0]}
    # the camera angle is MoGe-2's estimate on the framed FRONT crop, horizontal, in degrees; never the node's 20 default
    assert g["fov"] == {"class_type": "MoGeGeometryToFOV", "inputs": {"moge_geometry": ["mogei", 0], "axis": "horizontal", "unit": "degrees"}}
    assert g["mogei"]["class_type"] == "MoGeInference" and g["mogei"]["inputs"]["image"] == ["crop", 0]
    assert g["mogei"]["inputs"]["moge_model"] == ["moge", 0] and g["moge"]["inputs"]["model_name"] == MOGE_MODEL
    assert g[FOV_NODE] == {"class_type": "PreviewAny", "inputs": {"source": ["fov", 0]}}
    assert g["cv"]["inputs"]["clip_name"] == PIXAL3D_ENCODER
    assert g["unet"]["inputs"]["unet_name"] == PIXAL3D_MODEL
    for v in ("left", "back", "right"):
        assert g[f"img_{v}"]["inputs"]["image"] == f"{v}.png"
        assert g[f"inv_{v}"]["inputs"]["mask"] == [f"img_{v}", 1]
        # framed like the front: same size, pad and background, so every view is at the same scale
        assert {k: x for k, x in g[f"crop_{v}"]["inputs"].items() if k not in ("images", "masks")} == \
            {k: x for k, x in g["crop"]["inputs"].items() if k not in ("images", "masks")}
    assert g["img"]["inputs"]["image"] == "front.png"
    assert not any(n["class_type"] == "Trellis2Conditioning" for n in g.values())


def test_graph_keeps_the_trellis_output_contract_and_settings():
    kw = dict(target_faces=30000, tex=2048, upsample=1536, seed=7, padding=16, tex_steps=24)
    g, t = pixal3d_graph(NAMES, "p/x", **kw), convert_graph("front.png", "p/x", **kw)
    changed = {"cv", "unet", "cond"}
    for k, node in t.items():  # every TRELLIS.2 node is there; only the encoder, the model and the conditioning differ
        assert k in g
        if k not in changed:
            assert g[k] == node, k
    assert g["glb"]["class_type"] == "SaveGLB"
    for node in IMAGE_NODES:
        assert g[node]["class_type"] == "SaveImage"


def test_graph_needs_all_four_views():
    with pytest.raises(ValueError, match="back"):
        pixal3d_graph({"front": "f.png", "left": "l.png", "right": "r.png"}, "p/x")


def test_graph_nodes_are_known_to_the_setup_check():
    from vgac import preflight

    found = [x for x in preflight.graph_nodes() if x["what"].startswith("the Pixal3D")]
    assert found and not found[0]["required"]
    assert {"Pixal3DMultiViewConditioning", "MoGeGeometryToFOV", "LoadMoGeModel", "MoGeInference"} <= set(found[0]["classes"])


# --- the files-present gate ------------------------------------------------------------------------------------------


def _builder_options():
    return next(p for p in reference._schema("mesh") if p["name"] == "builder")["options"]


def test_pixal3d_offered_only_when_its_files_are_present(monkeypatch):
    monkeypatch.setattr(pixal3d, "available", lambda fresh=False: False)
    assert _builder_options() == ["trellis2"]
    with pytest.raises(ValueError):
        reference.validate_params("mesh", {"builder": "pixal3d"}, "humanoid")
    monkeypatch.setattr(pixal3d, "available", lambda fresh=False: True)
    assert _builder_options() == ["trellis2", "pixal3d"]
    assert reference.validate_params("mesh", {"builder": "pixal3d"}, "humanoid")["builder"] == "pixal3d"
    assert reference.stage_defaults("mesh")["builder"] == "trellis2"


def test_files_are_found_in_subfolders_of_each_type_folder(tmp_path):
    root = tmp_path / "models"
    for typ, name in pixal3d.FILES.items():
        (root / typ / "sub").mkdir(parents=True)
        (root / typ / "sub" / name).write_bytes(b"x")
    roots = {"bases": [root], "typed": {}}
    assert pixal3d._search(roots) == []
    (root / "geometry_estimation" / "sub" / MOGE_MODEL).unlink()
    assert pixal3d._search(roots) == [MOGE_MODEL]
    # a folder an extra-model-paths file names for the type counts too
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / MOGE_MODEL).write_bytes(b"x")
    assert pixal3d._search({"bases": [root], "typed": {"geometry_estimation": [other]}}) == []


def test_no_linked_comfyui_offers_nothing(monkeypatch):
    from vgac import downloader

    monkeypatch.setattr(downloader, "models_root", lambda: None)
    assert pixal3d.missing_files(fresh=True) == list(pixal3d.FILES.values())
    assert not pixal3d.available()


# --- the builder choice in step 3 ------------------------------------------------------------------------------------


def _rgba(path: Path) -> Path:
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(16, 48):
        for y in range(8, 60):
            im.putpixel((x, y), (200, 150, 40, 255))
    im.save(path)
    return path


class _SC:
    def __init__(self, d: Path, params: dict, files: dict):
        self.dir = d
        self.asset = SimpleNamespace(category="humanoid", id="asset0001")
        self.category = {"input_view_default": "concept"}
        self.budget = {}
        self.version = SimpleNamespace(params=dict(params), metrics={}, id="ver000001", stage="mesh")
        self.files = files
        self.registered: dict = {}
        self.models: list = []
        self.staged: list = []

    def lineage_file(self, key, stages=None):
        return self.files.get(key)

    def file(self, key):
        return self.registered.get(key)

    def register(self, key, p):
        self.registered[key] = Path(p)

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    def use_model(self, m):
        self.models.append(m)

    def progress(self, *_a, **_k):
        pass

    def copy_in(self, src, name):
        dest = self.dir / name
        dest.write_bytes(Path(src).read_bytes())
        return dest

    async def stage_input(self, p, name, instance=None):
        self.staged.append((Path(p).name, name))
        return name


@pytest.fixture
def build_env(tmp_path, monkeypatch):
    files = {k: _rgba(tmp_path / f"{k}.png") for k in ("cutout", "front_cutout", "left_cutout", "back_cutout", "right_cutout",
                                                       "quarter_cutout", "quarter2_cutout")}
    calls: list = []

    async def fake_build(sc, name, n, seed, *a, views=None, match_to=None, **k):
        calls.append({"name": name, "views": views, "match_to": match_to})
        return {"seed": seed, "elapsed": 1.0, "maps": {}, "other": [], "metrics": {}, "match": None}

    monkeypatch.setattr(mesh_stage, "_trellis_build", fake_build)
    monkeypatch.setattr(mesh_stage.texcolour, "picture_stats", lambda p: None)
    monkeypatch.setattr(mesh_stage.hairlib, "is_library", lambda sc: False)
    monkeypatch.setattr(pixal3d, "missing_files", lambda fresh=False: [])
    d = tmp_path / "v"
    d.mkdir()
    return SimpleNamespace(files=files, calls=calls, dir=d)


def test_default_builder_is_trellis2_from_one_picture(build_env):
    sc = _SC(build_env.dir, {"seed": 3}, build_env.files)
    asyncio.run(mesh_stage._trellis(sc, sc.version.params))
    call = build_env.calls[0]
    assert call["views"] is None and call["match_to"] == build_env.files["cutout"]
    assert sc.version.params["builder"] == "trellis2" and sc.version.metrics["builder"] == "trellis2"
    assert sc.version.metrics["recipe"]["mesh"]["builder"] == "trellis2"
    assert sc.models == ["trellis2"]


def test_pixal3d_builds_from_the_four_views_and_records_the_choice(build_env):
    sc = _SC(build_env.dir, {"seed": 3, "builder": "pixal3d", "hand_edit": True}, build_env.files)
    asyncio.run(mesh_stage._trellis(sc, sc.version.params))
    call = build_env.calls[0]
    assert set(call["views"]) == set(PIXAL3D_VIEWS)
    assert [src for src, _ in sc.staged] == [f"{v}_cutout.png" for v in PIXAL3D_VIEWS]
    assert call["match_to"] == build_env.files["front_cutout"]  # colours matched to the front picture it is posed to
    p, m = sc.version.params, sc.version.metrics
    assert p["builder"] == "pixal3d" and p["engine"] == "trellis" and p["input_view"] == "front"
    assert p["input_view_origin"] == "four_views"
    assert m["builder"] == "pixal3d" and m["recipe"]["mesh"]["builder"] == "pixal3d"
    assert sc.models == ["pixal3d_mv", "pixal3d_dino", "moge2"]
    assert sc.registered["input"].name == "input_image.png"
    assert "picture_edit" not in p  # never edits the picture (the hand edit is a one-picture tool)


def test_trellis2_after_pixal3d_decides_its_own_view_again(build_env):
    sc = _SC(build_env.dir, {"seed": 3, "builder": "trellis2", "input_view": "front", "input_view_origin": "four_views"}, build_env.files)
    sc.category = {"input_view_default": "quarter"}
    asyncio.run(mesh_stage._trellis(sc, sc.version.params))
    assert sc.version.params["input_view"] == "quarter" and sc.version.params["input_view_origin"] is None


def test_pixal3d_refused_in_plain_words_when_files_or_views_are_missing(build_env, monkeypatch):
    sc = _SC(build_env.dir, {"builder": "pixal3d"}, build_env.files)
    monkeypatch.setattr(pixal3d, "missing_files", lambda fresh=False: [MOGE_MODEL])
    with pytest.raises(FileNotFoundError, match="not installed: " + MOGE_MODEL):
        asyncio.run(mesh_stage._trellis(sc, sc.version.params))
    monkeypatch.setattr(pixal3d, "missing_files", lambda fresh=False: [])
    files = {k: v for k, v in build_env.files.items() if k != "back_cutout"}
    with pytest.raises(FileNotFoundError, match="back picture"):
        asyncio.run(mesh_stage._trellis(_SC(build_env.dir, {"builder": "pixal3d"}, files), {"builder": "pixal3d"}))
    monkeypatch.setattr(mesh_stage.hairlib, "is_library", lambda sc: True)
    with pytest.raises(ValueError, match="hair from the library"):
        asyncio.run(mesh_stage._trellis(sc, sc.version.params))
    assert not build_env.calls


def test_unknown_builder_is_an_error():
    with pytest.raises(ValueError):
        mesh_stage.builder_of({"builder": "other"})
    assert mesh_stage.builder_of({}) == "trellis2"


def test_camera_angle_is_read_from_the_preview_node():
    assert mesh_stage.camera_fov({"outputs": {FOV_NODE: {"text": ["35.69438340117313"]}}}) == 35.69
    assert mesh_stage.camera_fov({"outputs": {}}) is None


def test_a_refine_of_a_pixal3d_version_does_not_edit_the_front_picture(build_env, monkeypatch):
    built = []

    async def fake_run(sc, params):
        built.append(dict(params))

    async def no_edit(*a, **k):
        raise AssertionError("the picture must not be edited for a four-view build")

    monkeypatch.setattr(mesh_stage, "run", fake_run)
    monkeypatch.setattr(mesh_stage, "_edit_picture", no_edit)
    sc = _SC(build_env.dir, {}, build_env.files)
    sc.parent = SimpleNamespace(id="parent01", stage="mesh", params={"engine": "trellis", "builder": "pixal3d", "input_view": "front"})
    sc.settings = SimpleNamespace(stage_instances={})
    asyncio.run(mesh_stage.refine(sc, "make the boots red", None, None, {}))
    assert built and built[0]["builder"] == "pixal3d"


# --- painting without the three-quarter pictures ---------------------------------------------------------------------


def test_painted_views_leave_the_quarters_out_only_when_asked():
    assert srcproject.painted_views({}) == srcproject.TURN_VIEWS
    assert srcproject.painted_views({"quarter_pictures": True}) == srcproject.TURN_VIEWS
    assert srcproject.painted_views({"quarter_pictures": False}) == ("left", "right", "back")
    p = next(x for x in reference.STAGE_SCHEMAS["prep"] if x["name"] == "quarter_pictures")
    assert p["default"] is True and p["type"] == "boolean"


def test_face_pass_side_pictures_leave_the_quarters_out(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh_stage.hairlib, "is_library", lambda sc: False)
    files = {f"{v}_cutout": _rgba(tmp_path / f"{v}.png") for v in ("quarter", "quarter2", "left", "right")}
    sc = SimpleNamespace(lineage_file=lambda k, stages=None: files.get(k))
    sides = face_detail.side_pictures(sc)
    assert set(srcproject.without_quarters(sides, {})) == {"quarter", "quarter2", "left", "right"}
    assert set(srcproject.without_quarters(sides, {"quarter_pictures": False})) == {"left", "right"}
    # the face pass filters what side_pictures gives through the same rule
    import inspect
    assert "srcproject.without_quarters(sides, params)" in inspect.getsource(face_detail)


def test_picture_projection_is_given_no_quarter_pictures_when_off(tmp_path, monkeypatch):
    files = {f"{v}_cutout": _rgba(tmp_path / f"{v}.png") for v in srcproject.TURN_VIEWS}
    files.update(input=_rgba(tmp_path / "input.png"), cutout=_rgba(tmp_path / "cutout.png"))
    seen = {}

    def fake_apply(glb, base, picture, turn, *a, **k):
        seen["turn"] = dict(turn)
        return {"metrics": {}, "applied": False, "reason": "test"}

    async def same(sc, srcs, work):
        return srcs, {"upscaled": []}

    monkeypatch.setattr(srcproject, "apply_files", fake_apply)
    monkeypatch.setattr(srcproject, "upscale_sources", same)
    monkeypatch.setattr(srcproject, "original_pictures", lambda sc, p: (None, None))
    sc = SimpleNamespace(dir=tmp_path, lineage_file=lambda k, stages=None: files.get(k), stage_version=lambda s: None,
                         set_params=lambda **kw: None, set_metrics=lambda **kw: None, settings=None,
                         asset=SimpleNamespace(category="humanoid"))
    for params, want in (({}, set(srcproject.TURN_VIEWS)), ({"quarter_pictures": False, "face_marks": False}, {"left", "right", "back"})):
        asyncio.run(srcproject.run_stage(sc, tmp_path / "m.glb", {"base_color": files["input"]}, {**params, "face_marks": False}))
        assert set(seen["turn"]) == want
