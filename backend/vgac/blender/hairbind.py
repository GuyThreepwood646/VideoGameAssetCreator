"""Blender headless: bind the library hair to the skeleton (docs/HAIR-LIBRARY-PLAN.md section 6, rig step).

Run: blender -b --python hairbind.py -- <anim.glb> <out.glb> --hair-mesh <hair_mesh.npz> --report <hair_bind.json>
     [--hair-parts <hair_parts.json>] [--reference <prep.glb>] [--blend 0.10] [--fps 24]

Runs in the rig step after the moves are made and before the spring bones (stages/rig.py hair_bind), so hit boxes,
procanim and retarget never see the hair. It builds the object VGAC_Hair from the canonical hair_mesh.npz (verts in
glTF Y-up metres in the clean-up model's space, tris, corner uv, role 0 rests on the head, 1 the scalp cap, 2+k hang
group k) with the material VGAC_Hair, parents it to the armature with an Armature modifier and gives it weights:
- above the neck joint (the Head bone's head): Head 1.0;
- below it, over `--blend` metres (10 cm), a smooth blend from Head toward the weights of the nearest body vertex
  held by Neck, Spine2, Spine1 or the shoulders (a data transfer limited to those bones), so hair resting on the nape
  and shoulders follows them;
- hang-group vertices: Head 1.0 (the spring chains, added next by springs.py, take them over).
The clips are left as they are; the file is written again with the same NLA export as procanim and springs.

With --reference (the clean-up GLB the hair was fitted on) the hair is carried by the same box fit rig_fix used to put
the rig back at the clean-up size (centre in x and y, feet on the ground, uniform scale); when the rig was restored
this is the identity.

Report: {ok, verts, faces, rest_share, nape_blend_verts, groups, bound_share, follow_error_m, seconds, transform}.
bound_share is the share of hair vertices whose weights sum to 1 on bones of the skeleton (the hair_bound check);
follow_error_m is how far the hair resting on the head strays from the head bone's own motion in one walk frame.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import math
import os
import sys

import numpy as np

HAIR_OBJECT = "VGAC_Hair"
HAIR_MATERIAL = "VGAC_Hair"
BLEND_M = 0.10
NAPE_ROLES = ("Neck", "Spine2", "Spine1", "LeftShoulder", "RightShoulder")
HEAD_ROLES = ("Head",)
MAX_INFLUENCES = 4


def role_of(name):
    """The humanoid role in a bone name: "mixamorig:Head" -> "Head"; Extra and spring bones have none."""
    n = name.split(":")[-1].split("|")[-1]
    if n.startswith("Extra_") or n.startswith("Spring_"):
        return None
    return n


def gltf_to_blender(P):
    """glTF (x, y up, z toward the viewer) -> Blender (x, -z, y)."""
    P = np.asarray(P, float).reshape(-1, 3)
    return np.stack([P[:, 0], -P[:, 2], P[:, 1]], axis=1)


def blender_to_gltf(P):
    P = np.asarray(P, float).reshape(-1, 3)
    return np.stack([P[:, 0], P[:, 2], -P[:, 1]], axis=1)


def box_fit(src_lo, src_hi, dst_lo, dst_hi):
    """(scale, shift) carrying a box onto another as rig_fix.restore_from_reference does: uniform scale by the largest
    side, centred in x and y, standing on the same floor. p' = p * scale + shift."""
    src_lo, src_hi, dst_lo, dst_hi = (np.asarray(v, float) for v in (src_lo, src_hi, dst_lo, dst_hi))
    s = float(np.max(dst_hi - dst_lo)) / max(float(np.max(src_hi - src_lo)), 1e-9)
    lo, hi = src_lo * s, src_hi * s
    shift = np.array([(dst_lo[0] + dst_hi[0]) / 2 - (lo[0] + hi[0]) / 2, (dst_lo[1] + dst_hi[1]) / 2 - (lo[1] + hi[1]) / 2,
                      dst_lo[2] - lo[2]])
    return s, shift


