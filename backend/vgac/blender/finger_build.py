"""Blender headless: build the finger chains SkinToken left out, from the hand's own geometry.

Run: blender -b --python finger_build.py -- <rig.glb> <out.glb> [--report <json>]      (build)
     blender -b --python finger_build.py -- <rig.glb> <out_prefix> --render [--fist]   (pictures of both hands)
The rig step runs it after rig_fix (stages/rig.py _fingers). Reads a rigged GLB whose bones carry the Mixamo names (rig_fix.py has run), and per hand:
- a hand that already has five fingers of three bones is left alone ("complete");
- a hand where the skeleton found at most one chain (a mitten: the rigger saw a lump) is refused ("lump"): no chain
  can be placed in a mitten, the fix is the model's hands;
- otherwise the missing chains are placed across the hand: from the fingertips found in the outline ("tips", four
  tips seen) or by proportion across the knuckle line ("proportion", fingers too bunched to tell apart). The skin
  of the hand's vertices is then shared among the palm and the finger bones by distance.
The frame, lump test, chain layout and weights are plain numpy so tests can run them without Blender.
"""
import json
import math
import os
import sys

import numpy as np

PREFIX = "mixamorig:"
FINGERS = ("Index", "Middle", "Ring", "Pinky")
ALL_FINGERS = ("Thumb",) + FINGERS
FORWARD = np.array([0.0, -1.0, 0.0])  # the model's front after the glTF importer's axis change
SLOT_SHARE = np.array([0.27, 0.27, 0.25, 0.21])  # width of each finger's lane across the knuckle line, thumb side first
BONE_SHARE = (0.45, 0.30, 0.25)  # proximal, middle, distal share of a finger's length
MCP_SHARE = 0.48  # knuckle position along the finger's reach from the wrist
FAR_ZONE = 0.55  # the part of the hand (from the wrist, share of its reach) that holds the fingers
PEAK_MIN = 0.03  # a fingertip stands out of the outline by at least this share of the hand's reach
BINS = 28


def _unit(v):
    return v / max(float(np.linalg.norm(v)), 1e-12)


def hand_frame(wrist, pts, thumb_head, front=FORWARD):
    """Frame of one hand from its vertices: f (wrist to fingertips), u (across the hand, thumb side to little-finger
    side), n (out of the palm, toward the model's front). None without a thumb to tell the sides."""
    if thumb_head is None:
        return None
    q = pts - wrist
    d = np.linalg.norm(q, axis=1)
    far = q[d >= np.quantile(d, 0.85)]
    f = _unit(far.mean(axis=0))
    perp = q - np.outer(q @ f, f)
    _, v = np.linalg.eigh(np.cov(perp.T))
    lat = _unit(v[:, 2] - f * (v[:, 2] @ f))
    s = float((np.asarray(thumb_head, float) - wrist) @ lat)
    u = -lat if s > 0 else lat
    n = _unit(np.cross(f, u))
    if n @ front < 0:
        n = -n
    return {"f": f, "u": u, "n": n, "wrist": np.asarray(wrist, float)}


def coords(frame, pts):
    q = np.atleast_2d(pts) - frame["wrist"]
    return np.stack([q @ frame["f"], q @ frame["u"], q @ frame["n"]], axis=1)


