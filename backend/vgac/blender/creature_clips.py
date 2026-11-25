"""Blender headless: hand-made creature clips (CC0 Mesh2Motion animal clips) moved onto a creature rig by bone role.

Run: blender -b --python creature_clips.py -- <anim.glb> <out.glb> --classes <anim_anim.json> --spec <clips.json>
         [--fps 24] [--report <json>]

<anim.glb> is the rigged creature with its procedural clips (procanim's output) and <anim.glb>'s _clips.json sidecar;
--classes is the classification procanim wrote next to it (which chains are legs, the spine, the tail, which way the
creature faces). --spec lists the clips to take from the hand-made set:
  [{"name": "walk", "file": "<clip>.glb", "loop": true, "licence": "CC0 (Mesh2Motion)", "pack": "mesh2motion"}, ...]
Each file is a Mesh2Motion clip: one skeleton with named bones (front_leg_upper_l, back_leg_lower_r, spine_3, tail_2,
head, ...) and one action. Clips that are not in the spec keep their procedural version.

How a clip is moved across (by bone role, no shared bone names needed):
  1. Roles. The source's bones are grouped by role from the set's own bone names: each leg (front or back, left or
     right), the spine from the hips to the head, the tail. The target's roles come from procanim's classification
     (legs with their front and left flags, the spine, the tail).
  2. Both creatures face -Y and stand on Z, so a bone's turn away from its rest direction is the same in both
     skeletons' space. Per frame, per source bone: turn = world rotation(frame) * inverse(world rotation(rest)).
     A target bone takes the turn of the source bone at the same fraction along its chain (a target leg with five
     bones against a source leg with six reads each bone from the nearest one), applied to its own rest rotation.
     Bone lengths stay the target's. Bones with no role follow their parent.
  3. The hips: the source hips' move away from rest, scaled by the ratio of the two bodies' heights above their
     standing feet, minus the clip's own drift along the ground (every clip is in place). The body is then lifted so
     no foot sinks below its standing height, and lowered so the lowest foot touches it at its lowest point.
  3b. Feet (walk and run): over each stance the foot's goal is the straight line a planted foot travels at the body's
     speed; the leg's joints are turned a little (each within 30 degrees of the copied pose) to put the foot on it.
     A looping clip whose last pose is not its first is read as one period, closing back to the first pose.
  4. Each clip is baked as one action named like the procedural clip it replaces, written as an NLA track, and the
     sidecar rows get source "library" with the clip's pack and licence.
"""
import json
import math
import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import gait  # noqa: E402
import motionfile  # noqa: E402
from mathutils import Matrix, Quaternion, Vector  # noqa: E402

T0 = time.time()
_raw = _common.after_double_dash()
_VALUE_FLAGS = ("--classes", "--spec", "--fps", "--report")
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
src_glb, dst = os.path.abspath(_pos[0]), os.path.abspath(_pos[1])
fps = int(_common.opt("--fps", "24"))
report_path = _common.opt("--report", None)
report = {"ok": False, "clips": [], "library_clips": [], "missing_clips": [], "clip_reasons": {}, "detail": {}}


def fail(msg):
    report["error"] = msg
    if report_path:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=1)
    print("CREATURE_CLIPS_FAIL", msg)
    raise SystemExit(2)


with open(_common.opt("--classes", ""), encoding="utf-8") as f:
    classes = json.load(f)
with open(_common.opt("--spec", ""), encoding="utf-8") as f:
    spec = json.load(f)
if not spec:
    fail("no clips requested")

# ---- target ----------------------------------------------------------------------------------------------------
_common.open_glb(src_glb)
scene = bpy.context.scene
scene.render.fps = fps
tgt = next((o for o in scene.objects if o.type == "ARMATURE"), None)
if tgt is None:
    fail("no armature in the creature file")
