"""Blender headless: procedural clips for any SkinToken-style skeleton (G30, G31; bakeoff/procanim.py).

Run: blender -b --python procanim.py -- <rigged.glb> <out.glb> [fps=24]
         [--motion auto|legs|biped|quadruped|multileg|winged|serpent|none|wings|wave|library]
         [--clips idle,walk,run,turn_left,turn_right,attack,hit,death,flap,glide,takeoff,land,slither]
         [--size small|normal|big|<number>] [--speed slow|normal|fast|<number>]

With no --motion and no --clips it makes idle and walk, as it always has. With a motion and no clips it makes the
motion's full set (gait.DEFAULT_CLIPS, the plan 3.7 table). Chains are classified by geometry alone (no naming
needed): leg chains end near the ground, the spine runs from the root toward the head, the tail is the rearmost
chain, wings leave the torso sideways above the legs and are longer than the legs, a serpent's spine is the
longest chain in the tree. Every clip is in place. Each clip becomes an NLA track and a glTF animation of the same
name (Blender 5: strip.action_slot = action.slots[0]); cyclic clips (gait.LOOPING) have the loop flag.

Writes next to <out>:
    <out>_anim.json   classification report (kind, chains, legs, probes) plus "clips" (names) and "clip_table"
    <out>_clips.json  the clip sidecar: {version, fps, motion, clips: [{name, loop, frame_start, frame_end, ...}]}
"""
import json
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
import gait  # noqa: E402  (pure maths next to this script)
from mathutils import Quaternion, Vector  # noqa: E402

# ---- arguments -----------------------------------------------------------------------------------------------
_VALUE_FLAGS = ("--motion", "--clips", "--size", "--speed")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
src, dst = os.path.abspath(_pos[0]), os.path.abspath(_pos[1])
fps = int(_pos[2]) if len(_pos) > 2 else 24
motion_arg = _common.opt("--motion", None)
clips_arg = _common.opt("--clips", None)
SIZE = gait.factor(_common.opt("--size", None), gait.MOVE_SIZE)
SPEED = gait.factor(_common.opt("--speed", None), gait.MOVE_SPEED)

_common.open_glb(src)
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not arms:
    raise SystemExit("no armature")
arm = arms[0]
scene = bpy.context.scene
scene.render.fps = fps

# ---- geometry ------------------------------------------------------------------------------------------------
W = arm.matrix_world
bones = list(arm.data.bones)
byname = {b.name: b for b in bones}
head = {b.name: W @ b.head_local for b in bones}
tail = {b.name: W @ b.tail_local for b in bones}
pts = [p for o in meshes for p in (o.matrix_world @ Vector(c) for c in o.bound_box)]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
height = max(1e-4, hi.z - lo.z)
centre = (lo + hi) / 2
dims = hi - lo
hmax = max(dims.x, dims.y, 1e-4)
up = Vector((0, 0, 1))
roots = [b for b in bones if b.parent is None]
leaves = [b for b in bones if not b.children]
# Leaf end points: the lower of head and tail (a foot can be a leaf's tail when the leaf starts at the knee),
# and the end farther from the parent joint (a wing tip).
low = {b.name: (head[b.name] if head[b.name].z <= tail[b.name].z else tail[b.name]) for b in leaves}
far = {b.name: max((head[b.name], tail[b.name]), key=lambda p: (p - head[b.parent.name if b.parent else b.name]).length) for b in leaves}


def chain_to_root(b):
    out = []
    while b is not None:
        out.append(b)
        b = b.parent
    return out  # leaf first


def zrel(p):
    return (p.z - lo.z) / height


def snap(v):
    """Nearest of +-X and +-Y to a horizontal direction."""
    if abs(v.x) >= abs(v.y):
        return Vector((1 if v.x >= 0 else -1, 0, 0))
    return Vector((0, 1 if v.y >= 0 else -1, 0))


def path_len(names):
    return sum((head[a] - head[b]).length for a, b in zip(names, names[1:]))


# ---- motion request ------------------------------------------------------------------------------------------
_req = (motion_arg or "auto").strip().lower()
serpent_req = gait.MOTION_ALIASES.get(_req, _req) == "serpent"
none_req = gait.MOTION_ALIASES.get(_req, _req) == "none"

# ---- legs ----------------------------------------------------------------------------------------------------
# Leg chains: leaves whose head sits in the bottom 20 percent of the height, reduced to the lowest leaf per limb
# cluster (cluster by horizontal distance). Every cluster is kept (G31: spiders move all eight legs).
leg_leaves = [] if serpent_req else [b for b in leaves if zrel(low[b.name]) < 0.2]
leg_leaves.sort(key=lambda b: low[b.name].z)
clusters = []
for b in leg_leaves:
    p = low[b.name]
    if all((p - low[c.name]).xy.length > 0.12 * hmax for c in clusters):
        clusters.append(b)
legs = clusters
leg_leaf_names = {b.name for b in leg_leaves}

# ---- forward -------------------------------------------------------------------------------------------------
upper_leaves = [b for b in leaves if b.name not in leg_leaf_names and zrel(head[b.name]) > 0.5]
legs_centroid = (sum((low[b.name] for b in legs), Vector()) / len(legs)) if legs else centre.copy()
forward_reason = "default -Y (glTF front)"
forward = Vector((0, -1, 0))
serpent_path = []
if serpent_req:
    # The longest leaf-to-leaf path is the body; its higher end (or the -Y end) is the head.
    def _far(start):
        best, bestd, seen, stack = start, 0.0, {start.name}, [(start, 0.0)]
        while stack:
            b, d = stack.pop()
            if d > bestd:
                best, bestd = b, d
            for n in ([b.parent] if b.parent else []) + list(b.children):
                if n.name not in seen:
                    seen.add(n.name)
                    stack.append((n, d + (head[n.name] - head[b.name]).length))
        return best

    def _path(a, b):
        pa = [x.name for x in chain_to_root(a)]
        pb = [x.name for x in chain_to_root(b)]
        common = next(n for n in pa if n in set(pb))
        return pa[:pa.index(common) + 1] + pb[:pb.index(common)][::-1]

    e1 = _far(roots[0])
    e2 = _far(e1)
    serpent_path = _path(e1, e2)  # e1 ... e2
    p1, p2 = head[serpent_path[0]], head[serpent_path[-1]]
    if abs(p1.z - p2.z) > 0.15 * height:
        head_first = p1.z > p2.z
    else:
        head_first = p1.y < p2.y  # -Y is the glTF front
    if not head_first:
        serpent_path = serpent_path[::-1]
    d = head[serpent_path[0]] - head[serpent_path[-1]]
    if d.xy.length > 1e-4:
        forward, forward_reason = snap(Vector((d.x, d.y, 0))), "serpent: tail end to head end"
