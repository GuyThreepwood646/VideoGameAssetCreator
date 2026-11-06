"""Blender headless: repair skin weights on a rigged GLB.

Run: blender -b --python rig_fix.py -- <in.glb> <out.glb> [--reference <prep.glb>] [--report <json>]
     [--skeleton humanoid|other]
Vertices with no bone influence copy the groups of the nearest weighted vertex (KD-tree); every vertex is then
limited to 4 influences and normalised. Prints RIGFIX_OK <fixed_unweighted> <verts>.

With --reference (the cleaned-up mesh the rigger was given) it also restores what the rigger drops:
- texture coordinates and the material (the rigger writes neither, so the model would be untextured);
- the real-world size and position (the rigger normalises every model to a 2 m box).

With --skeleton humanoid it also corrects the bone names. SkinToken's Mixamo template writes Mixamo names that are
wrong on real rigs (shifted along one arm, so "Neck" and "Head" sit on an arm and the real head is an Extra bone;
left and right swapped between assets). Each bone's role is found by geometry instead: the legs are the two chains
that reach the ground, the hips is where they meet, the spine runs up to the first bone that has an arm on each side
and a chain going up between them, the middle chain there is neck and head, the outer chains are the arms; the hand
is the end of the arm chain, its child chains are fingers (thumb nearest the wrist, then index to pinky from the
front). The front is where the toes point; the character's left is up x front (facing -Y after glTF import, +X).
The bones are then renamed to the Mixamo convention (mixamorig:Hips ... mixamorig:LeftHandIndex3); bones with no
role get mixamorig:Extra_NN. Renaming through the armature renames the vertex groups too. Nothing is renamed when
the sanity checks fail (upright, two legs, two arms, one head chain); the report says why.

The role-finding maths below the docstring is plain numpy so the backend tests can run it without Blender; only
main() touches bpy.
"""
import math
import json
import os
import sys

import numpy as np

PREFIX = "mixamorig:"
UP = np.array([0.0, 0.0, 1.0])
DEFAULT_FORWARD = np.array([0.0, -1.0, 0.0])  # glTF +Z (the model's front) after the glTF importer's axis change
FINGERS = ("Index", "Middle", "Ring", "Pinky")


# --- role finding (pure numpy) --------------------------------------------------------------------------------


class Bones:
    """Bones as {name, parent, head[, tail]} in Blender world space (Z up), with tree helpers."""

    def __init__(self, bones):
        self.order = [b["name"] for b in bones]
        self.parent = {b["name"]: b.get("parent") for b in bones}
        self.pos = {b["name"]: np.asarray(b["head"], float) for b in bones}
        self.tail = {b["name"]: np.asarray(b.get("tail", b["head"]), float) for b in bones}
        self.children = {n: [] for n in self.order}
        for n in self.order:
            p = self.parent[n]
            if p in self.children:
                self.children[p].append(n)
            else:
                self.parent[n] = None
        self.leaves = [n for n in self.order if not self.children[n]]

    def up_chain(self, n):
        """n, its parent, ... the root."""
        out = []
        while n is not None:
            out.append(n)
            n = self.parent[n]
        return out

    def subtree(self, n):
        out, stack = [], [n]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(self.children[x])
        return out

    def top(self, n):
        """Highest head in n's subtree."""
        return max(float(self.pos[b][2]) for b in self.subtree(n))

    def lca(self, a, b):
        sa = set(self.up_chain(a))
        for n in self.up_chain(b):
            if n in sa:
                return n
        return None

    def down_to(self, top, leaf):
        """Bones from `top` down to `leaf` (both included); [] when leaf is not under top."""
        chain = self.up_chain(leaf)
        if top not in chain:
            return []
        return chain[:chain.index(top) + 1][::-1]


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def _flat(v):
    return np.array([v[0], v[1], 0.0])


def axis_label(v):
    """Dominant axis of a direction as "+x", "-y", ..."""
    i = int(np.argmax(np.abs(v[:3])))
    return ("+" if v[i] >= 0 else "-") + "xyz"[i]