tgt_objects = set(scene.objects)
proc_actions = {}
if tgt.animation_data:
    for tr in tgt.animation_data.nla_tracks:
        if tr.strips and tr.strips[0].action:
            proc_actions[tr.name] = tr.strips[0].action
    tgt.animation_data.action = None
    for tr in tgt.animation_data.nla_tracks:
        tr.mute = True
old_sidecar = {}
_sc = os.path.splitext(src_glb)[0] + "_clips.json"
if os.path.isfile(_sc):
    with open(_sc) as f:
        old_sidecar = json.load(f)
old_rows = {c["name"]: c for c in old_sidecar.get("clips", [])}

T_W = tgt.matrix_world.copy()
T_Wq = T_W.to_quaternion().normalized()
tbones = {b.name: b for b in tgt.data.bones}
t_rest = {n: T_W @ b.matrix_local for n, b in tbones.items()}  # armature-space rest matrix in world
t_order = []


def _depth(b):
    d = 0
    while b.parent is not None:
        b, d = b.parent, d + 1
    return d


t_order = sorted(tbones, key=lambda n: _depth(tbones[n]))
roots = [n for n in t_order if tbones[n].parent is None]
if not roots:
    fail("the creature rig has no root bone")
ROOT = roots[0]

# ---- target roles (procanim's classification) ----------------------------------------------------------------
lo_z = min((T_W @ b.head_local).z for b in tbones.values())
leg_rows = [r for r in classes.get("legs", []) if r.get("bones")]


def _foot_end(name):
    b = tbones[name]
    h, t = T_W @ b.head_local, T_W @ b.tail_local
    return ("head", h) if h.z <= t.z else ("tail", t)


if leg_rows:
    low_z = min(_foot_end(r["bones"][-1])[1].z for r in leg_rows)
    height = float(classes.get("height") or 1.0)
    # A chain that does not reach the ground is not a leg (procanim can read a tail that way).
    ground_legs = [r for r in leg_rows if _foot_end(r["bones"][-1])[1].z <= low_z + 0.15 * height]
    other_chains = [r for r in leg_rows if r not in ground_legs]
else:
    ground_legs, other_chains = [], []
tgt_roles = {}
for _side in ("l", "r"):
    # Each side's legs from front to back; the key is the side and the place in that order.
    _legs = sorted([r for r in ground_legs if bool(r["left"]) == (_side == "l")], key=lambda r: -float(r.get("fwd") or 0.0))
    for _rank, r in enumerate(_legs):
        tgt_roles["leg:%s:%d" % (_side, _rank)] = list(r["bones"])
tgt_roles["spine"] = list(classes.get("spine") or [])
tail = list(classes.get("tail") or [])
if not tail and other_chains:
    tail = list(other_chains[0]["bones"])
if tail:
    tgt_roles["tail"] = tail
if not tgt_roles["spine"]:
    fail("the creature rig has no spine chain")
FWD = Vector(classes.get("forward") or (0.0, -1.0, 0.0))
FWD.z = 0.0
FWD = FWD.normalized() if FWD.length > 1e-6 else Vector((0.0, -1.0, 0.0))
# The clips face -Y. A creature facing another way takes every turn and move turned the same way about Z.
FACE = Vector((0.0, -1.0, 0.0)).rotation_difference(FWD)
if not any(k.startswith("leg:") for k in tgt_roles):
    fail("the creature rig has no legs on the ground: not retargeted")
feet = {k: r[-1] for k, r in tgt_roles.items() if k.startswith("leg:")}
rest_ground = min(_foot_end(b)[1].z for b in feet.values()) if feet else lo_z


def _leg_group(n):
    """Which leg a Mesh2Motion bone belongs to ('front' or 'back'), or None."""
    if "leaf" in n or "anchor" in n:
        return None
    if n.startswith(("front_", "back_")):
        return n.split("_")[0]
    return None


