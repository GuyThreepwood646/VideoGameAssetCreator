"""Blender headless: retarget hand-made library clips onto a Mixamo-named humanoid (CC0 Quaternius Universal
Animation Library) and write them into the character's animated GLB.

Run: blender -b --python retarget.py -- <in.glb> <out.glb> --library <file.glb|.fbx>[;<file2>]
         --clips "idle=Idle_Loop,walk=Walk_Loop,run=Jog_Fwd_Loop|Run_Loop,..." [--loops idle,walk,...]
         [--fps 24] [--keep turn_left,turn_right] [--report <json>]

<in.glb> is the rigged character, normally procanim's anim.glb with its procedural clips; <out.glb> may be the
same path. Each --clips entry names the output clip and the library actions to try in order. Procedural clips in
<in.glb> whose names are not replaced by a library clip stay in the file (the turns: the library has none);
--keep limits which ones stay (none = drop them all). Writes <out>_clips.json (the sidecar, every clip with its
source "library" or "procedural", loop flag and duration) and, with --report, a JSON report.

How a clip is moved across (the standard rest-pose compensated method):
  1. Map the library bones to the target's Mixamo names (gait.bone_map; fingers the target lacks are skipped,
     fingers the library lacks follow the hand).
  2. Put both skeletons into the same reference pose, a T-pose computed for each (gait.tpose_turns: arms level,
     legs straight down, feet straight ahead), after turning each to face -Y.
  3. Per frame, per bone, in armature space: target = source(frame) * inverse(source_reference) *
     target_reference (gait.compensate). The target keeps its own bone lengths; the hips move by the source hips
     move times the leg-length ratio.
  4. Ground and contacts: the height of the lowest foot above its standing height follows the source's (scaled),
     which removes vertical drift and any sinking into the ground; while a source foot is planted the target foot
     follows the source foot's ground track (scaled) through a two-bone leg IK (gait.two_bone_ik), so a foot that
     stands still in the library clip stands still on the character and an in-place walk's feet move at the
     library's own treadmill speed.
  5. Bake to one action per clip (plain clip names), NLA tracks, glTF export as procanim does.
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
import numpy as np  # noqa: E402  (bundled with Blender)
from mathutils import Matrix, Quaternion, Vector  # noqa: E402

T0 = time.time()
_VALUE_FLAGS = ("--library", "--clips", "--loops", "--fps", "--keep", "--report")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
src_glb, dst = os.path.abspath(_pos[0]), os.path.abspath(_pos[1])
libraries = [p for p in _common.opt("--library", "").split(";") if p.strip()]
clip_spec = gait.parse_clip_spec(_common.opt("--clips", ""))
loops_arg = _common.opt("--loops", None)
fps = int(_common.opt("--fps", "24"))
keep_arg = _common.opt("--keep", None)
report_path = _common.opt("--report", None)

report = {"ok": False, "library": libraries, "clips": [], "library_clips": [], "procedural_clips": [], "missing_clips": [],
          "fps": fps}


def fail(msg):
    report["error"] = msg
    if report_path:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=1)
    print("RETARGET_FAIL", msg)
    raise SystemExit(2)


if not libraries or not all(os.path.isfile(p) for p in libraries):
    fail(f"library file missing: {libraries}")
if not clip_spec:
    fail("no clips requested")

# ---- target ------------------------------------------------------------------------------------------------
_common.open_glb(src_glb)
scene = bpy.context.scene
scene.render.fps = fps
tgt = next((o for o in scene.objects if o.type == "ARMATURE"), None)
if tgt is None:
    fail("no armature in the character file")
tgt_objects = set(scene.objects)
proc_actions = {}
if tgt.animation_data:
    for tr in tgt.animation_data.nla_tracks:
        if tr.strips and tr.strips[0].action:
            proc_actions[tr.name] = tr.strips[0].action
    if tgt.animation_data.action and tgt.animation_data.action.name not in proc_actions.values():
        a = tgt.animation_data.action
        proc_actions.setdefault(a.name, a)
    tgt.animation_data.action = None
    for tr in tgt.animation_data.nla_tracks:
        tr.mute = True
old_sidecar = {}
_sc = os.path.splitext(src_glb)[0] + "_clips.json"
if os.path.isfile(_sc):
    with open(_sc) as f:
        old_sidecar = json.load(f)
old_rows = {c["name"]: c for c in old_sidecar.get("clips", [])}

# ---- library ----------------------------------------------------------------------------------------------
lib_actions = {}
src = None
src_ground = [None]
for path in libraries:
    before = set(bpy.data.objects)
    acts_before = set(bpy.data.actions)
    if path.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=False)
    else:
        _common.import_glb(path)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next((o for o in new if o.type == "ARMATURE"), None)
    for o in new:
        if o.type == "MESH" and o.vertex_groups and src_ground[0] is None:
            src_ground[0] = min((o.matrix_world @ v.co).z for v in o.data.vertices)
    for o in new:
        if o.type != "ARMATURE":
            bpy.data.objects.remove(o, do_unlink=True)
    if arm is None:
        continue
    if src is None:
        src = arm
    elif arm is not src:
        # A second library: its skeleton must have the same bone names (UAL2 uses Unreal names, so it brings its
        # own armature); keep the actions with the armature they belong to.
        pass
    for a in bpy.data.actions:
        if a not in acts_before and a.name not in lib_actions:
            lib_actions[a.name] = (arm, a)
    if arm.animation_data:
        arm.animation_data.action = None
        for tr in list(arm.animation_data.nla_tracks):
            arm.animation_data.nla_tracks.remove(tr)
if src is None:
    fail("no armature in the library file")
report["library_actions"] = len(lib_actions)

# ---- skeleton descriptions --------------------------------------------------------------------------------
SEG_CHILD = {"LeftArm": ["LeftForeArm"], "LeftForeArm": ["LeftHand"], "LeftHand": ["LeftHandMiddle1", "LeftHandIndex1", "LeftHandRing1"],
             "RightArm": ["RightForeArm"], "RightForeArm": ["RightHand"],
             "RightHand": ["RightHandMiddle1", "RightHandIndex1", "RightHandRing1"],
             "LeftUpLeg": ["LeftLeg"], "LeftLeg": ["LeftFoot"], "LeftFoot": ["LeftToeBase"],
             "RightUpLeg": ["RightLeg"], "RightLeg": ["RightFoot"], "RightFoot": ["RightToeBase"],
             "Hips": ["Spine"], "Spine": ["Spine1", "Spine2", "Neck"], "Spine1": ["Spine2", "Neck"], "Spine2": ["Neck"],
             "Neck": ["Head"]}


class Skel:
    """World-space rest data of one armature, keyed by Mixamo short name (mapped bones only)."""

    def __init__(self, obj, short_of):
        self.obj = obj
        self.W = obj.matrix_world.copy()
        self.Wq = self.W.to_quaternion().normalized()
        self.short_of = short_of                      # bone name -> short
        self.name_of = {s: n for n, s in short_of.items()}
        self.rest_q, self.rest_head, self.rest_tail = {}, {}, {}
        for b in obj.data.bones:
            m = self.W @ b.matrix_local
            self.rest_q[b.name] = m.to_quaternion().normalized()
            self.rest_head[b.name] = self.W @ b.head_local
            self.rest_tail[b.name] = self.W @ b.tail_local
        lat = self.head("LeftUpLeg") - self.head("RightUpLeg")
        lat.z = 0
        fwd = lat.cross(Vector((0, 0, 1)))
        # G turns the skeleton to face -Y (the glTF front), so both skeletons share one canonical frame.
        self.G = Vector((fwd.x, fwd.y, 0)).normalized().rotation_difference(Vector((0, -1, 0))) if fwd.length > 1e-6 else Quaternion()
        self.Gi = self.G.inverted()

    def head(self, short):
        return self.rest_head[self.name_of[short]]

    def has(self, short):
        return short in self.name_of

    def seg_end(self, short):
        """Rest world position of the joint a bone points at (its child joint, else its tail)."""
        for c in SEG_CHILD.get(short, []):
            if self.has(c):
                return self.head(c)
        return self.rest_tail[self.name_of[short]]

    def parents_short(self):
        out = {}
        for n, s in self.short_of.items():
            b = self.obj.data.bones[n].parent
            while b is not None and b.name not in self.short_of:
                b = b.parent
            out[s] = self.short_of[b.name] if b is not None else None
        return out

    def references(self):
        """Canonical-frame reference (T-pose) orientation of every mapped bone."""
        dirs = {s: tuple(self.G @ (self.seg_end(s) - self.head(s))) for s in self.name_of if s in SEG_CHILD}
        turns = gait.tpose_turns(dirs, self.parents_short())
        return {s: Quaternion(turns.get(s, (1, 0, 0, 0))) @ (self.G @ self.rest_q[self.name_of[s]]) for s in self.name_of}

    def leg_length(self):
        ls = []
        for side in ("Left", "Right"):
            ls.append((self.head(side + "Leg") - self.head(side + "UpLeg")).length + (self.head(side + "Foot") - self.head(side + "Leg")).length)
        return sum(ls) / len(ls)


tgt_names = [b.name for b in tgt.data.bones]
mapping, missing_bones = gait.bone_map([b.name for b in src.data.bones], tgt_names)
missing_core = [c for c in gait.CORE_BONES if not any(gait.mixamo_short(t) == c for t in mapping)]
report["bone_map"] = {gait.mixamo_short(t): s for t, s in mapping.items()}
report["missing_bones"] = missing_bones
if missing_core:
    fail(f"bones not found on the character or in the library: {missing_core}")
T = Skel(tgt, {t: gait.mixamo_short(t) for t in mapping})
S = Skel(src, {s: gait.mixamo_short(t) for t, s in mapping.items()})
Tref, Sref = T.references(), S.references()
ratio = T.leg_length() / max(1e-6, S.leg_length())
report["leg_ratio"] = round(ratio, 4)
mapped_short = list(T.name_of)

# Target bone order (parents first) and rest relations for forward kinematics.
order = []


def _visit(b):
    order.append(b)
    for c in b.children:
        _visit(c)


for b in tgt.data.bones:
    if b.parent is None:
        _visit(b)
t_rest_q = T.rest_q
t_rest_h = T.rest_head
hips_name = T.name_of["Hips"]

# Source evaluation: library pose bone orientations in the canonical frame.
src_pbs = {s: src.pose.bones[S.name_of[s]] for s in mapped_short}


def sample_source(arm, act, frame):
    """{short: canonical orientation}, {short: canonical head position} of the source at a (fractional) frame."""
    ad = arm.animation_data
    if ad.action is not act:
        ad.action = act
        if hasattr(ad, "action_slot") and len(getattr(act, "slots", [])) > 0:
            ad.action_slot = act.slots[0]
    f = int(math.floor(frame))
    scene.frame_set(f, subframe=frame - f)
    Wq, W = S.Wq, S.W
    qs, ps = {}, {}
    for s, pb in src_pbs.items():
        qs[s] = S.G @ (Wq @ pb.matrix.to_quaternion())
        ps[s] = S.G @ (W @ pb.head)
    return qs, ps


src.animation_data_create()
S_rest_canon = {s: S.G @ S.head(s) for s in mapped_short}
T_rest_canon = {s: T.G @ T.head(s) for s in mapped_short}


def target_frame(qs, ps):
    """Canonical world orientation of every target bone and the hips position, before the ground pass."""
    out = {}
    for b in order:
        s = T.short_of.get(b.name)
        if s is not None:
            out[b.name] = Quaternion(gait.compensate(tuple(qs[s]), tuple(Sref[s]), tuple(Tref[s])))
        elif b.parent is not None:
            # Unmapped bone: rides on its parent (rest relation kept).
            p = b.parent.name
            out[b.name] = out[p] @ ((T.G @ t_rest_q[p]).inverted() @ (T.G @ t_rest_q[b.name]))
        else:
            out[b.name] = T.G @ t_rest_q[b.name]
    hips = T_rest_canon["Hips"] + (ps["Hips"] - S_rest_canon["Hips"]) * ratio
    return out, hips


def fk(orient, hips):
    """Canonical head positions of every target bone from canonical orientations and the hips position."""
    pos = {}
    for b in order:
        if b.parent is None:
            pos[b.name] = hips if b.name == hips_name else (T.G @ t_rest_h[b.name])
            continue
        p = b.parent.name
        delta = orient[p] @ (T.G @ t_rest_q[p]).inverted()
        pos[b.name] = pos[p] + delta @ (T.G @ (t_rest_h[b.name] - t_rest_h[p]))
    return pos


def short_pos(pos, s):
    return pos[T.name_of[s]]


FEET = {"Left": ("LeftFoot", "LeftToeBase"), "Right": ("RightFoot", "RightToeBase")}


def clearance(posf, side, rest):
    """Height of a foot above its standing height: the lower of ankle and toe joints relative to their rest."""
    vals = []
    for s in FEET[side]:
        if s in posf and s in rest:
            vals.append(posf[s].z - rest[s].z)
    return min(vals) if vals else 0.0


def seg_dir(pos, a, b):
    return (pos[b] - pos[a]).normalized()


def faithful_angles(qs_pos, t_pos):
    """Directions of a few key segments, source vs target, in degrees."""
    out = {}
    for a, b in (("LeftUpLeg", "LeftLeg"), ("LeftLeg", "LeftFoot"), ("RightUpLeg", "RightLeg"), ("RightLeg", "RightFoot"),
                 ("LeftArm", "LeftForeArm"), ("LeftForeArm", "LeftHand"), ("RightArm", "RightForeArm"), ("RightForeArm", "RightHand"),
                 ("Spine", "Neck")):
        if a in qs_pos and b in qs_pos and T.has(a) and T.has(b):
            ds = (qs_pos[b] - qs_pos[a]).normalized()
            dt = (short_pos(t_pos, b) - short_pos(t_pos, a)).normalized()
            out[a] = math.degrees(ds.angle(dt, 0.0))
    return out


def joint_bends(posf):
    """Knee and elbow bends (degrees) from short-name positions."""
    out = {}
    for side in ("Left", "Right"):
        for a, m, e, key in ((side + "UpLeg", side + "Leg", side + "Foot", side + "Knee"),
                             (side + "Arm", side + "ForeArm", side + "Hand", side + "Elbow")):
            if a in posf and m in posf and e in posf:
                out[key] = gait.bend_deg(tuple(posf[a]), tuple(posf[m]), tuple(posf[e]))
    return out


def knee_forward(posf, fwd):
    """True when both knees point the body's way (fwd: the hips' forward). A knee bent toward the back is an
    inverted knee."""
    ok = True
    for side in ("Left", "Right"):
        h, k, a = posf[side + "UpLeg"], posf[side + "Leg"], posf[side + "Foot"]
        line = a - h
        if line.length < 1e-6:
            continue
        off = (k - h) - line.normalized() * (k - h).dot(line.normalized())
        if gait.bend_deg(tuple(h), tuple(k), tuple(a)) > 15 and off.dot(fwd) < -0.02 * line.length:
            ok = False
    return ok


# Target meshes: the rest ground (lowest vertex standing) and the torso vertices, for the sinking check.
tgt_meshes = [o for o in tgt_objects if o.type == "MESH" and o.vertex_groups]
_TORSO = ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftShoulder", "RightShoulder", "LeftUpLeg", "RightUpLeg")
_torso_names = {T.name_of[x] for x in _TORSO if T.has(x)}
torso_mask = {}
tgt_ground = None
for _o in tgt_meshes:
    _names = {g.index: g.name for g in _o.vertex_groups}
    torso_mask[_o.name] = np.array([bool(v.groups) and _names.get(max(v.groups, key=lambda g: g.weight).group) in _torso_names
                                    for v in _o.data.vertices], dtype=bool)
    _z = min((_o.matrix_world @ v.co).z for v in _o.data.vertices)
    tgt_ground = _z if tgt_ground is None else min(tgt_ground, _z)
if tgt_ground is None:
    tgt_ground = min(T.rest_head[n].z for n in T.rest_head)
# Half the thickness of each hand (its palm vertices across the body's width at rest, where the arms hang with the
# palms inward): a hand resting on the ground keeps its wrist at least this high so the glove stays above ground.
hand_radius = {}
for _side in ("Left", "Right"):
    if not T.has(_side + "Hand"):
        continue
    _hn = T.name_of[_side + "Hand"]
    _xs = []
    for _o in tgt_meshes:
        _gi = _o.vertex_groups.get(_hn)
        if _gi is None:
            continue
        for v in _o.data.vertices:
            if v.groups and max(v.groups, key=lambda g: g.weight).group == _gi.index:
                _xs.append((T.G @ (_o.matrix_world @ v.co)).x)
    hand_radius[_side] = 0.5 * (max(_xs) - min(_xs)) if len(_xs) > 3 else 0.0
if src_ground[0] is None:
    src_ground[0] = min(S.head(x).z for x in ("LeftToeBase", "RightToeBase", "LeftFoot", "RightFoot") if S.has(x))


def torso_lowest():
    """Lowest world z of the target's torso vertices in the current evaluated pose (None without a mesh)."""
    dg = bpy.context.evaluated_depsgraph_get()
    low = None
    for o in tgt_meshes:
        mask = torso_mask.get(o.name)
        if mask is None or not mask.any():
            continue
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        if len(me.vertices) == len(mask):
            co = np.empty(len(me.vertices) * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)[mask]
            mw = np.array(ev.matrix_world)
            z = float((co @ mw[2, :3] + mw[2, 3]).min())
            low = z if low is None else min(low, z)
        ev.to_mesh_clear()
    return low


