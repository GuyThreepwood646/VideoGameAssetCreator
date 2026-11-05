"""Blender headless: fit hit boxes and the body shape for moving to a rigged GLB (plan section 4.2, G34; G16 table).

Run: blender -b --python hitboxes.py -- <rigged.glb> <out.json> [--category humanoid] [--mode detailed|simple|none]
     [--fit snug|normal|loose] [--regions head,chest,...]

Regions come from Mixamo bone names, or for other skeletons from the bone chains found by geometry (legs end near
the ground, the spine runs from the root to the highest leaf, the tail is the rearmost chain, wings reach far to the
side), the same rules procanim.py uses. Each region gets the vertices whose strongest bone belongs to it. A capsule's
axis runs along the region's bone chain, through the middle of those vertices; its radius is the 80th-percentile
vertex distance from the axis times the "how snug" factor; its ends are the bone chain extended by the radius and
clipped to the vertices' extent. Wings get an oriented box at least 5 cm thick. When the regions cannot be told apart
the result is one capsule around the whole body and one sphere at the head end.

The body shape for moving is always written too: a standing capsule, a lying capsule and a box, with the default by
category. Everything in the JSON is in glTF model space (Y up, metres), the space of rig_info.json
bone_segments_gltf. Prints HITBOXES_OK <count> <mode>.

The fitting maths below the imports is plain numpy so the backend tests can run it without Blender; only main()
touches bpy.
"""
import json
import os
import sys

import numpy as np

FIT_FACTORS = {"snug": 0.9, "normal": 1.0, "loose": 1.15}
MODES = ("detailed", "simple", "none")
STANDING = {"humanoid", "winged"}  # standing capsule for moving; the rest lie along the body
HITBOX_CATEGORIES = ("humanoid", "quadruped", "multileg", "winged", "serpent")
PERCENTILE = 80.0
MIN_BOX_THICKNESS = 0.05
# A surface vertex counts as covered when it lies inside a shape or within this fraction of the shape's radius
# (half the box thickness for boxes) of it: hits "land where they look" to within a few millimetres to centimetres.
COVER_TOLERANCE = 0.15
COVER_MIN = 0.85
OVERREACH_MAX = 1.6
FORWARD = np.array([0.0, -1.0, 0.0])  # glTF +Z (the model's front) after the glTF importer's axis change
UP = np.array([0.0, 0.0, 1.0])

MIXAMO_REQUIRED = {"Hips", "Spine", "Head", "LeftArm", "RightArm", "LeftUpLeg", "RightUpLeg"}
# region id: (bones that belong to it, bones whose chain gives the capsule axis). Bones not listed inherit the
# region of their nearest listed ancestor (fingers -> forearm, toes -> shin, eyes -> head).
MIXAMO_REGIONS = {
    "head": (["Neck", "Neck1", "Head", "HeadTop_End", "LeftEye", "RightEye"], ["Neck", "Head"]),
    "chest": (["Spine1", "Spine2", "LeftShoulder", "RightShoulder"], ["Spine1", "Spine2"]),
    "hips": (["Hips", "Spine"], ["Hips", "Spine"]),
    "upper_arm_l": (["LeftArm"], ["LeftArm"]),
    "forearm_l": (["LeftForeArm", "LeftHand"], ["LeftForeArm", "LeftHand"]),
    "upper_arm_r": (["RightArm"], ["RightArm"]),
    "forearm_r": (["RightForeArm", "RightHand"], ["RightForeArm", "RightHand"]),
    "thigh_l": (["LeftUpLeg"], ["LeftUpLeg"]),
    "shin_l": (["LeftLeg", "LeftFoot", "LeftToeBase", "LeftToe_End"], ["LeftLeg"]),
    "thigh_r": (["RightUpLeg"], ["RightUpLeg"]),
    "shin_r": (["RightLeg", "RightFoot", "RightToeBase", "RightToe_End"], ["RightLeg"]),
}

LABELS = {
    "head": "Head", "neck": "Neck", "chest": "Chest", "hips": "Hips", "body": "Body", "tail": "Tail",
    "upper_arm_l": "Left upper arm", "forearm_l": "Left forearm", "upper_arm_r": "Right upper arm", "forearm_r": "Right forearm",
    "thigh_l": "Left thigh", "shin_l": "Left shin", "thigh_r": "Right thigh", "shin_r": "Right shin",
    "leg_fl": "Left front leg", "leg_fr": "Right front leg", "leg_bl": "Left back leg", "leg_br": "Right back leg",
    "leg_l": "Left leg", "leg_r": "Right leg",
    "wing_l": "Left wing", "wing_r": "Right wing", "wings": "Wings",
}
NAMES = {
    "head": "Hit_Head", "neck": "Hit_Neck", "chest": "Hit_Chest", "hips": "Hit_Hips", "body": "Hit_Body", "tail": "Hit_Tail",
    "upper_arm_l": "Hit_UpperArm_L", "forearm_l": "Hit_Forearm_L", "upper_arm_r": "Hit_UpperArm_R", "forearm_r": "Hit_Forearm_R",
    "thigh_l": "Hit_Thigh_L", "shin_l": "Hit_Shin_L", "thigh_r": "Hit_Thigh_R", "shin_r": "Hit_Shin_R",
    "leg_fl": "Hit_Leg_FrontLeft", "leg_fr": "Hit_Leg_FrontRight", "leg_bl": "Hit_Leg_BackLeft", "leg_br": "Hit_Leg_BackRight",
    "leg_l": "Hit_Leg_L", "leg_r": "Hit_Leg_R",
    "wing_l": "Hit_Wing_L", "wing_r": "Hit_Wing_R", "wings": "Hit_Wings",
}


def region_label(rid):
    if rid in LABELS:
        return LABELS[rid]
    if rid.startswith("leg_l") or rid.startswith("leg_r"):
        side = "Left" if rid[4] == "l" else "Right"
        return f"{side} leg {rid[5:]}"
    if rid.startswith("spine_"):
        return f"Body, part {rid[6:]}"
    return rid.replace("_", " ").capitalize()