elif len(legs) == 2:
    # Bipeds (and birds): feet sit side by side, so the lateral axis joins them; the toes point forward.
    sep = low[legs[0].name] - low[legs[1].name]
    lat_axis = snap(Vector((sep.x, sep.y, 0)))
    fwd_axis = up.cross(lat_axis).normalized()
    toe = sum(((tail[b.name] - head[b.name]) for b in legs), Vector())
    toe.z = 0
    upper_mean = (sum((head[b.name] for b in upper_leaves), Vector()) / len(upper_leaves)) if upper_leaves else legs_centroid
    if abs(toe.dot(fwd_axis)) > 0.02 * height:
        forward, forward_reason = fwd_axis * (1 if toe.dot(fwd_axis) > 0 else -1), "biped: toes"
    elif abs((upper_mean - legs_centroid).dot(fwd_axis)) > 0.1 * hmax:
        forward, forward_reason = fwd_axis * (1 if (upper_mean - legs_centroid).dot(fwd_axis) > 0 else -1), "biped: head over feet"
    else:
        forward = fwd_axis if fwd_axis.dot(Vector((0, -1, 0))) >= 0 else -fwd_axis
        forward_reason = "biped: feet axis, glTF front"
elif upper_leaves:
    upper_mean = sum((head[b.name] for b in upper_leaves), Vector()) / len(upper_leaves)
    d = upper_mean - legs_centroid
    d.z = 0
    if d.length > 0.12 * hmax:
        forward, forward_reason = snap(d), "head end over the legs"
forward = forward.normalized()
lateral = up.cross(forward).normalized()  # points to the creature's left
span = max(1e-4, abs(dims.dot(lateral)))
length = max(1e-4, abs(dims.dot(forward)))


def fwd_of(p):
    return (p - centre).dot(forward)


def lat_of(p):
    return (p - centre).dot(lateral)


# ---- leg chains ----------------------------------------------------------------------------------------------
leg_info = []
for b in legs:
    p = low[b.name]
    leg_info.append({"leaf": b.name, "front": fwd_of(p) > fwd_of(legs_centroid), "left": lat_of(p) > 0, "fwd": fwd_of(p),
                     "chain": [x.name for x in chain_to_root(b)]})
all_chains = [set(li["chain"]) for li in leg_info]
for li in leg_info:
    own = []
    for name in li["chain"]:
        if sum(1 for c in all_chains if name in c) > 1:
            break
        own.append(name)
    li["bones"] = own[::-1]  # top of leg first
    if len(li["bones"]) > 6:  # a leg that swallowed the spine (single-leg skeletons): keep the lower part
        li["bones"] = li["bones"][-6:]
leg_info = [li for li in leg_info if li["bones"]]


def _tail_like(li):
    """A low chain that runs backward more than it drops is a tail lying on the ground, not a leg."""
    d = low[li["leaf"]] - head[li["bones"][0]]
    return -d.dot(forward) > max(0.0, -d.z) and abs(d.dot(lateral)) < 0.5 * abs(d.dot(forward))


if len(leg_info) > 2:
    leg_info = [li for li in leg_info if not _tail_like(li)]
if leg_info:
    _fc = sum(li["fwd"] for li in leg_info) / len(leg_info)
    for li in leg_info:
        li["front"] = li["fwd"] > _fc
leg_bone_set = {n for li in leg_info for n in li["bones"]}
leg_len = (sum((head[li["bones"][0]] - low[li["leaf"]]).length for li in leg_info) / len(leg_info)) if leg_info else 0.0

# ---- wings ---------------------------------------------------------------------------------------------------
# A wing leaves the torso sideways above the legs, reaches far out to the side, does not hang down, and is longer
# than the legs (plan 3.7 item 7). Per side the longest such chain wins.
wing_info = []
if not serpent_req:
    cands = {True: [], False: []}
    for b in leaves:
        if b.name in leg_leaf_names:
            continue
        p = far[b.name]
        if abs(lat_of(p)) < 0.28 * span or zrel(p) < 0.3:
            continue
        chain = []
        for x in chain_to_root(b):
            if abs(lat_of(head[x.name])) < 0.03 * span or x.name in leg_bone_set:
                break
            chain.append(x.name)
        chain = chain[::-1]
        if len(chain) < 2:
            continue
        root_p = head[chain[0]]
        reach = (p - root_p).length
        if reach < max(0.8 * leg_len, 0.2 * span):
            continue
        if p.z < root_p.z - 0.35 * reach:  # hangs down: an arm, not a wing
            continue
        cands[lat_of(p) > 0].append((reach, chain, b.name))
    for side in (True, False):
        if cands[side]:
            reach, chain, leaf = max(cands[side])
            wing_info.append({"side": "left" if side else "right", "left": side, "leaf": leaf, "bones": chain, "reach": reach})
wing_bone_set = {n for w in wing_info for n in w["bones"]}
wings_found = len(wing_info) >= 2

# ---- spine, head, neck, tail, arms ---------------------------------------------------------------------------
if serpent_req:
    spine = list(serpent_path)  # head end first for serpents
else:
    spine_cands = [b for b in upper_leaves if b.name not in wing_bone_set] or \
                  [b for b in leaves if b.name not in leg_leaf_names and b.name not in wing_bone_set]
    spine_leaf = max(spine_cands, key=lambda b: zrel(head[b.name]) + 0.5 * fwd_of(head[b.name]) / max(length, height)) if spine_cands else None
    spine = [x.name for x in chain_to_root(spine_leaf)][::-1] if spine_leaf else [roots[0].name]
spine_set = set(spine)
if serpent_req:
    head_bones = spine[:2] if len(spine) >= 6 else spine[:1]
    torso = [n for n in spine if n not in head_bones]
    neck = []
else:
    head_bones = spine[-2:] if len(spine) >= 2 else spine
    torso = spine[:-2] if len(spine) > 3 else spine[:1]
    # Neck: spine bones after the last limb attachment, short of the head.
    attach = [i for i, n in enumerate(spine) if any(c.name not in spine_set for c in byname[n].children)]
    last_attach = max([i for i in attach if i < len(spine) - 2], default=-1)
    neck = [n for n in spine[last_attach + 1:] if n not in head_bones] if last_attach >= 0 else []

kind = gait.kind_for_legs(len(leg_info))
tail_chain = []
if not serpent_req and kind != "biped":
    rear = [b for b in leaves if b.name not in leg_bone_set and b.name not in wing_bone_set and b.name not in spine_set
            and fwd_of(head[b.name]) < -0.2 * length]
    if rear:
        tail_leaf = min(rear, key=lambda b: fwd_of(head[b.name]))
        for b in chain_to_root(tail_leaf):
            if b.name in spine_set or b.name in leg_bone_set:
                break
            tail_chain.append(b.name)
        tail_chain = tail_chain[::-1]