def ik_two(fr_, a_s, m_s, e_s, want):
    """Two-bone IK on short names a_s (upper), m_s (middle) toward `want` for the end joint e_s, bending the way the
    limb already bends; the end bone keeps its world orientation."""
    pos = fk(fr_["orient"], fr_["hips"])
    a_n, m_n, e_n = T.name_of[a_s], T.name_of[m_s], T.name_of[e_s]
    hp, kp, ap = pos[a_n], pos[m_n], pos[e_n]
    l1 = (t_rest_h[m_n] - t_rest_h[a_n]).length
    l2 = (t_rest_h[e_n] - t_rest_h[m_n]).length
    mid, end_, _ = gait.two_bone_ik(tuple(hp), tuple(want), l1, l2, tuple(kp + (kp - (hp + ap) / 2) * 2.0), max_bend_deg=165.0)
    mid, end_ = Vector(mid), Vector(end_)
    o = fr_["orient"]
    o[a_n] = (kp - hp).rotation_difference(mid - hp) @ o[a_n]
    o[m_n] = (ap - kp).rotation_difference(end_ - mid) @ o[m_n]


def solve_frames(frames, loop, lift):
    """Ground pass, torso lift, foot and hand contacts on fresh copies of the retargeted frames. -> (frames, ik count)."""
    S_rest_pos = S_rest_canon
    T_rest_short = T_rest_canon
    work = [{"qs": f["qs"], "ps": f["ps"], "orient": dict(f["orient"]), "hips": f["hips"].copy()} for f in frames]
    # Ground pass: lowest-foot clearance follows the source's, scaled; then the torso lift where it would sink.
    for i, fr_ in enumerate(work):
        pos = fk(fr_["orient"], fr_["hips"])
        tshort = {x: short_pos(pos, x) for x in mapped_short}
        cs = min(clearance(fr_["ps"], side, S_rest_pos) for side in FEET)
        ct = min(clearance(tshort, side, T_rest_short) for side in FEET)
        fr_["hips"] = fr_["hips"] + Vector((0, 0, ratio * cs - ct + lift[i]))
    # Feet: while a source foot is planted, the target ankle follows the source ankle's ground track.
    leg = S.leg_length()
    near, far = 0.03 * leg, 0.07 * leg
    ik_moves = 0
    for side in FEET:
        ank = side + "Foot"
        cl = [clearance(fr_["ps"], side, S_rest_pos) for fr_ in work]
        for seg in gait.contact_segments([c < far for c in cl], cyclic=loop):
            offs = []
            for i in seg:
                tp = short_pos(fk(work[i]["orient"], work[i]["hips"]), ank)
                sp = work[i]["ps"][ank]
                offs.append(Vector((tp.x - ratio * sp.x, tp.y - ratio * sp.y)))
            anchor = sum(offs, Vector((0, 0))) / len(offs)
            for i in seg:
                fr_ = work[i]
                w = gait.contact_weight(cl[i], near, far)
                if w <= 0:
                    continue
                cur = short_pos(fk(fr_["orient"], fr_["hips"]), ank)
                sp = fr_["ps"][ank]
                want = Vector((anchor.x + ratio * sp.x, anchor.y + ratio * sp.y, T_rest_short[ank].z + ratio * (sp.z - S_rest_pos[ank].z)))
                want = cur.lerp(want, w)
                if (want - cur).length < 1e-4:
                    continue
                ik_two(fr_, side + "UpLeg", side + "Leg", ank, want)
                ik_moves += 1
    # Hands: a hand the library rests on (or near) the ground rests at the same scaled height on the character.
    hnear, hfar = 0.06 * leg, 0.12 * leg
    for side in ("Left", "Right"):
        hand = side + "Hand"
        if not all(T.has(x) for x in (side + "Arm", side + "ForeArm", hand)) or hand not in S_rest_pos:
            continue
        for fr_ in work:
            c = fr_["ps"][hand].z - src_ground[0]
            w = gait.contact_weight(c, hnear, hfar)
            if w <= 0:
                continue
            cur = short_pos(fk(fr_["orient"], fr_["hips"]), hand)
            want = Vector((cur.x, cur.y, tgt_ground + max(ratio * c, hand_radius.get(side, 0.0))))
            want = cur.lerp(want, w)
            if abs(want.z - cur.z) < 1e-4:
                continue
            ik_two(fr_, side + "Arm", side + "ForeArm", hand, want)
            ik_moves += 1
    return work, ik_moves


