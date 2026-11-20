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
    found = rig._rig_of_geometry(sc, root, "Mixamo", 4)
    assert found is not None and found.id == "rig_same"


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
    assert asyncio.run(rig.retexture(sc, "Mixamo", 4)) is True
    assert new_rig.metrics["retextured_of"] == "donor" and new_rig.metrics["rig_method"] == "retexture"
    assert judged == [True]
    # The rig's model carries the new picture; the donor's rig is untouched.
    assert (_base_colour(Path(new_rig.files["glb"]))[..., 0] == 240).all()
    assert (_base_colour(donor_glb)[..., 0] == base[..., 0]).all()


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
