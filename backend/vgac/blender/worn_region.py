"""Blender headless: a stiff part moves rigidly with one spine bone (WP-69).

Run: blender -b --python worn_region.py -- <rig.glb> <out.glb> --report <json> --points <json> [--seam 8]
     [--radius 0.25]

--points is the list of stiff parts placed on this rig version: [{"name", "location": [x, y, z]}], the location in the
rig GLB's own glTF frame (Y up, the character facing +Z), as declared_parts.py stores it.

For each point the region is grown from the mesh vertex nearest the point: the vertices connected to it along mesh
edges, within RADIUS metres of the point. The regions of all points are joined. The region's weights become one
spine bone: the chain bone (Spine, Spine1, Spine2, Chest) whose head is nearest the region's centre. Seam: the next
SEAM rings blend toward that bone (ring k takes (seam + 1 - k) / (seam + 1)), so the cut is not torn. Four
influences, normalised. Nothing else changes.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

MAX_INFLUENCES = 4
RADIUS = 0.8  # safety net: a region never reaches further than this from its point, in metres
SEED_SIZE = 4000  # safety net: a region never has more vertices than this
MIN_PIECE_TIDY = 12  # tidy: pieces smaller than this are dropped
HOLE_ROUNDS = 3  # rounds of hole filling after the growth (see fill_holes)
CREASE_DOT = 0.87  # seam duplicates join only when their normals agree within about 30 degrees
SECTOR_COS = 0.87  # a valley splits a vertex into sectors only when its two faces turn by about 30 degrees or more (measured in bakeoff/wp69/ridgefix)
SEAM = 8  # rings of the seam (measured on The Heir: no tear, body no worse)
SPINE_WORDS = ("spine", "chest")


def gltf_to_blender(p):
    """glTF (Y up, facing +Z) to Blender's Z-up frame after the glTF import (Blender Y = -glTF Z)."""
    x, y, z = p
    return np.array([x, -z, y], dtype=float)


def _adjacency(V, edges):
    adj = [[] for _ in range(V)]
    for a, b in np.asarray(edges, dtype=int).reshape(-1, 2):
        adj[a].append(b)
        adj[b].append(a)
    return adj


def _sectors(faces, edge_faces, concave, sharp):
    """Split every vertex into sectors: the faces round it that connect through edges that are not valleys. A growth
    that enters a vertex in one sector cannot leave it into another sector across a valley, so a shared corner vertex
    does not carry the region over a valley (WP-69 ridge fix). Returns (node_of, node_vertex, adjacency), where
    node_of[(v, f)] is the node id of vertex v in face f, node_vertex[n] the vertex, adjacency[n] the nodes it joins."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for (p, q), fs in edge_faces.items():
        if (p, q) in sharp:
            continue
        f0 = fs[0]
        for f in fs[1:]:
            union((p, f0), (p, f))
            union((q, f0), (q, f))
    node_id, node_vertex, vertex_nodes = {}, [], {}
    node_of = {}
    for f, tri in enumerate(faces):
        for v in tri:
            r = find((int(v), f))
            if r not in node_id:
                node_id[r] = len(node_vertex)
                node_vertex.append(int(v))
            n = node_id[r]
            node_of[(int(v), f)] = n
            nodes = vertex_nodes.setdefault(int(v), [])
            if n not in nodes:
                nodes.append(n)
    adjacency = [[] for _ in node_vertex]
    for (p, q), fs in edge_faces.items():
        if (p, q) in concave:
            continue
        f0 = fs[0]
        a, b = node_of[(p, f0)], node_of[(q, f0)]
        adjacency[a].append(b)
        adjacency[b].append(a)
    return node_of, node_vertex, vertex_nodes, adjacency


def grow_region(co, faces, point, radius=RADIUS, limit=SEED_SIZE, allowed=None):
    """The region grown from the vertex nearest the point (WP-69 stage 3). Growth crosses mesh edges and seam
    duplicates (same position, normals within about 30 degrees). It stops at a valley: a concave edge, where the next
    face lies in front of this face's plane (where a backpack meets the back, or a box meets the floor). Convex ridges,
    such as a box's own edges, are crossed. A sharp valley also stops growth through a vertex it shares (each vertex is
    split into sectors, see _sectors). Safety nets: radius from the point and a vertex limit. faces: (F, 3) triangle
    vertex indices. Returns a bool mask. The Heir's pack calibration (a click reaches 220 of its 227 vertices) was made
    with the old sign and is re-measured in bakeoff/wp69/ridgefix."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    V = len(co)
    mask = np.zeros(V, dtype=bool)
    faces = np.asarray(faces, dtype=int).reshape(-1, 3)
    if V == 0 or len(faces) == 0:
        return mask
    dist = np.linalg.norm(co - np.asarray(point, dtype=float), axis=1)
    start = int(np.argmin(dist))
    if dist[start] > radius:
        return mask
    fc = co[faces].mean(axis=1)
    fn = np.cross(co[faces[:, 1]] - co[faces[:, 0]], co[faces[:, 2]] - co[faces[:, 0]])
    fn = fn / np.maximum(np.linalg.norm(fn, axis=1), 1e-12)[:, None]
    vn = np.zeros_like(co)
    for k in range(3):
        np.add.at(vn, faces[:, k], fn)
    vn = vn / np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    edge_faces = {}
    for fi, (a0, b0, c0) in enumerate(faces):
        for p_, q_ in ((a0, b0), (b0, c0), (c0, a0)):
            edge_faces.setdefault((min(p_, q_), max(p_, q_)), []).append(fi)
    concave, sharp = set(), set()
    for (p_, q_), fs in edge_faces.items():
        if len(fs) == 2:
            f1, f2 = fs
            d12 = np.dot(fn[f1], fc[f2] - fc[f1])
            d21 = np.dot(fn[f2], fc[f1] - fc[f2])
            if d12 > 1e-6 or d21 > 1e-6:
                concave.add((p_, q_))
                if np.dot(fn[f1], fn[f2]) < SECTOR_COS:
                    sharp.add((p_, q_))
    node_of, node_vertex, vertex_nodes, adj = _sectors(faces, edge_faces, concave, sharp)
    buckets = {}
    for i, p_ in enumerate(np.round(co, 5)):
        buckets.setdefault(tuple(p_), []).append(i)
    for group in buckets.values():
        for a0 in group:
            for b0 in group:
                if a0 < b0 and np.dot(vn[a0], vn[b0]) > CREASE_DOT:
                    for na in vertex_nodes.get(a0, []):
                        for nb in vertex_nodes.get(b0, []):
                            adj[na].append(nb)
                            adj[nb].append(na)
    if allowed is not None and not allowed[start]:
        return mask
    visited = np.zeros(len(node_vertex), dtype=bool)
    mask[start] = True
    stack = []
    for n in vertex_nodes.get(start, []):
        visited[n] = True
        stack.append(n)
    count = 1
    while stack and count < limit:
        u = stack.pop()
        for m in adj[u]:
            if count >= limit:
                break
            if visited[m]:
                continue
            w = node_vertex[m]
            if dist[w] > radius or (allowed is not None and not allowed[w]):
                continue
            visited[m] = True
            if not mask[w]:
                mask[w] = True
                count += 1
            stack.append(m)
    return mask


