"""Blender headless: sleeve skin pass on a rigged GLB (WP-79), run last in the rig step's skin passes.

Run: blender -b --python sleeve_skin.py -- <rig.glb> <out.glb> --report <json>

A robe with a bell sleeve tears at the sleeve in the moves: the mage's cuff and forearm skin was measured 100 times
longer than at rest in the attack (5 mm edges over 0.7 m). Two causes, both on forearm skin only (vertices with at
least ARM_MASS_MIN of their weight on the arm chain: shoulder, upper arm, forearm, forearm twist, hand, fingers):
1. Leg weight on arm skin. A sleeve that hangs against the skirt took thigh and calf weight from the generator, so the
   forearm followed the legs. Each such vertex gives its leg weight (thigh, calf, foot, toe) to its strongest arm-chain
   bone.
   The other way round, skirt or leg skin (under half arm chain, at least LEG_MASS_MIN on the legs) gives away the
   forearm weight it picked up from the sleeve lying against it, to its strongest leg bone.
A third step that cut the mesh where the sleeve is welded to the skirt was tried and dropped: a cut turns the stretch into a
gap of the same size, and it opened a 14 to 34 mm see-through slit in walk, run and death (the copy is in bakeoff/mage_sleeve).
Known limit: a bell sleeve welded to the skirt keeps its stretched bridge at the cuff. The rest mesh, the
bones, the other weights and the animation are not touched. Only a skeleton with forearm bones is touched.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

ARM_MASS_MIN = 0.5  # share of a vertex's weight on the arm chain from which it is arm skin
LEG_MASS_MIN = 0.3  # share of a vertex's weight on leg bones from which it is skirt or leg skin, not sleeve
FORE_NOISE = 0.005  # forearm weight under this on skirt skin is left as it is
MAX_INFLUENCES = 4


def bone_kind(name):
    """'fore' for forearm, twist, hand and finger bones, 'arm' for shoulder and upper arm, 'leg' for thigh, calf, foot
    and toe bones, else None. Mixamo prefixes and left or right do not matter; spring and hair chain bones are None."""
    n = str(name or "").replace("mixamorig:", "").lower()
    if "spring" in n or "hair" in n:
        return None
    if "forearm" in n or "twist" in n or "hand" in n or any(k in n for k in ("thumb", "index", "middle", "ring", "pinky")):
        return "fore"
    if "arm" in n or "shoulder" in n or "clavicle" in n:
        return "arm"
    if any(k in n for k in ("upleg", "thigh", "calf", "foot", "toe", "ankle")) or n.endswith("leg") or n.startswith("leg"):
        return "leg"
    return None


def kind_columns(names):
    """{'fore': [cols], 'arm': [...], 'leg': [...]} for a list of vertex group names (arm here is the upper arm and
    collarbone only)."""
    out = {"fore": [], "arm": [], "leg": []}
    for i, n in enumerate(names):
        k = bone_kind(n)
        if k:
            out[k].append(i)
    return out


def arm_chain_share(W, cols):
    """Per vertex, the share of its weight on the arm chain columns (forearm group and upper arm group together)."""
    W = np.asarray(W, dtype=float)
    if not cols["fore"] and not cols["arm"]:
        return np.zeros(W.shape[0])
    both = cols["fore"] + cols["arm"]
    total = W.sum(axis=1)
    return W[:, both].sum(axis=1) / np.maximum(total, 1e-12)


def fore_share(W, cols):
    """Per vertex, the share of its weight on the forearm group (forearm, twist, hand, fingers)."""
    W = np.asarray(W, dtype=float)
    if not cols["fore"]:
        return np.zeros(W.shape[0])
    return W[:, cols["fore"]].sum(axis=1) / np.maximum(W.sum(axis=1), 1e-12)


def hand_over_leg_weight(W, cols, mass_min=ARM_MASS_MIN):
    """On arm skin (arm-chain share at least mass_min) move the leg columns' weight to the strongest arm-chain column of
    the vertex. Returns (new W, number of vertices changed). Row sums are unchanged."""
    W = np.array(W, dtype=float, copy=True)
    both = cols["fore"] + cols["arm"]
    if not both or not cols["leg"]:
        return W, 0
    skin = arm_chain_share(W, cols) >= mass_min
    leg = W[:, cols["leg"]].sum(axis=1)
    rows = np.where(skin & (leg > 1e-9))[0]
    if len(rows) == 0:
        return W, 0
    best = np.array(both)[np.argmax(W[np.ix_(rows, both)], axis=1)]
    W[np.ix_(rows, cols["leg"])] = 0.0
    W[rows, best] += leg[rows]
    return W, int(len(rows))


def hand_over_fore_weight(W, cols, leg_min=LEG_MASS_MIN, mass_min=ARM_MASS_MIN):
    """The other way round: skin that is not arm skin (arm-chain share under mass_min) and sits on the legs (leg share at
    least leg_min), the skirt hem that a sleeve lies against, gives its forearm-group weight to its strongest leg column.
    Upper-arm and collarbone weight is left alone. Returns (new W, number of vertices changed). Row sums are unchanged."""
    W = np.array(W, dtype=float, copy=True)
    if not cols["fore"] or not cols["leg"]:
        return W, 0
    total = np.maximum(W.sum(axis=1), 1e-12)
    fore = W[:, cols["fore"]].sum(axis=1)
    leg = W[:, cols["leg"]].sum(axis=1) / total
    rows = np.where((arm_chain_share(W, cols) < mass_min) & (leg >= leg_min) & (fore > FORE_NOISE))[0]
    if len(rows) == 0:
        return W, 0
    best = np.array(cols["leg"])[np.argmax(W[np.ix_(rows, cols["leg"])], axis=1)]
    W[np.ix_(rows, cols["fore"])] = 0.0
    W[rows, best] += fore[rows]
    return W, int(len(rows))


def limit_influences(weights, keep=MAX_INFLUENCES):
    """{group: weight} cut to the `keep` largest and normalised to 1 (an empty or zero row stays as it is)."""
    top = sorted(((g, w) for g, w in weights.items() if w > 1e-9), key=lambda gw: -gw[1])[:keep]
    s = sum(w for _, w in top)
    return {g: w / s for g, w in top} if s > 1e-12 else dict(weights)


# ---- Blender side -------------------------------------------------------------------------------------------------


def _process_mesh(o, report):  # pragma: no cover - runs inside Blender
    import bmesh

    names = [g.name for g in o.vertex_groups]
    cols = kind_columns(names)
    if not cols["fore"]:
        return False
    me = o.data
    bm = bmesh.new()
    bm.from_mesh(me)
    dl = bm.verts.layers.deform.active
    if dl is None:
        bm.free()
        return False
    bm.verts.ensure_lookup_table()
    V, G = len(bm.verts), len(names)
    W = np.zeros((V, G))
    for v in bm.verts:
        for g, w in v[dl].items():
            if g < G:
                W[v.index, g] = w
    W2, n_leg = hand_over_leg_weight(W, cols)
    W2, n_fore = hand_over_fore_weight(W2, cols)
    n_leg += n_fore
    changed = n_leg > 0
    if n_leg:
        for i in np.where(np.abs(W2 - W).sum(axis=1) > 1e-9)[0]:
            row = limit_influences({g: float(W2[i, g]) for g in np.nonzero(W2[i] > 1e-9)[0]})
            for g in list(bm.verts[i][dl].keys()):
                del bm.verts[i][dl][g]
            for g, w in row.items():
                bm.verts[i][dl][int(g)] = w
    report["leg_weight_vertices"] += n_leg
    if changed:
        bm.to_mesh(me)
        me.update()
    bm.free()
    return changed


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "leg_weight_vertices": 0, "changed": 0}
    if arm is None or not meshes:
        report["reason"] = "no armature or no skinned mesh"
    elif not any(kind_columns([g.name for g in o.vertex_groups])["fore"] for o in meshes):
        report["reason"] = "no forearm bones"
    else:
        any_changed = False
        for o in meshes:
            any_changed = _process_mesh(o, report) or any_changed
        report["ok"] = True
        report["changed"] = int(any_changed)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"] and report["changed"]:
        bpy.ops.object.select_all(action="DESELECT")
        keep_objs = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep_objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep_objs[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("SLEEVE_SKIN_OK" if report["ok"] else "SLEEVE_SKIN_SKIPPED", report.get("reason", ""), report["changed"],
          report["leg_weight_vertices"])


if __name__ == "__main__":  # pragma: no cover
    main()
