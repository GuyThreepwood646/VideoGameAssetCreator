"""Hair library builder, Blender part (run by tools/hairlib_build.py; not by hand).

    blender -b --python tools/hairlib_blender.py -- <job.json>

For every style in the job: import the pack mesh (OBJ or glTF, read as data only) or generate a rule-built style,
scale it together with its reference head so the reference figure is 1.7 m with the feet at 0 (Z up, facing -y),
optionally thin it (Decimate, collapse), fit the reference skull (backend/vgac/blender/hairfit.py find_skull), and
bake the plan's arrays (docs/HAIR-LIBRARY-PLAN.md section 4): q (vertices in the normalised skull frame), rho_r (the
reference scalp radius under each vertex), tris, uv (corner UVs), role (0 on the head, 2+k hang group k), flow (strand
direction in the skull frame). Loose pieces that hang below the jaw are clustered into up to 3 groups, each with a
path (root first) in the skull frame. Shells also get a `lite` variant of at most 750 triangles. Thumbnails (front and
three-quarter, 256 px) are rendered on the reference head in neutral grey with the grey texture.

Reference heads: the Quaternius base characters for Quaternius and rule-built styles; for MakeHuman styles the system
pack's female_generic / male_generic proxy bodies (the hm08 base mesh is not in the packs; the proxies share its space).
Writes <out>/styles/<id>.npz, <out>/styles/<id>_lite.npz, <out>/thumbs/<id>_{front,quarter}.png and
<out>/build_measures.json."""
import json
import math
import os
import sys

import numpy as np
import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend", "vgac", "blender"))
sys.path.insert(0, HERE)
import hairfit as hf  # noqa: E402
import hairlib_rulebuilt as rb  # noqa: E402
from mathutils import Vector  # noqa: E402

LITE_MAX = 750


def import_meshes(path):
    before = set(bpy.data.objects)
    if path.lower().endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=path)
    else:
        try:
            bpy.ops.import_scene.gltf(filepath=path, bone_shape_type="NONE")
        except TypeError:
            bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    return new


def arrays(obs):
    V, T, U, base = [], [], [], 0
    for o in obs:
        me = o.data
        me.calc_loop_triangles()
        v = np.array([o.matrix_world @ x.co for x in me.vertices])
        lt = np.empty(len(me.loop_triangles) * 3, int); me.loop_triangles.foreach_get("vertices", lt)
        ll = np.empty(len(me.loop_triangles) * 3, int); me.loop_triangles.foreach_get("loops", ll)
        if me.uv_layers:
            uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 2)
            cu = uv[ll].reshape(-1, 3, 2)
        else:
            cu = np.zeros((len(lt) // 3, 3, 2))
        V.append(v); T.append(lt.reshape(-1, 3) + base); U.append(cu); base += len(v)
    return np.vstack(V), np.vstack(T), np.vstack(U)


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)


def mesh_obj(name, V, T, CU):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], np.asarray(T).tolist()); me.update()
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", np.asarray(CU, np.float32).reshape(-1))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def decimate(V, T, CU, target):
    """Collapse decimation to about `target` triangles, UV seams kept."""
    if len(T) <= target:
        return V, T, CU
    ob = mesh_obj("dec", V, T, CU)
    m = ob.modifiers.new("d", "DECIMATE"); m.decimate_type = "COLLAPSE"; m.ratio = target / len(T)
    m.use_collapse_triangulate = True
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    tmp = bpy.data.objects.new("dec2", me); bpy.context.scene.collection.objects.link(tmp)
    out = arrays([tmp])
    bpy.data.objects.remove(ob, do_unlink=True); bpy.data.objects.remove(tmp, do_unlink=True)
    return out


REF_CACHE = {}


def reference(kind, path):
    if kind in REF_CACHE:
        return REF_CACHE[kind]
    new = import_meshes(path)
    obs = [o for o in new if o.type == "MESH" and not any(k in o.name for k in ("Icosphere", "Eye", "Eyebrow"))]
    ob = max(obs, key=lambda o: len(o.data.vertices))
    V, T, _ = arrays([ob])
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    Vn, s, z0 = hf.normalise(V)
    S = hf.find_skull(Vn, hf.head_share(Vn))
    bvh = hf.bvh_from(Vn, T)
    G = hf.radius_grid(S, hf._cast_fn(bvh))
    REF_CACHE[kind] = dict(V=Vn, T=T, s=s, z0=z0, S=S, bvh=bvh, G=G)
    return REF_CACHE[kind]


def components(V, T):
    key = np.round(V / 1e-5).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    parent = np.arange(inv.max() + 1)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for t in inv[T]:
        a = find(t[0])
        for b in t[1:]:
            parent[find(b)] = a
    return np.array([find(i) for i in inv])


