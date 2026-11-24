"""The app's leftover inputs in ComfyUI's input folder are removed only when this backend staged them itself.

Each backend keeps a record of the files its own stage_input wrote (in its own data folder, never in the ComfyUI
input folder). sweep() removes a file only when it is in that record, starts with "vgac_", is older than the age limit
and no active id of this backend is in its name. The person's own pictures, other tools' files and other backends'
files are never removed, whatever their names.
"""

import asyncio
import json
import os
import time

from vgac import comfy_inputs
from vgac.comfy import ComfyClient


def _old(p):
    t = time.time() - comfy_inputs.MIN_AGE_S - 60
    os.utime(p, (t, t))


def _write_old(folder, name):
    (folder / name).write_bytes(b"x")
    _old(folder / name)


def _record(data_dir):
    p = comfy_inputs.record_path(data_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def test_sweep_removes_only_recorded_old_file(tmp_path):
    """A non-VGAC file, an unrecorded "vgac_" file (another backend's) and a recorded old file: only the last goes."""
    inp, data = tmp_path / "input", tmp_path / "data"
    inp.mkdir()
    data.mkdir()
    _write_old(inp, "my_photo.png")
    _write_old(inp, "vgac_aaaaaaaa_concept.png")
    _write_old(inp, "vgac_bbbbbbbb_mesh_in.png")
    comfy_inputs.remember(data, "vgac_bbbbbbbb_mesh_in.png")
    _old(inp / "vgac_bbbbbbbb_mesh_in.png")
    removed = comfy_inputs.sweep(inp, set(), data_dir=data)
    assert removed == 1
    assert sorted(p.name for p in inp.iterdir()) == ["my_photo.png", "vgac_aaaaaaaa_concept.png"]
    assert _record(data) == {}  # the entry went with the file


def test_sweep_keeps_recorded_file_with_active_id_and_fresh_files(tmp_path):
    inp, data = tmp_path / "input", tmp_path / "data"
    inp.mkdir()
    data.mkdir()
    for name in ("vgac_cccccccc_live.png", "vgac_dddddddd_fresh.png"):
        (inp / name).write_bytes(b"x")
        comfy_inputs.remember(data, name)
    _old(inp / "vgac_cccccccc_live.png")
    assert comfy_inputs.sweep(inp, {"cccccccc"}, data_dir=data) == 0
    assert sorted(p.name for p in inp.iterdir()) == ["vgac_cccccccc_live.png", "vgac_dddddddd_fresh.png"]


def test_sweep_never_removes_recorded_file_without_vgac_prefix(tmp_path):
    inp, data = tmp_path / "input", tmp_path / "data"
    inp.mkdir()
    data.mkdir()
    _write_old(inp, "owner_picture.png")
    comfy_inputs.remember(data, "owner_picture.png")
    _old(inp / "owner_picture.png")
    assert comfy_inputs.sweep(inp, set(), data_dir=data) == 0
    assert (inp / "owner_picture.png").exists()


def test_recorded_file_already_gone_is_dropped_without_error(tmp_path):
    inp, data = tmp_path / "input", tmp_path / "data"
    inp.mkdir()
    data.mkdir()
    comfy_inputs.remember(data, "vgac_eeeeeeee_gone.png")
    assert comfy_inputs.sweep(inp, set(), data_dir=data) == 0
    assert _record(data) == {}


def test_two_backends_with_own_data_folders_never_delete_each_other(tmp_path):
    """Both stage into one ComfyUI input folder; each sweep removes only the files its own backend staged."""
    inp = tmp_path / "input"
    inp.mkdir()
    data_a, data_b = tmp_path / "data_a", tmp_path / "data_b"
    src = tmp_path / "src.png"
    src.write_bytes(b"png")
    a = ComfyClient("http://127.0.0.1:1", input_dir=inp, data_dir=data_a)
    b = ComfyClient("http://127.0.0.1:2", input_dir=inp, data_dir=data_b)
    name_a = asyncio.run(a.stage_input(src, "vgac_aaaaaaaa_concept.png"))
    name_b = asyncio.run(b.stage_input(src, "vgac_bbbbbbbb_concept.png"))
    _old(inp / name_a)
    _old(inp / name_b)

    assert comfy_inputs.sweep(inp, set(), data_dir=data_a) == 1   # backend A: only its own file
    assert (inp / name_b).exists()
    assert not (inp / name_a).exists()

    _old(inp / name_b)
    assert comfy_inputs.sweep(inp, set(), data_dir=data_a) == 0   # nothing of A's left; B's file untouched
    assert (inp / name_b).exists()

    assert comfy_inputs.sweep(inp, set(), data_dir=data_b) == 1   # backend B removes its own
    assert not (inp / name_b).exists()


def test_stage_input_records_name_and_time(tmp_path):
    inp, data = tmp_path / "input", tmp_path / "data"
    src = tmp_path / "src.png"
    src.write_bytes(b"png")
    client = ComfyClient("http://127.0.0.1:1", input_dir=inp, data_dir=data)
    before = time.time()
    name = asyncio.run(client.stage_input(src, "vgac_ffffffff_mesh_in.png"))
    rec = _record(data)
    assert list(rec) == [name]
    assert before <= rec[name] <= time.time()
    assert (inp / name).exists()


def test_sweep_without_folder_or_record(tmp_path):
    assert comfy_inputs.sweep(None, set(), data_dir=tmp_path) == 0
    assert comfy_inputs.sweep(tmp_path, set(), data_dir=tmp_path / "no_record") == 0
