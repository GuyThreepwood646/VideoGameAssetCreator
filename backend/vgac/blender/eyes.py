"""Blender headless: cut the two eye openings into a character's head and set an eye behind each (clean-up step).

Run: blender -b --python eyes.py -- <in.glb> <out.glb> --job <json> --report <json>

The job (vgac/eyes.py mesh_job) is in the head camera's frame: u right, v up, w away from the camera, metres from the
target. For each eye the faces that the lid outline crosses are split exactly along it (no other face changes), what lies
inside is removed, the cut edge is turned inward and closed behind into a socket, and a separate closed piece is added.
Its front is a ball fitted to the depth of the lid edge, tucked behind the skin on the lid line, so no socket wall
shows beside it in a turned view; past the opening it stays behind the skin. Both stay closed surfaces. Their faces use
the squares of texture the job names. The model is written only when it has no more open edges than it had.
"""
import json
import math
import os
import sys

BEHIND_EDGE_M = 0.0003  # the eye's front is this far behind the lid edge, in the middle of the fit
CLEAR_M = 0.0007  # past the opening the eye stays this far behind the skin
RIM_BEHIND_M = 0.0004  # the socket wall ends this far behind the eye's front
UNDER_LID_M = 0.002  # the eye reaches this far past the opening, under the lids
AT_EDGE_M = 0.0002  # on the lid line the eye is at least this far behind the skin
DEPTH_WINDOW_M = 0.02
WELD_M = 0.00005
RADIUS_OF_WIDTH = (0.6, 2.4)  # the ball's radius is fitted within these shares of the eye's width
FORWARD_M = 0.0012  # the eye comes no further than this in front of the surface it replaces
INNER_SHARES = (0.0, 0.35, 0.65)  # rows inside the opening whose depth the ball also follows
EDGE_SHARE = 0.8  # the ball is set so this share of the surface it follows is in front of it (a deeper eye leaves the
# lids standing as thin flaps)
RINGS = 3  # rows of the eye from its middle to the lid line (one more goes under the lids)
SEGMENTS = 36
SMOOTH_PASSES = 6  # the front is evened out this many times, so the iris is not bent by the old surface's lumps
SKIN, RIM, EYE = 0, 1, 2


def inside(poly, u, v):
    n, hit = len(poly), False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > v) != (y2 > v) and u < (x2 - x1) * (v - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def distance_to(poly, u, v):
    best = float("inf")
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((u - ax) * dx + (v - ay) * dy) / max(dx * dx + dy * dy, 1e-18)))
        best = min(best, math.hypot(u - ax - dx * t, v - ay - dy * t))
    return best


def barycentric(p, a, b, c):
    """Weights of 2D point p in the triangle a, b, c (None when the triangle has no area)."""
    area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(area) < 1e-14:
        return None
    w1 = ((p[0] - a[0]) * (c[1] - a[1]) - (p[1] - a[1]) * (c[0] - a[0])) / area
    w2 = ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / area
    return (1.0 - w1 - w2, w1, w2)


def dome(u, v, centre_u, centre_v, radius):
    """How far a ball of this radius comes forward at (u, v) from the depth of its own centre."""
    return math.sqrt(max(radius ** 2 - (u - centre_u) ** 2 - (v - centre_v) ** 2, 0.0))


def fit_ball(points, mid_u, mid_v, width, share=EDGE_SHARE):
    """(centre_u, centre_v, centre_w, radius) of the ball that follows the (u, v, depth) points of the lid edge best:
    the radius and the centre's place are searched, and its depth is set so `share` of the points are nearer the camera
    than the ball."""
    best = None
    for i in range(9):
        radius = width * (RADIUS_OF_WIDTH[0] + (RADIUS_OF_WIDTH[1] - RADIUS_OF_WIDTH[0]) * i / 8)
        for j in range(-5, 6):
            for k in range(-3, 4):
                cu, cv = mid_u + j * 0.001, mid_v + k * 0.0012
                behind = sorted(w + dome(u, v, cu, cv, radius) for u, v, w in points)
                cw = behind[int((1.0 - share) * (len(behind) - 1))]
                miss = sum(abs(b - cw) for b in behind) / len(behind)
                if best is None or miss < best[0]:
                    best = (miss, cu, cv, cw, radius)
    return best[1], best[2], best[3], best[4]