def strand_axis(grey_png):
    """0 when strands run along U (columns change), 1 along V, from the texture's gradient structure tensor."""
    if not grey_png or not os.path.exists(grey_png):
        return 1
    a, ch = hf._read_png(grey_png)
    L = a[..., 0]
    m = a[..., 3] > 0.5 if ch == 4 else np.ones(L.shape, bool)
    gy, gx = np.gradient(L)
    sx, sy = float((gx[m] ** 2).sum()), float((gy[m] ** 2).sum())
    return 1 if sx > sy else 0      # strong change across columns -> lines run along v


def flow_field(q, T, CU, axis):
    """Per-vertex strand direction in the skull frame: the surface direction of increasing V (or U), signed to
    point away from the top of the head."""
    P0, P1, P2 = q[T[:, 0]], q[T[:, 1]], q[T[:, 2]]
    e1, e2 = P1 - P0, P2 - P0
    d1 = CU[:, 1] - CU[:, 0]; d2 = CU[:, 2] - CU[:, 0]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    if axis == 1:
        t = (e2 * d1[:, :1] - e1 * d2[:, :1]) / det[:, None]
    else:
        t = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None]
    t = hf.nrm(t)
    c = (P0 + P1 + P2) / 3
    away = hf.nrm(c - np.array([0, 0, 1.0]))
    t = np.where(((t * away).sum(1) < 0)[:, None], -t, t)
    f = np.zeros_like(q)
    for k in range(3):
        np.add.at(f, T[:, k], t)
    f = hf.nrm(f)
    bad = np.linalg.norm(f, axis=1) < 0.5
    f[bad] = hf.nrm(q[bad] - np.array([0, 0, 1.0]))
    return f


def hang_groups(q, T, comp, override):
    """Loose pieces reaching below the jaw, clustered by side into up to 3 groups. Role 2+k for their vertices below
    the cut height; path (root first) from slice centroids."""
    role = np.zeros(len(q), np.int8)
    if override == []:
        return role, []
    CUT, LOW = -0.35, -1.25            # normalised skull heights: start of hanging; must reach below the jaw
    hanging = []
    for r in np.unique(comp):
        sel = comp == r
        if sel.sum() >= 20 and q[sel, 2].min() < LOW:
            hanging.append(sel)
    if not hanging:
        return role, []
    allsel = np.zeros(len(q), bool)
    for s in hanging:
        allsel |= s
    below = allsel & (q[:, 2] < CUT)
    if below.sum() < 20:
        return role, []
    # cluster by angle round the vertical axis: back, left, right (k-means, at most 3)
    ang = np.arctan2(q[below, 0], q[below, 1])          # 0 = back (+y)
    centres = np.array([0.0, 1.6, -1.6])
    for _ in range(10):
        lab = np.argmin(np.abs(((ang[:, None] - centres[None]) + np.pi) % (2 * np.pi) - np.pi), 1)
        for k in range(3):
            if (lab == k).any():
                centres[k] = math.atan2(np.sin(ang[lab == k]).mean(), np.cos(ang[lab == k]).mean())
    idx = np.nonzero(below)[0]
    groups = []
    for k in range(3):
        ids = idx[lab == k]
        if len(ids) < max(40, 0.1 * len(idx)):      # a few stray strands do not make a spring chain
            continue
        P = q[ids]
        z0, z1 = P[:, 2].max(), P[:, 2].min()
        zs = np.linspace(z0, z1, 7)
        path = []
        for i in range(len(zs)):
            lo, hi = zs[min(i + 1, 6)], zs[max(i - 1, 0)]
            m = (P[:, 2] >= min(lo, hi)) & (P[:, 2] <= max(lo, hi))
            if m.any():
                path.append(P[m].mean(0))
        if len(path) < 2:
            continue
        path = np.array(path)
        gk = len(groups)
        role[ids] = 2 + gk
        groups.append(dict(k=gk, kind="Hair", path_q=np.round(path, 5).tolist(),
                           length_q=round(float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum()), 4), vertices=int(len(ids))))
    return role, groups


def rule_mesh(sid, ref):
    spec = rb.RULE_STYLES[sid]
    S = ref["S"]
    sk = rb.Skull(S["R"], S["ear_phi"], S["ear_out"])
    locks, ex = rb.generate(spec["style"], spec["params"], sk, seed=1)
    bvh = ref["bvh"]

    def ray(o, d, dist):
        h = bvh.ray_cast(Vector(o), Vector(d), dist)
        return None if h[0] is None else np.array(h[0])

    def nearest(p):
        h = bvh.find_nearest(Vector(p))
        return None if h[0] is None else (np.array(h[0]), np.array(h[1]))
    # the generator works in the skull frame with the skull's own radii; place_on moves it to the world
    rb.place_on(locks, S, ray, nearest)
    for k in ("tie", "bun_centre"):
        if k in ex:
            if k == "tie":
                p, ax, r = ex["tie"]
                ex["tie"] = (rb.to_world_s(S, p), np.asarray(ax), r)
    M = rb.assemble_rule(locks, ex, np.asarray(S["c"]), S["R"][2])
    return M