def retarget_clip(name, arm, act, loop):
    fr = act.frame_range
    start, end = float(fr[0]), float(fr[1])
    n = max(1, int(round(end - start)))
    base = []
    for i in range(n + 1):
        f = start + (end - start) * i / n
        qs, ps = sample_source(arm, act, f)
        orient, hips = target_frame(qs, ps)
        base.append({"qs": qs, "ps": ps, "orient": orient, "hips": hips})
    lift = [0.0] * len(base)
    act_out = None
    sank = 0.0
    for attempt in range(2):
        frames, ik_moves = solve_frames(base, loop, lift)
        act_out = bake(name, act_out, frames, loop)
        if attempt or not tgt_meshes:
            break
        # Sinking check: where the body comes low (lying, rolling, crouching), measure the torso mesh and lift the
        # hips by however far it went below the ground; limbs in contact are solved again on the lifted body.
        rest_hips_z = T_rest_canon["Hips"].z
        need = [0.0] * len(frames)
        tgt.animation_data.action = act_out
        for i, fr_ in enumerate(frames):
            if fr_["hips"].z > tgt_ground + 0.6 * (rest_hips_z - tgt_ground):
                continue
            scene.frame_set(i + 1)
            low = torso_lowest()
            if low is not None and low < tgt_ground:
                need[i] = tgt_ground - low
        tgt.animation_data.action = None
        if max(need) < 0.002:
            break
        sank = max(need)
        lift = [max(need[max(0, i - 2):i + 3]) for i in range(len(need))]  # widened so the lift eases in and out
    return act_out, frames, ik_moves, sank


