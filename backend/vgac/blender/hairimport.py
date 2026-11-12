"""Your own hairstyle: read one hair mesh, check it, fit it to the library's reference head and bake it the way the
built-in hairstyles are baked (headless Blender).

    blender -b --python hairimport.py -- import <job.json>
    blender -b --python hairimport.py -- reference <body.gltf> <out.npz>     (one-off, makes data/hairlib/reference_head.npz)

`import` reads the user's GLB, glTF or FBX, refuses what is not one hair mesh (writes result.json with `error` and a
code the backend turns into a plain sentence), puts the hair on the reference head, and writes into out_dir:
<id>.npz and <id>_lite.npz (the arrays of tools/hairlib_blender.py: q, rho_r, tris, uv, role, flow), grey_rgba.png (the
mesh's own colour picture as the library's grey texture, when it has one), thumbs front and quarter, result.json.

Placing: the file's origin and size are unknown, so the hair is scaled and moved by its crown. Its crown (the top 40
percent of its height) is made as wide as the crown of the library's hairstyles, centred on the head, and its top is put
at the library's usual top, in the skull frame (job["stats"]: w, ycen, ztop). Z is up and the front is -Y, as glTF and
FBX imports give. The same fit the built-in styles use then does the rest.
"""
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bpy  # noqa: E402
import hairfit as hf  # noqa: E402

LITE_MAX = 750
THIN_TO = 8000
MAX_SOURCE_TRIS = 300000
MIN_TRIS = 40
TEX = 1024


class Refuse(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code, self.detail = code, detail


# ---------------------------------------------------------------------------------------------------- mesh helpers


def import_file(path):
    before = set(bpy.data.objects)
    low = path.lower()
    try:
        if low.endswith(".fbx"):
            bpy.ops.import_scene.fbx(filepath=path)
        else:
            try:
                bpy.ops.import_scene.gltf(filepath=path, bone_shape_type="NONE")
            except TypeError:
                bpy.ops.import_scene.gltf(filepath=path)
    except Exception as e:  # noqa: BLE001
        raise Refuse("unreadable", str(e)[-300:])
    return [o for o in bpy.data.objects if o not in before]


def arrays(obs):
    V, T, U, base = [], [], [], 0
    for o in obs:
        me = o.data
        me.calc_loop_triangles()
        if not len(me.loop_triangles):
            continue
        v = np.array([o.matrix_world @ x.co for x in me.vertices])
        lt = np.empty(len(me.loop_triangles) * 3, int)
        me.loop_triangles.foreach_get("vertices", lt)
        ll = np.empty(len(me.loop_triangles) * 3, int)
        me.loop_triangles.foreach_get("loops", ll)
        if me.uv_layers:
            uv = np.empty(len(me.loops) * 2)
            me.uv_layers.active.data.foreach_get("uv", uv)
            uv = uv.reshape(-1, 2)
            cu = uv[ll].reshape(-1, 3, 2)
        else:
            cu = np.zeros((len(lt) // 3, 3, 2))
        V.append(v)
        T.append(lt.reshape(-1, 3) + base)
        U.append(cu)
        base += len(v)
    if not V:
        raise Refuse("no_mesh")
    return np.vstack(V), np.vstack(T), np.vstack(U)


def mesh_obj(name, V, T, CU):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], np.asarray(T).tolist())
    me.update()
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", np.asarray(CU, np.float32).reshape(-1))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def decimate(V, T, CU, target):
    if len(T) <= target:
        return V, T, CU
    ob = mesh_obj("dec", V, T, CU)
    m = ob.modifiers.new("d", "DECIMATE")
    m.decimate_type = "COLLAPSE"
    m.ratio = target / len(T)
    m.use_collapse_triangulate = True
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    tmp = bpy.data.objects.new("dec2", me)
    bpy.context.scene.collection.objects.link(tmp)
    out = arrays([tmp])
    bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.objects.remove(tmp, do_unlink=True)
    return out


def components(V, T):
    key = np.round(V / 1e-5).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    parent = np.arange(inv.max() + 1)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for t in inv[T]:
        a = find(t[0])
        for b in t[1:]:
            parent[find(b)] = a
    return np.array([find(i) for i in inv])


def hang_groups(q, T, comp):
    role = np.zeros(len(q), np.int8)
    CUT, LOW = -0.35, -1.25
    hanging = [comp == r for r in np.unique(comp) if (comp == r).sum() >= 20 and q[comp == r, 2].min() < LOW]
    if not hanging:
        return role, []
    allsel = np.zeros(len(q), bool)
    for s in hanging:
        allsel |= s
    below = allsel & (q[:, 2] < CUT)
    if below.sum() < 20:
        return role, []
    ang = np.arctan2(q[below, 0], q[below, 1])
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
        if len(ids) < max(40, 0.1 * len(idx)):
            continue
        P = q[ids]
        zs = np.linspace(P[:, 2].max(), P[:, 2].min(), 7)
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
                           length_q=round(float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum()), 4)))
    return role, groups


