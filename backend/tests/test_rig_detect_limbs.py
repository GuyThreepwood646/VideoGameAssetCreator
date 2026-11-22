"""Rig body plan (WP-19, PREREG in bakeoff/rigdetect/PREREG.md): procanim counts legs by bone geometry and weights.

Planted case 1: a low pole bone with no vertex weights, far from the ankles, is a helper, not a leg: the rig stays a
two-legged biped. Planted case 2: a real extra leg pair (weighted geometry, reaching the ground) adds two legs.
The planted rigs are built from the WP-72 Knight_Male reference (set VGAC_WP72_KNIGHT to its .gltf path). One Blender
run per rig; skipped without Blender or without the reference file. Scratch files go under pytest's --basetemp.
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
MAKE_PLANTED = ROOT / "bakeoff" / "rigdetect" / "make_planted.py"
KNIGHT = Path(os.environ.get("VGAC_WP72_KNIGHT", "")) if os.environ.get("VGAC_WP72_KNIGHT") else None

needs = pytest.mark.skipif(
    not (BLENDER.is_file() and PROCANIM.is_file() and MAKE_PLANTED.is_file() and KNIGHT and KNIGHT.is_file()),
    reason="needs Blender (VGAC_TEST_BLENDER) and the WP-72 Knight_Male reference (VGAC_WP72_KNIGHT)")


def _env() -> dict:
    env = dict(os.environ)
    env["VGAC_REPO"] = str(ROOT)
    env["OMP_NUM_THREADS"] = "2"
    return env


def _planted(tmp_path: Path) -> Path:
    out = tmp_path / "planted"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(MAKE_PLANTED), "--", str(KNIGHT), str(out)],
                   check=True, capture_output=True, timeout=300, env=_env())
    return out


def _read(tmp_path: Path, rig: Path, tag: str) -> dict:
    out = tmp_path / f"{tag}.glb"
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(PROCANIM), "--", str(rig), str(out), "24",
                    "--clips", "idle"], check=True, capture_output=True, timeout=600, env=_env())
    return json.loads((tmp_path / f"{tag}_anim.json").read_text(encoding="utf-8"))


@needs
def test_planted_unweighted_pole_bone_is_not_a_leg(tmp_path):
    rig = _planted(tmp_path) / "planted_pole.glb"
    r = _read(tmp_path, rig, "pole")
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2
    assert "PoleLow" not in [leg["leaf"] for leg in r["legs"]]


@needs
def test_planted_real_extra_leg_pair_adds_two_legs(tmp_path):
    rig = _planted(tmp_path) / "planted_legs.glb"
    r = _read(tmp_path, rig, "legs")
    assert r["kind"] == "quadruped"
    assert len(r["legs"]) == 4
    assert {"ExtraLowerLeg.L", "ExtraLowerLeg.R"} <= {leg["leaf"] for leg in r["legs"]}


@needs
def test_planted_weighted_leg_with_unweighted_toe_leaves_is_still_a_leg(tmp_path):
    rig = _planted(tmp_path) / "planted_toes.glb"
    r = _read(tmp_path, rig, "toes")
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2
    assert {"Toe_End.L", "Toe_End.R"} == {leg["leaf"] for leg in r["legs"]}


@needs
def test_wp72_knight_male_reads_as_a_biped_with_two_legs(tmp_path):
    r = _read(tmp_path, KNIGHT, "knight")
    assert r["kind"] == "biped"
    assert len(r["legs"]) == 2
