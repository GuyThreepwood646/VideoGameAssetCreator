"""Touch up save (WP-23): the painted picture merged into a new clean-up version, the refusal of Adjust and Refine on it,
the rig that takes the new picture without a new rig run, and the export input that carries it.

The model is a small GLB with two UV islands. Library lookups go to the test's own folders, so the owner's library is
never touched. The API routes are tested in test_api.py.
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from vgac import imaging, paintsave
from vgac.stages import export, rig

SIZE = 64


def _glb(path: Path, base: np.ndarray) -> Path:
    trimesh = pytest.importorskip("trimesh")
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1],
                  [2, 0, 0], [3, 0, 0], [3, 1, 0], [2, 1, 0]], float)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7]])
    uv = np.array([[0.05, 0.95], [0.45, 0.95], [0.45, 0.55], [0.05, 0.55],
                   [0.55, 0.45], [0.95, 0.45], [0.95, 0.05], [0.55, 0.05]])
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(base))
    trimesh.Trimesh(v, f, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False).export(path)
    return path


def _base_colour(glb: Path) -> np.ndarray:
    trimesh = pytest.importorskip("trimesh")
    m = trimesh.load(str(glb), force="mesh")
    return np.array(m.visual.material.baseColorTexture.convert("RGBA"))


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _picture(seed: int = 3) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (SIZE, SIZE, 4), dtype=np.uint8)


def _grow(mask: np.ndarray, px: int) -> np.ndarray:
    """The mask grown by px texels with 8 neighbours (the reach imaging.dilate uses), no wrap at the edges."""
    out = mask.copy()
    for _ in range(px):
        grown = out.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ys = slice(max(0, dy), SIZE + min(0, dy))
                yd = slice(max(0, -dy), SIZE + min(0, -dy))
                xs = slice(max(0, dx), SIZE + min(0, dx))
                xd = slice(max(0, -dx), SIZE + min(0, -dx))
                grown[yd, xd] |= out[ys, xs]
        out = grown
    return out


def _island(tmp_path: Path) -> np.ndarray:
    return paintsave._picture_layout(_glb(tmp_path / "layout.glb", np.zeros((SIZE, SIZE, 3), np.uint8)), SIZE)


# --- the merge -----------------------------------------------------------------------------------------------------


def test_merge_changes_only_the_mask_and_the_padding(tmp_path):
    island = _island(tmp_path)
    parent = _picture()
    edit = np.zeros((SIZE, SIZE), bool)
    rows, cols = np.where(island)
    edit[rows[:6], cols[:6]] = True
    painted = parent.copy()
    painted[edit] = [200, 10, 10, 255]
    pad = 4
    out, counts = paintsave.merge_picture(parent, painted, edit, island, pad_px=pad)
    assert (out[edit] == painted[edit]).all()
    keep = island & ~edit
    assert np.array_equal(out[keep], parent[keep])
    # Only the padding reach around the islands and the paint may differ from the parent.
    reach = _grow(island | edit, pad)
    untouched = ~reach
    assert np.array_equal(out[untouched], parent[untouched])
    assert counts["painted_texels"] == int(edit.sum()) and counts["painted_on_island"] == int(edit.sum())


def test_merge_pads_the_gutter_from_the_painted_edge(tmp_path):
    island = _island(tmp_path)
    parent = np.full((SIZE, SIZE, 4), 40, np.uint8)
    painted = parent.copy()
    edit = island.copy()
    painted[edit] = [90, 180, 30, 255]
    out, counts = paintsave.merge_picture(parent, painted, edit, island, pad_px=3)
    edge = _grow(island, 1) & ~island
    assert edge.any()
    assert (out[edge] == [90, 180, 30, 255]).all()  # the padding carries the painted edge colour outward
    assert counts["padding_texels"] > 0


def test_merge_rejects_mismatched_sizes(tmp_path):
    island = _island(tmp_path)
    with pytest.raises(ValueError):
        paintsave.merge_picture(_picture(), _picture()[:32], island, island, 2)


# --- the refusal and the chain -------------------------------------------------------------------------------------


def _version(vid, stage="prep", parent=None, metrics=None, files=None, params=None):
    return SimpleNamespace(id=vid, stage=stage, parent_version_id=parent, metrics=metrics or {}, files=files or {},
                           params=params or {}, status="done")


class _FakeDb:
    """The two session calls the chain and the rig lookup make: get by id and scalars (every row of the query)."""

    def __init__(self, *versions):
        self.by_id = {v.id: v for v in versions}
        self.rows = list(versions)

    def get(self, _cls, vid):
        return self.by_id.get(vid)

    def scalars(self, _query):
        return SimpleNamespace(all=lambda: list(self.rows))


def test_refusal_only_for_a_painted_clean_up():
    assert paintsave.refusal(_version("p", metrics={"painted": True})) == paintsave.REFUSAL
    assert paintsave.refusal(_version("p")) is None
    assert paintsave.refusal(_version("m", stage="mesh", metrics={"painted": True})) is None
    assert paintsave.refusal(None) is None
    assert "Adjust and Refine" in paintsave.REFUSAL and "lose the paint" in paintsave.REFUSAL


def test_geometry_root_walks_back_over_painted_versions():
    base = _version("base")
    p1 = _version("p1", parent="base", metrics={"painted": True})
    p2 = _version("p2", parent="p1", metrics={"painted": True})
    db = _FakeDb(base, p1, p2)
    assert paintsave.geometry_root(db, p2).id == "base"
    assert paintsave.geometry_root(db, base).id == "base"


# --- the job: a new clean-up version with the paint -------------------------------------------------------------


class _Sc:
    """The StageContext parts the paint job uses, over real folders."""

    def __init__(self, folder: Path, parent, version):
        self.parent = parent
        self.asset = SimpleNamespace(id="a", project_id="p", category="character")
        self.settings = None
        self.version = version
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)

    def progress(self, *_a, **_k):
        pass

    def register(self, key, path):
        self.version.files[key] = str(path)
        return str(path)

    def set_metrics(self, **values):
        self.version.metrics.update(values)

    def set_params(self, **values):
        self.version.params.update(values)


@pytest.fixture
def version_files(monkeypatch):
    """Version files are real paths here: a version's folder is its `folder` attribute."""

    def version_dir(asset, version, settings=None, create=True):
        return version.folder

    def version_file(version, key, settings=None):
        rel = version.files.get(key)
        return Path(rel) if rel and Path(rel).is_file() else None

    monkeypatch.setattr(paintsave.library, "version_dir", version_dir)
    monkeypatch.setattr(paintsave.library, "version_file", version_file)
    monkeypatch.setattr(rig.library, "version_dir", version_dir)
    monkeypatch.setattr(rig.library, "version_file", version_file)