def region_name(rid):
    if rid in NAMES:
        return NAMES[rid]
    if rid.startswith("leg_l") or rid.startswith("leg_r"):
        return f"Hit_Leg_{rid[4].upper()}{rid[5:]}"
    if rid.startswith("spine_"):
        return f"Hit_Spine_{rid[6:]}"
    return "Hit_" + "".join(p.capitalize() for p in rid.split("_"))


def to_gltf(v):
    """Blender (x, y, z), Z up -> glTF (x, z, -y), Y up; the same change as rig_info bone_segments_gltf."""
    return [float(v[0]), float(v[2]), float(-v[1])]


def _r(v, n=5):
    return [round(float(x), n) for x in v]


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def _perp(axis, prefer=FORWARD):
    """A unit vector perpendicular to `axis`, as close to `prefer` as possible."""
    for cand in (prefer, UP, np.array([1.0, 0.0, 0.0])):
        p = cand - axis * float(np.dot(cand, axis))
        u = _unit(p)
        if u is not None:
            return u
    return np.array([1.0, 0.0, 0.0])


def short_name(name):
    return name.split(":", 1)[1] if ":" in name else name


# --- skeleton classification ------------------------------------------------------------------------------


class Skeleton:
    """Bones as {name, parent, head, tail} in Blender world space (Z up); index helpers."""

    def __init__(self, bones):
        self.bones = [dict(b, head=np.asarray(b["head"], float), tail=np.asarray(b["tail"], float)) for b in bones]
        self.by_name = {b["name"]: b for b in self.bones}
        self.children = {b["name"]: [] for b in self.bones}
        for b in self.bones:
            if b["parent"] in self.children:
                self.children[b["parent"]].append(b["name"])
        self.roots = [b["name"] for b in self.bones if b["parent"] not in self.by_name]
        self.leaves = [b["name"] for b in self.bones if not self.children[b["name"]]]

    def head(self, n):
        return self.by_name[n]["head"]

    def tail(self, n):
        return self.by_name[n]["tail"]

    def chain_to_root(self, n):
        out = []
        while n is not None and n in self.by_name:
            out.append(n)
            n = self.by_name[n]["parent"]
        return out  # leaf first

    def subtree(self, n):
        out, stack = [], [n]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(self.children.get(x, []))
        return out

    def end(self, n):
        """Where the bone really ends: the head of its main child (the one with the largest subtree), else its
        tail. glTF has no bone tails, so the importer's guessed tails can point anywhere."""
        kids = self.children.get(n) or []
        if not kids:
            return self.tail(n)
        main = max(kids, key=lambda k: len(self.subtree(k)))
        e = self.head(main)
        return e if float(np.linalg.norm(e - self.head(n))) > 1e-6 else self.tail(n)

    def length(self, n):
        return float(np.linalg.norm(self.end(n) - self.head(n)))

    def split_by_length(self, chain):
        """Split a limb chain in two where its cumulative length crosses half: (upper part, lower part)."""
        if len(chain) < 2:
            return list(chain), []
        lens = [self.length(n) for n in chain]
        total, acc = sum(lens), 0.0
        for i, ln in enumerate(lens):
            if acc + ln / 2 >= total / 2 and i > 0:
                return list(chain[:i]), list(chain[i:])
            acc += ln
        return list(chain[:-1]), list(chain[-1:])


def is_mixamo(sk):
    return MIXAMO_REQUIRED <= {short_name(b["name"]) for b in sk.bones}


def classify_mixamo(sk):
    """-> {region: {"bones": [...], "axis": [...]}} for the 11 humanoid regions."""
    full = {short_name(b["name"]): b["name"] for b in sk.bones}
    out = {}
    for rid, (members, axis) in MIXAMO_REGIONS.items():
        mem = [full[m] for m in members if m in full]
        ax = [full[m] for m in axis if m in full]
        if mem:
            out[rid] = {"bones": mem, "axis": ax or mem[:1]}
    return out


def validate_mixamo(sk, regions, lo, hi):
    """SkinToken's Mixamo names are sometimes shifted along one arm (the real head is an Extra bone, "Head" sits on
    the arm). Trust the names only when the highest bone belongs to the head and the arms and thighs sit on opposite
    sides. Left and right are then set by position (+X is the left when the model faces glTF +Z). -> regions or None."""
    if not {"head", "upper_arm_l", "upper_arm_r", "thigh_l", "thigh_r"} <= set(regions):
        return None
    b2r = bone_regions(sk, regions)
    top = max(sk.bones, key=lambda b: b["head"][2])["name"]
    if b2r.get(top) != "head":
        return None
    centre_x = float((lo[0] + hi[0]) / 2)
    side = lambda rid: float(np.mean([sk.head(b)[0] for b in regions[rid]["bones"]]) - centre_x)  # noqa: E731
    out = dict(regions)
    for a, b in (("upper_arm_l", "upper_arm_r"), ("thigh_l", "thigh_r")):
        if side(a) * side(b) >= 0:
            return None
    if side("upper_arm_l") < 0:  # names mirrored: swap every _l and _r region
        out = {(k[:-2] + "_r" if k.endswith("_l") else k[:-2] + "_l" if k.endswith("_r") else k): v for k, v in regions.items()}
    return out


def _own_chain(sk, leaf, shared):
    """From the leaf up to (not including) the first bone in `shared`; returned top first."""
    own = []
    for n in sk.chain_to_root(leaf):
        if n in shared:
            break
        own.append(n)
    return own[::-1]


def _body_frame(lo, hi):
    dims = hi - lo
    fwd_axis = np.array([1.0, 0.0, 0.0]) if dims[0] > dims[1] else np.array([0.0, 1.0, 0.0])
    return dims, fwd_axis


