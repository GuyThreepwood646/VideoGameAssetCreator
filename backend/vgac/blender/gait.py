"""Gait and clip maths for procanim.py, kept free of bpy and mathutils so plain-Python tests can check it.

procanim.py runs inside Blender and imports this module from the same folder (sys.path insert, like _common);
the tests import it as vgac.blender.gait. Everything here is numbers in, numbers out: phases in [0, 1), angles
in degrees, times as fractions of a clip.

Motions (the --motion argument):

    biped, quadruped, multileg   walk on legs; the gait follows the number of legs found
    winged                       legs plus wings: flap, glide, takeoff, land
    serpent                      no legs: a travelling wave along the spine
    none                         no clips
    legs, auto                   pick biped, quadruped or multileg from the number of legs found
    wings, wave, library         aliases from the category yaml: winged, serpent, biped with idle and walk only
"""
from __future__ import annotations

import math

MOTIONS = ("biped", "quadruped", "multileg", "winged", "serpent", "none")
MOTION_ALIASES = {
    "legs": "legs", "auto": "legs", "": "legs",
    "wings": "winged", "flying": "winged", "bird": "winged", "dragon": "winged",
    "wave": "serpent", "snake": "serpent", "fish": "serpent", "swim": "serpent",
    "library": "biped", "humanoid": "biped", "spider": "multileg", "insect": "multileg",
}

# Every clip procanim can make, in the order they are written to the GLB and the sidecar.
CLIP_ORDER = ("idle", "walk", "run", "slither", "flap", "glide", "takeoff", "land",
              "turn_left", "turn_right", "attack", "hit", "death")
CLIP_ALIASES = {
    "fly": ["flap"], "flight": ["flap"], "turn": ["turn_left", "turn_right"], "die": ["death"], "dead": ["death"],
    "get_hit": ["hit"], "hurt": ["hit"], "swim": ["slither"], "crawl": ["slither"], "bite": ["attack"],
}
# Cyclic clips carry the loop flag (G32); everything else plays once. Death holds its last frame.
LOOPING = frozenset({"idle", "walk", "run", "slither", "flap", "glide"})

# Plan 3.7 table: the clips each motion makes when the caller names none.
DEFAULT_CLIPS = {
    "biped": ["idle", "walk", "run", "turn_left", "turn_right", "attack", "hit", "death"],
    "quadruped": ["idle", "walk", "run", "turn_left", "turn_right", "attack", "hit", "death"],
    "multileg": ["idle", "walk", "run", "turn_left", "turn_right", "attack", "hit", "death"],
    "winged": ["idle", "walk", "flap", "glide", "takeoff", "land", "attack", "hit", "death"],
    "serpent": ["idle", "slither", "turn_left", "turn_right", "attack", "hit", "death"],
    "none": [],
}
LEGACY_CLIPS = ["idle", "walk"]  # no --motion and no --clips: what procanim made before G30 and G31

# Clip lengths in seconds at move speed 1.0. Run is the walk at RUN_FREQ times the frequency (plan 3.7 item 1).
RUN_FREQ = 1.8
RUN_STRIDE = 1.4
DURATION = {
    "idle": 2.0, "walk": 1.0, "run": 1.0 / RUN_FREQ, "slither": 1.2, "flap": 0.6, "glide": 3.0,
    "takeoff": 1.2, "land": 1.0, "turn_left": 1.0, "turn_right": 1.0, "attack": 0.8, "hit": 0.4, "death": 1.2,
}
DURATION_BY_MOTION = {("multileg", "walk"): 0.8, ("multileg", "run"): 0.8 / RUN_FREQ, ("serpent", "idle"): 2.4}
DEATH_HOLD = 0.3  # seconds the final death pose is held inside the clip, so the last frame is the resting pose

MOVE_SIZE = {"small": 0.7, "normal": 1.0, "big": 1.3}
MOVE_SPEED = {"slow": 0.8, "normal": 1.0, "fast": 1.25}

# Fraction of the cycle a foot is on the ground, per gait.
DUTY = {"alternate": 0.6, "walk4": 0.65, "trot": 0.5, "gallop": 0.4, "tripod": 0.5, "wave": 0.7, "alternating": 0.45, "run2": 0.4}


# ---- small curve helpers -------------------------------------------------------------------------------------