def _src_roles(arm):
    """Role -> bone names (root to tip) from the Mesh2Motion bone names: "legs" {side: chains, front first}, "spine"
    and "tail"."""
    names = [b.name for b in arm.data.bones]
    bones = arm.data.bones
    out = {"legs": {}}
    for side in ("l", "r"):
        groups = {}
        for n in names:
            g = _leg_group(n)
            if g and n.endswith("_" + side):
                groups.setdefault(g, []).append(n)
        chains = [sorted(v, key=lambda n: _depth(bones[n])) for v in groups.values()]
        # front first: the creature faces -Y, so the smaller Y is further forward
        chains.sort(key=lambda c: (arm.matrix_world @ bones[c[0]].head_local).y)
        if chains:
            out["legs"][side] = chains
    spine = [n for n in names if n == "hips" or n.startswith("spine_") or n == "head"]
    spine.sort(key=lambda n: _depth(bones[n]))
    if spine:
        out["spine"] = spine
    tl = [n for n in names if n.startswith("tail_") and "leaf" not in n]
    tl.sort(key=lambda n: _depth(bones[n]))
    if tl:
        out["tail"] = tl
    return out


def _source_leg(s_roles, key):
    """The source leg a target leg reads: the one at the same place in its side's front-to-back order."""
    _, side, rank = key.split(":")
    n_t = sum(1 for k in tgt_roles if k.startswith("leg:%s:" % side))
    chains = s_roles["legs"].get(side)
    if not chains:
        raise RuntimeError("the clip's skeleton has no legs on its %s side" % ("left" if side == "l" else "right"))
    i = 0 if n_t == 1 else int(round(int(rank) * (len(chains) - 1) / (n_t - 1)))
    return chains[i]


def _assign_chain(t_chain, s_chain):
    """Each target bone reads the source bone at the same fraction along its own chain (a target leg with five bones
    against a source leg with six reads each bone from the nearest one)."""
    n = len(t_chain)
    return {tb: (s_chain[0] if n == 1 else s_chain[int(round(j * (len(s_chain) - 1) / (n - 1)))]) for j, tb in enumerate(t_chain)}


FOOT_PLANT_SHARE = 0.03   # a foot is planted within this share of the creature's height of its own lowest point
PIN_MAX_TURN = math.radians(30.0)  # most a joint is turned from the copied pose to hold a foot still
PIN_ITERATIONS = 10
PIN_CLIPS = ("walk", "run")  # the clips whose feet travel along the ground; the others (turns, a fall) keep the copied pose


def _foot_pos(M, bn):
    end, _ = _foot_end(bn)
    local = tbones[bn].head_local if end == "head" else tbones[bn].tail_local
    return M[bn] @ (t_rest[bn].inverted() @ (T_W @ local))