def smoothed(middle, rows, passes=SMOOTH_PASSES):
    """The depths of the eye's front, evened out: the middle one and the rows round it, the last row (on the lid line)
    held. -> (middle, rows)."""
    rows = [list(r) for r in rows]
    for _ in range(passes):
        out = [list(r) for r in rows]
        n = len(rows[0])
        for i in range(len(rows) - 1):
            for j in range(n):
                inner = middle if i == 0 else rows[i - 1][j]
                around = (rows[i][(j - 1) % n] + rows[i][(j + 1) % n] + inner + rows[i + 1][j]) / 4
                out[i][j] = (rows[i][j] + around) / 2
        middle = (middle + sum(rows[0]) / n) / 2
        rows = out
    return middle, rows


def resample(poly, count):
    """count points evenly spaced by length round a closed polygon."""
    lengths = [math.dist(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]
    total, out, i, done = sum(lengths), [], 0, 0.0
    for k in range(count):
        at = total * k / count
        while done + lengths[i] < at:
            done += lengths[i]
            i += 1
        t = (at - done) / max(lengths[i], 1e-18)
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return out


def main():  # pragma: no cover - runs inside Blender
    import bmesh
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import barycentric_transform, delaunay_2d_cdt

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src_path, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    job = json.load(open(_common.opt("--job", None), encoding="utf-8"))
    report_path = _common.opt("--report", None)
    _common.open_glb(src_path)
    body = max((o for o in bpy.context.scene.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
    MW = body.matrix_world.copy()
    MI = MW.inverted()

    def blender_axes(v):  # glTF (y up) -> Blender (z up)
        return Vector((v[0], -v[2], v[1]))

    cam = job["camera"]
    T, R, U, F = (blender_axes(cam[k]) for k in ("target", "right", "up", "forward"))

    def uvw(co):
        d = MW @ co - T
        return d.dot(R), d.dot(U), d.dot(F)

    def local(u, v, w):
        return MI @ (T + R * u + U * v + F * w)

    # the skin as it was, for its depth under the eye and its shading afterwards
    old = body.data
    old.calc_loop_triangles()
    old_v = [v.co.copy() for v in old.vertices]
    old_n = [c.vector.copy() for c in old.corner_normals]
    old_tri = [tuple(t.vertices) for t in old.loop_triangles]
    old_loops = [tuple(t.loops) for t in old.loop_triangles]
    old_tree = BVHTree.FromPolygons(old_v, old_tri)
    forward = (MI.to_3x3() @ F).normalized()

    def skin_depth(u, v, lid):
        hit = old_tree.ray_cast(local(u, v, lid - 0.05), forward)
        return None if hit[0] is None else uvw(hit[0])[2]

    bm = bmesh.new()
    bm.from_mesh(old)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    near_eyes = [v for v in bm.verts
                 if any((MW @ v.co - (T + R * e["centre"][0] + U * e["centre"][1] + F * e["lid"])).length < 0.05 for e in job["eyes"])]
    bmesh.ops.remove_doubles(bm, verts=near_eyes, dist=WELD_M)

    def counts():
        copy = bm.copy()
        bmesh.ops.remove_doubles(copy, verts=copy.verts[:], dist=WELD_M)
        out = (sum(1 for e in copy.edges if e.is_boundary), sum(1 for e in copy.edges if len(e.link_faces) > 2), len(copy.faces))
        copy.free()
        return out

    before = counts()
    uvl = bm.loops.layers.uv.active
    tag = bm.faces.layers.int.new("vgac_eye")
    report = {"ok": False, "eyes": [], "open_edges_before": before[0], "faces_before": before[2]}

    def face_of(verts, uvs, kind, material=0):
        try:
            f = bm.faces.new(verts)
        except ValueError:
            return None
        f[tag] = kind
        f.smooth = True
        f.material_index = material
        for loop, uv in zip(f.loops, uvs):
            loop[uvl].uv = uv
        return f

    for eye in job["eyes"]:
        poly = [tuple(p) for p in eye["outline"]]
        cu, cv = eye["centre"]
        lid = eye["lid"]
        lo_u, hi_u = min(p[0] for p in poly), max(p[0] for p in poly)
        lo_v, hi_v = min(p[1] for p in poly), max(p[1] for p in poly)
        # 1. split the faces the outline crosses, exactly along it, and drop what is inside
        todo = []
        for f in bm.faces:
            if f[tag] != SKIN:
                continue
            p = [uvw(v.co) for v in f.verts]
            if (max(q[0] for q in p) < lo_u or min(q[0] for q in p) > hi_u or max(q[1] for q in p) < lo_v
                    or min(q[1] for q in p) > hi_v or any(abs(q[2] - lid) > DEPTH_WINDOW_M for q in p)):
                continue
            todo.append(f)
        made = {}

        def vert_at(co):
            key = tuple(round(c, 6) for c in co)
            if key not in made:
                made[key] = bm.verts.new(co)
            return made[key]

        for f in todo:
            for v in f.verts:
                made.setdefault(tuple(round(c, 6) for c in v.co), v)
        pieces, gone = [], []
        for f in todo:
            corners = list(f.verts)
            uvs = [loop[uvl].uv.copy() for loop in f.loops]
            flat = [uvw(v.co)[:2] for v in corners]
            area = (flat[1][0] - flat[0][0]) * (flat[2][1] - flat[0][1]) - (flat[1][1] - flat[0][1]) * (flat[2][0] - flat[0][0])
            if abs(area) < 1e-12:
                if inside(poly, sum(q[0] for q in flat) / 3, sum(q[1] for q in flat) / 3):
                    gone.append(f)
                continue
            points = [Vector(q) for q in flat] + [Vector(q) for q in poly]
            edges = [(3 + i, 3 + (i + 1) % len(poly)) for i in range(len(poly))]
            order = [0, 1, 2] if area > 0 else [0, 2, 1]
            pts2, _e, tris, *_rest = delaunay_2d_cdt(points, edges, [order], 1, 1e-8)
            keep = []
            for t in tris:
                mid = (sum(pts2[i][0] for i in t) / 3, sum(pts2[i][1] for i in t) / 3)
                if not inside(poly, *mid):
                    keep.append(t)
            if len(keep) == 1 and len(tris) == 1:
                continue  # the outline does not touch this face
            gone.append(f)
            for t in keep:
                t = list(t) if area > 0 else list(reversed(t))
                new_v, new_uv = [], []
                for i in t:
                    w = barycentric((pts2[i][0], pts2[i][1]), *flat)
                    co = corners[0].co * w[0] + corners[1].co * w[1] + corners[2].co * w[2]
                    new_v.append(co)
                    new_uv.append(uvs[0] * w[0] + uvs[1] * w[1] + uvs[2] * w[2])
                pieces.append((new_v, new_uv, f.material_index))
        new_verts = [([vert_at(co) for co in cos], uvs_, mat) for cos, uvs_, mat in pieces]
        bmesh.ops.delete(bm, geom=gone, context="FACES_ONLY")
        for verts, uvs_, mat in new_verts:
            if len(set(verts)) == 3:
                face_of(verts, uvs_, SKIN, mat)
        loose = [v for v in bm.verts if v.is_valid and not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        touched = [v for v in bm.verts if (MW @ v.co - (T + R * cu + U * cv + F * lid)).length < 0.05]
        bmesh.ops.remove_doubles(bm, verts=touched, dist=0.000002)

        # 2. where the eye sits: the depth of the lid edge carried across the opening, with a bulge in the middle
        edge_depth = [(u, v, w) for u, v, w in ((p[0], p[1], skin_depth(p[0], p[1], lid)) for p in resample(poly, 64))
                      if w is not None and abs(w - lid) <= DEPTH_WINDOW_M]
        if len(edge_depth) < 32:
            report["reason"] = "no_surface"
            break
        centre = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        within = [(centre[0] + (u - centre[0]) * k, centre[1] + (v - centre[1]) * k)
                  for k in INNER_SHARES for u, v in resample(poly, 16)]
        followed = edge_depth + [(u, v, w) for u, v, w in ((u, v, skin_depth(u, v, lid)) for u, v in within)
                                 if w is not None and abs(w - lid) <= DEPTH_WINDOW_M]
        ball_u, ball_v, ball_w, radius = fit_ball(followed, (hi_u + lo_u) / 2, (hi_v + lo_v) / 2, eye["width"])

        def eye_depth(u, v):
            """The ball, never far in front of the surface it replaces, tucked behind the skin on the lid line and past
            it."""
            depth = ball_w - dome(u, v, ball_u, ball_v, radius) + BEHIND_EDGE_M
            on_line = distance_to(poly, u, v) < 0.0001
            skin = skin_depth(u, v, lid)
            if skin is not None and abs(skin - lid) <= DEPTH_WINDOW_M:
                if on_line:
                    depth = max(depth, skin + AT_EDGE_M)
                elif inside(poly, u, v):
                    depth = max(depth, skin - FORWARD_M)
                else:
                    depth = max(depth, skin + CLEAR_M)
            return depth

        # 3. the socket: the cut edge turned inward, closed behind
        rim = [e for e in bm.edges if e.is_boundary and len(e.link_faces) == 1
               and all(distance_to(poly, *uvw(v.co)[:2]) < 0.0001 and abs(uvw(v.co)[2] - lid) < DEPTH_WINDOW_M for v in e.verts)]
        if len(rim) < 6:
            report["reason"] = "no_cut"
            break
        u0, v0, side = eye["rim_rect"]
        inner, bottoms = {}, []

        def below(v):
            if v not in inner:
                u, vv, w = uvw(v.co)
                deep = max(w + 0.0005, eye_depth(u, vv) + RIM_BEHIND_M)
                inner[v] = bm.verts.new(local(u, vv, deep))
                bottoms.append(deep)
            return inner[v]

        def swatch(v, down):
            u, vv = uvw(v.co)[:2]
            around = (math.atan2(vv - cv, u - cu) / (2 * math.pi)) % 1.0
            return (u0 + (0.1 + 0.8 * around) * side, v0 + (0.15 if down else 0.85) * side)

        lower_edges = []
        for e in rim:
            loop = e.link_loops[0]
            a, b = loop.vert, loop.link_loop_next.vert
            a2, b2 = below(a), below(b)
            face_of([b, a, a2], [swatch(b, False), swatch(a, False), swatch(a, True)], RIM)
            face_of([b, a2, b2], [swatch(b, False), swatch(a, True), swatch(b, True)], RIM)
            lower_edges.append((a2, b2, swatch(a, True), swatch(b, True)))
        floor = bm.verts.new(local(cu, cv, max(bottoms) + 0.003))
        for a2, b2, ua, ub in lower_edges:
            face_of([b2, a2, floor], [ub, ua, (u0 + 0.5 * side, v0 + 0.05 * side)], RIM)

        # 4. the eye: a front the shape of the opening (a little wider), closed behind
        e0, f0, eside = eye["eye_rect"]
        half = eye["half"]
        mid = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        line = resample(poly, SEGMENTS)  # on the lid line
        edge = []  # and past it, under the lids
        for u, v in line:
            d = math.hypot(u - mid[0], v - mid[1])
            k = (d + UNDER_LID_M) / max(d, 1e-9)
            edge.append((mid[0] + (u - mid[0]) * k, mid[1] + (v - mid[1]) * k))
        spots = [[(mid[0] + (q[0] - mid[0]) * i / RINGS, mid[1] + (q[1] - mid[1]) * i / RINGS) for q in line]
                 for i in range(1, RINGS + 1)] + [edge]

        def eye_uv(u, v):
            return (e0 + min(max((u - cu) / (2 * half) + 0.5, 0.0), 1.0) * eside,
                    f0 + min(max((v - cv) / (2 * half) + 0.5, 0.0), 1.0) * eside)

        def put(u, v, w):
            return bm.verts.new(local(u, v, w)), eye_uv(u, v)

        mid_depth, depths = smoothed(eye_depth(*mid), [[eye_depth(u, v) for u, v in row] for row in spots[:RINGS]])
        depths.append([eye_depth(u, v) for u, v in spots[-1]])
        front = put(mid[0], mid[1], mid_depth)
        rows = [[put(u, v, w) for (u, v), w in zip(row, ws)] for row, ws in zip(spots, depths)]
        back = put(mid[0], mid[1], max(uvw(v.co)[2] for v, _ in rows[-1]) + 0.001)
        # the winding that faces the camera for an outline given in either direction
        turn = sum(edge[i][0] * edge[(i + 1) % SEGMENTS][1] - edge[(i + 1) % SEGMENTS][0] * edge[i][1] for i in range(SEGMENTS))

        def tri(a, b, c):
            if turn < 0:
                b, c = c, b
            face_of([a[0], b[0], c[0]], [a[1], b[1], c[1]], EYE)

        for j in range(SEGMENTS):
            k = (j + 1) % SEGMENTS
            tri(front, rows[0][j], rows[0][k])
            for i in range(len(rows) - 1):
                tri(rows[i][j], rows[i + 1][j], rows[i + 1][k])
                tri(rows[i][j], rows[i + 1][k], rows[i][k])
            tri(back, rows[-1][k], rows[-1][j])
        report["eyes"].append({"rim_edges": len(rim), "radius_mm": round(radius * 1000, 2),
                               "sphere": [ball_u, ball_v, ball_w + BEHIND_EDGE_M]})

    after = counts()
    report.update(open_edges_after=after[0], over_shared_after=after[1], faces_after=after[2],
                  triangles_added=after[2] - before[2])
    good = len(report["eyes"]) == len(job["eyes"]) and after[0] <= before[0] and after[1] <= before[1]
    if not good:
        report.setdefault("reason", "not_closed")
    else:
        bm.faces.ensure_lookup_table()
        kinds = [f[tag] for f in bm.faces]
        bm.faces.layers.int.remove(tag)
        bm.normal_update()
        bm.to_mesh(body.data)
        me = body.data
        me.update()
        # the skin keeps the shading it had; the socket and the eyes take their own
        normals = [None] * len(me.loops)
        for poly_ in me.polygons:
            kind = kinds[poly_.index]
            for li in poly_.loop_indices:
                co = me.vertices[me.loops[li].vertex_index].co
                n = poly_.normal.copy()
                if kind == SKIN:
                    hit, _n, idx, dist = old_tree.find_nearest(co)
                    if dist is not None and dist < 0.0004:
                        a, b, c = (old_v[i] for i in old_tri[idx])
                        na, nb, nc = (old_n[i] for i in old_loops[idx])
                        n = barycentric_transform(hit, a, b, c, na, nb, nc).normalized()
                elif kind == EYE and poly_.normal.dot(forward) < -0.2:
                    u, v, w = uvw(co)
                    cu, cv, cw = min((s["sphere"] for s in report["eyes"]), key=lambda s: math.hypot(s[0] - u, s[1] - v))
                    d = R * (u - cu) + U * (v - cv) + F * (w - cw)
                    n = (MI.to_3x3() @ d).normalized()
                normals[li] = n
        me.normals_split_custom_set(normals)
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=True)
        report["ok"] = True
    bm.free()
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    print("EYES_OK" if report["ok"] else "EYES_SKIPPED", report.get("reason", ""), report.get("triangles_added"))


if __name__ == "__main__":
    main()