def _parent_on_disk(tmp_path: Path, base: np.ndarray):
    folder = tmp_path / "parent"
    folder.mkdir()
    glb = _glb(folder / "prep.glb", base[..., :3])
    Image.fromarray(base).save(folder / "base_color.png")
    parent = _version("parent", metrics={"atlas_used": 0.3, "checks": [{"id": "x"}], "export_mesh_hash": "h1"})
    parent.folder = folder
    parent.files = {"glb": str(glb), "base_color": str(folder / "base_color.png")}
    return parent


def test_paint_job_makes_a_new_version_and_keeps_the_parent(version_files, tmp_path):
    base = _picture()
    parent = _parent_on_disk(tmp_path, base)
    parent_files = dict(parent.files)
    island = _island(tmp_path)
    child = _version("child")
    child.folder = tmp_path / "child"
    sc = _Sc(tmp_path / "child", parent, child)
    edit = np.zeros((SIZE, SIZE), bool)
    rows, cols = np.where(island)
    edit[rows[:10], cols[:10]] = True
    painted = base.copy()
    painted[edit] = [220, 30, 30, 255]
    pic, mask, strokes = paintsave.decode_inputs(_png_b64(Image.fromarray(painted)),
                                                 _png_b64(Image.fromarray((edit * 255).astype(np.uint8), "L")),
                                                 [{"mode": "paint", "rgb": [220, 30, 30]}])
    paintsave.write_inputs(sc.dir, pic, mask, strokes)
    asyncio.run(paintsave.paint(sc, {}))

    assert child.metrics["painted"] is True and child.metrics["painted_of"] == "parent"
    assert "checks" not in child.metrics and child.metrics["atlas_used"] == 0.3
    assert child.metrics["geometry_of"] == "parent" and child.metrics["export_mesh_hash"] == "h1"
    assert child.metrics["paint_strokes"] == 1
    new_base = np.array(Image.open(child.files["base_color"]).convert("RGBA"))
    assert (new_base[edit] == [220, 30, 30, 255]).all()
    keep = island & ~edit
    assert np.array_equal(new_base[keep], base[keep])
    assert Path(child.files["paint_mask"]).is_file() and Path(child.files["paint_strokes"]).is_file()
    assert (_base_colour(Path(child.files["glb"]))[..., :3][edit] == [220, 30, 30]).all()
    # The parent is unchanged on disk and in its record.
    assert parent.files == parent_files
    assert np.array_equal(np.array(Image.open(parent.folder / "base_color.png").convert("RGBA")), base)
    assert paintsave.refusal(child) == paintsave.REFUSAL


