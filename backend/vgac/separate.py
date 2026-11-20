"""Separate a limb the 3D build grew into the body: the backend side of blender/separate.py (WP-25).

Takes parameters only: which arm (`side`), the gap to open (`gap`, metres) and how far down the upper arm the cut may
start (`start`, a share of the upper arm from the shoulder joint). The rig's skin weights say where the arm is; the
step 4 model is cut, capped and checked in Blender, and the result is a new GLB and a report. The caller (WP-26's
Shape tab job) makes the new step 4 version from it; nothing here touches the library.

Report keys the caller reads: ok (no new open edges, edges with three or more faces, pieces or crossing triangles),
check, input.check, sides.{L,R}.caps[*] (ok, reason, holes, cap_triangles, new_armpit_m), uv (placed, scale).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .tools import ToolError, run_blender

SIDES = ("L", "R", "both")
GAP_RANGE = (0.002, 0.02)  # m
START_RANGE = (0.15, 0.6)  # share of the upper arm
DEFAULT_GAP = 0.006
DEFAULT_START = 0.3


def check_params(side: str = "both", gap: float = DEFAULT_GAP, start: float = DEFAULT_START) -> dict[str, Any]:
    """The parameters as the Blender script takes them, or ValueError naming the one out of range."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {', '.join(SIDES)}")
    gap, start = float(gap), float(start)
    if not GAP_RANGE[0] <= gap <= GAP_RANGE[1]:
        raise ValueError(f"gap must be {GAP_RANGE[0] * 1000:.0f} to {GAP_RANGE[1] * 1000:.0f} mm")
    if not START_RANGE[0] <= start <= START_RANGE[1]:
        raise ValueError(f"start must be {START_RANGE[0]} to {START_RANGE[1]} of the upper arm")
    return {"side": side, "gap": gap, "start": start}


def separate(settings: Settings, step4_glb: Path, rig_glb: Path, out_glb: Path, *, side: str = "both",
             gap: float = DEFAULT_GAP, start: float = DEFAULT_START, rigged_out: Path | None = None,
             timeout: float | None = 600) -> dict[str, Any]:
    """Run blender/separate.py on a step 4 model with the labels of its rig -> the report. out_glb (and rigged_out,
    the cut model on the rig's skeleton, for previews) are written next to report.json and separate.log."""
    p = check_params(side, gap, start)
    for f in (step4_glb, rig_glb):
        if not Path(f).is_file():
            raise ToolError(f"missing input model: {f}")
    out_glb = Path(out_glb)
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    report_path = out_glb.parent / "report.json"
    report_path.unlink(missing_ok=True)
    args: list[Any] = [Path(step4_glb), Path(rig_glb), out_glb, "--report", report_path,
                       "--side", p["side"], "--gap", p["gap"], "--start", p["start"]]
    if rigged_out:
        args += ["--rigged-out", Path(rigged_out)]
    run_blender(settings, "separate.py", args, log_path=out_glb.parent / "separate.log", timeout=timeout)
    if not report_path.is_file():
        raise ToolError("the separation wrote no report (see separate.log)")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["params"] = p
    return report


def summary(report: dict[str, Any]) -> dict[str, Any]:
    """The few numbers a person or a check reads: whole, cut pieces made and skipped, and the measures."""
    caps = [c for s in report.get("sides", {}).values() for c in s.get("caps", [])]
    ck = report.get("check", {})
    return {"ok": bool(report.get("ok")), "cuts": sum(1 for c in caps if c.get("ok")),
            "skipped": [c.get("reason") for c in caps if not c.get("ok")],
            "open_edges": ck.get("open_edges"), "bad_edges": ck.get("bad_edges"), "pieces": ck.get("shells"),
            "new_crossings": ck.get("new_crossings"), "caps_placed": report.get("uv", {}).get("placed", 0)}