def pin_planted_feet(frames, turns, pose_frame, lift_f, loop):
    """Keep each planted foot still on the ground. The in-place clip has the body standing still, so a planted foot
    should slide back along the ground at one steady speed (the treadmill). The copied pose does not quite do that on
    a leg with other proportions, so over each stance the foot's goal is the straight line fitted to where it is, and
    the leg's joints are turned a little (cyclic coordinate descent, each joint kept within PIN_MAX_TURN of the copied
    pose) to put the foot on that line. Returns {"stances": n, "error_before_m": the largest distance a planted foot was from its line}."""
    n = len(frames)
    cyc = n - 1 if loop else n   # a looping clip's last frame is its first again
    H = float(classes.get("height") or 1.0)
    dt = 1.0 / fps
    info = {"stances": 0, "error_before_m": 0.0}
    for key, bn in feet.items():
        chain = tgt_roles[key]
        pts = [_foot_pos(frames[i], bn) for i in range(n)]
        zs = [p.z for p in pts]
        lo = min(zs)
        planted = [zs[i] <= lo + FOOT_PLANT_SHARE * H for i in range(cyc)]
        if not any(planted) or all(planted):
            continue
        # runs of planted frames, joined across the loop seam
        start = next(i for i in range(cyc) if not planted[i])
        order = [(start + k) % cyc for k in range(cyc)]
        runs, cur = [], []
        for k, i in enumerate(order):
            if planted[i]:
                cur.append((k, i))
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        steps = [(pts[b] - pts[a]) for r in runs for (ka, a), (kb, b) in zip(r, r[1:])]
        if not steps:
            continue
        v = Vector((sum(s.x for s in steps), sum(s.y for s in steps), 0.0)) / (len(steps) * dt)
        for r in runs:
            if len(r) < 3:
                continue
            c = Vector((0, 0, 0))
            for k, i in r:
                c += Vector((pts[i].x, pts[i].y, 0)) - v * (k * dt)
            c /= len(r)
            goals = {i: c + v * (k * dt) for k, i in r}
            info["stances"] += 1
            for k, i in r:
                before = (Vector((pts[i].x, pts[i].y, 0)) - goals[i]).length
                info["error_before_m"] = max(info["error_before_m"], before)
                tn = dict(turns[i])
                corr = {}
                for _ in range(PIN_ITERATIONS):
                    M = pose_frame(i, lift_f[i], tn)
                    for jb in range(len(chain) - 1, 0, -1):
                        M = pose_frame(i, lift_f[i], tn)
                        J = M[chain[jb]].translation
                        e = _foot_pos(M, bn)
                        tgt_p = Vector((goals[i].x, goals[i].y, e.z))
                        a, b = e - J, tgt_p - J
                        if a.length < 1e-6 or b.length < 1e-6:
                            continue
                        q = a.rotation_difference(b)
                        for bone in chain[jb:]:
                            tn[bone] = q @ tn[bone]
                        corr[chain[jb]] = corr.get(chain[jb], Quaternion()) @ q
                        ang = corr[chain[jb]].angle
                        if ang > PIN_MAX_TURN:  # undo the part past the limit
                            over = corr[chain[jb]].slerp(Quaternion(), 1.0 - PIN_MAX_TURN / ang)
                            back = over @ corr[chain[jb]].inverted()
                            for bone in chain[jb:]:
                                tn[bone] = back @ tn[bone]
                            corr[chain[jb]] = over
                turns[i] = tn
    if loop:
        turns[-1] = turns[0]
    for i in range(n):
        frames[i] = pose_frame(i, lift_f[i])
    info["error_before_m"] = round(info["error_before_m"], 4)
    return info


