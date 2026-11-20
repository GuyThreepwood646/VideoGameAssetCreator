"""Separate a limb the 3D build grew into the body: the backend side of blender/separate.py (WP-25).

Takes parameters only: which arm (`side`), the gap to open (`gap`, metres) and how far down the upper arm the cut may
start (`start`, a share of the upper arm from the shoulder joint). The rig's skin weights say where the arm is; the
step 4 model is cut, capped and checked in Blender, and the result is a new GLB and a report. The callers (WP-26's
Shape tab job, which makes a new step 4 version from it, and the rig step, which cuts an arm the joined-limb measure
finds joined, WP-75) keep what they need; nothing here touches the library. recolour() colours a cut rig's caps again
from a repainted picture.

Report keys the caller reads: changed (a new model was written; false means keep the one you have, and `messages`
says why), ok (no new open edges, edges with three or more faces, pieces or crossing triangles), check, input.check,
sides.{L,R}.caps[*] (ok, reason, holes, cap_triangles, new_armpit_m), sides.{L,R}.stretch (edges near the cut, and over
the whole model, stretched in a 90 degree raise of that arm, before and after), sides.{L,R}.weights (the weights derived
again round the cut, WP-27), uv (placed, scale, cap_islands with each cap's colour match at its ring), messages (plain
lines for the person). Beside the model: caps.json (CAPS_FILE: each cap's texture triangles, so the paint tools can
find the caps) and, with rigged_out, the cut model on the rig's skeleton in the rig's rest pose (rig.carry's input).
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
CAPS_FILE = "caps.json"  # written beside the separated model: each cap's texture triangles, side and colour match
CAPS_FORMAT = 1
STILL_STRETCHED = 0.025  # share of the edges near a cut still stretched in the raise worth telling the person about
SIDE_NAME = {"L": "left", "R": "right"}
REFUSED = {  # why an arm was left as it was, in the person's words
    "not_upper_arm": "its skin weights start on the forearm, so it is not an upper arm grown into the body",
    "stretch_rose": "cutting it made raising the arm stretch the model more, not less",
    "no_arm_bones": "the rig has no bones for that arm",
}


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
    # the caps' texture triangles go to caps.json beside the model (the paint tools find the caps there); the report
    # keeps each cap's few numbers
    islands = (report.get("uv") or {}).get("cap_islands") or []
    caps_path = out_glb.parent / CAPS_FILE
    caps_path.unlink(missing_ok=True)
    if islands and report.get("changed"):
        caps_path.write_text(json.dumps({"format": CAPS_FORMAT, "caps": islands}), encoding="utf-8")
    for isl in islands:
        isl.pop("uv_tris", None)
    if report.get("changed") and not report.get("ok"):  # never hand back a model less whole than the one given
        report["changed"] = False
        report["reason"] = report.get("reason") or "worse"
        out_glb.unlink(missing_ok=True)
        if rigged_out:
            Path(rigged_out).unlink(missing_ok=True)
        caps_path.unlink(missing_ok=True)
    report["messages"] = messages(report)
    return report


def messages(report: dict[str, Any]) -> list[str]:
    """Plain lines for the person: what was separated, what was left and why, and what still stretches."""
    lines: list[str] = []
    if report.get("reason") == "worse":
        return ["The model was not changed: the cut left it less whole than before."]
    for side, sd in sorted(report.get("sides", {}).items()):
        name = SIDE_NAME.get(side, side)
        caps = sd.get("caps", [])
        if any(c.get("ok") for c in caps) and report.get("changed"):
            st = sd.get("stretch", {})
            near, after = st.get("near_edges_after", 0), st.get("stretched_after", 0)
            if near and after / near > STILL_STRETCHED:
                share = round(100 * after / near)
                lines.append(f"The {name} arm is separate, but the skin near the cut still stretches when the arm is "
                             f"raised ({share} percent of it). The next rig keeps these weights; Try again on the rig "
                             "step makes them from scratch.")
            else:
                lines.append(f"The {name} arm is separate.")
            continue
        why = sd.get("reason") or next((c.get("reason") for c in caps if c.get("reason") in REFUSED), None)
        if why in REFUSED:
            lines.append(f"The {name} arm was not changed: {REFUSED[why]}.")
        elif caps:
            lines.append(f"The {name} arm was not changed: the cut could not be closed cleanly there.")
        else:
            lines.append(f"The {name} arm was not changed: its weights show no join to the body.")
    return lines


def summary(report: dict[str, Any]) -> dict[str, Any]:
    """The few numbers a person or a check reads: whole, cut pieces made and skipped, and the measures."""
    caps = [c for s in report.get("sides", {}).values() for c in s.get("caps", [])]
    ck = report.get("check", {})
    return {"ok": bool(report.get("ok")), "changed": bool(report.get("changed")),
            "cuts": sum(1 for c in caps if c.get("ok")),
            "skipped": [c.get("reason") for c in caps if not c.get("ok")],
            "open_edges": ck.get("open_edges"), "bad_edges": ck.get("bad_edges"), "pieces": ck.get("shells"),
            "new_crossings": ck.get("new_crossings"), "caps_placed": report.get("uv", {}).get("placed", 0)}


def recolour(settings: Settings, rig_glb: Path, caps_json: Path, picture: Path, out_png: Path,
             timeout: float | None = 600) -> dict[str, Any]:
    """WP-75: the caps of a separated rig coloured again from a new picture (a repainted clean-up), the way the cut
    coloured them (blender/separate.py --recolour). The caps are found by caps.json's texture triangles; every other
    texel of `picture` is kept. -> the report: ok, recoloured (caps coloured), caps [{side, of, triangles, found,
    ring_colour_diff, ...}], reason when ok is false. out_png is written only when ok."""
    for f in (rig_glb, caps_json, picture):
        if not Path(f).is_file():
            raise ToolError(f"missing input: {f}")
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    report_path = out_png.parent / "recolour.json"
    report_path.unlink(missing_ok=True)
    run_blender(settings, "separate.py", ["--recolour", Path(rig_glb), Path(caps_json), Path(picture), out_png,
                                          "--report", report_path],
                log_path=out_png.parent / "recolour.log", timeout=timeout)
    if not report_path.is_file():
        raise ToolError("the cap recolour wrote no report (see recolour.log)")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("ok"):
        out_png.unlink(missing_ok=True)
    return report