arm_info = []
if not serpent_req:
    for side in (True, False):
        best = None
        for b in leaves:
            if b.name in leg_bone_set or b.name in spine_set or b.name in wing_bone_set or b.name in tail_chain:
                continue
            p = head[b.name]
            if not (0.25 < zrel(p) < 0.92) or (lat_of(p) > 0) != side or abs(lat_of(p)) < 0.08 * span:
                continue
            chain = [x.name for x in chain_to_root(b) if x.name not in spine_set][::-1]
            if not chain:
                continue
            reach = (p - head[chain[0]]).length
            if best is None or reach > best[0]:
                best = (reach, chain, b.name)
        if best and kind == "biped":
            arm_info.append({"side": "left" if side else "right", "left": side, "leaf": best[2], "bones": best[1]})

# ---- motion and clips ----------------------------------------------------------------------------------------
motion = gait.resolve_motion(motion_arg, len(leg_info))
legacy = motion_arg is None or _req == "library"
clips, unknown_clips = gait.resolve_clips(motion if motion != "unknown" else "biped", clips_arg, legacy=legacy)
if motion == "none":
    clips = []
missing = [c for c in clips if not gait.clip_supported(c, motion, len(leg_info), wings_found, len(spine))]
clips = [c for c in clips if c not in missing]

report = {"kind": kind, "motion": motion, "requested_motion": motion_arg, "forward": list(forward), "forward_reason": forward_reason,
          "lateral": list(lateral), "height": height, "length": length, "span": span,
          "legs": [{k: v for k, v in li.items() if k != "chain"} for li in leg_info],
          "wings": [{k: v for k, v in w.items()} for w in wing_info], "wings_found": wings_found,
          "arms": arm_info, "spine": spine, "neck": neck, "tail": tail_chain, "head": head_bones,
          "fps": fps, "size": SIZE, "speed": SPEED, "clips": clips, "missing_clips": missing, "unknown_clips": unknown_clips}

# ---- per-limb rotation axes ----------------------------------------------------------------------------------


def unit(v, fallback):
    return v.normalized() if v.length > 1e-6 else fallback


for li in leg_info:
    b0 = li["bones"][0]
    d = low[li["leaf"]] - head[b0]
    horiz = Vector((d.x, d.y, 0))
    li["splay"] = horiz.length / max(1e-6, d.length)
    li["swing_axis"] = unit(d.cross(forward), -lateral)        # positive rotates the foot forward
    hdir = unit(horiz, lateral if li["left"] else -lateral)
    li["lift_axis"] = unit(d.cross(up), forward if li["left"] else -forward)  # positive raises the foot
    li["in_axis"] = unit(d.cross(-hdir), li["swing_axis"])   # positive pulls the foot in under the body
    li["splayed"] = li["splay"] > 0.45
for w in wing_info:
    d = far[w["leaf"]] - head[w["bones"][0]]
    w["dir"] = d.normalized()
    w["flap_axis"] = unit(d.cross(up), forward if w["left"] else -forward)   # positive raises the tip
    w["sweep_axis"] = unit(d.cross(-forward), up)                           # positive sweeps the tip back
    w["elev"] = math.degrees(math.asin(max(-1.0, min(1.0, d.normalized().dot(up)))))
for a in arm_info:
    d = head[a["leaf"]] - head[a["bones"][0]]
    a["swing_axis"] = unit(d.cross(forward), -lateral)
    a["out_axis"] = unit(d.cross(up), forward if a["left"] else -forward)
    a["upper"] = a["bones"][1] if len(a["bones"]) >= 4 else a["bones"][0]
    i = a["bones"].index(a["upper"])
    a["fore"] = a["bones"][i + 1] if i + 1 < len(a["bones"]) else None

# Serpent joints: body position 0 (head) .. 1 (tail) and the sign that turns a local bend into the same
# head-to-tail curvature whichever way the bone tree runs.
serp = []
if motion == "serpent" or serpent_req:
    total = max(1e-6, path_len(spine))
    acc = 0.0
    for i, n in enumerate(spine):
        if i:
            acc += (head[n] - head[spine[i - 1]]).length
        par = byname[n].parent
        if par is None:
            continue
        pidx = spine.index(par.name) if par.name in spine_set else None
        s = 1.0 if (pidx is None or pidx < i) else -1.0
        serp.append({"bone": n, "u": acc / total, "sign": s})

ground = Vector((centre.x, centre.y, lo.z))

# ---- pose accumulator ----------------------------------------------------------------------------------------


class Pose:
    """One frame's pose: per-bone rotations about world axes (at rest) composed in call order, root rotations
    about a world pivot, and world-space offsets for the roots."""

    def __init__(self):
        self.rot = {}
        self.root_q = Quaternion()   # world rotation of the whole body ...
        self.root_t = Vector()       # ... and translation: x -> root_q x + root_t, composed in call order
        self.offset = Vector()
        self.serp = {"yaw": {}, "pitch": {}}  # serpent bends, applied degrees per bone, compensated in finish()

    def bend(self, name, which, deg):
        self.serp[which][name] = self.serp[which].get(name, 0.0) + deg

    def r(self, name, axis, deg):
        if name is None or abs(deg) < 1e-6:
            return
        q = Quaternion(local_axis(name, axis), math.radians(deg))
        self.rot[name] = self.rot.get(name, Quaternion()) @ q

    def root_rotate(self, axis, deg, pivot=None):
        """Turn the whole body about a world axis through pivot (default: the ground under the centre)."""
        c = ground if pivot is None else pivot
        R = Quaternion(axis, math.radians(deg))
        self.root_t = (R @ self.root_t) + c - (R @ c)
        self.root_q = R @ self.root_q

    def root_prepend(self, axis, deg, pivot):
        """Like root_rotate, but applied before the turns already recorded (in the body's rest frame)."""
        R = Quaternion(axis, math.radians(deg))
        t_c = pivot - (R @ pivot)
        self.root_t = (self.root_q @ t_c) + self.root_t
        self.root_q = self.root_q @ R

    def move(self, offset):
        self.offset = self.offset + offset


_local_cache = {}


def local_axis(name, world_axis):
    key = (name, tuple(round(c, 6) for c in world_axis))
    if key not in _local_cache:
        m = (W @ byname[name].matrix_local).to_3x3()
        _local_cache[key] = (m.inverted() @ world_axis).normalized()
    return _local_cache[key]


def apply_pose(P):
    """Write a Pose into the pose bones (no keys)."""
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = P.rot.get(pb.name, Quaternion())
        pb.location = Vector()
    for rb in roots:
        pb = arm.pose.bones[rb.name]
        h = head[rb.name]
        m = (W @ rb.matrix_local).to_3x3()
        # World rotation of the body: bone rotation about the same world axis, plus the move of the bone's head.
        if P.root_q.angle > 1e-6:
            ax_local = (m.inverted() @ P.root_q.axis).normalized()
            pb.rotation_quaternion = Quaternion(ax_local, P.root_q.angle) @ pb.rotation_quaternion
        shift = (P.root_q @ h) + P.root_t - h + P.offset
        pb.location = m.inverted() @ shift