def flow_field(q, T, CU, axis):
    P0, P1, P2 = q[T[:, 0]], q[T[:, 1]], q[T[:, 2]]
    e1, e2 = P1 - P0, P2 - P0
    d1 = CU[:, 1] - CU[:, 0]
    d2 = CU[:, 2] - CU[:, 0]
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


def strand_axis(grey_png):
    if not grey_png or not os.path.exists(grey_png):
        return 1
    a, ch = hf._read_png(grey_png)
    L = a[..., 0]
    m = a[..., 3] > 0.5 if ch == 4 else np.ones(L.shape, bool)
    gy, gx = np.gradient(L)
    return 1 if float((gx[m] ** 2).sum()) > float((gy[m] ** 2).sum()) else 0


# ---------------------------------------------------------------------------------------------------- reference


def reference_from_arrays(Vn, T):
    S = hf.find_skull(Vn, hf.head_share(Vn))
    bvh = hf.bvh_from(Vn, T)
    G = hf.radius_grid(S, hf._cast_fn(bvh))
    return dict(V=Vn, T=T, S=S, bvh=bvh, G=G)


def make_reference(gltf, out):
    """One-off: the library's reference figure (Quaternius Superhero_Female, CC0) normalised to 1.7 m, feet at 0."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    new = import_file(gltf)
    obs = [o for o in new if o.type == "MESH" and not any(k in o.name for k in ("Icosphere", "Eye", "Eyebrow"))]
    ob = max(obs, key=lambda o: len(o.data.vertices))
    V, T, _ = arrays([ob])
    Vn, _s, _z0 = hf.normalise(V)
    np.savez_compressed(out, V=Vn.astype(np.float32), T=T.astype(np.int32))
    print("REFERENCE", len(Vn), len(T))


# ---------------------------------------------------------------------------------------------------- import


def crown(q):
    """Width, y centre, top and x centre of the crown: the top 40 percent of the hair's own height."""
    zmax, zmin = q[:, 2].max(), q[:, 2].min()
    m = q[:, 2] >= zmax - 0.4 * (zmax - zmin)
    P = q[m]
    return float(P[:, 0].max() - P[:, 0].min()), float((P[:, 1].max() + P[:, 1].min()) / 2), float(zmax), float((P[:, 0].max() + P[:, 0].min()) / 2)


def base_colour_pixels(obs):
    """The first base-colour picture the meshes use, scaled to 1024, as (float RGBA array, size), or (None, 0)."""
    for o in obs:
        for slot in o.material_slots:
            m = slot.material
            if not m or not m.use_nodes:
                continue
            for n in m.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image is not None and n.image.size[0] > 0:
                    if any(l.to_socket.name == "Base Color" for l in n.outputs[0].links):
                        img = n.image.copy()
                        try:
                            img.scale(TEX, TEX)
                            px = np.empty(TEX * TEX * 4, np.float32)
                            img.pixels.foreach_get(px)
                        except Exception:  # noqa: BLE001
                            continue
                        return px.reshape(TEX, TEX, 4), TEX
    return None, 0


def grey_rgba(px, cards):
    """The library's grey texture: luminance with the median hair value at 0.5 (shells opaque, cards keep alpha)."""
    c = px[..., :3]
    lin = c  # Blender gives linear values already
    L = lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    alpha = px[..., 3] if cards else np.ones(L.shape, np.float32)
    m = alpha > 0.5
    med = float(np.median(L[m])) if m.any() else float(L.mean())
    g = np.clip(L * (0.5 / max(med, 1e-4)), 0, 1)
    if cards:
        g = np.where(m, g, np.minimum(g, 0.5))
    return np.dstack([g, g, g, alpha]).astype(np.float32)


