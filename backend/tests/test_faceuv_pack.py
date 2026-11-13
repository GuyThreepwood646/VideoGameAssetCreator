"""The face relayout (blender/faceuv.py) packs the sheet once per try and puts the best try back, instead of packing the
best one a second time. Runs the real script in Blender on a small mesh of separate texture pieces; skipped when Blender
is not configured (config.yaml blender_exe) or not there.

Asserts: no two pieces overlap, the face gets clearly more texels across, the rest of the sheet keeps its texels
across within the loss limit, the sheet is used at least as well, and the number of packs equals the number of tries.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BLENDER_DIR = HERE.parent / "vgac" / "blender"
FIXTURE = HERE / "blender_uv_fixture.py"


def blender_exe() -> Path:
    try:
        from vgac.config import load_settings

        exe = load_settings().blender_exe
    except Exception:  # noqa: BLE001
        exe = None
    if not exe or not Path(exe).is_file():
        pytest.skip("Blender is not configured here")
    return Path(exe)


def run(exe: Path, script: Path, *args: str) -> str:
    p = subprocess.run([str(exe), "-b", "--python", str(script), "--", *args], capture_output=True, text=True, timeout=600)
    # Blender can exit 0 after a script error: the traceback is what shows it
    assert p.returncode == 0 and "Traceback" not in p.stderr + p.stdout, p.stdout[-1500:] + p.stderr[-1500:]
    return p.stdout


def test_relayout_leaves_no_overlap_and_the_face_keeps_its_texels(tmp_path):
    exe = blender_exe()
    tex = 512
    glb, pts, maps = tmp_path / "in.glb", tmp_path / "pts.json", tmp_path / "maps.json"
    run(exe, FIXTURE, "make", str(glb), str(pts), str(maps), str(tex))
    before = tmp_path / "before.json"
    run(exe, FIXTURE, "inspect", str(glb), str(before), str(pts))
    b = json.loads(before.read_text())
    assert b["overlap_px"] == 0 and b["islands"] == 24
    out = tmp_path / "out.glb"
    log = run(exe, BLENDER_DIR / "faceuv.py", str(glb), str(out), "--points", str(pts), "--maps", str(maps), "--gain", "2.4",
              "--tex", str(tex), "--padding", "4", "--margin", "8", "--max-loss", "0.12")
    rep = json.loads((tmp_path / "out_report.json").read_text())
    assert rep["layout_mode"] in ("fit", "repack"), rep
    after = tmp_path / "after.json"
    run(exe, FIXTURE, "inspect", str(out), str(after), str(pts))
    a = json.loads(after.read_text())
    assert a["islands"] == 24
    assert a["overlap_px"] == 0  # no two pieces share a texel
    face_gain = math.sqrt(a["face_area"] / b["face_area"])
    assert face_gain >= 1.5, (face_gain, rep)  # the face has clearly more texels across
    assert abs(face_gain - rep["gain"]) < 0.02  # the report says what the layout holds
    assert rep["rest_scale"] >= 1 - 0.12 - 1e-3  # the rest keeps its texels across within the loss limit
    assert a["used_share"] >= b["used_share"] - 1e-3  # the sheet is used at least as well
    if rep["layout_mode"] == "repack":
        # one pack per try and the best try put back: as many packs as tries, none after the last
        packs = int(log.split("FACEUV packs ")[1].split()[0])
        tries = log.count("FACEUV repack k")
        assert packs == tries, (packs, tries)