def _single_walk(bs, start):
    """From start, follow single children until a bone with no child or several. -> chain (start first)."""
    chain = [start]
    while len(bs.children[chain[-1]]) == 1:
        chain.append(bs.children[chain[-1]][0])
    return chain


def _highest_walk(bs, start):
    """From start, always go to the child whose subtree reaches highest. -> chain."""
    chain = [start]
    while bs.children[chain[-1]]:
        chain.append(max(bs.children[chain[-1]], key=bs.top))
    return chain


def _arm_roles(bs, start, side, forward):
    """Roles for one arm hanging from the chest: Shoulder, Arm, ForeArm, Hand and the finger chains."""
    chain = _single_walk(bs, start)
    if len(chain) < 3:
        return None, f"the {side.lower()} arm has only {len(chain)} bone(s)"
    roles = {}
    names = ["Shoulder", "Arm", "ForeArm", "Hand"]
    if len(chain) <= 4:
        names, part = names[-len(chain):], chain  # three bones: no shoulder
    elif bs.children[chain[-1]]:
        part = chain[-4:]  # the hand is where the fingers branch; bones before the shoulder have no role
    else:
        part = chain[:4]  # a long unbranched chain: the first four are the arm, the rest has no role
    for nm, b in zip(names, part):
        roles[side + nm] = b
    hand = part[-1]
    kids = bs.children[hand] if hand == chain[-1] else []
    if kids:
        wrist = bs.pos[hand]
        fingers = {k: _first_walk(bs, k) for k in kids}
        order = list(kids)
        if len(order) >= 2:
            thumb = min(order, key=lambda k: float(np.linalg.norm(bs.pos[k] - wrist)))
            rest = sorted([k for k in order if k != thumb], key=lambda k: -float(np.dot(bs.pos[k] - wrist, forward)))
            named = [("Thumb", thumb)] + list(zip(FINGERS, rest))
        else:
            named = [("Index", order[0])]
        for fname, k in named:
            for i, b in enumerate(fingers[k][:4]):
                roles[f"{side}Hand{fname}{i + 1}"] = b
    return roles, None


def _first_walk(bs, start):
    """From start, follow the first child (finger chains do not branch)."""
    chain = [start]
    while bs.children[chain[-1]]:
        chain.append(bs.children[chain[-1]][0])
    return chain


