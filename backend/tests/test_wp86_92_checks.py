"""WP-86 part 1 and WP-92: each new check is shown able to fail (a planted defect trips it) and able to pass (the same
without the defect). Limits are in the stage code; their measured basis is in docs the orchestrator keeps."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vgac import meshchecks

HERE = Path(__file__).resolve().parent
BLENDER = Path(os.environ.get("VGAC_TEST_BLENDER") or r"F:\Tools\blender-5.2.3-windows-x64\blender.exe")


def _unreached(cover: float) -> dict:
    r = subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(HERE / "blender_unreached_driver.py"), "--", str(cover),
                        str(HERE.parent / "vgac" / "blender")], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "OMP_NUM_THREADS": "2"})
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("UNREACHED_JSON "))
    return json.loads(line.split(" ", 1)[1])


@pytest.mark.skipif(not BLENDER.is_file(), reason="needs Blender (set VGAC_TEST_BLENDER)")
def test_unreached_count_sees_a_gap_and_passes_a_full_cover():
    gap = _unreached(0.5)
    assert 0.40 <= gap["share"] <= 0.60, gap  # half the target has no source under it
    full = _unreached(1.0)
    assert full["share"] is not None and full["share"] <= 0.02, full
