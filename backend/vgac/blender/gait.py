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
# D9: a one-frame still pose, the idle arm angle with the Arm pose offset (procanim --neutral-pose; humanoids only).
NEUTRAL_CLIP = "neutral_pose"

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


DEATH_FALLBACK_MARGIN_M = 0.01  # WP-78: the new death sinks at most this much deeper than Today's death, else Today's is used


def death_fallback_used(new_low: float, old_low: float, margin: float = DEATH_FALLBACK_MARGIN_M) -> bool:
    """WP-78 death fallback rule: True when the new death's lowest skinned point over the fall (metres from the rest ground,
    negative is under it) sinks more than `margin` deeper than Today's death's. Then Today's death is used for the rig,
    the same way the idle falls back to the old idle."""
    return new_low < old_low - margin


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
    """(motion_frames, total_frames). Keys run 0..total; the death clip adds DEATH_HOLD seconds of held pose. The
    neutral pose is one key (0, 0)."""
    if name == NEUTRAL_CLIP:
        return 0, 0
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


# ---- the built-in idle's stance (procanim.clip_idle) ----------------------------------------------------------
# A character is built in whatever rest stance its picture had, so some stand with their feet far apart. The idle
# turns the upper legs in until the ankles are IDLE_STANCE_RATIO of the shoulder width apart (shoulder joint to
# shoulder joint). A stance that is already that narrow is left alone, and the ankles never come closer than the hip
# joints are (STANCE_MIN_HIP: a leg hanging straight down) or than the widest foot allows (STANCE_FOOT_GAP).
IDLE_STANCE_RATIO = 0.85
STANCE_TOLERANCE = 1.05   # a stance up to this much wider than the target counts as already right
STANCE_MIN_HIP = 1.1
STANCE_KNEE_CLEAR = 0.04     # clear space between the legs' skin at the knee, as a share of the character's height
STANCE_THIGH_CLEAR = 0.015   # and at mid-thigh; a character whose rest pose is tighter than that keeps its own rest gap
STANCE_GAP_SLACK = 0.005    # tolerance on those gaps, as a share of height
STANCE_KNEE_OUT = 0.2        # sideways part of the direction a planted knee bends toward (forward is 1), outward positive
STANCE_FOOT_GAP = 1.6     # ankles stay at least this many foot widths apart, so big cartoon feet do not touch


def stance_want(shoulder: float, hip_sep: float, foot_width: float = 0.0, ratio: float = IDLE_STANCE_RATIO) -> float:
    """The narrowest ankle spacing the shoulder, hip and foot rules allow."""
    return max(ratio * shoulder, STANCE_MIN_HIP * hip_sep, STANCE_FOOT_GAP * foot_width)


def stance_pair(ankle_l: tuple[float, float], sep: float) -> tuple[float, float]:
    """Ankle lateral coordinates for a spacing `sep`, about the same midline, each foot on its own side."""
    mid = (ankle_l[0] + ankle_l[1]) / 2
    sgn = 1.0 if ankle_l[0] >= ankle_l[1] else -1.0
    return (mid + sgn * sep / 2, mid - sgn * sep / 2)


def narrowest_clear(lo: float, hi: float, ok) -> float:
    """The smallest spacing in [lo, hi] for which ok(spacing) holds (ok is true for wide spacings and false for narrow
    ones); hi when even hi fails, lo when lo already does."""
    if ok(lo):
        return lo
    if not ok(hi):
        return hi
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if ok(mid) else (mid, hi)
    return hi


def stance_targets(ankle_l: tuple[float, float], hip_l: tuple[float, float], shoulder: float,
                   ratio: float = IDLE_STANCE_RATIO, foot_width: float = 0.0,
                   min_sep: float = 0.0) -> tuple[float, float] | None:
    """New lateral coordinates for the two ankles, or None when the stance stays as it is. Lateral coordinates are
    along the body's side axis; the ankle midline is kept, so a stance that sits off centre is narrowed about it."""
    sep = abs(ankle_l[0] - ankle_l[1])
    want = stance_want(shoulder, abs(hip_l[0] - hip_l[1]), foot_width, ratio)
    want = max(want, min(min_sep, sep))
    if shoulder <= 1e-6 or sep <= want * STANCE_TOLERANCE:
        return None
    return stance_pair(ankle_l, want)