def save_png(arr, path):
    h, w = arr.shape[:2]
    img = bpy.data.images.new("g", w, h, alpha=True)
    img.pixels.foreach_set(arr.reshape(-1))
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def run_import(job):
    out = job["out_dir"]
    os.makedirs(out, exist_ok=True)
    sid = job["id"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ref_npz = np.load(job["reference"])
    ref = reference_from_arrays(ref_npz["V"].astype(float), ref_npz["T"])
    stats = job["stats"]
    new = import_file(job["src"])
    meshes = [o for o in new if o.type == "MESH" and not o.name.lower().startswith("icosphere")]
    arms = [o for o in new if o.type == "ARMATURE"]
    if not meshes:
        raise Refuse("no_mesh")
    if any(len(a.data.bones) >= 10 for a in arms) or any(len(o.vertex_groups) >= 10 for o in meshes):
        raise Refuse("whole_character")
    V, T, CU = arrays(meshes)
    src_tris = int(len(T))
    if src_tris < MIN_TRIS:
        raise Refuse("too_small", str(src_tris))
    if src_tris > MAX_SOURCE_TRIS:
        raise Refuse("too_big", str(src_tris))
    ext = V.max(0) - V.min(0)
    if not np.all(np.isfinite(ext)) or ext.min() <= 1e-9:
        raise Refuse("flat")
    if ext[2] / max(ext[0], 1e-9) > 4.5:
        raise Refuse("whole_character")
    px, _size = base_colour_pixels(meshes)
    see_through = float((px[..., 3] < 0.5).mean()) if px is not None else 0.0
    cards = see_through > 0.1
    grey_png = None
    if px is not None:
        grey_png = os.path.join(out, "grey_rgba.png")
        save_png(grey_rgba(px, cards), grey_png)
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    w, yc, zt, xc = crown(V)
    if w <= 1e-9:
        raise Refuse("flat")
    s = stats["w"] / w
    q = np.stack([(V[:, 0] - xc) * s, (V[:, 1] - yc) * s + stats["ycen"], (V[:, 2] - zt) * s + stats["ztop"]], 1)
    S = ref["S"]
    V = hf.to_world(S, q)
    if len(T) > THIN_TO:
        V, T, CU = decimate(V, T, CU, THIN_TO)
    q = hf.to_local(S, V)
    rho_r = hf.sample_grid(ref["G"], q)
    role, groups = hang_groups(q, T, components(V, T))
    axis = strand_axis(grey_png)
    flow = flow_field(q, T, CU, axis)
    np.savez_compressed(os.path.join(out, f"{sid}.npz"), q=q.astype(np.float32), rho_r=rho_r.astype(np.float32),
                        tris=T.astype(np.int32), uv=CU.astype(np.float32), role=role, flow=flow.astype(np.float32))
    lite = None
    if not cards:
        lv, lt, lu = decimate(V, T, CU, LITE_MAX) if len(T) > LITE_MAX else (V, T, CU)
        if len(lt) > LITE_MAX:
            lv, lt, lu = decimate(lv, lt, lu, int(LITE_MAX * 0.95))
        lq = hf.to_local(S, lv)
        lrole, _ = hang_groups(lq, lt, components(lv, lt))
        np.savez_compressed(os.path.join(out, f"{sid}_lite.npz"), q=lq.astype(np.float32),
                            rho_r=hf.sample_grid(ref["G"], lq).astype(np.float32), tris=lt.astype(np.int32),
                            uv=lu.astype(np.float32), role=lrole, flow=flow_field(lq, lt, lu, axis).astype(np.float32))
        lite = int(len(lt))
    back = hf.place(q, rho_r, ref["G"], S)
    err = float(np.abs(back - V).max())
    zt_ref = ref["V"][:, 2].max()
    try:
        body = dict(V=ref["V"], T=ref["T"], S=S)
        if grey_png:
            mat = hf.hair_material("t", grey_png, None, cards, True)
        else:
            mat = hf.flat_material("t", (128, 128, 128))
        os.makedirs(os.path.join(out, "thumbs"), exist_ok=True)
        for view, yaw in (("front", 0), ("quarter", 35)):
            hf._render(os.path.join(out, "thumbs", f"{sid}_{view}.png"), body, [("hair", V, T, CU, mat)], size=256, yaw=yaw)
    except Exception as e:  # noqa: BLE001
        print("THUMB_ERROR", e, flush=True)
    res = dict(triangles=int(len(T)), source_triangles=src_tris, vertices=int(len(V)), lite_triangles=lite, hang_groups=groups,
               length_rel_head=round(float((zt_ref - V[:, 2].min()) / S["HH"]), 2), self_check_mm=round(err * 1000, 3),
               kind="cards" if cards else "shell", has_texture=bool(grey_png), see_through=round(see_through, 3),
               head_top_over_hair_mm=round(float((V[:, 2].max() - zt_ref) * 1000), 1))
    json.dump(res, open(os.path.join(out, "result.json"), "w"), indent=1)
    print("BUILT", json.dumps(res)[:300], flush=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    if argv[0] == "reference":
        make_reference(argv[1], argv[2])
        return
    job = json.load(open(argv[1]))
    try:
        run_import(job)
    except Refuse as r:
        os.makedirs(job["out_dir"], exist_ok=True)
        json.dump({"error": r.code, "detail": r.detail}, open(os.path.join(job["out_dir"], "result.json"), "w"))
        print("REFUSED", r.code, r.detail, flush=True)
    except Exception as e:  # noqa: BLE001
        os.makedirs(job["out_dir"], exist_ok=True)
        json.dump({"error": "failed", "detail": str(e)[-400:]}, open(os.path.join(job["out_dir"], "result.json"), "w"))
        raise


if __name__ == "__main__":
    main()