# ---- building blocks -----------------------------------------------------------------------------------------
A = SIZE  # amplitude multiplier for angles and offsets


def drive_leg(P, li, swing, lift, a_swing, a_lift, a_knee):
    b = li["bones"]
    if li["splayed"]:
        P.r(b[0], li["swing_axis"], a_swing * swing)
        P.r(b[0], li["lift_axis"], a_lift * lift)
        if len(b) > 1:
            P.r(b[1], li["in_axis"], 0.5 * a_knee * lift)
        return
    hip = a_swing * swing
    knee = -a_knee * lift
    P.r(b[0], li["swing_axis"], hip + 0.25 * a_knee * lift)
    if len(b) > 1:
        P.r(b[1], li["swing_axis"], knee)
    if len(b) == 3:
        P.r(b[2], li["swing_axis"], -0.7 * (hip + knee))
    elif len(b) >= 4:
        P.r(b[2], li["swing_axis"], 0.6 * a_knee * lift)
        P.r(b[3], li["swing_axis"], -0.5 * (hip + 0.4 * knee))


def legs_cycle(P, u, gait_name, a_swing, a_lift, a_knee, scale=1.0, only=None):
    ph = gait.leg_phases(leg_info, gait_name)
    duty = gait.DUTY[gait_name]
    for i, (li, p) in enumerate(zip(leg_info, ph)):
        if only is not None and i not in only:
            continue
        s, l = gait.foot_cycle(u + p, duty)
        drive_leg(P, li, s * scale, l * scale, a_swing, a_lift, a_knee)


def fold_legs(P, amount, curl=False):
    """Bend every leg (death, crouch, tuck). curl pulls splayed legs in under the body like a dead spider."""
    for li in leg_info:
        b = li["bones"]
        if li["splayed"]:
            P.r(b[0], li["lift_axis"], (-10 if curl else 15) * amount)
            if len(b) > 1:
                P.r(b[1], li["in_axis"], (95 if curl else 30) * amount)
            if len(b) > 2:
                P.r(b[2], li["in_axis"], (40 if curl else 15) * amount)
        else:
            P.r(b[0], li["swing_axis"], 25 * amount)
            if len(b) > 1:
                P.r(b[1], li["swing_axis"], -55 * amount)
            if len(b) > 2:
                P.r(b[2], li["swing_axis"], 35 * amount)


def tuck_legs(P, amount):
    """Legs swept back and folded for flight."""
    for li in leg_info:
        b = li["bones"]
        P.r(b[0], li["swing_axis"], -45 * amount)
        if len(b) > 1:
            P.r(b[1], li["swing_axis"], 50 * amount if not li["splayed"] else 0)


def spine_pitch(P, deg, bones_=None):
    """Positive bends the front of the body down (a bow); spread over the torso bones."""
    bs = [n for n in (bones_ if bones_ is not None else torso) if byname[n].parent is not None] or list(bones_ or torso)[:1]
    for n in bs:
        P.r(n, lateral, deg / max(1, len(bs)))


def spine_yaw(P, deg, bones_=None):
    bs = [n for n in (bones_ if bones_ is not None else torso) if byname[n].parent is not None] or list(bones_ or torso)[:1]
    for n in bs:
        P.r(n, up, deg / max(1, len(bs)))


def head_pitch(P, deg):
    for n in head_bones[:1]:
        P.r(n, lateral, deg)


def head_yaw(P, deg):
    for n in head_bones[:1]:
        P.r(n, up, deg)


def tail_wave(P, u, deg, freq=1.0, lag=0.6):
    for i, n in enumerate(tail_chain):
        P.r(n, up, deg * math.sin(2 * math.pi * freq * u - i * lag))


def tail_lift(P, deg):
    for n in tail_chain[:2]:
        P.r(n, lateral, -deg / 2)


def arms_swing(P, u, deg, elbow=0.0, phase_from_legs=True):
    for a in arm_info:
        ph = 0.5 if a["left"] else 0.0  # opposite to the same-side leg
        s = math.sin(2 * math.pi * (u + ph)) if phase_from_legs else 0.0
        P.r(a["upper"], a["swing_axis"], deg * s)
        if a["fore"] and elbow:
            P.r(a["fore"], a["swing_axis"], elbow * (0.6 + 0.4 * max(0.0, s)))


def arms_relax(P, deg):
    """Bring A-pose arms down toward the sides."""
    for a in arm_info:
        P.r(a["upper"], a["out_axis"], -deg)


def wing_pose(P, w, flap_deg, sweep_deg=0.0, fold=0.0, lag_curve=None):
    """flap_deg raises the whole wing (negative lowers), sweep_deg sweeps it back, fold bends the outer bones in.
    lag_curve(k) gives an extra flap for bone k >= 1 so the outer wing trails the stroke."""
    b = w["bones"]
    P.r(b[0], w["flap_axis"], flap_deg)
    if sweep_deg:
        P.r(b[0], w["sweep_axis"], sweep_deg)
    for k in range(1, len(b)):
        extra = lag_curve(k) if lag_curve else 0.0
        P.r(b[k], w["flap_axis"], extra)
        if fold:
            P.r(b[k], w["sweep_axis"], fold / max(1, len(b) - 1) * (1.6 if k == 1 else 1.0))


def wings_folded(P, amount):
    for w in wing_info:
        wing_pose(P, w, -20 * amount, sweep_deg=40 * amount, fold=70 * amount)


def wave_body(P, t, amp, waves=1.25):
    for s in serp:
        P.bend(s["bone"], "yaw", s["sign"] * gait.wave_angle(s["u"], t, amp, waves))


def serp_front(P, axis, deg, upto=0.35):
    """Bend the head end of a serpent (u < upto) by about deg in all, weighted toward the head. For pitch,
    negative deg raises the head (checked by eye on a head-rooted snake; finish_serpent keeps the rest down)."""
    which = "yaw" if axis is up else "pitch"
    if which == "pitch":
        deg = -deg
    front = [s for s in serp if s["u"] < upto]
    wsum = sum(1.0 - s["u"] / upto for s in front) or 1.0
    for s in front:
        P.bend(s["bone"], which, s["sign"] * deg * (1.0 - s["u"] / upto) / wsum)


_serp_anc = {}


def _abs_angle(bends, name):
    """Sum of the bends on a bone and its spine ancestors: how far that segment turns from rest."""
    if name not in _serp_anc:
        chain, b = [], byname[name]
        while b is not None and b.name in spine_set:
            chain.append(b.name)
            b = b.parent
        _serp_anc[name] = chain
    return sum(bends.get(n, 0.0) for n in _serp_anc[name])