def test_paint_job_refuses_an_empty_mask(version_files, tmp_path):
    base = _picture()
    parent = _parent_on_disk(tmp_path, base)
    child = _version("child")
    child.folder = tmp_path / "child"
    sc = _Sc(tmp_path / "child", parent, child)
    pic, mask, strokes = paintsave.decode_inputs(_png_b64(Image.fromarray(base)),
                                                 _png_b64(Image.fromarray(np.zeros((SIZE, SIZE), np.uint8), "L")), [])
    paintsave.write_inputs(sc.dir, pic, mask, strokes)
    with pytest.raises(ValueError, match="nothing was painted"):
        asyncio.run(paintsave.paint(sc, {}))


def test_decode_refuses_what_is_not_a_picture():
    good = _png_b64(Image.fromarray(np.zeros((4, 4), np.uint8), "L"))
    with pytest.raises(ValueError, match="not a PNG"):
        paintsave.decode_inputs(base64.b64encode(b"not a picture").decode(), good, [])
    with pytest.raises(ValueError, match="not valid base64"):
        paintsave.decode_inputs("%%%", good, [])
    with pytest.raises(ValueError, match="stroke log"):
        paintsave.decode_inputs(good, good, "nope")  # type: ignore[arg-type]


# --- the rig: the same geometry gets the new picture without a new rig run -------------------------------------


def test_rig_of_geometry_picks_the_rig_of_the_unpainted_root(version_files):
    root = _version("root")
    painted = _version("painted", parent="root", metrics={"painted": True})
    other_root = _version("other")
    rig_same = _version("rig_same", stage="rig", parent="painted", params={"template_label": "Mixamo", "group_per_vertex": 4})
    rig_other_template = _version("rig_other", stage="rig", parent="root", params={"template_label": "Other", "group_per_vertex": 4})
    rig_other_geometry = _version("rig_geo", stage="rig", parent="other", params={"template_label": "Mixamo", "group_per_vertex": 4})
    for r in (rig_same, rig_other_template, rig_other_geometry):
        r.files = {"glb": str(Path(__file__).resolve())}  # a real file: the lookup checks it exists
    db = _FakeDb(root, painted, other_root, rig_same, rig_other_template, rig_other_geometry)
    sc = SimpleNamespace(db=db, asset=SimpleNamespace(id="a"), version=_version("now"), settings=None)
    found = rig._rig_candidates(sc, root, "Mixamo", 4)
    assert [r.id for r in found] == ["rig_same"]