def classify_standing(sk, lo, hi, category):
    """Generic skeleton of a creature that stands on legs (procanim rules). -> regions or None when the chains
    cannot be found."""
    dims, fwd_axis = _body_frame(lo, hi)
    height = float(dims[2])
    if height <= 1e-6:
        return None
    centre = (lo + hi) / 2
    span = float(max(dims[0], dims[1]))
    max_legs = {"humanoid": 2, "multileg": 8}.get(category, 4)
    low = lambda n: float(min(sk.head(n)[2], sk.tail(n)[2]) - lo[2])  # noqa: E731
    leg_leaves = sorted([n for n in sk.leaves if low(n) < 0.2 * height], key=low)
    legs = []
    for n in leg_leaves:
        p = sk.head(n)
        if all(np.linalg.norm((p - sk.head(c))[:2]) > 0.15 * span for c in legs):
            legs.append(n)
    legs = legs[:max_legs]
    if len(legs) < 2:
        return None
    upper = [n for n in sk.leaves if n not in leg_leaves and sk.head(n)[2] - lo[2] > 0.5 * height]
    if upper:
        mean_upper = np.mean([sk.head(n) for n in upper], axis=0)
        forward = fwd_axis if float(np.dot(mean_upper - centre, fwd_axis)) >= 0 else -fwd_axis
    else:
        forward = fwd_axis if float(np.dot(FORWARD, fwd_axis)) >= 0 else -fwd_axis
    if category == "humanoid":
        forward = FORWARD.copy()  # a biped's legs say nothing about its front; generated models face glTF +Z
    lateral = np.cross(UP, forward)  # the creature's left (facing -Y, +X is its left hand)
    lateral = lateral / (np.linalg.norm(lateral) or 1.0)
    left = lambda n: float(np.dot(sk.head(n) - centre, lateral)) > 0  # noqa: E731
    # Spine: root to the highest upper leaf that is not far out to the side (arms and wings are).
    side = lambda n: abs(float(np.dot(sk.head(n) - centre, lateral)))  # noqa: E731
    central = [n for n in upper if side(n) < 0.2 * max(span, height)] or upper
    if not central:
        return None
    spine_leaf = max(central, key=lambda n: sk.head(n)[2])
    spine = sk.chain_to_root(spine_leaf)[::-1]
    spine_set = set(spine)
    leg_chains = {}
    leg_sets = [set(sk.chain_to_root(n)) for n in legs]
    for n, s in zip(legs, leg_sets):
        shared = spine_set | set().union(*(o for o in leg_sets if o is not s))
        own = _own_chain(sk, n, shared)
        if own:
            leg_chains[n] = own
    if len(leg_chains) < 2:
        return None
    if category != "humanoid":
        # A leg chain often starts with a shoulder or hip bone inside the torso; its vertices are body, not leg.
        # Leading bones whose head sits above three quarters of the way from the ground to the spine go to the body.
        spine_z = float(np.mean([sk.head(n)[2] for n in spine]))
        cut = float(lo[2]) + 0.75 * (spine_z - float(lo[2]))
        for n, chain in leg_chains.items():
            while len(chain) > 2 and sk.head(chain[0])[2] > cut:
                chain.pop(0)
    used = spine_set | {b for c in leg_chains.values() for b in c}
    # Side chains: arms (humanoid) or wings (winged), the leaf farthest out on each side.
    side_chains = {}
    if category in ("humanoid", "winged"):
        reach = 0.12 * height if category == "humanoid" else 0.25 * span
        cands = [n for n in sk.leaves if n not in used and side(n) > reach and sk.head(n)[2] - lo[2] > 0.3 * height]
        for want_left, key in ((True, "l"), (False, "r")):
            s_c = [n for n in cands if left(n) == want_left]
            if s_c:
                best = max(s_c, key=side)
                own = _own_chain(sk, best, used)
                if category == "humanoid" and len(own) >= 3:
                    # The collarbone starts at the spine: it belongs to the chest, not the upper arm.
                    reach = max(side(n) for n in own)
                    while len(own) > 2 and side(own[0]) < 0.35 * reach:
                        own.pop(0)
                if own:
                    side_chains[key] = own
                    used |= set(own)
    # Tail: the rearmost low leaf that is not a leg.
    tail_chain = []
    if category != "humanoid":
        rear = [n for n in sk.leaves if n not in used and float(np.dot(sk.head(n) - centre, forward)) < -0.2 * span]
        if rear:
            tleaf = min(rear, key=lambda n: float(np.dot(sk.head(n) - centre, forward)))
            tail_chain = _own_chain(sk, tleaf, used)
            used |= set(tail_chain)
    # Head group: spine bones after the last bone a leg, arm, wing or tail hangs from.
    attach = []
    for chain in list(leg_chains.values()) + list(side_chains.values()) + ([tail_chain] if tail_chain else []):
        par = sk.by_name[chain[0]]["parent"]
        if par in spine_set:
            attach.append(spine.index(par))
    last = max(attach) if attach else 0
    group = spine[last + 1:] if last + 1 < len(spine) else spine[-1:]
    body_spine = [n for n in spine if n not in group] or spine[:1]
    regions = {}
    if category == "humanoid":
        head_bones = [b for g in group for b in sk.subtree(g) if b not in used or b in group]
        regions["head"] = {"bones": head_bones, "axis": group}
        half = max(1, len(body_spine) // 2)
        regions["hips"] = {"bones": body_spine[:half], "axis": body_spine[:half]}
        regions["chest"] = {"bones": body_spine[half:] or body_spine[-1:], "axis": body_spine[half:] or body_spine[-1:]}
        if regions["chest"]["bones"] == regions["hips"]["bones"]:
            regions.pop("chest")
        for key, chain in side_chains.items():
            up, low = sk.split_by_length(chain)
            regions[f"upper_arm_{key}"] = {"bones": up, "axis": up}
            if low:
                regions[f"forearm_{key}"] = {"bones": low, "axis": low}
        for n, chain in leg_chains.items():
            key = "l" if left(n) else "r"
            if f"thigh_{key}" in regions:
                key = "r" if key == "l" else "l"
            up, low = sk.split_by_length(chain)
            regions[f"thigh_{key}"] = {"bones": up, "axis": up}
            if low:
                regions[f"shin_{key}"] = {"bones": low, "axis": low}
        return regions
    neck = group[:1] if len(group) >= 2 else []
    head_root = group[1] if neck else group[0]
    head_bones = [b for b in sk.subtree(head_root)]
    if neck:
        head_bones += [b for c in sk.children[neck[0]] if c != head_root for b in sk.subtree(c) if b not in used]
    regions["head"] = {"bones": head_bones, "axis": [b for b in group if b in head_bones]}
    if neck:
        regions["neck"] = {"bones": neck, "axis": neck}
    regions["body"] = {"bones": body_spine, "axis": body_spine}
    if tail_chain:
        regions["tail"] = {"bones": tail_chain, "axis": tail_chain}
    legs_sorted = sorted(leg_chains, key=lambda n: -float(np.dot(sk.head(n) - centre, forward)))
    if len(leg_chains) == 4 and category != "multileg":
        front = set(legs_sorted[:2])
        for n in legs_sorted:
            rid = "leg_" + ("f" if n in front else "b") + ("l" if left(n) else "r")
            if rid in regions:
                rid = rid[:-1] + ("r" if rid.endswith("l") else "l")
            regions[rid] = {"bones": leg_chains[n], "axis": leg_chains[n]}
    elif len(leg_chains) == 2 and category != "multileg":
        for n in legs_sorted:
            rid = "leg_l" if left(n) else "leg_r"
            if rid in regions:
                rid = "leg_r" if rid == "leg_l" else "leg_l"
            regions[rid] = {"bones": leg_chains[n], "axis": leg_chains[n]}
    else:
        count = {"l": 0, "r": 0}
        for n in legs_sorted:
            s = "l" if left(n) else "r"
            count[s] += 1
            regions[f"leg_{s}{count[s]}"] = {"bones": leg_chains[n], "axis": leg_chains[n]}
    for key, chain in side_chains.items():
        regions[f"wing_{key}"] = {"bones": chain, "axis": chain, "kind": "box"}
    return regions


def classify_serpent(sk, lo, hi, detailed=True):
    """The longest bone path is the spine; its higher (else front) end is the head. -> regions or None."""
    if len(sk.bones) < 3:
        return None
    paths = {leaf: sk.chain_to_root(leaf)[::-1] for leaf in sk.leaves}  # root first
    best, best_len = None, -1.0
    leaves = list(paths)
    for i, a in enumerate(leaves):
        for b in leaves[i:]:
            pa, pb = paths[a], paths[b]
            k = 0
            while k < min(len(pa), len(pb)) and pa[k] == pb[k]:
                k += 1
            common = pa[k - 1] if k else None
            path = pa[k:][::-1] + ([common] if common else []) + pb[k:]
            length = sum(sk.length(n) for n in path)
            if length > best_len:
                best, best_len = path, length
    if not best or len(best) < 3:
        return None
    za, zb = sk.head(best[0])[2], sk.head(best[-1])[2]
    if abs(za - zb) < 0.02 * float(hi[2] - lo[2] + 1e-9):
        fa, fb = float(np.dot(sk.head(best[0]), FORWARD)), float(np.dot(sk.head(best[-1]), FORWARD))
        head_first = fa > fb
    else:
        head_first = za > zb
    spine = best if head_first else best[::-1]  # head end first
    nh = 2 if len(spine) >= 6 else 1
    head = spine[:nh]
    body = spine[nh:]
    spine_set = set(spine)
    head_bones = list(head) + [b for h in head for c in sk.children[h] if c not in spine_set for b in sk.subtree(c)]
    regions = {"head": {"bones": head_bones, "axis": head[::-1]}}
    if not body:
        return None
    n = 3 if not detailed else max(4, min(10, round(len(sk.bones) / 3)))
    n = max(1, min(n, len(body)))
    cuts = np.linspace(0, len(body), n + 1).round().astype(int)
    for i in range(n):
        seg = body[cuts[i]:cuts[i + 1]]
        regions[f"spine_{i + 1}"] = {"bones": seg, "axis": seg[::-1]}
    return regions


SIMPLE_MERGE = {
    "humanoid": {"head": ["head"]},
    "quadruped": {"head": ["head", "neck"]},
    "multileg": {"head": ["head", "neck"]},
    "winged": {"head": ["head", "neck"], "wings": ["wing_l", "wing_r"]},
}


def simplify(regions, category):
    """Detailed regions -> the simple set: head, body (and wings as one box for winged)."""
    if category == "serpent":
        return regions  # classify_serpent already made 3 spine parts
    merge = SIMPLE_MERGE.get(category, {"head": ["head"]})
    taken = {r for rs in merge.values() for r in rs}
    out = {}
    for rid, parts in merge.items():
        present = [p for p in parts if p in regions]
        if not present:
            continue
        out[rid] = {"bones": [b for p in present for b in regions[p]["bones"]],
                    "axis": regions[present[0]]["axis"], "kind": "box" if rid == "wings" else "capsule"}
    rest = [r for r in regions if r not in taken]
    body_axis = (regions.get("body") or {}).get("axis")
    if body_axis is None:
        hips, chest = regions.get("hips"), regions.get("chest")
        body_axis = (hips["axis"] if hips else []) + (chest["axis"] if chest else [])
    if rest:
        out["body"] = {"bones": [b for r in rest for b in regions[r]["bones"]], "axis": body_axis or regions[rest[0]]["axis"],
                       "ends": "points"}
    return out


def bone_regions(sk, regions, default="body"):
    """-> {bone: region} for every bone: listed bones first, the rest inherit their nearest listed ancestor."""
    direct = {}
    for rid, r in regions.items():
        for b in r["bones"]:
            direct.setdefault(b, rid)
    fallback = default if default in regions else (next(iter(regions)) if regions else None)
    out = {}
    for b in sk.bones:
        n = b["name"]
        rid = None
        for a in sk.chain_to_root(n):
            if a in direct:
                rid = direct[a]
                break
        out[n] = rid or fallback
    return out


# --- shape fitting --------------------------------------------------------------------------------------------


def fit_capsule(P, a, b, factor=1.0, pct=PERCENTILE, ends="bones"):
    """Capsule around points P (n, 3) whose axis runs along a -> b, through the middle of the points. The radius is
    the pct-th percentile distance from the axis times factor; the ends are a and b pushed out by the radius and
    clipped to the points' extent along the axis (ends="points": the points' own 1st to 99th percentile extent,
    for a merged body whose bones cover only the torso). -> {center, axis, radius, height} (height includes the
    caps)."""
    P = np.asarray(P, float)
    c = P.mean(axis=0)
    u = _unit(np.asarray(b, float) - np.asarray(a, float))
    if u is None:
        u = principal_axis(P)
    t = (P - c) @ u
    radial = P - c - np.outer(t, u)
    dist = np.linalg.norm(radial, axis=1)
    r = max(float(np.percentile(dist, pct)) * factor, 0.005)
    ta, tb = sorted((float(np.dot(np.asarray(a, float) - c, u)), float(np.dot(np.asarray(b, float) - c, u))))
    lo_t, hi_t = float(t.min()), float(t.max())
    e0, e1 = max(ta - r, lo_t), min(tb + r, hi_t)
    if ends == "points":
        e0, e1 = float(np.percentile(t, 1)), float(np.percentile(t, 99))
    if e1 < e0:  # bone outside the points: use the points' own extent
        e0, e1 = lo_t, hi_t
    mid = (e0 + e1) / 2
    height = max(e1 - e0, 2 * r)
    centre = c + u * mid
    return {"kind": "capsule", "center": centre, "axis": u, "radius": r, "height": height}


def principal_axis(P):
    P = np.asarray(P, float)
    if len(P) < 3:
        return UP.copy()
    w, v = np.linalg.eigh(np.cov((P - P.mean(axis=0)).T))
    return v[:, int(np.argmax(w))]


def fit_box(P, factor=1.0, min_thickness=MIN_BOX_THICKNESS):
    """Oriented box around P from its principal axes: Y along the longest spread, Z along the thinnest; extents from
    the 2nd to 98th percentile times factor, at least min_thickness thick. -> {center, axis, forward, size}."""
    P = np.asarray(P, float)
    c = P.mean(axis=0)
    w, v = np.linalg.eigh(np.cov((P - c).T))
    order = np.argsort(w)[::-1]
    ey, ex, ez = v[:, order[0]], v[:, order[1]], v[:, order[2]]
    size, mid = [], np.zeros(3)
    for i, e in enumerate((ex, ey, ez)):
        proj = (P - c) @ e
        lo, hi = float(np.percentile(proj, 2)), float(np.percentile(proj, 98))
        mid += e * (lo + hi) / 2
        size.append(max((hi - lo) * factor, min_thickness if i == 2 else 0.01))
    return {"kind": "box", "center": c + mid, "axis": ey, "forward": ez, "size": size}


def fit_sphere(P, factor=1.0, pct=PERCENTILE):
    P = np.asarray(P, float)
    c = (np.percentile(P, 2, axis=0) + np.percentile(P, 98, axis=0)) / 2
    r = max(float(np.percentile(np.linalg.norm(P - c, axis=1), pct)) * factor, 0.01)
    return {"kind": "sphere", "center": c, "axis": UP.copy(), "radius": r, "height": 2 * r}


def inside(shape, P, tol=0.0):
    """Boolean mask: points within `tol` (a fraction of the shape's radius, or of half the box thickness) of it."""
    P = np.asarray(P, float)
    c = np.asarray(shape["center"], float)
    if shape["kind"] == "box":
        ey = np.asarray(shape["axis"], float)
        ez = np.asarray(shape["forward"], float)
        ex = np.cross(ey, ez)
        half = np.asarray(shape["size"], float) / 2
        pad = tol * half[2]
        d = P - c
        return (np.abs(d @ ex) <= half[0] + pad) & (np.abs(d @ ey) <= half[1] + pad) & (np.abs(d @ ez) <= half[2] + pad)
    r = float(shape["radius"])
    if shape["kind"] == "sphere":
        return np.linalg.norm(P - c, axis=1) <= r * (1 + tol)
    u = np.asarray(shape["axis"], float)
    half_core = max(float(shape["height"]) / 2 - r, 0.0)
    t = np.clip((P - c) @ u, -half_core, half_core)
    nearest = c + np.outer(t, u)
    return np.linalg.norm(P - nearest, axis=1) <= r * (1 + tol)


def shape_volume(shape):
    if shape["kind"] == "box":
        s = shape["size"]
        return float(s[0] * s[1] * s[2])
    r = float(shape["radius"])
    if shape["kind"] == "sphere":
        return 4.0 / 3.0 * np.pi * r ** 3
    core = max(float(shape["height"]) - 2 * r, 0.0)
    return float(np.pi * r * r * core + 4.0 / 3.0 * np.pi * r ** 3)


def shape_bounds(shape):
    c = np.asarray(shape["center"], float)
    if shape["kind"] == "box":
        ey = np.asarray(shape["axis"], float)
        ez = np.asarray(shape["forward"], float)
        ex = np.cross(ey, ez)
        half = np.asarray(shape["size"], float) / 2
        ext = np.abs(ex) * half[0] + np.abs(ey) * half[1] + np.abs(ez) * half[2]
        return c - ext, c + ext
    r = float(shape["radius"])
    u = np.asarray(shape["axis"], float)
    half_core = max(float(shape["height"]) / 2 - r, 0.0)
    ext = np.abs(u) * half_core + r
    return c - ext, c + ext


def union_volume(shapes, res=48):
    """Volume of the union of the shapes (overlaps counted once), sampled on a res^3 grid over their joint bounds."""
    if not shapes:
        return 0.0
    bounds = [shape_bounds(s) for s in shapes]
    lo = np.min([b[0] for b in bounds], axis=0)
    hi = np.max([b[1] for b in bounds], axis=0)
    size = np.maximum(hi - lo, 1e-6)
    axes = [lo[i] + (np.arange(res) + 0.5) * size[i] / res for i in range(3)]
    g = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    hit = np.zeros(len(g), bool)
    for s in shapes:
        blo, bhi = shape_bounds(s)
        m = np.all((g >= blo) & (g <= bhi), axis=1) & ~hit
        if m.any():
            idx = np.nonzero(m)[0]
            hit[idx[inside(s, g[idx])]] = True
    return float(hit.mean() * np.prod(size))


def mesh_volume(V, tris):
    """Absolute signed-tetrahedron volume of a triangle mesh (closed meshes give their exact volume)."""
    if tris is None or len(tris) == 0:
        return 0.0
    V = np.asarray(V, float)
    T = np.asarray(tris, int)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)


