"""The person's own LoRAs for the picture-edit uses (docs/PLAN-qwen-edit-loras-2026-11-18.md, section 5).

Each use has its own choice param, so a LoRA picked for one use never reaches another:
  edit_addons   refine (step 1)                  -> stages/concept.py refine
  view_addons   step 2 views (every method)      -> stages/turnaround.py
  pose_addons   the pose redraw for 3D           -> stages/turnaround.py
  face_addons   the face close-up only           -> stages/face_detail.py

edit_loras resolves a choice against the registry (stage "edit" only), checks that ComfyUI lists each applied file on
every card the step may use BEFORE the first job, records what was applied and ignored (on the version as
<param>_applied, and in the metrics), and returns the applied entries for the graph builders. With no choice it returns
[] and makes no engine call, so the step runs exactly as it did before.
"""

from __future__ import annotations

from typing import Any

from .. import reference
from ..graphs.concept import lora_name
from ..preflight import combo_choices


def _all_cards(sc: Any) -> list[str | None]:
    """Every card this job may use (a lent card can be any of them); None (the job's own card) when none are known."""
    names = list((getattr(getattr(sc, "ctx", None), "clients", None) or {}).keys())
    return names or [None]


async def _check_listed(sc: Any, card: str | None, applied: list[dict[str, Any]]) -> None:
    """Stop before any job when this card's ComfyUI does not list one of the applied LoRA files. The message names
    only the LoRAs the person chose, never other files on the machine."""
    listed = combo_choices(await sc.client(card).object_info("LoraLoaderModelOnly"), "lora_name")
    if listed is None:
        raise RuntimeError("ComfyUI did not list its LoRA files, so your LoRAs cannot be checked. Try the step again.")
    missing = [a for a in applied if lora_name(a["file"]) not in listed]
    if missing:
        names = ", ".join(a.get("label") or a["id"] for a in missing)
        raise RuntimeError(f"ComfyUI does not list your LoRA file for {names}. Check that the file is in the LoRA folder "
                           "ComfyUI reads, restart ComfyUI, then run this step again.")


async def edit_loras(sc: Any, param: str, params: dict[str, Any], cards: list[str | None] | None = None) -> list[dict[str, Any]]:
    """The applied entries (reference.resolve_addons shape) for one picture-edit use, or [] when nothing is chosen.
    `cards`: the cards the step may run on (default: every card the job knows)."""
    choices = params.get(param) or None
    if not choices:
        sc.set_params(**{f"{param}_applied": {}})  # a record from an earlier run must not read as this one's
        return []
    active = [aid for aid, c in choices.items() if str(c).lower() != "off"]
    if len(active) > reference.MAX_EDIT_LORAS:
        raise ValueError(f"at most {reference.MAX_EDIT_LORAS} of your LoRAs can be used at once in one step")
    res = reference.resolve_addons(reference.EDIT_MODEL, "edit", None, None, choices)
    applied = res["applied"]
    sc.set_params(**{f"{param}_applied": {a["id"]: a["strength"] if a["strength"] != "custom" else a["value"] for a in applied}})
    sc.set_metrics(**{param: {"applied": applied, "ignored": res["ignored"], "unknown": res["unknown"]}})
    if not applied:
        return []
    for card in cards or _all_cards(sc):
        await _check_listed(sc, card, applied)
    for a in applied:
        sc.models_used[f"addon:{a['id']}"] = reference.addon_record(a["id"])
    return applied