def find_roles(bones):
    """Identify each bone's humanoid role by geometry. bones: [{name, parent, head}] in Blender world space (Z up).
    -> {"ok", "reason", "roles": {role: bone}, "forward", "left", "left_is", "forward_from", "face_agrees"}."""
    out = {"ok": False, "reason": None, "roles": {}, "left_is": None, "forward_from": None, "face_agrees": None}
    bs = Bones(bones)
    if len(bs.order) < 12:
        out["reason"] = f"only {len(bs.order)} bones, too few for a humanoid"
        return out
    P = np.array([bs.pos[n] for n in bs.order])
    zmin, zmax = float(P[:, 2].min()), float(P[:, 2].max())
    height = zmax - zmin
    if height <= 1e-6:
        out["reason"] = "the skeleton is flat"
        return out

    # Legs: the pair of ground-level leaves farthest apart whose chains meet in a common bone (the hips) with at
    # least three bones each.
    low = [n for n in bs.leaves if float(bs.pos[n][2]) - zmin < 0.15 * height]
    best = None
    for i, a in enumerate(low):
        for b in low[i + 1:]:
            h = bs.lca(a, b)
            if h is None:
                continue
            ca, cb = bs.down_to(h, a)[1:], bs.down_to(h, b)[1:]
            if len(ca) < 3 or len(cb) < 3 or ca[0] == cb[0]:
                continue
            sep = float(np.linalg.norm(_flat(bs.pos[a] - bs.pos[b])))
            if sep < 0.08 * height:
                continue
            if best is None or sep > best[0]:
                best = (sep, h, ca, cb)
    if best is None:
        out["reason"] = "no two legs reaching the ground"
        return out
    _, hips, leg_a, leg_b = best

    # Front: where the toes point (the leaf ahead of the foot), side to side removed. Else the glTF front.
    across = _unit(_flat(bs.pos[leg_a[-1]] - bs.pos[leg_b[-1]]))
    toe = np.zeros(3)
    for chain in (leg_a, leg_b):
        if len(chain) >= 4:
            d = _flat(bs.pos[chain[-1]] - bs.pos[chain[2]])
        else:
            d = _flat(bs.tail[chain[-1]] - bs.pos[chain[-1]])
        toe += d - across * float(np.dot(d, across))
    forward = _unit(toe)
    if forward is not None and float(np.linalg.norm(toe)) / 2 > 0.02 * height:
        out["forward_from"] = "toes"
    else:
        forward = DEFAULT_FORWARD.copy()
        out["forward_from"] = "assumed (glTF front)"
    left = _unit(np.cross(UP, forward))
    centre = bs.pos[hips]
    lat = lambda n: float(np.dot(bs.pos[n] - centre, left))  # noqa: E731
    reach = lambda n: [lat(b) for b in bs.subtree(n)]  # noqa: E731

    # Spine: from the hips up to the first bone with an arm on each side and a chain going up between them.
    leg_set = set(leg_a) | set(leg_b)
    up_kids = [k for k in bs.children[hips] if k not in leg_set]
    if not up_kids:
        out["reason"] = "nothing above the hips"
        return out
    spine = [max(up_kids, key=bs.top)]
    side_reach = 0.12 * height
    chest = arms = head_start = None
    while True:
        cur = spine[-1]
        kids = bs.children[cur]
        if not kids:
            break
        lk = [k for k in kids if max(reach(k)) > side_reach and bs.top(k) > float(centre[2])]
        rk = [k for k in kids if min(reach(k)) < -side_reach and bs.top(k) > float(centre[2])]
        if lk and rk:
            la = max(lk, key=lambda k: max(reach(k)))
            ra = min(rk, key=lambda k: min(reach(k)))
            mid = [k for k in kids if k not in (la, ra) and bs.top(k) > float(bs.pos[cur][2])]
            if la != ra and mid:
                chest, arms = cur, {"Left": la, "Right": ra}
                head_start = min(mid, key=lambda k: abs(lat(k)))
                break
        spine.append(max(kids, key=bs.top))
    if chest is None:
        out["reason"] = "no chest bone with an arm on each side and a head between them"
        return out

    roles = {"Hips": hips}
    spine_names = ["Spine"] + [f"Spine{i}" for i in range(1, len(spine))]
    for nm, b in zip(spine_names, spine):
        roles[nm] = b
    head = _highest_walk(bs, head_start)
    hn = {1: ["Head"], 2: ["Neck", "Head"], 3: ["Neck", "Head", "HeadTop_End"]}.get(len(head), ["Neck", "Neck1", "Head", "HeadTop_End"])
    for nm, b in zip(hn, head):
        roles[nm] = b
    for side, start in arms.items():
        r, why = _arm_roles(bs, start, side, forward)
        if r is None:
            out["reason"] = why
            return out
        roles.update(r)
    legs = {}
    for chain in (leg_a, leg_b):
        side = "Left" if lat(chain[-1]) > 0 else "Right"
        if side in legs:
            out["reason"] = "both legs are on the same side"
            return out
        legs[side] = chain
    for side, chain in legs.items():
        for nm, b in zip(["UpLeg", "Leg", "Foot", "ToeBase", "Toe_End"], chain):
            roles[side + nm] = b

    # Sanity checks.
    z = lambda n: float(bs.pos[n][2])  # noqa: E731
    head_top = max(z(b) for b in head)
    foot_z = max(z(legs["Left"][-1]), z(legs["Right"][-1]))
    spine_dir = _unit(bs.pos[chest] - bs.pos[hips])
    problems = []
    if not (head_top > z(chest) > z(hips) > foot_z):
        problems.append("not upright (head, chest, hips and feet are not in order from top to bottom)")
    if spine_dir is None or float(spine_dir[2]) < 0.7:
        problems.append("the spine does not run upwards")
    if z(hips) - foot_z < 0.3 * height:
        problems.append("the legs are too short for a standing humanoid")
    if head_top < zmax - 0.1 * height:
        problems.append("the head chain is not at the top of the skeleton")
    if not (lat(roles["LeftArm"]) > 0 > lat(roles["RightArm"])):
        problems.append("the arms are not one on each side")
    if not (lat(roles["LeftHand"]) > lat(roles["LeftArm"]) * 0.5 and lat(roles["RightHand"]) < lat(roles["RightArm"]) * 0.5):
        problems.append("the hands do not reach out to the sides")
    if problems:
        out["reason"] = "; ".join(problems)
        return out
    # Cross-check: the top of the head chain leans the same way as the toes (information only).
    tip = _flat(bs.pos[head[-1]] - bs.pos[head[0]])
    if float(np.linalg.norm(tip)) > 1e-4:
        out["face_agrees"] = bool(float(np.dot(tip, forward)) >= 0)
    out.update(ok=True, roles=roles, forward=[round(float(v), 4) for v in forward], left=[round(float(v), 4) for v in left],
               left_is=axis_label(left))
    return out


