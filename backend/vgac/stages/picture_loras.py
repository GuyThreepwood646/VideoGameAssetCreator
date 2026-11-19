"""The person's own LoRAs for the picture-edit uses (docs/PLAN-qwen-edit-loras-2026-11-18.md, section 5).

Each use has its own choice param, so a LoRA picked for one use never reaches another:
  edit_addons   refine (step 1)                  -> stages/concept.py refine
  view_addons   step 2 views (every method)      -> stages/turnaround.py
  pose_addons   the pose redraw for 3D           -> stages/turnaround.py
  face_addons   the face close-up only           -> stages/face_detail.py

edit_loras resolves a choice against the registry (stage "edit" only), checks ComfyUI's LoRA list BEFORE the first job
(the job's own card must answer; another card is checked when it answers and skipped when it does not, because it can
only be lent while its engine is running), records what was applied and ignored (on the version as <param>_applied,
and in the metrics), and returns the applied entries for the graph builders. With no choice it returns [] and makes no
engine call, so the step runs exactly as it did before.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .. import reference
from ..graphs.concept import lora_name
from ..preflight import combo_choices

# A card that is briefly busy or restarting gets one more question after this many seconds before the step gives up.
LORA_RETRY_WAIT_S = 5.0
ENGINE_NO_ANSWER = ("The graphics engine this step runs on did not answer, so your LoRAs cannot be checked. "
                    "Start ComfyUI on that card and run the step again.")


class LoraCheckBug(RuntimeError):
    """A code fault inside the LoRA check (not an engine that did not answer): the step stops with the fault's own text,
    and the face pass re-raises it instead of recording it as a skipped close-up."""


NO_ANSWER = (ConnectionError, TimeoutError, httpx.TransportError)  # the engine did not answer: connection or timeout


class LoraCheckFailed(RuntimeError):
    """The job's own card did not answer the LoRA check, twice. The step records it (face_detail: the close-up is not
    drawn and the prep shows the face_lora check) instead of carrying on as if the LoRA had been used."""


def _must_list(info: dict[str, Any] | None, applied: list[dict[str, Any]], card: str | None) -> None:
    """Stop when a card that answered does not list one of the applied LoRA files. The message names only the LoRAs the
    person chose, never other files on the machine."""
    listed = combo_choices(info, "lora_name")
    if listed is None:
        raise RuntimeError("ComfyUI did not list its LoRA files, so your LoRAs cannot be checked. Try the step again.")
    missing = [a for a in applied if lora_name(a["file"]) not in listed]
    if missing:
        names = ", ".join(a.get("label") or a["id"] for a in missing)
        raise RuntimeError(f"ComfyUI does not list your LoRA file for {names}. Check that the file is in the LoRA folder "
                           "ComfyUI reads, restart ComfyUI, then run this step again.")


async def _check_cards(sc: Any, applied: list[dict[str, Any]], card: str | None = None) -> None:
    """The job's own card must answer and list the files. Every other card of the job is checked only if it answers:
    one that is off cannot take work, so the step never needs its file."""
    home = card or getattr(getattr(sc, "ctx", None), "instance", None)
    info = None
    for attempt in range(2):
        try:
            info = await sc.client(home).object_info("LoraLoaderModelOnly")
            break
        except NO_ANSWER as e:  # the card did not answer (connection or timeout): retried once, then a plain stop
            if attempt == 0:
                await asyncio.sleep(LORA_RETRY_WAIT_S)
            else:
                raise LoraCheckFailed(ENGINE_NO_ANSWER) from e
        except Exception as e:  # a code fault: its own text, never the card message
            raise LoraCheckBug(f"The LoRA check could not run: {e}") from e
    _must_list(info, applied, home)
    for card in list((getattr(getattr(sc, "ctx", None), "clients", None) or {}).keys()):
        if card == home:
            continue
        try:
            info = await sc.client(card).object_info("LoraLoaderModelOnly")
        except NO_ANSWER:  # an engine that does not answer is not lent, so its file is not needed
            continue
        except Exception as e:  # a code fault: its own text
            raise LoraCheckBug(f"The LoRA check could not run: {e}") from e
        _must_list(info, applied, card)


def chosen_ids(params: dict[str, Any], param: str) -> list[str]:
    """The ids of the LoRAs a choice applies, without asking any engine (for comparing with a record)."""
    applied = reference.resolve_addons(reference.EDIT_MODEL, "edit", None, None, params.get(param) or None)["applied"]
    return [a["id"] for a in applied]


async def edit_loras(sc: Any, param: str, params: dict[str, Any], card: str | None = None) -> list[dict[str, Any]]:
    """The applied entries (reference.resolve_addons shape) for one picture-edit use, or [] when nothing is chosen.
    `card`: the ComfyUI instance the picture is drawn on, when the job itself does not run on one (the clean-up runs on
    the host, but its close-up is drawn on a card); the check asks that card, never the host."""
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
    await _check_cards(sc, applied, card)
    for a in applied:
        sc.models_used[f"addon:{a['id']}"] = reference.addon_record(a["id"])
    return applied
