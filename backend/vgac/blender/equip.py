"""Blender headless: put items on a character's attachment points, fit them to the motion and check they stay clear.

Run: blender -b --python equip.py -- <character.glb> <equip_input.json> <out_equipped.glb> <report.json>
         [--clips idle,attack,walk,run] [--frames 16] [--limit 0.03]

equip_input.json (vgac/items.py equip): {items: [{label, item, socket, glb, position, rotation (x, y, z, w), scale,
held, fit, outward}]}, every offset in the attachment point's own glTF frame. Each item's GLB is imported and parented
to the socket's empty (which the glTF importer parents to its bone), placed at rest so it follows every clip. The
result is exported as one GLB with the character's clips (NLA tracks, as pack.py writes them).

Clearance (check item_clear_of_body): for each named clip, frames spread over the strip; at each frame the skinned
body is evaluated and every sampled item point found inside it (behind the nearest surface and an odd number of
crossings along two rays) gives a depth. Points within HOLD_RADIUS of a hand or mouth socket are skipped for held
items: that part is meant to be in the hand. A frame counts the MIN_POINTS-th deepest point, so one stray vertex does
not fail it. The body is evaluated once per frame and kept, so the fit search below reuses it.

Fit to the motion (item `fit` true, items.py decides: placements the person has not adjusted, or "Fit it for me"):
a small set of changes is tried, least change first, and the first that keeps the item within --limit in every
sampled frame of every clip is kept. Strapped or worn items (not held): a push out along the attachment point's
outward axis and tilts about the pivot (itemfit.py lists the steps and the search). Held items:
the grip stays in the palm, so only a turn about the grip axis (the point's +Z) and small tilts about the palm are
tried. When nothing clears, the change with the shallowest worst depth is kept and reported with the move it
happens in. The final placement is measured again by the same function, so the check stays honest.

Also measured on the final placement (soft checks): each item's lowest point against the floor (the body's lowest
point at rest) in every measured clip, and each pair of items against each other (points of one inside the other).
"""
import json
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import itemfit  # noqa: E402  (the changes tried and the search, shared with the backend tests)
from mathutils import Matrix, Quaternion, Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

HOLD_RADIUS = 0.12   # metres around a hand socket that may be inside the fist
MIN_POINTS = 3
SAMPLE = 600
NEAR = 0.35          # a point farther than this from the body surface is outside it (no limb is thicker)

_VALUE_FLAGS = ("--clips", "--frames", "--limit", "--share-limit")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
char_glb, spec_p, out_glb, rep_p = (os.path.abspath(p) for p in _pos[:4])
clip_names = [c.strip() for c in _common.opt("--clips", "idle,attack,walk,run").split(",") if c.strip()]
nframes = int(_common.opt("--frames", "16"))
LIMIT = float(_common.opt("--limit", "0.03"))
SHARE_LIMIT = float(_common.opt("--share-limit", str(itemfit.SHARE_LIMIT)))
with open(spec_p, encoding="utf-8") as f:
    spec = json.load(f)

_common.open_glb(char_glb)
scene = bpy.context.scene
arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
body = [o for o in scene.objects if o.type == "MESH" and not o.name.startswith("Socket_")]
# Only the first detail level is the body (the GLB carries one mesh; LOD files are separate).
body = [o for o in body if "_LOD" not in o.name or o.name.endswith("_LOD0")] or body


def _mat_extras(m):
    try:
        return {k: m[k] for k in m.keys()} if m is not None else None
    except Exception:  # noqa: BLE001
        return None


# Per body object, the material slots' custom properties when one of them is hair from the library (else absent).
hair_marks = {}
for _o in body:
    _ex = [_mat_extras(m) for m in _o.data.materials]
    if any(itemfit.is_library_hair(e) for e in _ex):
        hair_marks[_o.name] = _ex
report ={"ok": False, "items": [], "clearance": None, "fit": [], "ground": None, "between": None, "warnings": []}
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
floor_z = min(((o.matrix_world @ v.co).z for o in body for v in o.data.vertices), default=0.0)