def test_retexture_swaps_the_picture_in_the_rig_and_skips_the_rig_run(version_files, tmp_path, monkeypatch):
    base = _picture(seed=5)
    donor_dir = tmp_path / "donor"
    donor_dir.mkdir()
    donor_glb = _glb(donor_dir / "rig.glb", base[..., :3])
    Image.fromarray(base).save(donor_dir / "base_color.png")
    root = _version("root")
    donor = _version("donor", stage="rig", parent="root", params={"template_label": "Mixamo", "group_per_vertex": 4},
                     files={"glb": str(donor_glb), "base_color": str(donor_dir / "base_color.png")})
    donor.folder = donor_dir
    # The painted clean-up (the rig's parent) with its own picture.
    painted_dir = tmp_path / "painted"
    painted_dir.mkdir()
    painted_glb = _glb(painted_dir / "prep.glb", base[..., :3])
    new_picture = base.copy()
    new_picture[..., 0] = 240
    Image.fromarray(new_picture).save(painted_dir / "base_color.png")
    painted = _version("painted", parent="root", metrics={"painted": True}, files={"glb": str(painted_glb), "base_color": str(painted_dir / "base_color.png")})
    painted.folder = painted_dir
    db = _FakeDb(root, painted, donor)
    new_rig = _version("new_rig", stage="rig", parent="painted", params={"template_label": "Mixamo", "group_per_vertex": 4})
    new_rig.folder = tmp_path / "new_rig"
    new_rig.folder.mkdir()
    lineage_files = {"base_color": str(painted_dir / "base_color.png")}

    sc = SimpleNamespace()
    sc.db = db
    sc.asset = SimpleNamespace(id="a", project_id="p", category="character")
    sc.settings = None
    sc.version = new_rig
    sc.dir = new_rig.folder
    sc.parent = painted
    sc.iter_lineage = lambda include_self=False: iter([painted, root])
    sc.lineage_file = lambda key, stages=None: Path(lineage_files[key]) if key in lineage_files else None
    sc.file = lambda key: Path(new_rig.files[key]) if new_rig.files.get(key) else None
    sc.progress = lambda *a, **k: None
    sc.register = lambda key, path: new_rig.files.__setitem__(key, str(path)) or str(path)
    sc.set_metrics = lambda **v: new_rig.metrics.update(v)
    sc.set_params = lambda **v: new_rig.params.update(v)
    sc.save = lambda: None

    judged: list[bool] = []

    async def no_judge(_sc):
        judged.append(True)

    monkeypatch.setattr(rig, "judge", no_judge)
    monkeypatch.setattr(rig, "link_textures", lambda s: new_rig.files.__setitem__("base_color", str(painted_dir / "base_color.png")))
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {})) is True
    assert new_rig.metrics["retextured_of"] == "donor" and new_rig.metrics["rig_method"] == "retexture"
    assert judged == [True]
    # The rig's model carries the new picture; the donor's rig is untouched.
    assert (_base_colour(Path(new_rig.files["glb"]))[..., 0] == 240).all()
    assert (_base_colour(donor_glb)[..., 0] == base[..., 0]).all()


def _retexture_setup(tmp_path, donor_metrics=None, donor_extra=None):
    """A donor rig of the root geometry, the painted clean-up over it (its picture's red channel 240) and the new rig
    version's StageContext stand-in. -> (sc, new rig, donor, base picture)."""
    base = _picture(seed=5)
    donor_dir = tmp_path / "donor"
    donor_dir.mkdir()
    donor_glb = _glb(donor_dir / "rig.glb", base[..., :3])
    Image.fromarray(base).save(donor_dir / "base_color.png")
    root = _version("root")
    donor = _version("donor", stage="rig", parent="root", params={"template_label": "Mixamo", "group_per_vertex": 4},
                     files={"glb": str(donor_glb), "base_color": str(donor_dir / "base_color.png"), **(donor_extra or {})},
                     metrics=donor_metrics)
    donor.folder = donor_dir
    painted_dir = tmp_path / "painted"
    painted_dir.mkdir()
    painted_glb = _glb(painted_dir / "prep.glb", base[..., :3])
    new_picture = base.copy()
    new_picture[..., 0] = 240
    Image.fromarray(new_picture).save(painted_dir / "base_color.png")
    painted = _version("painted", parent="root", metrics={"painted": True},
                       files={"glb": str(painted_glb), "base_color": str(painted_dir / "base_color.png")})
    painted.folder = painted_dir
    db = _FakeDb(root, painted, donor)
    new_rig = _version("new_rig", stage="rig", parent="painted", params={"template_label": "Mixamo", "group_per_vertex": 4})
    new_rig.folder = tmp_path / "new_rig"
    new_rig.folder.mkdir()
    lineage_files = {"base_color": str(painted_dir / "base_color.png")}
    sc = SimpleNamespace(db=db, asset=SimpleNamespace(id="a", project_id="p", category="character"),
                         settings=SimpleNamespace(blender_timeout_seconds=60), version=new_rig, dir=new_rig.folder,
                         parent=painted)
    sc.iter_lineage = lambda include_self=False: iter([painted, root])
    sc.lineage_file = lambda key, stages=None: Path(lineage_files[key]) if key in lineage_files else None
    sc.file = lambda key: Path(new_rig.files[key]) if new_rig.files.get(key) else None
    sc.progress = lambda *a, **k: None
    sc.register = lambda key, path: new_rig.files.__setitem__(key, str(path)) or str(path)
    sc.set_metrics = lambda **v: new_rig.metrics.update(v)
    sc.set_params = lambda **v: new_rig.params.update(v)
    sc.save = lambda: None
    return sc, new_rig, donor, base, painted_dir


