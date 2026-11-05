"""More triangles on the head (docs/HAIR-RESEARCH.md stage 1, option i): weighted decimation.

The 3D builder decimates to the triangle budget by shape error alone, so the head, where players look, ends up with
about 6 percent of the triangles (the knight: 1,241 of 19,594) and the outline of the hair shows straight segments.
For the looks and categories where it helps, the 3D step asks the builder for HEAD_ROOM times its usual target, and
the clean-up step decimates back to the platform budget in Blender with a vertex group on the head (the Decimate
modifier's collapse mode raises the collapse cost of the group's edges), so the head keeps about HEAD_GAIN times the
triangles an even decimation leaves it and the body pays for them. The total stays inside the budget; the 3D step's
model is over the budget on purpose and its check says so.

Who gets it (applies): humanoids whose look keeps the full triangle budget, smooth shading and smooth textures
(Painted, Cel-shaded, Clay, Photoreal and custom looks). Not Low-poly (flat shading, where even triangle size is the
look), Pixel-art (crisp texels) or Retro 64 (a fraction of the budget). Only when the 3D step chose its own target
(a person's own triangle count is never raised) and with the TRELLIS builder.

The head is the top HEAD_SHARE of the model's height, the same rule as face.find_head; blender/prep.py builds the
vertex group from it with a short fade below the cut so the density does not step at the neck.
"""

from __future__ import annotations

from typing import Any

# How much more than its usual target the 3D step asks the builder for. The builder spreads triangles by shape error
# at any count, so the head only has what the larger model gives it: on the knight (same seed) the head had 1,241 at
# the budget, 1,615 at 1.3 times and 2,439 at 2 times. Twice its share needs 2 times; the build took no longer
# (117 s against 116 s at the budget) and the decimation in Blender about a second (runtime/tmp/headtris).
HEAD_ROOM = 2.0
# The head's triangles after the weighted decimation, as a multiple of what an even decimation leaves it.
HEAD_GAIN = 2.0
# The top share of the model's height that is the head (face.find_head's default).
HEAD_SHARE = 0.13
# The clean-up option, on unless the version turns it off.
PARAM = "head_triangles"


def applies(rec: dict[str, Any] | None, category_id: str | None) -> bool:
    """Whether the look and the category get more triangles on the head: humanoids whose recipe keeps the full
    triangle budget with smooth shading and smooth (not crisp) textures."""
    if category_id != "humanoid":
        return False
    r = rec or {}
    mesh, srf = r.get("mesh") or {}, r.get("surface") or {}
    if float(mesh.get("triangle_scale") or 1.0) < 1.0:
        return False  # Pixel-art, Retro 64 and Low-poly ask for a fraction of the budget
    if (srf.get("shading") or "smooth") != "smooth":
        return False  # flat shading: even triangle size is the look
    if srf.get("texture_filter") == "crisp":
        return False
    return True


def on(params: dict[str, Any] | None) -> bool:
    """The clean-up option (head_triangles), on unless turned off."""
    return (params or {}).get(PARAM) is not False


def room_target(base_target: int) -> int:
    """The 3D step's triangle target with room for the head: HEAD_ROOM times the usual target."""
    return int(round(int(base_target) * HEAD_ROOM))


def mesh_room(params: dict[str, Any] | None) -> float | None:
    """The head room a 3D-step version was built with (its head_room param), or None for a model built at the budget
    (every version made before this existed, a chosen triangle count, another builder)."""
    v = (params or {}).get("head_room")
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def mesh_limit(params: dict[str, Any] | None, platform: int | None) -> int | None:
    """The triangle count a 3D-step model with head room is judged against: its own target (over the platform budget
    on purpose). None when the version has no head room (the usual budget then applies)."""
    if not mesh_room(params) or not platform:
        return None
    t = (params or {}).get("target_faces")
    return int(t) if t else int(round(int(platform) * float(mesh_room(params))))


def plan(rec: dict[str, Any] | None, category_id: str | None, prep_params: dict[str, Any] | None, shading: str,
         mesh_params: dict[str, Any] | None) -> dict[str, Any]:
    """What the clean-up step does with the head: {weighted, reason}. weighted is True when the option is on, the look
    and category qualify, the shading is smooth and the 3D model was built with head room. A model built at the budget
    is left exactly as before (prep does not decimate it at all)."""
    if not on(prep_params):
        return {"weighted": False, "reason": "off"}
    if not applies(rec, category_id):
        return {"weighted": False, "reason": "look"}
    if shading != "smooth":
        return {"weighted": False, "reason": "flat"}
    if not mesh_room(mesh_params):
        return {"weighted": False, "reason": "no_room"}
    return {"weighted": True, "reason": None}


def handoff_problem(mesh_params: dict[str, Any] | None, mesh_triangles: Any, prep_triangles: Any, platform: int | None) -> str | None:
    """Why a model must not be handed on (to the rig step): the 3D model is over the budget on purpose and no clean-up
    model under the budget stands between it and the rig. None when it may go on."""
    if not platform or not mesh_room(mesh_params):
        return None
    limit = int(platform)
    if prep_triangles is not None:
        try:
            if int(prep_triangles) <= limit:
                return None
        except (TypeError, ValueError):
            pass
        return (f"The cleaned-up model has {int(prep_triangles):,} triangles, more than the {limit:,} your platform allows. "
                "Run Clean up for the game again before making it move.")
    try:
        if mesh_triangles is not None and int(mesh_triangles) <= limit:
            return None
    except (TypeError, ValueError):
        pass
    return (f"The 3D model has more triangles than your platform's {limit:,} on purpose, so the clean-up step can give the head more of them. "
            "Run Clean up for the game first; it brings the model back to the budget.")


def head_vars(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    """The clean-up triangle_budget check's extra values for the head line, or None when the weighted decimation did
    not run: head_triangles, head_even (what an even decimation would have left it), head_share (of the total)."""
    m = metrics or {}
    if not m.get("head_weighted") or m.get("head_triangles") is None:
        return None
    total = m.get("triangles") or 0
    out = {"head_weighted": True, "head_triangles": int(m["head_triangles"])}
    if m.get("head_triangles_even") is not None:
        out["head_even"] = int(m["head_triangles_even"])
    if total:
        out["head_share"] = round(int(m["head_triangles"]) / float(total), 4)
    return out

