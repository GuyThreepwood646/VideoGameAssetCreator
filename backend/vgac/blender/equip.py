"""Blender headless: put items on a character's attachment points and check they stay clear of the body.

Run: blender -b --python equip.py -- <character.glb> <equip_input.json> <out_equipped.glb> <report.json>
         [--clips attack,walk,run] [--frames 12]

equip_input.json (vgac/items.py equip): {items: [{label, item, socket, glb, position, rotation (x, y, z, w), scale,
held}]}, every offset in the attachment point's own glTF frame. Each item's GLB is imported and parented to the
socket's empty (which the glTF importer parents to its bone), placed at rest so it follows every clip. The result is
exported as one GLB with the character's clips (NLA tracks, as pack.py writes them).

Clearance (check item_clear_of_body): for each named clip, frames spread over the strip; at each frame the skinned
body is evaluated and every sampled item point found inside it (behind the nearest surface, BVH) gives a depth. Points
within HOLD_RADIUS of a hand or mouth socket are skipped: that part is meant to be in the hand. A frame counts only
when at least MIN_POINTS item points are inside, so one stray vertex does not fail it.
"""
import json
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from mathutils import Matrix, Quaternion, Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

HOLD_RADIUS = 0.12   # metres around a hand socket that may be inside the fist
MIN_POINTS = 3
SAMPLE = 600

_VALUE_FLAGS = ("--clips", "--frames")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
char_glb, spec_p, out_glb, rep_p = (os.path.abspath(p) for p in _pos[:4])
clip_names = [c.strip() for c in _common.opt("--clips", "attack,walk,run").split(",") if c.strip()]
nframes = int(_common.opt("--frames", "12"))
with open(spec_p, encoding="utf-8") as f:
    spec = json.load(f)

_common.open_glb(char_glb)
scene = bpy.context.scene
arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
body = [o for o in scene.objects if o.type == "MESH" and not o.name.startswith("Socket_")]
# Only the first detail level is the body (the GLB carries one mesh; LOD files are separate).
body = [o for o in body if "_LOD" not in o.name or o.name.endswith("_LOD0")] or body
report = {"ok": False, "items": [], "clearance": None, "warnings": []}
ad = arm.animation_data if arm else None
if ad:
    ad.action = None
tracks = list(ad.nla_tracks) if ad else []
for t in tracks:
    t.mute = True
if arm:
    arm.data.pose_position = "REST"
bpy.context.view_layer.update()

# glTF (Y up) <-> Blender (Z up): g = C b.
C = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
Ci = C.inverted()
placed = []
for it in spec.get("items") or []:
    sock = scene.objects.get(it["socket"])
    if sock is None:
        report["warnings"].append(f"{it['socket']} not in the character file; {it['item']} skipped")
        continue
    before = set(scene.objects)
    _common.import_glb(it["glb"])
    new = [o for o in scene.objects if o not in before]
    roots = [o for o in new if o.parent is None or o.parent not in new]
    holder = bpy.data.objects.new("Item_" + "".join(ch if ch.isalnum() else "_" for ch in it["label"]), None)
    scene.collection.objects.link(holder)
    for o in roots:
        mw = o.matrix_world.copy()
        o.parent = holder
        o.matrix_world = mw  # the item's own file frame, relative to the holder at the origin
    bpy.context.view_layer.update()
    S_b = sock.matrix_world.copy()
    S_g = C @ S_b @ Ci
    q = it.get("rotation") or [0, 0, 0, 1]
    A_g = Matrix.Translation(Vector(it.get("position") or (0, 0, 0))) @ Quaternion((q[3], q[0], q[1], q[2])).to_matrix().to_4x4() \
        @ Matrix.Diagonal((it.get("scale") or 1.0,) * 3 + (1.0,))
    holder.parent = sock
    holder.matrix_world = Ci @ (S_g @ A_g) @ C
    bpy.context.view_layer.update()
    meshes = [o for o in new if o.type == "MESH"]
    placed.append({"spec": it, "holder": holder, "meshes": meshes, "socket": sock})
    report["items"].append({"item": it["item"], "socket": it["socket"], "objects": len(meshes),
                            "socket_world_gltf": [round(x, 5) for x in (C @ S_b).to_translation()]})

