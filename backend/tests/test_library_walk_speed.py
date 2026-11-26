"""The speed a shared-library walk plays at (blender/retarget.py writes speed_mps on the walk row; VgacImport.cs reads it)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import machine_tools
from vgac.stages import rig

BLENDER = machine_tools.blender()
UAL1_GLB = Path(__file__).resolve().parents[2] / "data" / "animations" / "ual1" / "Universal Animation Library[Standard]" / "Unreal-Godot" / "UAL1_Standard.glb"
IMPORTER = (Path(__file__).resolve().parents[2] / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs").read_text(encoding="utf-8")
needs = pytest.mark.skipif(BLENDER is None or not UAL1_GLB.is_file(), reason="needs Blender and the UAL1 clip library")


@needs
def test_library_walk_row_carries_the_planted_feet_speed_and_the_run_does_not(tmp_path):
    import test_animation_quality as taq

    build, src, out = tmp_path / "build.py", tmp_path / "synthetic.glb", tmp_path / "anim.glb"
    build.write_text(taq._BUILD, encoding="utf-8")
    subprocess.run([str(BLENDER), "-b", "--python", str(build), "--", str(src)], check=True, capture_output=True, timeout=300)
    script = Path(rig.__file__).resolve().parents[1] / "blender" / "retarget.py"
    subprocess.run([str(BLENDER), "-b", "--python", str(script), "--", str(src), str(out), "--library", str(UAL1_GLB),
                    "--clips", "walk=Walk_Loop,run=Jog_Fwd_Loop", "--loops", "walk,run"], check=True, capture_output=True, timeout=600)
    rows = {r["name"]: r for r in json.loads((tmp_path / "anim_clips.json").read_text())["clips"]}
    walk = rows["walk"]
    assert walk["source"] == "library" and walk["speed_method"] == "planted_feet"
    assert 0.2 < walk["speed_mps"] < 3.0  # the walk's own stance speed, not a fixed 1.4
    assert "speed_mps" not in rows["run"]  # the run keeps the importer's default


def test_importer_reads_the_speed_for_a_library_row_and_the_version_is_raised():
    assert 'string.Equals(c.source, "library"' in IMPORTER  # MeasuredSpeed accepts the shared clip of a library row
    assert 'Version = "27"' in IMPORTER