def leg_roll(dl: float, dz: float, new_dl: float) -> tuple[float, float]:
    """Turn a leg about the hip in the side-to-up plane. (dl, dz) is the ankle's lateral and vertical offset from the
    hip; the result is (angle in radians, ankle height change) for the smallest turn that puts the ankle new_dl to the
    side. The angle is the rotation about the forward axis (side axis toward up is positive). A negative height
    change means the ankle goes down, so the body has to rise by that much to keep the foot on the ground."""
    r = math.hypot(dl, dz)
    if r < 1e-9:
        return 0.0, 0.0
    c = max(-1.0, min(1.0, new_dl / r))
    psi = math.atan2(-dz, dl)
    cands = [psi + math.acos(c), psi - math.acos(c)]
    a = min(cands, key=lambda x: abs(math.atan2(math.sin(x), math.cos(x))))
    a = math.atan2(math.sin(a), math.cos(a))
    return a, dl * math.sin(a) + dz * math.cos(a) - dz


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


def cap_turn(q, max_rad):
    """The turn q (w, x, y, z) with its angle limited to max_rad, by the short way round (q and -q are the same turn, so a
    negative w is flipped first: otherwise a small turn reads as nearly a full circle). Same axis, so the direction is
    kept. A turn already within the limit is returned unchanged (as a normalised quaternion)."""
    w, x, y, z = q_normalize(tuple(q))
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    angle = 2.0 * math.acos(min(1.0, w))
    if angle <= max_rad or angle < 1e-9:
        return (w, x, y, z)
    s = math.sin(angle / 2.0)
    ax = (x / s, y / s, z / s)
    h = math.sin(max_rad / 2.0)
    return (math.cos(max_rad / 2.0), ax[0] * h, ax[1] * h, ax[2] * h)


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


# The 22 main bones a person's skeleton is counted against ("We recognised 21 of the 22 main bones"): the body
# without fingers. A file is usable when every CORE_BONES one is found.
BODY_BONES = (("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head")
              + tuple(s + b for s in ("Left", "Right") for b in ("Shoulder", "Arm", "ForeArm", "Hand", "UpLeg", "Leg", "Foot", "ToeBase")))
# Plain names for the recognition sentence ("We could not find: the left toes").
BODY_PLAIN = {"Hips": "the hips", "Spine": "the lower back", "Spine1": "the middle of the back", "Spine2": "the chest",
              "Neck": "the neck", "Head": "the head", "Shoulder": "shoulder", "Arm": "upper arm", "ForeArm": "forearm",
              "Hand": "hand", "UpLeg": "thigh", "Leg": "shin", "Foot": "foot", "ToeBase": "toes"}


def body_plain(short: str) -> str:
    for side in ("Left", "Right"):
        if short.startswith(side):
            return f"the {side.lower()} {BODY_PLAIN.get(short[len(side):], short[len(side):])}"
    return BODY_PLAIN.get(short, short)


# ---- names from other tools (a person's own animation file) ------------------------------------------------------
# Unity humanoid names, Rigify ORG/MCH names, 3ds Max Biped, Blender's .L/_l and "Left..." styles and BVH joint
# names (LeftHip, LeftKnee, LeftAnkle, LeftShoulder as the upper arm when the file also has elbows). The direct
# rules in library_to_mixamo run first; these only fill what is left.
_NOISE = {"mixamorig", "bip", "bip01", "bip001", "def", "org", "mch", "drv", "cc", "base", "j", "jnt", "joint", "bone",
          "b", "sk", "rig", "armature", "c", "ctrl", "skel", "bn", "jt", "g", "the"}
_SKIP = {"twist", "roll", "helper", "nub", "end", "leaf", "ik", "pole", "target", "tip", "top", "ctrl", "socket", "prop",
         "weapon", "attach", "dummy", "null", "share", "corrective", "jiggle", "breast", "eye", "eyes", "jaw", "tongue",
         "teeth", "hair", "cape", "skirt", "tail", "wing", "ear", "brow", "lid", "lip", "cheek", "nose", "metacarpal", "carpal"}
_LEFT = {"left", "l", "lf", "lft"}
_RIGHT = {"right", "r", "rt", "rgt"}
_FINGER_WORDS = {"thumb": "Thumb", "index": "Index", "middle": "Middle", "mid": "Middle", "ring": "Ring", "pinky": "Pinky",
                 "little": "Pinky", "pinkie": "Pinky"}


def name_tokens(name: str) -> list[str]:
    """'mixamorig:LeftUpLeg' -> ['left', 'up', 'leg']; 'Bip01 L Thigh' -> ['bip01', 'l', 'thigh'];
    'upper_arm.L.001' -> ['upper', 'arm', 'l', '001']. Splits on separators, case changes and letter-digit
    boundaries (keeping 'spine1' as 'spine', '1')."""
    import re

    n = name.split(":")[-1].split("|")[-1]
    parts = re.split(r"[^A-Za-z0-9]+", n)
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", p):
            out.append(w.lower())
    return out