def tidy_region(co, faces, allowed, min_piece=MIN_PIECE_TIDY):
    """Keep the pieces of `allowed` that grow together under the growth rule (WP-69 tidy): each allowed vertex is a
    seed in turn, each piece is grown within `allowed`, and pieces under min_piece vertices are dropped. A bool mask."""
    allowed = np.asarray(allowed, dtype=bool)
    out = np.zeros(len(allowed), dtype=bool)
    done = np.zeros(len(allowed), dtype=bool)
    for seed in np.where(allowed)[0]:
        if done[seed]:
            continue
        piece = grow_region(co, faces, co[seed], radius=np.inf, limit=SEED_SIZE, allowed=allowed)
        done |= piece
        if piece.sum() >= min_piece:
            out |= piece
    return out


def fill_holes(region, edges, rounds=HOLE_ROUNDS):
    """Take in any vertex outside the region whose edge neighbours are all inside it (a hole in the piece), for a few
    rounds. A flat hole left out of the region keeps the hips' weights and tears away from the pack (WP-69)."""
    region = np.array(region, dtype=bool, copy=True)
    if not np.any(region) or not len(edges):
        return region, 0
    V = len(region)
    adj = _adjacency(V, edges)
    added = 0
    for _ in range(max(0, int(rounds))):
        grow = [v for v in range(V) if not region[v] and adj[v] and all(region[w] for w in adj[v])]
        if not grow:
            break
        region[grow] = True
        added += len(grow)
    return region, added


def spine_bone(names, heads, centre):
    """The chain bone (spine or chest) whose head is nearest the region's centre; None when there is none."""
    best, best_d = None, None
    for n, h in zip(names, heads):
        low = str(n).replace("mixamorig:", "").lower()
        if not any(w in low for w in SPINE_WORDS):
            continue
        d = float(np.linalg.norm(np.asarray(h, dtype=float) - np.asarray(centre, dtype=float)))
        if best_d is None or d < best_d:
            best, best_d = n, d
    return best


def ring_blend(edges, region, seam):
    """Per vertex blend toward the bone: 1.0 in the region, (seam + 1 - k) / (seam + 1) in ring k, 0 elsewhere."""
    V = len(region)
    t = np.where(region, 1.0, 0.0)
    if seam <= 0 or not len(edges):
        return t
    adj = _adjacency(V, edges)
    frontier = np.where(region)[0].tolist()
    seen = np.array(region, dtype=bool, copy=True)
    for k in range(1, seam + 1):
        nxt = []
        for v in frontier:
            for w in adj[v]:
                if not seen[w]:
                    seen[w] = True
                    nxt.append(w)
        for w in nxt:
            t[w] = (seam + 1 - k) / (seam + 1.0)
        frontier = nxt
    return t


