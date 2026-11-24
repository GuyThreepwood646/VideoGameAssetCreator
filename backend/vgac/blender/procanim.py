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
from mathutils import Matrix, Quaternion, Vector  # noqa: E402

# ---- arguments -----------------------------------------------------------------------------------------------
_VALUE_FLAGS = ("--motion", "--clips", "--size", "--speed", "--arm-pose", "--hand-pose", "--finger-curl")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
src, dst = os.path.abspath(_pos[0]), os.path.abspath(_pos[1])
fps = int(_pos[2]) if len(_pos) > 2 else 24
motion_arg = _common.opt("--motion", None)
clips_arg = _common.opt("--clips", None)
SIZE = gait.factor(_common.opt("--size", None), gait.MOVE_SIZE)
SPEED = gait.factor(_common.opt("--speed", None), gait.MOVE_SPEED)
ARM_POSE = gait.arm_pose_choice(_common.opt("--arm-pose", None))  # close, relaxed (default) or open
NEUTRAL_POSE = _common.flag("--neutral-pose")  # D9: add the one-frame neutral pose clip (humanoids only)
HAND_POSE = gait.hand_pose_choice(_common.opt("--hand-pose", None))  # open, relaxed (default) or fist (WP-76)
FINGER_CURL = gait.finger_curl_amount(_common.opt("--finger-curl", None))  # scales the hand shape (WP-76)

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
# Automatic serpent: a rig whose only leaf ends one long bone chain has no limbs. Without this rule it reads as one leg
# with motion unknown, since serpent motion is only chosen by an explicit request. With the motion automatic and one
# leaf, it is read as --motion serpent instead. Thresholds: at least 8 bones on the chain, and a root-to-tip path of at
# least 2.5 rig heights. Two or more leaves never reach this rule, so no rig with limbs changes.
auto_serpent = False
if _req == "auto" and len(leaves) == 1 and not serpent_req:
    _chain = chain_to_root(leaves[0])
    _path = sum((head[a.name] - head[b.name]).length for a, b in zip(_chain, _chain[1:]))
    auto_serpent = len(_chain) >= 8 and _path >= 2.5 * height
serpent_req = serpent_req or auto_serpent

# ---- legs ----------------------------------------------------------------------------------------------------
# Leg chains: leaves whose head sits in the bottom 20 percent of the height, reduced to the lowest leaf per limb
# cluster (cluster by horizontal distance). Every cluster is kept (G31: spiders move all eight legs).
leg_leaves = [] if serpent_req else [b for b in leaves if zrel(low[b.name]) < 0.2]
# A leaf is a limb only if some bone on its branch carries a vertex weight above 0 (rig detection fix, 2026-11-22). The
# branch is the leaf and the bones above it up to the first junction (an ancestor with more than one child), or up to
# the root. A helper branch hung off the root with no weights (a pole or IK target) is not a limb; an unweighted toe or
# _End leaf at the end of a weighted leg is still a leg.
_weighted = set()
for _o in meshes:
    _names = {g.index: g.name for g in _o.vertex_groups}
    for _v in _o.data.vertices:
        for _g in _v.groups:
            if _g.weight > 0 and _g.group in _names:
                _weighted.add(_names[_g.group])


def _branch(b):
    out = [b]
    x = b
    while x.parent is not None and len(x.parent.children) <= 1:
        x = x.parent
        out.append(x)
    return out


if _weighted:  # a rig with no weighted mesh at all keeps every leaf, as before
    leg_leaves = [b for b in leg_leaves if any(x.name in _weighted for x in _branch(b))]
# A foot bone that hangs off the root (not off the shin) and starts at a leg's ankle is that leg's foot, not a
# second leg (rig detection fix, 2026-11-22): a leaf no longer than 0.12 x height, whose head sits within 0.05 x height of
# another leg leaf's low point (the ankle), adds no leg. Long leg chains are the ankle anchors.
_ankles = [b for b in leg_leaves if (tail[b.name] - head[b.name]).length > 0.12 * height]
_feet = {b.name for b in leg_leaves if b not in _ankles and (tail[b.name] - head[b.name]).length <= 0.12 * height
         and any((head[b.name] - low[a.name]).length <= 0.05 * height for a in _ankles)}
leg_leaves = [b for b in leg_leaves if b.name not in _feet]
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
# On a two-legged body a T-posed arm passes every bone test above, so the skin decides (rig-hands-wings, 2026-11-22): a
# wing is a flat sheet, an arm a round tube. Of the vertices weighted to the chain (summed weight 0.5 or more), the
# cross-section square to root-to-tip: thickness over chord (2nd to 98th percentile extents). Measured: arms 0.59 to
# 0.83 (four hand-made humans), wings 0.21 to 0.32 (songbird, two dragons). Fewer than 20 weighted vertices: no reading.
WING_FLAT_MAX = 0.45


def _skin_flatness(chain, root_p, tip_p):
    import numpy as np
    names = set(chain)
    d = tip_p - root_p
    if d.length < 1e-6:
        return None
    dh = np.array(d.normalized())
    pts = []
    for o in meshes:
        gi = {g.index for g in o.vertex_groups if g.name in names}
        if not gi:
            continue
        Wm = o.matrix_world
        for v in o.data.vertices:
            if sum(g.weight for g in v.groups if g.group in gi) >= 0.5:
                pts.append(tuple(Wm @ v.co))
    if len(pts) < 20:
        return None
    q = np.array(pts) - np.array(root_p)
    perp = q - np.outer(q @ dh, dh)
    _, vec = np.linalg.eigh(np.cov(perp.T))

    def ext(x):
        return float(np.quantile(x, 0.98) - np.quantile(x, 0.02))

    chord = ext(perp @ vec[:, 2])
    return ext(perp @ vec[:, 1]) / chord if chord > 1e-9 else None


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
        flat = _skin_flatness(chain, root_p, p) if gait.kind_for_legs(len(leg_info)) == "biped" else None
        if flat is not None and flat > WING_FLAT_MAX:  # a round tube of skin: a T-posed arm, not a wing
            continue
        cands[lat_of(p) > 0].append((reach, chain, b.name, flat))
    for side in (True, False):
        if cands[side]:
            reach, chain, leaf, flat = max(cands[side], key=lambda c: c[:3])
            wing_info.append({"side": "left" if side else "right", "left": side, "leaf": leaf, "bones": chain, "reach": reach})
            if flat is not None:
                wing_info[-1]["flat"] = round(flat, 3)
# Wings come in pairs: a lone sideways chain is not a wing on any body. On a two-legged body a lone candidate is an
# arm that reaches out sideways (WP-17 follow-up: the
# boy's right arm, rest 102 degrees, was taken as a wing, so the wing moves folded that arm alone and it crossed the
# body). Dropped here, before the wing bones are used to find the spine and arms.
if len(wing_info) < 2:
    wing_info = []
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
motion = "serpent" if auto_serpent else gait.resolve_motion(motion_arg, len(leg_info))
legacy = (motion_arg is None or _req == "library") and not auto_serpent
clips, unknown_clips = gait.resolve_clips(motion if motion != "unknown" else "biped", clips_arg, legacy=legacy)
if motion == "none":
    clips = []
missing = [c for c in clips if not gait.clip_supported(c, motion, len(leg_info), wings_found, len(spine))]
clips = [c for c in clips if c not in missing]
if NEUTRAL_POSE and arm_info:  # arm_info is only built for a biped (humanoid), so other characters never get it
    clips = clips + [gait.NEUTRAL_CLIP]

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
    li["foot_index"] = gait.foot_bone_index(li["bones"])
    if li["foot_index"] is not None and li["foot_index"] >= 2:
        d = head[li["bones"][li["foot_index"]]] - head[b0]  # to the ankle: a big cartoon foot is not a splayed leg
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
    # The arm's measured rest angle from straight down (upper arm: shoulder to elbow), and the axis its drop turns about:
    # perpendicular to the upper arm and to up, so the drop changes that angle and nothing else.
    elbow = head[a["fore"]] if a["fore"] else head[a["leaf"]]
    a["rest_deg"] = gait.rest_arm_angle(head[a["upper"]], elbow, up)
    a["shoulder_out"] = abs(lat_of(head[a["upper"]]))
    a["arm_len"] = (tail[a["leaf"]] - head[a["upper"]]).length
    a["drop_axis"] = unit((elbow - head[a["upper"]]).cross(up), a["out_axis"])
# Hips: the mean half width of the legs' top joints; a hand may not cross this line (gait.arm_hold_deg).
hip_half = (sum(abs(lat_of(head[li["bones"][0]])) for li in leg_info) / len(leg_info)) if leg_info else None
report["arm_pose"] = {"choice": ARM_POSE, "hip_half": hip_half, "arms": [
    {"side": a["side"], "rest_deg": round(a["rest_deg"], 2), "shoulder_out": round(a["shoulder_out"], 4),
     "arm_len": round(a["arm_len"], 4),
     "holds": {c: round(gait.arm_hold_deg(c, ARM_POSE, a["shoulder_out"], a["arm_len"], hip_half), 2) for c in gait.ARM_TARGET_DEG}}
    for a in arm_info]}

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


def basis_of(P, name):
    """(rotation, location) a Pose gives one pose bone. Roots also carry the whole-body turn about the same world
    axis and the move of the bone's head."""
    q = P.rot.get(name, Quaternion())
    b = byname[name]
    if b.parent is not None:
        return q, Vector()
    h = head[name]
    m = (W @ b.matrix_local).to_3x3()
    if P.root_q.angle > 1e-6:
        ax_local = (m.inverted() @ P.root_q.axis).normalized()
        q = Quaternion(ax_local, P.root_q.angle) @ q
    shift = (P.root_q @ h) + P.root_t - h + P.offset
    return q, m.inverted() @ shift


def apply_pose(P):
    """Write a Pose into the pose bones (no keys)."""
    for pb in arm.pose.bones:
        q, loc = basis_of(P, pb.name)
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = q
        pb.location = loc


# ---- forward kinematics without the depsgraph (for the leg and limb IK) --------------------------------------
REST = {b.name: b.matrix_local.copy() for b in bones}
REST_INV = {n: m.inverted() for n, m in REST.items()}
REST_W_INV = {n: (W @ m).inverted() for n, m in REST.items()}
TOPO = []


def _topo(b):
    TOPO.append(b.name)
    for c in b.children:
        _topo(c)


for _r in roots:
    _topo(_r)


def fk(P):
    """World matrix of every bone for a Pose, the same as Blender's pose evaluation (no scale, inherited
    rotation)."""
    M = {}
    for n in TOPO:
        q, loc = basis_of(P, n)
        B = Matrix.Translation(loc) @ q.to_matrix().to_4x4()
        p = byname[n].parent
        M[n] = REST[n] @ B if p is None else M[p.name] @ REST_INV[p.name] @ REST[n] @ B
    return {n: W @ m for n, m in M.items()}


def orient(Mw, n):
    return Mw[n].to_3x3().normalized().to_quaternion()


def posed_point(Mw, n, rest_point):
    """Where a rest-pose world point carried by bone n is in the pose."""
    return Mw[n] @ (REST_W_INV[n] @ rest_point)


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


def arms_relax(P, clip, extra=0.0):
    """Drop each upper arm from its measured rest angle to the clip's hold from straight down (gait.arm_drop_deg, with
    the arm_pose setting, the hip clamp and an optional extra such as the idle breath)."""
    for a in arm_info:
        d = gait.arm_drop_deg(a["rest_deg"], clip, ARM_POSE, a["shoulder_out"], a["arm_len"], hip_half, extra)
        P.r(a["upper"], a["drop_axis"], -d)