def _side_of(tokens: list[str]) -> tuple[str | None, list[str]]:
    side, rest = None, []
    for t in tokens:
        if side is None and t in _LEFT:
            side = "Left"
        elif side is None and t in _RIGHT:
            side = "Right"
        else:
            rest.append(t)
    return side, rest


def joint_style(names) -> bool:
    """True when a skeleton names its joints rather than its bones (LeftHip, LeftKnee, LeftShoulder, LeftElbow): then
    'shoulder' is the upper arm and 'hip' the thigh."""
    toks = {t for n in names for t in name_tokens(n)}
    return ("elbow" in toks or "knee" in toks) and not ({"forearm", "fore", "lower", "calf", "shin"} & toks)


def generic_to_mixamo(name: str, joints: bool = False) -> str | None:
    """Mixamo short name for a bone named the way another tool names it, or None. Spine bones are 'Spine' here and
    are numbered by source_shorts."""
    toks = name_tokens(name)
    if not toks or any(t in _SKIP for t in toks):
        return None
    side, rest = _side_of(toks)
    words = [t for t in rest if t not in _NOISE]
    digits = [int(t) for t in words if t.isdigit()]
    words = [t for t in words if not t.isdigit()]
    ws = set(words)
    joined = "".join(words)
    if side is None:
        if joined in ("hips", "hip", "pelvis"):
            return "Hips"
        if joined in ("spine", "chest", "upperchest", "abdomen", "torso", "waist", "lowerback", "back", "spinelower", "spineupper",
                      "spinemid", "spinemiddle", "abdomenupper", "abdomenlower", "chestupper", "chestlower"):
            return "Spine"
        if joined in ("neck",):
            return "Neck" if not digits or digits[0] <= 1 else None
        if joined in ("head",):
            return "Head"
        return None
    finger = next((f for w, f in _FINGER_WORDS.items() if w in ws), None)
    if finger is not None:
        k = digits[0] if digits else 1
        if 0 in digits:
            k += 1
        return f"{side}Hand{finger}{k}" if 1 <= k <= 3 else None
    # A numbered piece after the side ('upper_arm.L.001', a Rigify twist segment) is not the limb bone itself.
    after = toks[next(i for i, t in enumerate(toks) if t in _LEFT or t in _RIGHT) + 1:]
    if any(t.isdigit() and int(t) >= 1 for t in after):
        return None
    if joined in ("clavicle", "collar", "collarbone") or (joined == "shoulder" and not joints):
        return side + "Shoulder"
    if joined in ("upperarm", "uparm", "arm", "armupper") or (joined == "shoulder" and joints):
        return side + "Arm"
    if joined in ("forearm", "lowerarm", "elbow", "armlower", "lowarm"):
        return side + "ForeArm"
    if joined in ("hand", "wrist"):
        return side + "Hand"
    if joined in ("thigh", "upleg", "upperleg", "legupper", "femur") or (joined == "hip" and joints):
        return side + "UpLeg"
    if joined in ("calf", "shin", "leg", "lowerleg", "knee", "leglower", "lowleg"):
        return side + "Leg"
    if joined in ("foot", "ankle"):
        return side + "Foot"
    if joined in ("toe", "toes", "toebase", "ball", "toesbase"):
        return side + "ToeBase"
    return None


def source_shorts(names) -> dict[str, str]:
    """{source bone name: Mixamo short name} for every bone of a skeleton that can be recognised, each short name
    once. Direct rules first (Mixamo, Quaternius Rigify DEF, Unreal mannequin), then other tools' names. Spine bones
    found by the generic rule are numbered in file order (parents come first): Spine, Spine1, Spine2, and a fourth
    or later one is left out."""
    names = list(names)
    out: dict[str, str] = {}
    taken: set[str] = set()
    joints = joint_style(names)
    for n in names:
        m = library_to_mixamo(n)
        if joints and m and m.endswith("Shoulder"):
            continue  # a joint-named file: its "LeftShoulder" is the upper arm (the generic rule decides)
        if m and m not in taken:
            out[n] = m
            taken.add(m)
    spine_free = [s for s in ("Spine", "Spine1", "Spine2") if s not in taken]
    for n in names:
        if n in out:
            continue
        m = generic_to_mixamo(n, joints)
        if m == "Spine":
            if not spine_free:
                continue
            m = spine_free.pop(0)
        if m and m not in taken:
            out[n] = m
            taken.add(m)
    return out