def cap_and_normalise(W, rows):
    W = np.array(W, dtype=float, copy=True)
    if not np.any(rows):
        return W
    sub = W[rows]
    if sub.shape[1] > MAX_INFLUENCES:
        cut = -np.sort(-sub, axis=1)[:, MAX_INFLUENCES - 1:MAX_INFLUENCES]
        sub = np.where(sub >= cut, sub, 0.0)
    s = sub.sum(axis=1)
    sub = np.where(s[:, None] > 1e-12, sub / np.maximum(s[:, None], 1e-12), sub)
    W[rows] = sub
    return W


def run_numpy(W, edges, co, names, heads, region, seam=SEAM):
    """The pass on arrays. region: bool mask over vertices. names and heads: one bone per column of W (heads are the
    bone head positions). Returns (W, report). W unchanged when there is no region or no spine bone."""
    W0 = np.array(W, dtype=float, copy=True)
    region = np.asarray(region, dtype=bool)
    region, filled = fill_holes(region, edges)
    if not np.any(region):
        return W0, {"ok": False, "reason": "the stiff part region is empty"}
    centre = np.asarray(co, dtype=float)[region].mean(axis=0)
    bone = spine_bone(names, heads, centre)
    if bone is None:
        return W0, {"ok": False, "reason": "no spine or chest bone on this rig"}
    col = list(names).index(bone)
    t = ring_blend(edges, region, seam)
    target = np.zeros_like(W0)
    target[:, col] = 1.0
    blended = (1.0 - t[:, None]) * W0 + t[:, None] * target
    rows = t > 1e-9
    out = cap_and_normalise(blended, rows)
    err = float(np.abs(out[rows].sum(axis=1) - 1.0).max()) if np.any(rows) else 0.0
    report = {"ok": True, "bone": bone, "region_vertices": int(region.sum()), "holes_filled": int(filled),
              "seam_vertices": int((rows & ~region).sum()), "seam_rings": int(seam), "row_sum_error": err}
    return out, report


# ---- Blender side -------------------------------------------------------------------------------------------------


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    points_path = _common.opt("--points", None)
    seam = int(_common.opt("--seam", str(SEAM)))
    radius = float(_common.opt("--radius", str(RADIUS)))
    points = []
    if points_path:
        with open(points_path, encoding="utf-8") as fh:
            data = json.load(fh)
        points = data.get("parts", []) if isinstance(data, dict) else data
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "points": [], "meshes": []}
    if not meshes or arm is None:
        report["reason"] = "no armature or no skinned mesh"
    elif not points:
        report["reason"] = "no stiff parts placed"
    else:
        bone_heads = {b.name: list(arm.matrix_world @ b.head_local) for b in arm.data.bones}
        for o in meshes:
            vg_names = [g.name for g in o.vertex_groups]
            V = len(o.data.vertices)
            W = np.zeros((V, len(vg_names)))
            for v in o.data.vertices:
                for g in v.groups:
                    W[v.index, g.group] = g.weight
            co = np.array([tuple(o.matrix_world @ v.co) for v in o.data.vertices], dtype=float).reshape(-1, 3)
            edges = np.array([[e.vertices[0], e.vertices[1]] for e in o.data.edges], dtype=int).reshape(-1, 2)
            o.data.calc_loop_triangles()
            faces = np.array([tuple(t.vertices) for t in o.data.loop_triangles], dtype=int).reshape(-1, 3)
            region = np.zeros(V, dtype=bool)
            for p in points:
                for loc in p.get("locations") or []:
                    m = grow_region(co, faces, gltf_to_blender(loc), radius)
                    report["points"].append({"name": p.get("name"), "vertices": int(m.sum())})
                    region |= m
            heads = [bone_heads.get(n, [0.0, 0.0, 0.0]) for n in vg_names]
            W2, rep = run_numpy(W, edges, co, vg_names, heads, region, seam=seam)
            rep["mesh"] = o.name
            report["meshes"].append(rep)
            if rep.get("ok"):
                for g in o.vertex_groups:
                    g.remove([v.index for v in o.data.vertices])
                for gi, g in enumerate(o.vertex_groups):
                    for vi in np.where(W2[:, gi] > 1e-6)[0]:
                        g.add([int(vi)], float(W2[vi, gi]), "REPLACE")
        report["ok"] = any(m.get("ok") for m in report["meshes"])
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"]:
        bpy.ops.object.select_all(action="DESELECT")
        keep = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False,
                                  export_animations=True)
    print("WORN_REGION_OK" if report["ok"] else "WORN_REGION_SKIPPED", report.get("reason", ""))


if __name__ == "__main__":  # pragma: no cover
    main()
