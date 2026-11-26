"""Blender headless: hand skin pass on a rigged GLB (PL-126), run after palm_down and before sleeve_skin.

Run: blender -b --python hand_skin.py -- <rig.glb> <out.glb> --report <json>

The rigger can leave the hand as one lump skinned mostly to the forearm: the hand bends with the arm in game. This pass
moves weight from the forearm and forearm twist bones onto the Hand bone for skin around the hand, with a smooth ramp
across the wrist so a bend shows no step.

Per side, the hand region is the skin within HAND_REACH x hand length of the Hand bone (extended by one hand length for the
fingers), no further behind the wrist joint than the ramp reaches, within RADIUS_MAX x hand length of the hand axis and not
above the forearm start by more than 0.2 m. Of those, only vertices that already carry HAND_SKIN_MIN of their weight on the
hand chain are changed: a sleeve cuff lying round the wrist carries almost none, and must not follow the hand.
Only forearm and twist weight moves, so skin of other parts that happens to be near stays as it is.
Along the wrist-to-fingertip axis (a = 0 at the Hand joint) the share of a vertex's weight that the hand chain (Hand and
finger bones) should carry is smoothstep over a in [c-w, c+w], c = RAMP_CENTER and w = RAMP_HALF, both x hand length: 0 at
the elbow end, 0.5 at c, 1 from c+w on. The ramp ends at the joint: the skin of the hand mesh starts behind the Hand bone's
joint, so the whole ramp lies on the forearm side of it. A vertex below its target takes the difference from its forearm and twist weights (never from the
upper arm, shoulder or other bones) and gives it to the Hand bone. Finger bone weights are never touched, so fingers
stay as they were and palm skin goes to the Hand bone, not the finger roots. Row sums are unchanged. A skeleton without
Hand and ForeArm bones, or with no forearm weight on the hand skin, is left as it is.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import re
import sys

import numpy as np

HAND_REACH = 0.6  # skin within this x hand length of the hand segment belongs to the hand region
RAMP_HALF = 0.3  # half width of the wrist ramp, x hand length
RAMP_CENTER = -0.3  # where the ramp is at half, x hand length from the Hand joint (negative: toward the elbow)
FORE_UP = 0.2  # metres above the forearm start where the region ends
SKIN_RG = 0.06  # skin tone test on the picture colour: red above green by this much
SKIN_RB = 0.12  # and above blue by this much
HAND_SKIN_MIN = 0.15  # a vertex is hand skin (not a sleeve cuff lying round the wrist) from this share of its weight on the hand chain
RADIUS_MAX = 0.3  # and within this x hand length of the hand axis
MAX_INFLUENCES = 4


def _short(name):
    return str(name or "").replace("mixamorig:", "")


def side_of(name):
    """'L' or 'R' from the bone name (Left, Right, or an _L / _R suffix), else None."""
    n = _short(name).lower()
    if n.startswith("left"):
        return "L"
    if n.startswith("right"):
        return "R"
    m = re.search(r"_([lr])\d*$", n)
    return m.group(1).upper() if m else None


def bone_role(name):
    """'hand' for the Hand bone, 'finger' for finger bones, 'fore' for the forearm and its twist bones, else None."""
    n = _short(name).lower()
    if "spring" in n or "hair" in n:
        return None
    if re.fullmatch(r"(left|right)hand", n):
        return "hand"
    if any(k in n for k in ("thumb", "index", "middle", "ring", "pinky")):
        return "finger"
    if "forearm" in n or "twist" in n:
        return "fore"
    return None


def side_columns(names, side):
    """{'hand': col or None, 'finger': [cols], 'fore': [cols]} for one side ('L' or 'R')."""
    out = {"hand": None, "finger": [], "fore": []}
    for i, n in enumerate(names):
        role = bone_role(n)
        if role is None or side_of(n) != side:
            continue
        if role == "hand":
            out["hand"] = i
        else:
            out[role].append(i)
    return out


def seg_dist(P, a, b):
    """Distance of each row of P to the segment a-b."""
    P = np.asarray(P, dtype=float)
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ab = b - a
    t = np.clip(((P - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)


def hand_region(P, hand_head, hand_tail, fore_head):
    """Boolean mask of the hand region (see the module text) for world points P. Also returns the axial coordinate a."""
    P = np.asarray(P, dtype=float)
    h0, h1 = np.asarray(hand_head, dtype=float), np.asarray(hand_tail, dtype=float)
    hl = float(np.linalg.norm(h1 - h0)) or 0.05
    dh = seg_dist(P, h0, h1 + (h1 - h0))
    axis = (h1 - h0) / hl
    a = (P - h0) @ axis
    radial = np.linalg.norm((P - h0) - np.outer(a, axis), axis=1)
    mask = ((dh < hl * HAND_REACH) & (a > (RAMP_CENTER - RAMP_HALF) * hl) & (radial <= RADIUS_MAX * hl)
            & (P[:, 2] < float(np.asarray(fore_head)[2]) + FORE_UP))
    return mask, a, hl


def skin_coloured(rgb):
    """Boolean per colour row (0..1 floats): a warm skin tone (red clearly above green and blue). Purple or blue cloth,
    olive or yellow trim and grey fail it."""
    c = np.asarray(rgb, dtype=float).reshape(-1, 3)
    return (c[:, 0] - c[:, 1] >= SKIN_RG) & (c[:, 0] - c[:, 2] >= SKIN_RB)


def hand_skin_rows(W, mask, cols, skin=None):
    """Indices of the region's vertices that are hand skin. With `skin` (a boolean per vertex from the picture colour) it is
    the region's skin-coloured vertices: a sleeve cuff lying round the wrist is cloth-coloured and keeps its weights. Without
    a picture colour it falls back to vertices with at least HAND_SKIN_MIN of their weight on the hand chain."""
    if cols["hand"] is None:
        return np.zeros(0, dtype=int)
    if skin is not None:
        return np.where(mask & np.asarray(skin, dtype=bool))[0]
    chain = [cols["hand"]] + list(cols["finger"])
    W = np.asarray(W, dtype=float)
    share = W[:, chain].sum(axis=1) / np.maximum(W.sum(axis=1), 1e-12)
    return np.where(mask & (share >= HAND_SKIN_MIN))[0]


def target_share(a, hl):
    """Share of the weight the hand chain should carry at axial coordinate a (metres), smoothstep over the wrist."""
    w = max(RAMP_HALF * hl, 1e-9)
    t = np.clip((np.asarray(a, dtype=float) - RAMP_CENTER * hl + w) / (2.0 * w), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def move_to_hand(W, rows, target, cols):
    """Move forearm and twist weight of `rows` onto the Hand column until the hand chain (Hand and fingers) carries
    `target` of the row sum, or the forearm weight is used up. Returns (new W, number of vertices changed)."""
    W = np.array(W, dtype=float, copy=True)
    if cols["hand"] is None or not cols["fore"] or len(rows) == 0:
        return W, 0
    rows = np.asarray(rows)
    chain = [cols["hand"]] + list(cols["finger"])
    total = W[rows].sum(axis=1)
    have = W[np.ix_(rows, chain)].sum(axis=1)
    fore = W[np.ix_(rows, cols["fore"])].sum(axis=1)
    move = np.minimum(np.maximum(np.asarray(target) * total - have, 0.0), fore)
    ok = move > 1e-6
    rows, move, fore = rows[ok], move[ok], fore[ok]
    if len(rows) == 0:
        return W, 0
    scale = 1.0 - move / np.maximum(fore, 1e-12)
    scale[scale < 1e-9] = 0.0
    sub = W[np.ix_(rows, cols["fore"])] * scale[:, None]
    W[np.ix_(rows, cols["fore"])] = sub
    W[rows, cols["hand"]] += move
    return W, int(len(rows))


def hand_chain_stats(W, rows, cols):
    """(mean hand-chain share, share of rows where the forearm group outweighs the hand chain) for `rows`."""
    if len(rows) == 0 or cols["hand"] is None:
        return 0.0, 0.0
    rows = np.asarray(rows)
    chain = [cols["hand"]] + list(cols["finger"])
    hw = W[np.ix_(rows, chain)].sum(axis=1)
    fw = W[np.ix_(rows, cols["fore"])].sum(axis=1) if cols["fore"] else np.zeros(len(rows))
    return float(hw.mean()), float((fw > hw).mean())


def limit_influences(weights, keep=MAX_INFLUENCES):
    """{group: weight} cut to the `keep` largest and normalised to the original sum (an empty row stays as it is)."""
    items = [(g, w) for g, w in weights.items() if w > 1e-9]
    total = sum(w for _, w in items)
    top = sorted(items, key=lambda gw: -gw[1])[:keep]
    s = sum(w for _, w in top)
    return {g: w * total / s for g, w in top} if s > 1e-12 else dict(weights)


# ---- Blender side -------------------------------------------------------------------------------------------------


def _bone(arm, names):  # pragma: no cover - runs inside Blender
    for b in arm.data.bones:
        if _short(b.name) in names:
            return np.array(arm.matrix_world @ b.head_local), np.array(arm.matrix_world @ b.tail_local)
    return None


def _vertex_skin(o, report):  # pragma: no cover - runs inside Blender
    """Per vertex: is the base-colour picture at its UV skin-coloured (mean over its corners). None when the mesh has no
    UVs or no base-colour picture."""
    me = o.data
    img = None
    for slot in o.material_slots:
        nt = slot.material.node_tree if slot.material and slot.material.use_nodes else None
        if nt is None:
            continue
        for n in nt.nodes:
            if n.type == "BSDF_PRINCIPLED" and n.inputs["Base Color"].links:
                src = n.inputs["Base Color"].links[0].from_node
                if src.type == "TEX_IMAGE" and src.image is not None:
                    img = src.image
    if img is None or not me.uv_layers.active:
        return None
    w, h = img.size[0], img.size[1]
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, -1)[:, :, :3]
    uv = np.array([l.uv for l in me.uv_layers.active.data], dtype=float).reshape(-1, 2)
    x = np.clip((uv[:, 0] % 1.0 * w).astype(int), 0, w - 1)
    y = np.clip((uv[:, 1] % 1.0 * h).astype(int), 0, h - 1)
    corner = skin_coloured(px[y, x]).astype(float)
    sums, counts = np.zeros(len(me.vertices)), np.zeros(len(me.vertices))
    vi = np.array([l.vertex_index for l in me.loops])
    np.add.at(sums, vi, corner)
    np.add.at(counts, vi, 1.0)
    report["picture"] = img.name
    return sums / np.maximum(counts, 1.0) >= 0.5


def _process_mesh(o, arm, report):  # pragma: no cover - runs inside Blender
    import bmesh

    skin = _vertex_skin(o, report)

    names = [g.name for g in o.vertex_groups]
    me = o.data
    bm = bmesh.new()
    bm.from_mesh(me)
    dl = bm.verts.layers.deform.active
    if dl is None:
        bm.free()
        return False
    bm.verts.ensure_lookup_table()
    V, G = len(bm.verts), len(names)
    W = np.zeros((V, G))
    for v in bm.verts:
        for g, w in v[dl].items():
            if g < G:
                W[v.index, g] = w
    M = np.array(o.matrix_world)
    local = np.array([v.co for v in bm.verts], dtype=float)
    P = local @ M[:3, :3].T + M[:3, 3]
    W2 = W
    changed_rows = set()
    for side, full in (("L", "Left"), ("R", "Right")):
        hand = _bone(arm, {full + "Hand"})
        fore = _bone(arm, {full + "ForeArm"})
        cols = side_columns(names, side)
        if hand is None or fore is None or cols["hand"] is None:
            report["sides"][side] = {"skipped": "bones missing"}
            continue
        mask, a, hl = hand_region(P, hand[0], hand[1], fore[0])
        rows = hand_skin_rows(W2, mask, cols, skin)
        before = hand_chain_stats(W2, rows, cols)
        W2, n = move_to_hand(W2, rows, target_share(a[rows], hl), cols)
        after = hand_chain_stats(W2, rows, cols)
        report["sides"][side] = {"verts": int(len(rows)), "changed": n, "hand_share_before": round(before[0], 3),
                                 "hand_share_after": round(after[0], 3), "fore_dominant_before": round(before[1], 3),
                                 "fore_dominant_after": round(after[1], 3)}
        report["changed_vertices"] += n
    for i in np.where(np.abs(W2 - W).sum(axis=1) > 1e-9)[0]:
        changed_rows.add(int(i))
        row = limit_influences({int(g): float(W2[i, g]) for g in np.nonzero(W2[i] > 1e-9)[0]})
        for g in list(bm.verts[i][dl].keys()):
            del bm.verts[i][dl][g]
        for g, w in row.items():
            bm.verts[i][dl][int(g)] = w
    if changed_rows:
        bm.to_mesh(me)
        me.update()
    bm.free()
    return bool(changed_rows)


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
    report = {"ok": False, "changed": 0, "changed_vertices": 0, "sides": {}}
    if arm is None or not meshes:
        report["reason"] = "no armature or no skinned mesh"
    else:
        any_changed = False
        for o in meshes:
            any_changed = _process_mesh(o, arm, report) or any_changed
        report["ok"] = True
        report["changed"] = int(any_changed)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"] and report["changed"]:
        bpy.ops.object.select_all(action="DESELECT")
        keep_objs = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep_objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep_objs[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("HAND_SKIN_OK" if report["ok"] else "HAND_SKIN_SKIPPED", report.get("reason", ""), report["changed"],
          report["changed_vertices"])


if __name__ == "__main__":  # pragma: no cover
    main()