# --- body shape for moving (G16 table) ------------------------------------------------------------------------


def movement_shapes(V, category):
    """Standing capsule, lying capsule and box from the vertices (Blender space). -> {default, upright, along, box}."""
    V = np.asarray(V, float)
    lo, hi = V.min(axis=0), V.max(axis=0)
    dims = hi - lo
    h = float(dims[2])
    # Standing capsule: height = model height; radius from the median width of the torso band (30 to 70 percent).
    rs, mids = [], []
    for z0 in np.linspace(0.3, 0.7, 9)[:-1]:
        m = (V[:, 2] >= lo[2] + z0 * h) & (V[:, 2] < lo[2] + (z0 + 0.05) * h)
        if m.sum() < 4:
            continue
        S = V[m]
        a, b = np.percentile(S[:, :2], 5, axis=0), np.percentile(S[:, :2], 95, axis=0)
        rs.append(0.5 * float(max(b - a)))
        mids.append((a + b) / 2)
    r_up = float(np.median(rs)) if rs else 0.25 * float(max(dims[0], dims[1]))
    r_up = min(max(r_up, 0.02), h / 2 if h > 0 else 0.02)
    mid_xy = np.median(mids, axis=0) if mids else (lo[:2] + hi[:2]) / 2
    upright = {"kind": "capsule", "center": np.array([mid_xy[0], mid_xy[1], lo[2] + h / 2]), "axis": UP.copy(),
               "radius": r_up, "height": max(h, 2 * r_up)}
    # Lying capsule along the longer ground axis; radius from the median cross-section of the middle 60 percent
    # of the length, reaching down to the ground so it stands on the floor.
    ax = 0 if dims[0] > dims[1] else 1
    side = 1 - ax
    length = float(dims[ax])
    rs, sides = [], []
    for f0 in np.linspace(0.2, 0.8, 13)[:-1]:
        m = (V[:, ax] >= lo[ax] + f0 * length) & (V[:, ax] < lo[ax] + (f0 + 0.05) * length)
        if m.sum() < 4:
            continue
        S = V[m]
        a, b = float(np.percentile(S[:, side], 5)), float(np.percentile(S[:, side], 95))
        top = float(np.percentile(S[:, 2], 95)) - float(lo[2])
        rs.append(0.5 * max(b - a, top))
        sides.append((a + b) / 2)
    r_al = float(np.median(rs)) if rs else 0.5 * h
    r_al = min(max(r_al, 0.02), max(h / 2, 0.02), max(length / 2, 0.02))
    centre = np.zeros(3)
    centre[ax] = (lo[ax] + hi[ax]) / 2
    centre[side] = float(np.median(sides)) if sides else (lo[side] + hi[side]) / 2
    centre[2] = lo[2] + r_al
    axis = np.zeros(3)
    axis[ax] = 1.0
    along = {"kind": "capsule", "center": centre, "axis": axis, "radius": r_al, "height": max(length, 2 * r_al)}
    box = {"kind": "box", "center": (lo + hi) / 2, "axis": UP.copy(), "forward": FORWARD.copy(),
           "size_world": dims.copy()}
    default = "upright" if category in STANDING else "along"
    return {"default": default, "upright": upright, "along": along, "box": box}


