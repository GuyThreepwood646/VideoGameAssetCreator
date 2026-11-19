"""The app's leftover inputs in ComfyUI's input folder are removed; the person's own files and live jobs' stay."""

import os
import time

from vgac import comfy_inputs


def _old(p):
    t = time.time() - comfy_inputs.MIN_AGE_S - 60
    os.utime(p, (t, t))


def test_sweep_keeps_live_and_foreign_files(tmp_path):
    for n in ("vgac_aaaaaaaa_concept.png", "vgac_bbbbbbbb_mesh_in.png", "vgac_judge_cccccccc_x.png", "my_photo.png"):
        (tmp_path / n).write_bytes(b"x")
        _old(tmp_path / n)
    (tmp_path / "vgac_folder").mkdir()
    removed = comfy_inputs.sweep(tmp_path, {"bbbbbbbb"})
    assert removed == 2
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["my_photo.png", "vgac_bbbbbbbb_mesh_in.png", "vgac_folder"]


def test_sweep_without_folder():
    assert comfy_inputs.sweep(None, set()) == 0


def test_sweep_keeps_fresh_files_another_backend_may_need(tmp_path):
    """Another backend sharing this ComfyUI stages inputs this backend's queue knows nothing about."""
    (tmp_path / "vgac_dddddddd_concept.png").write_bytes(b"x")          # just written by another backend
    (tmp_path / "vgac_eeeeeeee_concept.png").write_bytes(b"x")
    _old(tmp_path / "vgac_eeeeeeee_concept.png")                         # a real leftover
    assert comfy_inputs.sweep(tmp_path, set()) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["vgac_dddddddd_concept.png"]