def test_retexture_over_a_separated_rig_colours_the_caps_from_the_new_paint(version_files, tmp_path, monkeypatch):
    caps = tmp_path / "shape_caps.json"
    caps.write_text('{"format": 1, "caps": []}', encoding="utf-8")
    sc, new_rig, donor, base, painted_dir = _retexture_setup(tmp_path, {"separation": {"changed": True}}, {"shape_caps": str(caps)})
    calls = []

    def fake_recolour(settings, rig_glb, caps_json, picture, out_png, timeout=None):
        calls.append((Path(rig_glb), Path(caps_json), Path(picture)))
        im = np.array(Image.open(picture).convert("RGBA"))
        im[..., 1] = 7  # the caps' texels, coloured from the new paint
        Image.fromarray(im).save(out_png)
        return {"ok": True, "recoloured": 2, "caps": [{"ring_colour_diff": 0.9}, {"ring_colour_diff": 1.1}]}

    monkeypatch.setattr(rig, "judge", lambda _sc: asyncio.sleep(0))
    monkeypatch.setattr(rig, "link_textures", lambda s: new_rig.files.__setitem__("base_color", str(painted_dir / "base_color.png")))
    from vgac import separate

    monkeypatch.setattr(separate, "recolour", fake_recolour)
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {})) is True
    assert calls and calls[0][1] == caps and calls[0][2] == painted_dir / "base_color.png"  # the new paint, not the old caps
    assert new_rig.metrics["caps_recoloured"] == {"caps": 2, "ring_colour_diff": [0.9, 1.1]}
    assert Path(new_rig.files["base_color"]).name == rig.RECOLOURED_FILE
    col = _base_colour(Path(new_rig.files["glb"]))
    assert (col[..., 0] == 240).all() and (col[..., 1] == 7).all()


def test_retexture_over_a_separated_rig_without_its_caps_file_makes_the_rig_again(version_files, tmp_path, monkeypatch):
    sc, new_rig, donor, base, painted_dir = _retexture_setup(tmp_path, {"separation": {"changed": True}})
    monkeypatch.setattr(rig, "judge", lambda _sc: asyncio.sleep(0))
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {})) is False
    assert "no caps file" in new_rig.metrics["retexture_why"]
    assert "glb" not in new_rig.files  # nothing of the donor was taken


# --- export: the input of an edited version carries the new picture --------------------------------------------


def test_export_input_is_the_retextured_rig(version_files, tmp_path):
    """The export of a retextured rig reads the rig's animated GLB, which carries the painted picture."""
    rig_dir = tmp_path / "rig"
    rig_dir.mkdir()
    anim = _glb(rig_dir / "anim.glb", np.full((SIZE, SIZE, 3), 7, np.uint8))
    picture = tmp_path / "picture.png"
    Image.new("RGB", (SIZE, SIZE), (33, 33, 33)).save(picture)
    assert imaging.glb_replace_images(anim, base_color=picture)
    files = {"anim_glb": str(anim)}

    class Sc:
        def lineage_file(self, key, stages=None):
            return Path(files[key]) if key in files and (stages is None or "rig" in stages) else None

    p, name = export._input_glb(Sc())
    assert p == anim and name == "rig.anim_glb"
    assert (_base_colour(p)[..., :3] == 33).all()


# --- when a painted clean-up reuses a rig (the rules of rig.retexture) -------------------------------------------


def test_reuse_differences_ignore_seeds_and_bookkeeping_only():
    mine = {"leg_skin": True, "head_sway": 2, "seed": 9, "face_seed": 3, "fresh_attempt": True, "instruction": "x"}
    theirs = {"leg_skin": True, "head_sway": 2, "seed": 1, "face_seed": 7}
    assert rig.reuse_differences(mine, theirs) == []
    assert rig.reuse_differences({"leg_skin": False}, {"leg_skin": True}) == ["leg_skin"]
    assert rig.reuse_differences({"arm_pose": "relaxed"}, {}) == ["arm_pose"]
    assert rig.reuse_differences({"moves": None}, {}) == []  # unset and missing are the same


