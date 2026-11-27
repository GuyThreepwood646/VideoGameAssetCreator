"""Blender headless: the eyes and their sockets move with the head, run after the other skin passes in the rig step.

Run: blender -b --python eye_skin.py -- <rig.glb> <out.glb> --rects <json> --report <json>

The clean-up's separate eyes (blender/eyes.py) use their own squares of the texture, which the rig keeps. Every vertex
whose texture coordinates lie in one of those squares, and every vertex at the same place as one (the skin on the cut
edge), gets the Head bone alone, so the eyes stay in their sockets in every move. The rest mesh, the bones and the
animation are not touched.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

SQUARE_MARGIN = 0.002  # texture coordinates this far outside a square still belong to it
SAME_PLACE_M = 0.00005


def in_squares(uv, rects, margin=SQUARE_MARGIN):
    """Which (n, 2) texture coordinates lie in one of the squares [u0, v0, side]."""
    uv = np.asarray(uv, dtype=np.float64)
    out = np.zeros(len(uv), dtype=bool)
    for u0, v0, side in rects:
        out |= ((uv[:, 0] >= u0 - margin) & (uv[:, 0] <= u0 + side + margin)
                & (uv[:, 1] >= v0 - margin) & (uv[:, 1] <= v0 + side + margin))
    return out


def with_same_place(positions, chosen, tol=SAME_PLACE_M):
    """chosen (bool per vertex), widened to every vertex at the same place as a chosen one."""
    keys = [tuple(k) for k in np.round(np.asarray(positions, dtype=np.float64) / tol).astype(np.int64)]
    marked = {k for k, c in zip(keys, chosen) if c}
    return np.array([k in marked for k in keys], dtype=bool)


def head_name(names):
    return next((n for n in names if str(n).split(":")[-1] == "Head"), None)


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    rects = json.load(open(_common.opt("--rects", None), encoding="utf-8"))
    report_path = _common.opt("--report", None)
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups and o.data.uv_layers]
    report = {"ok": False, "vertices": 0, "changed": 0}
    head = head_name([b.name for b in arm.data.bones]) if arm is not None else None
    if head is None or not meshes:
        report["reason"] = "no Head bone or no skinned mesh"
    else:
        for o in meshes:
            me = o.data
            uv = np.array([tuple(d.uv) for d in me.uv_layers.active.data])
            hit = in_squares(uv, rects)
            chosen = np.zeros(len(me.vertices), dtype=bool)
            for loop, h in zip(me.loops, hit):
                chosen[loop.vertex_index] |= bool(h)
            if not chosen.any():
                continue
            chosen = with_same_place([tuple(v.co) for v in me.vertices], chosen)
            index = [int(i) for i in np.flatnonzero(chosen)]
            for group in o.vertex_groups:
                group.remove(index)
            (o.vertex_groups.get(head) or o.vertex_groups.new(name=head)).add(index, 1.0, "REPLACE")
            report["vertices"] += len(index)
        report["ok"] = True
        report["changed"] = int(report["vertices"] > 0)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"] and report["changed"]:
        bpy.ops.object.select_all(action="DESELECT")
        keep = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("EYE_SKIN_OK" if report["ok"] else "EYE_SKIN_SKIPPED", report.get("reason", ""), report["vertices"])


if __name__ == "__main__":
    main()
