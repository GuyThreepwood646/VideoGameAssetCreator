"""Blender headless: part the legs where the 3D build joined them, and measure forearms joined to the body.

Run: blender -b --python joins.py -- <rig.glb> <out.glb> --report <json> [--measure-only]

The 3D builder makes one closed surface from the pictures, so parts that touch or nearly touch in the pictures come
out joined: a strand of surface between the shoes (Vivi), a forearm grown into the coat. Once the model is rigged each
point of the surface belongs mostly to one bone, and these joins show as triangles shared by parts that never meet on
a body: the two legs below the crotch, or a forearm or hand and the body.

- Legs: the edges where faces of the left leg meet faces of the right leg below LEG_JOIN_MAX of the hips' height are
  split, so the legs move apart freely, and the points within a few rings of the split let go of the other leg's
  weights. Measured on 9 rigged characters: Vivi's strand reaches 9 cm off the ground (hips at 50 cm); every other
  leg-to-leg join was 26 percent of the hips' height or higher (the crotch, a tabard or a cape between the legs, which
  must stay whole: cutting at 60 percent tore the knight's tabard).
- Forearms: only measured (arm_joins). Splitting them was tried on Vivi and left holes in the coat's sides as soon as
  the arms moved, because the sleeve and the coat share one surface there; the rig check says so instead, and the fix
  is a first picture with the arms clear of the body. 6 of 9 characters had none; Vivi 144 and 109 triangles,
  TheHeir 57 and 50, the Ranger 12 on one side.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

LEG_JOIN_MAX = 0.25  # share of the hips' height above the ground under which a leg-to-leg join is a web
RELEASE_RINGS = 3  # rings of points round a split that let go of the other leg's weights
ARM_JOIN_MIN = 20  # forearm-to-body triangles on one side from which the rig check speaks


def bone_class(name):
    """'Lleg', 'Rleg', 'Llowarm', 'Rlowarm', 'Luparm', 'Ruparm', 'torso' or 'other' for a Mixamo-named bone."""
    n = str(name).replace("mixamorig:", "")
    for side in ("Left", "Right"):
        if n.startswith(side):
            r = n[len(side):]
            if r.startswith(("UpLeg", "Leg", "Foot", "Toe")):
                return side[0] + "leg"
            if r.startswith(("ForeArm", "Hand")):
                return side[0] + "lowarm"
            if r.startswith("Arm"):
                return side[0] + "uparm"
            if r.startswith("Shoulder"):
                return "torso"
    if n.startswith(("Hips", "Spine", "Neck", "Head")):
        return "torso"
    return "other"


def face_labels(vlab, F):
    """Each triangle's label: the label most of its corners carry (the first corner's on a three-way tie)."""
    L = np.asarray(vlab)[np.asarray(F)]
    out = L[:, 0].copy()
    two = (L[:, 1] == L[:, 2]) & (L[:, 1] != L[:, 0])
    out[two] = L[two, 1]
    return out


def find_joins(V, F, vlab, hips_z, leg_max=LEG_JOIN_MAX):
    """-> {leg_edges: [(i, j)] vertex pairs to split, leg_edge_z: [...], arm_joins: {L: n, R: n} forearm-or-hand to body
    triangles}. V (n, 3) Z up; F (m, 3); vlab (n,) labels from bone_class; hips_z: the hips' height above the lowest
    point."""
    V, F = np.asarray(V, dtype=float), np.asarray(F, dtype=int)
    vlab = np.asarray(vlab)
    zmin = float(V[:, 2].min())
    flab = face_labels(vlab, F)
    edges = {}
    for fi, tri in enumerate(F):
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            edges.setdefault((min(a, b), max(a, b)), []).append(fi)
    leg_edges, zs = [], []
    for (a, b), fs in edges.items():
        if len(fs) != 2:
            continue
        if {flab[fs[0]], flab[fs[1]]} != {"Lleg", "Rleg"}:
            continue
        z = (V[a, 2] + V[b, 2]) / 2 - zmin
        if z < leg_max * float(hips_z):
            leg_edges.append((int(a), int(b)))
            zs.append(round(float(z), 4))
    L = vlab[F]
    body = np.isin(L, ["torso", "Lleg", "Rleg"]).any(axis=1)
    arm = {s: int(((L == s + "lowarm").any(axis=1) & body).sum()) for s in "LR"}
    return {"leg_edges": leg_edges, "leg_edge_z": zs, "arm_joins": arm}


def main():  # pragma: no cover - runs inside Blender
    import bmesh
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    measure_only = _common.flag("--measure-only")
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "leg_edges": 0, "released": 0, "arm_joins": None, "reason": None}
    if arm is None or not meshes:
        report["reason"] = "no_rig"
    else:
        mesh = max(meshes, key=lambda o: len(o.data.vertices))
        me = mesh.data
        mw = np.array(mesh.matrix_world)
        V = np.array([v.co[:] for v in me.vertices], dtype=float)
        Vw = (np.c_[V, np.ones(len(V))] @ mw.T)[:, :3]
        gcls = [bone_class(g.name) for g in mesh.vertex_groups]
        vlab = []
        for v in me.vertices:
            best = max(v.groups, key=lambda g: g.weight, default=None)
            vlab.append(gcls[best.group] if best is not None else "other")
        hips = next((b for b in arm.data.bones if b.name.replace("mixamorig:", "") == "Hips"), None)
        if hips is None:
            report["reason"] = "no_hips"
        else:
            hz = float((arm.matrix_world @ hips.head_local).z) - float(Vw[:, 2].min())
            bm = bmesh.new()
            bm.from_mesh(me)
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
            bm.verts.ensure_lookup_table()
            F = np.array([[v.index for v in f.verts] for f in bm.faces], dtype=int)
            found = find_joins(Vw, F, vlab, hz)
            report.update(arm_joins=found["arm_joins"], hips_z=round(hz, 4),
                          leg_edge_z=[min(found["leg_edge_z"]), max(found["leg_edge_z"])] if found["leg_edge_z"] else None)
            pairs = set(found["leg_edges"])
            cut = [e for e in bm.edges if (min(e.verts[0].index, e.verts[1].index), max(e.verts[0].index, e.verts[1].index)) in pairs]
            report["leg_edges"] = len(cut)
            if cut and not measure_only:
                # Which leg each point near the split goes with: the leg of the faces round it (after the split),
                # not its own strongest bone: the strand's far end leans on the other foot by weight.
                flab = {}
                for f in bm.faces:
                    labs = [vlab[v.index] for v in f.verts]
                    flab[f] = max(set(labs), key=labs.count)
                near = set(v for e in cut for v in e.verts)
                for _ in range(RELEASE_RINGS):
                    near |= {w for v in list(near) for e in v.link_edges for w in e.verts}
                key = lambda co: (round(co.x, 6), round(co.y, 6), round(co.z, 6))
                near_keys = {key(v.co) for v in near}
                bmesh.ops.split_edges(bm, edges=cut)
                dl = bm.verts.layers.deform.active
                released = 0
                for v in bm.verts:
                    if dl is None or key(v.co) not in near_keys:
                        continue
                    labs = {flab.get(f, "other") for f in v.link_faces}
                    if labs not in ({"Lleg"}, {"Rleg"}):
                        continue
                    other = "Rleg" if labs == {"Lleg"} else "Lleg"
                    d = v[dl]
                    drop = [g for g in d.keys() if gcls[g] == other]
                    if not drop or not any(d[g] > 0.02 for g in drop):
                        continue
                    keep = {g: w for g, w in d.items() if g not in drop}
                    tot = sum(keep.values())
                    if tot <= 1e-6:
                        continue  # weighted to the other leg only: leave it
                    for g in list(d.keys()):
                        del d[g]
                    for g, w in keep.items():
                        d[g] = w / tot
                    released += 1
                report["released"] = released
                bm.to_mesh(me)
                me.update()
            bm.free()
            report["ok"] = True
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if not measure_only and report.get("leg_edges"):
        bpy.ops.object.select_all(action="DESELECT")
        keep = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("JOINS_OK", report.get("leg_edges"), report.get("released"), report.get("arm_joins"))


if __name__ == "__main__":  # pragma: no cover
    main()