def recognise(names) -> dict:
    """How well a skeleton is recognised: {recognised, total, found (short names), missing (short names of the main
    bones not found), usable (every core bone found), sentence}. The sentence is what the person reads."""
    shorts = set(source_shorts(names).values())
    found = [b for b in BODY_BONES if b in shorts]
    missing = [b for b in BODY_BONES if b not in shorts]
    usable = all(b in shorts for b in CORE_BONES)
    total = len(BODY_BONES)
    if usable:
        sentence = f"We recognised {len(found)} of the {total} main bones."
    else:
        sentence = "This file's skeleton is not human-shaped, so we cannot use it."
    return {"recognised": len(found), "total": total, "found": found, "missing": missing, "usable": usable,
            "missing_core": [b for b in CORE_BONES if b not in shorts], "sentence": sentence}


def bone_map(source_names, target_names) -> tuple[dict[str, str], list[str]]:
    """({target_name: source_name}, missing). missing lists the target bones (short names) the library has no
    bone for, such as fingers it lacks: they are left out and follow their parent."""
    by_short = {}
    for s, m in source_shorts(source_names).items():
        if m not in by_short:
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

STRIKE_DEG = 12.0  # toes-up pitch at heel strike (default roll)
QUICK_STRIKE_DEG = 6.0  # toes-up pitch at heel strike of the quick roll
QUICK_STRIKE_SHARE = 0.15  # share of the stance the quick roll takes to bring the foot flat (default roll: 0.2)


def foot_path(u: float, duty: float, stride: float, lift: float, run: bool = False, quick_strike: bool = False) -> tuple:
    """(forward, up, pitch_deg) of one foot relative to its rest place at cycle fraction u.

    While planted (the first `duty` of the cycle) the foot moves backward at constant speed from +stride/2 to
    -stride/2 at ground height: on a body moving forward at stride / (duty * cycle seconds) it does not slide. In
    the air it swings forward again, leaving and landing at the stance's speed (no world speed at either end); a
    run lifts the heel high early in the swing (toward the seat), a walk keeps it low. pitch_deg: toes up positive (heel strike), toes down negative (push off). Periodic and continuous.
    quick_strike: the heel lands with the toes QUICK_STRIKE_DEG up and the foot rolls flat over QUICK_STRIKE_SHARE of
    the stance on an ease-out curve, so the toes drop mostly in the first frame after the heel lands. With the default
    (STRIKE_DEG, a smoothstep over 0.2 of the stance) the toes of a long foot stay several cm up and nearly still for
    two to three frames after heel strike."""
    u = wrap(u)
    duty = min(0.95, max(0.05, duty))
    push = 22.0 if run else 18.0
    strike = QUICK_STRIKE_DEG if quick_strike else STRIKE_DEG
    if u < duty:
        s = u / duty
        if quick_strike:
            roll = (1.0 - min(1.0, s / QUICK_STRIKE_SHARE)) ** 2
        else:
            roll = 1.0 - smoothstep(s / 0.2)
        pitch = strike * roll - push * smoothstep((s - 0.7) / 0.3)
        return stride * (0.5 - s), 0.0, pitch
    s = (u - duty) / (1.0 - duty)
    # The swing is a Hermite curve whose end slopes match the stance: the foot leaves the ground and lands on it with no
    # speed in the world (it draws back a little just before landing), so neither toe-off nor heel strike slides.
    m = -stride * (1.0 - duty) / duty
    s2, s3 = s * s, s * s * s
    fwd = -stride * 0.5 * (2 * s3 - 3 * s2 + 1) + m * (s3 - 2 * s2 + s) + stride * 0.5 * (3 * s2 - 2 * s3) + m * (s3 - s2)
    if run:
        up = lift * math.sin(math.pi * s) ** 1.5 * (1.0 - 0.35 * s)
        fwd -= stride * 0.3 * math.sin(math.pi * s) ** 2 * (1.0 - s) ** 2  # the heel rises behind before swinging through
    else:
        up = lift * math.sin(math.pi * s) ** 1.2
    pitch = -push * (1.0 - smoothstep(s / 0.4)) + strike * smoothstep((s - 0.6) / 0.4)
    return fwd, up, pitch


FOOT_NAMES = ("LeftFoot", "RightFoot")
HEEL_BACK_SHARE = 0.35  # the heel sits this share of the ankle-to-ball length behind the ankle (heel_raise uses the same)