def bake(name, act_out, frames, loop):
    """Write the frames as quaternion keys (and the hips location) into one action named after the clip."""
    if act_out is None:
        act_out = bpy.data.actions.new(name)
    tgt.animation_data_create()
    ad = tgt.animation_data
    ad.action = act_out
    bones_out = [b.name for b in order]
    rot_keys = {b: [] for b in bones_out}
    loc_keys = []
    Wtq_i = T.Wq.inverted()
    Wt_i = T.W.inverted()
    for i, fr_ in enumerate(frames):
        o = fr_["orient"]
        arm_q = {b: Wtq_i @ (T.Gi @ o[b]) for b in bones_out}
        for b in order:
            # Pose rotation R(b) = R(parent) * rest(parent)^-1 * rest(b) * basis(b), in armature space.
            rest_b = Wtq_i @ t_rest_q[b.name]
            if b.parent is None:
                basis = rest_b.inverted() @ arm_q[b.name]
            else:
                p = b.parent.name
                basis = rest_b.inverted() @ (Wtq_i @ t_rest_q[p]) @ arm_q[p].inverted() @ arm_q[b.name]
            basis.normalize()
            if rot_keys[b.name] and rot_keys[b.name][-1].dot(basis) < 0:
                basis = -basis
            rot_keys[b.name].append(basis)
        hips_world = T.Gi @ fr_["hips"]
        hb = tgt.data.bones[hips_name]
        d_arm = (Wt_i @ hips_world) - hb.head_local
        loc_keys.append(hb.matrix_local.to_3x3().inverted() @ d_arm)
    write_keys(act_out, bones_out, rot_keys, hips_name, loc_keys)
    if hasattr(act_out, "use_frame_range"):
        act_out.use_frame_range = True
        act_out.frame_start, act_out.frame_end = 1, len(frames)
    if hasattr(act_out, "use_cyclic"):
        act_out.use_cyclic = bool(loop)
    ad.action = None
    return act_out