def plan_names(bones, roles, prefix=PREFIX):
    """-> {old name: new name} for every bone: roles get prefix + role; the rest keep an existing prefix + "Extra_NN"
    name when it is free, else get the next free Extra number, in armature order."""
    order = [b["name"] for b in bones]
    new = {old: prefix + role for role, old in roles.items()}
    taken = set(new.values())
    rest = [n for n in order if n not in new]
    for n in rest:
        if n.startswith(prefix + "Extra_") and n not in taken:
            new[n] = n
            taken.add(n)
    k = 0
    for n in rest:
        if n in new:
            continue
        while True:
            k += 1
            cand = f"{prefix}Extra_{k:02d}"
            if cand not in taken:
                break
        new[n] = cand
        taken.add(cand)
    return new


def name_report(bones, skeleton):
    """The bone_names part of the report, and the rename map (empty when skipped)."""
    if skeleton != "humanoid":
        return {"renamed": 0, "by": "geometry", "left_is": None, "roles": {}, "skipped_reason": "not a humanoid skeleton"}, {}
    found = find_roles(bones)
    if not found["ok"]:
        return {"renamed": 0, "by": "geometry", "left_is": None, "roles": {}, "skipped_reason": found["reason"]}, {}
    mapping = plan_names(bones, found["roles"])
    changed = {o: n for o, n in mapping.items() if o != n}
    rep = {"renamed": len(changed), "by": "geometry", "left_is": found["left_is"], "forward_from": found["forward_from"],
           "face_agrees": found["face_agrees"], "roles": dict(found["roles"]),
           "extra": {n: o for o, n in mapping.items() if o not in found["roles"].values()}, "skipped_reason": None}
    return rep, mapping


# --- Blender side ---------------------------------------------------------------------------------------------


# Stray weights: the rigger sometimes ties a few vertices to a bone on the other end of the body (fingertips to a toe
# bone on one live character), which pulls them into long spikes as soon as the limb moves. An influence is stray when
# its bone is far from the vertex (more than STRAY_FAR of the height) and much farther (STRAY_RATIO times) than the
# nearest bone that also holds the vertex.
STRAY_FAR = 0.2
STRAY_RATIO = 4.0
STRAY_NEAR_FLOOR = 0.02
# Spring chains (springs.py, bones named Spring_*) hold a hanging part with weights that fade from the body bone at the
# root to the chain at the tip; the body bone is far from the tip on purpose, so those vertices are never cleaned here.
SPRING_PREFIX = "Spring_"