def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def envelope(t: float, keys: list[tuple[float, float]]) -> float:
    """Value at time t of a piecewise curve through (time, value) keys, smoothstep between keys, held outside."""
    if not keys:
        return 0.0
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            if t1 <= t0:
                return v1
            return v0 + (v1 - v0) * smoothstep((t - t0) / (t1 - t0))
    return keys[-1][1]


def wrap(x: float) -> float:
    return x - math.floor(x)


# ---- legs ----------------------------------------------------------------------------------------------------

def foot_cycle(u: float, duty: float) -> tuple[float, float]:
    """(swing, lift) of one foot at cycle fraction u.

    swing runs +1 (foot forward) to -1 (foot back) linearly while the foot is planted (the first `duty` of the
    cycle), then back to +1 with an ease while it is in the air. lift is 0 on the ground and a half sine in the
    air. Both are periodic and continuous at u = 0 and u = 1.
    """
    u = wrap(u)
    duty = min(0.95, max(0.05, duty))
    if u < duty:
        return 1.0 - 2.0 * (u / duty), 0.0
    s = (u - duty) / (1.0 - duty)
    return -1.0 + 2.0 * smoothstep(s), math.sin(math.pi * s)


def side_ranks(legs: list[dict]) -> list[int]:
    """Rank of each leg among the legs on its own side, 0 = front-most. legs: [{'left': bool, 'fwd': float}]."""
    ranks = [0] * len(legs)
    for side in (True, False):
        idx = [i for i, l in enumerate(legs) if bool(l["left"]) == side]
        idx.sort(key=lambda i: -float(legs[i]["fwd"]))
        for r, i in enumerate(idx):
            ranks[i] = r
    return ranks


def gait_for(n_legs: int, run: bool = False) -> str:
    """Gait name for a leg count: two legs alternate, four walk in lateral sequence (gallop when running),
    six use the alternating tripod, eight or more a back-to-front wave (alternating sets when running)."""
    if n_legs <= 3:
        return "run2" if run else "alternate"
    if n_legs <= 5:
        return "gallop" if run else "walk4"
    if n_legs <= 7:
        return "tripod"
    return "alternating" if run else "wave"


def leg_phases(legs: list[dict], gait: str) -> list[float]:
    """Cycle offset in [0, 1) for each leg. legs: [{'left': bool, 'front': bool, 'fwd': float}] where fwd is the
    leg's position along the body's forward axis (bigger = nearer the head)."""
    ranks = side_ranks(legs)
    out = []
    for leg, r in zip(legs, ranks):
        left = bool(leg["left"])
        front = bool(leg.get("front", r == 0))
        if gait in ("alternate", "run2"):
            p = 0.0 if left else 0.5
        elif gait == "walk4":
            # Lateral sequence: left hind, left fore, right hind, right fore.
            p = {(True, False): 0.0, (True, True): 0.25, (False, False): 0.5, (False, True): 0.75}[(left, front)]
        elif gait == "trot":
            p = 0.0 if left == front else 0.5
        elif gait == "gallop":
            p = {(True, False): 0.0, (False, False): 0.08, (True, True): 0.5, (False, True): 0.58}[(left, front)]
        elif gait in ("tripod", "alternating"):
            # Front and back legs of one side swing with the middle leg of the other side.
            p = 0.5 * ((r + (0 if left else 1)) % 2)
        elif gait == "wave":
            n_side = sum(1 for l in legs if bool(l["left"]) == left)
            from_rear = n_side - 1 - r
            p = wrap(from_rear / max(1, n_side) + (0.0 if left else 0.5))
        else:
            raise ValueError(f"unknown gait {gait!r}")
        out.append(wrap(p))
    return out


def tripod_groups(legs: list[dict]) -> tuple[list[int], list[int]]:
    """Indices of the two alternating sets for six (or more) legs."""
    ph = leg_phases(legs, "tripod")
    return [i for i, p in enumerate(ph) if p < 0.25], [i for i, p in enumerate(ph) if p >= 0.25]


