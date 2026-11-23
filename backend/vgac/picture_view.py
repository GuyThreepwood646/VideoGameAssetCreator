"""The angle the step 1 picture is known to be seen from, when it is known.

Step 2's angles model (graphs/qwen_edit.py) treats the picture it is given as the front: asked for the "front view" of a
house or a dog drawn from a corner, it gives the same picture back and turns every other view from there (golden set,
2026-11-22: the drawn front was the step 1 picture unchanged on 9 of 10 builds). So the views are only right when the step 1
picture is a square-on front, and the later steps may only assume that when the app drew the picture from wording that
asks for one.

A category whose step 1 wording asks for a level, square-on front says so with `picture_view: front` in its
data/categories file. The concept step records the view on the version it painted (params.picture_view), only when the
category's wording was in the prompt. A held item's own wording, a prompt without the category's wording, and every
picture the person gave (an upload, an edit of one, a picture combined from their photos) are unknown: their angle is
not assumed, and whatever measures it must search widely.
"""

from __future__ import annotations

from typing import Any

FRONT = "front"
KNOWN_VIEWS = {FRONT: {"view": FRONT, "turn": 0.0, "tilt": 0.0}}
# Pictures the person made or chose: the app did not ask for their angle.
PERSON_SOURCES = ("upload", "upload_edit", "compose", "compose_edit")


def asked(category: dict[str, Any] | None, category_wording_used: bool) -> str | None:
    """The view the step 1 prompt asked for: the category's picture_view when its wording was in the prompt, else None."""
    if not category_wording_used:
        return None
    view = (category or {}).get("picture_view")
    return view if view in KNOWN_VIEWS else None


def assumed(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """{view, turn, tilt} the step 1 version's picture may be assumed to be seen from, or None when it is not known (a
    picture the person gave, or one painted without wording that asks for a known view)."""
    p = params or {}
    if p.get("source") in PERSON_SOURCES:
        return None
    view = p.get("picture_view")
    return dict(KNOWN_VIEWS[view]) if view in KNOWN_VIEWS else None
