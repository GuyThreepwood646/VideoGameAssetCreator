"""Blender headless: rigid finger segments. A real finger is three rigid segments that hinge at three joints, the first where the finger meets
the palm; SkinToken's finger skin ramps across a whole segment (the weight passes from one bone to the next over 0.8 to 2 segment lengths), so a
curl bends like rubber, and its first bone holds skin 9 to 15 mm back into the palm, which puckers.

Run: blender -b --python finger_rigid.py -- <rig.glb> <out.glb> [--report <json>] [--band 6] [--knuckle 6]

Per finger chain (thumb and four fingers, three bones each, after the finger build and the lane naming):
- the chain is put on a straight line from the first bone's head (the knuckle) to the finger's tip (the farthest skin the chain itself holds), the
  joints at the anatomical fractions of its length (0.45 and 0.75; thumb 0.40 and 0.73);
- the finger's own skin (the weight its three bones hold together) is shared among its three bones by where each vertex lies along the finger:
  each segment on its own bone, with a smoothstep blend BAND_MM wide at each joint;
- behind the knuckle the first bone's share ramps into the palm bone over KNUCKLE_MM, so the first finger bone stops at the knuckle.
Skin is only ever moved between a finger's own bones and the palm bone, never to another finger, so a hand whose fingers SkinToken already
separated keeps that separation. At most four influences a vertex, normalised. 6 mm and not less: at 4 mm the M1 hand tore at a 40 degree curl.

Plain numpy below the docstring (shares, ramp) so the backend tests run without Blender.
"""
import json
import os
import sys

import numpy as np

PREFIX = "mixamorig:"
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Pinky")
FRACTIONS = (0.45, 0.30, 0.25)  # proximal, middle, distal share of a finger's length: joints at 0.45 and 0.75
THUMB_FRACTIONS = (0.40, 0.33, 0.27)  # thumb joints at 0.40 and 0.73
BAND_MM = 6.0  # full width of the blend at each joint
KNUCKLE_MM = 6.0  # full width of the ramp from the palm bone to the first finger bone at the knuckle
MAX_REACH = 1.6  # a chain's tip is searched within this many of its own lengths from its base (stray weights far away are not the finger)
MIN_VERTS = 10  # a chain holding fewer vertices than this is left as it is


def smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def joint_points(base, tip, fractions):
    """The chain's head/tail pairs on the straight line base to tip, split by `fractions`."""
    base, tip = np.asarray(base, float), np.asarray(tip, float)
    cum = np.cumsum((0.0,) + tuple(fractions))
    return [(base + (tip - base) * cum[i], base + (tip - base) * cum[i + 1]) for i in range(3)]


def segment_shares(t, j1, j2, band):
    """Share of a finger's own skin on bones 1, 2, 3 for a vertex at position t along the finger (m from the knuckle): each segment on its own
    bone, a smoothstep blend `band` wide centred on each joint (j1, j2: joint positions along the finger). The three shares sum to 1."""
    s1 = float(smooth((t - (j1 - band / 2)) / band))
    s2 = float(smooth((t - (j2 - band / 2)) / band))
    return [1.0 - s1, s1 * (1.0 - s2), s1 * s2]


def knuckle_share(t, knuckle):
    """How much of a finger's skin at position t stays on the finger: 0 well behind the knuckle (all on the palm bone), 1 from the knuckle on, a
    smoothstep over `knuckle` centred on the knuckle."""
    return float(smooth((t + knuckle / 2) / knuckle))


def new_weights(ws, chain_ids, hand_id, t, j1, j2, band, knuckle):
    """The vertex's new weights {group: weight}: its mass on the chain's three bones (ws: {group: weight}) re-shared by `segment_shares`, the part
    behind the knuckle moved to the palm bone, everything else untouched; at most four groups, normalised."""
    mass = sum(ws.get(i, 0.0) for i in chain_ids)
    sh = segment_shares(t, j1, j2, band)
    keep = knuckle_share(t, knuckle) if knuckle > 0 else 1.0
    new = {g: w for g, w in ws.items() if g not in chain_ids}
    for i, w in zip(chain_ids, sh):
        new[i] = mass * w * keep
    if keep < 1.0:
        new[hand_id] = new.get(hand_id, 0.0) + mass * (1.0 - keep)
    top = sorted(((g, w) for g, w in new.items() if w > 1e-6), key=lambda e: -e[1])[:4]
    tw = sum(w for _, w in top) or 1.0
    return {g: w / tw for g, w in top}


