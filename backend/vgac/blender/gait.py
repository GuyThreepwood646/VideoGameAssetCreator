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