def foot_bone_index(names) -> int | None:
    """Index in a leg's bone list (hip first) of its named foot bone (mixamorig:LeftFoot, Foot.L, foot_r), or None."""
    for k, n in enumerate(names):
        if mixamo_short(n) in FOOT_NAMES or generic_to_mixamo(n) in FOOT_NAMES:
            return k
    return None


def ik_pair_start(seg_lengths: list[float], foot_index: int | None = None) -> int:
    """Start of the two-bone IK pair of a limb: the bone index i whose bones i and i + 1 the IK turns. seg_lengths[k] is
    joint k to joint k + 1 (the last joint is the leaf end); the pair may not end on the leaf end. A leg with a named foot
    ends its pair at the ankle (thigh and shin), even when a big cartoon foot makes shin plus foot the longer pair; any
    other limb takes the longest pair (a dog's upper arm and forearm)."""
    last = max(0, len(seg_lengths) - 3)
    if foot_index is not None and 2 <= foot_index <= last + 2:
        return foot_index - 2
    best_i, best = 0, -1.0
    for i in range(0, last + 1):
        tot = seg_lengths[i] + seg_lengths[i + 1]
        if tot > best + 1e-9:
            best_i, best = i, tot
    return best_i


def rolled_ankle(ankle, ball, floor_z, forward, lateral, toe_len, pitch_deg, heel_pts=None):
    """Where the ankle goes when a planted foot pitches by pitch_deg (toes up positive) without sliding: about the ball
    joint for toes down (push off); for toes up (heel strike) it rolls on the heel. With heel_pts (rest points of the
    foot's skin), the point lowest once the foot is turned is set on the floor where it stands at rest, so a rounded heel
    touches the floor at every pitch; without them the heel is taken HEEL_BACK_SHARE of toe_len behind the ankle on the
    floor. The foot turns by the same rotation procanim gives it: pitch_deg about -lateral. Positions are the rest ones
    (world); the caller adds the foot path's forward and up offsets."""
    q = q_axis_angle(lateral, math.radians(-pitch_deg))
    if pitch_deg < 0 and ball is not None:
        piv = tuple(ball)
        return v_add(piv, q_rotate(q, v_sub(ankle, piv)))
    if heel_pts:
        c = min(heel_pts, key=lambda p: q_rotate(q, v_sub(p, ankle))[2])
        return v_sub((c[0], c[1], floor_z), q_rotate(q, v_sub(c, ankle)))
    piv = (ankle[0] - forward[0] * HEEL_BACK_SHARE * toe_len, ankle[1] - forward[1] * HEEL_BACK_SHARE * toe_len, floor_z)
    return v_add(piv, q_rotate(q, v_sub(ankle, piv)))


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


# ---- arm pose: each upper arm's angle from straight down (procanim; the rig setting arm_pose) ---------------------
# The built-in idle, walk, run and turn drop each arm from its own measured rest angle to a target angle from straight
# down, so a character whose arms rest raised keeps them down. The rest mesh and its weights are never changed.
ARM_TARGET_DEG = {"idle": 24.0, "walk": 23.0, "run": 35.0, "turn": 22.0}
ARM_POSE_CHOICES = ("close", "relaxed", "open")
ARM_POSE_DEFAULT = "relaxed"
ARM_POSE_SHIFT_DEG = {"close": -8.0, "relaxed": 0.0, "open": 10.0}
ARM_MIN_DEG = 10.0         # an arm never comes closer than this to straight down, whatever the setting
WALK_ARM_SWING_DEG = 20.0  # the walk's forward and back swing of the upper arm about the arm's swing axis
RUN_ARM_SWING_BIAS_DEG = 8.0   # the run's upper arm swings this far forward of the hold on average ...
RUN_ARM_SWING_DEG = 38.0       # ... and this far either way over the cycle (procanim's run arms use both)


def arm_pose_choice(value) -> str:
    """The arm_pose setting as one of ARM_POSE_CHOICES: None or empty is the default; anything else raises."""
    if value is None or value == "":
        return ARM_POSE_DEFAULT
    if value not in ARM_POSE_CHOICES:
        raise ValueError(f"arm_pose must be one of {list(ARM_POSE_CHOICES)}")
    return value


# ---- resting pose: the hand shape and one finger-curl amount (WP-76; procanim --hand-pose, --finger-curl) -------------
HAND_POSE_CHOICES = ("open", "relaxed", "fist")
HAND_POSE_DEFAULT = "relaxed"
FINGER_CURL_DEFAULT = 1.0
FINGER_CURL_MAX = 1.5  # the amount multiplies the hand shape; 0 is no curl, 1 is the shape as designed