# ---- relaxed hands (WP-76): finger and wrist curl about the palm-normal axis ---------------------------------------
# Built-in clips leave the fingers at their rest bend, so hands hang flat. Each humanoid hand with five finger chains
# curls toward its palm: proximal, middle and distal joints by the shape below (degrees, scaled by FINGER_SCALE and by
# the finger_curl setting), and the wrist bends a little. The axis is the rest palm normal's rotation axis (f x n, f the
# hand's direction, n the palm side from the thumb as palm_down.py takes it), so a positive turn moves a finger toward
# its palm. Hands with no finger chains (one-lump hands) get no curl.
HAND_SHAPES = {  # (proximal, middle, distal, wrist) at full amount; the hand_pose setting picks one
    "open": (0.0, 0.0, 0.0, 0.0),
    "relaxed": (12.0, 16.0, 10.0, 6.0),
    "fist": (50.0, 65.0, 35.0, 8.0),
}
GRIP_SHAPE = (90.0, 100.0, 60.0, 12.0)  # attack: a closed grip, blended in over the strike (each finger stops short of the palm, see _grip_caps)
TIP_FLOOR = 0.02  # a fingertip stays at least this share of the hand's length in front of the palm plane (knuckle line)
SEG_FLOOR = 0.12  # neighbouring fingers stay at least this share of the hand's length apart (closest points of their joints)
FINGER_SCALE = {"Thumb": 0.6, "Index": 0.75, "Middle": 0.9, "Ring": 1.0, "Pinky": 1.1}  # gentle, rising to the little finger
# A ceiling on a finger's first joint in the grip (degrees), none set: Unity's little finger falls short of the rig in the
# grip (bakeoff/wp76: about 9 degrees on the boy), a ceiling did not close that gap, so the grip keeps the rig's full curl.
GRIP_PROXIMAL_CEILING = {}
_hands_cache = None


def _wpos(name, tail=False):
    """World position of a rest bone's head (or tail)."""
    m = REST[name]
    p = m @ Vector((0, byname[name].length, 0)) if tail else m.translation
    return W @ p


def _hand_rig():
    """Per humanoid hand: the wrist bone, the three bones of each finger found (none on a one-lump hand, which only
    relaxes at the wrist), and the curl axis (world). Built once; empty for non-humanoids."""
    global _hands_cache
    if _hands_cache is not None:
        return _hands_cache
    _hands_cache = []
    for a in arm_info:
        fore = a.get("fore")
        if not fore or fore not in byname:
            continue
        if not fore.endswith("ForeArm"):
            continue
        base = fore[: -len("ForeArm")]  # the palm-down pass hangs the hand off the forearm's twist bones, so name it
        hand = base + "Hand"
        if hand not in byname:
            continue
        fingers = {}
        for f in FINGER_SCALE:
            bs = [f"{base}Hand{f}{k}" for k in (1, 2, 3)]
            if all(b in byname for b in bs):
                fingers[f] = bs
        if not fingers:
            # A one-lump hand (no finger bones): only the wrist relaxes. palm_down.py never turns it, so its palm faces
            # the front of the body; the wrist turns toward the front (f x forward, a positive turn moves f toward it).
            wrist = _wpos(hand)
            f_dir = (_wpos(hand, tail=True) - wrist).normalized()
            n = (forward - f_dir * forward.dot(f_dir)).normalized()
            axis = f_dir.cross(n).normalized()
            _hands_cache.append({"hand": hand, "fingers": {}, "axis": axis, "finger_axes": {}})
            continue
        if "Thumb" not in fingers or not all(k in fingers for k in ("Index", "Middle", "Ring", "Pinky")):
            continue
        wrist = _wpos(hand)
        knuckles = [_wpos(fingers[f][0]) for f in ("Index", "Middle", "Ring", "Pinky")]
        f_dir = ((sum(knuckles, Vector()) / len(knuckles)) - wrist).normalized()
        thumb = _wpos(fingers["Thumb"][0]) - wrist
        t = (thumb - f_dir * thumb.dot(f_dir)).normalized()
        n = f_dir.cross(t).normalized()  # out of a left palm, into a right one (palm_down.py)
        if not a["left"]:
            n = -n
        axis = f_dir.cross(n).normalized()
        # Each finger turns about its own proximal bone's axis (its rest direction x palm normal), so a finger whose
        # rest chain points off the hand's line still curls toward the palm; the hand axis is the fallback.
        finger_axes = {}
        turned_back = []
        for f, bs in list(fingers.items()):
            d = _wpos(bs[0], tail=True) - _wpos(bs[0])
            if f != "Thumb" and d.dot(f_dir) < 0.3 * d.length:  # the thumb points out to the side, so it is not tested
                # A finger chain whose first bone points back toward the wrist (a rig fault from the finger build) is
                # left as the rig has it: curling it would fold it through the palm. Recorded in the report.
                turned_back.append(f)
                del fingers[f]
                continue
            ax = d.cross(n)
            finger_axes[f] = ax.normalized() if ax.length > 0.2 * d.length else axis
        km = sum(knuckles, Vector()) / len(knuckles)
        _hands_cache.append({"hand": hand, "fingers": fingers, "axis": axis, "finger_axes": finger_axes,
                             "turned_back": turned_back, "palm": (km, n, (km - wrist).length), "left": a["left"],
                             "knuckles": [f"{base}Hand{f}1" for f in ("Index", "Middle", "Ring", "Pinky")], "thumb": f"{base}HandThumb1"})
    return _hands_cache


def _finger_turns(f, g, base, amount):
    """The three joint turns (degrees) of finger f at grip blend g: the hand shape scaled by amount and the finger factor,
    with the grip ceiling on the first joint."""
    fp = tuple(b + (G - b) * g for b, G in zip(base[:3], GRIP_SHAPE[:3]))
    k = amount * FINGER_SCALE[f]
    first = k * fp[0]
    if f in GRIP_PROXIMAL_CEILING:
        first = min(first, GRIP_PROXIMAL_CEILING[f])
    return first, k * fp[1], k * fp[2]


def _apply_hand(P, h, grip_by_finger, wrist_grip):
    """One hand's pose at the given grip blends (per finger, and for the wrist), the same turns as hands_curl."""
    base = HAND_SHAPES[HAND_POSE]
    amount = FINGER_CURL
    w = base[3] + (GRIP_SHAPE[3] - base[3]) * wrist_grip
    P.r(h["hand"], h["axis"], amount * w)
    for f, bs in h["fingers"].items():
        g = grip_by_finger.get(f, 0.0)
        fa = h["finger_axes"][f]
        t0, t1, t2 = _finger_turns(f, g, base, amount)
        P.r(bs[0], fa, t0)
        P.r(bs[1], fa, t1)
        P.r(bs[2], fa, t2)


def _tips_and_heads(h, grip_by_finger, wrist_grip):
    """Each finger's joint points (MCP, PIP, DIP, tip) and the palm (knuckle mean, palm normal, hand length), all with
    the pose's own forward kinematics (fk), so the palm turns with the wrist as it does in the moves."""
    P = Pose()
    _apply_hand(P, h, grip_by_finger, wrist_grip)
    Mw = fk(P)
    out = {}
    for f, bs in h["fingers"].items():
        tail = Mw[bs[2]] @ Vector((0, byname[bs[2]].length, 0))
        out[f] = [Mw[bs[0]].translation.copy(), Mw[bs[1]].translation.copy(), Mw[bs[2]].translation.copy(), tail]
    wrist = Mw[h["hand"]].translation.copy()
    knuck = [Mw[k].translation.copy() for k in h["knuckles"] if k in Mw]
    km = sum(knuck, Vector()) / max(len(knuck), 1)
    fdir = (km - wrist).normalized()
    thumb = Mw[h["thumb"]].translation.copy() - wrist
    t = (thumb - fdir * thumb.dot(fdir)).normalized()
    n = fdir.cross(t).normalized()
    if not h["left"]:
        n = -n
    return out, (km, n, (km - wrist).length)


def _grip_caps(h):
    """Per finger, the largest grip blend (0 to 3) that keeps its tip TIP_FLOOR in front of the palm, and every
    neighbouring pair SEG_FLOOR apart at every step of the grip from open to the cap, measured on the pose's own forward
    kinematics (the same turns the moves use). Cached on the hand."""
    if "caps" in h:
        return h["caps"]
    order = list(h["fingers"].keys())
    steps = [i * 0.02 for i in range(151)]
    grips = [i * 0.05 for i in range(21)]
    caps = {}
    for f in order:
        best = 0.0
        for g in steps:
            pts, (km, n, hl) = _tips_and_heads(h, {f: g}, 1.0)  # the wrist at its full grip, the most it reaches
            if (pts[f][3] - km).dot(n) / hl < TIP_FLOOR:
                break
            best = g
        caps[f] = best

    def check(caps_):
        """(worst tip lift, worst pair gap, the pair) over the grip from open to 1 with each finger held to its cap."""
        worst_tip, worst_gap, pair = None, None, None
        for gs in grips:
            fg = {f: min(gs, caps_[f]) for f in order}
            pts, (km, n, hl) = _tips_and_heads(h, fg, gs)
            for f in order:
                lift = (pts[f][3] - km).dot(n) / hl
                worst_tip = lift if worst_tip is None else min(worst_tip, lift)
            for i in range(len(order) - 1):
                a, b = order[i], order[i + 1]
                pa = [(pts[a][k], pts[a][k + 1]) for k in range(3)]
                pb = [(pts[b][k], pts[b][k + 1]) for k in range(3)]
                d = min(_seg_dist(p0, p1, q0, q1) for (p0, p1) in pa for (q0, q1) in pb) / hl
                if worst_gap is None or d < worst_gap:
                    worst_gap, pair = d, (a, b)
        return worst_tip, worst_gap, pair

    for _ in range(400):
        _, gap, pair = check(caps)
        if gap is None or gap >= SEG_FLOOR:
            break
        a, b = pair
        bigger = a if caps[a] >= caps[b] else b
        caps[bigger] = max(0.0, caps[bigger] - 0.02)
    h["caps"] = caps
    return caps


def _seg_dist(p0, p1, q0, q1):
    """Closest distance between segments p0p1 and q0q1."""
    d1 = p1 - p0
    d2 = q1 - q0
    r = p0 - q0
    a = d1.dot(d1)
    e = d2.dot(d2)
    f = d2.dot(r)
    eps = 1e-12
    if a <= eps and e <= eps:
        return (p0 - q0).length
    if a <= eps:
        s_, t_ = 0.0, max(0.0, min(1.0, f / e))
    else:
        c = d1.dot(r)
        if e <= eps:
            s_, t_ = max(0.0, min(1.0, -c / a)), 0.0
        else:
            b_ = d1.dot(d2)
            den = a * e - b_ * b_
            s_ = max(0.0, min(1.0, (b_ * f - c * e) / den)) if den > eps else 0.0
            t_ = (b_ * s_ + f) / e
            if t_ < 0.0:
                t_, s_ = 0.0, max(0.0, min(1.0, -c / a))
            elif t_ > 1.0:
                t_, s_ = 1.0, max(0.0, min(1.0, (b_ - c) / a))
    return ((p0 + d1 * s_) - (q0 + d2 * t_)).length