def _rig_sc(tmp_path, painted, donors, params, selected_ids=()):
    """A fake rig stage context over real folders: the painted clean-up, the donor rigs and the new rig."""
    new_rig = _version("new_rig", stage="rig", parent="painted", params=dict(params))
    new_rig.folder = tmp_path / "new_rig"
    new_rig.folder.mkdir(parents=True, exist_ok=True)
    db = _FakeDb(_version("root"), painted, *donors)
    for d in donors:
        d.selected = d.id in selected_ids
    db.rows = list(donors) + [new_rig]
    picture = painted.files["base_color"]
    sc = SimpleNamespace(db=db, asset=SimpleNamespace(id="a", project_id="p", category="character"), settings=None, version=new_rig,
                         dir=new_rig.folder, parent=painted)
    sc.iter_lineage = lambda include_self=False: iter([painted, _version("root")])
    sc.lineage_file = lambda key, stages=None: Path(picture) if key == "base_color" else None
    sc.file = lambda key: Path(new_rig.files[key]) if new_rig.files.get(key) else None
    sc.progress = lambda *a, **k: None
    sc.register = lambda key, path: new_rig.files.__setitem__(key, str(path)) or str(path)
    sc.set_metrics = lambda **v: new_rig.metrics.update(v)
    sc.set_params = lambda **v: new_rig.params.update(v)
    sc.save = lambda: None
    return sc, new_rig


def _donor(tmp_path, name, params, glb_source):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    glb = _glb(folder / "rig.glb", np.full((SIZE, SIZE, 3), 50, np.uint8))
    d = _version(name, stage="rig", parent="painted", params=params, files={"glb": str(glb)})
    d.folder = folder
    d.metrics = {}
    return d


def _painted_parent(tmp_path):
    folder = tmp_path / "painted"
    folder.mkdir(exist_ok=True)
    glb = _glb(folder / "prep.glb", np.full((SIZE, SIZE, 3), 50, np.uint8))
    Image.fromarray(np.full((SIZE, SIZE, 4), 240, np.uint8)).save(folder / "base_color.png")
    p = _version("painted", parent="root", metrics={"painted": True}, files={"glb": str(glb), "base_color": str(folder / "base_color.png")})
    p.folder = folder
    return p


BASE = {"template_label": "Mixamo", "group_per_vertex": 4, "leg_skin": True}


def test_fresh_attempt_never_reuses_a_rig(version_files, tmp_path, monkeypatch):
    monkeypatch.setattr(rig, "judge", lambda s: asyncio.sleep(0))
    painted = _painted_parent(tmp_path)
    donor = _donor(tmp_path, "donor", dict(BASE), None)
    sc, new_rig = _rig_sc(tmp_path, painted, [donor], dict(BASE))
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {"fresh_attempt": True})) is False
    assert "fresh attempt" in new_rig.metrics["retexture_why"]
    assert "retexture_from" not in new_rig.metrics


def test_changed_setting_runs_a_real_rig(version_files, tmp_path, monkeypatch):
    monkeypatch.setattr(rig, "judge", lambda s: asyncio.sleep(0))
    painted = _painted_parent(tmp_path)
    donor = _donor(tmp_path, "donor", dict(BASE), None)
    sc, new_rig = _rig_sc(tmp_path, painted, [donor], {**BASE, "leg_skin": False})
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {})) is False
    assert "leg_skin" in new_rig.metrics["retexture_why"]


def test_equal_settings_reuse_the_donor_and_a_seed_change_does_not_matter(version_files, tmp_path, monkeypatch):
    judged = []

    async def no_judge(_sc):
        judged.append(True)

    monkeypatch.setattr(rig, "judge", no_judge)
    monkeypatch.setattr(rig, "link_textures", lambda s: None)
    painted = _painted_parent(tmp_path)
    donor = _donor(tmp_path, "donor", {**BASE, "seed": 1}, None)
    sc, new_rig = _rig_sc(tmp_path, painted, [donor], {**BASE, "seed": 2})
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {})) is True
    assert new_rig.metrics["retextured_of"] == "donor" and judged == [True]


def test_selected_donor_is_preferred_over_a_newer_equal_one(version_files, tmp_path, monkeypatch):
    async def no_judge(_sc):
        return None

    monkeypatch.setattr(rig, "judge", no_judge)
    monkeypatch.setattr(rig, "link_textures", lambda s: None)
    painted = _painted_parent(tmp_path)
    newer = _donor(tmp_path, "newer", dict(BASE), None)
    older = _donor(tmp_path, "older_selected", dict(BASE), None)
    sc, new_rig = _rig_sc(tmp_path, painted, [newer, older], dict(BASE), selected_ids=("older_selected",))
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {})) is True
    assert new_rig.metrics["retextured_of"] == "older_selected"