def clip_quality(frames, ik_moves, sank):
    """How faithful the clip is to the library: limb segment directions and knee and elbow bends, source vs target."""
    angles, bends_s, bends_t = {}, {}, {}
    knees_bad = 0
    for fr_ in frames:
        o = fr_["orient"]
        pos = fk(o, fr_["hips"])
        tshort = {x: short_pos(pos, x) for x in mapped_short}
        for k, v in faithful_angles(fr_["ps"], pos).items():
            angles.setdefault(k, []).append(v)
        for k, v in joint_bends(fr_["ps"]).items():
            bends_s.setdefault(k, []).append(v)
        for k, v in joint_bends(tshort).items():
            bends_t.setdefault(k, []).append(v)
        hips_turn = o[hips_name] @ (T.G @ t_rest_q[hips_name]).inverted()
        if not knee_forward(tshort, hips_turn @ Vector((0, -1, 0))):
            knees_bad += 1
    fid = {k: {"max_deg": round(max(v), 1), "mean_deg": round(sum(v) / len(v), 2)} for k, v in angles.items()}
    bend_cmp = {k: {"source_max": round(max(bends_s[k]), 1), "target_max": round(max(bends_t.get(k, [0])), 1),
                    "max_diff": round(max(abs(a - b) for a, b in zip(bends_s[k], bends_t.get(k, bends_s[k]))), 1)}
                for k in bends_s}
    return {"segment_angle": fid, "bends": bend_cmp, "ik_frames": ik_moves, "inverted_knee_frames": knees_bad,
            "torso_lift_m": round(sank, 4)}