def hands_curl(P, shape, amount, grip=0.0):
    """Curl every humanoid hand: the wrist by shape, each finger by its own angles scaled by amount. grip (0 to 1)
    blends the fingers toward the closed grip, but no finger goes past its cap (_grip_caps)."""
    if amount <= 0:
        return
    p, m, d, w = shape
    base = HAND_SHAPES[HAND_POSE]
    for h in _hand_rig():
        ax = h["axis"]
        P.r(h["hand"], ax, amount * w)
        caps = _grip_caps(h) if (grip > 0 and h["fingers"]) else {}  # a one-lump hand has no fingers to cap
        for f, bs in h["fingers"].items():
            g = min(grip, caps.get(f, 0.0))
            fa = h["finger_axes"][f]
            t0, t1, t2 = _finger_turns(f, g, base, amount)
            P.r(bs[0], fa, t0)
            P.r(bs[1], fa, t1)
            P.r(bs[2], fa, t2)


def hands_pose(P, grip=0.0):
    """The resting hand (hand_pose setting, scaled by finger_curl), blended toward a closed grip by grip (0 to 1)."""
    base = HAND_SHAPES[HAND_POSE]
    shape = tuple(b + (g - b) * grip for b, g in zip(base, GRIP_SHAPE))
    hands_curl(P, shape, FINGER_CURL, grip)


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


# ---- limb IK: planted feet, settled limbs --------------------------------------------------------------------


def limb_chain(bone_list, leaf_end, foot_index=None):
    """The two-bone part of a limb for the IK: the longest pair of consecutive bones whose end is a real joint
    (the head of the next bone), so a dog's leg uses upper arm and forearm, a person's thigh and shin; a leg with a
    named foot bone always ends the pair at the ankle (gait.ik_pair_start). Bones below the end keep their world
    orientation while the IK moves the pair."""
    if len(bone_list) < 2:
        return None
    joints = [head[n] for n in bone_list] + [leaf_end]
    i = gait.ik_pair_start([(joints[k + 1] - joints[k]).length for k in range(len(joints) - 1)], foot_index)
    l1, l2 = (joints[i + 1] - joints[i]).length, (joints[i + 2] - joints[i + 1]).length
    end_bone = bone_list[i + 2] if i + 2 < len(bone_list) else None
    d0 = (joints[i + 2] - joints[i]).length
    mid_off = joints[i + 1] - (joints[i] + joints[i + 2]) / 2
    return {"a": bone_list[i], "m": bone_list[i + 1], "end_bone": end_bone, "end_rest": joints[i + 2].copy(),
            "l1": l1, "l2": l2, "rest_reach": d0, "below": bone_list[i + 3:] if end_bone else [],
            "bend_rest": mid_off if mid_off.length > 0.03 * (l1 + l2) else None}


for li in leg_info:
    li["ik"] = None if li["splayed"] else limb_chain(li["bones"], low[li["leaf"]], li.get("foot_index"))
for a_ in arm_info:
    # The palm-down pass puts Extra_ForeArmTwist bones between ForeArm and Hand, all starting at the elbow. They are
    # left out of the IK chain so its end is the Hand (the wrist), and the forearm length is elbow to wrist.
    ik_bones = [b for b in a_["bones"] if "ForeArmTwist" not in b]
    i0 = ik_bones.index(a_["upper"])
    a_["ik"] = limb_chain(ik_bones[i0:i0 + 3], head[ik_bones[min(len(ik_bones) - 1, i0 + 2)]]) if len(ik_bones) >= i0 + 3 else None
    if a_["ik"] and a_["ik"]["a"] != a_["upper"]:
        a_["ik"] = None


def chain_points(Mw, ch):
    ha = Mw[ch["a"]].translation
    hm = Mw[ch["m"]].translation
    he = Mw[ch["end_bone"]].translation if ch["end_bone"] else posed_point(Mw, ch["m"], ch["end_rest"])
    return ha, hm, he


def chain_pole(P, Mw, ch, fallback_dir):
    """A point the middle joint should bend toward: the limb's own rest bend carried by the body, else the
    fallback direction (world, before the body's turn)."""
    ha, hm, he = chain_points(Mw, ch)
    if ch["bend_rest"] is not None:
        d = (Mw[ch["a"]].to_3x3().normalized() @ (REST_W_INV[ch["a"]].to_3x3().normalized() @ ch["bend_rest"])).normalized()
    else:
        d = (P.root_q @ fallback_dir).normalized()
    return hm + d * (ch["l1"] + ch["l2"])


def ik_chain(P, ch, target, pole, end_orient=None, max_bend=150.0, Mw=None):
    """Turn the chain's two bones (rotations appended to the Pose) so its end reaches `target` (world), bending
    toward `pole`. end_orient: the world orientation the end bone should have afterwards (None: it rides along).
    -> distance left between the end and the target."""
    Mw = Mw or fk(P)
    ha, hm, he = chain_points(Mw, ch)
    mid, end, _ = gait.two_bone_ik(tuple(ha), tuple(target), ch["l1"], ch["l2"], tuple(pole), max_bend_deg=max_bend)
    mid, end = Vector(mid), Vector(end)
    Oa = orient(Mw, ch["a"])
    r1 = (hm - ha).rotation_difference(mid - ha)
    P.rot[ch["a"]] = P.rot.get(ch["a"], Quaternion()) @ (Oa.inverted() @ r1 @ Oa)
    Om = r1 @ orient(Mw, ch["m"])
    he1 = mid + r1 @ (he - hm)
    r2 = (he1 - mid).rotation_difference(end - mid)
    P.rot[ch["m"]] = P.rot.get(ch["m"], Quaternion()) @ (Om.inverted() @ r2 @ Om)
    if ch["end_bone"] and end_orient is not None:
        Oe = r2 @ r1 @ orient(Mw, ch["end_bone"])
        P.rot[ch["end_bone"]] = P.rot.get(ch["end_bone"], Quaternion()) @ (Oe.inverted() @ end_orient)
    return (end - Vector(target)).length


def set_world_orient(P, name, want, Mw=None):
    Mw = Mw or fk(P)
    O = orient(Mw, name)
    P.rot[name] = P.rot.get(name, Quaternion()) @ (O.inverted() @ want)


REST_ORIENT = {b.name: (W @ b.matrix_local).to_3x3().normalized().to_quaternion() for b in bones}


def foot_geometry(ch):
    """(toe bone or None, horizontal ankle-to-ball length) for heel raises."""
    if not ch or not ch["below"]:
        return None, 0.0
    toe = ch["below"][0]
    d = head[toe] - ch["end_rest"]
    return toe, Vector((d.x, d.y, 0)).length


def heel_raise(toe_len, pitch):
    """How far the ankle rises when the foot pitches: about the ball for toes down (push off), about the heel
    (taken as a third of the ankle-to-ball length behind the ankle) for toes up (heel strike)."""
    if pitch < 0:
        return toe_len * math.sin(math.radians(-pitch))
    return 0.35 * toe_len * math.sin(math.radians(pitch))


