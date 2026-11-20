"""The Shape tab (WP-26): separate a fused arm from the body, as a new step 4 version.

The browser sends parameters only (operation, side, gap, start, the rig version it was opened on). This module checks
them, reads the model files from the library, and runs the mesh-editing engine (separate.py, WP-25). Two calls:

- preview(): computes where the cut would start on each upper arm from the rig's skeleton. It runs no Blender job and
  writes no version. The page shades the band it names.
- run(): the job behind Apply. It makes a new step 4 (clean-up) version from the clean-up the rig was made from. The
  source version is never changed. A refusal (the engine's `changed` is false) raises, so the job fails with the plain
  reasons and the new version carries no model.

The new version is marked `shaped`, not `painted`: its geometry is new, so it must not claim the rig of the old
geometry (rig.retexture would reuse that rig's weights). Its picture is kept from the source. Adjust and Refine are
refused on it (paintsave.refusal), since they rebuild the clean-up from the mesh and would drop the cut.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import library, separate
from .db import Version
from .tools import ToolError

OPERATIONS = {"separate": "Separate a fused arm"}
# Metrics a painted or polished version carries that a separated one must not inherit.
_NOT_CARRIED = ("painted", "painted_of", "geometry_of", "polished", "shaped", "shape_of", "shape_log", "elapsed_seconds",
                "checks", "checks_passed", "checks_failed", "gate_ok", "refit_of")
_NOT_CARRIED_PREFIX = ("paint_", "polish_")
REFUSAL_NOTE = "Nothing was changed: the model stays as it was."


class ShapeRefused(ValueError):
    """The engine left the model as it was: the message is the plain reason(s) for the person."""


def parse(params: dict[str, Any] | None) -> dict[str, Any]:
    """The checked parameters of a shape job or preview, or ValueError naming the bad one."""
    params = dict(params or {})
    op = params.get("operation", "separate")
    if op not in OPERATIONS:
        raise ValueError("choose an operation from the list")
    p = separate.check_params(params.get("side", "both"), params.get("gap", separate.DEFAULT_GAP),
                              params.get("start", separate.DEFAULT_START))
    return {"operation": op, **p}


def rig_source(db: Any, rig: Version) -> Version:
    """The step 4 version a rig was made from (the nearest clean-up up its parent chain). ValueError when there is none."""
    cur: Version | None = db.get(Version, rig.parent_version_id) if rig.parent_version_id else None
    seen: set[str] = set()
    while cur is not None and cur.id not in seen:
        if cur.stage == "prep":
            return cur
        seen.add(cur.id)
        cur = db.get(Version, cur.parent_version_id) if cur.parent_version_id else None
    raise ValueError("this rig has no clean-up step to shape")


def _joint(scene: Any, names: list[str]) -> list[float] | None:
    for n in names:
        if n in scene.graph.nodes_geometry or n in scene.graph.nodes:
            m = scene.graph.get(n)[0]
            return [float(x) for x in m[:3, 3]]
    return None


def preview(rig_glb: Path, *, start: float = separate.DEFAULT_START, gap: float = separate.DEFAULT_GAP) -> dict[str, Any]:
    """Where each arm's cut would start, from the rig's skeleton: a plane across the upper arm at `start` of its length
    from the shoulder joint. An estimate for the person to look at: the engine follows the weight line inside the fused
    strip, so the real cut sits near this plane, not exactly on it. Writes nothing."""
    import numpy as np
    import trimesh

    p = separate.check_params("both", gap, start)
    scene = trimesh.load(str(rig_glb))
    if not isinstance(scene, trimesh.Scene):
        return {"arms": {}, "messages": ["The rig has no skeleton to measure the arms from."]}
    arms: dict[str, Any] = {}
    for side, prefix in (("L", "Left"), ("R", "Right")):
        shoulder = _joint(scene, [f"mixamorig:{prefix}Arm", f"{prefix}Arm"])
        elbow = _joint(scene, [f"mixamorig:{prefix}ForeArm", f"{prefix}ForeArm"])
        if shoulder is None or elbow is None:
            continue
        s, e = np.asarray(shoulder), np.asarray(elbow)
        u = e - s
        length = float(np.linalg.norm(u))
        if length < 1e-6:
            continue
        axis = u / length
        at = s + p["start"] * u
        arms[side] = {"shoulder": shoulder, "elbow": elbow, "upper_arm_m": round(length, 4),
                      "cut_start": [float(x) for x in at], "axis": [float(x) for x in axis],
                      "half_band_m": round(max(p["gap"], 0.01) / 2, 4),
                      "radius_m": round(0.35 * length, 4)}
    messages = []
    for side, a in sorted(arms.items()):
        name = separate.SIDE_NAME[side]
        messages.append(f"The {name} cut would start {round(p['start'] * 100)} percent of the way down the upper arm.")
    if not arms:
        messages.append("The rig has no arm bones to show a cut on.")
    return {"arms": arms, "params": p, "messages": messages}


def _extract_picture(glb: Path, out: Path) -> bool:
    """Write the model's base colour picture (the engine has painted the caps into it) to `out`. False when there is none."""
    import trimesh

    scene = trimesh.load(str(glb))
    geoms = list(scene.geometry.values()) if isinstance(scene, trimesh.Scene) else [scene]
    for g in geoms:
        mat = getattr(getattr(g, "visual", None), "material", None)
        tex = getattr(mat, "baseColorTexture", None)
        if tex is not None:
            tex.convert("RGBA").save(out)
            return True
    return False