def write_keys(act, bones_out, rot_keys, hips, loc_keys):
    """Write quaternion keys for every bone (and the hips location) at frames 1..n."""
    n = len(loc_keys)
    tgt.animation_data.action = act
    fast = hasattr(act, "fcurve_ensure_for_datablock")
    for b in bones_out:
        pb = tgt.pose.bones[b]
        pb.rotation_mode = "QUATERNION"
    if not fast:
        for i in range(n):
            for b in bones_out:
                pb = tgt.pose.bones[b]
                pb.rotation_quaternion = rot_keys[b][i]
                pb.keyframe_insert("rotation_quaternion", frame=i + 1)
            pb = tgt.pose.bones[hips]
            pb.location = loc_keys[i]
            pb.keyframe_insert("location", frame=i + 1)
        return

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

    for b in bones_out:
        qs = rot_keys[b]
        for k in range(4):
            put(f'pose.bones["{b}"].rotation_quaternion', k, [q[k] for q in qs])
    for k in range(3):
        put(f'pose.bones["{hips}"].location', k, [v[k] for v in loc_keys])


# ---- run every clip ---------------------------------------------------------------------------------------
loops = set(c.strip() for c in loops_arg.split(",")) if loops_arg else None
made = []
detail = {}
for name, candidates in clip_spec:
    pick = next((c for c in candidates if c in lib_actions), None)
    if pick is None:
        report["missing_clips"].append(name)
        continue
    arm, act = lib_actions[pick]
    if arm is not src:
        continue  # a clip from a second library skeleton is not supported in one pass
    loop = (name in loops) if loops is not None else (pick.lower().endswith("_loop") or name in gait.LOOPING)
    t1 = time.time()
    a_out, frames_, ik_moves_, sank_ = retarget_clip(name, arm, act, loop)
    nfr = len(frames_)
    det = clip_quality(frames_, ik_moves_, sank_)
    det["from"] = pick
    det["seconds"] = round(time.time() - t1, 2)
    detail[name] = det
    made.append({"name": name, "action": a_out, "frames": nfr, "loop": loop, "from": pick})