# --- whole fit ------------------------------------------------------------------------------------------------


def classify(sk, lo, hi, category, mode):
    """-> (regions, skeleton_kind). regions is None when the regions cannot be told apart."""
    regions = None
    if category == "humanoid" and is_mixamo(sk):
        regions, kind = validate_mixamo(sk, classify_mixamo(sk), lo, hi), "mixamo"
    if regions:
        pass
    elif category == "humanoid":
        regions, kind = classify_standing(sk, lo, hi, category), "generic"
    elif category == "serpent":
        regions, kind = classify_serpent(sk, lo, hi, detailed=(mode == "detailed")), "generic"
        return regions, kind
    else:
        regions, kind = classify_standing(sk, lo, hi, category), "generic"
    if regions and mode == "simple":
        regions = simplify(regions, category)
    return regions, kind


def _fallback(sk, V, owner_names, category, factor, lo, hi):
    """One capsule around the whole body and one sphere at the head end (top when standing, front when lying)."""
    dims = hi - lo
    if category in STANDING:
        ax = UP.copy()
    else:
        ax = np.array([1.0, 0.0, 0.0]) if dims[0] > dims[1] else np.array([0.0, 1.0, 0.0])
    c = V.mean(axis=0)
    t = (V - c) @ ax
    span = float(t.max() - t.min())
    if category in STANDING:
        end = t >= t.max() - 0.15 * span
    else:
        front = t >= t.max() - 0.15 * span
        back = t <= t.min() + 0.15 * span
        top_f = float(np.percentile(V[front][:, 2], 90)) if front.any() else 0.0
        top_b = float(np.percentile(V[back][:, 2], 90)) if back.any() else 0.0
        if abs(top_f - top_b) < 0.02 * float(dims[2] + 1e-9):
            use_front = float(np.dot(ax, FORWARD)) >= 0
        else:
            use_front = top_f > top_b
        end = front if use_front else back
    body = fit_capsule(V, c + ax * t.min(), c + ax * t.max(), factor, ends="points")
    head = fit_sphere(V[end], factor)
    roots_owned = {}
    for n in owner_names:
        roots_owned[n] = roots_owned.get(n, 0) + 1
    root = max(sk.roots, key=lambda n: sum(roots_owned.get(b, 0) for b in sk.subtree(n))) if sk.roots else None
    head_owner = {}
    for n in np.asarray(owner_names, object)[end]:
        head_owner[n] = head_owner.get(n, 0) + 1
    head_bone = max(head_owner, key=head_owner.get) if head_owner else root
    body.update(region="body", bone=root, vertices=int(len(V)))
    head.update(region="head", bone=head_bone, vertices=int(end.sum()))
    return [head, body], np.ones(len(V), bool)