def hand_pose_choice(value) -> str:
    """The hand_pose setting as one of HAND_POSE_CHOICES: None or empty is the default; anything else raises."""
    if value is None or value == "":
        return HAND_POSE_DEFAULT
    if value not in HAND_POSE_CHOICES:
        raise ValueError(f"hand_pose must be one of {list(HAND_POSE_CHOICES)}")
    return value


def finger_curl_amount(value) -> float:
    """The finger_curl setting as a number from 0 to FINGER_CURL_MAX: None or empty is the default; anything else raises."""
    if value is None or value == "":
        return FINGER_CURL_DEFAULT
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError("finger_curl must be a number from 0 to 1.5") from None
    if not (0.0 <= x <= FINGER_CURL_MAX):
        raise ValueError(f"finger_curl must be from 0 to {FINGER_CURL_MAX}")
    return x


def angle_from_down(v, up=(0.0, 0.0, 1.0)) -> float:
    """Degrees between direction v and straight down (the opposite of up). 0 hangs down, 180 points straight up."""
    n = v_len(v)
    if n < 1e-12:
        return 0.0
    c = -v_dot(v, up) / (n * v_len(up))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def rest_arm_angle(shoulder, elbow, up=(0.0, 0.0, 1.0)) -> float:
    """The measured rest angle of an upper arm: from its shoulder joint to the elbow, in degrees from straight down."""
    return angle_from_down(v_sub(elbow, shoulder), up)


def arm_target_deg(clip: str, choice: str | None = None) -> float:
    """The angle from straight down an upper arm should hold in this clip, before the body's own limits."""
    if clip not in ARM_TARGET_DEG:
        raise ValueError(f"no arm target for clip {clip!r}")
    return ARM_TARGET_DEG[clip] + ARM_POSE_SHIFT_DEG[arm_pose_choice(choice)]


def hip_clear_min_deg(shoulder_out: float, arm_len: float, hip_half: float) -> float:
    """The smallest angle from straight down that keeps the hand outside the hip line. With the arm taken straight, the
    hand sits about shoulder_out + arm_len * sin(angle) from the centre line and must reach hip_half. 0 when the
    shoulder is already outside the hips or the arm has no length."""
    if arm_len <= 1e-9 or shoulder_out >= hip_half:
        return 0.0
    return math.degrees(math.asin(min(1.0, (hip_half - shoulder_out) / arm_len)))


def run_arm_mean_deg(hold: float, samples: int = 256) -> float:
    """Mean angle from straight down of the upper arm over one run cycle held at hold: the run swings it
    RUN_ARM_SWING_BIAS_DEG + RUN_ARM_SWING_DEG * cos(phase) about the swing axis (swung_arm_angle)."""
    total = 0.0
    for i in range(samples):
        total += swung_arm_angle(hold, RUN_ARM_SWING_BIAS_DEG + RUN_ARM_SWING_DEG * math.cos(2 * math.pi * i / samples))
    return total / samples