def test_retexture_of_a_rig_without_an_embedded_picture_uses_the_sidecar(version_files, tmp_path, monkeypatch):
    """Rig GLBs made by the rig run carry no material: their picture is the base_color file, which is linked to the new
    clean-up. Retexture must not fail on them."""
    trimesh = pytest.importorskip("trimesh")
    monkeypatch.setattr(rig, "judge", lambda s: asyncio.sleep(0))
    painted = _painted_parent(tmp_path)
    donor_dir = tmp_path / "donor_plain"
    donor_dir.mkdir()
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    trimesh.Trimesh(v, [[0, 1, 2]], process=False).export(donor_dir / "rig.glb")
    donor = _version("donor", stage="rig", parent="painted", params=dict(BASE), files={"glb": str(donor_dir / "rig.glb")})
    donor.folder = donor_dir
    donor.metrics = {}
    sc, new_rig = _rig_sc(tmp_path, painted, [donor], dict(BASE))
    link = {"done": False}
    sc.lineage_file = lambda key, stages=None: Path(painted.files["base_color"]) if key == "base_color" else None
    monkeypatch.setattr(rig, "link_textures", lambda s: link.__setitem__("done", True))
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4, {})) is True
    assert link["done"] and new_rig.metrics["rig_method"] == "retexture"


# --- Smooth the paint (WP-24) ---------------------------------------------------------------------------------------


def _two_islands():
    """Labels: island 1 on the left half, island 2 on the right half, with a 2 texel gap of no island between them."""
    labels = np.zeros((SIZE, SIZE), np.int32)
    labels[:, :30] = 1
    labels[:, 34:] = 2
    return labels


def test_smooth_softens_speckle_inside_the_mask_only():
    rng = np.random.default_rng(5)
    labels = np.ones((SIZE, SIZE), np.int32)
    parent = np.zeros((SIZE, SIZE, 4), np.uint8)
    parent[..., :3] = 120
    parent[..., 3] = 255
    noise = rng.integers(-40, 40, (SIZE, SIZE, 3))
    parent[..., :3] = np.clip(parent[..., :3].astype(int) + noise, 0, 255).astype(np.uint8)
    edit = np.zeros((SIZE, SIZE), bool)
    edit[20:44, 20:44] = True
    out, counts = paintsave.smooth_picture(parent, edit, labels, "strong", SIZE)
    before = parent[edit][..., :3].astype(float).std()
    after = out[edit][..., :3].astype(float).std()
    assert after < before
    assert counts["polished_texels"] > 0
    assert np.array_equal(out[~edit & (labels == 0)], parent[~edit & (labels == 0)])


def test_smooth_keeps_a_real_colour_edge():
    labels = np.ones((SIZE, SIZE), np.int32)
    parent = np.zeros((SIZE, SIZE, 4), np.uint8)
    parent[:, :32, :3] = [200, 40, 40]
    parent[:, 32:, :3] = [20, 20, 220]
    parent[..., 3] = 255
    edit = np.zeros((SIZE, SIZE), bool)
    edit[:, 26:38] = True
    out, _ = paintsave.smooth_picture(parent, edit, labels, "strong", SIZE)
    # Far from the edge the colours do not move, and the edge is not blurred into a ramp.
    assert (out[10, 2, :3] == [200, 40, 40]).all()
    assert (out[10, 60, :3] == [20, 20, 220]).all()
    assert int(np.abs(out[10, 31, :3].astype(int) - out[10, 32, :3].astype(int)).sum()) > 200


def test_smooth_never_mixes_colour_across_an_island_border():
    labels = _two_islands()
    parent = np.zeros((SIZE, SIZE, 4), np.uint8)
    parent[..., 3] = 255
    parent[:, :30, :3] = [200, 0, 0]
    parent[:, 34:, :3] = [0, 0, 200]
    rng = np.random.default_rng(9)
    parent[..., 0] = np.where(labels == 1, np.clip(200 + rng.integers(-30, 30, (SIZE, SIZE)), 0, 255), parent[..., 0])
    parent[..., 2] = np.where(labels == 2, np.clip(200 + rng.integers(-30, 30, (SIZE, SIZE)), 0, 255), parent[..., 2])
    edit = np.zeros((SIZE, SIZE), bool)
    edit[:, 24:40] = True  # straddles both islands and the gap
    for strength in ("light", "strong"):
        out, _ = paintsave.smooth_picture(parent, edit, labels, strength, SIZE)
        assert (out[labels == 1][..., 2] == 0).all()
        assert (out[labels == 2][..., 0] == 0).all()
        assert (out[labels == 1][..., 0] > 100).all() and (out[labels == 2][..., 2] > 100).all()


