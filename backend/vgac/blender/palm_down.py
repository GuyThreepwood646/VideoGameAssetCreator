"""Blender headless: turn a rigged humanoid's hands from palm-forward to palm-down and re-bind (the rest pose correction).

Run: blender -b --python palm_down.py -- <rig.glb> <out.glb> --report <json> [--twist 3] [--min-roll 15]

The generated pictures show open palms facing forward (step 1's pose for the 3D model), so SkinToken fits the skeleton in
that pose, and the retarget's T-pose reference and Unity's Humanoid avatar (which aim each arm segment but never fix its
roll) keep it: every clip then shows palms forward. The hand-made library and Mixamo clips are authored palm-down.

Per arm (needs the thumb and four finger chains, so run it after the finger build): the palm normal comes from the
finger bones' plane (the palm side faces the model's front); the roll about the arm axis that takes it to palm-down is applied
to the Hand bone (fingers follow); the forearm skin is shared by position between ForeArm, N ignorable twist bones
(mixamorig:Extra_ForeArmTwist_<L|R><k>, a chain between ForeArm and Hand, turned by their share of the roll) and Hand, so the forearm keeps
its thickness; the deformed mesh is kept and the pose becomes the rest pose. An arm whose palm is already within --min-roll
of palm-down is left alone. The report has per hand: path (turned | skipped), roll_deg, twist_bones, reason.

Plain numpy below the docstring (roll_angle, node_layout, node_weights) so the backend tests run without Blender.
"""
import json
import math
import os
import sys

import numpy as np

PREFIX = "mixamorig:"
DOWN = np.array([0.0, 0.0, -1.0])


def _unit(v):
    return v / np.linalg.norm(v)


def palm_normal(wrist, finger_bases, thumb1, side, hand_pts=None):
    """Unit normal of the palm, on the palm side. f is the wrist to the middle of the four finger bases (names are not used:
    SkinToken names its finger chains by a depth order that is noise on a palm-forward hand), t the thumb base's offset across
    the hand. The palm's side comes from the hand's anatomy: cross(f, t) points out of a left palm and into a right one,
    whatever the pose. With the hand's vertices the axis is the thin direction of their cross-section across f (more exact
    than the thumb base's offset); the thumb only gives the sign."""
    wrist = np.asarray(wrist, float)
    f = _unit(np.mean([np.asarray(b, float) for b in finger_bases], axis=0) - wrist)
    t = np.asarray(thumb1, float) - wrist
    t = t - f * (t @ f)
    n = _unit(np.cross(f, t))
    n = n if side == "Left" else -n
    if hand_pts is not None and len(hand_pts) >= 30:
        q = np.asarray(hand_pts, float) - wrist
        q = q[(q @ f) > 0.2 * float((q @ f).max())]  # beyond the wrist: palm and fingers
        if len(q) >= 30:
            perp = q - np.outer(q @ f, f)
            _, v = np.linalg.eigh(np.cov(perp.T))
            thin = _unit(v[:, 0] - f * (v[:, 0] @ f))  # v[:, 0] along f has zero spread: take the thinnest of the other two
            ev, vv = np.linalg.eigh(np.cov(perp.T))
            cand = [vv[:, i] for i in range(3) if abs(vv[:, i] @ f) < 0.9]
            thin = _unit(cand[0] - f * (cand[0] @ f))  # eigh sorts ascending: the first is the thinner
            n = thin if thin @ n >= 0 else -thin
    return n


def roll_angle(arm_axis, normal, down=DOWN):
    """Signed angle (radians) about the arm axis that turns the palm normal onto palm-down."""
    a = _unit(arm_axis)
    pn, pd = normal - a * (normal @ a), down - a * (down @ a)
    pn, pd = _unit(pn), _unit(pd)
    return math.atan2(float(np.cross(pn, pd) @ a), float(pn @ pd))


def node_layout(n_twist):
    """The skin-sharing nodes along the forearm as (position 0..1 elbow to wrist, share of the roll): ForeArm, the twist
    bones at the middle of each of n equal parts, Hand. The roll share equals the position."""
    nodes = [(0.0, "forearm")] + [((2 * k + 1) / (2 * n_twist), f"twist{k + 1}") for k in range(n_twist)] + [(1.0, "hand")]
    return nodes