def finish_serpent(P):
    """Write serpent bends as bone rotations, then turn the whole body back so the bends change its shape only:
    the yaw wave keeps the body's mean heading, a pitch bend keeps the tail on the ground. Without this a skeleton
    rooted at the head would swing its tail through the air for every head movement."""
    if not serp:
        return
    for which, axis in (("yaw", up), ("pitch", lateral)):
        bends = P.serp[which]
        if not bends:
            continue
        for n, d in bends.items():
            P.r(n, axis, d)
        if which == "yaw":
            mean = sum(_abs_angle(bends, s["bone"]) for s in serp) / len(serp)
            P.root_prepend(up, -mean, Vector((centre.x, centre.y, lo.z)))
        else:
            # Pin the first unbent joint behind the bends, so only the head end lifts.
            bent_u = max(s["u"] for s in serp if abs(bends.get(s["bone"], 0.0)) > 1e-6) if any(bends.values()) else 0.0
            pin_s = next((s for s in serp if s["u"] > bent_u), serp[-1])
            P.root_prepend(lateral, -_abs_angle(bends, pin_s["bone"]), head[pin_s["bone"]])


def hind_pivot():
    hind = [li for li in leg_info if not li["front"]]
    if not hind:
        return ground - forward * 0.3 * length
    p = sum((low[li["leaf"]] for li in hind), Vector()) / len(hind)
    return Vector((p.x, p.y, lo.z))


# ---- clips ---------------------------------------------------------------------------------------------------
# Each clip is fn(P, t, u_frames) where t is 0..1 over the clip's motion frames (held at 1 during a death hold).
n_legs = len(leg_info)
walk_gait = gait.gait_for(n_legs)
run_gait = gait.gait_for(n_legs, run=True)
biped = kind == "biped" and motion in ("biped", "winged", "unknown")
amp_walk = {"biped": (24, 0, 38), "quadruped": (20, 0, 34), "multileg": (16, 18, 30)}
_amp_key = "multileg" if n_legs >= 6 else ("quadruped" if n_legs >= 4 else "biped")
a_sw, a_li, a_kn = (x * A for x in amp_walk[_amp_key])


def clip_idle(P, t):
    breath = math.sin(2 * math.pi * t)
    P.move(up * 0.006 * height * breath * A)
    if motion == "serpent":
        wave_body(P, t, 0.25 * gait.wave_amplitude_deg(len(serp)) * A, waves=0.8)
        serp_front(P, lateral, -4 * A * (0.5 + 0.5 * math.sin(2 * math.pi * t)))
        return
    spine_pitch(P, 1.5 * A * breath)
    head_pitch(P, -2.0 * A * breath)
    head_yaw(P, 8.0 * A * math.sin(2 * math.pi * t + 0.7))
    tail_wave(P, t, 7.0 * A)
    if biped:
        # Slow weight shift from foot to foot, a little torso twist and breathing in the arms.
        P.move(lateral * 0.012 * height * A * math.sin(2 * math.pi * t))
        spine_yaw(P, 3 * A * math.sin(2 * math.pi * t + 0.5))
        for n in spine[1:2]:
            P.r(n, forward, -3 * A * math.sin(2 * math.pi * t))
        arms_relax(P, (6 + 3 * breath) * A)
        arms_swing(P, t, 2 * A, elbow=(8 + 4 * breath) * A, phase_from_legs=False)
    if wing_info:
        wings_folded(P, 1.0)
        for w in wing_info:
            P.r(w["bones"][0], w["flap_axis"], 3 * A * breath)
    if n_legs >= 6:
        for i, li in enumerate(leg_info):
            P.r(li["bones"][0], li["lift_axis"], 3 * A * max(0.0, math.sin(2 * math.pi * t + i * 1.7)) ** 4)


def _walk_like(P, t, run):
    if motion == "serpent" or (n_legs == 0 and len(serp) >= 3):
        wave_body(P, t, gait.wave_amplitude_deg(len(serp)) * A * (1.3 if run else 1.0), waves=1.25)
        return
    if n_legs == 0:
        spine_yaw(P, 4 * A * math.sin(2 * math.pi * t))
        P.move(up * 0.01 * height * A * gait.body_bob(t, 2))
        return
    g = run_gait if run else walk_gait
    stride = gait.RUN_STRIDE if run else 1.0
    legs_cycle(P, t, g, a_sw * stride, a_li * stride, a_kn * (1.35 if run else 1.0))
    steps = 2 if g in ("alternate", "run2", "trot", "tripod", "alternating") else (4 if g == "walk4" else (1 if g == "gallop" else 2))
    bob_amp = (0.045 if run else 0.018) * height * A
    if biped:
        P.move(up * bob_amp * (gait.body_bob(t, 2) - 0.5))
        spine_yaw(P, (6 if run else 4) * A * math.sin(2 * math.pi * t))
        spine_pitch(P, (12 if run else 3) * A)
        head_pitch(P, -(6 if run else 2) * A)
        arms_relax(P, 10 * A)
        arms_swing(P, t, (38 if run else 22) * A, elbow=(75 if run else 12) * A)
    elif g == "gallop":
        # Spine flexes and extends once per stride; the body rocks nose-down when the front feet land.
        hind = gait.foot_cycle(t, gait.DUTY["gallop"])[0]
        spine_pitch(P, -10 * A * hind)
        P.root_rotate(lateral, 4 * A * math.sin(2 * math.pi * t), ground)
        P.move(up * bob_amp * (0.5 + 0.5 * math.sin(2 * math.pi * t + 1.0)))
        head_pitch(P, 6 * A * hind)
        tail_lift(P, 12 * A)
        tail_wave(P, t, 8 * A)
    else:
        P.move(up * bob_amp * (gait.body_bob(t, steps) - 0.5))
        spine_yaw(P, (4 if n_legs < 6 else 2) * A * math.sin(2 * math.pi * t))
        head_pitch(P, 3 * A * math.sin(4 * math.pi * t))
        tail_wave(P, t, (14 if run else 10) * A)
        if run:
            spine_pitch(P, 4 * A)
    if wing_info:
        wings_folded(P, 1.0)
        for w in wing_info:
            P.r(w["bones"][0], w["flap_axis"], 4 * A * math.sin(4 * math.pi * t))


def clip_walk(P, t):
    _walk_like(P, t, False)


def clip_run(P, t):
    _walk_like(P, t, True)


def clip_slither(P, t):
    wave_body(P, t, gait.wave_amplitude_deg(len(serp)) * A, waves=1.25)


