"""Blender headless: spring bones (docs/HAIR-RESEARCH.md 3.2 (f)): parts that hang free sway when the character moves.

Run: blender -b --python springs.py -- <anim.glb> <out.glb> --report <springs_report.json> --doc <springs.json>
     [--clips <anim_clips.json>] [--hitboxes <hitboxes.json>] [--hair-points <hair_points.json>]
     [--metallic <metallic.png>] [--pictures <dir>] [--fps 24] [--off]

What it does, in order:
1. Finds the regions that hang free. The model is cut into thin horizontal slices (a voxel grid about 0.5 percent of
   the height); in each slice the surface is filled to a solid and split into separate pieces. A piece that holds a
   bone of the body (hips, spine, head, arms, legs) is the body; every other piece belongs to a part that is clear of
   the body at that height. Pieces are joined across slices into parts. A part hangs free when it is joined to the
   body only at its upper end, is long enough to swing, thin for its length, and clear of the body by a few
   centimetres over most of its length. What it is comes from where it is joined (the bone that holds the surface
   there) and from the hair pass's hair triangles: hair from the head is a ponytail or a braid, a wide sheet from the
   back or the hips is a cape or a hem, a narrow tapering part centred behind the hips is a tail. Everything else is
   left alone with a plain reason: parts that point up (a quiver's arrows, a sword hilt over the shoulder), parts
   joined at both ends (a strap), parts hanging from a hand or a limb (a held sword), narrow parts at the side of the
   hips (a scabbard, a pouch), metal, parts that rest against the body, short or thick parts.
2. Adds a chain of 2 to 4 bones along each part (two or three chains side by side across a wide cape), parented to
   the body bone that holds the surface where the part is joined (Head or Neck, the top Spine bone, Hips), named
   Spring_Hair_01, Spring_Cape_01 and so on. Weights fade from the original weights at the root (so the body surface
   there cannot stretch) to the chain toward the tip; at most 4 influences, normalised.
3. Bakes the sway into every clip: a small spring simulation (the same one the Unity script runs) is stepped at
   120 Hz over each clip, with the hit boxes as shapes the chain is pushed out of, and keyed onto the chain bones.
   Looping clips are run three times and the last pass is kept, with the small remaining difference between its
   end and its start spread over the clip, so the first and last frames match. Walk and run clips are simulated
   as if the character travelled at the clip's speed through still air, so the part trails behind as in the game.
4. Writes springs.json (glTF model space, Y up, metres): the chains, their settings and the hit boxes as colliders.

Hair from the library (--hair-object VGAC_Hair --hair-mesh hair_mesh.npz [--hair-parts hair_parts.json]): the hair
object bound by hairbind.py is left out of step 1; each of its hanging groups (role 2+k in the npz, found on the object
by matching positions, the path from hair_parts.json) gets a chain of 2 to 4 bones along its path, parented to the Head
bone, before the body's own parts and within the same cap of 12 bones. When nothing sways and the hair is from the
library, the reason is library_rests. Without these arguments nothing changes.

Everything above main() is plain numpy so the backend tests can run it without Blender; only main() touches bpy.
"""
import json
import math
import os
import sys

import numpy as np

# --- settings --------------------------------------------------------------------------------------------------------

CELL_PER_HEIGHT = 1.0 / 220.0  # voxel size: about 8 mm on a 1.75 m character
CELL_MIN, CELL_MAX = 0.004, 0.02
MIN_LENGTH = 0.08  # metres; and at least MIN_LENGTH_REL of the height
MIN_LENGTH_REL = 0.05
CLEAR_MIN = 0.015  # metres of air between the part and the body over most of its length
CLEAR_SHARE = 0.6  # share of the part's slices (lower part) that must be that clear
THIN_RATIO = 2.0  # length over the part's thickness
WIDE = 0.12  # metres: a part at least this wide across is a sheet (cape, hem)
TAIL_CENTRE = 0.06  # a tail hangs within this share of the height from the middle line
TAPER_MAX = 0.75  # a tail's tip cross-section over its root cross-section
METAL = 0.5
CONFIDENCE_MIN = 0.5
MAX_BONES = 12
CHAIN_FULL = 0.4  # the chain holds the surface fully from this share of its length on
PREFIX = "Spring_"
KINDS = ("Hair", "Cape", "Tail", "Cloth")

# Settings per kind: stiffness as the natural frequency in Hz, damping as a ratio of critical damping, drag as air
# drag in 1/s (what makes a part trail when the character moves), gravity in m/s^2 relative to the rest pose (the
# part keeps its built shape while the character stands still and upright). The Unity script reads the same values.
SETTINGS = {
    "Hair": {"stiffness": 1.8, "damping": 0.35, "drag": 1.6, "gravity": 9.81},
    "Cape": {"stiffness": 1.2, "damping": 0.4, "drag": 2.4, "gravity": 9.81},
    "Tail": {"stiffness": 1.5, "damping": 0.3, "drag": 1.2, "gravity": 9.81},
    "Cloth": {"stiffness": 1.6, "damping": 0.4, "drag": 1.8, "gravity": 9.81},
}
# Hair from the library hangs: its chains' rest direction is pulled toward world down by this share (0 keeps the built
# shape relative to the parent, as every other chain does; 1 points straight down), so a ponytail droops when the head
# bows instead of standing out like a handle. Written to springs.json as "hang" for those chains only.
LIBRARY_HAIR_HANG = 0.7
SUBSTEP = 1.0 / 120.0
COLLIDE_ITER = 3  # rounds of "push out of the hit boxes, keep the length" per joint and step
LOOP_PASSES = 3
SETTLE_SECONDS = 1.0
# Clip speeds through the air in m/s for a 1.8 m person (the Unity locomotion blend tree's walk and run, export.py).
TRAVEL = {"walk": 1.4, "run": 3.5, "jog": 3.5, "sprint": 5.5, "crouch_walk": 0.8, "strafe": 1.4}

HEAD_ROLES = ("Head", "Neck", "Neck1", "HeadTop_End")
CHEST_ROLES = ("Spine1", "Spine2", "Spine3", "LeftShoulder", "RightShoulder")
HIP_ROLES = ("Hips", "Spine", "LeftUpLeg", "RightUpLeg")

REASONS = {
    "off": "Spring bones are turned off.",
    "category": "Spring bones are made for people-shaped characters only so far.",
    "tail_moves": "The tail already moves with the built-in moves, so no spring bones were added to it.",
    "no_rig": "There is no skeleton to add spring bones to.",
    "no_clips": "There are no moves to bake the sway into.",
    "none_found": "Nothing hangs clear of the body, so nothing can sway.",
    "points_up": "It points up instead of hanging, like a quiver's arrows or a sword hilt; rigid things do not sway.",
    "both_ends": "It is joined to the body at both ends, like a strap.",
    "limb": "It hangs from a hand or a limb, like a held sword or a strap; rigid things do not sway.",
    "side": "It hangs at the side of the hips like a scabbard or a pouch; rigid things do not sway.",
    "narrow_back": "It is narrow and hangs from the back like a quiver or a strap; rigid things do not sway.",
    "loose": "It is not joined to the body.",
    "short": "It is too short to swing.",
    "thick": "It is too thick for its length to swing.",
    "rests": "It rests against the body (less than 1.5 cm of air), so it is joined to it.",
    "metal": "It looks like metal, so it was taken as a rigid thing.",
    "not_tapered": "It is as thick at the end as at the top, like a sheath; a tail tapers.",
    "low_confidence": "It was not clear enough that it hangs free.",
}


def role_of(name):
    """The humanoid role in a bone name: "mixamorig:Head" -> "Head"; Extra and spring bones have none."""
    n = name.split(":")[-1].split("|")[-1]
    if n.startswith("Extra_") or n.startswith(PREFIX):
        return None
    return n


# --- voxel grid ------------------------------------------------------------------------------------------------------


def make_grid(V, cell):
    lo = V.min(axis=0) - 3 * cell
    hi = V.max(axis=0) + 3 * cell
    shape = tuple(int(math.ceil((hi[i] - lo[i]) / cell)) + 1 for i in (2, 1, 0))  # (z, y, x)
    return {"lo": lo, "cell": float(cell), "shape": shape}


def to_cells(grid, P):
    """Points (n, 3) in x, y, z -> integer (n, 3) cell indices in z, y, x order, clipped to the grid."""
    q = np.floor((np.asarray(P, float) - grid["lo"]) / grid["cell"]).astype(np.int64)[:, ::-1]
    return np.clip(q, 0, np.array(grid["shape"]) - 1)


def cell_centres(grid, idx):
    """(n, 3) z, y, x cell indices -> (n, 3) x, y, z centres."""
    return grid["lo"] + (np.asarray(idx, float)[:, ::-1] + 0.5) * grid["cell"]


def surface_cells(V, F, grid):
    """Cells the surface passes through (points sampled over every triangle at under half a cell apart)."""
    occ = np.zeros(grid["shape"], dtype=bool)
    tri = V[F]
    edge = np.max(np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2), axis=1)
    steps = np.clip(np.ceil(edge / (0.45 * grid["cell"])).astype(int), 1, 600)
    for n in np.unique(steps):
        sel = tri[steps == n]
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        keep = (i + j) <= n
        a, b = i[keep] / n, j[keep] / n
        bary = np.stack([a, b, 1.0 - a - b], axis=1)  # (k, 3)
        pts = np.einsum("kj,mjd->mkd", bary, sel).reshape(-1, 3)
        q = to_cells(grid, pts)
        occ[q[:, 0], q[:, 1], q[:, 2]] = True
    return occ


