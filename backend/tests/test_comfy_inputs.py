"""The app's leftover inputs in ComfyUI's input folder are removed; the person's own files and live jobs' stay."""

from vgac import comfy_inputs


def test_sweep_keeps_live_and_foreign_files(tmp_path):
    for n in ("vgac_aaaaaaaa_concept.png", "vgac_bbbbbbbb_mesh_in.png", "vgac_judge_cccccccc_x.png", "my_photo.png"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "vgac_folder").mkdir()
    removed = comfy_inputs.sweep(tmp_path, {"bbbbbbbb"})
    assert removed == 2
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["my_photo.png", "vgac_bbbbbbbb_mesh_in.png", "vgac_folder"]


def test_sweep_without_folder():
    assert comfy_inputs.sweep(None, set()) == 0