def fit(bones, V, owner, tris, category, mode="detailed", fit_name="normal", keep_regions=None):
    """The whole fit in Blender space. bones: [{name, parent, head, tail}]; V: (n, 3) rest-pose vertices; owner:
    (n,) index into bones of each vertex's strongest bone (-1 when unweighted); tris: (m, 3) or None.
    -> result dict (Blender space; see to_json for the file)."""
    V = np.asarray(V, float)
    owner = np.asarray(owner, int)
    sk = Skeleton(bones)
    names = [b["name"] for b in sk.bones]
    factor = FIT_FACTORS.get(fit_name, 1.0)
    lo, hi = V.min(axis=0), V.max(axis=0)
    # Unweighted vertices belong to the nearest bone segment.
    if (owner < 0).any() and names:
        miss = np.nonzero(owner < 0)[0]
        H = np.array([sk.head(n) for n in names])
        T = np.array([sk.tail(n) for n in names])
        d = T - H
        dd = np.maximum((d * d).sum(axis=1), 1e-12)
        P = V[miss][:, None, :]
        s = np.clip(((P - H[None]) * d[None]).sum(axis=2) / dd[None], 0, 1)
        q = H[None] + s[..., None] * d[None]
        owner[miss] = np.argmin(np.linalg.norm(P - q, axis=2), axis=1)
    owner_names = [names[i] if 0 <= i < len(names) else None for i in owner]
    move = movement_shapes(V, category)
    result = {"category": category, "mode": mode, "fit": fit_name, "factor": factor, "fallback": False,
              "skeleton": "mixamo" if (category == "humanoid" and is_mixamo(sk)) else "generic",
              "shapes": [], "regions_found": [], "skipped": [], "movement": move,
              "verts": int(len(V)), "mesh_volume": mesh_volume(V, tris)}
    if mode == "none" or not names:
        result["mode"] = "none"
        return result
    regions, kind = classify(sk, lo, hi, category, mode)
    result["skeleton"] = kind
    min_verts = max(12, int(0.003 * len(V)))
    shapes, covered_mask = [], None
    if regions:
        b2r = bone_regions(sk, regions)
        vreg = np.array([b2r.get(n) if n else None for n in owner_names], object)
        for rid, r in regions.items():
            m = vreg == rid
            if int(m.sum()) < min_verts:
                result["skipped"].append(rid)
                continue
            P = V[m]
            if r.get("kind") == "box":
                s = fit_box(P, factor)
            else:
                ax = r["axis"] or r["bones"][:1]
                s = fit_capsule(P, sk.head(ax[0]), sk.end(ax[-1]), factor, ends=r.get("ends", "bones"))
            counts = {}
            for n in np.asarray(owner_names, object)[m]:
                counts[n] = counts.get(n, 0) + 1
            own = {n: c for n, c in counts.items() if n in set(r["bones"])} or counts
            s.update(region=rid, bone=max(own, key=own.get), vertices=int(m.sum()))
            shapes.append(s)
        if category == "winged":
            wing_ids = {s["region"] for s in shapes if s["region"].startswith("wing")}
            if wing_ids:
                keep = ~np.isin(vreg, list(wing_ids))
                if keep.sum() > 10:
                    up = movement_shapes(V[keep], category)["upright"]
                    move["upright"] = up
        has_head = any(s["region"] == "head" for s in shapes)
        if len(shapes) < 2 or not has_head:
            shapes = []
        else:
            result["regions_found"] = [s["region"] for s in shapes]
            if keep_regions:
                keep = set(keep_regions)
                shapes = [s for s in shapes if s["region"] in keep]
                covered_mask = np.isin(vreg, [s["region"] for s in shapes])
            else:
                covered_mask = np.ones(len(V), bool)
    if not shapes:
        shapes, covered_mask = _fallback(sk, V, owner_names, category, factor, lo, hi)
        result["fallback"] = True
        result["regions_found"] = ["head", "body"]
        if keep_regions:
            shapes = [s for s in shapes if s["region"] in set(keep_regions)]
    result["shapes"] = shapes
    result.update(score(shapes, V[covered_mask] if covered_mask is not None else V, result["mesh_volume"]))
    return result


