"""Blender headless: find where the hand holds an item and give the item its pivot there (vgac/items.py pivot_item).

Run: blender -b --python item_pivot.py -- <prep.glb> <out.glb> <grip.json> [--kind blade] [--grip-fraction 0.38]
         [--head-end wider|narrower]

Reads the item's prepared mesh, finds the grip with grip.py (pure numpy, next to this script) in glTF model space,
then moves and turns the mesh so the grip is at the origin and the grip frame is the item's own axes (+Z toward the
blade's tip or the item's head, +Y across the blade). Writes the moved item as a GLB (materials and textures kept)
and grip.json: the grip, the frame, the confidence and the reasons.
"""
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.dirname(HERE))  # vgac/: sockets.py (quaternion helper used by grip.to_json)
import _common  # noqa: E402
import grip  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix  # noqa: E402

_VALUE_FLAGS = ("--kind", "--grip-fraction", "--head-end")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
src, out, gj = (os.path.abspath(p) for p in _pos[:3])
kind = _common.opt("--kind", "blade")
fraction = float(_common.opt("--grip-fraction", "0.38"))
head_end = _common.opt("--head-end", "wider")

_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh in " + src)
# Blender (Z up) -> glTF (Y up): g = C b.
C = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
pts = []
rng = np.random.default_rng(7)
SAMPLES = 30000  # points spread over the surface by area, so a long blade made of two quads still has slices
for o in meshes:
    mw = np.array(o.matrix_world)
    V = np.array([v.co[:] for v in o.data.vertices], float)
    if not len(V):
        continue
    Vw = V @ mw[:3, :3].T + mw[:3, 3]
    o.data.calc_loop_triangles()
    T = np.array([t.vertices[:] for t in o.data.loop_triangles], int)
    pts.append(Vw @ C.T)
    if len(T):
        a, b, c = Vw[T[:, 0]], Vw[T[:, 1]], Vw[T[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
        if area.sum() > 0:
            k = rng.choice(len(T), size=SAMPLES, p=area / area.sum())
            u, v = rng.random(SAMPLES), rng.random(SAMPLES)
            flip = u + v > 1
            u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
            S = a[k] + u[:, None] * (b[k] - a[k]) + v[:, None] * (c[k] - a[k])
            pts.append(S @ C.T)
P = np.vstack(pts)
res = grip.find(P, kind, grip_fraction=fraction, head_end=head_end)
R = np.asarray(res["rotation"], float)
p = np.asarray(res["point"], float)
# New glTF coordinates g' = R^T (g - p); in Blender b' = C^T R^T C b - C^T R^T p.
A = C.T @ R.T @ C
t = -C.T @ R.T @ p
M = np.eye(4)
M[:3, :3] = A
M[:3, 3] = t
for o in meshes:
    mw = o.matrix_world.copy()
    o.data.transform(Matrix(M.tolist()) @ mw)
    o.matrix_world = Matrix.Identity(4)
    o.data.update()
# Parents (empties from the importer) would add their transform back: unparent and drop them.
for o in meshes:
    o.parent = None
    o.matrix_world = Matrix.Identity(4)
for o in [o for o in bpy.context.scene.objects if o.type == "EMPTY"]:
    bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_apply=True)
doc = grip.to_json(res)
lo = (P - p) @ R
doc["extents_item"] = {"min": [round(float(x), 4) for x in lo.min(axis=0)], "max": [round(float(x), 4) for x in lo.max(axis=0)]}
doc["source"] = os.path.basename(src)
with open(gj, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=1)
print("VGAC_GRIP", json.dumps({k: doc[k] for k in ("kind", "method", "confidence", "sure")}))