def _turn(P, t, sign):
    yaw = 90.0 * gait.smoothstep(t) * sign
    P.root_rotate(up, yaw, ground)
    if motion == "serpent" or (n_legs == 0 and serp):
        bend = 30.0 * A * math.sin(math.pi * t) * sign
        for s in serp:
            P.bend(s["bone"], "yaw", s["sign"] * -bend / max(1, len(serp)) * 2.0 * (1.0 - s["u"]))
        wave_body(P, t, 0.5 * gait.wave_amplitude_deg(len(serp)) * A)
        return
    if n_legs:
        g = "trot" if n_legs in (4, 5) else walk_gait
        legs_cycle(P, t * 2, g, 0.6 * a_sw, a_li, 0.9 * a_kn)
    P.move(up * 0.01 * height * A * (gait.body_bob(t, 4) - 0.5))
    head_yaw(P, 20 * A * sign * math.sin(math.pi * min(1.0, t * 1.4)))
    spine_yaw(P, 8 * A * sign * math.sin(math.pi * t))
    tail_wave(P, t, 12 * A)
    if biped:
        arms_relax(P, 10 * A)
        arms_swing(P, t * 2, 10 * A, elbow=10 * A)
    if wing_info:
        wings_folded(P, 1.0)


def clip_turn_left(P, t):
    _turn(P, t, 1.0)


def clip_turn_right(P, t):
    _turn(P, t, -1.0)


def clip_attack(P, t):
    windup = gait.envelope(t, [(0.0, 0), (0.35, 1), (0.5, 0), (1.0, 0)])
    strike = gait.envelope(t, [(0.0, 0), (0.35, 0), (0.5, 1), (0.62, 1), (1.0, 0)])
    if motion == "serpent":
        serp_front(P, lateral, A * (-55 * windup - 12 * strike))
        P.move(forward * length * A * (-0.06 * windup + 0.12 * strike))
        wave_body(P, t, 0.3 * gait.wave_amplitude_deg(len(serp)) * A)
        return
    if biped:
        right = [a for a in arm_info if not a["left"]] or arm_info
        for a in right:
            P.r(a["upper"], a["swing_axis"], A * (150 * windup + 50 * strike))
            if a["fore"]:
                P.r(a["fore"], a["swing_axis"], A * (60 * windup + 5 * strike))
        for a in arm_info:
            if a not in right:
                P.r(a["upper"], a["swing_axis"], A * (-15 * windup + 20 * strike))
        spine_pitch(P, A * (-10 * windup + 22 * strike))
        spine_yaw(P, A * (-20 * windup + 25 * strike))
        head_pitch(P, A * (-6 * windup + 8 * strike))
        P.move(forward * height * A * 0.05 * strike - up * height * 0.03 * A * strike)
        for li in leg_info:  # a short step in with the lead leg, the back leg bends
            lead = not li["left"]
            P.r(li["bones"][0], li["swing_axis"], A * (20 if lead else -12) * strike)
            if len(li["bones"]) > 1:
                P.r(li["bones"][1], li["swing_axis"], -A * (15 if lead else 22) * strike)
        return
    # Quadrupeds, multileg and winged: rock back, then lunge with the head down and the forelimbs raised.
    rear_up = A * (8 * windup + 18 * strike)
    P.root_rotate(lateral, -rear_up, hind_pivot())
    P.move(forward * length * A * (-0.05 * windup + 0.14 * strike))
    spine_pitch(P, A * (-6 * windup + 10 * strike))
    head_pitch(P, A * (-18 * windup + 30 * strike))
    fronts = [i for i, li in enumerate(leg_info) if li["front"]]
    if n_legs >= 6:  # spiders raise the front pair only
        fronts = sorted(range(n_legs), key=lambda i: -leg_info[i]["fwd"])[:2]
    for i, li in enumerate(leg_info):
        b = li["bones"]
        if i in fronts:
            if li["splayed"]:
                P.r(b[0], li["lift_axis"], A * (45 * windup + 30 * strike))
                P.r(b[0], li["swing_axis"], A * (25 * windup + 20 * strike))
            else:
                P.r(b[0], li["swing_axis"], A * (20 * windup + 55 * strike))
                if len(b) > 1:
                    P.r(b[1], li["swing_axis"], -A * (45 * windup + 30 * strike))
        else:
            P.r(b[0], li["swing_axis"], -A * 10 * (windup + strike))
            if len(b) > 1 and not li["splayed"]:
                P.r(b[1], li["swing_axis"], -A * 15 * windup)
    tail_lift(P, A * 15 * (windup + strike))
    tail_wave(P, t, A * 8)
    if wing_info:
        for w in wing_info:
            wing_pose(P, w, A * (35 * windup + 15 * strike) - 20 * (1 - windup - strike), sweep_deg=10 * (1 - windup - strike))


def clip_hit(P, t):
    k = gait.envelope(t, [(0.0, 0), (0.22, 1), (1.0, 0)])
    wob = math.sin(2 * math.pi * 2.5 * t) * (1 - t)
    if motion == "serpent":
        serp_front(P, lateral, -A * 40 * k)
        serp_front(P, up, A * 25 * wob)
        P.move(-forward * length * A * 0.05 * k)
        return
    if biped:
        spine_pitch(P, -A * 18 * k)
        head_pitch(P, -A * 22 * k + 6 * wob)
        P.move(-forward * height * A * 0.04 * k)
        for a in arm_info:
            P.r(a["upper"], a["swing_axis"], -A * 25 * k)
            P.r(a["upper"], a["out_axis"], A * 15 * k)
            if a["fore"]:
                P.r(a["fore"], a["swing_axis"], A * 30 * k)
        for li in leg_info:
            if len(li["bones"]) > 1:
                P.r(li["bones"][0], li["swing_axis"], A * 10 * k)
                P.r(li["bones"][1], li["swing_axis"], -A * 18 * k)
        P.move(-up * height * A * 0.02 * k)
        return
    P.root_rotate(lateral, -A * 8 * k, hind_pivot())
    P.move(-forward * length * A * 0.06 * k)
    spine_pitch(P, -A * 10 * k)
    head_pitch(P, -A * 28 * k + 6 * A * wob)
    spine_yaw(P, A * 6 * wob)
    for li in leg_info:
        b = li["bones"]
        if li["front"] and not li["splayed"]:
            P.r(b[0], li["swing_axis"], A * 15 * k)
        if li["splayed"]:
            P.r(b[0], li["lift_axis"], A * 10 * k)
    tail_lift(P, -A * 10 * k)
    if wing_info:
        for w in wing_info:
            wing_pose(P, w, -20 + A * 35 * k, sweep_deg=40 * (1 - k), fold=70 * (1 - k))


_death_drop = [None]