def _segment_distance(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    den = sum(x * x for x in ab)
    t = 0.0 if den <= 1e-12 else max(0.0, min(1.0, sum(ap[i] * ab[i] for i in range(3)) / den))
    return math.sqrt(sum((ap[i] - t * ab[i]) ** 2 for i in range(3)))


def stray_influences(p, weights, segments, height):
    """Names of the bones in `weights` ({bone: weight}) that hold the point `p` from too far away. `segments` is
    {bone: (head, tail)} in the same space as `p`; bones without a segment are never dropped."""
    if any(b.startswith(SPRING_PREFIX) for b, w in weights.items() if w > 0):
        return []
    dist = {b: _segment_distance(p, *segments[b]) for b, w in weights.items() if w > 0 and b in segments}
    if len(dist) < 2:
        return []
    near = max(min(dist.values()), STRAY_NEAR_FLOOR * height)
    return [b for b, d in dist.items() if d > STRAY_FAR * height and d > STRAY_RATIO * near]


def _world_box(objs):
    from mathutils import Vector

    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(q[i] for q in pts) for i in range(3)])
    hi = Vector([max(q[i] for q in pts) for i in range(3)])
    return lo, hi


def restore_from_reference(meshes, path):
    """Copy UVs and the material from the reference mesh and scale and place the rig to match it. -> report."""
    import bpy
    import _common
    from mathutils import Vector, kdtree

    out = {"reference": os.path.basename(path)}
    before = set(bpy.data.objects)
    _common.import_glb(path)
    new = [o for o in bpy.data.objects if o not in before]
    try:
        srcs = [o for o in new if o.type == "MESH"]
        if not srcs:
            return {**out, "ok": False, "reason": "reference has no mesh"}
        src_o = max(srcs, key=lambda o: len(o.data.polygons))
        target = max(meshes, key=lambda o: len(o.data.polygons))
        sd, td = src_o.data, target.data
        s_lo, s_hi = _world_box([src_o])
        t_lo, t_hi = _world_box([target])
        s_size, t_size = max(s_hi - s_lo) or 1.0, max(t_hi - t_lo) or 1.0
        s_mid, t_mid = (s_lo + s_hi) / 2, (t_lo + t_hi) / 2
        sw, tw = src_o.matrix_world, target.matrix_world
        # Texture coordinates: match each face to the nearest reference face in a size-independent frame.
        if sd.uv_layers and not td.uv_layers:
            tree = kdtree.KDTree(len(sd.polygons))
            for poly in sd.polygons:
                tree.insert((sw @ poly.center - s_mid) / s_size, poly.index)
            tree.balance()
            svert = [(sw @ v.co - s_mid) / s_size for v in sd.vertices]
            tvert = [(tw @ v.co - t_mid) / t_size for v in td.vertices]
            suv = sd.uv_layers.active.data
            layer = td.uv_layers.new(name="UVMap")
            worst = 0.0
            for poly in td.polygons:
                _, si, dist = tree.find((tw @ poly.center - t_mid) / t_size)
                worst = max(worst, dist)
                sp = sd.polygons[si]
                for li in poly.loop_indices:
                    vp = tvert[td.loops[li].vertex_index]
                    best = min(sp.loop_indices, key=lambda sl: (svert[sd.loops[sl].vertex_index] - vp).length_squared)
                    layer.data[li].uv = suv[best].uv
            out["uvs"] = {"faces": len(td.polygons), "source_faces": len(sd.polygons), "max_face_offset": round(worst, 5)}
        else:
            out["uvs"] = {"skipped": "target already has UVs" if td.uv_layers else "reference has no UVs"}
        # Material: the rigger writes none, so the model would be untextured in the viewer and in GLB exports.
        if sd.materials and not [m for m in td.materials if m is not None]:
            td.materials.clear()
            for m in sd.materials:
                td.materials.append(m)
            out["materials"] = [m.name for m in sd.materials if m is not None]
        # Size and place: the rigger normalises to a 2 m box; put the model back at the size cleanup gave it,
        # standing on the same spot.
        factor = s_size / t_size
        out["scale_factor"] = round(factor, 5)
        out["size_before_m"] = [round(v, 4) for v in (t_hi - t_lo)]
        roots = [o for o in bpy.context.scene.objects if o.parent is None and o not in new]
        if abs(factor - 1.0) > 1e-4:
            for o in roots:
                o.scale = [c * factor for c in o.scale]
                o.location = [c * factor for c in o.location]
            bpy.context.view_layer.update()
        t_lo, t_hi = _world_box([target])
        shift = Vector(((s_lo.x + s_hi.x) / 2 - (t_lo.x + t_hi.x) / 2, (s_lo.y + s_hi.y) / 2 - (t_lo.y + t_hi.y) / 2, s_lo.z - t_lo.z))
        if shift.length > 1e-5:
            for o in roots:
                o.location = Vector(o.location) + shift
            bpy.context.view_layer.update()
        mine = [o for o in bpy.context.scene.objects if o not in new and (o.type == "ARMATURE" or o.type == "MESH")]
        bpy.ops.object.select_all(action="DESELECT")
        for o in mine:
            o.select_set(True)
        bpy.context.view_layer.objects.active = mine[0]
        bpy.ops.object.transform_apply(location=True, rotation=False, scale=True)
        t_lo, t_hi = _world_box([target])
        out["size_after_m"] = [round(v, 4) for v in (t_hi - t_lo)]
        out["reference_size_m"] = [round(v, 4) for v in (s_hi - s_lo)]
        out["ok"] = True
        return out
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)