def bake_clip(entry):
    """-> (action, frames, detail) for one spec entry."""
    path = entry["file"]
    info = motionfile.load(path)
    sarm = info["arm"]
    if sarm is None or not info["actions"]:
        raise RuntimeError("no skeleton or action in " + os.path.basename(path))
    sact = next(iter(info["actions"].values()))
    s_roles = _src_roles(sarm)
    if "spine" not in s_roles:
        raise RuntimeError("the clip's skeleton has no spine")
    src_chain = {k: (_source_leg(s_roles, k) if k.startswith("leg:") else s_roles.get(k)) for k in tgt_roles}
    S_W = sarm.matrix_world.copy()
    S_Wq = S_W.to_quaternion().normalized()
    sb = {b.name: b for b in sarm.data.bones}
    s_rest_q = {n: (S_W @ b.matrix_local).to_quaternion().normalized() for n, b in sb.items()}
    # target bone -> source bone
    assign = {}
    for key, chain in tgt_roles.items():
        if src_chain[key]:   # a tail the clip's skeleton has none of stays with its parent
            assign.update(_assign_chain(chain, src_chain[key]))
    # hips: the target root reads the source hips (its own turn included)
    s_hips = "hips" if "hips" in sb else s_roles["spine"][0]
    assign[ROOT] = s_hips
    s_feet = [src_chain[k][-1] for k in tgt_roles if k.startswith("leg:")]

    def foot_pt(b):  # the lower end of a source foot bone at rest
        h, t = S_W @ b.head_local, S_W @ b.tail_local
        return "head" if h.z <= t.z else "tail"

    s_foot_end = {n: foot_pt(sb[n]) for n in s_feet}
    s_ground = min(((S_W @ (sb[n].head_local if s_foot_end[n] == "head" else sb[n].tail_local)).z for n in s_feet), default=0.0)
    s_hip_rest = (S_W @ sb[s_hips].head_local)
    # the body's size against the source's: its height above the standing feet
    s_top = max(max((S_W @ b.head_local).z, (S_W @ b.tail_local).z) for b in sarm.data.bones)
    ratio = float(classes.get("height") or 1.0) / max(1e-4, s_top - s_ground)

    motionfile.use_action(sarm, sact)
    f0, f1 = sact.frame_range
    src_fps = info["fps"] or 24.0
    loop = bool(entry.get("loop"))

    def sample(fr):
        scene.frame_set(int(math.floor(fr)), subframe=fr - math.floor(fr))
        out = {}
        for sbn in set(assign.values()):
            m = S_W @ sarm.pose.bones[sbn].matrix
            q = m.to_quaternion().normalized() @ s_rest_q[sbn].inverted()
            out[sbn] = (FACE @ q @ FACE.inverted(), m.translation.copy())
        return out

    # A looping clip whose last pose is not its first one (the file stops one frame short of the repeat) is one
    # period long: the closing stretch eases from the last pose back to the first.
    period_end = f1
    wrap = False
    if loop:
        a0, a1 = sample(f0), sample(f1)
        gap = sum(abs(a0[b][0].rotation_difference(a1[b][0]).angle) for b in a0) / max(1, len(a0))
        wrap = gap > math.radians(1.0)
        if wrap:
            period_end = f1 + 1
    n_out = max(2, int(round((period_end - f0) / src_fps * fps)) + 1)
    turns = []     # per frame: {target bone: world quaternion turn}
    hips_pos = []
    for k in range(n_out):
        fr = f0 + (period_end - f0) * k / (n_out - 1)
        if wrap and fr > f1:
            u = fr - f1
            c0, c1 = sample(f1), sample(f0)
            cache = {b: (c0[b][0].slerp(c1[b][0], u), c0[b][1].lerp(c1[b][1], u)) for b in c0}
        else:
            cache = sample(fr)
        turns.append({tb: cache[s][0] for tb, s in assign.items()})
        hips_pos.append(cache[s_hips][1])
    bpy.data.objects.remove(sarm, do_unlink=True)

    # hips move: scaled, minus the horizontal drift over the clip (in place), vertical kept
    d = [(FACE @ (p - s_hip_rest)) * ratio for p in hips_pos]
    for i in range(n_out):
        u = i / (n_out - 1)
        d[i] = Vector((d[i].x - (d[-1].x - d[0].x) * u - d[0].x, d[i].y - (d[-1].y - d[0].y) * u - d[0].y, d[i].z))
    d[:] = [Vector((v.x, v.y, v.z)) for v in d]

    def pose_frame(i, lift, tn=None):
        tn = turns[i] if tn is None else tn
        M = {}
        for n in t_order:
            b = tbones[n]
            if b.parent is None:
                base = t_rest[n].copy()
                if n == ROOT:
                    base.translation = base.translation + d[i] + Vector((0, 0, lift))
            else:
                p = b.parent.name
                base = M[p] @ (t_rest[p].inverted() @ t_rest[n])
            if n in tn:
                dq = T_Wq.inverted() @ S_Wq @ tn[n] @ S_Wq.inverted() @ T_Wq
                rq = (dq @ t_rest[n].to_quaternion().normalized()).normalized()
                m = rq.to_matrix().to_4x4()
                m.translation = base.translation
                base = m
            M[n] = base
        return M

    def foot_z(M):
        z = []
        for k, bn in feet.items():
            end, _ = _foot_end(bn)
            local = tbones[bn].head_local if end == "head" else tbones[bn].tail_local
            pos = M[bn] @ (t_rest[bn].inverted() @ (T_W @ local))
            z.append(pos.z)
        return min(z) if z else rest_ground

    M0 = [pose_frame(i, 0.0) for i in range(n_out)]
    lifts = [max(0.0, rest_ground - foot_z(M)) for M in M0]
    # drop so the lowest foot touches the ground at the clip's lowest point
    M1 = [pose_frame(i, lifts[i]) for i in range(n_out)]
    low_after = min(foot_z(M) for M in M1)
    drop = low_after - rest_ground
    lift_f = [lifts[i] - drop for i in range(n_out)]
    frames = [pose_frame(i, lift_f[i]) for i in range(n_out)]
    slide = pin_planted_feet(frames, turns, pose_frame, lift_f, loop) if entry["name"] in PIN_CLIPS else None
    return frames, {"hip_ratio": round(ratio, 3), "lift_max_m": round(max(lifts), 4), "drop_m": round(drop, 4),
                    "source_frames": [round(f0), round(f1)], "from": os.path.basename(path), "pinned": slide}