if arm:
    arm.data.pose_position = "POSE"
bpy.context.view_layer.update()


def _samples(obj):
    vs = obj.data.vertices
    step = max(1, len(vs) // max(1, SAMPLE // max(1, len(placed))))
    return [vs[i].co.copy() for i in range(0, len(vs), step)]


def _crossings(tree, origin, direction, limit=64):
    n, o = 0, origin.copy()
    for _ in range(limit):
        hit, _nrm, _idx, _d = tree.ray_cast(o, direction, 10.0)
        if hit is None:
            break
        n += 1
        o = hit + direction * 1e-4
    return n


def _inside(tree, w):
    return all(_crossings(tree, w, d) % 2 == 1 for d in (Vector((1, 0.013, 0.007)).normalized(), Vector((-0.011, 0.009, 1)).normalized()))


samples = {id(p): [(m, _samples(m)) for m in p["meshes"]] for p in placed}
by_name = {t.name: t for t in tracks}
clear = {"measured": False, "clips": [], "worst_depth_m": 0.0, "worst_clip": "", "worst_frame": None, "worst_label": "",
         "per_clip": {}, "hold_radius_m": HOLD_RADIUS, "frames": nframes}
if placed and arm and body:
    dg = bpy.context.evaluated_depsgraph_get()
    for name in clip_names:
        track = by_name.get(name)
        if track is None or not track.strips:
            continue
        for t in tracks:
            t.mute = (t != track)
        st = track.strips[0]
        worst = (0.0, None, "")
        for i in range(nframes):
            fr = int(round(st.frame_start + (st.frame_end - st.frame_start) * i / max(1, nframes - 1)))
            scene.frame_set(fr)
            dg = bpy.context.evaluated_depsgraph_get()
            trees = []
            for b in body:
                be = b.evaluated_get(dg)
                me = be.to_mesh()
                mw = be.matrix_world
                verts = [mw @ v.co for v in me.vertices]
                polys = [tuple(p.vertices) for p in me.polygons]
                trees.append(BVHTree.FromPolygons(verts, polys))
                be.to_mesh_clear()
            for p in placed:
                held = bool(p["spec"].get("held"))
                sp = p["socket"].evaluated_get(dg).matrix_world.to_translation()
                depths = []
                for m, pts in samples[id(p)]:
                    mw = m.evaluated_get(dg).matrix_world
                    for co in pts:
                        w = mw @ co
                        if held and (w - sp).length < HOLD_RADIUS:
                            continue
                        for tree in trees:
                            loc, nrm, _idx, dist = tree.find_nearest(w)
                            # Inside: behind the nearest surface AND an odd number of crossings along two rays
                            # (the normal alone misleads near thin or open parts of a generated mesh).
                            if loc is not None and (w - loc).dot(nrm) < 0 and _inside(tree, w):
                                depths.append(dist)
                                break
                depths.sort(reverse=True)
                d = depths[MIN_POINTS - 1] if len(depths) >= MIN_POINTS else 0.0
                if d > worst[0]:
                    worst = (d, fr, p["spec"]["label"])
        clear["clips"].append(name)
        clear["per_clip"][name] = {"worst_depth_m": round(worst[0], 4), "frame": worst[1], "item": worst[2]}
        if worst[0] > clear["worst_depth_m"]:
            clear.update(worst_depth_m=round(worst[0], 4), worst_clip=name, worst_frame=worst[1], worst_label=worst[2])
    clear["measured"] = bool(clear["clips"])
    if not clear["worst_label"] and placed:
        clear["worst_label"] = placed[0]["spec"]["label"]
report["clearance"] = clear

# Export: every clip (NLA tracks), the body, the sockets and the items under them.
for t in tracks:
    t.mute = False
if ad:
    ad.action = None
scene.frame_set(0)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=out_glb, export_format="GLB", use_selection=True, export_apply=True,
                          export_animations=bool(tracks), export_animation_mode="NLA_TRACKS", export_force_sampling=True)
report["ok"] = os.path.isfile(out_glb)
with open(rep_p, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=1)
print("VGAC_EQUIP", json.dumps({"ok": report["ok"], "items": len(placed), "clearance": clear.get("worst_depth_m")}))