def _run_spread(vals, mask, axis, op):
    """Within every unbroken run of `mask` along `axis`, set every cell to op (max for bool reach, min for labels) of the
    run. Cells outside the mask keep their value."""
    v = np.moveaxis(vals, axis, -1)
    m = np.moveaxis(mask, axis, -1)
    n = v.shape[-1]
    seg = np.cumsum(~m, axis=-1)
    rows = np.arange(v.size // n, dtype=np.int64).reshape(v.shape[:-1])[..., None]
    key = (rows * (n + 1) + seg).ravel()
    mk = m.ravel()
    flat = v.ravel()
    if op == "max":
        acc = np.zeros(int(key.max()) + 1, dtype=bool)
        acc[key[mk & flat]] = True
        out = np.where(mk, acc[key], flat)
    else:
        big = np.iinfo(np.int64).max
        acc = np.full(int(key.max()) + 1, big, dtype=np.int64)
        np.minimum.at(acc, key[mk], flat[mk])
        out = np.where(mk, acc[key], flat)
    return np.moveaxis(out.reshape(v.shape), -1, axis)


def fill_slices(occ):
    """Each horizontal slice filled to a solid: cells the outside cannot reach without crossing the surface."""
    empty = ~occ
    reach = np.zeros_like(occ)
    reach[:, 0, :] = empty[:, 0, :]
    reach[:, -1, :] = empty[:, -1, :]
    reach[:, :, 0] = empty[:, :, 0]
    reach[:, :, -1] = empty[:, :, -1]
    for _ in range(200):
        before = int(reach.sum())
        for ax in (1, 2):
            reach = _run_spread(reach, empty, ax, "max")
        if int(reach.sum()) == before:
            break
    return ~reach


def label(mask, axes):
    """Connected components of `mask` along the given axes (face neighbours). -> int64 labels, -1 outside."""
    lab = np.where(mask, np.arange(mask.size, dtype=np.int64).reshape(mask.shape), -1)
    big = np.iinfo(np.int64).max
    work = np.where(mask, lab, big)
    for _ in range(500):
        before = work.copy()
        for ax in axes:
            work = _run_spread(work, mask, ax, "min")
        if np.array_equal(before, work):
            break
    return np.where(mask, work, -1)


def segment_points(heads, tails, step):
    out = []
    for h, t in zip(heads, tails):
        h, t = np.asarray(h, float), np.asarray(t, float)
        n = max(2, int(np.linalg.norm(t - h) / step) + 2)
        out.append(h + np.linspace(0, 1, n)[:, None] * (t - h))
    return np.concatenate(out) if out else np.zeros((0, 3))


# --- finding the hanging parts ---------------------------------------------------------------------------------------


def find_parts(V, F, bones, weights=None, hair_v=None, metal_v=None, category="humanoid", max_bones=MAX_BONES, counter=None):
    """The parts that hang free. V (n, 3) vertices and bones in Blender world space (Z up, the character's front -Y,
    its left +X); F (m, 3) triangles; bones: [{name, head, tail, parent}]; weights: (WI (n, 4) bone indices, WW (n, 4))
    or None; hair_v (n,) bool: vertex on a hair triangle; metal_v (n,) metallic 0..1. -> {"parts": accepted,
    "rejected": [...], "height", "cell", "seconds"}. Each part: kind, parent, confidence, attach, centreline, columns,
    length, width, thickness, gap_cm, hair_share, cells (for the weights), reason None."""
    import time

    t0 = time.time()
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    height = float(V[:, 2].max() - V[:, 2].min())
    cell = float(np.clip(height * CELL_PER_HEIGHT, CELL_MIN, CELL_MAX))
    grid = make_grid(V, cell)
    occ = surface_cells(V, F, grid)
    solid = fill_slices(occ) | occ
    lay = label(solid, (1, 2))
    names = [b["name"] for b in bones]
    roles = [role_of(n) for n in names]
    body_bones = [i for i, r in enumerate(roles) if r is not None]
    if not body_bones:
        body_bones = list(range(len(bones)))
    # the skeleton as joint to joint (a bone's head to each child's head; a bone's own tail only for an end bone): a
    # rigger's bone tails can point anywhere (thigh tails out to the side), the joints sit inside the body
    heads, tails = [], []
    by_name = {b["name"]: i for i, b in enumerate(bones)}
    kids = {i: [] for i in range(len(bones))}
    for i, b in enumerate(bones):
        if b.get("parent") in by_name:
            kids[by_name[b["parent"]]].append(i)
    body_set = set(body_bones)
    for i in body_bones:
        ch = [k for k in kids[i] if k in body_set]
        for k in ch:
            heads.append(bones[i]["head"])
            tails.append(bones[k]["head"])
        if not ch:
            heads.append(bones[i]["head"])
            tails.append(bones[i]["tail"])
    pts = segment_points(heads, tails, 0.5 * cell)
    q = to_cells(grid, pts)
    hit = lay[q[:, 0], q[:, 1], q[:, 2]]
    body_labels = np.unique(hit[hit >= 0])
    body = np.isin(lay, body_labels) & solid
    rest = solid & ~body
    parts_lab = label(rest, (0, 1, 2))
    ids, counts = np.unique(parts_lab[parts_lab >= 0], return_counts=True)
    vcell = to_cells(grid, V)
    vlab = parts_lab[vcell[:, 0], vcell[:, 1], vcell[:, 2]]
    centre_x = float(np.median(V[:, 0]))
    hips = next((bones[i] for i, r in enumerate(roles) if r == "Hips"), None)
    if hips is not None:
        centre_x = float(hips["head"][0])
    centre_y = float(np.median(V[:, 1]))
    min_len = max(MIN_LENGTH, MIN_LENGTH_REL * height)
    accepted, rejected = [], []
    for pid, n in zip(ids, counts):
        if n * cell ** 3 < 2e-6:  # specks under 2 cubic centimetres
            continue
        info = describe_part(pid, parts_lab, body, grid, V, vlab, bones, roles, weights, hair_v, metal_v,
                             centre_x, centre_y, height, min_len)
        if info is None:
            continue
        (accepted if info["reason"] is None else rejected).append(info)
    accepted.sort(key=lambda p: -p["length"])
    # every chain gets its name; the bone cap keeps the longest parts
    total = 0
    kept = []
    for p in accepted:
        nb = sum(len(c) - 1 for c in p["columns"])
        if total + nb > max_bones:
            p["reason"] = "low_confidence"
            p["why"] = "There were already as many spring bones as a character gets."
            rejected.append(p)
            continue
        total += nb
        kept.append(p)
    counter = dict(counter or {})
    for p in kept:
        for c in range(len(p["columns"])):
            counter[p["kind"]] = counter.get(p["kind"], 0) + 1
            p.setdefault("names", []).append(f"{PREFIX}{p['kind']}_{counter[p['kind']]:02d}")
    return {"parts": kept, "rejected": [r for r in rejected if r.get("notable")], "height": height, "cell": cell,
            "grid": grid, "seconds": round(time.time() - t0, 2), "body_cells": int(body.sum()), "part_count": int(len(ids))}


def _dominant_bone(V, weights, sel, nbones):
    if weights is None or not np.any(sel):
        return None, {}
    WI, WW = weights
    acc = np.zeros(nbones)
    wi, ww = WI[sel].ravel(), WW[sel].ravel()
    ok = wi >= 0
    np.add.at(acc, wi[ok], ww[ok])
    if acc.sum() <= 0:
        return None, {}
    share = acc / acc.sum()
    return int(np.argmax(acc)), {int(i): float(share[i]) for i in np.argsort(-share)[:4] if share[i] > 0}


def describe_part(pid, parts_lab, body, grid, V, vlab, bones, roles, weights, hair_v, metal_v, centre_x, centre_y,
                  height, min_len):
    cell = grid["cell"]
    idx = np.argwhere(parts_lab == pid)  # (k, 3) z, y, x
    z0, z1 = int(idx[:, 0].min()), int(idx[:, 0].max())
    Z = body.shape[0]
    above = np.zeros(len(idx), bool)
    below = np.zeros(len(idx), bool)
    up = idx[:, 0] + 1 < Z
    above[up] = body[idx[up, 0] + 1, idx[up, 1], idx[up, 2]]
    dn = idx[:, 0] - 1 >= 0
    below[dn] = body[idx[dn, 0] - 1, idx[dn, 1], idx[dn, 2]]
    span = max(1, z1 - z0)
    rel = (idx[:, 0] - z0) / span  # 0 at the bottom, 1 at the top
    xyz = cell_centres(grid, idx)
    length_z = (z1 - z0 + 1) * cell
    out = {"id": int(pid), "cells_n": int(len(idx)), "top": float(xyz[:, 2].max()), "bottom": float(xyz[:, 2].min()),
           "length": float(length_z), "reason": None, "notable": length_z >= min_len}
    out["centre"] = [round(float(v), 4) for v in xyz.mean(axis=0)]
    n_above, n_below = int(above.sum()), int(below.sum())
    if n_above + n_below == 0:
        out["reason"] = "loose"
        return out
    top_share = float(np.mean(rel[above] >= 0.6)) if n_above else 0.0
    bottom_share = float(np.mean(rel[below] <= 0.4)) if n_below else 0.0
    hang = n_above > 0 and top_share >= 0.9 and n_below <= max(2, 0.1 * n_above)
    points_up = n_below > 0 and bottom_share >= 0.9 and n_above <= max(2, 0.1 * n_below)
    if not hang:
        out["reason"] = "points_up" if points_up else "both_ends"
        if points_up:
            # a cap on the body (the top of the head above its last bone, a shoulder pad) sits on a wide footprint
            biggest = max(int(np.sum(idx[:, 0] == z)) for z in range(z0, z1 + 1))
            out["notable"] = out["notable"] and n_below < 0.4 * biggest
        return out
    # the centre line, top to bottom, from the slice centroids
    layers = list(range(z1, z0 - 1, -1))
    cents, areas, widths = [], [], []
    for z in layers:
        c = xyz[idx[:, 0] == z]
        cents.append(c.mean(axis=0))
        areas.append(len(c) * cell * cell)
        if len(c) >= 3:
            cov = np.cov((c[:, :2] - c[:, :2].mean(axis=0)).T)
            lam = np.linalg.eigvalsh(cov)
            widths.append(max(cell, 4.0 * math.sqrt(max(lam[-1], 0.0)) + cell))
        else:
            widths.append(cell * max(1, len(c)))
    cents = np.array(cents)
    areas, widths = np.array(areas), np.array(widths)
    thick = areas / widths
    attach = cell_centres(grid, idx[above])
    root = attach.mean(axis=0)
    root[2] = float(xyz[:, 2].max()) + 0.5 * cell
    line = np.vstack([root, cents])
    seglen = np.linalg.norm(np.diff(line, axis=0), axis=1)
    length = float(seglen.sum()) + 0.5 * cell
    out["length"] = length
    out["notable"] = length >= min_len
    out["width"] = float(np.percentile(widths, 75))
    out["thickness"] = float(np.percentile(thick, 75))
    # air between the part and the body, slice by slice, over the lower part
    gaps = []
    for z in layers:
        pz = idx[idx[:, 0] == z][:, 1:]
        bz = np.argwhere(body[z])
        if len(bz) == 0:
            gaps.append(1.0)
            continue
        lo_, hi_ = pz.min(axis=0) - 20, pz.max(axis=0) + 20
        near = bz[np.all((bz >= lo_) & (bz <= hi_), axis=1)]
        if len(near) == 0:
            gaps.append(0.2)
            continue
        if len(near) > 4000:
            near = near[np.linspace(0, len(near) - 1, 4000).astype(int)]
        d = np.sqrt(((pz[:, None, :] - near[None, :, :]) ** 2).sum(axis=2)).min()
        gaps.append(max(0.0, (float(d) - 1.0) * cell))
    gaps = np.array(gaps)
    lower = gaps[int(len(gaps) * 0.3):] if len(gaps) >= 4 else gaps
    out["gap_cm"] = round(float(np.median(lower)) * 100, 1)
    clear_share = float(np.mean(lower >= CLEAR_MIN))
    out["clear_share"] = round(clear_share, 3)
    # the mesh vertices in the part, and what holds the surface where it is joined
    vin = vlab == pid
    out["verts"] = int(vin.sum())
    nb = len(bones)
    near_root = (np.abs(V[:, 2] - root[2]) <= max(3 * cell, 0.02)) & \
                (np.linalg.norm(V[:, :2] - root[:2], axis=1) <= max(0.5 * out["width"], 3 * cell) + 2 * cell)
    _, shares = _dominant_bone(V, weights, near_root, nb)
    out["parent_weights"] = {bones[i]["name"]: round(s, 3) for i, s in shares.items()}
    # where it is joined: the body bone nearest to the contact (the skin weights there are reported, but a handful of
    # vertices at a seam are a weaker witness than the skeleton itself)
    contact = attach.mean(axis=0)
    cand = [i for i, r in enumerate(roles) if r is not None] or list(range(nb))
    dists = [float(np.linalg.norm(contact - _closest_on_segment(contact, np.asarray(bones[i]["head"], float),
                                                                np.asarray(bones[i]["tail"], float)))) for i in cand]
    dom = cand[int(np.argmin(dists))]
    parent_role = roles[dom]
    body_roles = HEAD_ROLES + CHEST_ROLES + HIP_ROLES
    if parent_role not in body_roles and shares:
        # the skeleton says a limb, but the surface there clearly follows the body (a cape corner beside the arm)
        wi, ws = max(shares.items(), key=lambda t: t[1])
        if ws >= 0.6 and roles[wi] in body_roles:
            dom, parent_role = wi, roles[wi]
    # the lowest spine bones belong with the hips only in the lower third of the torso
    hips_z = next((float(b["head"][2]) for b, r in zip(bones, roles) if r == "Hips"), None)
    neck_z = next((float(b["head"][2]) for b, r in zip(bones, roles) if r in ("Neck", "Head")), None)
    if parent_role in ("Hips", "Spine") and hips_z is not None and neck_z is not None and contact[2] > hips_z + 0.3 * (neck_z - hips_z):
        parent_role = "Spine1"
    hair_share = float(np.mean(hair_v[vin])) if (hair_v is not None and vin.any()) else 0.0
    out["hair_share"] = round(hair_share, 3)
    metal = float(np.median(metal_v[vin])) if (metal_v is not None and vin.any()) else 0.0
    out["metal"] = round(metal, 3)
    lateral = float(np.mean(cents[:, 0]) - centre_x)
    behind = float(root[1] - centre_y)
    out["lateral"] = round(lateral, 4)
    out["behind"] = round(behind, 4)
    tip_area = float(np.mean(areas[-max(1, len(areas) // 5):]))
    root_area = float(np.mean(areas[:max(1, len(areas) // 5)]))
    out["taper"] = round(tip_area / max(root_area, 1e-9), 3)
    # what it is
    kind, parent = None, None
    if parent_role in HEAD_ROLES:
        kind = "Hair" if hair_share >= 0.5 else "Cloth"
        parent = bones[dom]["name"]
    elif parent_role in CHEST_ROLES or (parent_role or "").startswith("Spine") and parent_role != "Spine":
        kind = "Hair" if hair_share >= 0.5 else ("Cape" if out["width"] >= WIDE else ("Cloth" if behind < 0 else None))
        parent = _spine_top(bones, roles, dom)
        if kind is None:
            out["reason"] = "narrow_back"
    elif parent_role in HIP_ROLES:
        parent = next((b["name"] for b, r in zip(bones, roles) if r == "Hips"), bones[dom]["name"])
        if hair_share >= 0.5:
            kind = "Hair"
        elif out["width"] >= WIDE:
            kind = "Cape"
        elif abs(lateral) <= TAIL_CENTRE * height and behind > 0:
            kind = "Tail"
        else:
            out["reason"] = "side"
    else:
        out["reason"] = "limb"
    out["kind"] = kind
    out["parent"] = parent
    out["parent_role"] = parent_role
    if out["reason"] is None:
        if length < min_len:
            out["reason"] = "short"
        elif clear_share < CLEAR_SHARE:
            out["reason"] = "rests"
        elif length / max(out["thickness"], cell) < THIN_RATIO and kind != "Cape":
            out["reason"] = "thick"
        elif metal >= METAL and kind != "Hair":
            out["reason"] = "metal"
        elif kind == "Tail" and out["taper"] > TAPER_MAX:
            out["reason"] = "not_tapered"
    conf = min(1.0, out["gap_cm"] / (200 * CLEAR_MIN)) * min(1.0, length / (2 * min_len)) * min(1.0, clear_share / 0.8)
    conf *= {"Hair": 1.0 if hair_share >= 0.8 else 0.85, "Cape": 0.9, "Tail": 0.85, "Cloth": 0.75}.get(kind, 0.5)
    out["confidence"] = round(float(conf), 3)
    if out["reason"] is None and conf < CONFIDENCE_MIN:
        out["reason"] = "low_confidence"
    out["why"] = REASONS.get(out["reason"]) if out["reason"] else None
    if out["reason"] is None:
        out["columns"] = chain_columns(idx, xyz, root, out["width"], height, kind, length)
        out["vertex_ids"] = np.nonzero(vin)[0]
    out["root"] = [round(float(v), 4) for v in root]
    out["line"] = [[round(float(c), 4) for c in p] for p in line]
    return out


def _spine_top(bones, roles, dom):
    """The highest spine bone (the chest, where the arms branch) for a part hanging from the back or the shoulders."""
    spine = [(b["head"][2], b["name"]) for b, r in zip(bones, roles) if r and r.startswith("Spine")]
    if roles[dom] and roles[dom].startswith("Spine"):
        return bones[dom]["name"]
    return max(spine)[1] if spine else bones[dom]["name"]


def bones_for(length):
    return 2 if length < 0.15 else (3 if length < 0.35 else 4)


def chain_columns(idx, xyz, root, width, height, kind, length):
    """The chain joints for a part: one column down the middle, or two or three side by side across a wide sheet.
    -> [[(x, y, z) root, ..., tip]] with bones_for(length) bones each."""
    ncol = 1
    if kind == "Cape":
        ncol = 3 if width >= 0.36 else (2 if width >= 0.2 else 1)
    zs = np.unique(idx[:, 0])[::-1]
    nb = bones_for(length)
    cols = []
    lat = xyz[:, 0]
    edges = np.quantile(lat, np.linspace(0, 1, ncol + 1))
    for c in range(ncol):
        lo_, hi_ = edges[c], edges[c + 1]
        sel = (lat >= lo_ - 1e-9) & (lat <= hi_ + 1e-9)
        pts = []
        cell = float(np.min(np.abs(np.diff(np.unique(xyz[:, 2]))))) if len(np.unique(xyz[:, 2])) > 1 else 0.01
        for z in zs:
            s = sel & (idx[:, 0] == z)
            if s.any():
                c_ = xyz[s].mean(axis=0)
                if pts and np.linalg.norm(c_[:2] - pts[-1][:2]) > 6 * cell:
                    break  # the column's slice jumped to another piece (a hem folding round to the front): stop
                pts.append(c_)
        if not pts:
            continue
        pts = np.array(pts)
        r = root.copy()
        if ncol > 1:
            r[0] = float(np.mean(pts[: max(1, len(pts) // 6), 0]))
            r[1] = float(np.mean(pts[: max(1, len(pts) // 6), 1]))
        line = np.vstack([r, pts])
        cols.append([[round(float(v), 5) for v in p] for p in resample(line, nb)])
    return cols


def resample(line, nbones):
    """nbones + 1 points evenly along a polyline (root first)."""
    line = np.asarray(line, float)
    d = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
    if d[-1] <= 1e-9:
        return np.repeat(line[:1], nbones + 1, axis=0)
    t = np.linspace(0, d[-1], nbones + 1)
    return np.stack([np.interp(t, d, line[:, k]) for k in range(3)], axis=1)


# --- library hair (docs/HAIR-LIBRARY-PLAN.md section 6) ---------------------------------------------------------------
# A hairstyle from the library is its own object (VGAC_Hair, bound by hairbind.py) with its hanging groups known from
# the library: role 2+k in hair_mesh.npz, the path of each group in hair_parts.json (glTF, the npz's frame, root
# first). Each group becomes a chain of 2 to 4 bones along its path, parented to the Head bone, before the body's own
# hanging parts are looked for, within the same cap.


def gltf_to_blender(P):
    P = np.asarray(P, float).reshape(-1, 3)
    return np.stack([P[:, 0], -P[:, 2], P[:, 1]], axis=1)


def box_map(src, dst):
    """(scale, shift) with dst ~ src * scale + shift for two copies of the same points (uniform scale, a shift): the
    hair object in the animated file against the npz it was built from."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    slo, shi, dlo, dhi = src.min(0), src.max(0), dst.min(0), dst.max(0)
    s = float(np.max(dhi - dlo)) / max(float(np.max(shi - slo)), 1e-9)
    return s, (dlo + dhi) / 2 - s * (slo + shi) / 2


def match_roles(V_obj, npz_verts_b, npz_role, tol=1e-4):
    """The npz role of every vertex of the hair object, matched by position after normalising both to their boxes.
    -> (n,) roles, -1 where nothing matched."""
    def norm(P):
        lo, hi = P.min(axis=0), P.max(axis=0)
        return (P - (lo + hi) / 2) / max(float(np.max(hi - lo)), 1e-9)

    A, B = norm(np.asarray(V_obj, float)), norm(np.asarray(npz_verts_b, float))
    key = lambda P: np.floor(P / tol).astype(np.int64)  # noqa: E731
    table = {}
    for i, k in enumerate(map(tuple, key(B))):
        table.setdefault(k, []).append(i)
    out = np.full(len(A), -1, np.int64)
    offs = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    role = np.asarray(npz_role)
    for i, k in enumerate(map(tuple, key(A))):
        best, bd = -1, tol
        for o in offs:
            for j in table.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]), ()):
                d = float(np.linalg.norm(B[j] - A[i]))
                if d <= bd:
                    best, bd = j, d
        if best >= 0:
            out[i] = int(role[best])
    return out


def path_from_points(P, step=0.02):
    """A root-first centre line for a group with no usable path: slice centroids from its top down."""
    P = np.asarray(P, float)
    if len(P) == 0:
        return np.zeros((0, 3))
    z1, z0 = float(P[:, 2].max()), float(P[:, 2].min())
    out = []
    z = z1
    while z > z0 - 1e-9:
        s = (P[:, 2] <= z) & (P[:, 2] > z - step)
        if s.any():
            out.append(P[s].mean(axis=0))
        z -= step
    if len(out) == 1:
        out.append(P[np.argmin(P[:, 2])])
    return np.array(out)


def library_parts(groups, V_obj, role_obj, head, scale=1.0, shift=(0.0, 0.0, 0.0), max_bones=MAX_BONES):
    """Chains for the library hair's hanging groups. groups: hair_parts.json groups ({k, kind, path glTF, length_m});
    V_obj (n, 3) the hair object's vertices (Blender space); role_obj (n,) their npz roles; head the Head bone's name;
    scale and shift carry the npz frame onto the object (box_map). -> (parts, bones used). Each part: kind Hair,
    parent the Head, columns [joints root first], length, thickness, confidence 1, library True, group k,
    hair_vertex_ids."""
    V_obj = np.asarray(V_obj, float)
    role_obj = np.asarray(role_obj)
    parts, used = [], 0
    for g in sorted(groups or [], key=lambda g: int(g.get("k", 0)))[:3]:
        k = int(g.get("k", 0))
        ids = np.nonzero(role_obj == 2 + k)[0]
        if len(ids) == 0:
            continue
        path = np.asarray(g.get("path") or [], float).reshape(-1, 3)
        if len(path) >= 2:
            path = gltf_to_blender(path) * scale + np.asarray(shift, float)
        else:
            path = path_from_points(V_obj[ids])
        if len(path) < 2:
            continue
        length = float(np.sum(np.linalg.norm(np.diff(path, axis=0), axis=1)))
        nb = bones_for(length)
        if used + nb > max_bones:
            break
        joints = resample(path, nb)
        _, dist = project_on_chain(V_obj[ids], joints)
        thick = float(np.clip(2.0 * np.median(dist), 0.02, 0.1))
        used += nb
        parts.append({"kind": str(g.get("kind") or "Hair"), "parent": head, "parent_role": "Head", "confidence": 1.0,
                      "columns": [[[round(float(v), 5) for v in p] for p in joints]], "length": length, "thickness": thick,
                      "width": thick, "gap_cm": None, "reason": None, "library": True, "group": k, "verts": int(len(ids)),
                      "hair_vertex_ids": ids, "root": [round(float(v), 4) for v in joints[0]]})
    return parts, used


# --- weights ---------------------------------------------------------------------------------------------------------


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def project_on_chain(P, joints):
    """Position along a chain for points P: (s 0..1 along its length, distance to it)."""
    J = np.asarray(joints, float)
    seg = np.diff(J, axis=0)
    L = np.linalg.norm(seg, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(L)])
    best_d = np.full(len(P), np.inf)
    best_s = np.zeros(len(P))
    for k in range(len(seg)):
        a = J[k]
        t = np.clip(((P - a) @ seg[k]) / max(L[k] ** 2, 1e-12), 0.0, 1.0)
        q = a + t[:, None] * seg[k]
        d = np.linalg.norm(P - q, axis=1)
        better = d < best_d
        best_d[better] = d[better]
        best_s[better] = (cum[k] + t[better] * L[k]) / max(cum[-1], 1e-9)
    return best_s, best_d


def chain_weights(P, old, columns, full=CHAIN_FULL):
    """New weights for the part's vertices. P (k, 3); old: list of {bone: weight} (the original weights, normalised);
    columns: [{"bones": [name...], "joints": [...]}] side by side. The chain's share rises from 0 at the root to 1 at
    `full` of the length (a smooth step), each vertex's chain share is spread over the two nearest bones along the
    chain (by position) and the two nearest columns (by distance), the original weights keep the rest (their two
    strongest). -> list of {bone: weight}, at most 4 each, summing to 1."""
    P = np.asarray(P, float)
    per_col = []
    for col in columns:
        s, d = project_on_chain(P, col["joints"])
        per_col.append((s, d))
    S = np.array([s for s, _ in per_col])  # (c, k)
    D = np.array([d for _, d in per_col])
    if len(columns) > 1:
        order = np.argsort(D, axis=0)[:2]
        d1 = np.take_along_axis(D, order[:1], 0)[0]
        d2 = np.take_along_axis(D, order[1:2], 0)[0]
        w1 = d2 / np.maximum(d1 + d2, 1e-9)
    else:
        order = np.zeros((1, len(P)), int)
        w1 = np.ones(len(P))
    out = []
    for i in range(len(P)):
        cols = [(int(order[0, i]), float(w1[i]))]
        if len(columns) > 1:
            cols.append((int(order[1, i]), 1.0 - float(w1[i])))
        s_mean = sum(S[c, i] * w for c, w in cols)
        share = float(smoothstep(0.0, full, s_mean))
        new = {}
        for c, wc in cols:
            names = columns[c]["bones"]
            n = len(names)
            u = S[c, i] * n - 0.5  # bone centres at k + 0.5
            k0 = int(np.clip(math.floor(u), 0, n - 1))
            k1 = int(np.clip(k0 + 1, 0, n - 1))
            f = float(np.clip(u - k0, 0.0, 1.0)) if k1 != k0 else 0.0
            for k, w in ((k0, 1 - f), (k1, f)):
                if w > 0:
                    new[names[k]] = new.get(names[k], 0.0) + share * wc * w
        o = sorted(old[i].items(), key=lambda t: -t[1])[:2] if old[i] else []
        osum = sum(w for _, w in o)
        for b, w in o:
            if osum > 0:
                new[b] = new.get(b, 0.0) + (1 - share) * w / osum
        top = sorted(new.items(), key=lambda t: -t[1])[:4]
        tot = sum(w for _, w in top)
        out.append({b: w / tot for b, w in top if w > 0} if tot > 0 else dict(o))
    return out


# --- the spring simulation (the same steps as VgacSpringBones.cs) ----------------------------------------------------


def _q_mul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def _q_rot(q, v):
    w, x, y, z = q
    u = np.array([x, y, z])
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def _q_between(a, b):
    """Shortest rotation taking direction a to direction b, as (w, x, y, z)."""
    a = a / max(np.linalg.norm(a), 1e-12)
    b = b / max(np.linalg.norm(b), 1e-12)
    d = float(np.dot(a, b))
    if d < -0.999999:
        axis = np.cross([1.0, 0.0, 0.0], a)
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross([0.0, 1.0, 0.0], a)
        axis /= np.linalg.norm(axis)
        return np.array([0.0, *axis])
    c = np.cross(a, b)
    q = np.array([1.0 + d, *c])
    return q / np.linalg.norm(q)


def slerp_dir(a, b, t):
    """The unit direction t of the way from unit vector a to unit vector b along the great circle."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    d = float(np.clip(np.dot(a, b), -1.0, 1.0))
    ang = math.acos(d)
    if ang < 1e-6:
        return a.copy()
    if ang > math.pi - 1e-4:  # opposite: turn about any axis square to a
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        axis /= np.linalg.norm(axis)
        return a * math.cos(ang * t) + np.cross(axis, a) * math.sin(ang * t)
    sa = math.sin(ang)
    return (math.sin((1 - t) * ang) / sa) * a + (math.sin(t * ang) / sa) * b


def _q_nlerp(a, b, t):
    if np.dot(a, b) < 0:
        b = -b
    q = a * (1 - t) + b * t
    return q / np.linalg.norm(q)


def _closest_on_segment(p, a, b):
    ab = b - a
    t = float(np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-12), 0.0, 1.0))
    return a + t * ab


def push_out(p, colliders, radius):
    """Move point p out of every collider (spheres and capsules as (a, b, r), or a tuple of arrays (A, B, R)), keeping
    `radius` of air. -> (p, depth before the push: the deepest it was inside, in metres, 0 when clear)."""
    if isinstance(colliders, tuple) and len(colliders) == 3 and isinstance(colliders[0], np.ndarray) and colliders[0].ndim == 2:
        A, B, R = colliders
        if len(A) == 0:
            return p, 0.0
        AB = B - A
        den = np.maximum((AB * AB).sum(axis=1), 1e-12)
        t = np.clip(((p - A) * AB).sum(axis=1) / den, 0.0, 1.0)
        Q = A + AB * t[:, None]
        dist = np.linalg.norm(p - Q, axis=1)
        inside = dist < R + radius
        if not inside.any():
            return p, 0.0  # the usual case: clear of everything
        colliders = [(A[i], B[i], float(R[i])) for i in np.nonzero(inside)[0]]
    deepest = 0.0
    for a, b, r in colliders:
        q = _closest_on_segment(p, a, b)
        d = p - q
        n = float(np.linalg.norm(d))
        lim = r + radius
        if n < lim:
            deepest = max(deepest, lim - n)
            if n < 1e-9:
                d, n = np.array([0.0, 1.0, 0.0]), 1.0
            p = q + d / n * lim
    return p, deepest


def simulate(frames, chain, settings, fps, colliders=None, travel=None, loop=False, passes=LOOP_PASSES,
             settle=SETTLE_SECONDS, gravity_dir=(0.0, 0.0, -1.0)):
    """Run the spring over a clip. frames: [{"root": (3,), "q": (4,) the parent's rotation from its rest}] one per
    clip frame; chain: {"rest": [(3,)...] joints at rest (root first), "radius": metres}; settings: stiffness (Hz),
    damping (ratio), drag (1/s), gravity (m/s^2, relative to the rest pose); colliders: per frame a list of
    (a, b, r) capsules (a == b for a sphere) with the chain's own allowances already applied; travel: (3,) m/s the
    clip moves through the air; settings "hang" (0..1, default 0): the share by which each bone's rest direction is
    pulled toward world down (library hair; 0 is the behaviour every other chain has). -> {"joints": (frames, n+1, 3)
    world positions, "depth": deepest push in metres}."""
    rest = np.asarray(chain["rest"], float)
    seg_rest = np.diff(rest, axis=0)
    lengths = np.linalg.norm(seg_rest, axis=1)
    n = len(seg_rest)
    w = 2 * math.pi * float(settings["stiffness"])
    zeta = float(settings["damping"])
    drag = float(settings["drag"])
    g = float(settings["gravity"]) * np.asarray(gravity_dir, float)
    hang = float(settings.get("hang") or 0.0)
    down = np.asarray(gravity_dir, float) / max(np.linalg.norm(gravity_dir), 1e-12)
    v_air = np.zeros(3) if travel is None else np.asarray(travel, float)
    radius = float(chain.get("radius") or 0.0)
    nf = len(frames)
    dt = SUBSTEP
    per = max(1, int(round((1.0 / fps) / dt)))
    dt = (1.0 / fps) / per
    dirs = seg_rest / np.maximum(lengths[:, None], 1e-12)
    g_across = np.array([g - np.dot(g, d) * d for d in dirs])
    roots = np.array([f["root"] for f in frames], float)
    qs = np.array([f["q"] for f in frames], float)

    def parent_at(t):
        """Parent root and rotation at clip time t (frames), wrapping for loops."""
        if loop:
            t = t % (nf - 1) if nf > 1 else 0.0
        t = float(np.clip(t, 0, nf - 1))
        i = int(math.floor(t))
        j = min(i + 1, nf - 1)
        f = t - i
        return roots[i] * (1 - f) + roots[j] * f, _q_nlerp(qs[i], qs[j], f), (i, j, f)

    # the hit boxes as arrays per frame, moved between frames with the body (a fast leg must not skip through)
    if colliders:
        CA = np.array([[np.asarray(a, float) for a, _, _ in fc] for fc in colliders]).reshape(nf, -1, 3)
        CB = np.array([[np.asarray(b, float) for _, b, _ in fc] for fc in colliders]).reshape(nf, -1, 3)
        CR = np.array([[float(r_) for _, _, r_ in fc] for fc in colliders]).reshape(nf, -1)
    else:
        CA = CB = np.zeros((nf, 0, 3))
        CR = np.zeros((nf, 0))

    def cols_at(i, j, f):
        return (CA[i] * (1 - f) + CA[j] * f, CB[i] * (1 - f) + CB[j] * f, CR[i])

    def targets(r, q, P):
        out = [r]
        Q = q
        for k in range(n):
            out.append(out[-1] + _q_rot(Q, seg_rest[k]) if k == 0 else P[k] + _q_rot(Q, seg_rest[k]))
            if k + 1 < len(P):
                Q = _q_mul(_q_between(_q_rot(Q, seg_rest[k]), P[k + 1] - P[k]), Q)
        return out

    r0, q0, _ = parent_at(0.0)
    P = np.array(targets(r0, q0, [r0 + np.zeros(3)] * (n + 1)))
    # rest shape carried by the first frame
    P[0] = r0
    Q = q0
    for k in range(n):
        P[k + 1] = P[k] + _q_rot(Q, seg_rest[k])
    prev = P.copy()
    T_prev = P.copy()
    deepest = 0.0
    out = []
    total_frames = (nf - 1) * passes + 1 if (loop and nf > 1) else nf
    settle_steps = 0 if loop else int(settle / dt)
    clip_time = 0.0
    record_from = (nf - 1) * (passes - 1) if (loop and nf > 1) else 0
    step = 0
    nsteps = settle_steps + (total_frames - 1) * per
    while True:
        t_clip = max(0.0, (step - settle_steps) / per)
        r, q, fi = parent_at(t_clip)
        cols = cols_at(*fi)
        # record
        if step >= settle_steps and (step - settle_steps) % per == 0:
            fr = (step - settle_steps) // per
            if fr >= record_from:
                out.append(P.copy())
        if step >= nsteps:
            break
        P[0] = r
        Q = q
        newP = P.copy()
        for k in range(n):
            if hang > 0:
                # library hair: the rest direction pulled toward world down, and no gravity across the bone there (the
                # target is where it hangs)
                seg_w = _q_rot(Q, seg_rest[k])
                d = slerp_dir(seg_w / max(lengths[k], 1e-12), down, hang)
                tgt = newP[k] + d * lengths[k]
                g_eff = np.dot(g, d) * d
            else:
                tgt = newP[k] + _q_rot(Q, seg_rest[k])
            vel = (P[k + 1] - prev[k + 1]) / dt
            vt = (tgt - T_prev[k + 1]) / dt
            # gravity, less the part of it across the bone at rest, carried by the parent: the part keeps its built
            # shape standing still, and gravity along the bone still swings it back like a pendulum
            if hang <= 0:
                g_eff = g - _q_rot(Q, g_across[k])
            acc = w * w * (tgt - P[k + 1]) - 2 * zeta * w * (vel - vt) - drag * (vel + v_air) + g_eff
            x = P[k + 1] + vel * dt + acc * dt * dt
            for _ in range(COLLIDE_ITER):
                # out of the hit boxes, then back to the bone's length (which can pull it in again: repeat)
                x, dep = push_out(x, cols, radius * (k + 1) / n)
                if step >= settle_steps + record_from * per:
                    deepest = max(deepest, dep)
                mid, _ = push_out(0.5 * (x + newP[k]), cols, radius * (k + 0.5) / n)
                x = x + (mid - 0.5 * (x + newP[k])) * 2.0  # the bone's middle out too, by moving its far end
                dvec = x - newP[k]
                x = newP[k] + dvec / max(np.linalg.norm(dvec), 1e-12) * lengths[k]
            T_prev[k + 1] = tgt
            newP[k + 1] = x
            Q = _q_mul(_q_between(_q_rot(Q, seg_rest[k]), x - newP[k]), Q)
        newP[0] = r
        prev = P
        P = newP
        step += 1
        clip_time = t_clip
    J = np.array(out)
    if loop and len(J) > 1:
        J = close_loop(J, rest_lengths=lengths)
    return {"joints": J, "depth": float(deepest)}


def close_loop(J, rest_lengths=None):
    """Spread the difference between the last frame and the first over the clip, so the last frame equals the first
    (the clip's last frame is the same pose as its first). Positions are taken relative to the root of each frame."""
    J = np.asarray(J, float).copy()
    rel = J - J[:, :1, :]
    diff = rel[-1] - rel[0]
    n = len(J) - 1
    for i in range(len(J)):
        rel[i] = rel[i] - diff * (i / n)
    J = rel + J[:, :1, :]
    if rest_lengths is not None:
        for i in range(len(J)):
            for k in range(len(rest_lengths)):
                d = J[i, k + 1] - J[i, k]
                J[i, k + 1] = J[i, k] + d / max(np.linalg.norm(d), 1e-12) * rest_lengths[k]
    return J


def seam_error(J):
    """Largest distance (metres) between a joint in the first frame and the same joint in the last frame."""
    J = np.asarray(J, float)
    return float(np.max(np.linalg.norm(J[-1] - J[0], axis=1))) if len(J) > 1 else 0.0


def travel_for(clip, height):
    """The clip's speed through still air (m/s along the character's front, -Y) for walk and run kinds, else 0."""
    n = clip.lower()
    for key in sorted(TRAVEL, key=len, reverse=True):
        if key in n:
            return TRAVEL[key] * height / 1.8
    return 0.0


def collider_allowance(rest_joints, shapes, radius):
    """For each collider (a, b, r) at rest, the radius to use for this chain: never more than what keeps the chain's
    rest pose just outside it (hit boxes are loose, and a part built touching the body would otherwise be pushed off
    its rest shape). -> list of radii."""
    out = []
    pts = np.asarray(rest_joints, float)[1:]
    for a, b, r in shapes:
        d = min(float(np.linalg.norm(p - _closest_on_segment(p, a, b))) for p in pts)
        out.append(max(0.0, min(r, d - radius - 0.002)))
    return out


# --- springs.json ----------------------------------------------------------------------------------------------------


def to_gltf(p):
    """Blender world (x, y, z) -> glTF (x, z, -y)."""
    return [round(float(p[0]), 5), round(float(p[2]), 5), round(float(-p[1]), 5)]


def springs_doc(chains, hitboxes_doc, height, version=1):
    """springs.json: glTF model space (Y up, metres). chains: [{name, kind, parent, bones, joints (Blender space),
    radius, settings, confidence}]."""
    cols = []
    for h in (hitboxes_doc or {}).get("hitboxes") or []:
        cols.append({k: h.get(k) for k in ("name", "bone", "kind", "center", "axis", "forward", "radius", "height", "size", "offset")})
    return {
        "version": version, "space": "gltf", "units": "m", "height": round(float(height), 4),
        "substep_hz": int(round(1.0 / SUBSTEP)),
        "settings_note": ("stiffness: natural frequency in Hz; damping: share of critical damping; drag: air drag in 1/s "
                          "(what makes a part trail when the character moves); gravity: m/s^2, added relative to the rest "
                          "pose, so the part keeps its built shape while the character stands upright. Scale-free: the "
                          "engine scripts scale gravity and radii with the character's size."),
        "chains": [{"name": c["name"], "kind": c["kind"], "parent": c["parent"], "bones": c["bones"],
                    "joints": [to_gltf(p) for p in c["joints"]], "radius": round(float(c["radius"]), 4),
                    "stiffness": c["settings"]["stiffness"], "damping": c["settings"]["damping"],
                    "drag": c["settings"]["drag"], "gravity": c["settings"]["gravity"],
                    **({"hang": c["settings"]["hang"]} if c["settings"].get("hang") else {}),
                    "confidence": c.get("confidence"), "colliders": c.get("colliders", [h["name"] for h in cols])}
                   for c in chains],
        "colliders": cols,
    }


# --- Blender side ----------------------------------------------------------------------------------------------------


def _opt(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def main():  # pragma: no cover - runs inside Blender
    import time

    import bpy
    from mathutils import Matrix, Quaternion, Vector

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    t_start = time.time()
    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = os.path.abspath(_opt(argv, "--report"))
    doc_path = _opt(argv, "--doc")
    clips_path = _opt(argv, "--clips")
    hb_path = _opt(argv, "--hitboxes")
    hair_path = _opt(argv, "--hair-points")
    metal_path = _opt(argv, "--metallic")
    pic_dir = _opt(argv, "--pictures")
    fps = int(_opt(argv, "--fps", "24"))
    category = _opt(argv, "--category", "humanoid")
    find_only = "--find-only" in argv
    hair_obj_name = _opt(argv, "--hair-object")
    hair_mesh_path = _opt(argv, "--hair-mesh")
    hair_parts_path = _opt(argv, "--hair-parts")
    report = {"ok": False, "chains": [], "rejected": [], "reason": None}

    def finish():
        report["seconds"] = round(time.time() - t_start, 2)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))

    _common.open_glb(src)
    scene = bpy.context.scene
    scene.render.fps = fps
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    # Library hair (--hair-object): its own object, left out of the hanging-part search; its groups get chains below.
    hair_obj = None
    if hair_obj_name:
        hair_obj = next((o for o in meshes if o.name == hair_obj_name or o.name.startswith(hair_obj_name + ".")), None)
    bodies = [o for o in meshes if o is not hair_obj]
    if arm is None or not bodies:
        report["reason"] = "no_rig"
        finish()
        return
    mesh = max(bodies, key=lambda o: len(o.data.vertices))
    me = mesh.data
    me.calc_loop_triangles()
    MW = mesh.matrix_world
    V = np.array([tuple(MW @ v.co) for v in me.vertices])
    Ft = np.array([tuple(t.vertices) for t in me.loop_triangles], np.int64)
    AW = arm.matrix_world
    bone_list = list(arm.data.bones)
    bnames = [b.name for b in bone_list]
    bones = [{"name": b.name, "head": list(AW @ b.head_local), "tail": list(AW @ b.tail_local),
              "parent": b.parent.name if b.parent else None} for b in bone_list]
    gname = {g.index: g.name for g in mesh.vertex_groups}
    bidx = {n: i for i, n in enumerate(bnames)}
    WI = np.full((len(V), 4), -1, np.int64)
    WW = np.zeros((len(V), 4))
    old_w = []
    for v in me.vertices:
        e = sorted(((gname[g.group], g.weight) for g in v.groups if g.weight > 0 and gname.get(g.group) in bidx), key=lambda t: -t[1])[:4]
        tot = sum(w for _, w in e) or 1.0
        old_w.append({b: w / tot for b, w in e})
        for k, (b, w) in enumerate(e):
            WI[v.index, k] = bidx[b]
            WW[v.index, k] = w
    hair_v = None
    if hair_path and os.path.isfile(hair_path):
        pts = np.asarray(json.load(open(hair_path, encoding="utf-8")), float).reshape(-1, 3)
        if len(pts):
            pts_b = np.stack([pts[:, 0], -pts[:, 2], pts[:, 1]], axis=1)
            cen = V[Ft].mean(axis=1)
            hair_t = match_points(cen, pts_b, 0.004)
            hair_v = np.zeros(len(V), bool)
            hair_v[np.unique(Ft[hair_t])] = True
            report["hair_triangles"] = int(hair_t.sum())
            report["hair_points"] = int(len(pts))
    metal_v = None
    if metal_path and os.path.isfile(metal_path) and me.uv_layers:
        try:
            img = bpy.data.images.load(metal_path)
            w_, h_ = img.size
            px = np.array(img.pixels[:], dtype=np.float32).reshape(h_, w_, img.channels)[..., 0]
            uvl = me.uv_layers.active.data
            UV = np.zeros((len(V), 2))
            for lp in me.loops:
                UV[lp.vertex_index] = uvl[lp.index].uv
            xs = np.clip((UV[:, 0] % 1.0) * (w_ - 1), 0, w_ - 1).astype(int)
            ys = np.clip((UV[:, 1] % 1.0) * (h_ - 1), 0, h_ - 1).astype(int)
            metal_v = px[ys, xs]
        except Exception as e:  # noqa: BLE001
            report["metallic_error"] = str(e)[:200]
    lib_parts, lib_bones, lib_counter = [], 0, {}
    if hair_obj is not None and hair_mesh_path and os.path.isfile(hair_mesh_path):
        try:
            nz = np.load(hair_mesh_path)
            NV = gltf_to_blender(nz["verts"])
            nrole = np.asarray(nz["role"]) if "role" in nz.files else np.zeros(len(NV), int)
            HW = hair_obj.matrix_world
            HV = np.array([tuple(HW @ v.co) for v in hair_obj.data.vertices])
            hrole = match_roles(HV, NV, nrole)
            s_map, sh_map = box_map(NV, HV)
            groups = (json.load(open(hair_parts_path, encoding="utf-8")).get("groups") or []) \
                if hair_parts_path and os.path.isfile(hair_parts_path) else []
            if not groups:  # no parts file: one group per hang role found in the npz
                groups = [{"k": int(r) - 2, "kind": "Hair", "path": []} for r in sorted(set(int(x) for x in nrole if x >= 2))]
            head = next((b["name"] for b in bones if role_of(b["name"]) == "Head"), None)
            if head is not None:
                lib_parts, lib_bones = library_parts(groups, HV, hrole, head, s_map, sh_map)
            for p in lib_parts:
                lib_counter["Hair"] = lib_counter.get("Hair", 0) + 1
                p["names"] = [f"{PREFIX}Hair_{lib_counter['Hair']:02d}"]
            report["library_hair"] = {"object": hair_obj.name, "verts": int(len(HV)), "matched": int(np.sum(hrole >= 0)),
                                      "groups": len(groups), "chains": len(lib_parts), "scale": round(s_map, 6)}
        except Exception as e:  # noqa: BLE001
            report["library_hair"] = {"error": str(e)[:300]}
    found = find_parts(V, Ft, bones, (WI, WW), hair_v, metal_v, category, max_bones=MAX_BONES - lib_bones, counter=lib_counter)
    report.update(height=found["height"], cell=found["cell"], find_seconds=found["seconds"],
                  rejected=[_public(r) for r in found["rejected"]], parts_seen=found["part_count"])
    parts = lib_parts + found["parts"]
    hb_doc = json.load(open(hb_path, encoding="utf-8")) if hb_path and os.path.isfile(hb_path) else None
    if pic_dir:
        try:
            os.makedirs(pic_dir, exist_ok=True)
            report["pictures"] = render_pictures(bpy, scene, mesh, arm, V, parts, found, pic_dir, hair_obj)
        except Exception as e:  # noqa: BLE001
            report["picture_error"] = str(e)[:300]
    if not parts:
        report["reason"] = "library_rests" if hair_obj is not None else "none_found"
        report["ok"] = True
        finish()
        return
    if find_only:
        report["ok"] = True
        report["chains"] = [_public(p) for p in parts]
        finish()
        return

    # --- bones ---
    chains = []
    bpy.context.view_layer.objects.active = arm
    for o in scene.objects:
        o.select_set(False)
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    AWi = AW.inverted()
    eb = arm.data.edit_bones
    for p in parts:
        for ci, joints in enumerate(p["columns"]):
            name = p["names"][ci]
            parent = eb[p["parent"]]
            prev = parent
            bl = []
            for k in range(len(joints) - 1):
                b = eb.new(f"{name}_{k + 1}" if len(joints) > 2 else name)
                b.head = AWi @ Vector(joints[k])
                b.tail = AWi @ Vector(joints[k + 1])
                b.roll = 0.0
                b.parent = prev
                b.use_connect = k > 0
                b.use_deform = True
                prev = b
                bl.append(b.name)
            chains.append({"name": name, "kind": p["kind"], "parent": p["parent"], "bones": bl, "joints": joints,
                           "part": p, "radius": float(np.clip(0.5 * p["thickness"], 0.01, 0.05)),
                           "settings": dict(SETTINGS[p["kind"]], **({"hang": LIBRARY_HAIR_HANG} if p.get("library") else {})),
                           "confidence": p["confidence"]})
    bpy.ops.object.mode_set(mode="OBJECT")

    # --- weights ---
    hair_old_w = None
    for p in parts:
        mine = [c for c in chains if c["part"] is p]
        if p.get("library"):
            # the library hair's own object: its weights from hairbind.py (Head) fade onto the chain
            target, tme = hair_obj, hair_obj.data
            vids = p["hair_vertex_ids"]
            if hair_old_w is None:
                hg = {g.index: g.name for g in hair_obj.vertex_groups}
                hair_old_w = []
                for v in tme.vertices:
                    e = sorted(((hg[g.group], g.weight) for g in v.groups if g.weight > 0 and hg.get(g.group) in bidx), key=lambda t: -t[1])[:4]
                    tot = sum(w for _, w in e) or 1.0
                    hair_old_w.append({b: w / tot for b, w in e})
            HWm = hair_obj.matrix_world
            P = np.array([tuple(HWm @ tme.vertices[int(i)].co) for i in vids])
            olds = [hair_old_w[int(i)] for i in vids]
        else:
            target, tme = mesh, me
            vids = p["vertex_ids"]
            P = V[vids]
            olds = [old_w[i] for i in vids]
        cols = [{"bones": c["bones"], "joints": c["joints"]} for c in mine]
        neww = chain_weights(P, olds, cols)
        for c in mine:
            for bn in c["bones"]:
                if bn not in target.vertex_groups:
                    target.vertex_groups.new(name=bn)
        for vi, wmap in zip(vids, neww):
            for g in list(tme.vertices[int(vi)].groups):
                target.vertex_groups[g.group].remove([int(vi)])
            for bn, w in wmap.items():
                grp = target.vertex_groups.get(bn) or target.vertex_groups.new(name=bn)
                grp.add([int(vi)], float(w), "REPLACE")
        for c in mine:
            c["verts"] = int(len(vids))
    report["weights"] = {c["name"]: c.get("verts", 0) for c in chains}

    # --- bake ---
    ad = arm.animation_data
    clip_rows = {}
    if clips_path and os.path.isfile(clips_path):
        for r in (json.load(open(clips_path, encoding="utf-8")).get("clips") or []):
            clip_rows[r["name"]] = r
    actions = []
    if ad is not None:
        for tr in ad.nla_tracks:
            if tr.strips and tr.strips[0].action:
                actions.append((tr.name, tr.strips[0].action, tr))
        if not actions and ad.action is not None:
            actions.append((ad.action.name, ad.action, None))
    if not actions:
        report["reason"] = "no_clips"
    for m in meshes:
        for mod in m.modifiers:
            mod.show_viewport = False
    shapes_rest = hitbox_shapes(hb_doc)
    for c in chains:
        c["allow"] = collider_allowance(c["joints"], [(s["a"], s["b"], s["r"]) for s in shapes_rest], c["radius"])
        c["colliders"] = [s["name"] for s in shapes_rest]
    rest_mat = {b.name: (AW @ b.matrix_local) for b in arm.data.bones}
    clip_report = {}
    keyed = {}
    t_bake = time.time()
    for name, act, tr in actions:
        row = clip_rows.get(name) or {}
        loop = bool(row.get("loop", getattr(act, "use_cyclic", False)))
        f0, f1 = act.frame_range
        f0, f1 = int(round(f0)), int(round(f1))
        if ad.nla_tracks:
            for t2 in ad.nla_tracks:
                t2.mute = True
        ad.action = act
        if hasattr(ad, "action_slot") and len(getattr(act, "slots", [])) > 0:
            ad.action_slot = act.slots[0]
        frames = list(range(f0, f1 + 1))
        parents = sorted({c["parent"] for c in chains})
        shape_bones = sorted({s["bone"] for s in shapes_rest if s["bone"] in arm.pose.bones})
        samples = {b: [] for b in set(parents) | set(shape_bones)}
        for f in frames:
            scene.frame_set(f)
            for b in samples:
                samples[b].append((AW @ arm.pose.bones[b].matrix).copy())
        travel = np.array([0.0, -travel_for(name, found["height"]), 0.0])
        clip_report[name] = {"loop": loop, "frames": len(frames), "travel_m_s": round(float(-travel[1]), 2), "chains": {}}
        # colliders per frame, from the bones they ride on
        colliders = []
        for i in range(len(frames)):
            fc = []
            for s in shapes_rest:
                if s["bone"] not in samples:
                    continue
                Mdef = samples[s["bone"]][i] @ rest_mat[s["bone"]].inverted()
                fc.append((np.array(Mdef @ Vector(s["a"])), np.array(Mdef @ Vector(s["b"])), s["r"]))
            colliders.append(fc)
        for c in chains:
            Rrest = rest_mat[c["parent"]]
            fr = []
            for i in range(len(frames)):
                Mdef = samples[c["parent"]][i] @ Rrest.inverted()
                qd = Mdef.to_quaternion()
                fr.append({"root": np.array(Mdef @ Vector(c["joints"][0])), "q": np.array([qd.w, qd.x, qd.y, qd.z])})
            per_frame = [[(a, b, r_) for (a, b, _), r_ in zip(fc, c["allow"])] for fc in colliders]
            res = simulate(fr, {"rest": c["joints"], "radius": c["radius"]}, c["settings"], fps, per_frame,
                           travel, loop=loop and len(frames) > 2)
            J = res["joints"]
            if os.environ.get("VGAC_SPRING_DUMP"):
                np.save(os.path.join(os.environ["VGAC_SPRING_DUMP"], f"{name}_{c['name']}.npy"),
                        np.array({"frames": fr, "colliders": per_frame, "rest": c["joints"], "radius": c["radius"],
                                  "settings": c["settings"], "travel": travel, "loop": loop, "J": J}, dtype=object))
            full_depth = deepest_in_shapes(J, colliders)
            clip_report[name]["chains"][c["name"]] = {
                "seam_m": round(seam_error(J), 5) if loop else None, "pushed_m": round(res["depth"], 4),
                "into_hitbox_m": round(full_depth, 4),
                "into_beyond_rest_m": round(deepest_beyond_rest(J, colliders, c["allow"]), 4),
                "swing_deg": round(swing_degrees(J, fr, c["joints"]), 1)}
            if c["settings"].get("hang"):
                hm, hx = hang_degrees(J)
                clip_report[name]["chains"][c["name"]].update(hang_mean_deg=round(hm, 1), hang_max_deg=round(hx, 1))
            keyed[(name, c["name"])] = (J, fr)
            write_chain_keys(arm, act, c, J, samples[c["parent"]], rest_mat)
        ad.action = None
    if ad is not None:
        for t2 in ad.nla_tracks:
            t2.mute = False
    report["bake_seconds"] = round(time.time() - t_bake, 2)
    report["clips"] = clip_report
    for m in meshes:
        for mod in m.modifiers:
            mod.show_viewport = True
    if pic_dir:
        try:
            report["chain_picture"] = render_chains(bpy, scene, mesh, arm, chains, pic_dir)
        except Exception as e:  # noqa: BLE001
            report["picture_error"] = str(e)[:300]
    for pb in arm.pose.bones:
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
    scene.frame_set(1)
    # --- export (as procanim and retarget do) ---
    bpy.ops.object.select_all(action="SELECT")
    args = dict(filepath=dst, export_format="GLB", use_selection=True, export_animations=bool(actions),
                export_animation_mode="NLA_TRACKS", export_force_sampling=True, export_apply=False)
    try:
        bpy.ops.export_scene.gltf(**args, export_anim_slide_to_zero=True)
    except TypeError:
        bpy.ops.export_scene.gltf(**args)
    report["chains"] = [{"name": c["name"], "kind": c["kind"], "parent": c["parent"], "bones": c["bones"],
                         "joints": c["joints"], "radius": round(c["radius"], 4), "confidence": c["confidence"],
                         "verts": c.get("verts", 0), "settings": c["settings"], "part": _public(c["part"]),
                         **({"library": True} if c["part"].get("library") else {})} for c in chains]
    report["bones_added"] = sum(len(c["bones"]) for c in chains)
    if doc_path:
        doc = springs_doc(chains, hb_doc, found["height"])
        with open(doc_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
    report["ok"] = True
    finish()
    print("SPRINGS_OK", report["bones_added"], "bones,", len(chains), "chains,", report.get("bake_seconds"), "s bake")


def _public(p):
    return {k: v for k, v in p.items() if k not in ("vertex_ids", "hair_vertex_ids")}


def match_points(cen, pts, tol):
    """Boolean (m,): triangle centroid within `tol` of one of `pts` (a grid hash, no KD tree needed)."""
    key = lambda P: np.floor(P / tol).astype(np.int64)  # noqa: E731
    table = {}
    for i, k in enumerate(map(tuple, key(pts))):
        table.setdefault(k, []).append(i)
    out = np.zeros(len(cen), bool)
    kc = key(cen)
    offs = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    for t, k in enumerate(map(tuple, kc)):
        for o in offs:
            cand = table.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]))
            if cand and np.min(np.linalg.norm(pts[cand] - cen[t], axis=1)) <= tol:
                out[t] = True
                break
    return out


def hitbox_shapes(doc):
    """Hit boxes as segments with a radius in Blender space: capsules from their axis, spheres as a point, boxes as a
    capsule along their longest side with the half thickness as radius (a close fit for the limb-shaped boxes)."""
    out = []
    for h in (doc or {}).get("hitboxes") or []:
        c = np.array([h["center"][0], -h["center"][2], h["center"][1]], float)
        ax = np.array([h["axis"][0], -h["axis"][2], h["axis"][1]], float)
        if h.get("kind") == "box":
            size = np.asarray(h.get("size") or [0.1, 0.1, 0.1], float)
            r = float(min(size[0], size[2]) / 2)
            half = max(float(size[1]) / 2 - r, 0.0)
        else:
            r = float(h.get("radius") or 0.05)
            half = max(float(h.get("height") or 2 * r) / 2 - r, 0.0) if h.get("kind") != "sphere" else 0.0
        out.append({"name": h["name"], "bone": h["bone"], "a": c - ax * half, "b": c + ax * half, "r": r})
    return out


def deepest_in_shapes(J, colliders):
    """The deepest any point of the chain (joints and 4 points per bone) is inside a hit box, in metres."""
    worst = 0.0
    for i in range(len(J)):
        pts = []
        for k in range(len(J[i]) - 1):
            for t in (0.25, 0.5, 0.75, 1.0):
                pts.append(J[i][k] + (J[i][k + 1] - J[i][k]) * t)
        for a, b, r in colliders[min(i, len(colliders) - 1)] if colliders else []:
            for p in pts:
                d = float(np.linalg.norm(p - _closest_on_segment(p, a, b)))
                worst = max(worst, r - d)
    return max(0.0, worst)


def deepest_beyond_rest(J, colliders, allow):
    """As deepest_in_shapes, against the radii this chain uses (its rest pose already inside a loose hit box does not
    count)."""
    worst = 0.0
    for i in range(len(J)):
        pts = [J[i][k] + (J[i][k + 1] - J[i][k]) * t for k in range(len(J[i]) - 1) for t in (0.5, 1.0)]
        for (a, b, _), r in zip(colliders[min(i, len(colliders) - 1)] if colliders else [], allow):
            for p in pts:
                d = float(np.linalg.norm(p - _closest_on_segment(p, a, b)))
                worst = max(worst, r - d)
    return max(0.0, worst)


def hang_degrees(J, down=(0.0, 0.0, -1.0)):
    """Mean and largest angle between the chain's root-to-tip direction and world down over the clip, in degrees."""
    J = np.asarray(J, float)
    v = J[:, -1] - J[:, 0]
    c = (v @ np.asarray(down, float)) / np.maximum(np.linalg.norm(v, axis=1), 1e-12)
    a = np.degrees(np.arccos(np.clip(c, -1, 1)))
    return float(a.mean()), float(a.max())


def swing_degrees(J, frames, rest):
    """Largest angle between the chain's root-to-tip direction and the rest direction carried by the parent."""
    rest = np.asarray(rest, float)
    d0 = rest[-1] - rest[0]
    best = 0.0
    for i in range(len(J)):
        q = frames[min(i, len(frames) - 1)]["q"]
        want = _q_rot(q, d0)
        got = J[i][-1] - J[i][0]
        c = float(np.dot(want, got) / max(np.linalg.norm(want) * np.linalg.norm(got), 1e-12))
        best = max(best, math.degrees(math.acos(np.clip(c, -1, 1))))
    return best


def write_chain_keys(arm, act, chain, J, parent_world, rest_mat):  # pragma: no cover - Blender
    """Key the chain bones' rotations so each bone points from joint k to joint k + 1 of the simulation, frame by
    frame (frame numbers as the action's own)."""
    from mathutils import Matrix, Quaternion, Vector

    AWi = arm.matrix_world.inverted()
    bones = chain["bones"]
    f0 = int(round(act.frame_range[0]))
    keys = {b: [] for b in bones}
    for i in range(len(J)):
        P_world = parent_world[min(i, len(parent_world) - 1)]  # parent pose, world
        parent_pose = AWi @ P_world  # armature space
        parent_rest = arm.data.bones[chain["parent"]].matrix_local
        prev_pose, prev_rest = parent_pose, parent_rest
        for k, bn in enumerate(bones):
            b = arm.data.bones[bn]
            head = AWi @ Vector(J[i][k])
            tail = AWi @ Vector(J[i][k + 1])
            rest_dir = (b.tail_local - b.head_local).normalized()
            # the bone's pose without its own rotation: parent pose carried onto the bone's rest
            base = prev_pose @ (prev_rest.inverted() @ b.matrix_local)
            cur_dir = base.to_3x3() @ Vector((0, 1, 0))
            want = (tail - head).normalized()
            rot = cur_dir.rotation_difference(want)
            M = Matrix.Translation(base.to_translation()) @ (rot.to_matrix().to_4x4() @ Matrix.Translation(-base.to_translation())) @ base
            basis = (base.inverted() @ M).to_quaternion()
            basis.normalize()
            if keys[bn] and keys[bn][-1].dot(basis) < 0:
                basis = -basis
            keys[bn].append(basis)
            prev_pose, prev_rest = M, b.matrix_local
            _ = rest_dir
    ad = arm.animation_data
    ad.action = act
    fast = hasattr(act, "fcurve_ensure_for_datablock")
    for bn in bones:
        arm.pose.bones[bn].rotation_mode = "QUATERNION"
        if fast:
            for k in range(4):
                fc = act.fcurve_ensure_for_datablock(arm, f'pose.bones["{bn}"].rotation_quaternion', index=k)
                fc.keyframe_points.clear()
                fc.keyframe_points.add(len(keys[bn]))
                co = []
                for i, q in enumerate(keys[bn]):
                    co += [float(f0 + i), float(q[k])]
                fc.keyframe_points.foreach_set("co", co)
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
                fc.update()
        else:
            for i, q in enumerate(keys[bn]):
                arm.pose.bones[bn].rotation_quaternion = q
                arm.pose.bones[bn].keyframe_insert("rotation_quaternion", frame=f0 + i)


def _camera(bpy, scene, centre, extent, direction, res=640):  # pragma: no cover - Blender
    import math as m

    from mathutils import Vector

    cam_data = bpy.data.cameras.get("vgac_spring_cam") or bpy.data.cameras.new("vgac_spring_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = extent * 1.1
    cam = bpy.data.objects.get("vgac_spring_cam") or bpy.data.objects.new("vgac_spring_cam", cam_data)
    if cam.name not in scene.collection.objects:
        scene.collection.objects.link(cam)
    scene.camera = cam
    d = Vector(direction).normalized()
    cam.location = Vector(centre) + d * extent * 4
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.render.resolution_x = scene.render.resolution_y = res
    return cam


VIEWS = (("Back", (0.0, 1.0, 0.0)), ("Side", (1.0, 0.0, 0.0)), ("Back three-quarter", (0.7, 0.7, 0.15)), ("Front", (0.0, -1.0, 0.0)))


def rest_pose(arm):  # pragma: no cover - Blender
    """Show the bind pose: no action, every clip track muted, every bone at rest. -> the muted tracks' old states."""
    from mathutils import Quaternion, Vector

    ad = arm.animation_data
    states = []
    if ad is not None:
        ad.action = None
        states = [(t, t.mute) for t in ad.nla_tracks]
        for t, _ in states:
            t.mute = True
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
        pb.scale = Vector((1, 1, 1))
    return states


def _project(cam, scene, p):  # pragma: no cover - Blender
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    co = world_to_camera_view(scene, cam, Vector(p))
    R = scene.render.resolution_x
    return co.x * R, (1 - co.y) * R


def render_pictures(bpy, scene, mesh, arm, V, parts, found, out_dir, hair_obj=None):  # pragma: no cover - Blender
    """"What was taken as a hanging part": the model from behind, the side and the back three-quarter, the parts that
    sway tinted green and the parts looked at and left alone tinted orange. -> {view: png, camera points}. Library hair
    (hair_obj) is drawn light brown, its swaying groups green."""
    me = mesh.data
    col = np.tile(np.array([0.72, 0.72, 0.72, 1.0]), (len(V), 1))
    grid = found["grid"]
    vcell = to_cells(grid, V)
    for p in parts:
        if "vertex_ids" in p:
            col[p["vertex_ids"]] = [0.15, 0.85, 0.25, 1.0]
    attr = me.color_attributes.get("vgac_spring") or me.color_attributes.new("vgac_spring", "FLOAT_COLOR", "POINT")
    attr.data.foreach_set("color", col.ravel().astype(np.float32))
    hattr = None
    if hair_obj is not None:
        hcol = np.tile(np.array([0.55, 0.42, 0.3, 1.0]), (len(hair_obj.data.vertices), 1))
        for p in parts:
            if "hair_vertex_ids" in p:
                hcol[p["hair_vertex_ids"]] = [0.15, 0.85, 0.25, 1.0]
        hattr = hair_obj.data.color_attributes.new("vgac_spring", "FLOAT_COLOR", "POINT")
        hattr.data.foreach_set("color", hcol.ravel().astype(np.float32))
        try:
            hair_obj.data.color_attributes.active_color = hattr
        except Exception:  # noqa: BLE001
            pass
    _ = vcell
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "VERTEX"
    scene.view_settings.view_transform = "Standard"
    scene.render.film_transparent = False
    try:
        me.color_attributes.active_color = attr
    except Exception:  # noqa: BLE001
        pass
    lo, hi = V.min(axis=0), V.max(axis=0)
    centre, extent = (lo + hi) / 2, float(max(hi - lo))
    out = {}
    states = rest_pose(arm)
    scene.frame_set(scene.frame_current)
    arm.hide_render = True
    for label_, d in VIEWS:
        cam = _camera(bpy, scene, centre, extent, d)
        fn = os.path.join(out_dir, "hanging_" + label_.lower().replace(" ", "_").replace("-", "_") + ".png")
        scene.render.filepath = fn
        bpy.ops.render.render(write_still=True)
        out[label_] = {"file": os.path.basename(fn),
                       "lines": [[_project(cam, scene, q) for q in c] for p in parts for c in p["columns"]]}
    me.color_attributes.remove(attr)
    if hattr is not None:
        hair_obj.data.color_attributes.remove(hattr)
    scene.display.shading.color_type = "MATERIAL"
    for t, m in states:
        t.mute = m
    return out


def render_chains(bpy, scene, mesh, arm, chains, out_dir):  # pragma: no cover - Blender
    return None


if __name__ == "__main__":
    main()