def score(shapes, V, mesh_vol):
    """cover: share of the vertices inside a shape (within COVER_TOLERANCE of its radius). overreach: the shapes'
    union volume (overlaps counted once) over the mesh volume; None when the mesh has no measurable volume."""
    if not shapes or len(V) == 0:
        return {"cover": 0.0, "overreach": None, "shapes_volume": 0.0, "shapes_volume_sum": 0.0}
    hit = np.zeros(len(V), bool)
    for s in shapes:
        hit |= inside(s, V, COVER_TOLERANCE)
    uv = union_volume(shapes)
    return {"cover": round(float(hit.mean()), 4), "overreach": round(uv / mesh_vol, 3) if mesh_vol > 1e-9 else None,
            "shapes_volume": round(uv, 6), "shapes_volume_sum": round(sum(shape_volume(s) for s in shapes), 6)}


def _shape_json(s, sk=None):
    """One shape in glTF space. Every key is always present (numbers, never null) so Unity's JsonUtility reads it."""
    axis = np.asarray(s["axis"], float)
    fwd = np.asarray(s["forward"], float) if "forward" in s else _perp(axis)
    if s["kind"] == "box":
        size = [float(x) for x in s["size"]]
        radius, height = 0.0, size[1]
    else:
        radius, height = float(s["radius"]), float(s["height"])
        size = [2 * radius, height, 2 * radius]
    out = {"kind": s["kind"], "center": _r(to_gltf(s["center"])), "axis": _r(to_gltf(axis)), "forward": _r(to_gltf(fwd)),
           "radius": round(radius, 5), "height": round(height, 5), "size": _r(size)}
    return out