def write_action(name, frames):
    act = bpy.data.actions.new(name)
    tgt.animation_data_create()
    tgt.animation_data.action = act
    for pb in tgt.pose.bones:
        pb.rotation_mode = "QUATERNION"
    n = len(frames)
    fast = hasattr(act, "fcurve_ensure_for_datablock")
    chans = {}
    prev = {}
    for i, M in enumerate(frames):
        for bn in t_order:
            b = tbones[bn]
            rest = t_rest[bn]
            if b.parent is None:
                basis = rest.inverted() @ M[bn]
            else:
                p = b.parent.name
                basis = (M[p] @ (t_rest[p].inverted() @ rest)).inverted() @ M[bn]
            q = basis.to_quaternion().normalized()
            if bn in prev and q.dot(prev[bn]) < 0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
            prev[bn] = q
            chans.setdefault(bn, []).append((q, basis.translation.copy()))
    for bn in t_order:
        pb = tgt.pose.bones[bn]
        qs = [c[0] for c in chans[bn]]
        ls = [c[1] for c in chans[bn]]
        use_loc = any(v.length > 1e-6 for v in ls)

        def put(path, idx, values):
            fc = act.fcurve_ensure_for_datablock(tgt, path, index=idx)
            fc.keyframe_points.clear()
            fc.keyframe_points.add(n)
            co = []
            for i, v in enumerate(values):
                co += [float(i + 1), float(v)]
            fc.keyframe_points.foreach_set("co", co)
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
            fc.update()

        for k in range(4):
            put(f'pose.bones["{bn}"].rotation_quaternion', k, [q[k] for q in qs])
        if use_loc:
            for k in range(3):
                put(f'pose.bones["{bn}"].location', k, [v[k] for v in ls])
    return act


def end_tracks(frames, bones):
    """Per bone, per frame: the world position of the low end of the bone (for the speed and hit measures)."""
    out = {}
    for bn in bones:
        end, _ = _foot_end(bn)
        local = tbones[bn].head_local if end == "head" else tbones[bn].tail_local
        out[bn] = [tuple(M[bn] @ (t_rest[bn].inverted() @ (T_W @ local))) for M in frames]
    return out


made = {}
for entry in spec:
    name = entry["name"]
    try:
        frames, det = bake_clip(entry)
    except Exception as e:  # noqa: BLE001
        report["missing_clips"].append(name)
        report["clip_reasons"][name] = str(e)[:300]
        continue
    act = write_action(name, frames)
    ends = end_tracks(frames, list(feet.values()))
    row_extra = {}
    h = float(classes.get("height") or 1.0)
    if name in ("walk", "run") and ends:
        res = gait.planted_speed([ends[b] for b in feet.values()], fps, h)
        row_extra.update(gait.speed_row_fields(res))
    if name == "attack" and ends:
        fwd = classes.get("forward") or [0.0, -1.0, 0.0]
        hit = gait.hit_frame(ends, fps, tuple(fwd), h)
        if hit["frame"] >= 0:
            row_extra.update(hit_frame=hit["frame"], hit_time=hit["time"], hit_speed_mps=hit["speed"], hit_end=hit["end"],
                             hit_method=hit["method"], hit_peak_frame=hit["peak_frame"])
    made[name] = {"action": act, "frames": len(frames), "entry": entry, "extra": row_extra}
    report["detail"][name] = det