def body_bob(u: float, steps_per_cycle: int) -> float:
    """0..1 vertical bob, highest at mid stance of each step."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * steps_per_cycle * u)


# ---- wings ---------------------------------------------------------------------------------------------------

def flap_curve(u: float, down: float = 0.4) -> float:
    """Wing elevation in [-1, 1] at cycle fraction u: +1 fully up, -1 fully down. The downstroke takes `down` of
    the cycle (plan 3.7 item 7: 40 percent), the upstroke the rest. Continuous and periodic."""
    u = wrap(u)
    if u < down:
        return math.cos(math.pi * u / down)
    return -math.cos(math.pi * (u - down) / (1.0 - down))


# ---- serpent wave --------------------------------------------------------------------------------------------

def wave_amplitude_deg(n_joints: int, waves: float = 1.25, disp_frac: float = 0.08) -> float:
    """Peak bend per joint (degrees) so the tail end swings about disp_frac of the body length sideways.

    For a sine of lateral displacement Y and wavelength L / waves, curvature peaks at Y (2 pi waves / L)^2; one
    joint covers L / n of the length, so its bend is Y (2 pi waves)^2 / (L n).
    """
    n = max(2, n_joints)
    return math.degrees(min(0.6, disp_frac * (2 * math.pi * waves) ** 2 / n))


def wave_angle(u_body: float, t: float, amp: float, waves: float = 1.25, min_frac: float = 0.2) -> float:
    """Bend (degrees) at body position u_body (0 = head, 1 = tail tip) and cycle time t.

    A(u) sin(2 pi (t - waves u)) with A rising from min_frac to 1 of amp toward the tail (plan 3.7 item 8). The
    phase falls with u, so crests travel from head to tail, which is what pushes a snake or fish forward.
    """
    a = amp * (min_frac + (1.0 - min_frac) * min(1.0, max(0.0, u_body)))
    return a * math.sin(2 * math.pi * (t - waves * u_body))


def wave_angles(n: int, t: float, amp: float, waves: float = 1.25, min_frac: float = 0.2) -> list[float]:
    """wave_angle for n joints spread evenly from head (index 0) to tail."""
    return [wave_angle(i / max(1, n - 1), t, amp, waves, min_frac) for i in range(n)]


# ---- argument resolution and the clip table ------------------------------------------------------------------

def factor(value, table: dict[str, float]) -> float:
    """'small' / 'normal' / 'big' (or slow / fast) or a number -> multiplier; unknown text -> 1.0."""
    if value is None:
        return 1.0
    if isinstance(value, (int, float)):
        return float(value)
    v = str(value).strip().lower()
    if v in table:
        return table[v]
    try:
        return float(v)
    except ValueError:
        return 1.0


def kind_for_legs(n_legs: int) -> str:
    if n_legs >= 6:
        return "multileg"
    if n_legs >= 4:
        return "quadruped"
    if n_legs >= 2:
        return "biped"
    return "unknown"


def resolve_motion(requested: str | None, n_legs: int) -> str:
    """The motion to animate: the requested one (aliases resolved), or for legs and auto the kind the leg count
    implies. A legs request on a skeleton with no legs found gives 'unknown' (idle and a torso sway walk)."""
    m = (requested or "auto").strip().lower()
    m = MOTION_ALIASES.get(m, m)
    if m == "legs":
        return kind_for_legs(n_legs)
    if m not in MOTIONS:
        raise ValueError(f"unknown motion {requested!r}; expected one of {MOTIONS + tuple(MOTION_ALIASES)}")
    return m


def resolve_clips(motion: str, requested, *, legacy: bool = False) -> tuple[list[str], list[str]]:
    """(clips, unknown_names). requested: None, a comma string or a list. None gives the motion's default list
    (LEGACY_CLIPS when legacy is true, which is the no-arguments call and the humanoid 'library' motion)."""
    if requested is None or requested == "" or requested == []:
        if legacy:
            return list(LEGACY_CLIPS) if motion != "none" else [], []
        return list(DEFAULT_CLIPS.get(motion, LEGACY_CLIPS)), []
    names = requested.split(",") if isinstance(requested, str) else list(requested)
    want, unknown = set(), []
    for raw in names:
        n = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
        if not n or n == "none":
            continue
        expanded = CLIP_ALIASES.get(n, [n])
        for e in expanded:
            if e in CLIP_ORDER:
                want.add(e)
            else:
                unknown.append(str(raw).strip())
    return [c for c in CLIP_ORDER if c in want], unknown


def clip_supported(name: str, motion: str, n_legs: int, wings_found: bool, spine_len: int) -> bool:
    """Whether the skeleton has what the clip needs (legs to walk, wings to fly, a spine to slither)."""
    if name in ("flap", "glide", "takeoff", "land"):
        return wings_found
    if name == "slither":
        return spine_len >= 3
    if name in ("walk", "run"):
        return n_legs >= 1 or motion in ("serpent", "unknown") or spine_len >= 1
    return True


def clip_seconds(name: str, motion: str, speed: float = 1.0) -> float:
    base = DURATION_BY_MOTION.get((motion, name), DURATION[name])
    return base / max(0.1, speed)


def clip_frames(name: str, motion: str, fps: int, speed: float = 1.0) -> tuple[int, int]:
    """(motion_frames, total_frames). Keys run 0..total; the death clip adds DEATH_HOLD seconds of held pose."""
    n = max(4, int(round(clip_seconds(name, motion, speed) * fps)))
    total = n + (int(round(DEATH_HOLD * fps)) if name == "death" else 0)
    return n, total


def clip_table(clips: list[str], motion: str, fps: int, speed: float = 1.0) -> list[dict]:
    """Sidecar rows: name, loop, frame range in the exported clip (starts at 0), seconds, motion frames."""
    rows = []
    for c in clips:
        n, total = clip_frames(c, motion, fps, speed)
        rows.append({"name": c, "loop": c in LOOPING, "frame_start": 0, "frame_end": total, "frames": total + 1,
                     "seconds": round(total / fps, 4), "motion_frames": n, "hold_last": c == "death", "in_place": True})
    return rows


# ---- vectors and quaternions (w, x, y, z), for the retarget and the IK ---------------------------------------

def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_norm(a, fallback=(0.0, 0.0, 1.0)):
    n = v_len(a)
    return v_scale(a, 1.0 / n) if n > 1e-12 else tuple(fallback)


def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def q_conj(q):
    return (q[0], -q[1], -q[2], -q[3])


def q_normalize(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def q_axis_angle(axis, rad):
    ax = v_norm(axis)
    s = math.sin(rad / 2)
    return (math.cos(rad / 2), ax[0] * s, ax[1] * s, ax[2] * s)


def q_rotate(q, v):
    """Rotate vector v by unit quaternion q."""
    p = q_mul(q_mul(q, (0.0, v[0], v[1], v[2])), q_conj(q))
    return (p[1], p[2], p[3])


def q_from_to(u, v):
    """The smallest rotation taking direction u onto direction v."""
    a, b = v_norm(u), v_norm(v)
    d = v_dot(a, b)
    if d > 1.0 - 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    if d < -1.0 + 1e-12:
        ortho = v_cross(a, (1.0, 0.0, 0.0))
        if v_len(ortho) < 1e-6:
            ortho = v_cross(a, (0.0, 1.0, 0.0))
        return q_axis_angle(ortho, math.pi)
    c = v_cross(a, b)
    return q_normalize((1.0 + d, c[0], c[1], c[2]))


def q_angle_deg(a, b):
    """Angle in degrees of the rotation between two orientations (0 .. 180)."""
    d = abs(sum(x * y for x, y in zip(q_normalize(a), q_normalize(b))))
    return math.degrees(2 * math.acos(min(1.0, d)))


# ---- retarget maths ------------------------------------------------------------------------------------------

def compensate(src_q, src_ref_q, tgt_ref_q):
    """Rest-pose compensated retarget of one bone, all orientations in armature (world) space:

        target(frame) = source(frame) * inverse(source_reference) * target_reference

    source(frame) * inverse(source_reference) is how far the source bone has turned away from its reference pose;
    the same world turn is applied to the target bone in its own reference pose. When both skeletons stand in the
    same reference pose (a T-pose), the limbs point the same way in every frame whatever each skeleton's rest pose
    and bone roll were."""
    return q_normalize(q_mul(q_mul(src_q, q_conj(src_ref_q)), tgt_ref_q))


# Library bone names (Quaternius UAL1 Rigify "DEF-" names, UAL2 Unreal names) -> Mixamo names without the prefix.
_SIDE = {"L": "Left", "R": "Right", "l": "Left", "r": "Right"}
_FINGERS = {"index": "Index", "middle": "Middle", "ring": "Ring", "pinky": "Pinky"}
_TORSO_MAP = {
    "DEF-hips": "Hips", "DEF-spine.001": "Spine", "DEF-spine.002": "Spine1", "DEF-spine.003": "Spine2",
    "DEF-neck": "Neck", "DEF-head": "Head",
    "pelvis": "Hips", "spine_01": "Spine", "spine_02": "Spine1", "spine_03": "Spine2", "neck_01": "Neck", "Head": "Head",
}
_LIMB_RIGIFY = {"shoulder": "Shoulder", "upper_arm": "Arm", "forearm": "ForeArm", "hand": "Hand",
                "thigh": "UpLeg", "shin": "Leg", "foot": "Foot", "toe": "ToeBase"}
_LIMB_UE = {"clavicle": "Shoulder", "upperarm": "Arm", "lowerarm": "ForeArm", "hand": "Hand",
            "thigh": "UpLeg", "calf": "Leg", "foot": "Foot", "ball": "ToeBase"}


def mixamo_short(name: str) -> str:
    """'mixamorig:LeftArm' -> 'LeftArm' (any prefix up to a colon, as Mixamo exports vary the prefix)."""
    return name.split(":")[-1]


def library_to_mixamo(name: str) -> str | None:
    """Mixamo short name for one library bone, or None when the bone has no Mixamo counterpart (root, leaf ends).
    A library that already uses Mixamo names maps to itself."""
    if name in _TORSO_MAP:
        return _TORSO_MAP[name]
    short = mixamo_short(name)
    if short in MIXAMO_BONES:
        return short
    n = name[4:] if name.startswith("DEF-") else name
    # Rigify: upper_arm.L, f_index.01.L, thumb.01.L
    if "." in n and n.rsplit(".", 1)[1] in ("L", "R"):
        base, side = n.rsplit(".", 1)
        side = _SIDE[side]
        if base in _LIMB_RIGIFY:
            return side + _LIMB_RIGIFY[base]
        parts = base.split(".")
        if len(parts) == 2 and parts[1].isdigit():
            finger = parts[0][2:] if parts[0].startswith("f_") else parts[0]
            k = int(parts[1])
            if finger == "thumb" and 1 <= k <= 3:
                return f"{side}HandThumb{k}"
            if finger in _FINGERS and 1 <= k <= 3:
                return f"{side}Hand{_FINGERS[finger]}{k}"
        return None
    # Unreal: upperarm_l, index_01_l, ball_l (leaf bones such as index_04_leaf_l have no counterpart)
    if "_" in n and n.rsplit("_", 1)[1] in ("l", "r") and "leaf" not in n:
        base, side = n.rsplit("_", 1)
        side = _SIDE[side]
        if base in _LIMB_UE:
            return side + _LIMB_UE[base]
        parts = base.split("_")
        if len(parts) == 2 and parts[1].isdigit():
            k = int(parts[1])
            if parts[0] == "thumb" and 1 <= k <= 3:
                return f"{side}HandThumb{k}"
            if parts[0] in _FINGERS and 1 <= k <= 3:
                return f"{side}Hand{_FINGERS[parts[0]]}{k}"
    return None


MIXAMO_BONES = frozenset(
    ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head"]
    + [s + b for s in ("Left", "Right") for b in ("Shoulder", "Arm", "ForeArm", "Hand", "UpLeg", "Leg", "Foot", "ToeBase")]
    + [f"{s}Hand{f}{k}" for s in ("Left", "Right") for f in ("Thumb", "Index", "Middle", "Ring", "Pinky") for k in (1, 2, 3)])
# The bones a retarget cannot do without; fingers and toes are optional.
CORE_BONES = ("Hips", "Spine", "Head", "LeftArm", "LeftForeArm", "RightArm", "RightForeArm",
              "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot")


def bone_map(source_names, target_names) -> tuple[dict[str, str], list[str]]:
    """({target_name: source_name}, missing). missing lists the target bones (short names) the library has no
    bone for, such as fingers it lacks: they are left out and follow their parent."""
    by_short = {}
    for s in source_names:
        m = library_to_mixamo(s)
        if m and m not in by_short:
            by_short[m] = s
    out, missing = {}, []
    for t in target_names:
        short = mixamo_short(t)
        if short in by_short:
            out[t] = by_short[short]
        elif short in MIXAMO_BONES:
            missing.append(short)
    return out, missing


# Bones the T-pose reference aims, with the direction each one points in that pose (+X is the character's left
# when it faces -Y, the glTF front). Other bones keep their rest direction relative to their parent.
TPOSE_DIRS = {
    "LeftArm": (1.0, 0.0, 0.0), "LeftForeArm": (1.0, 0.0, 0.0), "LeftHand": (1.0, 0.0, 0.0),
    "RightArm": (-1.0, 0.0, 0.0), "RightForeArm": (-1.0, 0.0, 0.0), "RightHand": (-1.0, 0.0, 0.0),
    "LeftUpLeg": (0.0, 0.0, -1.0), "LeftLeg": (0.0, 0.0, -1.0), "RightUpLeg": (0.0, 0.0, -1.0), "RightLeg": (0.0, 0.0, -1.0),
}
# Feet keep their rest pitch (standing flat) but point straight ahead.
TPOSE_FLAT = ("LeftFoot", "RightFoot")


def tpose_turns(rest_dirs: dict, parents: dict) -> dict:
    """World turn per bone that moves a skeleton from its rest pose into the T-pose reference (as the Unity
    Humanoid importer does: arms level, legs straight down).

    rest_dirs: {mixamo_short: rest direction of the bone segment (joint to child joint), world space, facing -Y}.
    parents:   {mixamo_short: parent's mixamo_short or None}.
    Aimed bones (TPOSE_DIRS) get the smallest turn onto their T-pose direction; feet turn to point straight ahead
    keeping their rest pitch; every other bone inherits its parent's turn (fingers follow the hand, toes the foot).
    A bone's reference orientation is turn * rest orientation."""
    turns: dict = {}

    def turn(n):
        if n in turns:
            return turns[n]
        if n in TPOSE_DIRS and n in rest_dirs:
            q = q_from_to(rest_dirs[n], TPOSE_DIRS[n])
        elif n in TPOSE_FLAT and n in rest_dirs:
            d = rest_dirs[n]
            q = q_from_to(d, (0.0, d[1] if abs(d[1]) > 1e-6 else -1e-6, d[2]))
        else:
            p = parents.get(n)
            q = turn(p) if p else (1.0, 0.0, 0.0, 0.0)
        turns[n] = q
        return q

    for n in list(rest_dirs) + list(parents):
        turn(n)
    return turns


def contact_weight(clearance: float, near: float, far: float) -> float:
    """1 when a foot is on the ground (clearance <= near), 0 when clear of it (>= far), smooth between."""
    if far <= near:
        return 1.0 if clearance <= near else 0.0
    return 1.0 - smoothstep((clearance - near) / (far - near))


def contact_segments(flags: list, cyclic: bool) -> list:
    """Runs of consecutive True frames. With cyclic, the last frame repeats the first (a loop), and a run that
    wraps from the end to the start is one run."""
    n = len(flags)
    if n == 0:
        return []
    m = n - 1 if cyclic and n > 1 else n
    segs, cur = [], []
    for i in range(m):
        if flags[i]:
            cur.append(i)
        elif cur:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    if cyclic and len(segs) > 1 and segs[0][0] == 0 and segs[-1][-1] == m - 1:
        segs[0] = segs[-1] + segs[0]
        segs.pop()
    if cyclic and m < n and flags[0]:
        for s in segs:
            if 0 in s:
                s.append(n - 1)
    return segs


# ---- two-bone IK and limb settling ---------------------------------------------------------------------------

def two_bone_ik(root, target, l1, l2, pole, max_bend_deg=160.0, min_bend_deg=0.0):
    """Analytic two-bone IK. -> (mid, end, reached).

    root: the upper joint (shoulder or hip); target: where the end (wrist or ankle) should go; l1, l2: segment
    lengths; pole: a point the middle joint (elbow or knee) should bend toward. The bend at the middle joint is
    clamped to [min_bend_deg, max_bend_deg] (0 = straight), so an unreachable target gives the straightest allowed
    limb pointing at it and a target too close gives the tightest allowed bend."""
    d = v_sub(target, root)
    dist = v_len(d)
    axis = v_norm(d, (0.0, 0.0, -1.0))

    def reach(bend):
        inner = math.radians(180.0 - bend)  # angle inside the middle joint
        return math.sqrt(max(0.0, l1 * l1 + l2 * l2 - 2 * l1 * l2 * math.cos(inner)))

    far_r, near_r = reach(min_bend_deg), reach(max_bend_deg)
    dist_c = min(far_r, max(near_r, dist))
    cos_a = (l1 * l1 + dist_c * dist_c - l2 * l2) / max(1e-12, 2 * l1 * dist_c)
    a = math.acos(max(-1.0, min(1.0, cos_a)))  # angle at the root between the root-to-end line and the upper bone
    pv = v_sub(pole, root)
    side = v_sub(pv, v_scale(axis, v_dot(pv, axis)))
    if v_len(side) < 1e-9:
        side = v_cross(axis, (1.0, 0.0, 0.0))
        if v_len(side) < 1e-9:
            side = v_cross(axis, (0.0, 1.0, 0.0))
    side = v_norm(side)
    mid = v_add(root, v_add(v_scale(axis, l1 * math.cos(a)), v_scale(side, l1 * math.sin(a))))
    end = v_add(root, v_scale(axis, dist_c))
    return mid, end, abs(dist_c - dist) < 1e-6


def bend_deg(root, mid, end):
    """Bend at the middle joint in degrees: 0 straight, 180 folded back."""
    a, b = v_sub(mid, root), v_sub(end, mid)
    return math.degrees(math.acos(max(-1.0, min(1.0, v_dot(v_norm(a), v_norm(b))))))


def ground_target(root, end, ground_z, reach, outward=None, min_out=0.0):
    """Where a limb's end should rest for a settled pose (death): below its current place at ground_z, pushed at
    least `min_out` along `outward` (a horizontal direction away from the body, so an arm lies beside the body
    rather than across it), then pulled toward the root until the root can reach it."""
    x, y = end[0], end[1]
    if outward is not None:
        o = v_norm((outward[0], outward[1], 0.0), (1.0, 0.0, 0.0))
        cur = (x - root[0]) * o[0] + (y - root[1]) * o[1]
        if cur < min_out:
            x += o[0] * (min_out - cur)
            y += o[1] * (min_out - cur)
    dz = ground_z - root[2]
    horiz_max = math.sqrt(max(0.0, reach * reach - dz * dz))
    hx, hy = x - root[0], y - root[1]
    h = math.hypot(hx, hy)
    if h > horiz_max and h > 1e-9:
        x, y = root[0] + hx * horiz_max / h, root[1] + hy * horiz_max / h
    return (x, y, ground_z)


# ---- foot paths for procedural walking and running -----------------------------------------------------------

def foot_path(u: float, duty: float, stride: float, lift: float, run: bool = False) -> tuple:
    """(forward, up, pitch_deg) of one foot relative to its rest place at cycle fraction u.

    While planted (the first `duty` of the cycle) the foot moves backward at constant speed from +stride/2 to
    -stride/2 at ground height: on a body moving forward at stride / (duty * cycle seconds) it does not slide. In
    the air it swings forward again; a run lifts the heel high early in the swing (toward the seat), a walk keeps
    it low. pitch_deg: toes up positive (heel strike), toes down negative (push off). Periodic and continuous."""
    u = wrap(u)
    duty = min(0.95, max(0.05, duty))
    push = 22.0 if run else 18.0
    if u < duty:
        s = u / duty
        pitch = 12.0 * (1.0 - smoothstep(s / 0.2)) - push * smoothstep((s - 0.7) / 0.3)
        return stride * (0.5 - s), 0.0, pitch
    s = (u - duty) / (1.0 - duty)
    fwd = -stride * 0.5 + stride * smoothstep(s)
    if run:
        up = lift * math.sin(math.pi * s) ** 1.5 * (1.0 - 0.35 * s)
        fwd -= stride * 0.3 * math.sin(math.pi * s) * (1.0 - s) ** 2  # the heel rises behind before swinging through
    else:
        up = lift * math.sin(math.pi * s) ** 1.2
    pitch = -push * (1.0 - smoothstep(s / 0.4)) + 12.0 * smoothstep((s - 0.6) / 0.4)
    return fwd, up, pitch


# ---- library clip choice -------------------------------------------------------------------------------------

def parse_clip_spec(spec) -> list:
    """'idle=Idle_Loop,run=Jog_Fwd_Loop|Run_Loop' -> [('idle', ['Idle_Loop']), ('run', ['Jog_Fwd_Loop', 'Run_Loop'])].
    Also takes a list of {name, from} dicts (the category yaml). A bare name maps to itself."""
    out = []
    if not spec:
        return out
    if isinstance(spec, str):
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            name, _, rest = part.partition("=")
            cands = [c.strip() for c in (rest or name).split("|") if c.strip()]
            out.append((name.strip(), cands))
        return out
    for row in spec:
        if isinstance(row, dict):
            src = row.get("from") or [row["name"]]
            out.append((str(row["name"]), [str(c) for c in (src if isinstance(src, (list, tuple)) else [src])]))
    return out


def clip_spec_text(rows) -> str:
    """The inverse of parse_clip_spec, for the command line."""
    return ",".join(f"{n}={'|'.join(c)}" for n, c in rows)
