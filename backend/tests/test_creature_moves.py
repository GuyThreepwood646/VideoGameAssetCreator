"""F14: rigged creatures (beetle, dog, snake, dragon) finished with no moves. procanim's death clip crashed on a rig
without Mixamo names (no "Hips" bone: StopIteration) and on a serpent or six-legged body (tilt_deg never set), and
Blender exits 0 on a script error, so the stage kept a missing anim.glb and no error.

The Blender tests build small creature rigs with model-named bones (tests/blender_creature_rig.py) and run procanim
with the category's own default moves, death included; skipped without Blender. The stage test needs no Blender.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.machine_tools import blender as machine_blender
from vgac.stages import rig

ROOT = Path(__file__).resolve().parents[2]
BLENDER = machine_blender() or Path("blender-not-found")
PROCANIM = ROOT / "backend" / "vgac" / "blender" / "procanim.py"
BUILD = Path(__file__).resolve().parent / "blender_creature_rig.py"
CATEGORIES = ROOT / "backend" / "vgac" / "data" / "categories"

needs_blender = pytest.mark.skipif(not (BLENDER.is_file() and PROCANIM.is_file()), reason="needs Blender (VGAC_TEST_BLENDER)")


def _env() -> dict:
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = "2"
    return env


def _category(name: str) -> dict:
    return yaml.safe_load((CATEGORIES / f"{name}.yaml").read_text(encoding="utf-8"))


@needs_blender
@pytest.mark.parametrize("kind,category", [("quad", "quadruped"), ("hexapod", "multileg"), ("serpent", "serpent")])
def test_a_creature_rig_gets_its_default_moves_death_included(tmp_path, kind, category):
    tmp_path = tmp_path.resolve()
    src = tmp_path / f"{kind}.glb"
    subprocess.run([str(BLENDER), "-b", "-t", "2", "--factory-startup", "--python", str(BUILD), "--", str(src), kind],
                   check=True, capture_output=True, timeout=300, env=_env())
    assert src.is_file()
    cat = _category(category)
    assert "death" in cat["clips"]
    out = tmp_path / "anim.glb"
    run = subprocess.run([str(BLENDER), "-b", "-t", "2", "--factory-startup", "--python", str(PROCANIM), "--", str(src), str(out),
                          "24", "--motion", cat["motion"], "--clips", ",".join(cat["clips"])],
                         capture_output=True, timeout=900, env=_env(), text=True, errors="replace")
    log = run.stdout + run.stderr
    assert "Traceback" not in log, log[-3000:]
    assert "PROCANIM_OK" in log
    assert out.is_file()
    rep = json.loads((tmp_path / "anim_anim.json").read_text(encoding="utf-8"))
    assert set(cat["clips"]) <= set(rep["clips"]) and not rep["missing_clips"]


class _SC:
    """The stage context _animate uses, with a Blender call that exits 0 but writes nothing (as procanim did)."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "ver"
        self.dir.mkdir()
        self.category = _category("quadruped")
        self.asset = SimpleNamespace(category="quadruped")
        self.settings = SimpleNamespace(clips_libraries={}, blender_timeout_seconds=60)
        self.version = SimpleNamespace(metrics={}, params={})
        self.files, self.calls = {}, []

    def set_params(self, **kw):
        self.version.params.update(kw)

    def set_metrics(self, **kw):
        self.version.metrics.update(kw)

    def register(self, key, path):
        self.files[key] = Path(path)

    def file(self, key):
        p = self.files.get(key)
        return p if p is not None and p.is_file() else None

    def progress(self, *a, **k):
        pass

    async def blender(self, script, args, log_name=None, timeout=None):
        self.calls.append(script)
        if script == "procanim.py":
            (self.dir / (log_name or "procanim.log")).write_text(
                "Traceback (most recent call last):\n  File \"procanim.py\", line 2219, in _death_build\nStopIteration\n")
        return ""


def test_a_moves_script_that_writes_no_file_is_recorded_as_an_error(tmp_path, monkeypatch):
    sc = _SC(tmp_path)
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {}))
    assert "anim_glb" not in sc.files  # no missing file is listed on the version
    err = sc.version.metrics.get("anim_error") or ""
    assert "StopIteration" in err
    assert sc.calls == ["procanim.py"]  # nothing runs on a file that is not there
    # The step's own checks then say the moves are missing (before, no clips check ran at all).
    monkeypatch.setattr(rig.quality, "rig_checks", lambda *a, **k: [])
    asyncio.run(rig.judge(sc))
    clips = {c["id"]: c for c in sc.version.metrics["checks"]}["clips"]
    assert clips["ok"] is False
    assert clips["vars"]["missing"] == sc.category["clips"]