def run_hold_for_mean(target: float) -> float:
    """The hold whose run cycle averages target degrees from straight down. The swing adds about 7 degrees on
    average, so the hold sits below the target. Bisection: run_arm_mean_deg rises with the hold."""
    lo, hi = 0.0, 90.0
    if run_arm_mean_deg(lo) >= target:
        return lo
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if run_arm_mean_deg(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def arm_hold_deg(clip: str, choice: str | None = None, shoulder_out: float | None = None,
                 arm_len: float | None = None, hip_half: float | None = None, extra: float = 0.0) -> float:
    """The angle from straight down an upper arm is held at in a clip: the clip's target (with the arm_pose setting and
    an optional extra, such as the idle breath) clamped to at least ARM_MIN_DEG and, when the skeleton's widths are
    given, to the angle that keeps the hand outside the hips. The run target is the mean over its cycle, so its hold
    is solved from the swing (run_hold_for_mean)."""
    base = arm_target_deg(clip, choice)
    if clip == "run":
        base = run_hold_for_mean(base)
    hold = max(ARM_MIN_DEG, base + extra)
    if shoulder_out is not None and arm_len is not None and hip_half is not None:
        hold = max(hold, hip_clear_min_deg(shoulder_out, arm_len, hip_half))
    return hold


def arm_drop_deg(rest_deg: float, clip: str, choice: str | None = None, shoulder_out: float | None = None,
                 arm_len: float | None = None, hip_half: float | None = None, extra: float = 0.0) -> float:
    """Degrees the upper arm rotates toward straight down so it holds the clip's angle. Positive drops the arm; a
    negative value lifts an arm that rests closer to the body than its target."""
    return rest_deg - arm_hold_deg(clip, choice, shoulder_out, arm_len, hip_half, extra)


def swung_arm_angle(hold_deg: float, swing_deg: float) -> float:
    """Angle from straight down after an upper arm held at hold_deg in the side plane swings swing_deg about the
    lateral axis: cos(angle) = cos(hold) * cos(swing)."""
    c = math.cos(math.radians(hold_deg)) * math.cos(math.radians(swing_deg))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# ---- collarbone share (WP-75): a raised arm lifts the collarbone too ----------------------------------------------
# A real shoulder lifts the collarbone as the arm goes past about 60 degrees from straight down (about 25 degrees by
# 120). Turning only the upper arm caves the shoulder top in. The built-in clips, the retarget (on top of the
# library's own collarbone turn) and the bend check add this lift; the upper arm turns back by the same amount, so the
# arm still points where the clip put it.
CLAVICLE_LIFT_START_DEG = 60.0   # no lift up to this raise (measured in the chest's frame) ...
CLAVICLE_LIFT_PER_DEG = 25.0 / 60.0  # ... then this many degrees of lift per degree of raise ...
CLAVICLE_LIFT_MAX_DEG = 30.0     # ... up to this


def _clavicle_curve(raise_deg: float) -> float:
    return min(CLAVICLE_LIFT_MAX_DEG, max(0.0, raise_deg - CLAVICLE_LIFT_START_DEG) * CLAVICLE_LIFT_PER_DEG)


def clavicle_lift_deg(raise_deg: float, rest_deg: float = 0.0) -> float:
    """Degrees the collarbone lifts (its outer end up) for an upper arm raise_deg from the chest's own down direction.
    rest_deg is the arm's angle in the rest mesh: a model made with its arms already raised (a T-pose) was built with
    that lift in it, so only the lift beyond the rest's counts, and never below none."""
    return max(0.0, _clavicle_curve(raise_deg) - _clavicle_curve(rest_deg))


def walk_arm_mean_deg(clip: str = "walk", choice: str | None = None, samples: int = 256) -> float:
    """Mean angle from straight down of the upper arm over one built-in walk cycle once it is dropped to its hold: the
    walk swings it WALK_ARM_SWING_DEG each way. The hold does not depend on the rest angle, which the drop removes."""
    hold = arm_hold_deg(clip, choice)
    total = 0.0
    for i in range(samples):
        total += swung_arm_angle(hold, WALK_ARM_SWING_DEG * math.cos(2 * math.pi * i / samples))
    return total / samples


# ---- clip data for the game: locomotion speed and attack hit time (WP-19) -------------------------------------------
# Both come from the posed frames a clip is made of, so they follow the motion that is actually exported rather than a
# table. Coordinates are (x, y, z) with z up, metres in the rig's own units, sampled at fps.
PLANT_TOLERANCE_SHARE = 0.02   # a foot counts as planted within this share of the character's height of its lowest point
PLANT_MIN_SHARE = 0.15         # fewer planted frames than this share of the clip means no stance was found: fall back
PLANT_MIN_SPEED = 0.05         # m/s: a planted foot that barely moves is an in-place clip, not a walk: fall back


def _horizontal_speeds(track: list[tuple[float, float, float]], fps: float) -> list[float]:
    """Horizontal speed (m/s) between consecutive frames of one point; len(track) - 1 values."""
    out = []
    for (x0, y0, _z0), (x1, y1, _z1) in zip(track, track[1:]):
        out.append(math.hypot(x1 - x0, y1 - y0) * fps)
    return out


def planted_speed(feet: list[list[tuple[float, float, float]]], fps: float, height: float,
                  tolerance_share: float = PLANT_TOLERANCE_SHARE) -> dict:
    """The locomotion speed of a cyclic clip from its planted feet.

    feet[k][i] is foot k's position at frame i. A foot is planted on the frames where it is within
    tolerance_share * height of its own lowest point over the clip; while planted it travels with the body, so
    the horizontal speed there is the body's speed. The speed is the median over the planted frames of every foot.
    -> {"speed": m/s or None, "method": "planted_feet" | "fallback", "planted_share": 0..1, "reason": text}.
    None means the clip has no stance to read (flat in-place motion, or too few planted frames): the caller keeps
    its own default speed.
    """
    if not feet or not feet[0] or fps <= 0 or height <= 0:
        return {"speed": None, "method": "fallback", "planted_share": 0.0, "reason": "no feet or no height"}
    tol = tolerance_share * height
    speeds: list[float] = []
    frames_total = 0
    for track in feet:
        if len(track) < 3:
            continue
        low = min(p[2] for p in track)
        speeds_k = _horizontal_speeds(track, fps)
        frames_total += len(speeds_k)
        for i, v in enumerate(speeds_k):
            # the speed between frames i and i+1 belongs to the planted state if both ends are planted
            if track[i][2] <= low + tol and track[i + 1][2] <= low + tol:
                speeds.append(v)
    if frames_total == 0:
        return {"speed": None, "method": "fallback", "planted_share": 0.0, "reason": "clip too short"}
    share = len(speeds) / frames_total
    if share < PLANT_MIN_SHARE:
        return {"speed": None, "method": "fallback", "planted_share": round(share, 4),
                "reason": "planted on %.0f%% of frames, below %.0f%%" % (100 * share, 100 * PLANT_MIN_SHARE)}
    speeds.sort()
    mid = len(speeds) // 2
    median = speeds[mid] if len(speeds) % 2 else (speeds[mid - 1] + speeds[mid]) / 2
    if median < PLANT_MIN_SPEED:
        return {"speed": None, "method": "fallback", "planted_share": round(share, 4),
                "reason": "planted feet barely move (%.3f m/s): in place" % median}
    return {"speed": round(median, 3), "method": "planted_feet", "planted_share": round(share, 4), "reason": ""}


def peak_speed_frame(ends: dict[str, list[tuple[float, float, float]]], fps: float) -> dict:
    """The hit frame of a one-shot clip: the frame of peak limb speed.

    ends maps a limb end (hand, foot) to its positions per frame. The speed at frame i is the central-difference
    speed of that end; the hit is the frame where any end is fastest. Checked on the built-in attack (WP-19 evidence):
    the strike snap is the fastest frame, well above the wind-up and the recovery. A wind-up faster than the strike
    would be picked instead, so each clip's speed curve is written to the report (hit_probe) to check that.
    -> {"frame", "time" (seconds), "speed" (m/s), "end"}; frame is -1 when no end has three frames.
    """
    best = {"frame": -1, "time": -1.0, "speed": 0.0, "end": ""}
    for name, track in ends.items():
        if len(track) < 3:
            continue
        for i in range(1, len(track) - 1):
            v = math.dist(track[i - 1], track[i + 1]) * fps / 2.0
            if v > best["speed"]:
                best = {"frame": i, "time": round(i / fps, 4), "speed": round(v, 3), "end": name}
    return best


HIT_REACH_SHARE = 0.002  # the hit is the first frame within this share of the height of the striking end's furthest reach


def hit_frame(ends: dict[str, list[tuple[float, float, float]]], fps: float, forward, height: float) -> dict:
    """The hit frame of a one-shot clip: contact, where the strike swing ends.

    The striking end is the fastest one (peak_speed_frame). Peak speed falls in the middle of the swing, 2 to 3 frames
    before the hand or foot stops at its furthest point (WP-19 rerun: planted contacts 11, 16 and 13 with peaks 8, 13
    and 10; reviewer marks 2 frames after the peak on every real attack). So from the peak frame on, the hit is the first
    frame where that end is within HIT_REACH_SHARE * height of its furthest point along `forward`.
    -> {"frame", "time", "speed" (the peak speed, m/s), "end", "peak_frame", "method"}; frame -1 when no end has three frames.
    """
    peak = peak_speed_frame(ends, fps)
    out = dict(peak, peak_frame=peak["frame"], method="furthest_reach")
    if peak["frame"] < 0:
        return out
    track = ends[peak["end"]]
    f = v_norm((forward[0], forward[1], forward[2]), (0.0, 1.0, 0.0))
    reach = [v_dot(p, f) for p in track]
    best = max(reach[peak["frame"]:])
    tol = HIT_REACH_SHARE * max(height, 1e-6)
    i = next(k for k in range(peak["frame"], len(track)) if reach[k] >= best - tol)
    out.update(frame=i, time=round(i / fps, 4))
    return out


def speed_row_fields(res: dict) -> dict:
    """The clip-row fields for a planted_speed result. speed_mps is present only when a speed was measured: a clip
    without one (or a library clip, which never goes through here) carries no speed, and the importer keeps its
    default for it."""
    out = {"speed_method": res["method"], "planted_share": res["planted_share"]}
    if res.get("speed") is not None:
        out["speed_mps"] = res["speed"]
    else:
        out["speed_reason"] = res.get("reason", "")
    return out