def thumbs(out, sid, ref, V, T, CU, grey, normal, cutout):
    try:
        body = dict(V=ref["V"], T=ref["T"], S=ref["S"])
        mat = hf.hair_material("t", grey, normal, cutout, True)
        for view, yaw in (("front", 0), ("quarter", 35)):
            hf._render(os.path.join(out, "thumbs", f"{sid}_{view}.png"), body, [("hair", V, T, CU, mat)], size=256, yaw=yaw)
    except Exception as e:
        print("THUMB_ERROR", sid, e, flush=True)


def main():
    job = json.load(open(sys.argv[sys.argv.index("--") + 1]))
    out = job["out"]
    for d in ("styles", "thumbs"):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    measures = {}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for st in job["styles"]:
        sid = st["id"]
        ref = reference(st["ref"], job["refs"][st["ref"]])
        if st.get("rule"):
            M = rule_mesh(sid, ref)
            V, T, CU = M["verts"], M["tris"], M["cuv"]
            rule_hang = M["hang"]
            # its grey strip texture, painted here because the atlas is only known now; the driver turns it into LA
            g = rb.grey_texture(M["rects"], M["meta"], size=int(job.get("tex", 1024)))
            os.makedirs(os.path.join(out, "tex"), exist_ok=True)
            gp = os.path.join(out, "tex", f"{sid}_rule_grey.png")
            img = bpy.data.images.new("rg", g.shape[1], g.shape[0], alpha=True)
            img.pixels.foreach_set(np.dstack([g, g, g, np.ones_like(g)])[::-1].astype(np.float32).ravel())
            img.filepath_raw = gp; img.file_format = "PNG"; img.save(); bpy.data.images.remove(img)
            st["grey"] = gp
        else:
            new = import_meshes(st["path"])
            obs = [o for o in new if o.type == "MESH" and not o.name.startswith("Icosphere")]
            V, T, CU = arrays(obs)
            for o in new:
                bpy.data.objects.remove(o, do_unlink=True)
            V = V * ref["s"] - np.array([0, 0, ref["z0"]])
            rule_hang = None
        src_tris = int(len(T))
        if st.get("thin"):
            V, T, CU = decimate(V, T, CU, int(st["thin"]))
        S = ref["S"]
        q = hf.to_local(S, V)
        rho_r = hf.sample_grid(ref["G"], q)
        comp = components(V, T)
        if rule_hang is not None and st.get("hang_groups") is None:
            # rule-built: the generator knows its hanging locks; group them the same way
            comp = np.where(rule_hang > 0, -1, comp)
        role, groups = hang_groups(q, T, comp, st.get("hang_groups"))
        axis = 1 if st.get("rule") else strand_axis(st.get("grey"))
        flow = flow_field(q, T, CU, axis)
        np.savez_compressed(os.path.join(out, "styles", f"{sid}.npz"), q=q.astype(np.float32), rho_r=rho_r.astype(np.float32),
                            tris=T.astype(np.int32), uv=CU.astype(np.float32), role=role, flow=flow.astype(np.float32))
        lite = None
        if st.get("kind") == "shell" and st.get("lite", True):
            lv, lt, lu = decimate(V, T, CU, LITE_MAX) if len(T) > LITE_MAX else (V, T, CU)
            if len(lt) > LITE_MAX:
                lv, lt, lu = decimate(lv, lt, lu, int(LITE_MAX * 0.95))
            lq = hf.to_local(S, lv)
            lrole, _ = hang_groups(lq, lt, components(lv, lt), [] if not groups else None)
            np.savez_compressed(os.path.join(out, "styles", f"{sid}_lite.npz"), q=lq.astype(np.float32),
                                rho_r=hf.sample_grid(ref["G"], lq).astype(np.float32), tris=lt.astype(np.int32),
                                uv=lu.astype(np.float32), role=lrole, flow=flow_field(lq, lt, lu, axis).astype(np.float32))
            lite = int(len(lt))
        # self-check: placing the style on its own reference skull gives back its positions
        back = hf.place(q, rho_r, ref["G"], S)
        err = float(np.abs(back - V).max())
        zt = ref["V"][:, 2].max()
        hh = S["HH"]
        thumbs(out, sid, ref, V, T, CU, st.get("grey"), st.get("normal"), st.get("alpha") == "cutout")
        measures[sid] = dict(triangles=int(len(T)), source_triangles=src_tris, vertices=int(len(V)), lite_triangles=lite,
                             hang_groups=groups, length_rel_head=round(float((zt - V[:, 2].min()) / hh), 2),
                             self_check_mm=round(err * 1000, 3), strand_axis="v" if axis == 1 else "u",
                             skull=dict(R=S["R"], resid_mm=round(S["resid_mm"], 2)))
        print("BUILT", sid, json.dumps(measures[sid])[:300], flush=True)
        json.dump(measures, open(os.path.join(out, "build_measures.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