def to_json(result, bones):
    """The hitboxes.json document (glTF space)."""
    sk = Skeleton(bones)
    boxes = []
    for s in result["shapes"]:
        rid = s["region"]
        d = {"name": region_name(rid), "region": rid, "label": region_label(rid), "bone": s.get("bone") or "", **_shape_json(s)}
        bone = s.get("bone")
        if bone in sk.by_name:
            bh, bt = to_gltf(sk.head(bone)), to_gltf(sk.tail(bone))
            d["bone_head"], d["bone_tail"] = _r(bh), _r(bt)
            d["offset"] = _r(np.asarray(d["center"]) - np.asarray(bh))
        else:
            d["bone_head"] = d["bone_tail"] = d["offset"] = [0.0, 0.0, 0.0]
        d["vertices"] = int(s.get("vertices", 0))
        boxes.append(d)
    move = result["movement"]
    mv = {"default": move["default"]}
    for key in ("upright", "along"):
        s = move[key]
        j = _shape_json(s)
        a = [abs(x) for x in j["axis"]]
        j["direction"] = int(np.argmax(a))  # Unity CapsuleCollider.direction: 0 X, 1 Y, 2 Z
        mv[key] = j
    b = move["box"]
    dims = b["size_world"]
    mv["box"] = {"kind": "box", "center": _r(to_gltf(b["center"])), "axis": [0.0, 1.0, 0.0], "forward": [0.0, 0.0, 1.0],
                 "radius": 0.0, "height": round(float(dims[2]), 5), "size": _r([dims[0], dims[2], dims[1]]), "direction": 1}
    return {
        "version": 1,
        "space": "gltf",
        "units": "m",
        "category": result["category"],
        "mode": result["mode"],
        "fit": result["fit"],
        "factor": result["factor"],
        "fallback": bool(result["fallback"]),
        "skeleton": result["skeleton"],
        "count": len(boxes),
        "regions_found": [{"id": r, "label": region_label(r)} for r in result["regions_found"]],
        "skipped": list(result.get("skipped") or []),
        "hitboxes": boxes,
        "movement": mv,
        "cover": result.get("cover"),
        "overreach": result.get("overreach"),
        "shapes_volume": result.get("shapes_volume"),
        "shapes_volume_sum": result.get("shapes_volume_sum"),
        "mesh_volume": round(float(result.get("mesh_volume") or 0.0), 6),
        "verts": result["verts"],
        "cover_min": COVER_MIN,
        "overreach_max": OVERREACH_MAX,
    }


# --- Blender side ---------------------------------------------------------------------------------------------


def main():
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    argv = _common.after_double_dash()
    src, out = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    category = _common.opt("--category", "humanoid")
    mode = _common.opt("--mode", "detailed")
    fit_name = _common.opt("--fit", "normal")
    regions = [r for r in _common.opt("--regions", "").split(",") if r.strip()]
    _common.open_glb(src)
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups] or \
             [o for o in bpy.context.scene.objects if o.type == "MESH"]
    bones = []
    if arms:
        arm = arms[0]
        W = arm.matrix_world
        bones = [{"name": b.name, "parent": b.parent.name if b.parent else None,
                  "head": list(W @ b.head_local), "tail": list(W @ b.tail_local)} for b in arm.data.bones]
    index = {b["name"]: i for i, b in enumerate(bones)}
    Vs, owners, tris, base = [], [], [], 0
    for o in meshes:
        me = o.data
        n = len(me.vertices)
        co = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        M = np.array(o.matrix_world)
        Vs.append(co @ M[:3, :3].T + M[:3, 3])
        gname = {g.index: g.name for g in o.vertex_groups}
        own = np.full(n, -1, dtype=np.int64)
        for v in me.vertices:
            best, bw = -1, 0.0
            for g in v.groups:
                if g.weight > bw and gname.get(g.group) in index:
                    best, bw = index[gname[g.group]], g.weight
            own[v.index] = best
        owners.append(own)
        me.calc_loop_triangles()
        t = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", t)
        tris.append(t.reshape(-1, 3) + base)
        base += n
    V = np.concatenate(Vs) if Vs else np.zeros((0, 3))
    owner = np.concatenate(owners) if owners else np.zeros(0, int)
    T = np.concatenate(tris) if tris else None
    if len(V) == 0:
        raise SystemExit("no mesh")
    result = fit(bones, V, owner, T, category, mode, fit_name, regions or None)
    doc = to_json(result, bones)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(doc, f, indent=1)
    print("HITBOXES_OK", doc["count"], doc["mode"], "fallback" if doc["fallback"] else "", "cover", doc["cover"], "overreach", doc["overreach"])


if __name__ == "__main__":
    main()