def _death_pose(P, t, drop):
    fall = gait.envelope(t, [(0.0, 0), (0.2, 0), (0.85, 1)])
    buckle = gait.envelope(t, [(0.0, 0), (0.3, 1)])
    settle = math.sin(math.pi * gait.envelope(t, [(0.8, 0), (1.0, 1)])) * 0.04
    if motion == "serpent":
        # Belly up: roll half a turn about the body line while the wave dies out.
        _zs = sorted(head[n].z for n in spine)
        P.root_rotate(forward, 180 * fall, Vector((centre.x, centre.y, _zs[len(_zs) // 2])))
        wave_body(P, t, gait.wave_amplitude_deg(len(serp)) * A * (1 - fall))
        serp_front(P, lateral, -20 * buckle * (1 - fall))
    elif biped:
        P.root_rotate(lateral, -90 * fall, ground - forward * 0.12 * height)
        for li in leg_info:
            b = li["bones"]
            P.r(b[0], li["swing_axis"], 30 * buckle * (1 - 0.6 * fall))
            if len(b) > 1:
                P.r(b[1], li["swing_axis"], -55 * buckle * (1 - 0.7 * fall))
        spine_pitch(P, 15 * buckle - 10 * fall)
        head_pitch(P, -20 * fall + 15 * buckle * (1 - fall))
        for a in arm_info:
            P.r(a["upper"], a["out_axis"], 35 * fall)
            P.r(a["upper"], a["swing_axis"], 30 * fall)
    else:
        # Legs buckle, then the body rolls onto its right side (quadrupeds) or settles flat with the legs curled
        # in (multileg).
        if n_legs >= 6:
            fold_legs(P, buckle * 0.4 + 0.6 * fall, curl=True)
            P.root_rotate(lateral, 4 * fall, ground)
        else:
            fold_legs(P, buckle * (1 - 0.6 * fall))
            P.root_rotate(forward, 90 * fall, ground - lateral * 0.5 * span * (0.6 if wing_info else 1.0))
        head_pitch(P, 25 * fall)
        spine_pitch(P, 8 * buckle)
        tail_wave(P, 0.25, 15 * (1 - fall))
        if wing_info:
            for w in wing_info:
                wing_pose(P, w, -20 - 10 * fall, sweep_deg=40, fold=70 + 20 * fall)
    P.move(up * (drop * fall + settle * height * fall * 0.3))


def clip_death(P, t):
    if _death_drop[0] is None:
        _death_drop[0] = ground_fix(lambda Q: (_death_pose(Q, 1.0, 0.0), finish_serpent(Q)))
    _death_pose(P, t, _death_drop[0])


def clip_flap(P, t):
    f = gait.flap_curve(t)
    for w in wing_info:
        wing_pose(P, w, 38 * A * f, lag_curve=lambda k: 10 * A * gait.flap_curve(t - 0.07 * k) * (0.6 ** (k - 1)))
    tuck_legs(P, 1.0)
    P.move(up * 0.03 * height * A * -gait.flap_curve(t - 0.15))
    head_pitch(P, 4 * A * f)
    tail_wave(P, t, 6 * A)
    tail_lift(P, 4 * A * f)


def clip_glide(P, t):
    bank = math.sin(2 * math.pi * t)
    for w in wing_info:
        level = -w["elev"] * 0.8
        flutter = 2.5 * A * math.sin(2 * math.pi * 3 * t + (0 if w["left"] else 1.0))
        wing_pose(P, w, level + flutter, lag_curve=lambda k: 1.5 * A * math.sin(2 * math.pi * 3 * t - k))
    P.root_rotate(forward, 7 * A * bank, Vector((centre.x, centre.y, centre.z)))
    tuck_legs(P, 1.0)
    head_yaw(P, 5 * A * bank)
    tail_wave(P, t, 5 * A)
    P.move(up * 0.01 * height * math.sin(4 * math.pi * t))


def clip_takeoff(P, t):
    crouch = gait.envelope(t, [(0.0, 0), (0.25, 1), (0.42, 0)])
    leap = gait.envelope(t, [(0.25, 0), (0.45, 1), (1.0, 1)])
    # Wings go up during the crouch, beat hard twice, and finish exactly where the flap cycle starts.
    if t < 0.25:
        wf = 45 * A * gait.envelope(t, [(0.0, -0.45), (0.25, 1.2)])
    else:
        u = (t - 0.25) / 0.75 * 2.0
        wf = 45 * A * gait.flap_curve(u) * (1.0 + 0.2 * (1 - leap))
    for w in wing_info:
        fold0 = 1.0 - gait.envelope(t, [(0.0, 0), (0.2, 1)])
        wing_pose(P, w, wf + -20 * fold0, sweep_deg=40 * fold0, fold=70 * fold0,
                  lag_curve=lambda k: 18 * A * gait.flap_curve((t - 0.25) / 0.75 * 2.0 - 0.07 * k) * (0.7 ** (k - 1)) * leap)
    fold_legs(P, 0.7 * crouch)
    tuck_legs(P, gait.envelope(t, [(0.45, 0), (0.9, 1)]))
    P.move(-up * 0.08 * height * A * crouch)
    spine_pitch(P, -10 * A * leap * (1 - gait.envelope(t, [(0.6, 0), (1.0, 1)])))
    head_pitch(P, -6 * A * crouch)


def clip_land(P, t):
    air = 1.0 - gait.envelope(t, [(0.0, 0), (0.45, 1)])
    brake = gait.envelope(t, [(0.0, 0), (0.25, 1), (0.6, 0)])
    impact = gait.envelope(t, [(0.4, 0), (0.55, 1), (0.85, 0)])
    rest = gait.envelope(t, [(0.5, 0), (1.0, 1)])
    for w in wing_info:
        wf = 45 * A * (air * (1 - brake)) + 30 * A * brake  # starts at the flap cycle's first pose
        wing_pose(P, w, wf * (1 - rest) - 20 * rest - 10 * impact, sweep_deg=40 * rest - 15 * brake, fold=70 * rest)
    tuck_legs(P, air * (1 - brake))
    fold_legs(P, 0.6 * impact)
    P.root_rotate(lateral, -12 * A * brake, hind_pivot())
    P.move(-up * 0.07 * height * A * impact)
    head_pitch(P, 8 * A * impact)
    tail_lift(P, 10 * A * brake)


CLIP_FNS = {"idle": clip_idle, "walk": clip_walk, "run": clip_run, "slither": clip_slither, "turn_left": clip_turn_left,
            "turn_right": clip_turn_right, "attack": clip_attack, "hit": clip_hit, "death": clip_death, "flap": clip_flap,
            "glide": clip_glide, "takeoff": clip_takeoff, "land": clip_land}


# ---- ground fix for death ------------------------------------------------------------------------------------


def lowest_z(P):
    arm.animation_data_create()
    arm.animation_data.action = None
    apply_pose(P)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    z = None
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        for v in me.vertices:
            vz = (mw @ v.co).z
            z = vz if z is None or vz < z else z
        ev.to_mesh_clear()
    return z if z is not None else lo.z


_rest_low = [None]


def ground_fix(pose_fn):
    """Vertical offset that puts the lowest vertex of pose_fn's pose on the rest pose's ground."""
    if _rest_low[0] is None:
        _rest_low[0] = lowest_z(Pose())
    P = Pose()
    pose_fn(P)
    return _rest_low[0] - lowest_z(P)


# ---- actions and NLA -----------------------------------------------------------------------------------------
table = gait.clip_table(clips, motion, fps, SPEED)
poses = {}
for row in table:
    fn = CLIP_FNS[row["name"]]
    n = row["motion_frames"]
    frames = []
    for f in range(row["frame_end"] + 1):
        P = Pose()
        fn(P, min(1.0, f / n))
        finish_serpent(P)
        frames.append(P)
    poses[row["name"]] = frames
keyed = set()
for frames in poses.values():
    for P in frames:
        keyed.update(P.rot)
keyed.update(r.name for r in roots)


def make_action(name, frames, loop):
    act = bpy.data.actions.new(name)
    arm.animation_data_create()
    arm.animation_data.action = act
    for f, P in enumerate(frames):
        apply_pose(P)
        for nm in keyed:
            pb = arm.pose.bones[nm]
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            if byname[nm].parent is None:
                pb.keyframe_insert("location", frame=f + 1)
    if hasattr(act, "use_frame_range"):
        act.use_frame_range = True
        act.frame_start, act.frame_end = 1, len(frames)
    if hasattr(act, "use_cyclic"):
        act.use_cyclic = bool(loop)
    return act


arm.animation_data_create()
acts = [make_action(row["name"], poses[row["name"]], row["loop"]) for row in table]
arm.animation_data.action = None
for pb in arm.pose.bones:
    pb.rotation_quaternion = Quaternion()
    pb.location = Vector()
for a in acts:
    track = arm.animation_data.nla_tracks.new()
    track.name = a.name
    strip = track.strips.new(a.name, 1, a)
    strip.name = a.name
    if hasattr(strip, "action_slot") and len(getattr(a, "slots", [])) > 0:
        strip.action_slot = a.slots[0]
scene.frame_start, scene.frame_end = 1, max([r["frame_end"] + 1 for r in table] or [fps * 2])

# ---- probes: does each clip move what it should? -------------------------------------------------------------


def solo(name):
    for t in arm.animation_data.nla_tracks:
        t.mute = (t.name != name)


def sample(name, frames_):
    solo(name)
    out = []
    for f in frames_:
        scene.frame_set(f)
        out.append({pb.name: pb.matrix.copy() for pb in arm.pose.bones})
    return out


def _angle_between(q0, q1):
    a = q0.rotation_difference(q1).angle
    return math.degrees(min(a, 2 * math.pi - a))


def max_delta(samples, bone):
    q0 = samples[0][bone].to_quaternion()
    return max(_angle_between(q0, s[bone].to_quaternion()) for s in samples)


probes = {}
rows = {r["name"]: r for r in table}
for name, row in rows.items():
    fr = [1 + int(round(row["frame_end"] * i / 8)) for i in range(9)]
    S = sample(name, fr)
    moved = [b for b in keyed if max_delta(S, b) > 2.0]
    probes[name] = {"bones_moved": len(moved)}
    if name in ("walk", "run") and leg_info:
        legs_moved = sum(1 for li in leg_info if max(max_delta(S, b) for b in li["bones"]) > 4.0)
        probes[name]["legs_moved"] = legs_moved
        probes[name]["legs_total"] = len(leg_info)
    if name in ("flap", "glide", "takeoff", "land") and wing_info:
        probes[name]["wing_delta_deg"] = round(max(max_delta(S, w["bones"][0]) for w in wing_info), 2)
    if name in ("slither", "walk") and motion == "serpent" and len(serp) >= 4:
        n = row["motion_frames"]
        solo(name)
        sa, sb = serp[len(serp) // 4], serp[(3 * len(serp)) // 4]
        a_, b_ = sa["bone"], sb["bone"]
        sgn = {a_: sa["sign"], b_: sb["sign"]}
        series = {a_: [], b_: []}
        for f in range(n):
            scene.frame_set(1 + f)
            for bn in series:
                q = arm.pose.bones[bn].matrix_basis.to_quaternion()
                series[bn].append(sgn[bn] * 2 * math.atan2(Vector((q.x, q.y, q.z)).dot(local_axis(bn, up)), q.w))
        peak = {bn: max(range(n), key=lambda i: s[i]) for bn, s in series.items()}
        probes[name]["wave_lag_frames"] = (peak[b_] - peak[a_]) % n
        probes[name]["wave_period_frames"] = n
for t in arm.animation_data.nla_tracks:
    t.mute = False
report["probes"] = probes
if "walk" in probes and "legs_moved" in probes["walk"]:
    report["legs_moved"] = probes["walk"]["legs_moved"]
    report["legs_total"] = probes["walk"]["legs_total"]
# Backward compatible walk_probe: the first leg's top bone over a quarter walk cycle.
if "walk" in rows:
    _probe = leg_info[0]["bones"][0] if leg_info else roots[0].name
    S = sample("walk", [1, 1 + max(1, rows["walk"]["motion_frames"] // 4)])
    report["walk_probe"] = {"bone": _probe, "angle": round(math.radians(max_delta(S, _probe)), 3)}
    print("WALK_PROBE", _probe, report["walk_probe"]["angle"])
    for t in arm.animation_data.nla_tracks:
        t.mute = False
report["clip_table"] = table

# ---- export --------------------------------------------------------------------------------------------------
scene.frame_set(1)
bpy.ops.object.select_all(action="SELECT")
_gltf_args = dict(filepath=dst, export_format="GLB", use_selection=True, export_animations=bool(table),
                  export_animation_mode="NLA_TRACKS", export_force_sampling=True, export_apply=False)
try:  # clips start at time 0, matching the sidecar's frame_start
    bpy.ops.export_scene.gltf(**_gltf_args, export_anim_slide_to_zero=True)
except TypeError:
    bpy.ops.export_scene.gltf(**_gltf_args)
base = os.path.splitext(dst)[0]
sidecar = {"version": 1, "fps": fps, "motion": motion, "requested_motion": motion_arg, "size": SIZE, "speed": SPEED,
           "root_motion": False, "clips": table, "missing": missing, "unknown": unknown_clips,
           "wings_found": wings_found, "legs": len(leg_info)}
with open(base + "_clips.json", "w") as f:
    json.dump(sidecar, f, indent=1)


def _jsonable(o):
    if isinstance(o, Vector):
        return [round(c, 5) for c in o]
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


with open(base + "_anim.json", "w") as f:
    json.dump(_jsonable(report), f, indent=1)
print("PROCANIM_OK", kind, motion, "legs", len(leg_info), "wings", len(wing_info), "spine", len(spine), "tail", len(tail_chain),
      "clips", ",".join(clips), "missing", ",".join(missing))
