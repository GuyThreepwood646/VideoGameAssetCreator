"""The attack-grip guard (vgac/grip_guard.py rule, rig._grip_guard wiring): built-in attacks whose hands tear past the
bar are rebuilt with a smaller grip; rigs under the bar and library attacks are left alone."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import yaml

from tests.test_animation_quality import _lib_dir
from vgac import grip_guard
from vgac.stages import rig

HUMAN_CATEGORY = yaml.safe_load((Path(__file__).resolve().parents[1] / "vgac" / "data" / "categories" / "humanoid.yaml").read_text(encoding="utf-8"))


def test_trials_only_follow_a_share_at_or_over_the_bar():
    assert grip_guard.trial_scales(None) == ()
    assert grip_guard.trial_scales(0.0) == ()
    assert grip_guard.trial_scales(0.0699) == ()
    assert grip_guard.trial_scales(0.07) == (0.75, 0.5, 0.35)


def test_choose_keeps_the_first_step_under_the_bar():
    assert grip_guard.choose([]) == (1.0, None)
    assert grip_guard.choose([(0.75, 0.05)]) == (0.75, None)
    assert grip_guard.choose([(0.75, 0.09), (0.5, 0.06), (0.35, 0.01)]) == (0.5, None)
    assert grip_guard.choose([(0.75, None)]) == (0.75, None)  # no hand edges to tear counts as under the bar


def test_choose_stops_at_the_floor_and_warns():
    scale, warning = grip_guard.choose([(0.75, 0.10), (0.5, 0.09), (0.35, 0.08)])
    assert scale == 0.35
    assert warning and "0.35" in warning and "tear" in warning


class _Blender:
    """Stands in for the Blender calls the guard makes. procanim writes a file whose name carries the grip; the stretch
    measure returns the share a synthetic hand has at that grip."""

    def __init__(self, shares):
        self.shares = shares  # grip scale (float) -> hands over-share at that grip
        self.calls = []

    async def __call__(self, script, args, log_name=None, timeout=None):
        self.calls.append((script, [str(a) for a in args]))
        if script == "procanim.py":
            grip = float(args[args.index("--grip-scale") + 1]) if "--grip-scale" in args else 1.0
            Path(args[1]).write_bytes(f"grip {grip}".encode())
        elif script == "skin_stretch.py":
            glb, out = Path(args[0]), Path(args[1])
            grip = float(glb.read_bytes().decode().split()[1]) if glb.read_bytes().startswith(b"grip") else 1.0
            share = self.shares[grip]
            out.write_text(json.dumps({"clips": {"attack": {"regions": {"hands": {"edges": 9, "over_share": share, "max_ratio": 2.0}}}}}))
        return ""


def _sc(tmp_path, category, libs=None, blender=None):
    sc = SimpleNamespace()
    sc.dir = tmp_path
    sc.category = category
    sc.settings = SimpleNamespace(clips_libraries=libs or {}, blender_timeout_seconds=60)
    sc.version = SimpleNamespace(metrics={}, params={})
    sc.set_params = lambda **kw: sc.version.params.update(kw)
    sc.set_metrics = lambda **kw: sc.version.metrics.update(kw)
    sc.blender = blender
    return sc


def _build(out, clips, grip):
    args = [Path("rig.glb"), out, 24, "--clips", clips or "none"]
    if grip != 1.0:
        args += ["--grip-scale", f"{grip:g}"]
    return args


def _run(tmp_path, shares, category=None, libs=None, moves=None, chosen=None):
    blender = _Blender(shares)
    sc = _sc(tmp_path, category or {"id": "creature", "rig_engine": "none"}, libs, blender)
    glb = tmp_path / "anim.glb"
    glb.write_bytes(b"grip 1.0")
    asyncio.run(rig._grip_guard(sc, glb, moves, {}, chosen, _build))
    return sc, blender, glb


def test_a_built_in_attack_under_the_bar_is_not_rebuilt(tmp_path):
    sc, blender, glb = _run(tmp_path, {1.0: 0.02})
    assert [c[0] for c in blender.calls] == ["skin_stretch.py"]
    assert sc.version.metrics["hand_grip_guard"]["grip_scale"] == 1.0 and glb.read_bytes() == b"grip 1.0"


def test_an_attack_over_the_bar_is_rebuilt_at_the_first_grip_under_it(tmp_path):
    sc, blender, glb = _run(tmp_path, {1.0: 0.10, 0.75: 0.05, 0.5: 0.02, 0.35: 0.01}, moves=["idle", "attack"])
    scripts = [c[0] for c in blender.calls]
    assert scripts == ["skin_stretch.py", "procanim.py", "skin_stretch.py", "procanim.py"]  # trial at 0.75, then the full rebuild
    full = blender.calls[-1][1]
    assert "--grip-scale" in full and full[full.index("--grip-scale") + 1] == "0.75"
    assert full[full.index("--clips") + 1] == "idle,attack"
    rec = sc.version.metrics["hand_grip_guard"]
    assert rec["grip_scale"] == 0.75 and rec["warning"] is None and rec["default_share"] == 0.10
    assert glb.read_bytes() == b"grip 0.75"


def test_an_attack_still_over_the_bar_at_the_floor_keeps_the_floor_with_a_warning(tmp_path):
    sc, blender, glb = _run(tmp_path, {1.0: 0.11, 0.75: 0.09, 0.5: 0.08, 0.35: 0.075})
    rec = sc.version.metrics["hand_grip_guard"]
    assert rec["grip_scale"] == 0.35 and rec["warning"] and "tear" in rec["warning"]
    assert [t["grip_scale"] for t in rec["trials"]] == [0.75, 0.5, 0.35]


def test_a_library_attack_skips_the_guard(tmp_path):
    lib = {"ual1": _lib_dir(tmp_path / "lib")}
    sc, blender, glb = _run(tmp_path, {1.0: 0.2}, category=HUMAN_CATEGORY, libs=lib, moves=["attack"], chosen=None)
    assert blender.calls == []
    assert sc.version.metrics["hand_grip_guard"]["skipped"] == "the library attack replaces the built-in one"


def test_a_build_without_an_attack_skips_the_guard(tmp_path):
    sc, blender, glb = _run(tmp_path, {1.0: 0.2}, moves=["idle", "walk"])
    assert blender.calls == []
    assert sc.version.metrics["hand_grip_guard"]["skipped"] == "no attack clip in this build"
