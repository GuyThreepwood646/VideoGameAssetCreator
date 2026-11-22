"""Per-platform LOD switch heights (platforms.yaml lod_screen_heights): the manifest, the Unity arguments and the
loud failures when a platform has no list or the list is not usable."""
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import reference
from vgac.stages import export

IMPORTER = Path(__file__).resolve().parents[2] / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"
PLATFORM_IDS = [p["id"] for p in reference.platforms()]


@pytest.mark.parametrize("pid", PLATFORM_IDS)
def test_manifest_levels_carry_this_platforms_heights(pid):
    lods = {"LOD0": 1000, "LOD1": 500, "LOD2": 250}
    levels = export.lod_list(lods, pid)
    expected = reference.platform(pid)["lod_screen_heights"]
    assert [lv["screen_height"] for lv in levels] == expected[:3]
    assert [lv["triangles"] for lv in levels] == [1000, 500, 250]


def test_the_platforms_really_differ():
    lo = reference.lod_screen_heights("mobile_low")
    mid = reference.lod_screen_heights("pc_mid")
    hi = reference.lod_screen_heights("high")
    assert lo[0] > mid[0] > hi[0]
    assert mid == [0.6, 0.3, 0.12, 0.03]  # the values the importer used before the per-platform lists


@pytest.mark.parametrize("pid", PLATFORM_IDS)
def test_unity_args_pass_this_platforms_heights_to_the_importer(tmp_path, pid):
    sc = SimpleNamespace(dir=tmp_path, platform=pid, category={"rig_engine": "skintoken_generic", "size_m": 1.2},
                         stage_version=lambda s: SimpleNamespace(metrics={"bones": 30}, params={}),
                         settings=SimpleNamespace(clips_libraries={}))
    args, _, _ = export._unity_args(sc, {}, "prop")
    assert args["-vgacLodHeights"] == ",".join(f"{h:g}" for h in reference.platform(pid)["lod_screen_heights"])


def test_too_many_levels_for_the_platform_fails_loudly():
    with pytest.raises(ValueError, match="pc_mid"):
        export.lod_list({f"LOD{i}": 100 for i in range(6)}, "pc_mid")


def test_unknown_platform_fails():
    with pytest.raises(KeyError):
        export.lod_list({"LOD0": 100}, "no_such_platform")


def test_a_platform_without_heights_stops_the_data_from_loading(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(reference.DATA_DIR, data)
    text = (data / "platforms.yaml").read_text(encoding="utf-8")
    cut = "    lod_screen_heights: [0.45, 0.225, 0.09, 0.0225]\n"
    assert cut in text
    (data / "platforms.yaml").write_text(text.replace(cut, ""), encoding="utf-8")
    monkeypatch.setattr(reference, "DATA_DIR", data)
    reference.reload()
    try:
        with pytest.raises(ValueError, match="high"):
            reference.platforms()
    finally:
        monkeypatch.undo()
        reference.reload()
    assert reference.lod_screen_heights("high") == [0.45, 0.225, 0.09, 0.0225]


def test_heights_must_fall_from_one_to_zero(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(reference.DATA_DIR, data)
    text = (data / "platforms.yaml").read_text(encoding="utf-8")
    (data / "platforms.yaml").write_text(text.replace("[0.45, 0.225, 0.09, 0.0225]", "[0.2, 0.45, 0.08, 0.02]"), encoding="utf-8")
    monkeypatch.setattr(reference, "DATA_DIR", data)
    reference.reload()
    try:
        with pytest.raises(ValueError, match="high"):
            reference.platforms()
    finally:
        monkeypatch.undo()
        reference.reload()


def test_the_importer_has_no_fixed_height_list_and_takes_the_argument():
    text = IMPORTER.read_text(encoding="utf-8")
    assert "0.6f, 0.3f" not in text
    assert "-vgacLodHeights" in text and "ParseFloats" in text and "lodHeights" in text


def test_each_level_switches_at_the_same_pixel_size_on_every_platform():
    # pc_mid at 1080 px is the anchor: the old list is 648, 324, 129.6 and 32.4 px. Every platform's heights, times its
    # reference screen height, give those same pixels (rule in platforms.yaml).
    anchor = [648.0, 324.0, 129.6, 32.4]
    for pid in PLATFORM_IDS:
        ref = reference.platform(pid)["lod_reference_height_px"]
        px = [h * ref for h in reference.lod_screen_heights(pid)]
        assert all(abs(a - b) < 0.01 for a, b in zip(px, anchor)), (pid, px)


@pytest.mark.parametrize("pid", PLATFORM_IDS)
def test_the_last_level_keeps_at_least_32_pixels_on_the_reference_screen(pid):
    ref = reference.platform(pid)["lod_reference_height_px"]
    assert reference.lod_screen_heights(pid)[-1] * ref >= 32


def test_a_last_level_that_culls_below_32_pixels_fails_loudly(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(reference.DATA_DIR, data)
    text = (data / "platforms.yaml").read_text(encoding="utf-8")
    (data / "platforms.yaml").write_text(text.replace("[0.45, 0.225, 0.09, 0.0225]", "[0.45, 0.225, 0.09, 0.02]"), encoding="utf-8")
    monkeypatch.setattr(reference, "DATA_DIR", data)
    reference.reload()
    try:
        with pytest.raises(ValueError, match="32"):
            reference.platforms()
    finally:
        monkeypatch.undo()
        reference.reload()


def test_a_platform_without_a_reference_height_fails_loudly(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(reference.DATA_DIR, data)
    text = (data / "platforms.yaml").read_text(encoding="utf-8")
    (data / "platforms.yaml").write_text(text.replace("    lod_reference_height_px: 720\n", ""), encoding="utf-8")
    monkeypatch.setattr(reference, "DATA_DIR", data)
    reference.reload()
    try:
        with pytest.raises(ValueError, match="mobile_low"):
            reference.platforms()
    finally:
        monkeypatch.undo()
        reference.reload()
