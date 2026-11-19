"""Clean-up (prep) takes texture maps only from the mesh version it works on, never from another mesh version.

Two mesh versions of one asset on different engines: a TRELLIS.2 model with maps and a Hunyuan3D model without any.
Before the fix, prep found the Hunyuan model's maps through lineage_file, which falls back to the selected mesh
version (the TRELLIS one), and baked them onto a mesh with no UV layout.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import library
from vgac.stages import prep

KEYS = ("base_color", "normal", "metallic", "roughness", "ao")


@pytest.fixture
def files_on_disk(monkeypatch, tmp_path):
    """library.version_file reads the file named in version.files; the test files live in tmp_path."""

    def fake_version_file(version, key, settings=None):
        rel = version.files.get(key)
        if not rel:
            return None
        p = Path(rel)
        return p if p.is_file() else None

    monkeypatch.setattr(library, "version_file", fake_version_file)
    return tmp_path


def _version(tmp_path: Path, vid: str, engine: str, maps: tuple[str, ...]) -> SimpleNamespace:
    files = {"glb": str(tmp_path / f"{vid}_model.glb")}
    (tmp_path / f"{vid}_model.glb").write_bytes(b"glb")
    for k in maps:
        f = tmp_path / f"{vid}_{k}.png"
        f.write_bytes(b"png")
        files[k] = str(f)
    return SimpleNamespace(id=vid, stage="mesh", files=files, params={"engine": engine}, metrics={})


def test_hunyuan_mesh_takes_no_maps_from_the_trellis_version(files_on_disk):
    trellis = _version(files_on_disk, "trellis-1", "trellis", KEYS)
    hunyuan = _version(files_on_disk, "hy3d-1", "hy3d", ())

    maps, missing = prep.own_texture_maps(hunyuan, KEYS)

    assert maps == {}
    assert missing == list(KEYS)
    # The sibling's maps exist on disk (the old fallback found them); none of them is used for this mesh.
    assert all(Path(trellis.files[k]).is_file() for k in KEYS)
    assert not any(Path(p).name.startswith("trellis-1") for p in maps.values())


def test_trellis_mesh_keeps_its_own_maps(files_on_disk):
    trellis = _version(files_on_disk, "trellis-1", "trellis", KEYS)

    maps, missing = prep.own_texture_maps(trellis, KEYS)

    assert missing == []
    assert maps == {k: Path(trellis.files[k]) for k in KEYS}


def test_partial_maps_are_reported_and_not_borrowed(files_on_disk):
    partial = _version(files_on_disk, "hy3d-2", "hy3d", ("base_color",))
    _version(files_on_disk, "trellis-2", "trellis", KEYS)

    maps, missing = prep.own_texture_maps(partial, KEYS)

    assert set(maps) == {"base_color"}
    assert missing == ["normal", "metallic", "roughness", "ao"]


def test_no_mesh_version_gives_no_maps(files_on_disk):
    maps, missing = prep.own_texture_maps(None, KEYS)
    assert maps == {}
    assert missing == list(KEYS)


def test_keys_are_the_ones_asked_for(files_on_disk):
    trellis = _version(files_on_disk, "trellis-3", "trellis", KEYS)
    maps, missing = prep.own_texture_maps(trellis, ("base_color", "metallic"))
    assert set(maps) == {"base_color", "metallic"}
    assert missing == []