def rename_bones(skeleton):
    """Find the roles on the (first) armature and rename its bones; vertex groups follow. -> bone_names report."""
    import bpy

    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not arms:
        return {"renamed": 0, "by": "geometry", "left_is": None, "roles": {}, "skipped_reason": "no armature"}
    arm = arms[0]
    W = arm.matrix_world
    bones = [{"name": b.name, "parent": b.parent.name if b.parent else None, "head": list(W @ b.head_local),
              "tail": list(W @ b.tail_local)} for b in arm.data.bones]
    rep, mapping = name_report(bones, skeleton)
    changed = {o: n for o, n in mapping.items() if o != n}
    if not changed:
        return rep
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups]
    # What every vertex group must be called afterwards, by group index (old and new names overlap, so names alone
    # cannot say which groups have already been renamed).
    want = {o.name: {g.index: changed.get(g.name, g.name) for g in o.vertex_groups} for o in meshes}
    # Two passes through unique temporary names, so a new name never collides with a bone not yet renamed.
    temp = {}
    for i, old in enumerate(changed):
        tmp = f"__vgac_rename_{i:03d}"
        arm.data.bones[old].name = tmp
        temp[tmp] = changed[old]
    for tmp, new in temp.items():
        arm.data.bones[tmp].name = new
    names = {b.name for b in arm.data.bones}
    missing = sorted(set(mapping.values()) - names)
    # Vertex groups follow through the armature API on a mesh the armature deforms; rename any that did not (a mesh
    # not linked by an Armature modifier, say), again through temporary names, so the skin still deforms.
    fixed_groups = 0
    for o in meshes:
        wrong = [g for g in o.vertex_groups if g.name != want[o.name][g.index]]
        for i, g in enumerate(wrong):
            g.name = f"__vgac_group_{i:03d}"
        for g in wrong:
            g.name = want[o.name][g.index]
        fixed_groups += len(wrong)
    stray = sorted({g.name for o in meshes for g in o.vertex_groups} - names)
    rep["vertex_groups_fixed_by_hand"] = fixed_groups
    rep["vertex_groups_without_bone"] = stray
    if missing:
        rep["missing_after_rename"] = missing
    return rep


