"""Attachment sockets: named points on the skeleton where a game attaches a weapon, hat, backpack or saddle.

Pure maths and tables (numpy only, no bpy), so the backend tests can import it. pack.py (inside Blender) loads it
from this folder (sys.path insert, like blender/gait.py) to place one empty per socket, parented to its bone, in the
FBX and every GLB; the export stage writes sockets.json from the same result.

Sets:

    humanoid    Socket_Hand_R, Socket_Hand_L, Socket_Back, Socket_Hip_L, Socket_Hip_R, Socket_Head, Socket_Chest
    creatures   Socket_Head, Socket_Back (saddle point over the spine), Socket_Mouth (when a head is found)

Body parts come from vgac/blender/hitboxes.py: Mixamo names when they describe a person, else the bone chains found
by geometry (legs end near the ground, the spine runs to the highest leaf, and so on). Positions come from bone heads
plus the skinned surface: the back, chest and hip sockets are cast from inside the body outward to the outermost
surface of the torso (arms left out, so a hanging hand is never mistaken for the belt) and sit SURFACE_OFFSET outside
it; the head socket sits on top of the skull on the centre line (ears and horns to the side are skipped); hand
sockets sit in the middle of the palm.

Orientation (every socket): +Z is the socket's forward and +Y its up, in glTF and Unity terms (X = Y x Z in glTF,
right-handed; Unity's left-handed import keeps forward and up). An item modelled facing +Z with +Y up and its grip or
base at the origin lands correctly with no extra rotation.

    hands   forward along the grip, out of the index-finger side of the fist (where a blade or torch head points);
            up toward the thumb
    back    forward straight out of the back (away from the body); up along the upper spine, so it leans with the
            posture: a stowed weapon or a backpack hangs flat against the back
    chest   forward straight out of the chest; up along the upper spine
    hips    forward the character's forward; up is up; at the belt on each side
    head    forward the character's forward; up is up; on top of the head
    mouth   forward the creature's forward; up is up; just behind the tip of the snout, at jaw height
    back (creatures)   forward the creature's forward; up is up; on the spine's midpoint, on the surface of the back

Spaces: compute() works in Blender world space (Z up), the space pack.py reads the rest pose in; to_json() writes
glTF model space (Y up, metres), the space of hitboxes.json and rig_info bone_segments_gltf. Bone-local values
(relative to the glTF joint node's rest frame, which is what engines see) are read back from the exported GLB by
attach_glb().
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np

try:  # backend: vgac.sockets
    from .blender import hitboxes as hb
except ImportError:  # inside Blender: pack.py puts vgac/blender on sys.path
    import hitboxes as hb  # type: ignore[no-redef]

VERSION = 1
SOCKET_CATEGORIES = ("humanoid", "quadruped", "multileg", "winged", "serpent")
SURFACE_OFFSET = 0.02     # metres outside the outermost surface (back, chest, hips)
SADDLE_OFFSET = 0.01      # metres above the creature's back
SADDLE_FRACTION = 0.45    # saddle point: this far from the front legs' tops to the back legs' tops
MOUTH_BACK = 0.12         # mouth: this far behind the snout's tip, as a share of the head's length
HAND_SPLIT = 0.55         # palm centre: this far from the wrist to the knuckles
BACK_HEIGHT = 0.5         # between the chest bone's head (0) and the neck's head (1): shoulder blades
CHEST_HEIGHT = 0.35       # the same scale: the sternum, where a badge goes

# name: (label, purpose) in the order they are written.
HUMANOID = {
    "Socket_Hand_R": ("Right hand", "a weapon or tool held in the right hand"),
    "Socket_Hand_L": ("Left hand", "a shield, torch or off-hand item"),
    "Socket_Back": ("Back", "a stowed weapon, quiver or backpack between the shoulder blades"),
    "Socket_Hip_L": ("Left hip", "a sheathed sword, pouch or holster on the left of the belt"),
    "Socket_Hip_R": ("Right hip", "a sheathed sword, pouch or holster on the right of the belt"),
    "Socket_Head": ("Head", "a hat, helmet or crown"),
    "Socket_Chest": ("Chest", "a badge, armour plate or chest light"),
}
CREATURE = {
    "Socket_Head": ("Head", "a hat, helmet, horns or a marker above the head"),
    "Socket_Back": ("Back", "a saddle, rider or pack over the spine"),
    "Socket_Mouth": ("Mouth", "something carried in the jaws, such as a stick or a bone"),
}
WHERE = {
    "Socket_Hand_R": "in the palm of the right hand",
    "Socket_Hand_L": "in the palm of the left hand",
    "Socket_Back": "between the shoulder blades, just outside the clothing",
    "Socket_Hip_L": "at the belt on the left side",
    "Socket_Hip_R": "at the belt on the right side",
    "Socket_Head": "on top of the head",
    "Socket_Chest": "on the front of the chest",
    "Socket_Mouth": "at the front of the mouth",
}
CREATURE_WHERE = {"Socket_Back": "on the middle of the back, over the spine"}

FINGER_ORDER = ("index", "middle", "ring", "pinky", "little")
FORWARD = hb.FORWARD  # Blender (0, -1, 0): glTF +Z, the model's front
UP = hb.UP


def catalogue(category: str | None) -> dict[str, tuple[str, str]]:
    """The socket names, labels and purposes a category gets ({} for unrigged categories)."""
    if category == "humanoid":
        return HUMANOID
    if category in SOCKET_CATEGORIES:
        return CREATURE
    return {}


def _unit(v):
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def _flat(v):
    """v with its vertical part removed, as a unit vector (None when vertical)."""
    v = np.asarray(v, float).copy()
    v[2] = 0.0
    return _unit(v)


def frame(forward, up, fallback_up=UP, keep_up=False):
    """Orthonormal (right, up, forward) with forward kept exactly and up made perpendicular to it (keep_up: up kept
    and forward made perpendicular). right = up x forward (glTF X = Y x Z)."""
    if keep_up and _unit(up) is not None:
        r, f, u = frame(up, forward, fallback_up)
        return np.cross(u, f), u, f
    f = _unit(forward)
    if f is None:
        f = FORWARD.copy()
    for cand in (up, fallback_up, UP, FORWARD, np.array([1.0, 0.0, 0.0])):
        if cand is None:
            continue
        u = np.asarray(cand, float) - f * float(np.dot(cand, f))
        u = _unit(u)
        if u is not None:
            break
    r = np.cross(u, f)
    return r, u, f


# --- ray casting (Moller-Trumbore, all triangles at once) --------------------------------------------------------


def ray_hits(origin, direction, V, tris) -> np.ndarray:
    """Distances t > 0 along the unit ray at which it crosses the triangles (either side). -> sorted array."""
    if tris is None or len(tris) == 0:
        return np.zeros(0)
    V = np.asarray(V, float)
    T = np.asarray(tris, int)
    o = np.asarray(origin, float)
    d = np.asarray(direction, float)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    e1, e2 = b - a, c - a
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = o - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
    return np.sort(t[hit])


def surface_point(origin, direction, V, tris, verts_mask=None, offset=SURFACE_OFFSET):
    """The point `offset` past the outermost surface crossed by the ray from `origin` (inside the body) along
    `direction`, using only triangles whose three vertices are in `verts_mask`. Without a hit, the farthest masked
    vertex along the direction within a narrow beam stands in. -> (point, distance to the surface, "ray" | "nearest" |
    "none")."""
    o = np.asarray(origin, float)
    d = _unit(direction)
    V = np.asarray(V, float)
    T = None if tris is None else np.asarray(tris, int)
    if T is not None and verts_mask is not None and len(T):
        m = np.asarray(verts_mask, bool)
        T = T[m[T].all(axis=1)]
    t = ray_hits(o, d, V, T)
    if len(t):
        far = float(t[-1])
        return o + d * (far + offset), far, "ray"
    P = V if verts_mask is None else V[np.asarray(verts_mask, bool)]
    if len(P):
        rel = P - o
        along = rel @ d
        side = np.linalg.norm(rel - np.outer(along, d), axis=1)
        beam = (along > 0) & (side < 0.05 + 0.15 * np.maximum(along, 0))
        if beam.any():
            far = float(along[beam].max())
            return o + d * (far + offset), far, "nearest"
    return o + d * offset, 0.0, "none"


# --- body parts ------------------------------------------------------------------------------------------------


def _owners(sk, V, owner):
    """Unweighted vertices belong to the nearest bone segment (as hitboxes.fit does). -> owner bone names."""
    names = [b["name"] for b in sk.bones]
    owner = np.asarray(owner, int).copy()
    if (owner < 0).any() and names:
        miss = np.nonzero(owner < 0)[0]
        H = np.array([sk.head(n) for n in names])
        Tl = np.array([sk.tail(n) for n in names])
        d = Tl - H
        dd = np.maximum((d * d).sum(axis=1), 1e-12)
        P = V[miss][:, None, :]
        s = np.clip(((P - H[None]) * d[None]).sum(axis=2) / dd[None], 0, 1)
        q = H[None] + s[..., None] * d[None]
        owner[miss] = np.argmin(np.linalg.norm(P - q, axis=2), axis=1)
    return np.array([names[i] if 0 <= i < len(names) else None for i in owner], object)


def hand_bone(sk, chain):
    """The hand in an arm chain (top first): the first bone with two or more children (the fingers branch); else the
    second from the end of a longer chain."""
    for b in chain:
        if len(sk.children.get(b) or []) >= 2:
            return b
    if not chain:
        return None
    return chain[-2] if len(chain) >= 3 else chain[-1]


def _finger_kind(name):
    n = hb.short_name(name).lower()
    if "thumb" in n:
        return "thumb"
    for i, k in enumerate(FINGER_ORDER):
        if k in n:
            return min(i, 3)
    return None


def hand_socket(sk, hand, V, vowner, character_forward):
    """Palm centre and grip frame of one hand (Blender space). -> (position, forward, up, method)."""
    w = sk.head(hand)
    kids = list(sk.children.get(hand) or [])
    thumb = next((k for k in kids if _finger_kind(k) == "thumb"), None)
    fingers = [k for k in kids if k != thumb]
    if thumb is None and len(kids) >= 3:
        # No names: the thumb's base is the child closest to the wrist.
        thumb = min(kids, key=lambda k: float(np.linalg.norm(sk.head(k) - w)))
        fingers = [k for k in kids if k != thumb]
    if fingers:
        knuckles = np.mean([sk.head(k) for k in fingers], axis=0)
    else:
        knuckles = sk.end(hand)
    if float(np.linalg.norm(knuckles - w)) < 1e-4:
        knuckles = w + (sk.tail(hand) - sk.head(hand))
    p = w + HAND_SPLIT * (knuckles - w)
    method = "bones"
    # The middle of the hand's own surface around that point (the palm's thickness, not the skin on one side).
    reach = 0.5 * float(np.linalg.norm(knuckles - w))
    own = np.nonzero(vowner == hand)[0]
    if len(own) and reach > 0:
        near = own[np.linalg.norm(V[own] - p, axis=1) < max(reach, 0.01)]
        if len(near) >= 8:
            p = 0.5 * p + 0.5 * V[near].mean(axis=0)
            method = "bones+surface"
    # Grip axis: across the knuckles from the little-finger side to the index side.
    fwd = None
    named = sorted([k for k in fingers if isinstance(_finger_kind(k), int)], key=_finger_kind)
    if len(named) >= 2:
        fwd = _unit(sk.head(named[0]) - sk.head(named[-1]))
    elif len(fingers) >= 2 and thumb is not None:
        near_thumb = min(fingers, key=lambda k: float(np.linalg.norm(sk.head(k) - sk.head(thumb))))
        far_thumb = max(fingers, key=lambda k: float(np.linalg.norm(sk.head(k) - sk.head(thumb))))
        fwd = _unit(sk.head(near_thumb) - sk.head(far_thumb))
    axis = _unit(knuckles - w)
    if fwd is None and axis is not None:
        # No finger spread to read: the character's forward, square to the hand.
        fwd = _unit(character_forward - axis * float(np.dot(character_forward, axis)))
    if fwd is None:
        fwd = character_forward
    up = (sk.head(thumb) - p) if thumb is not None else (-axis if axis is not None else UP)
    up = up - fwd * float(np.dot(up, fwd))
    if _unit(up) is None:
        up = -axis if axis is not None else UP
    return p, fwd, up, method


def _region_mask(vreg, regions):
    return np.isin(vreg, list(regions)) if regions else np.zeros(len(vreg), bool)


def head_top(V, mask, lateral, forward, tris=None):
    """Top of the head on its centre line, over the crown: a ray straight up from inside the head (on the centre
    line, at the crown's place front to back: the middle of the upper half of the head) to the outermost head
    surface. Ears and horns to the side are never crossed. Without a hit, the highest vertex near the centre line.
    -> (point, method) or (None, "")."""
    P = V[mask]
    if len(P) < 4:
        return None, ""
    lat = P @ lateral
    mid = 0.5 * (float(np.percentile(lat, 2)) + float(np.percentile(lat, 98)))
    width = max(float(np.percentile(lat, 98) - np.percentile(lat, 2)), 1e-3)
    upper = P[P[:, 2] >= float(np.median(P[:, 2]))]
    t = upper @ forward
    crown_t = 0.5 * (float(np.percentile(t, 5)) + float(np.percentile(t, 95)))
    c = P.mean(axis=0)
    o = c + lateral * (mid - float(c @ lateral)) + forward * (crown_t - float(c @ forward))
    o[2] = float(np.median(P[:, 2]))
    if tris is not None:
        p, _, how = surface_point(o, UP, V, tris, mask, offset=0.0)
        if how == "ray":
            return p, "ray"
    col = P[np.abs(lat - mid) < 0.15 * width]
    if len(col) == 0:
        col = P
    top = col[int(np.argmax(col[:, 2]))].copy()
    top += lateral * (mid - float(top @ lateral))
    return top, "surface"


def mouth_point(V, mask, forward, lateral):
    """Where the jaws close on something carried: on the centre line, a little behind the tip of the snout (MOUTH_BACK
    of the head's length) and low in the snout (the 30th percentile height of its front quarter). -> point or None."""
    P = V[mask]
    if len(P) < 8:
        return None
    t = P @ forward
    span = float(t.max() - t.min())
    if span <= 1e-4:
        return None
    front = P[t >= t.max() - 0.25 * span]
    p = P.mean(axis=0)
    p += forward * ((float(t.max()) - MOUTH_BACK * span) - float(p @ forward))
    p[2] = float(np.percentile(front[:, 2], 30))
    lat = P @ lateral
    mid = 0.5 * (float(np.percentile(lat, 2)) + float(np.percentile(lat, 98)))
    p += lateral * (mid - float(p @ lateral))
    return p


def _polyline_at(sk, chain, direction, target):
    """The point on the bone chain's polyline whose coordinate along `direction` is nearest `target`. -> (point,
    bone of that segment)."""
    pts = [sk.head(chain[0])] + [sk.end(b) for b in chain]
    best = (float("inf"), pts[0], chain[0])
    for i in range(len(pts) - 1):
        for k in np.linspace(0.0, 1.0, 21):
            p = pts[i] + k * (pts[i + 1] - pts[i])
            d = abs(float(p @ direction) - target)
            if d < best[0]:
                best = (d, p, chain[i])
    return best[1], best[2]


def _polyline_point(sk, chain, frac):
    pts = [sk.head(chain[0])] + [sk.end(b) for b in chain]
    seg = [float(np.linalg.norm(pts[i + 1] - pts[i])) for i in range(len(pts) - 1)]
    total = sum(seg)
    if total <= 1e-9:
        return pts[0], chain[0]
    goal, acc = frac * total, 0.0
    for i, ln in enumerate(seg):
        if acc + ln >= goal:
            k = (goal - acc) / ln if ln > 1e-12 else 0.0
            return pts[i] + k * (pts[i + 1] - pts[i]), chain[i]
        acc += ln
    return pts[-1], chain[-1]


# --- the whole placement ----------------------------------------------------------------------------------------


def _sock(name, table, bone, pos, fwd, up, method, where=None, keep_up=False):
    label, purpose = table[name]
    r, u, f = frame(fwd, up, keep_up=keep_up)
    return {"name": name, "label": label, "purpose": purpose, "where": where or WHERE.get(name, ""), "bone": bone,
            "position": np.asarray(pos, float), "forward": f, "up": u, "right": r, "method": method}


def compute(bones, V, owner, tris, category):
    """Place the sockets on a rigged body in its rest pose (Blender space). bones: [{name, parent, head, tail}]; V:
    (n, 3) vertices; owner: (n,) index into bones of each vertex's strongest bone (-1 unweighted); tris: (m, 3) or None.
    -> {category, skeleton, sockets: [...], skipped: [{name, reason}]}."""
    V = np.asarray(V, float)
    table = catalogue(category)
    out = {"category": category, "skeleton": "none", "sockets": [], "skipped": []}
    if not table or not bones or len(V) == 0:
        out["skipped"] = [{"name": n, "reason": "no skeleton"} for n in table]
        return out
    sk = hb.Skeleton(bones)
    lo, hi = V.min(axis=0), V.max(axis=0)
    vowner = _owners(sk, V, owner)
    regions, kind = hb.classify(sk, lo, hi, category, "detailed")
    out["skeleton"] = kind
    if not regions or "head" not in regions:
        return _fallback(sk, V, vowner, tris, category, table, out)
    b2r = hb.bone_regions(sk, regions)
    vreg = np.array([b2r.get(n) if n else None for n in vowner], object)
    if category == "humanoid":
        return _humanoid(sk, V, vowner, vreg, tris, regions, table, out)
    return _creature(sk, V, vowner, vreg, tris, regions, table, category, out)


def _humanoid(sk, V, vowner, vreg, tris, regions, table, out):
    fwd = FORWARD.copy()
    arms = {r for r in regions if r.startswith(("upper_arm", "forearm"))}
    torso_mask = ~_region_mask(vreg, arms)
    head_axis = regions["head"]["axis"]
    head = head_axis[-1]
    neck = head_axis[0]
    chest_r = regions.get("chest") or regions.get("hips")
    chest = chest_r["axis"][-1]
    hips = (regions.get("hips") or chest_r)["axis"][0]
    hips_head = sk.head(hips)
    thighs = {k: regions[k]["bones"][0] for k in ("thigh_l", "thigh_r") if k in regions}
    if len(thighs) == 2:
        lateral = _flat(sk.head(thighs["thigh_l"]) - sk.head(thighs["thigh_r"]))
    else:
        lateral = None
    if lateral is None:
        lateral = np.cross(UP, fwd)  # +X: the character's left when it faces glTF +Z
    socks = []
    for side, key in (("R", "forearm_r"), ("L", "forearm_l")):
        name = f"Socket_Hand_{side}"
        chain = (regions.get(key) or regions.get("upper_arm_" + side.lower()) or {}).get("bones") or []
        hand = hand_bone(sk, chain)
        if hand is None:
            out["skipped"].append({"name": name, "reason": "no hand bone found"})
            continue
        p, f, u, how = hand_socket(sk, hand, V, vowner, fwd)
        socks.append(_sock(name, table, hand, p, f, u, how))
    # Back and chest: from the spine at shoulder-blade (sternum) height, out through the torso's outermost surface.
    spine_up = _unit(sk.head(neck) - hips_head)
    if spine_up is None:
        spine_up = UP
    base, top = sk.head(chest), sk.head(neck)
    o_back = base + BACK_HEIGHT * (top - base)
    back_dir = -fwd
    p, dist, how = surface_point(o_back, back_dir, V, tris, torso_mask)
    socks.append(_sock("Socket_Back", table, chest, p, back_dir, spine_up, how, keep_up=True))
    # Hips: at the hip joint's height, out to each side of the belt.
    for side, sign in (("L", 1.0), ("R", -1.0)):
        p, dist, how = surface_point(hips_head, sign * lateral, V, tris, torso_mask)
        socks.append(_sock(f"Socket_Hip_{side}", table, hips, p, fwd, UP, how))
    # Head: on top of the skull.
    head_mask = vreg == "head"
    tp, how = head_top(V, head_mask, lateral, fwd, tris)
    if tp is None:
        tp, how = sk.end(head), "bones"
    socks.append(_sock("Socket_Head", table, head, tp, fwd, UP, how))
    o_chest = base + CHEST_HEIGHT * (top - base)
    p, dist, how = surface_point(o_chest, fwd, V, tris, torso_mask)
    socks.append(_sock("Socket_Chest", table, chest, p, fwd, spine_up, how, keep_up=True))
    order = list(table)
    out["sockets"] = sorted(socks, key=lambda s: order.index(s["name"]))
    return out


def _creature(sk, V, vowner, vreg, tris, regions, table, category, out):
    head_mask = vreg == "head"
    centre = V.mean(axis=0)
    fwd = _flat(V[head_mask].mean(axis=0) - centre) if head_mask.any() else None
    if fwd is None:
        fwd = FORWARD.copy()
    lateral = np.cross(UP, fwd)
    lateral = lateral / (np.linalg.norm(lateral) or 1.0)
    socks = []
    head_axis = regions["head"]["axis"] or regions["head"]["bones"][:1]
    head = head_axis[-1]
    # Head and mouth.
    tp, how = head_top(V, head_mask, lateral, fwd, tris)
    if tp is None:
        tp, how = sk.end(head), "bones"
    socks.append(_sock("Socket_Head", table, head, tp, fwd, UP, how))
    jaw = next((b for b in regions["head"]["bones"] if "jaw" in hb.short_name(b).lower()), None)
    mp = mouth_point(V, head_mask, fwd, lateral)
    if mp is not None:
        socks.append(_sock("Socket_Mouth", table, jaw or head, mp, fwd, UP, "surface"))
    else:
        out["skipped"].append({"name": "Socket_Mouth", "reason": "head too small to find the mouth"})
    # Back: on the spine a little behind the front legs (SADDLE_FRACTION of the way to the back legs; the spine's
    # midpoint without four legs), out through the top of the back.
    if "body" in regions:
        chain = regions["body"]["axis"] or regions["body"]["bones"]
        torso = ["body"]
    else:
        spine = sorted([r for r in regions if r.startswith("spine_")], key=lambda r: int(r.split("_")[1]))
        mid = spine[len(spine) // 2] if spine else None
        chain = (regions[mid]["axis"][::-1] if mid else []) or (regions[mid]["bones"] if mid else [])
        torso = spine or ["head"]
    if chain:
        o, bone = _polyline_point(sk, chain, 0.5)
        front = [sk.head(regions[r]["bones"][0]) for r in ("leg_fl", "leg_fr") if r in regions]
        rear = [sk.head(regions[r]["bones"][0]) for r in ("leg_bl", "leg_br") if r in regions]
        if front and rear:
            tf, tb = float(np.mean([p @ fwd for p in front])), float(np.mean([p @ fwd for p in rear]))
            o, bone = _polyline_at(sk, chain, fwd, tf + SADDLE_FRACTION * (tb - tf))
        mask = _region_mask(vreg, set(torso) | {"neck"})
        p, dist, how = surface_point(o, UP, V, tris, mask, offset=SADDLE_OFFSET)
        socks.append(_sock("Socket_Back", table, bone, p, fwd, UP, how, CREATURE_WHERE["Socket_Back"]))
    else:
        out["skipped"].append({"name": "Socket_Back", "reason": "no spine found"})
    order = list(table)
    out["sockets"] = sorted(socks, key=lambda s: order.index(s["name"]))
    return out


def _fallback(sk, V, vowner, tris, category, table, out):
    """Body parts not found: only a back socket over the root bone (top of the body), the rest skipped."""
    root = max(sk.roots, key=lambda n: len(sk.subtree(n))) if sk.roots else None
    if root is None:
        out["skipped"] = [{"name": n, "reason": "no skeleton"} for n in table]
        return out
    o = sk.head(root).copy()
    if category == "humanoid":
        p, dist, how = surface_point(o, -FORWARD, V, tris, None)
        fwd, up = -FORWARD, UP
    else:
        p, dist, how = surface_point(o, UP, V, tris, None, offset=SADDLE_OFFSET)
        fwd, up = FORWARD, UP
    out["sockets"] = [_sock("Socket_Back", table, root, p, fwd, up, "fallback:" + how,
                            CREATURE_WHERE["Socket_Back"] if category != "humanoid" else None)]
    out["skipped"] = [{"name": n, "reason": "body parts not found"} for n in table if n != "Socket_Back"]
    out["fallback"] = True
    return out


# --- sockets.json -----------------------------------------------------------------------------------------------


def quat_from_frame(r, u, f):
    """(x, y, z, w) of the rotation whose columns are right, up, forward (a proper rotation)."""
    m = np.column_stack([r, u, f])
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    q = np.array([x, y, z, w])
    q = q / (np.linalg.norm(q) or 1.0)
    return q if q[3] >= 0 else -q


def quat_to_matrix(q):
    x, y, z, w = [float(a) for a in q]
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _r(v, n=5):
    return [round(float(x), n) for x in v]


def sentence(s: dict[str, Any]) -> str:
    """One plain sentence for the manifest: 'Right hand (Socket_Hand_R): in the palm of the right hand, for a weapon
    or tool held in the right hand. Follows mixamorig:RightHand.'"""
    where = s.get("where") or WHERE.get(s.get("name", ""), "")
    return f"{s.get('label')} ({s.get('name')}): {where}, for {s.get('purpose')}. Follows {s.get('bone')}."


def to_json(result, bones=None):
    """The sockets.json document (glTF model space, Y up, metres). Every key of every socket is always present
    (numbers, never null) so Unity's JsonUtility reads it; `local_*` start as the offset from the bone's head with
    the model's axes and are replaced by the GLB's real joint-local values in attach_glb()."""
    by = {b["name"]: b for b in bones or []}
    items = []
    for s in result["sockets"]:
        pos = np.asarray(hb.to_gltf(s["position"]))
        f, u, r = (np.asarray(hb.to_gltf(s[k])) for k in ("forward", "up", "right"))
        q = quat_from_frame(r, u, f)
        b = by.get(s["bone"])
        bh = np.asarray(hb.to_gltf(b["head"])) if b else np.zeros(3)
        items.append({"name": s["name"], "label": s["label"], "purpose": s["purpose"], "where": s.get("where") or "",
                      "bone": s["bone"] or "", "position": _r(pos), "rotation": _r(q, 6), "forward": _r(f), "up": _r(u),
                      "right": _r(r), "bone_head": _r(bh), "offset": _r(pos - bh),
                      "local_position": _r(pos - bh), "local_rotation": _r(q, 6), "local_space": "model_axes",
                      "method": s.get("method", ""), "sentence": sentence(s)})
    return {
        "version": VERSION,
        "space": "gltf",
        "units": "m",
        "axes": "forward +Z, up +Y, right +X (X = Y x Z); rotation is a quaternion x, y, z, w",
        "category": result.get("category"),
        "skeleton": result.get("skeleton"),
        "fallback": bool(result.get("fallback", False)),
        "count": len(items),
        "names": [i["name"] for i in items],
        "sockets": items,
        "skipped": list(result.get("skipped") or []),
        "glb_checked": False,
        "glb_max_error": 0.0,
    }


# --- reading the exported GLB back ------------------------------------------------------------------------------


def read_glb_json(path: Path | str) -> dict[str, Any]:
    """The JSON chunk of a .glb file."""
    data = Path(path).read_bytes()
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError(f"not a GLB file: {path}")
    clen, ctype = struct.unpack_from("<II", data, 12)
    if ctype != 0x4E4F534A:
        raise ValueError("first GLB chunk is not JSON")
    return json.loads(data[20:20 + clen].decode("utf-8"))


def _node_matrix(n):
    if "matrix" in n:
        return np.array(n["matrix"], float).reshape(4, 4).T  # glTF matrices are column-major
    m = np.eye(4)
    s = np.array(n.get("scale", [1, 1, 1]), float)
    m[:3, :3] = quat_to_matrix(n.get("rotation", [0, 0, 0, 1])) * s[None, :]
    m[:3, 3] = n.get("translation", [0, 0, 0])
    return m


def glb_nodes(gltf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{node name: {local, world, parent}} for every named node, worlds from the default (rest) transforms."""
    nodes = gltf.get("nodes") or []
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children") or []:
            parent[c] = i
    world: dict[int, np.ndarray] = {}

    def w(i):
        if i not in world:
            m = _node_matrix(nodes[i])
            world[i] = (w(parent[i]) @ m) if i in parent else m
        return world[i]

    out = {}
    for i, n in enumerate(nodes):
        name = n.get("name")
        if name is None:
            continue
        out[name] = {"index": i, "local": _node_matrix(n), "world": w(i),
                     "parent": nodes[parent[i]].get("name") if i in parent else None}
    return out


def _frame_quat(m3):
    m = np.asarray(m3, float)
    scale = np.linalg.norm(m, axis=0)
    m = m / np.where(scale > 1e-12, scale, 1.0)
    return quat_from_frame(m[:, 0], m[:, 1], m[:, 2])


def attach_glb(doc: dict[str, Any], glb: Path | str) -> dict[str, Any]:
    """Fill each socket's local_position and local_rotation from the GLB's socket node (relative to its parent joint,
    which is what an engine sees) and check the node sits where the file says. Sets glb_checked, glb_max_error
    (metres) and glb_angle_error (degrees, worst forward or up mismatch), and per socket glb_parent. -> doc."""
    nodes = glb_nodes(read_glb_json(glb))
    worst, worst_angle, missing = 0.0, 0.0, []
    for s in doc.get("sockets") or []:
        n = nodes.get(s["name"])
        if n is None:
            missing.append(s["name"])
            continue
        loc = n["local"]
        s["local_position"] = _r(loc[:3, 3])
        s["local_rotation"] = _r(_frame_quat(loc[:3, :3]), 6)
        s["local_space"] = "joint"
        s["glb_parent"] = n["parent"] or ""
        wm = n["world"]
        worst = max(worst, float(np.linalg.norm(wm[:3, 3] - np.asarray(s["position"], float))))
        for col, key in ((2, "forward"), (1, "up")):
            a = _unit(wm[:3, col])
            b = _unit(s[key])
            if a is not None and b is not None:
                worst_angle = max(worst_angle, math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b)))))))
    doc["glb_checked"] = True
    doc["glb_max_error"] = round(worst, 5)
    doc["glb_angle_error"] = round(worst_angle, 3)
    doc["glb_missing"] = missing
    return doc


def manifest_block(doc: dict[str, Any] | None, unity_report: dict[str, Any] | None = None, file_name: str = "sockets.json") -> dict[str, Any] | None:
    """The manifest's `sockets` block: the file, the names, and one plain sentence per socket."""
    if not doc or not doc.get("sockets"):
        return None
    u = unity_report or {}
    return {"file": file_name, "count": len(doc["sockets"]), "names": [s["name"] for s in doc["sockets"]],
            "sentences": [s.get("sentence") or sentence(s) for s in doc["sockets"]],
            "space": doc.get("space", "gltf"), "unity": u.get("sockets") if u else None,
            "note": "Empty transforms parented to the bones in the FBX and every GLB; parent an item to one and it "
                    "follows the animation."}


def metrics(doc: dict[str, Any] | None) -> dict[str, Any]:
    """metrics.sockets: {count, names}."""
    socks = (doc or {}).get("sockets") or []
    return {"count": len(socks), "names": [s["name"] for s in socks]}