if not made:
    fail("no clip could be moved onto this rig: " + "; ".join(f"{k}: {v}" for k, v in report["clip_reasons"].items()))

# ---- assemble: keep the procedural clips that are not replaced, in their old order ---------------------------
ad = tgt.animation_data
ad.action = None
order = list(old_rows) or list(proc_actions)
for n in made:
    if n not in order:
        order.append(n)
for tr in list(ad.nla_tracks):
    ad.nla_tracks.remove(tr)
rows = []
for n in order:
    if n in made:
        a = made[n]["action"]
    elif n in proc_actions:
        a = proc_actions[n]
    else:
        continue
    tr = ad.nla_tracks.new()
    tr.name = n
    st = tr.strips.new(n, 1, a)
    st.name = n
    if hasattr(st, "action_slot") and len(getattr(a, "slots", [])) > 0:
        st.action_slot = a.slots[0]
    if n in made:
        m = made[n]
        total = m["frames"] - 1
        e = m["entry"]
        row = {"name": n, "loop": bool(e.get("loop", n in gait.LOOPING)), "frame_start": 0, "frame_end": total, "frames": m["frames"],
               "seconds": round(total / fps, 4), "motion_frames": total, "hold_last": n == "death", "in_place": True,
               "source": "library", "from": e.get("from") or n, "library": e.get("pack", "mesh2motion")}
        if e.get("licence"):
            row["licence"] = e["licence"]
        row.update(m["extra"])
        rows.append(row)
        report["library_clips"].append(n)
    else:
        r = dict(old_rows.get(n) or {})
        if not r:
            total = int(round(a.frame_range[1] - a.frame_range[0]))
            r = {"name": n, "loop": n in gait.LOOPING, "frame_start": 0, "frame_end": total, "frames": total + 1,
                 "seconds": round(total / fps, 4), "motion_frames": total, "hold_last": n == "death", "in_place": True}
        r["source"] = "procedural"
        rows.append(r)
for pb in tgt.pose.bones:
    pb.rotation_quaternion = Quaternion()
    pb.location = Vector()
    pb.scale = Vector((1, 1, 1))
scene.frame_start, scene.frame_end = 1, max([r["frame_end"] + 1 for r in rows] or [fps * 2])
scene.frame_set(1)
for o in list(scene.objects):
    if o not in tgt_objects:
        bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.object.select_all(action="SELECT")
_args = dict(filepath=dst, export_format="GLB", use_selection=True, export_animations=bool(rows),
             export_animation_mode="NLA_TRACKS", export_force_sampling=True, export_apply=False)
try:
    bpy.ops.export_scene.gltf(**_args, export_anim_slide_to_zero=True)
except TypeError:
    bpy.ops.export_scene.gltf(**_args)

base = os.path.splitext(dst)[0]
sidecar = {k: v for k, v in old_sidecar.items() if k != "clips"}
sidecar.update({"version": 2, "fps": fps, "root_motion": False, "clips": rows, "clips_source": "library"})
_lic = list(dict.fromkeys(r["licence"] for r in rows if r.get("licence")))
if _lic:
    sidecar["licence"] = "; ".join(_lic)
with open(base + "_clips.json", "w") as f:
    json.dump(sidecar, f, indent=1)
report.update({"ok": True, "clips": [r["name"] for r in rows], "clip_table": rows, "seconds": round(time.time() - T0, 2)})
if report_path:
    with open(report_path, "w") as f:
        json.dump(report, f, indent=1)
print("CREATURE_CLIPS_OK", len(made), "hand-made clips;", ",".join(report["clips"]), "in", report["seconds"], "s")
