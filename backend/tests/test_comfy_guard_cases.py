"""The shared guard cases (setup/guard-cases.json) run against the backend's own guard, so the starter's C# copy and this one
cannot drift apart: both read the same file."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vgac import comfy_install as ci

CASES = json.loads((Path(__file__).resolve().parents[2] / "setup" / "guard-cases.json").read_text(encoding="utf-8"))["cases"]


def _build(tmp: Path, steps: list[list[str]]) -> None:
    for step in steps:
        kind = step[0]
        if kind == "mkdir":
            (tmp / step[1]).mkdir(parents=True, exist_ok=True)
        elif kind == "file":
            p = tmp / step[1]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        elif kind == "marker":
            (tmp / step[1]).mkdir(parents=True, exist_ok=True)
            (tmp / step[1] / ci.MARKER).write_text(json.dumps({"made_by": ci.MADE_BY}), encoding="utf-8")
        elif kind == "junction":
            link = tmp / step[1]
            link.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(tmp / step[2])], check=True, capture_output=True)
        else:  # pragma: no cover
            raise AssertionError(step)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_guard_case(case, tmp_path, monkeypatch):
    _build(tmp_path, case["setup"])
    monkeypatch.setattr(ci, "install_root", lambda: tmp_path / case["root"])
    fence = ci.Fence()
    for f in case["fence"]:
        fence.add(tmp_path / f)
    target = tmp_path / case["target"]
    if case["expect"] == "allow":
        assert ci.guard(target, fence) == target
    else:
        with pytest.raises(ci.NotOurs):
            ci.guard(target, fence)