def main():
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common
    from mathutils import kdtree

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    reference = _common.opt("--reference", "")
    report_path = _common.opt("--report", "")
    skeleton = _common.opt("--skeleton", "other")
    # --weights-only: clean the weights of a rig this script already repaired (an older version of the clean-up),
    # leaving its names, size and texture coordinates as they are.
    weights_only = "--weights-only" in argv
    _common.open_glb(src)
    fixed = total = 0
    # Drop helper meshes the rigger's exporter leaves behind (no vertex groups, not skinned): they would count as
    # unweighted geometry and ship into the engine.
    removed = []
    for arm in [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]:
        for pb in arm.pose.bones:
            pb.custom_shape = None
    for o in [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.vertex_groups]:
        removed.append(o.name)
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
    unnormalised = 0
    stray = {"verts": 0, "by_bone": {}}
    for obj in [o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups]:
        verts = obj.data.vertices
        total += len(verts)
        groups = obj.vertex_groups
        weighted = [v for v in verts if sum(g.weight for g in v.groups) > 1e-6]
        unweighted = [v for v in verts if sum(g.weight for g in v.groups) <= 1e-6]
        if unweighted and weighted:
            tree = kdtree.KDTree(len(weighted))
            for i, v in enumerate(weighted):
                tree.insert(v.co, i)
            tree.balance()
            for v in unweighted:
                _, idx, _ = tree.find(v.co)
                for g in weighted[idx].groups:
                    groups[g.group].add([v.index], g.weight, "REPLACE")
                fixed += 1
        # Drop stray influences (a bone far across the body); the normalise pass below fills the gap.
        arm = obj.find_armature() or next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
        if arm is not None:
            segs = {b.name: (tuple(arm.matrix_world @ b.head_local), tuple(arm.matrix_world @ b.tail_local)) for b in arm.data.bones}
            zs = [(obj.matrix_world @ v.co).z for v in verts]
            height = (max(zs) - min(zs)) if zs else 0.0
            names = {g.index: g.name for g in groups}
            if height > 1e-6:
                for v in verts:
                    w = {names[g.group]: g.weight for g in v.groups if g.weight > 0 and g.group in names}
                    if any(n.startswith(SPRING_PREFIX) for n in w):
                        continue  # a hanging part on its spring chain (springs.py): its weights fade on purpose
                    drop = stray_influences(tuple(obj.matrix_world @ v.co), w, segs, height)
                    if drop:
                        stray["verts"] += 1
                        for b in drop:
                            stray["by_bone"][b] = stray["by_bone"].get(b, 0) + 1
                            groups[b].remove([v.index])
        # Limit to 4 influences and normalise, in plain Python so it works headless and deterministically.
        for v in verts:
            entries = sorted(((g.group, g.weight) for g in v.groups if g.weight > 0), key=lambda t: -t[1])
            drop = entries[4:]
            entries = entries[:4]
            for gi, _ in drop:
                groups[gi].remove([v.index])
            total_w = sum(w for _, w in entries)
            if total_w <= 0:
                continue
            if abs(total_w - 1.0) > 1e-4:
                unnormalised += 1
                for gi, w in entries:
                    groups[gi].add([v.index], w / total_w, "REPLACE")

    ref_report = None
    skinned = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups]
    if reference and os.path.isfile(reference) and skinned and not weights_only:
        try:
            ref_report = restore_from_reference(skinned, reference)
        except Exception as e:  # keep the repaired rig even when the restore fails
            ref_report = {"ok": False, "reason": str(e)[:300]}
    try:
        names_report = {"renamed": 0, "by": "geometry", "left_is": None, "roles": {}, "skipped_reason": "weights only"} if weights_only else rename_bones(skeleton)
    except Exception as e:  # keep the repaired rig with its old names
        names_report = {"renamed": 0, "by": "geometry", "left_is": None, "roles": {}, "skipped_reason": f"error: {str(e)[:300]}"}
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump({"fixed_unweighted": fixed, "verts": total, "normalised": unnormalised, "removed": removed,
                       "stray_weights": stray, "reference": ref_report, "bone_names": names_report}, fh, indent=1)

    # Export only the armature(s) and skinned meshes, so nothing stray can come along.
    bpy.ops.object.select_all(action="DESELECT")
    keep = [o for o in bpy.context.scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
    for o in keep:
        o.select_set(True)
    bpy.context.view_layer.objects.active = keep[0]
    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("RIGFIX_OK", fixed, total, "normalised", unnormalised, "removed", removed, "reference", ref_report,
          "bone_names", names_report.get("renamed"), names_report.get("skipped_reason"))


if __name__ == "__main__":
    main()