def _metrics_carried(parent: Version) -> dict[str, Any]:
    return {k: v for k, v in (parent.metrics or {}).items()
            if k not in _NOT_CARRIED and not k.startswith(_NOT_CARRIED_PREFIX)}


async def run(sc: Any, params: dict[str, Any]) -> None:
    """The job behind Apply (stage prep, op shape). Runs on the host. Raises ShapeRefused when nothing was changed."""
    import asyncio
    import shutil

    from . import paintsave

    parent = sc.parent
    if parent is None or parent.stage != "prep" or not parent.files:
        raise ValueError("the arm can only be separated on a clean-up step")
    p = parse(params)
    rig = sc.db.get(Version, str(params.get("rig_version_id") or ""))
    if rig is None:
        raise ValueError("the rig this was opened on is missing")
    rig_glb = library.version_file(rig, "glb", sc.settings)
    step4 = library.version_file(parent, "glb", sc.settings)
    if rig_glb is None or step4 is None:
        raise ValueError("the rig or the clean-up has no model to separate")

    paintsave._copy_parent_files(sc, parent)
    work = sc.dir / "shape_work"
    out_glb = work / Path(step4).name
    sc.progress(0.1, "separating the arm in Blender")
    timeout = float(sc.settings.blender_timeout_seconds)

    def work_it() -> dict[str, Any]:
        return separate.separate(sc.settings, Path(step4), Path(rig_glb), out_glb, side=p["side"], gap=p["gap"],
                                 start=p["start"], timeout=timeout)

    report = await asyncio.to_thread(work_it)
    if not report.get("changed"):
        why = " ".join(report.get("messages") or ["The cut could not be made on this model."])
        raise ShapeRefused(f"{why} {REFUSAL_NOTE}")

    final_glb = sc.dir / Path(step4).name
    shutil.copyfile(out_glb, final_glb)
    sc.register("glb", final_glb)
    picture = sc.dir / "base_color.png"
    if _extract_picture(final_glb, picture):
        sc.register("base_color", picture)
    shutil.rmtree(work, ignore_errors=True)
    log = {"operation": OPERATIONS[p["operation"]], "parameters": {"side": p["side"], "gap": p["gap"], "start": p["start"]},
           "rig_version": rig.id, "engine": separate.summary(report), "messages": report.get("messages", [])}
    sc.set_metrics(**_metrics_carried(parent), shaped=True, shape_of=parent.id, shape_log=log)
    sc.set_params(shape_of=parent.id, shape_operation=p["operation"])