def test_smooth_is_byte_identical_outside_the_mask_except_the_padding():
    labels = np.ones((SIZE, SIZE), np.int32)
    labels[:4, :] = 0
    labels[:, :4] = 0
    rng = np.random.default_rng(11)
    parent = rng.integers(0, 255, (SIZE, SIZE, 4), dtype=np.uint8)
    parent[..., 3] = 255
    edit = np.zeros((SIZE, SIZE), bool)
    edit[30:34, 30:34] = True
    out, counts = paintsave.smooth_picture(parent, edit, labels, "strong", SIZE)
    island = labels > 0
    # Island texels outside the mask are exactly the parent's.
    assert np.array_equal(out[island & ~edit], parent[island & ~edit])
    # Changed texels are all painted island texels or gutter texels within the padding reach of a painted texel.
    changed = (out != parent).any(axis=-1)
    reach = _grow(edit, paintsave._pad_px(SIZE))
    assert not (changed & ~edit & island).any()
    assert not (changed & ~reach).any()
    assert counts["polished_texels"] == int((changed & edit & island).sum())


def test_smooth_with_no_painted_island_texel_changes_nothing():
    labels = np.zeros((SIZE, SIZE), np.int32)
    labels[:, :32] = 1
    parent = _picture()
    edit = np.zeros((SIZE, SIZE), bool)
    edit[:, 40:] = True  # painted only where no island lies
    out, counts = paintsave.smooth_picture(parent, edit, labels, "light", SIZE)
    assert np.array_equal(out, parent)
    assert counts["polished_texels"] == 0


def test_smooth_rejects_an_unknown_strength():
    with pytest.raises(ValueError, match="light or strong"):
        paintsave.smooth_picture(_picture(), np.ones((SIZE, SIZE), bool), np.ones((SIZE, SIZE), np.int32), "medium", SIZE)


def test_smooth_passes_scale_with_the_texture():
    assert paintsave.smooth_passes("light", 1024) == 2
    assert paintsave.smooth_passes("strong", 1024) == 5
    assert paintsave.smooth_passes("light", 4096) == 8
    assert paintsave.smooth_passes("strong", 256) == 5


def test_smooth_job_makes_a_new_painted_version(version_files, tmp_path):
    base = np.zeros((SIZE, SIZE, 4), np.uint8)
    rng = np.random.default_rng(2)
    base[..., :3] = np.clip(120 + rng.integers(-50, 50, (SIZE, SIZE, 3)), 0, 255)
    base[..., 3] = 255
    parent = _parent_on_disk(tmp_path, base)
    island = _island(tmp_path)
    edit = np.zeros((SIZE, SIZE), bool)
    rows, cols = np.where(island)
    edit[rows[:200], cols[:200]] = True
    Image.fromarray((edit * 255).astype(np.uint8), "L").save(parent.folder / "paint_mask.png")
    strokes = parent.folder / "paint_strokes.json"
    strokes.write_text('[{"mode": "paint"}]', encoding="utf-8")
    parent.files.update({"paint_mask": str(parent.folder / "paint_mask.png"), "paint_strokes": str(strokes)})
    parent.metrics.update(painted=True, painted_of="base", geometry_of="base")
    child = _version("child")
    child.folder = tmp_path / "child"
    sc = _Sc(tmp_path / "child", parent, child)
    asyncio.run(paintsave.smooth(sc, {"strength": "strong"}))

    assert child.metrics["painted"] is True and child.metrics["polished"] == "strong"
    assert child.metrics["geometry_of"] == "base" and child.metrics["painted_of"] == "parent"
    assert child.params["polish_strength"] == "strong"
    assert Path(child.files["paint_mask"]).is_file() and Path(child.files["paint_strokes"]).is_file()
    new_base = np.array(Image.open(child.files["base_color"]).convert("RGBA"))
    assert np.array_equal(new_base[island & ~edit], base[island & ~edit])
    assert not np.array_equal(new_base[edit], base[edit])
    assert (_base_colour(Path(child.files["glb"]))[..., :3][edit] == new_base[..., :3][edit]).all()
    assert parent.metrics.get("polished") is None
    assert paintsave.refusal(child) == paintsave.REFUSAL


def test_smooth_refuses_an_unpainted_parent(version_files, tmp_path):
    base = _picture()
    parent = _parent_on_disk(tmp_path, base)
    child = _version("child")
    child.folder = tmp_path / "child"
    sc = _Sc(tmp_path / "child", parent, child)
    with pytest.raises(ValueError, match="only a painted clean-up"):
        asyncio.run(paintsave.smooth(sc, {"strength": "light"}))
