"""Rig body plan: a single long chain reads as a serpent; a lone sideways chain is not a wing (procanim).

Self-contained: the planted rigs are built from scratch by body_plan_planted.py (no library file, no env var, no
picture). One Blender process builds all plants; each plant is then read by procanim.py. Skipped without Blender.
Scratch files go under pytest's tmp_path (point it at bakeoff/batch-tmp with --basetemp).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.machine_tools import blender as machine_blender

ROOT = Path(__file__).resolve().parents[2]
BLENDER = machine_blender() or Path("blender-not-found")
PROCANIM = ROOT / "backend" / "vgac" / "blender" / "procanim.py"
BUILDER = Path(__file__).resolve().parent / "body_plan_planted.py"

needs = pytest.mark.skipif(not (BLENDER.is_file() and PROCANIM.is_file() and BUILDER.is_file()),
                           reason="needs Blender (VGAC_TEST_BLENDER or config)")


def _env() -> dict:
    env = dict(os.environ)
    env["VGAC_REPO"] = str(ROOT)
    env["OMP_NUM_THREADS"] = "2"
    return env


@pytest.fixture(scope="module")
def plants(tmp_path_factory):
    out = tmp_path_factory.mktemp("body_plan_plants")
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(BUILDER), "--", str(out)],
                   check=True, capture_output=True, timeout=600, env=_env())
    return out


def _read(plants: Path, tmp: Path, name: str) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    out_glb = tmp / f"{name}.glb"
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(PROCANIM), "--",
                    str(plants / f"{name}.glb"), str(out_glb), "24", "--clips", "idle"],
                   check=True, capture_output=True, timeout=600, env=_env())
    return json.loads((tmp / f"{name}_anim.json").read_text(encoding="utf-8"))


@needs
def test_a_long_single_chain_reads_as_a_serpent(plants, tmp_path):
    r = _read(plants, tmp_path, "snake_long")
    assert r["motion"] == "serpent"
    assert r["legs"] == []


@needs
def test_a_short_single_chain_stays_unknown(plants, tmp_path):
    r = _read(plants, tmp_path, "snake_short")
    assert r["motion"] == "unknown"


@needs
def test_a_long_tail_on_a_quadruped_still_reads_quadruped(plants, tmp_path):
    r = _read(plants, tmp_path, "quad_long_tail")
    assert r["kind"] == "quadruped"
    assert len(r["legs"]) == 4
    assert r["wings"] == []
    assert len(r["tail"]) >= 1


@needs
def test_a_real_wing_pair_on_a_quadruped_keeps_two_wings(plants, tmp_path):
    r = _read(plants, tmp_path, "quad_wing_pair")
    assert len(r["wings"]) == 2
    assert {w["side"] for w in r["wings"]} == {"left", "right"}


@needs
def test_a_lone_sideways_chain_on_a_quadruped_is_not_a_wing(plants, tmp_path):
    r = _read(plants, tmp_path, "quad_one_chain")
    assert r["kind"] == "quadruped"
    assert len(r["legs"]) == 4
    assert r["wings"] == []
