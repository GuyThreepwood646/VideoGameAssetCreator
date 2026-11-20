"""The Shape tab (WP-26): parameter checks, the cut preview from a rig's skeleton, the apply job (a refusal writes no model,
a success makes a shaped clean-up with its operation log), and the refusals on a shaped version. The engine is replaced
by a stand-in here (the real Blender cut is covered in test_separate.py and by the bakeoff run). Nothing touches the
owner's library: every file lives in pytest's temporary folder.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vgac import paintsave, separate, shape
from vgac.db import Version

trimesh = pytest.importorskip("trimesh")


def _version(vid: str, stage: str, files: dict | None = None, metrics: dict | None = None, parent: str | None = None) -> Version:
    return Version(id=vid, stage=stage, status="done", files=files or {}, metrics=metrics or {}, params={},
                   parent_version_id=parent)


def _rig_glb(path: Path) -> Path:
    """A rig with a left and right arm: shoulder and elbow joints as named nodes with a small box on each."""
    scene = trimesh.Scene()
    box = trimesh.creation.box(extents=(0.02, 0.02, 0.02))
    joints = {"mixamorig:LeftArm": (0.2, 1.4, 0.0), "mixamorig:LeftForeArm": (0.5, 1.4, 0.0),
              "mixamorig:RightArm": (-0.2, 1.4, 0.0), "mixamorig:RightForeArm": (-0.5, 1.4, 0.0)}
    for name, (x, y, z) in joints.items():
        t = np.eye(4)
        t[:3, 3] = (x, y, z)
        scene.add_geometry(box.copy(), node_name=name, geom_name=name, transform=t)
    scene.export(path)
    return path


def test_parse_accepts_the_defaults_and_rejects_bad_values():
    p = shape.parse({})
    assert p == {"operation": "separate", "side": "both", "gap": separate.DEFAULT_GAP, "start": separate.DEFAULT_START}
    assert shape.parse({"side": "L", "gap": 0.004, "start": 0.25})["side"] == "L"
    with pytest.raises(ValueError, match="operation"):
        shape.parse({"operation": "delete"})
    with pytest.raises(ValueError, match="side"):
        shape.parse({"side": "front"})
    with pytest.raises(ValueError, match="gap"):
        shape.parse({"gap": 0.05})
    with pytest.raises(ValueError, match="start"):
        shape.parse({"start": 0.9})


def test_the_rig_source_is_its_clean_up_parent():
    clean = _version("clean", "prep")
    mid = _version("mid", "mesh", parent="clean")
    rig = _version("rig", "rig", parent="clean")
    db = {"clean": clean, "mid": mid, "rig": rig}
    fake_db = SimpleNamespace(get=lambda _cls, vid: db.get(vid))
    assert shape.rig_source(fake_db, rig) is clean
    with pytest.raises(ValueError, match="no clean-up"):
        shape.rig_source(fake_db, _version("orphan", "rig"))


def test_preview_puts_each_cut_start_along_the_upper_arm_and_writes_nothing(tmp_path):
    rig = _rig_glb(tmp_path / "rig.glb")
    before = sorted(p.name for p in tmp_path.iterdir())
    out = shape.preview(rig, start=0.3, gap=0.006)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    left = out["arms"]["L"]
    assert np.allclose(left["cut_start"], [0.2 + 0.3 * 0.3, 1.4, 0.0], atol=1e-4)
    assert left["upper_arm_m"] == pytest.approx(0.3, abs=1e-4)
    assert set(out["arms"]) == {"L", "R"}
    assert "The left cut would start 30 percent of the way down the upper arm." in out["messages"]


def test_preview_with_no_arm_bones_says_so(tmp_path):
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(), node_name="Body", geom_name="Body")
    path = tmp_path / "body.glb"
    scene.export(path)
    out = shape.preview(path)
    assert out["arms"] == {}
    assert "no arm bones" in out["messages"][0]


class _Ctx:
    def __init__(self, tmp: Path, parent: Version, rig: Version, db_versions: dict):
        self.dir = tmp / "new"
        self.dir.mkdir()
        self.settings = SimpleNamespace(blender_timeout_seconds=60)
        self.parent = parent
        self.version = _version("new", "prep")
        self.db = SimpleNamespace(get=lambda _cls, vid: db_versions.get(vid))
        self.registered: dict[str, Path] = {}
        self.metrics: dict = {}
        self.params: dict = {}

    def progress(self, *_a, **_k):
        pass

    def register(self, key, path):
        self.registered[key] = Path(path)
        return str(path)

    def set_metrics(self, **values):
        self.metrics.update(values)

    def set_params(self, **values):
        self.params.update(values)


@pytest.fixture
def job(tmp_path, monkeypatch):
    """A clean-up (painted, with a picture) and the rig made from it; the library lookups point at tmp files."""
    step4 = tmp_path / "step4.glb"
    step4.write_bytes(b"glb-step4")
    rig_file = tmp_path / "rig.glb"
    rig_file.write_bytes(b"glb-rig")
    parent = _version("clean", "prep", files={"glb": "clean/step4.glb", "base_color": "clean/base.png"},
                      metrics={"painted": True, "geometry_of": "root", "paint_texels": 12, "hair_library": {"x": 1},
                               "elapsed_seconds": 3.0})
    rig = _version("rig", "rig", files={"glb": "rig/rig.glb"}, parent="clean")
    files = {"clean/step4.glb": step4, "rig/rig.glb": rig_file}
    monkeypatch.setattr(shape.library, "version_file", lambda v, key, settings=None: files.get(v.files.get(key)))
    monkeypatch.setattr(paintsave, "_copy_parent_files", lambda sc, parent: None)
    ctx = _Ctx(tmp_path, parent, rig, {"rig": rig, "clean": parent})
    return ctx, rig, tmp_path


def _report(changed: bool, messages: list[str]) -> dict:
    return {"changed": changed, "ok": changed, "sides": {}, "messages": messages, "check": {"open_edges": 0,
            "bad_edges": 0, "shells": 1, "new_crossings": 0}, "uv": {"placed": 2}}


def test_a_refusal_writes_no_model_and_fails_with_the_plain_reason(job, monkeypatch):
    import asyncio

    ctx, rig, tmp = job

    def refused(settings, step4, rig_glb, out, **kw):
        return _report(False, ["The left arm was not changed: its skin weights start on the forearm, so it is not an upper arm grown into the body."])

    monkeypatch.setattr(shape.separate, "separate", refused)
    with pytest.raises(shape.ShapeRefused) as e:
        asyncio.run(shape.run(ctx, {"side": "L", "gap": 0.006, "start": 0.3, "rig_version_id": "rig"}))
    text = str(e.value)
    assert "not an upper arm" in text and "Nothing was changed" in text
    assert "glb" not in ctx.registered and "base_color" not in ctx.registered
    assert ctx.metrics == {} and ctx.params == {}


def test_a_success_makes_a_shaped_version_with_its_log_and_keeps_the_source(job, monkeypatch):
    import asyncio

    ctx, rig, tmp = job
    source_before = (ctx.parent.files.copy(), dict(ctx.parent.metrics))

    def cut(settings, step4, rig_glb, out, *, side, gap, start, timeout):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"glb-cut")
        return {**_report(True, ["The left arm is separate."]), "params": {"side": side, "gap": gap, "start": start}}

    monkeypatch.setattr(shape.separate, "separate", cut)
    monkeypatch.setattr(shape, "_extract_picture", lambda glb, out: (out.write_bytes(b"png") or True))
    asyncio.run(shape.run(ctx, {"side": "L", "gap": 0.004, "start": 0.25, "rig_version_id": "rig"}))

    assert ctx.registered["glb"].read_bytes() == b"glb-cut"
    assert "base_color" in ctx.registered
    log = ctx.metrics["shape_log"]
    assert log["operation"] == "Separate a fused arm"
    assert log["parameters"] == {"side": "L", "gap": 0.004, "start": 0.25}
    assert log["rig_version"] == "rig"
    assert log["engine"]["changed"] is True and log["messages"] == ["The left arm is separate."]
    assert ctx.metrics["shaped"] is True and ctx.metrics["shape_of"] == "clean"
    # The painted flags and the run's own measures are not carried to the cut; the hair library is.
    for dropped in ("painted", "geometry_of", "paint_texels", "elapsed_seconds"):
        assert dropped not in ctx.metrics
    assert ctx.metrics["hair_library"] == {"x": 1}
    # The source clean-up is not changed.
    assert (ctx.parent.files.copy(), dict(ctx.parent.metrics)) == source_before


def test_a_shaped_version_is_not_painted_so_a_rig_never_reuses_the_old_geometry_rig(job):
    shaped = _version("shaped", "prep", metrics={"shaped": True, "shape_of": "clean"})
    assert not paintsave.is_painted(shaped)
    assert paintsave.geometry_root(None, shaped) is shaped
    assert paintsave.refusal(shaped) == paintsave.SHAPE_REFUSAL
    assert paintsave.refusal(_version("plain", "prep")) is None


def test_the_shape_job_refuses_a_non_clean_up_parent(job):
    import asyncio

    ctx, rig, tmp = job
    ctx.parent = _version("mesh", "mesh")
    with pytest.raises(ValueError, match="clean-up step"):
        asyncio.run(shape.run(ctx, {"side": "L", "rig_version_id": "rig"}))


def test_a_rig_made_from_a_shaped_version_never_takes_an_old_rig_from_before_the_cut(tmp_path):
    """The next rig run after a cut makes the weights again: retexture (the reuse of a rig on the same geometry) is
    refused, because the cut version is not painted and so owns its own geometry."""
    import asyncio

    from vgac.stages import rig

    shaped = _version("shaped", "prep", metrics={"shaped": True, "shape_of": "clean"})
    rig_v = _version("rig2", "rig", parent="shaped")

    class Ctx:
        version = rig_v
        metrics: dict = {}

        def iter_lineage(self, include_self=True):
            return iter([shaped])

        def set_metrics(self, **values):
            self.metrics.update(values)

    ctx = Ctx()
    assert asyncio.run(rig.retexture(ctx, "humanoid", 4, {})) is False
    assert ctx.metrics == {}  # nothing was reused, so nothing was set