report["detail"] = detail

# ---- assemble the clip list: library first in the requested order, procedural clips kept for what is left ----
lib_names = {m["name"] for m in made}
keep = None if keep_arg is None else {c.strip() for c in keep_arg.split(",") if c.strip() and c.strip() != "none"}
kept_proc = [n for n in proc_actions if n not in lib_names and (keep is None or n in keep)]

# Remove the library skeleton and its actions, and the replaced procedural clips.
for arm in {a for a, _ in lib_actions.values()}:
    bpy.data.objects.remove(arm, do_unlink=True)
for nm, (_, a) in list(lib_actions.items()):
    bpy.data.actions.remove(a)
ad = tgt.animation_data
for tr in list(ad.nla_tracks):
    if tr.name not in kept_proc:
        ad.nla_tracks.remove(tr)
for nm, a in proc_actions.items():
    if nm not in kept_proc and a.users == 0:
        bpy.data.actions.remove(a)
ad.action = None
for pb in tgt.pose.bones:
    pb.rotation_quaternion = Quaternion()
    pb.location = Vector()
    pb.scale = Vector((1, 1, 1))

# Order: the requested clip order; procedural clips go where the spec places them, or after the library ones.
spec_order = [n for n, _ in clip_spec]
final = []
by_made = {m["name"]: m for m in made}
proc_order = [n for n in old_rows if n in kept_proc] + [n for n in kept_proc if n not in old_rows]
for n in spec_order:
    if n in by_made:
        final.append(("library", n))
    elif n in kept_proc:
        final.append(("procedural", n))
