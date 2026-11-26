"""Triangle floor (PL-131): the fewest triangles a model can be cut to before its surface moves more than the picture
can show, read from one descent of the reduction (blender/trifloor.py).

The picture's pixel size, as a share of the model's bounding diagonal, is 1 / picture_px. The floor is the triangle count
where the 95th percentile of the surface distance first passes `error_px` pixels, read between two steps of the descent
on a log-log line. The limit prep cuts to is the larger of the look's target and the floor, never over the platform's
own maximum.
"""
import math
from typing import Any

# The descent is only believed where it has a step both under and over the allowed error; below the last step (500
# triangles) a model is not worth cutting to anyway.
MIN_TRIANGLES = 300


def floor_from_steps(steps: list[dict[str, float]], picture_px: int, error_px: float) -> int | None:
    """The floor in triangles, or None when the descent cannot say (no steps, or the error never reaches the allowance:
    then the model holds its shape all the way down and any count above MIN_TRIANGLES is fine)."""
    if not steps or picture_px <= 0 or error_px <= 0:
        return None
    allowed = error_px / float(picture_px)
    pts = sorted(((int(s["tris"]), float(s["p95"])) for s in steps if s.get("tris") and s.get("p95") is not None), reverse=True)
    if not pts:
        return None
    if pts[-1][1] <= allowed:
        return MIN_TRIANGLES
    if pts[0][1] > allowed:
        return pts[0][0]  # even the first step is over: nothing under it is allowed
    for (n_hi, e_hi), (n_lo, e_lo) in zip(pts, pts[1:]):
        if e_hi <= allowed < e_lo:
            if e_hi <= 0 or e_lo <= e_hi:
                return n_hi
            frac = (math.log(allowed) - math.log(e_hi)) / (math.log(e_lo) - math.log(e_hi))
            return int(round(math.exp(math.log(n_hi) + frac * (math.log(n_lo) - math.log(n_hi)))))
    return None


def limit_with_floor(target: int, floor: int | None, platform_max: int) -> dict[str, Any]:
    """{limit, floor, state}: state is "none" (no floor read), "ok" (target already holds the shape), "raised" (the
    target is under the floor; cut to the floor) or "over_max" (the floor is over the platform's maximum; cut to that)."""
    if not floor:
        return {"limit": int(target), "floor": None, "state": "none"}
    if floor <= target:
        return {"limit": int(target), "floor": int(floor), "state": "ok"}
    if floor > platform_max:
        return {"limit": int(max(target, platform_max)), "floor": int(floor), "state": "over_max"}
    return {"limit": int(floor), "floor": int(floor), "state": "raised"}