def node_weights(t, nodes):
    """{node name: share} for a point at position t (0 elbow, 1 wrist; clamped) between its two neighbouring nodes."""
    t = min(1.0, max(0.0, t))
    for (t0, n0), (t1, n1) in zip(nodes[:-1], nodes[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0)
            return {n0: 1.0 - f, n1: f}
    return {nodes[-1][1]: 1.0}


# --- Blender side ---------------------------------------------------------------------------------------------


def _hand_points(arm, mesh, side):
    """World positions of the vertices held (half or more) by the hand bone and everything below it."""
    names, stack = [f"{PREFIX}{side}Hand"], [arm.data.bones[f"{PREFIX}{side}Hand"]]
    while stack:
        b = stack.pop()
        for ch in b.children:
            names.append(ch.name)
            stack.append(ch)
    idx = {g.name: g.index for g in mesh.vertex_groups}
    gi = {idx[n] for n in names if n in idx}
    W = mesh.matrix_world
    return np.array([np.array(W @ v.co) for v in mesh.data.vertices if sum(g.weight for g in v.groups if g.group in gi) >= 0.5])


def main():
    import bpy
    from mathutils import Matrix, Vector

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", "")
    n_twist = int(_common.opt("--twist", "3"))
    min_roll = float(_common.opt("--min-roll", "15"))
    _common.open_glb(src)
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    mesh = max((o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups), key=lambda o: len(o.data.vertices))
    AW = arm.matrix_world
    bones = arm.data.bones
    rep = {}
    plans = {}
    for side in ("Left", "Right"):
        rep[side] = {"path": "skipped"}
        need = [f"{PREFIX}{side}{k}" for k in ("ForeArm", "Hand", "HandThumb1", "HandIndex1", "HandMiddle1", "HandRing1", "HandPinky1")]
        miss = [n for n in need if n not in bones]
        if miss:
            rep[side]["reason"] = "no " + miss[0].split(":")[1]
            continue
        P = lambda k: np.array(AW @ bones[f"{PREFIX}{side}{k}"].head_local)
        hand_pts_of = lambda sd: _hand_points(arm, mesh, sd)
        a = _unit(P("Hand") - P("ForeArm"))
        ang = roll_angle(a, palm_normal(P("Hand"), [P("Hand" + f + "1") for f in ("Index", "Middle", "Ring", "Pinky")], P("HandThumb1"), side, hand_pts_of(side)))
        rep[side]["roll_deg"] = round(math.degrees(ang), 1)
        if abs(math.degrees(ang)) < min_roll:
            rep[side]["reason"] = "the palm is already down"
            continue
        plans[side] = {"a": a, "wrist": P("Hand"), "elbow": P("ForeArm"), "angle": ang}
    if plans:
        W = mesh.matrix_world
        inv = AW.inverted()
        tw = {}
        if n_twist:
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="EDIT")
            eb = arm.data.edit_bones
            for side, pl in plans.items():
                tw[side] = []
                parent = eb[f"{PREFIX}{side}ForeArm"]
                for k in range(n_twist):
                    nm = f"{PREFIX}Extra_ForeArmTwist_{side[0]}{k + 1}"
                    b = eb.new(nm)
                    b.head = inv @ Vector(pl["elbow"] + (pl["wrist"] - pl["elbow"]) * k / n_twist)
                    b.tail = inv @ Vector(pl["elbow"] + (pl["wrist"] - pl["elbow"]) * (k + 1) / n_twist)
                    b.parent = parent
                    parent = b
                    tw[side].append(nm)
                # the twist bones sit in line between ForeArm and Hand, so ForeArm keeps one child and the hand is still the
                # first bone where the fingers branch (the sockets and hit boxes find the hand that way)
                eb[f"{PREFIX}{side}Hand"].parent = parent
            bpy.ops.object.mode_set(mode="OBJECT")
            for names in tw.values():
                for nm in names:
                    mesh.vertex_groups.new(name=nm)
        gi = {g.name: g.index for g in mesh.vertex_groups}
        nodes = node_layout(n_twist)
        for side, pl in plans.items():
            fa, hd = f"{PREFIX}{side}ForeArm", f"{PREFIX}{side}Hand"
            name_of = {"forearm": fa, "hand": hd, **{f"twist{k + 1}": nm for k, nm in enumerate(tw.get(side, []))}}
            pl["node_t"] = {name_of[n]: t for t, n in nodes if n in name_of}
            length = float(np.linalg.norm(pl["wrist"] - pl["elbow"]))
            for v in mesh.data.vertices:
                wf = next((g.weight for g in v.groups if g.group == gi[fa]), 0.0)
                if wf <= 0:
                    continue
                t = float((np.array(W @ v.co) - pl["elbow"]) @ pl["a"]) / length
                if not (0.0 <= t <= 1.4):
                    continue
                share = node_weights(t, [(tt, n) for tt, n in nodes if n in name_of])
                mesh.vertex_groups[gi[fa]].add([v.index], 0.0, "REPLACE")
                for n, f in share.items():
                    cur = next((g.weight for g in v.groups if g.group == gi[name_of[n]]), 0.0)
                    mesh.vertex_groups[gi[name_of[n]]].add([v.index], cur + wf * f, "REPLACE")
            # four influences at most, normalised, for the vertices touched
            for v in mesh.data.vertices:
                ents = sorted(((g.group, g.weight) for g in v.groups if g.weight > 0), key=lambda e: -e[1])
                if len(ents) > 4:
                    for gidx, _ in ents[4:]:
                        mesh.vertex_groups[gidx].remove([v.index])
                    ents = ents[:4]
                    tot = sum(w for _, w in ents)
                    for gidx, w in ents:
                        mesh.vertex_groups[gidx].add([v.index], w / tot, "REPLACE")
        # pose: each node turns by its share of the roll about the arm axis through the wrist
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="POSE")
        for side, pl in plans.items():
            pivot = Vector(pl["wrist"])
            for nm, tt in pl["node_t"].items():
                if tt == 0.0:
                    continue
                pb = arm.pose.bones[nm]
                R = Matrix.Translation(pivot) @ Matrix.Rotation(pl["angle"] * tt, 4, Vector(pl["a"])) @ Matrix.Translation(-pivot)
                pb.matrix = inv @ (R @ (AW @ bones[nm].matrix_local))  # absolute: the node's rest frame turned by its share
                bpy.context.view_layer.update()
        ev = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = ev.to_mesh()
        co = [v.co.copy() for v in me.vertices]
        ev.to_mesh_clear()
        bpy.ops.object.mode_set(mode="OBJECT")
        if len(co) != len(mesh.data.vertices):
            raise RuntimeError("the posed mesh has a different vertex count")
        for v, c in zip(mesh.data.vertices, co):
            v.co = c
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.select_all(action="SELECT")
        bpy.ops.pose.armature_apply(selected=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        for side in plans:
            rep[side].update(path="turned", twist_bones=len(tw.get(side, [])))
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
    if plans:
        bpy.ops.object.select_all(action="DESELECT")
        for o in (arm, mesh):
            o.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=False)
    print("PALMDOWN_OK", {s: rep[s].get("path") for s in rep})


if __name__ == "__main__":
    main()
