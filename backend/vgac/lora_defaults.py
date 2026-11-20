"""Which of the person's own LoRAs start on, per kind of asset and per step (System > Your library).

Each of the person's LoRAs may carry `start_on` in my_addons.json: {"categories": [category ids], "uses": [step settings]}.
A painter LoRA (stage "concept") has one use, the step 1 LoRA list (`addons`); a picture-edit LoRA lists the step settings
it starts on in (reference.EDIT_ADDON_KNOBS: refine and combined photos, side views, pose redraw, face close-up).

The defaults are filled in once, when a version is made (pipeline.new_version): a LoRA setting the new version does not
have (None) gets the defaults for the asset's category, so the run records what it used and later versions inherit it.
A setting that is there, even empty ({} = none of your LoRAs), is the person's choice and is never changed. With no
defaults nothing is written, so the version is exactly as it was before.
"""

from __future__ import annotations

from typing import Any

from . import reference

CONCEPT_USE = "addons"
USES = (CONCEPT_USE, *reference.EDIT_ADDON_KNOBS)


def lora_knobs(stage: str, category: str | None) -> list[str]:
    """The LoRA settings of a step that apply to this kind of asset (the step's schema, with its categories)."""
    out = []
    for p in reference.STAGE_SCHEMAS.get(stage, []):
        if p["type"] not in ("addons", "edit_addons"):
            continue
        cats = p.get("categories")
        if cats and category not in cats:
            continue
        out.append(p["name"])
    return out


def _uses_of(a: dict[str, Any]) -> list[str]:
    """The step settings a LoRA can start on: the step 1 list for a painter LoRA, the picture-edit uses it names otherwise."""
    so = a.get("start_on") or {}
    if a.get("stage", "concept") == "concept":
        return [CONCEPT_USE]
    return [u for u in so.get("uses") or [] if u in reference.EDIT_ADDON_KNOBS]


def starts_on(a: dict[str, Any], knob: str, category: str | None) -> bool:
    so = a.get("start_on") or {}
    return bool(category) and category in (so.get("categories") or []) and knob in _uses_of(a)


def defaults_for(knob: str, category: str | None) -> dict[str, str]:
    """{lora id: strength} for the person's LoRAs that start on in this step setting for this kind of asset. A picture-edit
    use keeps at most reference.MAX_EDIT_LORAS (the first on the list; saving refuses more, so this only guards old files)."""
    out: dict[str, str] = {}
    for a in reference.addons():
        if not a.get("mine") or not starts_on(a, knob, category):
            continue
        out[a["id"]] = a.get("default_strength") or "normal"
    if knob in reference.EDIT_ADDON_KNOBS:
        out = dict(list(out.items())[: reference.MAX_EDIT_LORAS])
    return out


def fill(stage: str, category: str | None, params: dict[str, Any], knobs: list[str] | None = None) -> dict[str, Any]:
    """`params` with the defaults written into every LoRA setting of the step it does not have. A new dict."""
    out = dict(params or {})
    for knob in knobs if knobs is not None else lora_knobs(stage, category):
        mark = by_default_key(knob)
        d = defaults_for(knob, category)
        if out.get(knob) is None:
            if d:
                out[knob] = d
                out[mark] = list(d)
            continue
        if mark in out:
            # A LoRA stays "on by default" only while it is still on at the strength its default gives it; one the person
            # changed or switched off is their own choice from then on (the list only ever shrinks).
            choice = out[knob] if isinstance(out[knob], dict) else {}
            kept = [i for i in out.get(mark) or [] if i in d and choice.get(i) == d[i]]
            out[mark] = kept  # an empty list (not a missing key) so a stage that merges its parent's settings sees it too
    return out


def by_default_key(knob: str) -> str:
    """The version setting that lists the LoRAs of `knob` that are on because of their default, not chosen on the asset."""
    return f"{knob}_by_default"


def clean_start_on(a: dict[str, Any], categories: Any, uses: Any) -> dict[str, list[str]]:
    """The checked `start_on` for one LoRA -> {categories, uses}. ValueError with a plain sentence when it cannot be saved:
    an unknown kind of asset or step, a picture-edit LoRA with kinds but no step, or a step and kind that would have more
    LoRAs on than one step can take."""
    known = [c["id"] for c in reference.categories()]
    if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
        raise ValueError("Choose the kinds of assets from the list.")
    cats = [c for c in dict.fromkeys(categories)]
    if any(c not in known for c in cats):
        raise ValueError("Choose the kinds of assets from the list.")
    edit = a.get("stage", "concept") == "edit"
    if not isinstance(uses, list) or not all(isinstance(u, str) for u in uses):
        raise ValueError("Choose the steps from the list.")
    us = [u for u in dict.fromkeys(uses)]
    if edit:
        if any(u not in reference.EDIT_ADDON_KNOBS for u in us):
            raise ValueError("Choose the steps from the list.")
        if cats and not us:
            raise ValueError("Choose at least one step it starts on in.")
    else:
        us = []
    if edit:
        for u in us:
            for c in cats:
                others = [o for o in reference.addons() if o.get("mine") and o["id"] != a["id"] and starts_on(o, u, c)]
                if len(others) >= reference.MAX_EDIT_LORAS:
                    names = " and ".join(o.get("label") or "another LoRA" for o in others)
                    raise ValueError(f"{names} are already on by default for {category_plural(c)} in {USE_LABELS[u]}. One step takes at most "
                                     f"{reference.MAX_EDIT_LORAS} of your LoRAs; turn one of them off there first.")
    return {"categories": cats, "uses": us}


def category_plural(cid: str) -> str:
    """The app's plain word for a kind of asset, in the plural ("characters", "props"): reference.CATEGORY_NOUNS."""
    noun = reference.CATEGORY_NOUNS.get(cid)
    if noun:
        return noun + "s"
    try:
        return str(reference.category(cid).get("label") or cid)
    except KeyError:
        return cid


# The step each LoRA setting belongs to, in the words the step settings use.
USE_LABELS = {CONCEPT_USE: "the first picture", "edit_addons": "refines and combined photos", "view_addons": "the side views",
              "pose_addons": "the pose redraw", "face_addons": "the face close-up"}