def foot_heel(ch):
    """(rest points of the foot's lower skin, the sole's height) for rolling on the heel: the vertices mostly weighted to
    the leg's end bone (the foot, not the toes, which stay flat) within 3 percent of the height of its lowest point, at
    most 400 of them. (None, lo.z) when the foot has no skin of its own; rolled_ankle then estimates the heel."""
    if "heel" not in ch:
        e = ch["end_bone"]
        pts = []
        for o in meshes if e else []:
            gname = {g.index: g.name for g in o.vertex_groups}
            for v in o.data.vertices:
                if v.groups and gname.get(max(v.groups, key=lambda g: g.weight).group) == e:
                    pts.append(o.matrix_world @ v.co)
        if pts:
            z0 = min(p.z for p in pts)
            low = [p for p in pts if p.z <= z0 + 0.03 * height]
            step = max(1, len(low) // 400)
            ch["heel"] = ([tuple(p) for p in low[::step]], z0)
        else:
            ch["heel"] = (None, lo.z)
    return ch["heel"]


def ankle_target(li, fwd_, up_, pitch):
    """Where a leg's IK end goes for a foot path point. A biped foot rolls about a point that stays put (the ball for toes
    down, the heel on the floor for toes up, gait.rolled_ankle), so push-off and heel strike do not slide; other feet keep
    the plain heel raise."""
    ch = li["ik"]
    toe, toe_len = foot_geometry(ch)
    if not biped:
        return ch["end_rest"] + forward * fwd_ + up * (up_ + heel_raise(toe_len, pitch))
    pts, sole_z = foot_heel(ch)
    A = gait.rolled_ankle(tuple(ch["end_rest"]), tuple(head[toe]) if toe else None, sole_z, tuple(forward), tuple(lateral),
                          toe_len, pitch, heel_pts=pts)
    return Vector(A) + forward * fwd_ + up * up_


def plant_feet(P, feet, biped_feet):
    """feet: [(leg, fwd, up, pitch, toe_flat)] -> IK every leg so its end joint follows its foot path (world,
    relative to the rest place), the foot keeps its rest orientation turned by the path's pitch, and a planted
    foot's toes stay flat while the heel lifts."""
    for li, fwd_, up_, pitch, toe_flat in feet:
        ch = li["ik"]
        toe, toe_len = foot_geometry(ch)
        target = ankle_target(li, fwd_, up_, pitch)
        Mw = fk(P)
        pole = chain_pole(P, Mw, ch, forward if biped_feet else -forward)
        want = None
        if ch["end_bone"]:
            want = Quaternion(lateral, math.radians(-pitch)) @ REST_ORIENT[ch["end_bone"]]
        ik_chain(P, ch, target, pole, end_orient=want, max_bend=160.0, Mw=Mw)
        if toe and toe_flat > 0:
            Mw = fk(P)
            cur = orient(Mw, toe)
            set_world_orient(P, toe, cur.slerp(REST_ORIENT[toe], toe_flat), Mw)


def planted_drop(feet, lmax_frac):
    """How far the hips must come down so every planted leg reaches its foot (0 when they all reach)."""
    drop = 0.0
    for li, fwd_, up_, pitch, _ in feet:
        ch = li["ik"]
        if up_ > 1e-6 or ch is None:
            continue
        E = ankle_target(li, fwd_, 0.0, pitch)
        H = head[ch["a"]]
        hor = E - H
        hor.z = 0
        lmax = max(ch["rest_reach"], lmax_frac * (ch["l1"] + ch["l2"]))
        allowed = math.sqrt(max(0.0, lmax * lmax - hor.length_squared))
        drop = max(drop, (H.z - E.z) - allowed)
    return drop


def leg_chain(li):
    """The leg's two-bone chain: the IK chain, or for a splayed leg (no IK) the same geometry worked out once."""
    if li.get("ik"):
        return li["ik"]
    if "stance_chain" not in li:
        li["stance_chain"] = limb_chain(li["bones"], low[li["leaf"]], li.get("foot_index"))
    return li["stance_chain"]


def foot_width(ch):
    """Sideways extent of the vertices mostly weighted to the leg's foot (the end bone and everything below it), at rest."""
    e = ch["end_bone"] or ch["m"]
    names, stack = set(), [byname[e]]
    while stack:
        b = stack.pop()
        names.add(b.name)
        stack.extend(b.children)
    ls = []
    for o in meshes:
        gname = {g.index: g.name for g in o.vertex_groups}
        for v in o.data.vertices:
            if v.groups and gname.get(max(v.groups, key=lambda g: g.weight).group) in names:
                ls.append(lat_of(o.matrix_world @ v.co))
    return (max(ls) - min(ls)) if ls else 0.0


def posed_leg_gaps(P, legs):
    """Clear space between the two legs' skin, measured on the skinned mesh in a pose: slabs at the knee joints'
    height and at mid-thigh, {"knee": (gap, width), "thigh": (gap, width)}."""
    sub = []
    for li in legs:
        names, stack = set(), [byname[leg_chain(li)["a"]]]
        while stack:
            b = stack.pop()
            names.add(b.name)
            stack.extend(b.children)
        sub.append(names)
    Mw = fk(P)
    left = max(range(2), key=lambda i: lat_of(head[leg_chain(legs[i])["a"]]))
    chA, chB = leg_chain(legs[left]), leg_chain(legs[1 - left])
    kz = Mw[chA["m"]].translation.z
    mz = (Mw[chA["a"]].translation.z + kz) / 2
    apply_pose(P)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    slab = 0.02 * height
    out = {}
    cols = {"knee": ([], []), "thigh": ([], [])}
    for o in meshes:
        gname = {g.index: g.name for g in o.vertex_groups}
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        for i, v in enumerate(me.vertices):
            ov = o.data.vertices[i] if i < len(o.data.vertices) else None
            if ov is None or not ov.groups:
                continue
            bn = gname.get(max(ov.groups, key=lambda g: g.weight).group)
            side = 0 if bn in sub[left] else (1 if bn in sub[1 - left] else None)
            if side is None:
                continue
            w = mw @ v.co
            for nm, z in (("knee", kz), ("thigh", mz)):
                if abs(w.z - z) <= slab:
                    cols[nm][side].append(lat_of(w))
        ev.to_mesh_clear()
    for nm, (la, lb) in cols.items():
        out[nm] = None if not la or not lb else (min(la) - max(lb), min(max(la) - min(la), max(lb) - min(lb)))
    return out


def stance_clear(legs, ank, hip, sep, need):
    """True when, with the ankles `sep` apart, the legs' skin keeps the needed clear space at the knee and mid-thigh
    (need: {"knee": space or None, "thigh": ...}) in the idle's pose at the extremes of the weight shift and the breath."""
    tgt = gait.stance_pair(ank, sep)
    rolls, rise = stance_rolls(legs, ank, hip, tgt)
    cand = {"changed": True, "legs": [{"leg": li, "roll": r} for li, r in zip(legs, rolls)], "rise": rise, "ankle_target": list(tgt)}
    for shift in (-1.0, 1.0):
        for bob in (0.0, -0.012):
            P = Pose()
            narrow_stance(P, cand)
            P.move(lateral * 0.012 * height * _idle_scale * shift)
            P.move(up * bob * height * _idle_scale)
            plant_idle_feet(P, cand)
            g = posed_leg_gaps(P, legs)
            for nm, n in need.items():
                if n is not None and g.get(nm) and g[nm][0] < n:
                    return False
    return True


def stance_rolls(legs, ank, hip, tgt):
    """([roll per leg], mean rise) for the ankle targets."""
    rolls, rises = [], []
    for li, a_l, h_l, t_l in zip(legs, ank, hip, tgt):
        ch = leg_chain(li)
        roll, dzc = gait.leg_roll(a_l - h_l, ch["end_rest"].z - head[ch["a"]].z, t_l - h_l)
        rolls.append(roll)
        rises.append(-dzc)
    return rolls, sum(rises) / len(rises)


def idle_stance_plan():
    """How the built-in idle narrows a wide rest stance (gait.stance_targets): per leg the roll about the hip (radians,
    about `forward`) that brings the ankles to the target spacing, and the rise of the body that keeps the feet on the
    ground. None when the character is not a two-legged one with an IK leg pair and both shoulders, or already stands
    about shoulder width. Also the numbers the report carries (stance, measured at rest)."""
    legs_ik = leg_info if len(leg_info) == 2 and all(leg_chain(li) for li in leg_info) else []
    if not legs_ik or not arm_info or not biped:
        return None
    ank = [lat_of(leg_chain(li)["end_rest"]) for li in legs_ik]
    hip = [lat_of(head[leg_chain(li)["a"]]) for li in legs_ik]
    sh = [lat_of(head[a["upper"]]) for a in arm_info]
    # One arm found (the other hidden by the pose): mirror it about the hips' midline.
    shoulder = abs(sh[0] - sh[1]) if len(sh) == 2 else 2 * abs(sh[0] - (hip[0] + hip[1]) / 2)
    plan = {"ankle_sep": abs(ank[0] - ank[1]), "hip_sep": abs(hip[0] - hip[1]), "shoulder": shoulder,
            "ratio_rest": abs(ank[0] - ank[1]) / max(shoulder, 1e-6), "legs": [], "rise": 0.0, "changed": False}
    fw = max(foot_width(leg_chain(li)) for li in legs_ik)
    plan["foot_width"] = fw
    sep0 = abs(ank[0] - ank[1])
    want = gait.stance_want(shoulder, plan["hip_sep"], fw)
    plan["ankle_sep_basic"] = want
    min_sep = 0.0
    rest_g = posed_leg_gaps(Pose(), legs_ik)
    plan["posed_gaps_rest"] = rest_g
    # Clear space the legs must keep: the clearance, but never more than the rest pose has (less a small tolerance), so a
    # character built with its legs close together is not asked for more than it came with.
    need = {"knee": gait.STANCE_KNEE_CLEAR * height, "thigh": gait.STANCE_THIGH_CLEAR * height}
    for nm in need:
        need[nm] = (min(need[nm], rest_g[nm][0]) - gait.STANCE_GAP_SLACK * height) if rest_g.get(nm) else None
    plan["clearance_need"] = need
    if sep0 > want * gait.STANCE_TOLERANCE:
        min_sep = gait.narrowest_clear(want, sep0, lambda sep: stance_clear(legs_ik, ank, hip, sep, need))
    tgt = gait.stance_targets(ank, hip, shoulder, foot_width=fw, min_sep=min_sep)
    final_sep = sep0 if tgt is None else abs(tgt[0] - tgt[1])
    # Per-character check: if the planted idle would still push the legs closer than that, it is not used at all.
    plan["plant_fails"] = not stance_clear(legs_ik, ank, hip, final_sep, need)
    if tgt is None or plan["plant_fails"]:
        return plan
    plan["floor_applied"] = min_sep > want * 1.001
    rises = []
    for li, a_l, h_l, t_l in zip(legs_ik, ank, hip, tgt):
        ch = leg_chain(li)
        dz = ch["end_rest"].z - head[ch["a"]].z
        roll, dzc = gait.leg_roll(a_l - h_l, dz, t_l - h_l)
        plan["legs"].append({"leg": li, "roll": roll})
        rises.append(-dzc)
    plan["rise"] = sum(rises) / len(rises)
    plan["rise_spread"] = max(rises) - min(rises)
    plan["ankle_sep_target"] = abs(tgt[0] - tgt[1])
    plan["ankle_target"] = list(tgt)
    plan["changed"] = True
    return plan


def narrow_stance(P, plan):
    """Pose part of the narrowing: upper legs turned in about `forward`, each foot turned back to its rest orientation
    (soles stay flat, toes stay forward), body raised so the feet stay on the ground."""
    if not plan or not plan["changed"]:
        return
    for e in plan["legs"]:
        ch = leg_chain(e["leg"])
        P.r(ch["a"], forward, math.degrees(e["roll"]))
    for e in plan["legs"]:
        ch = leg_chain(e["leg"])
        if ch["end_bone"]:
            set_world_orient(P, ch["end_bone"], REST_ORIENT[ch["end_bone"]], fk(P))
    P.move(up * plan["rise"])


_plant_error = [0.0]


def plant_idle_feet(P, plan):
    """Keep both feet where the stance puts them (the rest place, moved sideways to the narrowed stance, at rest height
    and rest orientation) while the body breathes and shifts its weight, with the existing two-bone leg solver."""
    for k, li in enumerate(leg_info):
        ch = leg_chain(li)
        target = ch["end_rest"].copy()
        if plan and plan["changed"]:
            target += lateral * (plan["ankle_target"][k] - lat_of(ch["end_rest"]))
        Mw = fk(P)
        pole = chain_pole(P, Mw, ch, forward)
        if gait.STANCE_KNEE_OUT:
            # The knee keeps its own bend direction but points at least slightly outward (away from the other leg), so planted
            # legs do not swing their knees together as the body lowers.
            ha, hm, he = chain_points(Mw, ch)
            out = P.root_q @ (lateral if li["left"] else -lateral)
            d = pole - hm
            d = d / max(d.length, 1e-9)
            lack = gait.STANCE_KNEE_OUT - d.dot(out)
            if lack > 0:
                pole = hm + (d + out * lack).normalized() * (ch["l1"] + ch["l2"])
        want = REST_ORIENT[ch["end_bone"]] if ch["end_bone"] else None
        err = ik_chain(P, ch, target, pole, end_orient=want, max_bend=160.0, Mw=Mw)
        _plant_error[0] = max(_plant_error[0], err)


# ---- clips ---------------------------------------------------------------------------------------------------
# Each clip is fn(P, t, u_frames) where t is 0..1 over the clip's motion frames (held at 1 during a death hold).
n_legs = len(leg_info)
walk_gait = gait.gait_for(n_legs)
run_gait = gait.gait_for(n_legs, run=True)
biped = kind == "biped" and motion in ("biped", "winged", "unknown")
amp_walk = {"biped": (24, 0, 38), "quadruped": (20, 0, 34), "multileg": (16, 18, 30)}
_amp_key = "multileg" if n_legs >= 6 else ("quadruped" if n_legs >= 4 else "biped")
a_sw, a_li, a_kn = (x * A for x in amp_walk[_amp_key])
# Both feet of a two-legged character are kept planted in the idle (plant_idle_feet).
_plant_ok = biped and len(leg_info) == 2 and all(leg_chain(li) for li in leg_info)
# The breath and the weight shift are sized for a leg about 0.45 of the height; planted short legs would flex too far for them.
_idle_scale = min(1.0, leg_len / (0.45 * height)) if _plant_ok else 1.0
_stance = idle_stance_plan()
if _stance and _stance.get("plant_fails"):
    _plant_ok, _idle_scale = False, 1.0  # the old idle, untouched (see idle_stance_plan)
    report["idle_stance_fallback"] = True
    _stance = None


def clip_idle(P, t):
    breath = math.sin(2 * math.pi * t)
    # Planted feet cannot follow the body upward (a leg at full reach has nowhere to go), so the breath moves it from the
    # standing height downward by the same range.
    P.move(up * 0.006 * height * _idle_scale * ((breath - 1) if _plant_ok else breath) * A)
    if motion == "serpent":
        wave_body(P, t, 0.25 * gait.wave_amplitude_deg(len(serp)) * A, waves=0.8)
        serp_front(P, lateral, -4 * A * (0.5 + 0.5 * math.sin(2 * math.pi * t)))
        return
    spine_pitch(P, 1.5 * A * breath)
    head_pitch(P, -2.0 * A * breath)
    head_yaw(P, 8.0 * A * math.sin(2 * math.pi * t + 0.7))
    tail_wave(P, t, 7.0 * A)
    if biped:
        narrow_stance(P, _stance)
        # Slow weight shift from foot to foot, a little torso twist and breathing in the arms.
        P.move(lateral * 0.012 * height * _idle_scale * A * math.sin(2 * math.pi * t))
        spine_yaw(P, 3 * A * math.sin(2 * math.pi * t + 0.5))
        for n in spine[1:2]:
            P.r(n, forward, -3 * A * math.sin(2 * math.pi * t))
        arms_relax(P, "idle", extra=-3 * breath * A)
        arms_swing(P, t, 2 * A, elbow=(8 + 4 * breath) * A, phase_from_legs=False)
        if _plant_ok:
            plant_idle_feet(P, _stance)
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
    ik_idx = [i for i, li in enumerate(leg_info) if li.get("ik")]
    other = [i for i in range(n_legs) if i not in ik_idx]
    if other:
        legs_cycle(P, t, g, a_sw * stride, a_li * stride, a_kn * (1.35 if run else 1.0), only=other)
    steps = 2 if g in ("alternate", "run2", "trot", "tripod", "alternating") else (4 if g == "walk4" else (1 if g == "gallop" else 2))
    bob_amp = (0.045 if run else 0.018) * height * A
    # Planted feet: each IK leg's end follows gait.foot_path, so it stays put on the ground for an implied forward
    # speed of stride / (duty * cycle) while the hips come down just enough for the legs to reach.
    feet = []
    if ik_idx:
        ph = gait.leg_phases(leg_info, g)
        duty = gait.DUTY[g]
        L = leg_len
        if biped:
            S_, lift = (0.7 * L, 0.42 * L) if run else (0.62 * L, gait.walk_lift(L, height))
        else:
            S_, lift = (0.75 * L, 0.3 * L) if run else (0.5 * L, 0.16 * L)
        S_, lift = S_ * A, lift * A
        for i in ik_idx:
            uu = gait.wrap(t + ph[i])
            # A biped walk lands with a smaller, quicker heel roll, so the toes do not hang in the air after heel strike,
            # and swings on a plateau, so the foot is clear of the floor through the swing and drops just before landing.
            bw = biped and not run
            fwd_, up_, pitch = gait.foot_path(uu, duty, S_, lift, run=run and biped, quick_strike=bw, plateau=bw,
                                              late_ease=biped and not run)  # walk only: run landed harder by 2 to 20 percent with the ease (RESULTS.md)
            if not biped:
                pitch *= 0.6
            toe_flat = 1.0 if uu < duty else 1.0 - gait.smoothstep((uu - duty) / (1.0 - duty) / 0.3)
            feet.append((leg_info[i], fwd_, up_, pitch, toe_flat))
    if biped:
        L = leg_len
        hips_p = head[roots[0].name]
        drop = planted_drop(feet, 0.985) if feet else 0.0
        if run:
            # Lowest at mid stance (the leg takes the landing), highest in the flight between steps.
            u_mid = gait.DUTY[g] / 2
            dz = min(-0.035 * L - 0.03 * L * math.cos(4 * math.pi * (t - u_mid)), -drop) * A
        else:
            dz = -(drop + 0.008 * L)
        P.move(up * dz)
        # Weight over the planted foot, pelvis turning with the swinging leg and the shoulders the other way.
        P.move(lateral * 0.012 * L * A * math.cos(2 * math.pi * (t - 0.3)) * (0.4 if run else 1.0))
        pelvis = (7 if run else 5) * A
        P.root_rotate(up, -pelvis * math.cos(2 * math.pi * t), hips_p)
        P.root_rotate(forward, (2 if run else 3) * A * math.cos(2 * math.pi * (t - 0.25)), hips_p)
        lean = (7 if run else 2) * A
        P.root_rotate(lateral, -lean * 0.4, hips_p)
        spine_yaw(P, 1.7 * pelvis * math.cos(2 * math.pi * t))
        spine_pitch(P, lean * 0.6 + (2.5 * A * math.cos(4 * math.pi * t) if run else 0))
        head_pitch(P, -lean * 0.8)
        head_yaw(P, -0.6 * pelvis * math.cos(2 * math.pi * t))
        arms_relax(P, "run" if run else "walk")
        # Arms swing from the shoulder against the same-side leg; the elbow stays bent in the run.
        for a in arm_info:
            sgn = -1.0 if a["left"] else 1.0
            s = sgn * math.cos(2 * math.pi * t)
            if run:
                P.r(a["upper"], a["swing_axis"], A * (gait.RUN_ARM_SWING_BIAS_DEG + gait.RUN_ARM_SWING_DEG * s))
                if a["fore"]:
                    P.r(a["fore"], a["swing_axis"], A * (82 + 12 * s))
            else:
                P.r(a["upper"], a["swing_axis"], A * gait.WALK_ARM_SWING_DEG * s)
                if a["fore"]:
                    P.r(a["fore"], a["swing_axis"], A * (14 + 8 * max(0.0, s)))
        if feet:
            plant_feet(P, feet, True)
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
    if feet and not biped:
        P.move(-up * planted_drop(feet, 0.97))
        plant_feet(P, feet, False)


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
        arms_relax(P, "turn")
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
        head_yaw(P, 22 * gait.envelope(t, [(0.6, 0), (1.0, 1)]))
        # Arms fling out a little as he goes over; death_settle then lays them on the ground beside the body.
        for a in arm_info:
            P.r(a["upper"], a["out_axis"], 25 * fall + 20 * buckle * (1 - fall))
            P.r(a["upper"], a["swing_axis"], 25 * buckle * (1 - fall))
            if a["fore"]:
                P.r(a["fore"], a["swing_axis"], 30 * buckle * (1 - fall) + 15 * fall)
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


def _reach_at_elbow(ch, deg):
    """Distance from shoulder to wrist when the elbow is `deg` degrees (interior angle) for this arm chain."""
    l1, l2 = ch["l1"], ch["l2"]
    return math.sqrt(max(0.0, l1 * l1 + l2 * l2 - 2 * l1 * l2 * math.cos(math.radians(deg))))


def death_settle(P, ground_z, lift=None):
    """Lay every limb of a final death pose on the ground: for each arm and leg chain, aim its end (wrist,
    ankle, paw) at ground height below where it is (gait.ground_target), arms pushed out beside the body, then
    solve the two-bone IK with the bend clamped to 150 degrees. lift: {end bone: height of the end joint above
    the ground}, measured from the mesh by clip_death. -> {bone: local correction quaternion}."""
    lift = lift or {}
    before = {n: q.copy() for n, q in P.rot.items()}
    chains = [("arm", a, a["ik"]) for a in arm_info if a.get("ik")] + [("leg", li, li["ik"]) for li in leg_info if li.get("ik")]
    body_fwd = P.root_q @ forward
    # Quadrupeds on their side: the upper legs land a little ahead of (front) or behind (hind) the lower ones.
    leg_root_z = {}
    if chains:
        Mw = fk(P)
        for kind_, limb, ch in chains:
            leg_root_z[id(limb)] = Mw[ch["a"]].translation.z
    mean_z = sum(leg_root_z.values()) / max(1, len(leg_root_z))
    for kind_, limb, ch in chains:
        Mw = fk(P)
        ha, hm, he = chain_points(Mw, ch)
        L = ch["l1"] + ch["l2"]
        reach = 0.97 * L
        if kind_ == "arm":
            out_dir = P.root_q @ (lateral if limb["left"] else -lateral)
            r = lift.get(ch["end_bone"] or ch["m"], 0.022 * height)
            target = gait.ground_target(tuple(ha), tuple(he), ground_z + r, reach, outward=tuple(out_dir), min_out=(0.8 if _TF[0] else 0.35) * L)
            # WP-77: the wrist target may not be farther from the shoulder than an arm at a 170 degree elbow, so the
            # settled arm never straightens past 170 degrees (a target out of reach straightens it).
            tv = Vector(target) - ha
            d170 = _reach_at_elbow(ch, 170.0)
            if tv.length > d170:
                target = tuple(ha + tv * (d170 / tv.length))
            # The elbow bends along the ground toward the feet, never down into it.
            pole = hm + (-(P.root_q @ up) * 0.8 + out_dir * 0.2) * L
        else:
            r = lift.get(ch["end_bone"] or ch["m"], 0.7 * max(0.0, ch["end_rest"].z - lo.z) if biped else max(0.0, ch["end_rest"].z - lo.z))
            end = he.copy()
            if not biped and leg_root_z[id(limb)] > mean_z:
                end += body_fwd * (0.18 if limb["front"] else -0.18) * L
            target = gait.ground_target(tuple(ha), tuple(end), ground_z + r, reach)
            pole = hm + up * L if biped else chain_pole(P, Mw, ch, -forward)
        ik_chain(P, ch, Vector(target), pole, end_orient=None, max_bend=150.0, Mw=Mw)
        hand = ch["end_bone"]
        if kind_ == "arm" and hand and byname[hand].children:  # torso-first: the hand lies limp, not pressed flat
            # WP-78: lay the palm flat. The middle finger's base is made level (fingers along the ground), then the
            # across-knuckle line is levelled by rolling about it, so the palm faces up and no finger tip hangs
            # below the hand.
            Mw = fk(P)
            base = {}
            for c in byname[hand].children:
                for key in ("Middle1", "Index1", "Pinky1"):
                    if c.name.endswith(key):
                        base[key] = Mw[c.name].translation
            if len(base) == 3:
                wrist = Mw[hand].translation
                f = base["Middle1"] - wrist
                flat_f = Vector((f.x, f.y, 0.0))
                if flat_f.length > 1e-6:
                    r1 = f.rotation_difference(flat_f.normalized() * f.length)
                    set_world_orient(P, hand, r1 @ orient(Mw, hand), Mw)
                    Mw = fk(P)
                    base = {k: Mw[next(c.name for c in byname[hand].children if c.name.endswith(k))].translation
                            for k in ("Middle1", "Index1", "Pinky1")}
                    wrist = Mw[hand].translation
                    f = base["Middle1"] - wrist
                    across = base["Index1"] - base["Pinky1"]
                    nrm = f.cross(across)
                    if nrm.length > 1e-6:
                        nrm.normalize()
                        if nrm.z < 0:
                            nrm = -nrm
                        r2 = nrm.rotation_difference(Vector((0.0, 0.0, 1.0)))
                        set_world_orient(P, hand, r2 @ orient(Mw, hand), Mw)
    return {n: before.get(n, Quaternion()).inverted() @ q for n, q in P.rot.items()
            if n not in before or before[n].rotation_difference(q).angle > 1e-6}


def apply_fix(P, fix, w):
    if w <= 0:
        return
    for n, q in fix.items():
        P.rot[n] = P.rot.get(n, Quaternion()) @ Quaternion().slerp(q, min(1.0, w))


_death = {}
TORSO_BONES = ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head")


def torso_lowest(P):
    """Lowest mesh z of the vertices mostly weighted to the torso and head (hands, arms and legs left out)."""
    if not _vertex_bone:
        for o in meshes:
            names = {g.index: g.name for g in o.vertex_groups}
            _vertex_bone[o.name] = [names.get(max(v.groups, key=lambda g: g.weight).group) if v.groups else None for v in o.data.vertices]
    apply_pose(P)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    z = None
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        vb = _vertex_bone.get(o.name, [])
        for i, v in enumerate(me.vertices):
            b = vb[i] if i < len(vb) else None
            if b and b.split(":")[-1] in TORSO_BONES:
                vz = (mw @ v.co).z
                z = vz if z is None or vz < z else z
        ev.to_mesh_clear()
    return z if z is not None else lo.z


def limb_lowest_parts(P, parts):
    """{key: lowest mesh z of the vertices whose main bone is in parts[key]} for a pose."""
    if not _vertex_bone:
        for o in meshes:
            names = {g.index: g.name for g in o.vertex_groups}
            _vertex_bone[o.name] = [names.get(max(v.groups, key=lambda g: g.weight).group) if v.groups else None for v in o.data.vertices]
    apply_pose(P)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    out = {}
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        vb = _vertex_bone.get(o.name, [])
        for i, v in enumerate(me.vertices):
            b = vb[i] if i < len(vb) else None
            if not b:
                continue
            for key, names in parts.items():
                if b in names:
                    vz = (mw @ v.co).z
                    out[key] = min(out.get(key, vz), vz)
        ev.to_mesh_clear()
    return out


def _below(name):
    """A bone and every bone under it."""
    s_ = set()
    stack = [byname[name]]
    while stack:
        b = stack.pop()
        s_.add(b.name)
        stack.extend(b.children)
    return s_


def arm_parts():
    """{end bone: every bone from the arm's root down (upper arm, sleeve, forearm, hand)} for each arm chain."""
    out = {}
    for a in arm_info:
        ch = a.get("ik")
        if ch:
            out[ch["end_bone"] or ch["m"]] = _below(ch["a"])
    return out


def leg_parts():
    """{end bone: every bone from the leg's root down (thigh, robe, shin, foot) for each leg chain}."""
    out = {}
    for li in leg_info:
        ch = leg_chain(li)  # the IK chain, or the stance chain of a splayed leg (WP-78)
        if ch:
            out[ch["end_bone"] or ch["m"]] = _below(ch["a"])
    return out


def _splay_clearance(P):
    """Torso-first death: a leg (splayed or not) whose foot or shin goes under the ground in the fall is taken
    clear by turning its ankle (within 25 degrees) and then its hip (within 40), about the leg's own swing axis, by
    the smallest turn that clears it. No knee bend, so the leg skin is not stretched."""
    g = _rest_low[0]
    parts = leg_parts()
    for li in leg_info:
        ch = leg_chain(li)
        if not ch:  # torso-first only, every leg: the goblin's legs have IK since splay is read to the ankle
            continue
        key = ch["end_bone"] or ch["m"]
        if key not in parts:
            continue

        def low():
            return limb_lowest_parts(P, {key: parts[key]}).get(key, g)

        if low() >= g - 0.003:
            continue
        for name, lim in ((key, 25), (li["bones"][0], 40)):
            base = P.rot.get(name, Quaternion()).copy()
            ax = local_axis(name, lateral if name == key else li["swing_axis"])
            best = (low(), 0)
            for k in range(1, lim // 2 + 1):
                hit = None
                for sg in (1, -1):
                    P.rot[name] = base @ Quaternion(ax, math.radians(2 * k * sg))
                    z = low()
                    if z > best[0] + 1e-6:
                        best = (z, 2 * k * sg)
                    if z >= g - 0.001:
                        hit = 2 * k * sg
                        break
                if hit is not None:
                    break
            P.rot[name] = base @ Quaternion(ax, math.radians(best[1]))
            if best[0] >= g - 0.001:
                break


def _splay_lay(P, g):
    """Torso-first death, final pose: a leg that hangs above the ground is swung down at the hip (within 25
    degrees) until its lowest point is 1 cm over it, so the leg lies along the ground. -> {bone: local correction}."""
    parts = leg_parts()
    out = {}
    for li in leg_info:
        ch = leg_chain(li)
        if not ch:  # torso-first only, every leg: the goblin's legs have IK since splay is read to the ankle
            continue
        key = ch["end_bone"] or ch["m"]
        name = li["bones"][0]
        base = P.rot.get(name, Quaternion()).copy()
        ax = local_axis(name, li["swing_axis"])

        def low(d):
            P.rot[name] = base @ Quaternion(ax, math.radians(d))
            return limb_lowest_parts(P, {key: parts[key]}).get(key, g) - g

        z0 = low(0.0)
        if z0 <= 0.01:
            P.rot[name] = base
            continue
        sg = 1 if low(3.0) < z0 else -1
        pick = 0.0
        for k in range(1, 26):
            pick = float(k * sg)
            if low(pick) <= 0.01:
                break
        P.rot[name] = base @ Quaternion(ax, math.radians(pick))
        out[name] = Quaternion(ax, math.radians(pick))
    return out


def _splay_relax(P, g):
    """Torso-first death, final pose: each leg's ankle is relaxed from the standing right angle toward a foot in
    line with the shin (up to 14 degrees, the most that keeps the leg skin within 0.1 points of Today), as far as the foot stays off the ground. -> {bone: local correction}."""
    parts = leg_parts()
    out = {}
    for li in leg_info:
        ch = leg_chain(li)
        if not ch:  # torso-first only, every leg: the goblin's legs have IK since splay is read to the ankle
            continue
        key = ch["end_bone"] or ch["m"]
        base = P.rot.get(key, Quaternion()).copy()
        ax = local_axis(key, lateral)

        def put(d):
            P.rot[key] = base @ Quaternion(ax, math.radians(d))

        def inline(d):
            put(d)
            Mw = fk(P)
            shin = Mw[key].translation - Mw[li["bones"][1]].translation
            tip = max((Mw[c].translation for c in _below(key) if c != key), key=lambda v: (v - Mw[key].translation).length, default=None)
            if tip is None or shin.length < 1e-6:
                return 0.0
            f = tip - Mw[key].translation
            return shin.normalized().dot(f.normalized())

        sg = 1 if inline(10.0) > inline(-10.0) else -1
        pick = 0.0
        for k in range(14, 0, -2):
            put(float(k * sg))
            if limb_lowest_parts(P, {key: parts[key]}).get(key, g) >= g - 0.003:
                pick = float(k * sg)
                break
        put(pick)
        if pick:
            out[key] = Quaternion(ax, math.radians(pick))
    return out


def _death_render(P, t):
    """The death pose at clip time t: fall, resting hand, settled limbs, torso tilt and head bend."""
    _death_pose(P, t, _death["drop"])
    hands_pose(P, 0.0)
    w = gait.envelope(t, [(0.45, 0), (0.9, 1)])
    apply_fix(P, _death["fix"], w)
    spine_pitch(P, _death["tilt"] * w)
    head_pitch(P, _death["head"] * w)
    if _TF[0]:
        _splay_clearance(P)


def _lift_at(table, t):
    x = min(1.0, max(0.0, t)) * (len(table) - 1)
    i = int(x)
    if i >= len(table) - 1:
        return table[-1]
    f = x - i
    return table[i] * (1 - f) + table[i + 1] * f


def _smooth_lift(defs, step=0.006):
    """WP-78: the smallest smooth lift that keeps every fall sample clear of the ground: a window max, a short
    average, then limits on the change between neighbouring samples (no jump over `step` metres per sample)."""
    n = len(defs)
    L = [max(defs[max(0, i - 2):i + 3]) for i in range(n)]
    L = [sum(L[max(0, i - 1):i + 2]) / len(L[max(0, i - 1):i + 2]) for i in range(n)]
    L = [max(L[i], defs[i]) for i in range(n)]
    for _ in range(3):
        for i in range(1, n):
            L[i] = max(L[i], L[i - 1] - step)
        for i in range(n - 2, -1, -1):
            L[i] = max(L[i], L[i + 1] - step)
        L = [max(L[i], defs[i]) for i in range(n)]
    return L


# ---- Today's death: the fallback (WP-78) -----------------------------------------------------------------------------
# The death clip above is the new one. Today's death (master d2dc40d) is kept here as the old death: clip_death uses it
# when the new death sinks more than 1 cm deeper than it on the lowest skinned point over the fall (the death_fallback
# rule), the same way the idle falls back to the old idle. Its limbs are settled by death_settle_old.
def death_settle_old(P, ground_z, lift=None):
    """Lay every limb of a final death pose on the ground: for each arm and leg chain, aim its end (wrist,
    ankle, paw) at ground height below where it is (gait.ground_target), arms pushed out beside the body, then
    solve the two-bone IK with the bend clamped to 150 degrees. lift: {end bone: height of the end joint above
    the ground}, measured from the mesh by clip_death. -> {bone: local correction quaternion}."""
    lift = lift or {}
    before = {n: q.copy() for n, q in P.rot.items()}
    chains = [("arm", a, a["ik"]) for a in arm_info if a.get("ik")] + [("leg", li, li["ik"]) for li in leg_info if li.get("ik")]
    body_fwd = P.root_q @ forward
    # Quadrupeds on their side: the upper legs land a little ahead of (front) or behind (hind) the lower ones.
    leg_root_z = {}
    if chains:
        Mw = fk(P)
        for kind_, limb, ch in chains:
            leg_root_z[id(limb)] = Mw[ch["a"]].translation.z
    mean_z = sum(leg_root_z.values()) / max(1, len(leg_root_z))
    for kind_, limb, ch in chains:
        Mw = fk(P)
        ha, hm, he = chain_points(Mw, ch)
        L = ch["l1"] + ch["l2"]
        reach = 0.97 * L
        if kind_ == "arm":
            out_dir = P.root_q @ (lateral if limb["left"] else -lateral)
            r = lift.get(ch["end_bone"] or ch["m"], 0.022 * height)
            target = gait.ground_target(tuple(ha), tuple(he), ground_z + r, reach, outward=tuple(out_dir), min_out=0.35 * L)
            # WP-77: the wrist target may not be farther from the shoulder than an arm at a 170 degree elbow, so the
            # settled arm never straightens past 170 degrees (a target out of reach straightens it).
            tv = Vector(target) - ha
            d170 = _reach_at_elbow(ch, 170.0)
            if tv.length > d170:
                target = tuple(ha + tv * (d170 / tv.length))
            # The elbow bends along the ground toward the feet, never down into it.
            pole = hm + (-(P.root_q @ up) * 0.8 + out_dir * 0.2) * L
        else:
            r = lift.get(ch["end_bone"] or ch["m"], 0.7 * max(0.0, ch["end_rest"].z - lo.z) if biped else max(0.0, ch["end_rest"].z - lo.z))
            end = he.copy()
            if not biped and leg_root_z[id(limb)] > mean_z:
                end += body_fwd * (0.18 if limb["front"] else -0.18) * L
            target = gait.ground_target(tuple(ha), tuple(end), ground_z + r, reach)
            pole = hm + up * L if biped else chain_pole(P, Mw, ch, -forward)
        ik_chain(P, ch, Vector(target), pole, end_orient=None, max_bend=150.0, Mw=Mw)
        hand = ch["end_bone"]
        if kind_ == "arm" and hand and byname[hand].children:
            # Lay the hand flat on the ground: its direction (wrist to knuckles) turned level.
            Mw = fk(P)
            kn = max(byname[hand].children, key=lambda c: (head[c.name] - head[hand]).length).name
            d = Mw[kn].translation - Mw[hand].translation
            flat = Vector((d.x, d.y, 0.0))
            if flat.length > 1e-6:
                r = d.rotation_difference(flat.normalized() * d.length)
                set_world_orient(P, hand, r @ orient(Mw, hand), Mw)
    return {n: before.get(n, Quaternion()).inverted() @ q for n, q in P.rot.items()
            if n not in before or before[n].rotation_difference(q).angle > 1e-6}


_death_old = {}


def _death_old_build():
    def final(Q):
        _death_pose(Q, 1.0, 0.0)
        finish_serpent(Q)

    drop = ground_fix(final)
    fix = {}
    if motion != "serpent" and n_legs < 6 and (arm_info or any(li.get("ik") for li in leg_info)):
        g = _rest_low[0] - drop
        lift = {}
        for _ in range(3):
            Q = Pose()
            final(Q)
            fix = death_settle_old(Q, g, lift)
            R = Pose()
            final(R)
            apply_fix(R, fix, 1.0)
            lows = limb_lowest(R)
            Mw = fk(R)
            done = True
            for end_bone, z in lows.items():
                joint_h = Mw[end_bone].translation.z - g
                lift[end_bone] = max(0.0, joint_h - (z - g))
                if abs(z - g) > 0.004 * height:
                    done = False
            if done:
                break
        drop = ground_fix(lambda R, f=fix: (final(R), apply_fix(R, f, 1.0)))
    _death_old.update(drop=drop, fix=fix)


def _death_old_render(P, t):
    """Today's death at clip time t, as master drew it: the fall, the settled limbs, then the resting hand."""
    _death_pose(P, t, _death_old["drop"])
    apply_fix(P, _death_old["fix"], gait.envelope(t, [(0.45, 0), (0.9, 1)]))
    hands_pose(P, 0.0)


def _lowest_over_fall(render, samples=48):
    """Lowest skinned point over the fall (metres from the rest ground; negative is under it), sampled over the clip."""
    g = _rest_low[0]
    return min(lowest_z(_pose_at(render, i / (samples - 1))) - g for i in range(samples))


def _pose_at(render, t):
    Pt = Pose()
    render(Pt, t)
    return Pt


_TF = [False]  # True while the torso-first death is being built or drawn


def _hips_name():
    """The hips bone the death measures: the Mixamo Hips when there is one, else the root end of the spine (F14: a
    creature rigged with its model's own bone names has no Hips, and the bare lookup stopped the whole script)."""
    return next((n for n in byname if n.split(":")[-1] == "Hips"), spine[0] if spine else roots[0].name)


def _torso_first_search(final):
    """Torso-first death: the spine tilt (within 30 degrees) and head bend (within 35) that bring the hips lowest while
    the head rests on the ground, judged on the torso and head alone; the limbs then settle against that pose."""
    hips_name = _hips_name()
    hset = set()
    for hb in head_bones:
        hset |= _below(hb)
    g = _rest_low[0]

    def ev(tl, hd_):
        Q = Pose()
        _death_pose(Q, 1.0, 0.0)
        hands_pose(Q, 0.0)
        finish_serpent(Q)
        spine_pitch(Q, tl)
        head_pitch(Q, hd_)
        d_ = g - torso_lowest(Q)
        hz = fk(Q)[hips_name].translation.z + d_ - g
        hl = limb_lowest_parts(Q, {"head": hset}).get("head", g) + d_ - g
        return hz, hl

    best = None
    for tl in range(-30, 31, 5):
        for hd_ in range(-35, 36, 5):
            hz, hl = ev(float(tl), float(hd_))
            if hl > 0.03:
                continue
            if best is None or hz < best[0]:
                best = (hz, float(tl), float(hd_))
    if best is None:
        return {"tl": 0.0, "hd": 0.0}
    for tl in range(int(best[1]) - 4, int(best[1]) + 5, 2):
        for hd_ in range(int(best[2]) - 4, int(best[2]) + 5, 2):
            if abs(tl) > 30 or abs(hd_) > 35:
                continue
            hz, hl = ev(float(tl), float(hd_))
            if hl <= 0.03 and hz < best[0]:
                best = (hz, float(tl), float(hd_))
    print("DEATH_TORSO_FIRST hips", round(best[0], 3), "tilt", best[1], "head", best[2], file=sys.stderr)
    return {"tl": best[1], "hd": best[2]}


def _death_build():
    """Build the death for this rig into _death: the new death, or Today's when the fallback rule says so."""
    tf = {"tl": 0.0, "hd": 0.0}  # torso-first variant: the spine tilt and head bend are part of the pose the limbs settle against

    def final(Q, drop=0.0):
        _death_pose(Q, 1.0, drop)
        hands_pose(Q, 0.0)  # the resting hand is part of the death pose, so the limbs settle against it (WP-78)
        finish_serpent(Q)
        if tf["tl"]:
            spine_pitch(Q, tf["tl"])
        if tf["hd"]:
            head_pitch(Q, tf["hd"])

    drop = ground_fix(final)
    fix = {}
    head_deg = 0.0
    tilt_deg = 0.0  # F14: a serpent or a six-legged body skips the branch below, and the death still reads it
    if motion != "serpent" and n_legs < 6 and (arm_info or any(li.get("ik") for li in leg_info)):
        # WP-78: the torso comes down onto the ground first (head, neck, spine, hips), so the arms are aimed from
        # shoulders that are near the ground rather than from a body held up by its straight arms. The limbs then
        # settle as before, and the whole pose is put back on the ground.
        if _rest_low[0] is None:
            _rest_low[0] = lowest_z(Pose())
        if _TF[0]:
            tf.update(_torso_first_search(final))
        P0 = Pose()
        final(P0)
        drop_t = _rest_low[0] - torso_lowest(P0)
        g = _rest_low[0]
        lift = {}
        for _ in range(5):
            Q = Pose()
            final(Q, drop_t)
            fix = death_settle(Q, g, lift)
            R = Pose()
            final(R, drop_t)
            apply_fix(R, fix, 1.0)
            lows = limb_lowest(R)
            # WP-78: an arm's sleeve or upper arm may go under the ground too; the arm is lifted locally for it.
            lows.update(limb_lowest_parts(R, {**arm_parts(), **leg_parts()}))
            Mw = fk(R)
            done = True
            for end_bone, z in lows.items():
                # WP-78: a hand or sleeve may hover above the ground, so it never holds the torso up. Only a point
                # that goes under the ground is lifted, locally (its target moves up); the body is not moved for it.
                dev = z - g
                if dev < -0.004 * height:
                    lift[end_bone] = lift.get(end_bone, 0.0) + (-dev)
                    done = False
            if done:
                break
        # The body is put on the ground by its torso, pelvis and head only, never by an arm, sleeve or hand.
        R = Pose()
        final(R, drop_t)
        apply_fix(R, fix, 1.0)
        extra = _rest_low[0] - torso_lowest(R)
        drop = drop_t + extra
        # WP-78: the head is bent (neck, within 35 degrees) so its lowest point rests on the ground, not held up.
        hset = set()
        for hb in head_bones:
            hset |= _below(hb)

        def head_low(d_):
            Q = Pose()
            final(Q, drop)
            apply_fix(Q, fix, 1.0)
            head_pitch(Q, d_)
            return limb_lowest_parts(Q, {"head": hset}).get("head", g) - g

        best, best_cost = 0.0, None
        # WP-78 follow-up (PREREG section 11): the standard rung keeps the head flat. Its head bend (up to 35 degrees)
        # stretched the torso of blacksmith and mage. The torso-first rung sets its own head bend in _torso_first_search.
        for d_ in range(0, 1):
            z = head_low(d_)
            if z < -0.003:
                continue
            if best_cost is None or abs(z) < best_cost:
                best, best_cost = float(d_), abs(z)
        head_deg = best
        # WP-78: one bounded torso-tilt try, only where the head-only pose still leaves the hips above 0.25 m. The
        # spine tilts within 30 degrees and the head bends, the whole body stays on the ground (3 mm tolerance) and
        # the hips go as low as they can.
        hips_name = _hips_name()

        def tilt_eval(tl, hd_):
            Q = Pose()
            _death_pose(Q, 1.0, 0.0)
            hands_pose(Q, 0.0)
            finish_serpent(Q)
            apply_fix(Q, fix, 1.0)
            spine_pitch(Q, tl)
            head_pitch(Q, hd_)
            d_ = _rest_low[0] - torso_lowest(Q)
            hz_ = fk(Q)[hips_name].translation.z + d_ - g
            hd2 = limb_lowest_parts(Q, {"head": hset}).get("head", g) + d_ - g
            full_ = lowest_z(Q) + d_ - g
            return d_, hz_, hd2, full_

        head_only = tilt_eval(0.0, head_deg)
        if _TF[0]:
            drop, tilt_deg, head_deg = _rest_low[0] - torso_lowest(R), tf["tl"], tf["hd"]
            R = Pose()
            final(R, drop)
            apply_fix(R, fix, 1.0)
            for n_, q_ in _splay_lay(R, g).items():
                fix[n_] = fix.get(n_, Quaternion()) @ q_
            R = Pose()
            final(R, drop)
            apply_fix(R, fix, 1.0)
            for n_, q_ in _splay_relax(R, g).items():
                fix[n_] = fix.get(n_, Quaternion()) @ q_
        elif head_only[1] > 0.25:
            best_t = None
            for tl in range(-30, 31, 5):
                for hd_ in range(-35, 36, 5):
                    d_, hz_, hd2, full_ = tilt_eval(float(tl), float(hd_))
                    if full_ < -0.003 or not (-0.003 <= hd2 <= 0.05):
                        continue
                    if best_t is None or hz_ < best_t[1]:
                        best_t = (d_, hz_, float(tl), float(hd_))
            if best_t is not None and best_t[1] < head_only[1] - 0.05:
                drop, tilt_deg, head_deg = best_t[0], best_t[2], best_t[3]
    _death.update(drop=drop, fix=fix, head=head_deg, tilt=tilt_deg)
    # WP-78: contact in the fall. Sample the whole clip, find how far any body point goes under the ground, and lift
    # the body by the smooth amount that clears it (logged, so the largest lift is visible).
    samples = 48
    defs = []
    raw = []
    for i in range(samples):
        Pt = Pose()
        _death_render(Pt, i / (samples - 1))
        low = lowest_z(Pt) - _rest_low[0]
        raw.append(low)
        defs.append(max(0.0, -low))
    # WP-78 death_fallback: the new death is kept unless it sinks more than 1 cm deeper than Today's death over the
    # fall (lowest skinned point). Today's death is then used for this rig, like the idle fallback.
    new_low = min(raw)
    _death_old_build()
    old_low = _lowest_over_fall(_death_old_render)
    fallback = gait.death_fallback_used(new_low, old_low)
    report["death_fallback"] = {"used": fallback, "new_lowest_m": round(new_low, 4), "old_lowest_m": round(old_low, 4),
                                "rule": "new kept unless it sinks more than 0.01 m deeper than the old death over the fall",
                                "rung": "today" if fallback else ("torso_first" if _TF[0] else "standard")}
    print("DEATH_FALLBACK", report["death_fallback"], file=sys.stderr)
    if fallback:
        _death.clear()
        _death["old"] = True
    else:
        table = [min(0.02, v) for v in _smooth_lift(defs)]  # 2 cm at most for any character (the boy needs it: 2.08 cm uncapped)
        _death["lift"] = table
        print("DEATH_LIFT max_deficit", round(max(defs), 4), "max_lift", round(max(table), 4), file=sys.stderr)


def clip_death(P, t):
    if not _death:
        _death_build()
        # WP-78 goblin: a biped that fails the rule gets one more try, the torso-first death: the torso and head are
        # tilted down first and the limbs settle against that pose (a splayed leg, with no IK chain, is cleared as well).
        if _death.get("old") and biped and motion != "serpent":
            first = dict(report["death_fallback"], rung="standard_failed")
            _TF[0] = True
            _death.clear()
            _death_build()
            report["death_fallback"]["standard"] = first
            if _death.get("old"):
                _TF[0] = False
    if _death.get("old"):
        _death_old_render(P, t)
        return
    _death_render(P, t)
    _lift = _death.get("lift")
    if _lift:
        P.move(up * _lift_at(_lift, t))


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


def clip_neutral(P, t):
    """D9 one-frame neutral pose: the idle arm angle with the Arm pose offset (gait.arm_hold_deg), nothing else moved."""
    arms_relax(P, "idle")


CLIP_FNS = {gait.NEUTRAL_CLIP: clip_neutral, "idle": clip_idle, "walk": clip_walk, "run": clip_run, "slither": clip_slither, "turn_left": clip_turn_left,
            "turn_right": clip_turn_right, "attack": clip_attack, "hit": clip_hit, "death": clip_death, "flap": clip_flap,
            "glide": clip_glide, "takeoff": clip_takeoff, "land": clip_land}


def _with_hands(fn, clip):
    """A clip's pose plus the resting hand (WP-76); attack closes the hand into a grip over its strike."""
    def run(P, t):
        fn(P, t)
        grip = gait.envelope(t, [(0.0, 0), (0.35, 0.3), (0.5, 1), (0.62, 1), (1.0, 0)]) if clip == "attack" else 0.0
        hands_pose(P, grip)
    return run


CLIP_FNS = {k: (v if k == "death" else _with_hands(v, k)) for k, v in CLIP_FNS.items()}  # death sets its own hand (WP-78)


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
_vertex_bone = {}


def _limb_ends(chains=None):
    """{end bone: set of bones whose vertices belong to that limb end (the end bone and everything below it)}."""
    out = {}
    if chains is None:
        chains = [a["ik"] for a in arm_info if a.get("ik")] + [li["ik"] for li in leg_info if li.get("ik")]
    for ch in chains:
        e = ch["end_bone"] or ch["m"]
        s = set()
        stack = [byname[e]]
        while stack:
            b = stack.pop()
            s.add(b.name)
            stack.extend(b.children)
        out[e] = s
    return out


def limb_lowest(P, chains=None):
    """{end bone: lowest mesh z of the vertices mostly weighted to that limb end} for a pose."""
    ends = _limb_ends(chains)
    if not _vertex_bone:
        for o in meshes:
            names = {g.index: g.name for g in o.vertex_groups}
            _vertex_bone[o.name] = [names.get(max(v.groups, key=lambda g: g.weight).group) if v.groups else None for v in o.data.vertices]
    apply_pose(P)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    out = {}
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        vb = _vertex_bone.get(o.name, [])
        for e, names in ends.items():
            zs = [(mw @ v.co).z for i, v in enumerate(me.vertices) if i < len(vb) and vb[i] in names]
            if zs:
                out[e] = min(out.get(e, zs[0]), min(zs))
        ev.to_mesh_clear()
    return out


def ground_fix(pose_fn):
    """Vertical offset that puts the lowest vertex of pose_fn's pose on the rest pose's ground."""
    if _rest_low[0] is None:
        _rest_low[0] = lowest_z(Pose())
    P = Pose()
    pose_fn(P)
    return _rest_low[0] - lowest_z(P)


# ---- collarbone share (WP-75) ----------------------------------------------------------------------------------
# An arm with a collarbone (Shoulder bone before the upper arm) lifts it by gait.clavicle_lift_deg of the upper arm's
# raise in the chest's frame; the upper arm turns back by the same amount, so the arm points where the clip put it.
_CLAV = []
for _a in arm_info:
    if len(_a["bones"]) >= 4 and _a["upper"] == _a["bones"][1] and byname[_a["bones"][0]].parent is not None:
        _sh = _a["bones"][0]
        _elbow = head[_a["fore"]] if _a["fore"] else head[_a["leaf"]]
        _d = head[_a["upper"]] - head[_sh]
        _CLAV.append({"sh": _sh, "up": _a["upper"], "chest": byname[_sh].parent.name, "arm": _elbow - head[_a["upper"]],
                      "rest": _a["rest_deg"],
                      "axis": unit(Vector((_d.x, _d.y, 0)).cross(up), forward)})  # +deg lifts the outer end


def collarbone_share(P):
    if not _CLAV:
        return
    Mw = fk(P)
    for c in _CLAV:
        chest = (Mw[c["chest"]] @ REST_W_INV[c["chest"]]).to_3x3()
        arm_dir = (Mw[c["up"]] @ REST_W_INV[c["up"]]).to_3x3() @ c["arm"]
        lift = gait.clavicle_lift_deg(gait.angle_from_down(tuple(chest.inverted() @ arm_dir), tuple(up)), c["rest"])
        if lift > 1e-3:
            P.r(c["sh"], c["axis"], lift)
            P.rot[c["up"]] = Quaternion(local_axis(c["up"], c["axis"]), math.radians(-lift)) @ P.rot.get(c["up"], Quaternion())


# ---- actions and NLA -----------------------------------------------------------------------------------------
table = gait.clip_table(clips, motion, fps, SPEED)
poses = {}
for row in table:
    fn = CLIP_FNS[row["name"]]
    n = max(1, row["motion_frames"])  # the neutral pose has no motion frames
    frames = []
    for f in range(row["frame_end"] + 1):
        P = Pose()
        fn(P, min(1.0, f / n))
        finish_serpent(P)
        collarbone_share(P)
        frames.append(P)
    poses[row["name"]] = frames
# ---- clip data for the game (WP-19): locomotion speed from the planted feet, attack hit time from peak limb speed.
# Read from the posed frames themselves, so they follow the motion that is exported. Speed is None where the clip has
# no stance (in-place flat clips): the importer keeps its own default for those. Written into each clip row.
def _ends_positions(frames, bones):
    """{bone: [(x, y, z) per frame]} for the bones' head positions in world space."""
    out = {b: [] for b in bones}
    for P in frames:
        Mw = fk(P)
        for b in bones:
            t = Mw[b].translation
            out[b].append((t.x, t.y, t.z))
    return out


def _character_height():
    Mw = fk(Pose())
    zs = [m.translation.z for m in Mw.values()]
    return max(zs) - min(zs)


def _speed_bone(li):
    """The bone whose head the walk speed is read at: the toe joint (the push-off pivot, still through the whole stance)
    on a leg with a named foot and a toe, else the IK end (ankle)."""
    ch = leg_chain(li)
    if li.get("foot_index") is not None and ch.get("below"):
        return ch["below"][0]
    return ch["end_bone"] or ch["m"]


_foot_bones = [_speed_bone(li) for li in leg_info if leg_chain(li)]
_hand_bones = []
for _a in arm_info:
    if _a.get("ik") and _a["ik"]["end_bone"]:  # the arm chain's end is the Hand (the wrist), past any twist bones
        _hand_bones.append(_a["ik"]["end_bone"])
    elif _a.get("fore") and byname[_a["fore"]].children:
        _hand_bones.append(byname[_a["fore"]].children[0].name)
for row in table:
    if row["name"] in ("walk", "run") and _foot_bones:
        feet = _ends_positions(poses[row["name"]], _foot_bones)
        res = gait.planted_speed([feet[b] for b in _foot_bones], fps, _character_height())
        row.update(gait.speed_row_fields(res))  # speed_mps only when measured; the importer keeps its default otherwise
    elif row["name"] in ("walk", "run"):
        row.update(gait.speed_row_fields({"speed": None, "method": "fallback", "planted_share": 0.0, "reason": "no legs"}))
    if row["name"] == "attack" and (_hand_bones or _foot_bones):
        ends = _ends_positions(poses[row["name"]], _hand_bones + _foot_bones)
        hit = gait.hit_frame(ends, fps, tuple(forward), _character_height())
        report.setdefault("hit_probe", {})[row["name"]] = {b: [round(math.dist(ends[b][i - 1], ends[b][i + 1]) * fps / 2.0, 3) for i in range(1, len(ends[b]) - 1)] for b in ends}
        if hit["frame"] >= 0:
            row["hit_frame"], row["hit_time"], row["hit_speed_mps"], row["hit_end"] = hit["frame"], hit["time"], hit["speed"], hit["end"]
            row["hit_method"], row["hit_peak_frame"] = hit["method"], hit["peak_frame"]
keyed = set()
for frames in poses.values():
    for P in frames:
        keyed.update(P.rot)
keyed.update(r.name for r in roots)


def idle_feet_report():
    """Numbers for the idle's feet over the loop (world units; ratios against the character's height): the ankle travel
    (largest distance from the first frame), its height range, how far the soles tilt from the rest, the gap between
    each foot's lowest vertex and where it stands at rest (positive floats, negative sinks), and the stance."""
    frames = poses.get("idle")
    legs_ik = leg_info if len(leg_info) == 2 and all(leg_chain(li) for li in leg_info) else []
    if not frames or not biped or not legs_ik:
        return None
    n = len(frames)
    ends = [(leg_chain(li)["end_bone"] or leg_chain(li)["m"]) for li in legs_ik]
    seq = []
    for P in frames[:-1] if n > 1 else frames:
        Mw = fk(P)
        seq.append([(Mw[e].translation.copy(), math.degrees(orient(Mw, e).rotation_difference(REST_ORIENT[e]).angle)) for e in ends])
    chains_ = [leg_chain(li) for li in legs_ik]
    rest = limb_lowest(Pose(), chains_)
    idx = sorted({int(round(i * (len(seq) - 1) / 8)) for i in range(9)})
    gap = []
    for i in idx:
        low_i = limb_lowest(frames[i], chains_)
        gap.append([low_i.get(e, rest.get(e, 0.0)) - rest.get(e, 0.0) for e in ends])
    out = {"frames": len(seq), "legs": []}
    for k, e in enumerate(ends):
        p0 = seq[0][k][0]
        out["legs"].append({"end_bone": e, "left": legs_ik[k]["left"],
                            "travel": max((s[k][0] - p0).length for s in seq),
                            "travel_lateral": max(abs((s[k][0] - p0).dot(lateral)) for s in seq),
                            "travel_forward": max(abs((s[k][0] - p0).dot(forward)) for s in seq),
                            "ankle_z_range": max(s[k][0].z for s in seq) - min(s[k][0].z for s in seq),
                            "sole_tilt_deg_max": max(s[k][1] for s in seq),
                            "ground_gap_min": min(g[k] for g in gap), "ground_gap_max": max(g[k] for g in gap)})
    sep = [abs((s[0][0] - s[1][0]).dot(lateral)) for s in seq]
    out["ankle_sep_min"], out["ankle_sep_max"] = min(sep), max(sep)
    out["posed_gaps_rest"] = posed_leg_gaps(Pose(), legs_ik)
    pg = [posed_leg_gaps(frames[i], legs_ik) for i in idx]
    out["knee_gap_frames"] = [[i, round(g["knee"][0], 4) if g["knee"] else None] for i, g in zip(idx, pg)]
    out["posed_gaps_min"] = {nm: (min(g[nm][0] for g in pg if g[nm]), min(g[nm][1] for g in pg if g[nm])) if any(g[nm] for g in pg) else None for nm in ("knee", "thigh")}
    out["height"] = height
    return out


report["idle_feet"] = idle_feet_report()
report["hands"] = [{"hand": h["hand"], "fingers": sorted(h["fingers"]), "turned_back": h.get("turned_back", []), "axis": [round(c, 4) for c in h["axis"]], "grip_caps": {f: round(v, 2) for f, v in _grip_caps(h).items()} if h["fingers"] else {}} for h in _hand_rig()]  # WP-76
if report["idle_feet"]:
    report["idle_feet"]["plant_error_max"] = _plant_error[0]
if _stance:
    report["stance"] = {k: v for k, v in _stance.items() if k != "legs"}
    report["stance"]["roll_deg"] = [round(math.degrees(e["roll"]), 3) for e in _stance["legs"]]


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
    # The walk's measured upper-arm angle from straight down, over eight frames of the cycle (the walk track is soloed).
    arm_angles = {}
    if arm_info:
        S = sample("walk", [1 + int(round(rows["walk"]["motion_frames"] * i / 8)) for i in range(8)])
        for a in arm_info:
            if not a["fore"]:
                continue
            vals = [gait.angle_from_down(W.to_3x3() @ (s[a["fore"]].translation - s[a["upper"]].translation), up) for s in S]
            arm_angles[a["side"]] = {"mean_deg": round(sum(vals) / len(vals), 2), "min_deg": round(min(vals), 2),
                                     "max_deg": round(max(vals), 2)}
        report["arm_pose"]["walk_measured"] = arm_angles
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