# A kept procedural clip goes after the clip that came before it in the procedural order (the turns after run).
old_order = list(old_rows) or proc_order
for n in proc_order:
    if ("procedural", n) in final:
        continue
    names = [x for _, x in final]
    i = old_order.index(n) if n in old_order else -1
    prev = next((old_order[j] for j in range(i - 1, -1, -1) if old_order[j] in names), None) if i > 0 else None
    if prev is None:
        final.append(("procedural", n))
        continue
    at = names.index(prev) + 1
    while at < len(final) and final[at][0] == "procedural":
        at += 1
    final.insert(at, ("procedural", n))

for tr in list(ad.nla_tracks):
    tr.mute = False
existing = {tr.name: tr for tr in ad.nla_tracks}
for kind, n in final:
    if kind == "library":
        a = by_made[n]["action"]
        tr = ad.nla_tracks.new()
        tr.name = n
        st = tr.strips.new(n, 1, a)
        st.name = n
        if hasattr(st, "action_slot") and len(getattr(a, "slots", [])) > 0:
            st.action_slot = a.slots[0]

rows = []
for kind, n in final:
    if kind == "library":
        m = by_made[n]
        total = m["frames"] - 1
        rows.append({"name": n, "loop": m["loop"], "frame_start": 0, "frame_end": total, "frames": m["frames"],
                     "seconds": round(total / fps, 4), "motion_frames": total, "hold_last": n == "death", "in_place": True,
                     "source": "library", "from": m["from"]})
    else:
        r = dict(old_rows.get(n) or {})
        if not r:
            a = proc_actions[n]
            total = int(round(a.frame_range[1] - a.frame_range[0]))
            r = {"name": n, "loop": n in gait.LOOPING, "frame_start": 0, "frame_end": total, "frames": total + 1,
                 "seconds": round(total / fps, 4), "motion_frames": total, "hold_last": n == "death", "in_place": True}
        r["source"] = "procedural"
        rows.append(r)

scene.frame_start, scene.frame_end = 1, max([r["frame_end"] + 1 for r in rows] or [fps * 2])
scene.frame_set(1)
for o in list(scene.objects):
    if o not in tgt_objects:
        bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.object.select_all(action="SELECT")
_gltf_args = dict(filepath=dst, export_format="GLB", use_selection=True, export_animations=bool(rows),
                  export_animation_mode="NLA_TRACKS", export_force_sampling=True, export_apply=False)
try:
    bpy.ops.export_scene.gltf(**_gltf_args, export_anim_slide_to_zero=True)
except TypeError:
    bpy.ops.export_scene.gltf(**_gltf_args)

base = os.path.splitext(dst)[0]
sidecar = {k: v for k, v in old_sidecar.items() if k not in ("clips",)}
sidecar.update({"version": 2, "fps": fps, "root_motion": False, "clips": rows,
                "clips_source": "library" if made else "procedural",
                "library": [os.path.basename(p) for p in libraries], "licence": "CC0 (Quaternius Universal Animation Library)"})
with open(base + "_clips.json", "w") as f:
    json.dump(sidecar, f, indent=1)

report.update({"ok": bool(made), "clips": [r["name"] for r in rows], "library_clips": [m["name"] for m in made],
               "procedural_clips": [n for k, n in final if k == "procedural"], "clip_table": rows,
               "seconds": round(time.time() - T0, 2)})
if report_path:
    with open(report_path, "w") as f:
        json.dump(report, f, indent=1)
print("RETARGET_OK", len(made), "library clips;", ",".join(report["clips"]), "in", report["seconds"], "s")