# --- Blender side ---------------------------------------------------------------------------------------------


def main():
    import bpy
    from mathutils import Vector

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", "")
    band = float(_common.opt("--band", str(BAND_MM))) / 1000.0
    knuckle = float(_common.opt("--knuckle", str(KNUCKLE_MM))) / 1000.0
    _common.open_glb(src)
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    mesh = max((o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups), key=lambda o: len(o.data.vertices))
    AW, W = arm.matrix_world, mesh.matrix_world
    inv = AW.inverted()
    V = np.array([np.array(W @ v.co) for v in mesh.data.vertices])
    gi = {g.name: g.index for g in mesh.vertex_groups}
    rep = {}
    plans = {}
    for side in ("Left", "Right"):
        r = rep[side] = {"straightened": [], "skipped": {}, "verts": 0}
        for f in FINGERS:
            ch = [f"{PREFIX}{side}Hand{f}{i}" for i in (1, 2, 3)]
            if not all(n in arm.data.bones and n in gi for n in ch):
                r["skipped"][f] = "no chain"
                continue
            ids = [gi[n] for n in ch]
            base = np.array(AW @ arm.data.bones[ch[0]].head_local)
            old_len = float(np.linalg.norm(np.array(AW @ arm.data.bones[ch[2]].tail_local) - base)) or 0.05
            own = [i for i, v in enumerate(mesh.data.vertices) if sum(g.weight for g in v.groups if g.group in ids) >= 0.5]
            near = [i for i in own if np.linalg.norm(V[i] - base) <= MAX_REACH * old_len]
            if len(near) < MIN_VERTS:
                r["skipped"][f] = "too little skin on the chain"
                continue
            tip = max((V[i] for i in near), key=lambda p: np.linalg.norm(p - base))
            plans[(side, f)] = (ch, ids, base, tip)
    # 1 straighten: the bones go on their straight line (the skin weights are re-shared below)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    for (side, f), (ch, ids, base, tip) in plans.items():
        for n, (h, t) in zip(ch, joint_points(base, tip, THUMB_FRACTIONS if f == "Thumb" else FRACTIONS)):
            eb[n].head, eb[n].tail = inv @ Vector(h), inv @ Vector(t)
        rep[side]["straightened"].append(f)
    bpy.ops.object.mode_set(mode="OBJECT")
    # 2 rigid segments and the knuckle ramp, per finger, along its own length only
    for (side, f), (ch, ids, base, tip) in plans.items():
        axis = tip - base
        L = float(np.linalg.norm(axis))
        axis /= L
        frac = THUMB_FRACTIONS if f == "Thumb" else FRACTIONS
        j1, j2 = L * frac[0], L * (frac[0] + frac[1])
        hand_id = gi[f"{PREFIX}{side}Hand"]
        for v in mesh.data.vertices:
            ws = {g.group: g.weight for g in v.groups if g.weight > 0}
            if sum(ws.get(i, 0.0) for i in ids) < 0.02:
                continue
            t = float((V[v.index] - base) @ axis)
            new = new_weights(ws, ids, hand_id, t, j1, j2, band, knuckle)
            for g in list(ws):
                mesh.vertex_groups[g].remove([v.index])
            for g, w in new.items():
                mesh.vertex_groups[g].add([v.index], w, "REPLACE")
            rep[side]["verts"] += 1
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
    if plans:
        bpy.ops.object.select_all(action="DESELECT")
        for o in (arm, mesh):
            o.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=False)
    print("FINGERRIGID_OK", {s: len(rep[s]["straightened"]) for s in rep})


if __name__ == "__main__":
    main()