def outline_peaks(c, reach):
    """Fingertips in the hand's outline seen from the palm: high points of the far edge across the hand.
    -> [(u, a, prominence)] sorted by u, from the vertices in the finger zone."""
    zone = c[c[:, 0] >= FAR_ZONE * reach]
    if len(zone) < 10:
        return []
    lo, hi = np.quantile(zone[:, 1], 0.01), np.quantile(zone[:, 1], 0.99)
    edges = np.linspace(lo, hi, BINS + 1)
    prof = np.full(BINS, np.nan)
    for i in range(BINS):
        m = (zone[:, 1] >= edges[i]) & (zone[:, 1] <= edges[i + 1])
        if m.any():
            prof[i] = float(zone[m, 0].max())
    prof = np.where(np.isnan(prof), np.nanmin(prof), prof)
    sm = np.convolve(np.pad(prof, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    low = float(sm.min()) - reach  # outside the hand: a finger on the rim stands out of it
    sm = np.concatenate([[low], sm, [low]])
    peaks = []
    for i in range(1, BINS + 1):
        if sm[i] <= sm[i - 1] or sm[i] < sm[i + 1]:
            continue
        lmin = rmin = sm[i]
        j = i - 1
        while j >= 0 and sm[j] <= sm[i]:
            lmin, j = min(lmin, sm[j]), j - 1
        k = i + 1
        while k < BINS + 2 and sm[k] <= sm[i]:
            rmin, k = min(rmin, sm[k]), k + 1
        prom = sm[i] - max(lmin, rmin)
        if prom >= PEAK_MIN * reach:
            peaks.append(((edges[i - 1] + edges[i]) / 2, float(prof[i - 1]), float(prom)))
    return peaks


def plan_chains(c, peaks, reach):
    """Lane centres across the knuckle line and the path taken. c: hand vertices in frame coordinates.
    -> {"path": "tips"|"proportion", "centres": [4 u values, thumb side first], "lane": [4 widths], "peaks": n}"""
    zone = c[c[:, 0] >= FAR_ZONE * reach]
    lo, hi = float(np.quantile(zone[:, 1], 0.02)), float(np.quantile(zone[:, 1], 0.98))
    width = hi - lo
    lanes = [float(width * s) for s in SLOT_SHARE]
    pk = sorted(peaks, key=lambda p: p[0])
    if len(pk) >= 5:  # the thumb tip stands in the zone: drop the one on the thumb side
        pk = pk[1:]
    if len(pk) == 4:
        return {"path": "tips", "centres": [float(p[0]) for p in pk], "lane": lanes, "peaks": 4}
    prop = [lo + width * (float(SLOT_SHARE[:i].sum()) + float(SLOT_SHARE[i]) / 2) for i in range(4)]
    return {"path": "proportion", "centres": prop, "lane": lanes, "peaks": len(pk)}


def chain_points(c, centre, lane, reach):
    """The four joint points (knuckle, two joints, tip) of one finger in frame coordinates (a, u, n): tip at the lane's
    farthest reach, knuckle at MCP_SHARE of it, bones split BONE_SHARE; the finger lies at the middle thickness of the hand."""
    inl = c[np.abs(c[:, 1] - centre) <= lane / 2]
    zone = inl[inl[:, 0] >= FAR_ZONE * reach]
    if len(zone) == 0:
        zone = c[c[:, 0] >= FAR_ZONE * reach]
    tip_a = float(np.quantile(zone[:, 0], 0.98))
    sel = inl[(inl[:, 0] > 0.3 * tip_a) & (inl[:, 0] < 0.95 * tip_a)]
    n_mid = float(sel[:, 2].mean()) if len(sel) else float(c[:, 2].mean())
    a0 = MCP_SHARE * tip_a
    cum = np.cumsum((0.0,) + BONE_SHARE)
    return [np.array([a0 + (tip_a - a0) * k, centre, n_mid]) for k in cum]


def point_segment(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return np.linalg.norm(p - (a + np.outer(t, ab)), axis=1)


def share_weights(pts, segments, sigma):
    """Soft share of each vertex among bone segments by distance: exp(-(d/sigma)^2), normalised.
    segments {name: (head, tail)}. -> (names, array (n, k))."""
    names = list(segments)
    d = np.stack([point_segment(pts, np.asarray(segments[k][0], float), np.asarray(segments[k][1], float)) for k in names], axis=1)
    w = np.exp(-(d / sigma) ** 2) + 1e-9
    return names, w / w.sum(axis=1, keepdims=True)


# --- Blender side ---------------------------------------------------------------------------------------------


def _chain_names(side):
    return {f: [f"{PREFIX}{side}Hand{f}{k}" for k in (1, 2, 3)] for f in ALL_FINGERS}


def _descendants(arm, name):
    out, stack = [], [arm.data.bones[name]]
    while stack:
        b = stack.pop()
        for ch in b.children:
            out.append(ch.name)
            stack.append(ch)
    return out


def hand_state(arm, mesh, side):
    """The hand's vertices (world) and their skin mass on the hand bone and everything below it."""
    hand = f"{PREFIX}{side}Hand"
    names = [hand] + _descendants(arm, hand)
    idx = {g.name: g.index for g in mesh.vertex_groups}
    gi = {idx[n] for n in names if n in idx}
    W = mesh.matrix_world
    pts, ids = [], []
    for v in mesh.data.vertices:
        if sum(g.weight for g in v.groups if g.group in gi) >= 0.5:
            pts.append(np.array(W @ v.co))
            ids.append(v.index)
    return {"hand": hand, "names": names, "pts": np.array(pts), "ids": ids}


def _common_opt(flag, default):
    argv = sys.argv[sys.argv.index("--") + 1:]
    return argv[argv.index(flag) + 1] if flag in argv else default


def build_side(arm, mesh, side, report):
    import bpy
    from mathutils import Vector

    AW = arm.matrix_world
    have = {f: [n for n in ch if n in arm.data.bones] for f, ch in _chain_names(side).items()}
    rep = report[side] = {"side": side, "had": {f: len(v) for f, v in have.items()}}
    if all(len(v) == 3 for v in have.values()):
        rep.update(path="complete", built=0)
        return
    st = hand_state(arm, mesh, side)
    pts = st["pts"]
    if len(pts) < 50:
        rep.update(path="refused", reason="no hand found on the skin")
        return
    wrist = np.array(AW @ arm.data.bones[st["hand"]].head_local)
    thumb = np.array(AW @ arm.data.bones[have["Thumb"][0]].head_local) if have["Thumb"] else None
    chains = sum(1 for v in have.values() if len(v) == 3)
    fr = hand_frame(wrist, pts, thumb)
    if chains < 2 or fr is None:
        # the rigger itself saw one lump (a single chain or none): nothing tells the fingers' sides or says they exist
        rep.update(path="lump", chains=chains, reason="the skeleton found at most one chain in this hand: the model's hand is a lump, so no finger chains were built")
        return
    c = coords(fr, pts)
    reach = float(c[:, 0].max())
    plan = plan_chains(c, outline_peaks(c, reach), reach)
    rep.update(path=plan["path"], peaks=plan["peaks"], reach_m=round(reach, 4))
    # a predicted finger chain stays on its nearest lane (renamed to that lane's finger); the other lanes are built
    lane_of = {}
    for f in FINGERS:
        if len(have[f]) == 3:
            head = np.array(AW @ arm.data.bones[have[f][0]].head_local)
            k = int(np.argmin([abs(float(coords(fr, head)[0, 1]) - cc) for cc in plan["centres"]]))
            lane_of.setdefault(k, f)
    renames = {}
    for k, f in lane_of.items():
        if f != FINGERS[k]:
            for i in (1, 2, 3):
                renames[f"{PREFIX}{side}Hand{f}{i}"] = f"{PREFIX}{side}Hand{FINGERS[k]}{i}"
    tmp = {o: f"__fb_{i}" for i, o in enumerate(renames)}
    group_at = {o: mesh.vertex_groups[o].index for o in renames if o in mesh.vertex_groups}
    for o, t in tmp.items():
        arm.data.bones[o].name = t
    for o, t in tmp.items():
        arm.data.bones[t].name = renames[o]
    # the vertex groups follow a bone rename only through an Armature modifier: set them by index so the skin keeps
    # following the chain whatever the scene's modifiers are
    for o, i in group_at.items():
        mesh.vertex_groups[i].name = tmp[o]
    for o, i in group_at.items():
        mesh.vertex_groups[i].name = renames[o]
    rep["kept"] = {FINGERS[k]: f for k, f in lane_of.items()}
    rep["renamed"] = {o.split(":")[1]: n.split(":")[1] for o, n in renames.items()}
    inv = AW.inverted()
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    hand_eb = eb[st["hand"]]
    built = []
    for k, f in enumerate(FINGERS):
        if k in lane_of:
            continue
        jp = chain_points(c, plan["centres"][k], plan["lane"][k], reach)
        world = [fr["wrist"] + fr["f"] * p[0] + fr["u"] * p[1] + fr["n"] * p[2] for p in jp]
        parent = hand_eb
        for i in range(3):
            b = eb.new(f"{PREFIX}{side}Hand{f}{i + 1}")
            b.head, b.tail, b.parent = inv @ Vector(world[i]), inv @ Vector(world[i + 1]), parent
            parent = b
            built.append(b.name)
    bpy.ops.object.mode_set(mode="OBJECT")
    rep["built"] = len(built)
    for n in built:
        if n not in mesh.vertex_groups:
            mesh.vertex_groups.new(name=n)
    # skin: the hand's vertices share their hand mass among the palm, the thumb and the finger bones by distance
    bones = arm.data.bones
    mcp = np.mean([coords(fr, np.array(AW @ bones[f"{PREFIX}{side}Hand{f}1"].head_local))[0] for f in FINGERS], axis=0)
    segs = {st["hand"]: (wrist, wrist + fr["f"] * mcp[0] + fr["u"] * mcp[1] + fr["n"] * mcp[2])}
    for f in ALL_FINGERS:
        for i in (1, 2, 3):
            nm = f"{PREFIX}{side}Hand{f}{i}"
            if nm in bones:
                segs[nm] = (np.array(AW @ bones[nm].head_local), np.array(AW @ bones[nm].tail_local))
    names, share = share_weights(pts, segs, float(_common_opt("--sigma", "1.0")) * float(np.mean(plan["lane"])))
    gidx = {g.name: g.index for g in mesh.vertex_groups}
    keep = {gidx[n] for n in set(st["names"]) | set(segs) if n in gidx}
    for row, vid in enumerate(st["ids"]):
        v = mesh.data.vertices[vid]
        mass = sum(g.weight for g in v.groups if g.group in keep)
        others = {g.group: g.weight for g in v.groups if g.group not in keep and g.weight > 0}
        for g in list(v.groups):
            if g.group in keep:
                mesh.vertex_groups[g.group].remove([vid])
        cand = {gidx[n]: float(share[row, j]) * mass for j, n in enumerate(names) if n in gidx}
        top = sorted({**others, **cand}.items(), key=lambda t: -t[1])[:4]
        tw = sum(w for _, w in top) or 1.0
        for gi, w in top:
            mesh.vertex_groups[gi].add([vid], w / tw, "REPLACE")
    rep["frame"] = {k: [round(float(x), 4) for x in fr[k]] for k in ("f", "u", "n")}


def _finger_bones(arm, side):
    return [n for n in (f"{PREFIX}{side}Hand{f}{i}" for f in ALL_FINGERS for i in (1, 2, 3)) if n in arm.pose.bones]


def best_clip_frame(arm, clip):
    """Frame of the clip where the two hands' finger bones are turned the most (a grip). -> (frame, score)."""
    import bpy

    if arm.animation_data:
        for tr in list(arm.animation_data.nla_tracks):
            arm.animation_data.nla_tracks.remove(tr)
    act = bpy.data.actions[clip]
    ad = arm.animation_data_create()
    ad.action = act
    if hasattr(ad, "action_slot") and getattr(act, "slots", None):
        ad.action_slot = act.slots[0]
    lo, hi = int(act.frame_range[0]), int(act.frame_range[1])
    best = (lo, -1.0)
    for fr in range(lo, hi + 1, 2):
        bpy.context.scene.frame_set(fr)
        sc = sum(arm.pose.bones[n].matrix_basis.to_quaternion().angle for side in ("Left", "Right") for n in _finger_bones(arm, side))
        if sc > best[1]:
            best = (fr, sc)
    return best


def render(arm, mesh, prefix, fist, mode=None):
    """Pictures of each hand: palm view and side view, open and (with --fist) closed."""
    import bpy
    from mathutils import Quaternion, Vector

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = 600
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.75, 0.75, 0.75)
    world = bpy.data.worlds.new("w")
    world.use_nodes = False
    world.color = (0.93, 0.93, 0.93)
    scene.world = world
    scene.display.shading.background_type = "WORLD"
    cam_d = bpy.data.cameras.new("c")
    cam_d.type = "ORTHO"
    cam = bpy.data.objects.new("c", cam_d)
    scene.collection.objects.link(cam)
    scene.camera = cam
    AW = arm.matrix_world
    bones = arm.data.bones
    clip_frame = 1
    if mode and mode.startswith("clip:"):
        clip_frame, score = best_clip_frame(arm, mode[5:])
        print("CLIP_FRAME", mode, clip_frame, round(score, 3))
        bpy.context.scene.frame_set(1)
    for side in ("Left", "Right"):
        st = hand_state(arm, mesh, side)
        wrist = np.array(AW @ bones[st["hand"]].head_local)
        th = [n for n in _chain_names(side)["Thumb"] if n in bones]
        fr = hand_frame(wrist, st["pts"], np.array(AW @ bones[th[0]].head_local) if th else None)
        if fr is None:
            continue
        reach = float(coords(fr, st["pts"])[:, 0].max())
        centre = wrist + fr["f"] * reach * 0.45
        for pose in (("point",) if mode == "point" else (mode,) if mode else ("open", "fist") if fist else ("open",)):
            if mode and mode.startswith("clip:"):
                bpy.context.scene.frame_set(clip_frame)
            else:
                for b in arm.pose.bones:
                    b.rotation_mode = "QUATERNION"
                    b.rotation_quaternion = (1, 0, 0, 0)
            if pose in ("fist", "point"):
                # curl about the across-hand axis u, fingertips toward the palm side (+n): rotating by +angle about u
                # turns f toward (u x f); pick the sign that points it to +n
                sgn = 1.0 if float(np.cross(fr["u"], fr["f"]) @ fr["n"]) > 0 else -1.0
                for f in ALL_FINGERS:
                    if pose == "point" and f == "Index":
                        continue
                    for i, ang in zip((1, 2, 3), (60, 70, 50) if f == "Thumb" else (80, 95, 65)):
                        nm = f"{PREFIX}{side}Hand{f}{i}"
                        if nm in arm.pose.bones:
                            ax = (AW.to_3x3() @ bones[nm].matrix_local.to_3x3()).inverted() @ Vector(fr["u"])
                            arm.pose.bones[nm].rotation_quaternion = Quaternion(ax.normalized(), sgn * math.radians(ang))
            bpy.context.view_layer.update()
            ctr, nv, uv = Vector(centre), Vector(fr["n"]), Vector(fr["u"])
            if mode and mode.startswith("clip:"):  # the hand has moved: carry its frame along with the hand bone
                hb = arm.pose.bones[st["hand"]]
                M = AW @ hb.matrix @ bones[st["hand"]].matrix_local.inverted() @ AW.inverted()
                ctr, nv, uv = M @ ctr, M.to_3x3() @ nv, M.to_3x3() @ uv
            for view, look in (("palm", -nv), ("side", -uv)):
                cam.location = Vector(ctr) - Vector(look) * 1.0
                cam.rotation_euler = Vector(look).to_track_quat("-Z", "Y").to_euler()
                cam_d.ortho_scale = reach * 2.2
                cam_d.clip_end = 5
                scene.render.filepath = f"{prefix}_{side}_{pose.replace(":", "_")}_{view}.png"
                bpy.ops.render.render(write_still=True)
    print("FINGERRENDER_OK")


def main():
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", "")
    _common.open_glb(src)
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    mesh = max((o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups), key=lambda o: len(o.data.vertices))
    if "--render" in argv:
        render(arm, mesh, dst, "--fist" in argv, _common_opt("--mode", None))
        return
    report = {}
    for side in ("Left", "Right"):
        try:
            build_side(arm, mesh, side, report)
        except Exception as e:  # noqa: BLE001
            import traceback

            report.setdefault(side, {})["error"] = str(e)[:300]
            report[side]["trace"] = traceback.format_exc()[-800:]
    report["bone_names"] = [b.name for b in arm.data.bones]
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    bpy.ops.object.select_all(action="DESELECT")
    for o in (arm, mesh):
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=False)
    print("FINGERBUILD_OK", {s: report.get(s, {}).get("path") for s in ("Left", "Right")})


if __name__ == "__main__":
    main()