def match_positions(A, B, tol):
    """For each point of A the index of the nearest point of B within `tol` (-1 when none), and its distance. A grid
    hash, no KD tree needed. Used to find the npz vertex (and its role) of every vertex of an imported hair object."""
    A = np.asarray(A, float).reshape(-1, 3)
    B = np.asarray(B, float).reshape(-1, 3)
    key = lambda P: np.floor(P / tol).astype(np.int64)  # noqa: E731
    table = {}
    for i, k in enumerate(map(tuple, key(B))):
        table.setdefault(k, []).append(i)
    idx = np.full(len(A), -1, np.int64)
    dist = np.full(len(A), np.inf)
    offs = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    for i, k in enumerate(map(tuple, key(A))):
        best, bd = -1, np.inf
        for o in offs:
            cand = table.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]))
            if not cand:
                continue
            d = np.linalg.norm(B[cand] - A[i], axis=1)
            j = int(np.argmin(d))
            if d[j] < bd:
                best, bd = cand[j], float(d[j])
        if best >= 0 and bd <= tol:
            idx[i], dist[i] = best, bd
    return idx, dist


def roles_by_position(V_obj, npz_verts, npz_role, tol=1e-4):
    """The npz role of each vertex of a hair object, matched by position after both are normalised to their bounding
    boxes (the glTF export reorders and splits vertices; a uniform scale and shift between the two does not matter).
    -> (role (n,) with -1 where nothing matched, worst distance in normalised units)."""
    V = np.asarray(V_obj, float).reshape(-1, 3)
    N = np.asarray(npz_verts, float).reshape(-1, 3)

    def norm(P):
        lo, hi = P.min(axis=0), P.max(axis=0)
        return (P - (lo + hi) / 2) / max(float(np.max(hi - lo)), 1e-9)

    idx, dist = match_positions(norm(V), norm(N), tol)
    role = np.where(idx >= 0, np.asarray(npz_role)[np.clip(idx, 0, None)], -1)
    ok = idx >= 0
    return role.astype(np.int64), float(dist[ok].max()) if ok.any() else None


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / max(e1 - e0, 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def nape_share(z, neck_z, blend=BLEND_M):
    """The body's share of a hair vertex's weight: 0 at and above the neck joint, rising smoothly to 1 at `blend`
    metres below it (Blender z up)."""
    return smoothstep(0.0, blend, neck_z - np.asarray(z, float))


def limit_normalise(w, n=MAX_INFLUENCES):
    """{bone: weight} -> the n strongest, summing to 1 (empty stays empty)."""
    top = sorted(((b, float(x)) for b, x in w.items() if x > 1e-6), key=lambda t: -t[1])[:n]
    tot = sum(x for _, x in top)
    return {b: x / tot for b, x in top} if tot > 0 else {}


def bind_weights(V, role, neck_z, head, near, blend=BLEND_M):
    """Weights for the hair's vertices. V (n, 3) Blender space; role (n,) npz roles (2+k hang group); neck_z the
    Head bone's head height; head the Head bone's name; near: per vertex the nearest body vertex's weights limited to
    the nape bones ({bone: w}, possibly empty). -> (weights list of {bone: w}, stats)."""
    V = np.asarray(V, float)
    role = np.asarray(role)
    share = nape_share(V[:, 2], neck_z, blend)
    out, blended, rest = [], 0, 0
    for i in range(len(V)):
        hang = role[i] >= 2
        s = 0.0 if hang else float(share[i])
        nw = near[i] if near is not None and i < len(near) else {}
        if s > 0 and not nw:
            s = 0.0  # nothing to blend toward: the head carries it
        w = {head: 1.0 - s}
        for b, x in (nw or {}).items():
            w[b] = w.get(b, 0.0) + s * x
        w = limit_normalise(w)
        if s > 1e-6:
            blended += 1
        if w.get(head, 0.0) >= 1.0 - 1e-6:
            rest += 1
        out.append(w)
    n = max(len(V), 1)
    groups = sorted({int(r) - 2 for r in role if r >= 2})
    return out, {"verts": int(len(V)), "nape_blend_verts": int(blended), "rest_share": round(rest / n, 4), "groups": groups}


def bound_share(weights, bones):
    """Share of vertices whose weights sum to 1 (within 1e-3) on bones that exist in the skeleton."""
    if not weights:
        return 0.0
    names = set(bones)
    good = sum(1 for w in weights if w and abs(sum(x for b, x in w.items() if b in names) - 1.0) <= 1e-3)
    return round(good / len(weights), 4)


def nape_filter(w, roles_of_bone):
    """A body vertex's weights kept on the nape bones only, renormalised ({} when it has none of them)."""
    kept = {b: x for b, x in w.items() if roles_of_bone.get(b) in NAPE_ROLES and x > 0}
    tot = sum(kept.values())
    return {b: x / tot for b, x in kept.items()} if tot > 0 else {}


def _opt(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def main():  # pragma: no cover - runs inside Blender
    import time

    import bpy
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    t0 = time.time()
    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    npz_path = os.path.abspath(_opt(argv, "--hair-mesh"))
    report_path = os.path.abspath(_opt(argv, "--report"))
    ref_path = _opt(argv, "--reference")
    blend = float(_opt(argv, "--blend", str(BLEND_M)))
    fps = int(_opt(argv, "--fps", "24"))
    report = {"ok": False}

    def finish():
        report["seconds"] = round(time.time() - t0, 2)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)

    _common.open_glb(src)
    scene = bpy.context.scene
    scene.render.fps = fps
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    bodies = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups and o.name != HAIR_OBJECT]
    if arm is None or not bodies:
        report["error"] = "no skinned body in the animated file"
        finish()
        return
    body = max(bodies, key=lambda o: len(o.data.vertices))
    for o in [o for o in scene.objects if o.name.startswith(HAIR_OBJECT)]:
        bpy.data.objects.remove(o, do_unlink=True)  # a hair left from an earlier bind
    d = np.load(npz_path)
    Vg = np.asarray(d["verts"], float)
    tris = np.asarray(d["tris"], np.int64)
    uv = np.asarray(d["uv"], float).reshape(len(tris), 3, 2)
    role = np.asarray(d["role"], np.int64) if "role" in d.files else np.zeros(len(Vg), np.int64)
    V = gltf_to_blender(Vg)
    # The rest pose: no clip playing.
    ad = arm.animation_data
    muted = []
    if ad is not None:
        ad.action = None
        muted = [(t, t.mute) for t in ad.nla_tracks]
        for t, _ in muted:
            t.mute = True
    arm.data.pose_position = "REST"
    bpy.context.view_layer.update()
    BW = body.matrix_world
    BV = np.array([tuple(BW @ v.co) for v in body.data.vertices])
    transform = {"scale": 1.0, "shift": [0.0, 0.0, 0.0], "reference": None}
    if ref_path and os.path.isfile(ref_path):
        before = set(bpy.data.objects)
        _common.import_glb(ref_path)
        new = [o for o in bpy.data.objects if o not in before]
        refs = [o for o in new if o.type == "MESH"]
        if refs:
            ro = max(refs, key=lambda o: len(o.data.vertices))
            RV = np.array([tuple(ro.matrix_world @ v.co) for v in ro.data.vertices])
            s, sh = box_fit(RV.min(0), RV.max(0), BV.min(0), BV.max(0))
            V = V * s + sh
            transform = {"scale": round(s, 6), "shift": [round(float(x), 5) for x in sh], "reference": os.path.basename(ref_path)}
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
    report["transform"] = transform
    # The hair object.
    me = bpy.data.meshes.new(HAIR_OBJECT)
    me.from_pydata([tuple(p) for p in V], [], [tuple(int(i) for i in t) for t in tris])
    me.update()
    uv_name = body.data.uv_layers.active.name if body.data.uv_layers else "UVMap"
    layer = me.uv_layers.new(name=uv_name)
    flat = uv.reshape(-1, 2)
    # from_pydata keeps the face order and each face's corner order
    layer.data.foreach_set("uv", flat.astype(np.float32).ravel())
    for p in me.polygons:
        p.use_smooth = True
    mat = bpy.data.materials.get(HAIR_MATERIAL) or bpy.data.materials.new(HAIR_MATERIAL)
    me.materials.append(mat)
    hair = bpy.data.objects.new(HAIR_OBJECT, me)
    scene.collection.objects.link(hair)
    hair.parent = arm
    hair.matrix_parent_inverse = arm.matrix_world.inverted()
    mod = hair.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    # Weights.
    AW = arm.matrix_world
    bones = list(arm.data.bones)
    names = [b.name for b in bones]
    roles = {b.name: role_of(b.name) for b in bones}
    head = next((b for b in bones if roles[b.name] in HEAD_ROLES), None)
    if head is None:
        report["error"] = "the skeleton has no Head bone"
        finish()
        return
    neck_z = float((AW @ head.head_local).z)
    gname = {g.index: g.name for g in body.vertex_groups}
    nape_idx, nape_w = [], []
    for v in body.data.vertices:
        w = {gname[g.group]: g.weight for g in v.groups if g.weight > 0 and gname.get(g.group) in roles}
        nw = nape_filter(w, roles)
        if nw:
            nape_idx.append(v.index)
            nape_w.append(nw)
    near = [{} for _ in range(len(V))]
    if nape_idx:
        kd = KDTree(len(nape_idx))
        for j, vi in enumerate(nape_idx):
            kd.insert(Vector(BV[vi]), j)
        kd.balance()
        share = nape_share(V[:, 2], neck_z, blend)
        for i in np.nonzero(share > 0)[0]:
            _, j, _ = kd.find(Vector(V[i]))
            near[int(i)] = nape_w[j]
    weights, stats = bind_weights(V, role, neck_z, head.name, near, blend)
    for i, w in enumerate(weights):
        for b, x in w.items():
            g = hair.vertex_groups.get(b) or hair.vertex_groups.new(name=b)
            g.add([i], float(x), "REPLACE")
    report.update(stats)
    report["faces"] = int(len(tris))
    report["neck_z"] = round(neck_z, 4)
    report["head_bone"] = head.name
    report["bound_share"] = bound_share(weights, names)
    report["cap_verts"] = int(np.sum(role == 1))
    # Does it follow? One frame of the walk (or the first clip): hair resting above the neck should move exactly as the
    # head bone does.
    arm.data.pose_position = "POSE"
    try:
        tracks = list(ad.nla_tracks) if ad else []
        track = next((t for t in tracks if "walk" in t.name.lower()), tracks[0] if tracks else None)
        if track is not None and track.strips:
            for t in tracks:
                t.mute = t != track
            st = track.strips[0]
            scene.frame_set(int(round((st.frame_start + st.frame_end) / 2)))
            dg = bpy.context.evaluated_depsgraph_get()
            he = hair.evaluated_get(dg)
            m2 = he.to_mesh()
            P = np.array([tuple(he.matrix_world @ v.co) for v in m2.vertices])
            he.to_mesh_clear()
            M = AW @ arm.pose.bones[head.name].matrix @ head.matrix_local.inverted() @ AW.inverted()
            sel = [i for i, w in enumerate(weights) if w.get(head.name, 0) >= 1 - 1e-6]
            if sel and len(P) == len(V):
                exp = np.array([tuple(M @ Vector(V[i])) for i in sel])
                report["follow_error_m"] = round(float(np.max(np.linalg.norm(P[sel] - exp, axis=1))), 6)
                report["follow_clip"] = track.name
                report["moved_m"] = round(float(np.max(np.linalg.norm(P[sel] - V[sel], axis=1))), 4)
    except Exception as e:  # noqa: BLE001
        report["follow_error"] = str(e)[:200]
    for t, m in muted:
        t.mute = m
    for pb in arm.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
    scene.frame_set(1)
    bpy.ops.object.select_all(action="SELECT")
    has_anim = bool(ad and ad.nla_tracks)
    args = dict(filepath=dst, export_format="GLB", use_selection=True, export_animations=has_anim,
                export_animation_mode="NLA_TRACKS", export_force_sampling=True, export_apply=False)
    try:
        bpy.ops.export_scene.gltf(**args, export_anim_slide_to_zero=True)
    except TypeError:
        bpy.ops.export_scene.gltf(**args)
    report["ok"] = True
    report["clips"] = [t.name for t in ad.nla_tracks] if ad else []
    finish()
    print("HAIRBIND_OK", report.get("verts"), "verts", report.get("bound_share"), "bound")


if __name__ == "__main__":
    main()
