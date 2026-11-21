"""Blender headless: measure how much the skin stretches on an animated, skinned GLB (WP-18).

Run: blender -b --python skin_stretch.py -- <anim.glb> <out.json> [--clips walk,run] [--samples 16]

For each NLA clip (walk and run by default) the script poses the skinned meshes at evenly spaced frames of the
clip, and compares every mesh edge with its rest length (the GLB with no clip playing). An edge counts as stretched
in a clip when its longest length over the sampled frames is more than THRESHOLD (1.3) times its rest length.
Edges shorter than MIN_REST_M (1 mm) are skipped. Each clip also carries "regions_floor_5mm", the same figures with
edges under FLOOR_M (5 mm) at rest skipped too, so sliver-length edges cannot dominate the max. Each edge belongs to one body region, from the bones that
dominate its two ends (see region_of_bone and edge_region):
  hips  the hips bone (the pelvis and the hip joints where they meet it)
  legs  thigh, calf, foot and toe bones
  arms  shoulder-to-hand bones
  torso spine, neck, head
  other anything else

Output JSON: {"threshold", "rest_edges", "regions": {name: edges}, "clips": {clip: {"frames", "regions": {name:
{"edges", "over_share", "p95_ratio", "max_ratio"}}, "all": {...}}}}. over_share is the share of the region's edges
over THRESHOLD in that clip; p95_ratio is the 95th percentile of the per-edge longest ratio.

Everything above main() is plain numpy plus string rules so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

THRESHOLD = 1.3
MIN_REST_M = 0.001  # edges shorter than this at rest are skipped in every figure
FLOOR_M = 0.005  # the second set of figures also skips edges shorter than this at rest (sliver-length edges)
REGION_PRIORITY = ("hips", "legs", "arms", "torso", "other")


def region_of_bone(name):
    """One of REGION_PRIORITY for a bone name (Mixamo 'mixamorig:' prefixes and renamed bones both work)."""
    n = str(name or "").replace("mixamorig:", "").lower()
    if "hips" in n or "pelvis" in n or "root" == n:
        return "hips"
    if any(k in n for k in ("upleg", "leg", "foot", "toe", "thigh", "calf", "ankle")):
        return "legs"
    if any(k in n for k in ("arm", "hand", "shoulder", "finger", "thumb", "clavicle")):
        return "arms"
    if any(k in n for k in ("spine", "neck", "head", "chest")):
        return "torso"
    return "other"


def edge_region(a, b):
    """The region an edge belongs to from its two end regions: the one that comes first in REGION_PRIORITY."""
    return a if REGION_PRIORITY.index(a) <= REGION_PRIORITY.index(b) else b


def edge_stats(rest, posed_frames, edges, regions, threshold=THRESHOLD, min_rest=MIN_REST_M):
    """Per-region stretch figures.

    rest: (V, 3) rest positions. posed_frames: list of (V, 3) arrays for the sampled frames of one clip.
    edges: (E, 2) vertex index pairs. regions: list of E region names. Returns {region: {...}} for each region
    present plus "all"; with no measurable edges, every figure is None."""
    rest = np.asarray(rest, dtype=float)
    edges = np.asarray(edges, dtype=int).reshape(-1, 2)
    if len(edges) == 0 or not posed_frames:
        return {"all": _empty()}
    rest_len = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
    keep = rest_len > min_rest
    worst = np.zeros(len(edges))
    for frame in posed_frames:
        fr = np.asarray(frame, dtype=float)
        posed_len = np.linalg.norm(fr[edges[:, 0]] - fr[edges[:, 1]], axis=1)
        ratio = np.where(keep, posed_len / np.maximum(rest_len, 1e-12), 0.0)
        worst = np.maximum(worst, ratio)
    regions = np.asarray(regions, dtype=object)
    out = {"all": _figures(worst[keep], threshold)}
    for name in REGION_PRIORITY:
        sel = keep & (regions == name)
        if np.any(sel):
            out[name] = _figures(worst[sel], threshold)
    return out


def pose_spread(frames):
    """Largest distance any vertex moves between the first sampled frame and any other (metres). 0.0 means every frame is
    the same pose, which is a measure reading a frozen pose and not the clip (Blender 5.2 does that after an early
    set_tracks(None); see main)."""
    if len(frames) < 2:
        return 0.0
    first = np.asarray(frames[0], dtype=float)
    return float(max(np.linalg.norm(np.asarray(f, dtype=float) - first, axis=1).max() for f in frames[1:]))


def _figures(ratios, threshold):
    if len(ratios) == 0:
        return _empty()
    return {"edges": int(len(ratios)), "over_share": round(float(np.mean(ratios > threshold)), 4),
            "p95_ratio": round(float(np.percentile(ratios, 95)), 3), "max_ratio": round(float(np.max(ratios)), 3)}


def _empty():
    return {"edges": 0, "over_share": None, "p95_ratio": None, "max_ratio": None}


def dominant_regions(bone_names_per_vertex):
    """Region per vertex from its dominant bone name (None for an unweighted vertex)."""
    return [region_of_bone(b) if b else "other" for b in bone_names_per_vertex]


# ---- Blender side -------------------------------------------------------------------------------------------------


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, out_path = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    clips = [c.strip() for c in _common.opt("--clips", "walk,run").split(",") if c.strip()]
    samples = int(_common.opt("--samples", "16"))
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    ad = arm.animation_data if arm else None
    if ad:
        ad.action = None
    tracks = list(ad.nla_tracks) if ad else []
    by_name = {t.name: t for t in tracks}

    def set_tracks(active):
        for t in tracks:
            t.mute = t is not active
        # A mute change alone does not re-evaluate the pose in Blender 5.2 (the troll rig returned the rest pose for
        # every frame after unmuting walk); a view-layer update does.
        bpy.context.view_layer.update()

    # Per mesh: edges, region per vertex, rest coordinates (the mesh data as stored, below). Do not call set_tracks(None)
    # here: in Blender 5.2.3 an all-muted evaluation before the first clip leaves the pose frozen for every later frame,
    # so every clip measured one static pose (WP-79 found it; pose_spread_m in the output says when it happens).
    parts = []
    offset = 0
    rest_rows, edge_rows, region_rows = [], [], []
    for o in meshes:
        names = [g.name for g in o.vertex_groups]
        dom = []
        for v in o.data.vertices:
            best = max(v.groups, key=lambda g: g.weight, default=None) if v.groups else None
            dom.append(names[best.group] if best is not None and best.weight > 0 else None)
        regs = dominant_regions(dom)
        ed = np.array([[e.vertices[0], e.vertices[1]] for e in o.data.edges], dtype=int).reshape(-1, 2)
        ereg = [edge_region(regs[a], regs[b]) for a, b in ed]
        parts.append(o)
        edge_rows.append(ed + offset)
        region_rows.append(ereg)
        offset += len(o.data.vertices)
    edges = np.vstack(edge_rows) if edge_rows else np.zeros((0, 2), dtype=int)
    regions = [r for rr in region_rows for r in rr]

    def coords():
        rows = []
        dgraph = bpy.context.evaluated_depsgraph_get()
        for o in parts:
            ev = o.evaluated_get(dgraph)
            me = ev.to_mesh()
            rows.append(np.array([tuple(v.co) for v in me.vertices], dtype=float).reshape(-1, 3))
            ev.to_mesh_clear()
        return np.vstack(rows) if rows else np.zeros((0, 3))

    # Rest = the mesh data as stored (the bind pose the GLB ships with). Evaluating with every clip muted gave a pose
    # the clips never left in Blender 5.2, so the rest is read from the data instead.
    rest = np.vstack([np.array([tuple(v.co) for v in o.data.vertices], dtype=float).reshape(-1, 3) for o in parts])         if parts else np.zeros((0, 3))
    result = {"threshold": THRESHOLD, "rest_edges": int(len(edges)),
              "regions": {r: regions.count(r) for r in REGION_PRIORITY}, "clips": {}}
    for name in clips:
        track = by_name.get(name)
        if track is None or not track.strips:
            result["clips"][name] = {"missing": True}
            continue
        set_tracks(track)
        strip = track.strips[0]
        f0, f1 = int(round(strip.frame_start)), int(round(strip.frame_end))
        frames = []
        for i in range(samples):
            f = int(round(f0 + (f1 - f0) * i / max(1, samples)))
            scene.frame_set(f)
            frames.append(coords())
        stats = edge_stats(rest, frames, edges, regions)
        floored = edge_stats(rest, frames, edges, regions, min_rest=FLOOR_M)
        result["clips"][name] = {"frames": len(frames), "pose_spread_m": round(pose_spread(frames), 4), "regions": stats,
                                 "regions_floor_5mm": floored}
    set_tracks(None)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print("SKIN_STRETCH_OK", result["rest_edges"], list(result["clips"]))


if __name__ == "__main__":  # pragma: no cover
    main()
