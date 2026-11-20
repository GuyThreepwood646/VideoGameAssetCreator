"""Blender headless: shoulder skin pass on a rigged GLB (WP-75), run after the leg skin pass.

Run: blender -b --python shoulder_skin.py -- <rig.glb> <out.glb> --report <json>

The moves lift the collarbone when an arm goes past 60 degrees (gait.clavicle_lift_deg). The collarbone turns about
its inner end, so skin it owns that stands high above it (a hood, a tall collar, hair lying on the shoulder) swings
in toward the neck like a lever and leaves a peak over the shoulder. This pass hands that skin to the collarbone's
parent (the chest), which does not lift: on each side, collarbone weight on a vertex whose height above the
collarbone's line (its head to the upper arm's head) is more than FADE_FROM of the shoulder width (upper-arm joint to
upper-arm joint) fades to none at FADE_TO. Nothing else changes: other bones' weights, vertices under FADE_FROM, the
rest mesh, the bones. Rows stay normalised. Only a Mixamo-named skeleton with both Shoulder and Arm bones is touched.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

FADE_FROM = 0.28  # share of the shoulder width above the collarbone line where the collarbone's weight starts to fade
FADE_TO = 0.40    # ... and where it is gone (measured: human shoulder tops reach about 0.27, the goblin's hood 0.39)


def keep_share(height_w, fade_from=FADE_FROM, fade_to=FADE_TO):
    """Share of the collarbone's weight a vertex keeps, from its height above the collarbone line in shoulder widths."""
    return np.clip((fade_to - np.asarray(height_w, dtype=float)) / (fade_to - fade_from), 0.0, 1.0)


def heights_above_line(co, head, tip, width):
    """Height (z) of each point's offset from the line through head and tip, square to the line, in widths."""
    co = np.asarray(co, dtype=float)
    head = np.asarray(head, dtype=float)
    ax = np.asarray(tip, dtype=float) - head
    n = np.linalg.norm(ax)
    if n < 1e-9 or width <= 1e-9:
        return np.zeros(len(co))
    ax = ax / n
    d = co - head
    perp = d - np.outer(d @ ax, ax)
    return perp[:, 2] / width


def hand_over(W, clav_col, parent_col, keep, side_mask):
    """Move (1 - keep) of the collarbone column's weight to the parent column on the side's vertices. W: (V, G).
    Returns (new W, number of vertices changed). Row sums are unchanged."""
    W = np.array(W, dtype=float, copy=True)
    take = W[:, clav_col] * (1.0 - keep) * side_mask
    changed = int(np.count_nonzero(take > 1e-6))
    W[:, clav_col] -= take
    W[:, parent_col] += take
    return W, changed


# ---- Blender side -------------------------------------------------------------------------------------------------


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "fade_from": FADE_FROM, "fade_to": FADE_TO, "sides": {}}
    bones = arm.data.bones if arm else {}
    need = ["mixamorig:" + s + b for s in ("Left", "Right") for b in ("Shoulder", "Arm")]
    if arm is None or not meshes:
        report["reason"] = "no armature or no skinned mesh"
    elif not all(n in bones for n in need) or any(bones["mixamorig:" + s + "Shoulder"].parent is None for s in ("Left", "Right")):
        report["reason"] = "no Mixamo collarbones"
    else:
        Mw = arm.matrix_world
        head = {n: np.array(Mw @ bones[n].head_local) for n in need}
        width = float(np.linalg.norm(head["mixamorig:LeftArm"] - head["mixamorig:RightArm"]))
        mid = (head["mixamorig:LeftArm"] + head["mixamorig:RightArm"]) / 2
        lat = (head["mixamorig:LeftArm"] - head["mixamorig:RightArm"]) / max(width, 1e-9)
        for o in meshes:
            names = [g.name for g in o.vertex_groups]
            V = len(o.data.vertices)
            co = np.array([tuple(o.matrix_world @ v.co) for v in o.data.vertices], dtype=float).reshape(-1, 3)
            W = np.zeros((V, len(names)))
            for v in o.data.vertices:
                for g in v.groups:
                    W[v.index, g.group] = g.weight
            changed_any = False
            for s, sign in (("Left", 1.0), ("Right", -1.0)):
                cn = "mixamorig:" + s + "Shoulder"
                if cn not in names:
                    continue
                pn = bones[cn].parent.name
                if pn not in names:
                    o.vertex_groups.new(name=pn)
                    names.append(pn)
                    W = np.hstack([W, np.zeros((V, 1))])
                h = heights_above_line(co, head[cn], head["mixamorig:" + s + "Arm"], width)
                side_mask = (((co - mid) @ lat) * sign > 0).astype(float)
                keep = keep_share(h)
                W, n = hand_over(W, names.index(cn), names.index(pn), keep, side_mask)
                rep = report["sides"].setdefault(s, {"vertices": 0})
                rep["vertices"] += n
                rep["parent"] = pn.replace("mixamorig:", "")
                changed_any = changed_any or n > 0
            if changed_any:
                for g in o.vertex_groups:
                    g.remove(list(range(V)))
                for gi, g in enumerate(o.vertex_groups):
                    for vi in np.where(W[:, gi] > 1e-6)[0]:
                        g.add([int(vi)], float(W[vi, gi]), "REPLACE")
        report["ok"] = True
        report["changed"] = sum(r["vertices"] for r in report["sides"].values())
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"] and report.get("changed"):
        bpy.ops.object.select_all(action="DESELECT")
        keep_objs = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep_objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep_objs[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("SHOULDER_SKIN_OK" if report["ok"] else "SHOULDER_SKIN_SKIPPED", report.get("reason", ""), report.get("changed", 0))


if __name__ == "__main__":  # pragma: no cover
    main()
