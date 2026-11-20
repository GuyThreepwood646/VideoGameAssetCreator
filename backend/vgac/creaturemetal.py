"""Creatures are not metal unless the description says so (WP-65 phase 2, step 3).

The 3D builder spreads metal over a creature's body from a smooth guess (a dog at about 0.44 metal over 98 % of its
surface, a songbird at 0.45, a beetle at 0.60 before the clean-up's sharpening, which then lifted it further). Only the
description can say a creature is metal, so, for the creature categories only (quadruped, winged, multileg, serpent):

- keep: the description names metal or armour (skinmetal.metal_body, skinmetal.armoured, or a metal-look word below).
  The builder's metal stays as it is. Examples: "iridescent beetle", "a clockwork hawk", "steel-plated crab".
- shine: a shine word (shiny, glossy, wet, slick, polished, lacquered, glassy) and no metal word. Metal goes to 0 and,
  where the roughness is clearly too high, it is lowered to SHINE_ROUGH_CAP. Examples: "glossy black beetle".
- clear: neither. Metal goes to 0 over the whole creature. Example: "a brown dog".

Vehicles, props, environment, foliage, material and humanoids are not touched here: humanoids have their own skin pass
(skinmetal.clear), and vehicles and props keep the builder's metal. The clear runs after the texture passes, so the
prep sharpening (imaging.sharpen_metallic) cannot raise metal that this clears: zero stays zero under the sharpening.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import face, hair, imaging, skinmetal

CREATURE_CATEGORIES = ("quadruped", "winged", "multileg", "serpent")
SHINE_ROUGH_CAP = 0.35  # shine words: roughness above this is lowered to it (the builder's creatures sit at 0.64 to 0.86)
METAL_LIMIT = 0.5  # the measure: texels over this count as metal (as in skinmetal)

# Words that always say the creature has metal (or armour): a creature described with one of these keeps its metal.
METAL_LOOK = re.compile(r"\b(metallic|metal|chrome|chromed|steel|iron|plated|armou?red|armou?r|mechanical|mechanised|mechanized|"
                        r"clockwork|burnished|iridescent|gilded|plate|chain ?mail|mail|cuirass|breastplate|gauntlets?|greaves|"
                        r"pauldrons?|bracers?|vambraces?)\b", re.I)
# Colour words that are metal only when they are not a colour of fur, feathers, scales or a breed ("golden retriever",
# "silver fox", "copper-coloured fur", "golden feathers" are colours; "a golden dragon" is gold).
COLOUR_METAL = re.compile(r"\b(golden|gold|silver|copper|bronze|brass)\b", re.I)
COLOUR_CONTEXT = re.compile(r"\b(fur|furred|hair|haired|coat|coated|feathers?|feathered|plumage|mane|wool|woolly|fleece|scales?|scaled|"
                            r"pelt|hide|fox|retriever|silverback|tabby|colou?red|hued|dyed|colou?r|breed)\b", re.I)
SHINE = re.compile(r"\b(shiny|glossy|wet|slick|polished|lacquered|glassy)\b", re.I)


def says_metal(description: str | None) -> bool:
    """Whether a creature's description says it is metal: a metal word, or a colour word that is not a colour of fur,
    feathers, scales or a breed. skinmetal.metal_body (robots, golems) counts too."""
    if not description:
        return False
    if skinmetal.metal_body(description) or METAL_LOOK.search(description):
        return True
    return bool(COLOUR_METAL.search(description) and not COLOUR_CONTEXT.search(description))


def mode(category: str | None, description: str | None) -> str | None:
    """'keep', 'shine' or 'clear' for a creature; None when the category is not a creature (the rule does not apply)."""
    if category not in CREATURE_CATEGORIES:
        return None
    if says_metal(description):
        return "keep"
    if description and SHINE.search(description):
        return "shine"
    return "clear"


def apply_maps(metal: np.ndarray, rough: np.ndarray | None, covered: np.ndarray, how: str) -> tuple[np.ndarray, np.ndarray | None]:
    """The new metal (and roughness) maps for a mode. Pure numpy, on the covered texels only."""
    out_m = metal.copy()
    out_r = None if rough is None else rough.copy()
    if how == "keep":
        return out_m, out_r
    out_m[covered] = 0.0
    if how == "shine" and out_r is not None:
        out_r[covered] = np.minimum(out_r[covered], SHINE_ROUGH_CAP)
    return out_m, out_r


def clear(glb: Path | str, maps: dict[str, Path], category: str | None, description: str | None, out_dir: Path) -> dict[str, Any]:
    """The creature rule on the current maps (CPU work, run in a thread). -> {files: {metallic[, roughness]: path}, metrics:
    {creature_metal: {...}}}. Never raises: a failure is recorded and the maps stay as they are."""
    t0 = time.time()
    rec: dict[str, Any] = {"ran": False, "mode": mode(category, description)}
    try:
        if rec["mode"] is None:
            rec["skipped"] = "not a creature"
        elif rec["mode"] == "keep":
            rec.update(ran=True, skipped="the description keeps metal")
        elif not maps.get("metallic"):
            rec["skipped"] = "no metal map"
        else:
            mesh = face.load_mesh(glb)
            met = imaging.read_map(maps["metallic"], grey=True)
            if mesh is None or met is None:
                rec["skipped"] = "no texture layout"
            else:
                T = met.shape[0]
                covered = hair.texel_region(mesh, T, np.ones(len(mesh["faces"]), dtype=bool))["covered"]
                rough = imaging.read_map(maps["roughness"], grey=True) if maps.get("roughness") else None
                if rough is not None and rough.shape[0] != T:
                    rough = None
                before_m = float(met[covered].mean()) if covered.any() else 0.0
                before_r = float(rough[covered].mean()) if (rough is not None and covered.any()) else None
                new_m, new_r = apply_maps(met, rough, covered, rec["mode"])
                changed = int((new_m != met).sum())
                files: dict[str, Path] = {}
                out_dir = Path(out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                if changed:
                    files["metallic"] = imaging.write_map(new_m, out_dir / "creature_metallic.png")
                if rec["mode"] == "shine" and new_r is not None and float((new_r != rough).sum()):
                    files["roughness"] = imaging.write_map(new_r, out_dir / "creature_roughness.png")
                rec.update(ran=True, covered_texels=int(covered.sum()), texels_changed=changed, metal_before=round(before_m, 3),
                           metal_after=round(float(new_m[covered].mean()) if covered.any() else 0.0, 3),
                           roughness_before=None if before_r is None else round(before_r, 3),
                           roughness_after=None if new_r is None else round(float(new_r[covered].mean()), 3),
                           seconds=round(time.time() - t0, 2))
                return {"files": files, "metrics": {"creature_metal": rec}}
    except Exception as e:  # noqa: BLE001  (the maps stay as they are)
        rec.update(ran=False, error=str(e)[:300])
    rec["seconds"] = round(time.time() - t0, 2)
    return {"files": {}, "metrics": {"creature_metal": rec}}