def placement(pos, q, scale):
    """The item's offset in its attachment point's glTF frame as a 4x4."""
    return Matrix.Translation(Vector(pos)) @ Quaternion((q[3], q[0], q[1], q[2])).to_matrix().to_4x4() \
        @ Matrix.Diagonal((scale,) * 3 + (1.0,))


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
    meshes = [o for o in new if o.type == "MESH"]
    # Sample points and the item's own surface in its glTF frame (pivot at the grip), fixed for every frame.
    pts, verts, polys = [], [], []
    per = max(1, SAMPLE // max(1, len(spec.get("items") or [])))
    for m in meshes:
        mw = m.matrix_world  # the holder is at the origin, so this is the item's file frame
        vs = m.data.vertices
        step = max(1, len(vs) // max(1, per // max(1, len(meshes))))
        pts += [C @ (mw @ vs[i].co) for i in range(0, len(vs), step)]
        base = len(verts)
        verts += [C @ (mw @ v.co) for v in vs]
        polys += [tuple(base + i for i in p.vertices) for p in m.data.polygons]
    q = it.get("rotation") or [0, 0, 0, 1]
    placed.append({"spec": it, "holder": holder, "meshes": meshes, "socket": sock, "points": pts,
                   "tree": BVHTree.FromPolygons(verts, polys) if polys else None,
                   "pos": Vector(it.get("position") or (0, 0, 0)), "quat": Quaternion((q[3], q[0], q[1], q[2])),
                   "scale": float(it.get("scale") or 1.0), "held": bool(it.get("held"))})


def _crossings(tree, origin, direction, limit=64):
    n, o = 0, origin.copy()
    for _ in range(limit):
        hit, _nrm, _idx, _d = tree.ray_cast(o, direction, 10.0)
        if hit is None:
            break
        n += 1
        o = hit + direction * 1e-4
    return n


RAYS = (Vector((1, 0.013, 0.007)).normalized(), Vector((-0.011, 0.009, 1)).normalized())


def _inside(tree, w):
    return all(_crossings(tree, w, d) % 2 == 1 for d in RAYS)


# --- the body, once per sampled frame -----------------------------------------------------------------------------
by_name = {t.name: t for t in tracks}
frames = []  # [{clip, frame, trees, sockets: {name: Matrix}}]
if arm:
    arm.data.pose_position = "POSE"
bpy.context.view_layer.update()
socket_names = {p["socket"].name for p in placed}
measured_clips = []
if placed and arm and body:
    for name in clip_names:
        track = by_name.get(name)
        if track is None or not track.strips:
            continue
        measured_clips.append(name)
        for t in tracks:
            t.mute = (t != track)
        st = track.strips[0]
        for i in range(nframes):
            fr = int(round(st.frame_start + (st.frame_end - st.frame_start) * i / max(1, nframes - 1)))
            scene.frame_set(fr)
            dg = bpy.context.evaluated_depsgraph_get()
            trees = []
            for b in body:
                be = b.evaluated_get(dg)
                me = be.to_mesh()
                mw = be.matrix_world
                if hair_marks.get(b.name):
                    # hair from the library is its own piece, not the body: its slot is left out of the body tree
                    polys = itemfit.body_polygons([(p.vertices, p.material_index) for p in me.polygons], hair_marks[b.name])
                else:
                    polys = [tuple(p.vertices) for p in me.polygons]
                trees.append(BVHTree.FromPolygons([mw @ v.co for v in me.vertices], polys))
                be.to_mesh_clear()
            socks = {n: scene.objects[n].evaluated_get(dg).matrix_world.copy() for n in socket_names}
            frames.append({"clip": name, "frame": fr, "trees": trees, "sockets": socks})
    for t in tracks:
        t.mute = True


def world_points(p, A, fr):
    """The item's sample points in Blender world space at a frame, for offset A (glTF socket frame)."""
    K = fr["sockets"][p["socket"].name] @ Ci @ A
    return [K @ v for v in p["points"]], fr["sockets"][p["socket"].name].to_translation()


def _escape(tree, w, d):
    """How far a point inside the body travels along d before it is outside (the first surface it crosses)."""
    hit, _n, _i, dist = tree.ray_cast(w + d * 1e-5, d, 1.0)
    return dist if hit is not None else 0.0


def frame_eval(p, A, fr):
    """One frame's {depth, share, score} for the item (itemfit.frame_score): every counted point inside the body gives
    the larger of its nearest way out and, for a strapped or worn item, its way out along the push direction."""
    W, sp = world_points(p, A, fr)
    out_dir = None
    if not p["held"]:
        out_dir = ((fr["sockets"][p["socket"].name] @ Ci).to_3x3() @ Vector(p["spec"].get("outward") or (0, 0, 1))).normalized()
    depths, counted = [], 0
    for w in W:
        if p["held"] and (w - sp).length < HOLD_RADIUS:
            continue
        counted += 1
        for tree in fr["trees"]:
            loc, nrm, _idx, dist = tree.find_nearest(w, NEAR)
            if loc is not None and (w - loc).dot(nrm) < 0 and _inside(tree, w):
                depths.append(itemfit.point_depth(dist, _escape(tree, w, out_dir) if out_dir is not None else None))
                break
    return itemfit.frame_score(depths, counted, LIMIT, SHARE_LIMIT)


def frame_depth(p, A, fr):
    """One frame's score in metres (the worse of depth and share against their limits)."""
    return frame_eval(p, A, fr)["score"]


def frame_clearance(p, A, fr):
    """How far the item is from the body at one frame: the smallest distance of any point to the body surface (0 when
    a point is inside). Used to skip frames a small change cannot reach."""
    W, _sp = world_points(p, A, fr)
    best = NEAR
    for w in W:
        for tree in fr["trees"]:
            loc, nrm, _idx, dist = tree.find_nearest(w, NEAR)
            if loc is None:
                continue
            if (w - loc).dot(nrm) < 0 and _inside(tree, w):
                return 0.0
            best = min(best, dist)
    return best


def measure(p, A, order=None, stop_above=None, skip=None):
    """Worst depth over the sampled frames -> (depth, frame index). order: frame indices to try (default all);
    stop_above: stop at the first frame deeper than this; skip(i): frames that cannot matter."""
    worst, at = 0.0, None
    for i in (order if order is not None else range(len(frames))):
        if skip is not None and skip(i):
            continue
        d = frame_depth(p, A, frames[i])
        if d > worst:
            worst, at = d, i
            if stop_above is not None and d > stop_above:
                break
    return worst, at


def _as_matrix(pos, R3, scale):
    m = Matrix([list(r) for r in R3]).to_4x4()
    return Matrix.Translation(Vector([float(x) for x in pos])) @ m @ Matrix.Diagonal((scale,) * 3 + (1.0,))


def fit(p):
    """Search the changes for one item (itemfit.py). -> the fit record and the chosen offset."""
    outward = p["spec"].get("outward") or [0, 0, 1]
    pos0 = [p["pos"].x, p["pos"].y, p["pos"].z]
    R0 = [list(r) for r in p["quat"].to_matrix()]
    A0 = _as_matrix(pos0, R0, p["scale"])
    # Frames by how close the item comes at its current place (deepest first), so a failing change fails fast; a
    # frame where the item stays farther from the body than a change can move it is skipped for that change.
    clear0 = [frame_clearance(p, A0, fr) for fr in frames]
    order = sorted(range(len(frames)), key=lambda i: clear0[i])
    if p["held"]:
        radius = max(((A0 @ v).length for v in p["points"]), default=0.5)
    else:
        piv = A0 @ Vector((0, 0, 0))
        radius = max((((A0 @ v) - piv).length for v in p["points"]), default=0.5)
    mats, where = {}, {}

    def offset(c):
        if c is None:
            return A0
        k = id(c)
        if k not in mats:
            pos, R = itemfit.apply(c, pos0, R0, outward)
            mats[k] = _as_matrix(pos, R, p["scale"])
        return mats[k]

    def depth(c, stop_above):
        if c is None or c["cost"] == 0:
            d, i = measure(p, A0, order, stop_above)
        else:
            reach = c.get("push", 0.0) + c["angle"] * radius + 0.002
            d, i = measure(p, offset(c), order, stop_above, skip=lambda k: clear0[k] > reach)
        where[id(c)] = i
        return d

    cands = itemfit.candidates(p["held"], outward)
    res = itemfit.search(cands, depth, LIMIT)
    c = res["cand"]
    A = offset(c) if res["changed"] else A0
    d1, i1 = measure(p, A)  # the chosen change, measured again on every frame
    d0, i0 = measure(p, A0)
    e0 = frame_eval(p, A0, frames[i0]) if i0 is not None else {"depth": 0.0, "share": 0.0}
    e1 = frame_eval(p, A, frames[i1]) if i1 is not None else {"depth": 0.0, "share": 0.0}
    rec = {"item": p["spec"]["item"], "label": p["spec"]["label"], "searched": True, "tried": res["tried"],
           "before_m": round(e0["depth"], 4), "before_share": round(e0["share"], 3), "after_share": round(e1["share"], 3),
           "before_score_m": round(d0, 4), "after_score_m": round(d1, 4),
           "before_clip": frames[i0]["clip"] if i0 is not None else "",
           "before_frame": frames[i0]["frame"] if i0 is not None else None,
           "outward_cm": c["outward_cm"] if res["changed"] else 0.0, "turn_deg": c["turn_deg"] if res["changed"] else 0,
           "tilt_deg": list(c["tilt_deg"]) if res["changed"] else [0, 0],
           "after_m": round(e1["depth"], 4), "after_clip": frames[i1]["clip"] if i1 is not None else "",
           "after_frame": frames[i1]["frame"] if i1 is not None else None, "cleared": d1 <= LIMIT, "changed": res["changed"]}
    return rec, A


def a_to_json(A):
    loc, rot, sc = A.decompose()
    return [round(v, 5) for v in loc], [round(rot.x, 6), round(rot.y, 6), round(rot.z, 6), round(rot.w, 6)]


# --- fit, then measure the final placement --------------------------------------------------------------------------
final = {}
for p in placed:
    A0 = placement(p["pos"], [p["quat"].x, p["quat"].y, p["quat"].z, p["quat"].w], p["scale"])
    if p["spec"].get("fit") and frames:
        rec, A = fit(p)
        rec["position"], rec["rotation"] = a_to_json(A)
        report["fit"].append(rec)
        final[id(p)] = A
    else:
        final[id(p)] = A0

clear = {"measured": False, "clips": [], "worst_depth_m": 0.0, "worst_share": 0.0, "worst_score_m": 0.0, "worst_clip": "",
         "worst_frame": None, "worst_label": "", "worst_by": "", "per_clip": {}, "per_item": {}, "hold_radius_m": HOLD_RADIUS,
         "frames": nframes, "limit_m": LIMIT, "share_limit": SHARE_LIMIT,
         "measure": "nearest way out, or the way out along the push direction for strapped or worn items; and the share inside"}
if frames:
    for name in measured_clips:
        idx = [i for i, fr in enumerate(frames) if fr["clip"] == name]
        pc = {"worst_depth_m": 0.0, "worst_share": 0.0, "score_m": 0.0, "frame": None, "item": "", "depths": [], "shares": []}
        for i in idx:
            row_d, row_s = 0.0, 0.0
            for p in placed:
                e = frame_eval(p, final[id(p)], frames[i])
                key = p["spec"]["label"]
                row_d, row_s = max(row_d, e["depth"]), max(row_s, e["share"])
                pi = clear["per_item"].setdefault(key, {"worst_depth_m": 0.0, "worst_share": 0.0, "score_m": 0.0, "clip": "", "frame": None})
                pi["worst_depth_m"] = round(max(pi["worst_depth_m"], e["depth"]), 4)
                pi["worst_share"] = round(max(pi["worst_share"], e["share"]), 3)
                if e["score"] > pi["score_m"]:
                    pi.update(score_m=round(e["score"], 4), clip=name, frame=frames[i]["frame"])
                pc["worst_depth_m"] = round(max(pc["worst_depth_m"], e["depth"]), 4)
                pc["worst_share"] = round(max(pc["worst_share"], e["share"]), 3)
                if e["score"] > pc["score_m"]:
                    pc.update(score_m=round(e["score"], 4), frame=frames[i]["frame"], item=key)
                if e["score"] > clear["worst_score_m"]:
                    clear.update(worst_score_m=round(e["score"], 4), worst_clip=name, worst_frame=frames[i]["frame"], worst_label=key,
                                 worst_by="share" if e["share"] * LIMIT / SHARE_LIMIT > e["depth"] else "depth")
            pc["depths"].append(round(row_d, 4))
            pc["shares"].append(round(row_s, 3))
        clear["clips"].append(name)
        clear["per_clip"][name] = pc
        clear["worst_depth_m"] = max(clear["worst_depth_m"], pc["worst_depth_m"])
        clear["worst_share"] = max(clear["worst_share"], pc["worst_share"])
    clear["measured"] = bool(clear["clips"])
    if not clear["worst_label"] and placed:
        clear["worst_label"] = placed[0]["spec"]["label"]
report["clearance"] = clear

# Ground: each item's lowest point against the floor (the body's lowest point at rest), death left out (it lies down).
if frames:
    g = {"measured": True, "floor_z": round(floor_z, 4), "worst_below_m": 0.0, "worst_label": "", "worst_clip": "",
         "worst_frame": None, "per_clip": {}}
    for i, fr in enumerate(frames):
        for p in placed:
            W, _ = world_points(p, final[id(p)], fr)
            zs = sorted(w.z for w in W)
            low = zs[MIN_POINTS - 1] if len(zs) >= MIN_POINTS else (zs[0] if zs else floor_z)
            below = max(0.0, floor_z - low)
            pc = g["per_clip"].setdefault(fr["clip"], {"worst_below_m": 0.0, "item": "", "frame": None})
            if below > pc["worst_below_m"]:
                pc.update(worst_below_m=round(below, 4), item=p["spec"]["label"], frame=fr["frame"])
            if below > g["worst_below_m"]:
                g.update(worst_below_m=round(below, 4), worst_label=p["spec"]["label"], worst_clip=fr["clip"], worst_frame=fr["frame"])
    report["ground"] = g

# Items against each other: points of one inside the other's own surface.
if frames and len(placed) >= 2:
    bt = {"measured": True, "worst_depth_m": 0.0, "pair": [], "worst_clip": "", "worst_frame": None, "pairs": []}
    for ai in range(len(placed)):
        for bi in range(len(placed)):
            pa, pb = placed[ai], placed[bi]
            if ai == bi or pb["tree"] is None:
                continue
            worst = (0.0, "", None)
            for fr in frames:
                Ka = fr["sockets"][pa["socket"].name] @ Ci @ final[id(pa)]
                Kb = fr["sockets"][pb["socket"].name] @ Ci @ final[id(pb)]
                to_b = Kb.inverted() @ Ka  # A's item frame -> B's item frame
                depths = []
                for v in pa["points"]:
                    w = to_b @ v
                    loc, nrm, _i, dist = pb["tree"].find_nearest(w, NEAR)
                    if loc is not None and (w - loc).dot(nrm) < 0 and _inside(pb["tree"], w):
                        depths.append(dist * pb["scale"])
                depths.sort(reverse=True)
                d = depths[MIN_POINTS - 1] if len(depths) >= MIN_POINTS else 0.0
                if d > worst[0]:
                    worst = (d, fr["clip"], fr["frame"])
            bt["pairs"].append({"a": pa["spec"]["label"], "b": pb["spec"]["label"], "worst_depth_m": round(worst[0], 4),
                                "clip": worst[1], "frame": worst[2]})
            if worst[0] > bt["worst_depth_m"]:
                bt.update(worst_depth_m=round(worst[0], 4), pair=[pa["spec"]["label"], pb["spec"]["label"]],
                          worst_clip=worst[1], worst_frame=worst[2])
    report["between"] = bt

# Place every item at its final offset on the rest pose (the holder follows its socket in every clip).
if arm:
    arm.data.pose_position = "REST"
scene.frame_set(0)
bpy.context.view_layer.update()
for p in placed:
    S_b = p["socket"].matrix_world.copy()
    S_g = C @ S_b @ Ci
    p["holder"].parent = p["socket"]
    p["holder"].matrix_world = Ci @ (S_g @ final[id(p)]) @ C
    bpy.context.view_layer.update()
    report["items"].append({"item": p["spec"]["item"], "socket": p["spec"]["socket"], "objects": len(p["meshes"]),
                            "socket_world_gltf": [round(x, 5) for x in (C @ S_b).to_translation()]})
if arm:
    arm.data.pose_position = "POSE"

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
print("VGAC_EQUIP", json.dumps({"ok": report["ok"], "items": len(placed), "clearance": [clear.get("worst_depth_m"), clear.get("worst_share")],
                                "fit": [(r["label"], r["before_m"], r["after_m"]) for r in report["fit"]]}))
