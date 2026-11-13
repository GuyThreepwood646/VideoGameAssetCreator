"""Hair from the library (ready-made hairstyles fitted to the head): the per-character choice, when it can be offered,
which style a build uses, and the API (docs/HAIR-LIBRARY-PLAN.md sections 1, 3 and 5).

The choice lives on the asset (Asset.hair: {source: "library"|"model", pick, picked_at, changed_by}); absent means
today's hair, and then nothing in any step changes. Upstream steps (Check all sides, Build the 3D model) read the asset
setting; downstream steps follow the lineage: the 3D version's params.hair_source. An explicit param wins over the
asset setting, and a Fix that sends hair_source "model" to the 3D step writes the setting back (set_choice, "fix").

The library itself (vgac/hairstyles.py), the picture maths (vgac/baldpic.py) and the fit engine (vgac/hairfit.py) are
lane 1's; they are imported where they are used, so this module and every step load without them.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import hairpic
from .db import Asset, Version, get_session

SOURCES = ("model", "library")
LIBRARY = "library"
MODEL = "model"
# Looks the library is not offered for (their hair is one simple solid shape made with the model).
LOOKS_OFF = ("pixel", "retro64", "lowpoly_flat")
CATEGORIES = ("humanoid",)
HAIR_SHARE_MAX = 0.25  # the hair (with its cap) may take at most this share of the platform's triangles
# Triangle counts: a catalogue entry's `triangles` is the hair alone; rank's candidates and attach's metrics count the
# fitted hair plus its scalp cap (blender/hairfit.py: len(hair) + len(cap); attach also gives cap_triangles). Before a
# style is fitted its cap is not known: CAP_MARGIN covers it (lane 1 measured caps of 235 to 264 triangles).
CAP_MARGIN = 400
ALLOWANCE_STEP = 250
PHONE_PLATFORM = "mobile_low"
NOT_INSTALLED = ("The hair library is not installed in this copy of the app. To get it, run setup\\Setup-Extras.ps1 -Packs -Install "
                 "(it downloads the free hairstyle packs and builds the library on this computer; the steps are in docs\\FIRST-TIME-SETUP.md), "
                 "then restart the app. Hairstyles you added yourself still work without it.")
PHONE_TRIANGLES_MAX = 750  # on Phone only shell styles at or under this many triangles
FIT_GOOD = 0.6  # hair_fit: under this nothing fits well
COVERS = ("normal", "more")
STYLE_SOURCES = ("chosen", "picked", "best")
# The prep params a hair-only Clean up may change (only_hair_change).
HAIR_ONLY_KEYS = ("hair_style", "hair_cover")
DATA_DIR = Path(__file__).resolve().parent / "data" / "hairlib"
# Rough minutes for the rebuild line in the panel (trial figures).
REBUILD_MINUTES = {"turnaround": 16, "mesh": 12, "prep": 5}


# --- the choice --------------------------------------------------------------------------------------------------


def choice(asset: Asset | None) -> dict[str, Any] | None:
    return getattr(asset, "hair", None) if asset is not None else None


def asset_source(asset: Asset | None) -> str | None:
    """The asset setting: "library", "model", or None (never set: today's hair)."""
    c = choice(asset) or {}
    s = c.get("source")
    return s if s in SOURCES else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def set_choice(db: Session, asset: Asset, source: str | None = None, pick: Any = ..., changed_by: str = "page") -> dict[str, Any]:
    """Write the asset setting. source None keeps the current source; pick ... keeps the current pick."""
    c = dict(choice(asset) or {"source": MODEL, "pick": None})
    if source is not None:
        if source not in SOURCES:
            raise ValueError(f"hair source must be one of {SOURCES}")
        c["source"] = source
    if pick is not ...:
        c["pick"] = pick
        c["picked_at"] = now_iso() if pick else None
    c.setdefault("pick", None)
    c["changed_by"] = changed_by
    asset.hair = c
    db.add(asset)
    db.commit()
    return c


def _hairstyles():
    from . import hairstyles  # lane 1

    return hairstyles


def _installed(hs: Any) -> bool:
    try:
        return bool(hs.installed()) if hasattr(hs, "installed") else True
    except Exception:  # noqa: BLE001
        return False


def _look_of(asset: Asset) -> str:
    return str(getattr(asset, "style_preset", "") or "")


def available(asset: Asset, look: str | None = None, platform: str | None = None) -> str | None:
    """A plain reason when the library cannot be used for this asset, else None."""
    if getattr(asset, "category", None) not in CATEGORIES:
        return "Hair from the library is for people and other humanoid characters only."
    look = look or _look_of(asset)
    if look in LOOKS_OFF:
        return "Hair from the library is not offered for the Pixel-art, Retro 64 and Low-poly looks."
    t = hairpic.norm(getattr(asset, "prompt", "") or "")
    if hairpic.any_of(t, hairpic.BALD_WORDS):
        return "The description says the character is bald."
    if hairpic.covered(t):
        return "The description covers the head (a helmet, hood or hat), so no hairstyle would show."
    try:
        hs = _hairstyles()
        family, _words = hs.family_of(getattr(asset, "prompt", "") or "")
        platform = platform or getattr(asset, "platform_target", None)
        styles = [s for s in hs.styles_for(None, platform, look) or []]
    except Exception:  # noqa: BLE001  (no library installed or it could not be read)
        return NOT_INSTALLED
    if not styles and not _installed(hs):
        return NOT_INSTALLED  # no built-in library and none of the person's own hairstyles either
    if platform == PHONE_PLATFORM:
        styles = [s for s in styles if phone_ok(s)]
        if not styles:
            return "No hairstyle in the library is light enough for Phone."
    budget = _platform_triangles(asset, platform)
    if styles and budget and not any(fits_budget(s, budget) for s in styles):
        return "Every hairstyle in the library has more triangles than this platform leaves for hair."
    if not styles:
        return "No hairstyle in the library can be used here."
    return None


def _platform_triangles(asset: Asset, platform: str | None) -> int | None:
    try:
        from . import reference

        return int(reference.budget(asset.category, platform).get("triangles") or 0) or None
    except Exception:  # noqa: BLE001
        return None


def phone_ok(style: dict[str, Any]) -> bool:
    """Phone takes shell styles at or under PHONE_TRIANGLES_MAX triangles (or their lite variant); cards never."""
    if style.get("kind") != "shell":
        return False
    return int(style.get("triangles") or 0) <= PHONE_TRIANGLES_MAX or bool(style.get("lite")) or bool(style.get("phone_ok"))


def wants_bald(asset: Asset) -> bool:
    """Check all sides draws the bald pictures: the asset setting is library and the library can be used."""
    return asset_source(asset) == LIBRARY and available(asset) is None


def mesh_source(asset: Asset, explicit: Any, engine: str) -> tuple[str | None, str | None]:
    """The 3D step's hair_source and a note: an explicit param first, then the asset setting (when the library can
    be used); None when neither says anything (today's hair, nothing recorded). Hunyuan3D always builds today's hair."""
    if explicit in SOURCES:
        src = explicit
    elif asset_source(asset) == LIBRARY and available(asset) is None:
        src = LIBRARY
    else:
        src = None
    if src == LIBRARY and engine == "hy3d":
        return MODEL, "Hair from the library is not offered with Hunyuan3D, so the hair is built with the 3D model."
    return src, None


def lineage_source(sc: Any) -> str | None:
    """The hair source of the 3D version this version is built on (the 3D version's own for a 3D version)."""
    v = sc.version if getattr(sc.version, "stage", None) == "mesh" else sc.stage_version("mesh")
    if v is None:
        return None
    s = (v.params or {}).get("hair_source")
    return s if s in SOURCES else None


def is_library(sc: Any) -> bool:
    try:
        return lineage_source(sc) == LIBRARY
    except Exception:  # noqa: BLE001  (a test double without a lineage)
        return False


# --- which style -------------------------------------------------------------------------------------------------


def resolve_style(explicit: Any, asset: Asset | None, mesh_metrics: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """(style id, chosen|picked|best): the explicit param, then the asset's pick, then the 3D step's best."""
    if explicit:
        return str(explicit), "chosen"
    pick = (choice(asset) or {}).get("pick")
    if pick:
        return str(pick), "picked"
    best = ((mesh_metrics or {}).get("hair_library") or {}).get("best")
    if best:
        return str(best), "best"
    return None, None


def candidate(mesh_metrics: dict[str, Any] | None, style_id: str | None) -> dict[str, Any] | None:
    for c in ((mesh_metrics or {}).get("hair_library") or {}).get("candidates") or []:
        if c.get("style") == style_id:
            return c
    return None


def style_triangles(style_id: str | None, mesh_metrics: dict[str, Any] | None) -> int:
    """Hair plus cap for a style: the fitted count when the 3D step ranked it, else the catalogue's hair count plus
    CAP_MARGIN."""
    c = candidate(mesh_metrics, style_id)
    if c and c.get("triangles"):
        return int(c["triangles"])
    if not style_id:
        return 0
    try:
        return catalogue_total(_hairstyles().style(style_id))
    except Exception:  # noqa: BLE001
        return 0


def catalogue_total(style: dict[str, Any]) -> int:
    """A catalogue entry's hair triangles plus CAP_MARGIN for the cap it will get."""
    t = int(style.get("triangles") or 0)
    return t + CAP_MARGIN if t else 0


def fits_budget(style: dict[str, Any], budget_triangles: int | None) -> bool:
    """A style is offered when its hair plus cap margin fits HAIR_SHARE_MAX of the budget (the allowance's own cap)."""
    if not budget_triangles:
        return True
    return catalogue_total(style) <= int(HAIR_SHARE_MAX * int(budget_triangles))


def allowance(budget_triangles: int | None, mesh_metrics: dict[str, Any] | None, style_id: str | None) -> int:
    """The triangles kept back from the body for the hair: the largest count (hair plus cap) among the 3D step's
    candidates and the chosen style, rounded up to ALLOWANCE_STEP, at most HAIR_SHARE_MAX of the budget."""
    counts = [int(c.get("triangles") or 0) for c in ((mesh_metrics or {}).get("hair_library") or {}).get("candidates") or []]
    counts.append(style_triangles(style_id, mesh_metrics))
    top = max(counts or [0])
    if top <= 0:
        return 0
    value = int(math.ceil(top / ALLOWANCE_STEP) * ALLOWANCE_STEP)
    if budget_triangles:
        value = min(value, int(HAIR_SHARE_MAX * int(budget_triangles)))
    return value


def only_hair_change(parent_params: dict[str, Any] | None, params: dict[str, Any] | None, style_triangles_new: int) -> bool:
    """A Clean up adjust that changes only the hairstyle or its cover, with a style that fits the parent's allowance:
    the parent's body files are copied and only the hair is attached again."""
    pp, p = dict(parent_params or {}), dict(params or {})
    if pp.get("hair_source") != LIBRARY:
        return False
    changed = {k for k, v in p.items() if pp.get(k) != v}
    if not changed or not changed <= set(HAIR_ONLY_KEYS):
        return False
    allow = int(pp.get("hair_allowance") or 0)
    return allow > 0 and int(style_triangles_new or 0) <= allow


def cover_of(params: dict[str, Any]) -> str:
    c = params.get("hair_cover")
    return c if c in COVERS else "normal"


def file_sha1(path: Path | str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fit_words(score: Any) -> str:
    try:
        from . import hairfit  # lane 1's words, when it is installed

        return hairfit.fit_words(None if score is None else float(score))
    except Exception:  # noqa: BLE001
        pass
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "not measured"
    if s >= 0.75:
        return "fits the picture well"
    if s >= FIT_GOOD:
        return "fits the picture"
    return "fits the picture poorly"


# --- checks (the sentences are in guidance.CHECK_GUIDE) -------------------------------------------------------------


def fit_check(m: dict[str, Any], step: str, picked: bool = False) -> dict[str, Any] | None:
    """hair_fit: the 3D step's best style (step "mesh"), or the style Clean up used (step "prep")."""
    from . import quality

    hl = (m or {}).get("hair_library") or {}
    if not hl:
        return None
    cands = hl.get("candidates") or []
    if step == "mesh":
        score = hl.get("best_score")
        best = next((c for c in cands if c.get("style") == hl.get("best")), None)
        name = (best or {}).get("name")
        vars_ = {"style_name": name, "family": hl.get("family"), "family_source": hl.get("family_source"), "picked": False,
                 "best_name": name, "candidates": len(cands), "step": "mesh", "eye_cover": (best or {}).get("eye_cover"),
                 "colour_delta": None}
    else:
        if not hl.get("attached"):
            score = None
        else:
            score = hl.get("score")
        vars_ = {"style_name": hl.get("name") or hl.get("style"), "family": hl.get("family"), "family_source": hl.get("family_source"),
                 "picked": bool(picked), "best_name": hl.get("best_name"), "candidates": hl.get("candidates_n"), "step": "prep",
                 "eye_cover": hl.get("eye_cover"), "colour_delta": hl.get("colour_delta")}
    if hl.get("error"):
        vars_["error"] = True
    ok = score is not None and float(score) >= FIT_GOOD
    return quality.check("hair_fit", ok, None if score is None else round(float(score), 3), FIT_GOOD, vars=vars_)


def prep_checks(m: dict[str, Any], budget_triangles: int | None, picked: bool = False) -> list[dict[str, Any]]:
    """hair_fit, hair_skin_through and hair_triangles from the Clean up's hair_library metrics."""
    from . import quality

    hl = (m or {}).get("hair_library") or {}
    if not hl:
        return []
    out: list[dict[str, Any]] = []
    fc = fit_check(m, "prep", picked)
    if fc:
        out.append(fc)
    if not hl.get("attached"):
        return out
    st = hl.get("skin_through") or {}
    bare = (st.get("all", st.get("share")) if isinstance(st, dict) else st)
    if bare is not None:
        out.append(quality.check("hair_skin_through", float(bare) <= 0.02, round(float(bare), 4), 0.02,
                                 vars={"crown": st.get("crown") if isinstance(st, dict) else None,
                                       "sides": st.get("sides") if isinstance(st, dict) else None}))
    hair = int(hl.get("triangles") or 0)  # attach counts the cap in its triangles (cap_triangles is the cap's share)
    body = int((m or {}).get("triangles") or 0)
    if budget_triangles:
        total = body + hair
        out.append(quality.check("hair_triangles", total <= int(budget_triangles), total, int(budget_triangles),
                                 vars={"hair": hair, "body": body, "allowance": hl.get("allowance")}))
    return out


# --- the API -------------------------------------------------------------------------------------------------------

router = APIRouter()


class HairPut(BaseModel):
    source: Literal["model", "library"]


class HairPick(BaseModel):
    style: str
    rebuild: bool = False


def _asset_or_404(db: Session, aid: str) -> Asset:
    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    return a


def _selected(db: Session, aid: str, stage: str) -> Version | None:
    from . import pipeline

    return pipeline.selected(db, aid, stage)


def _url(v: Version, key: str) -> str | None:
    rel = (v.files or {}).get(key)
    return f"/api/library/{rel}" if rel else None


def _credit(style_id: str) -> str | None:
    try:
        return _hairstyles().credit(style_id)
    except Exception:  # noqa: BLE001
        return None


def panel(db: Session, a: Asset) -> dict[str, Any]:
    """GET /api/assets/{aid}/hair."""
    src = asset_source(a) or MODEL
    reason = available(a)
    try:
        family, words = _hairstyles().family_of(a.prompt or "")
    except Exception:  # noqa: BLE001
        family, words = None, []
    mesh_v = _selected(db, a.id, "mesh")
    prep_v = _selected(db, a.id, "prep")
    hl = ((mesh_v.metrics or {}).get("hair_library") or {}) if mesh_v is not None and (mesh_v.params or {}).get("hair_source") == LIBRARY else {}
    cands = []
    for i, c in enumerate(hl.get("candidates") or [], start=1):
        cands.append({"style": c.get("style"), "name": c.get("name"), "score": c.get("score"), "fit_words": fit_words(c.get("score")),
                      "picture_url": _url(mesh_v, f"hair_cand_{i}") if mesh_v is not None else None,
                      "triangles": c.get("triangles"), "kind": c.get("kind"), "credit": _credit(str(c.get("style")))})
    applied = None
    if prep_v is not None and ((prep_v.metrics or {}).get("hair_library") or {}).get("attached"):
        ph = prep_v.metrics["hair_library"]
        applied = {"style": ph.get("style"), "score": ph.get("score"), "prep_version_id": prep_v.id}
    pick = (choice(a) or {}).get("pick")
    rebuild = rebuild_need(db, a)
    state = bald_state(db, a)
    if src == LIBRARY and reason:
        sentence = f"Hair from the library is switched on, but it cannot be used: {reason} The hair is built with the 3D model."
    elif src == LIBRARY and state in BALD_STATE_SENTENCES:
        sentence = BALD_STATE_SENTENCES[state]
    elif src == LIBRARY and applied:
        sentence = f"The character wears \"{style_name(applied['style'])}\" from the hair library (ready-made hairstyles)."
    elif src == LIBRARY and cands:
        sentence = "Pick the hairstyle you like best; the best match is used when you do not pick one."
    elif src == LIBRARY:
        sentence = "The hairstyles are tried on the bald 3D model when it is built."
    else:
        sentence = "The hair is built with the 3D model."
    return {"source": src, "available": reason is None, "unavailable_reason": reason, "family": family, "family_words": list(words or []),
            "pick": pick, "candidates": cands, "mesh_version_id": mesh_v.id if mesh_v is not None else None,
            "best": hl.get("best"), "nothing_fits": bool(hl.get("nothing_fits")) if hl else False, "applied": applied,
            "allowance": kept_allowance(a, mesh_v, prep_v, pick),
            "rebuild": rebuild, "bald_state": state, "sentence": sentence}


def style_name(style_id: str | None) -> str | None:
    """A style's catalogue name (its id when the library does not know it)."""
    if not style_id:
        return None
    try:
        return str(_hairstyles().style(style_id).get("name") or style_id)
    except Exception:  # noqa: BLE001
        return str(style_id)


def body_limit_source(a: Asset, mesh_v: Version | None) -> int | None:
    """The triangle limit Clean up would split between body and hair (stages/prep.py prep_triangle_limit)."""
    try:
        from . import reference
        from .stages.prep import prep_triangle_limit

        platform = a.platform_target
        rec = reference.recipe(a.style_preset, a.category, platform, strict=False)
        return prep_triangle_limit(rec, (mesh_v.params if mesh_v is not None else None) or {}, reference.budget(a.category, platform))["limit"]
    except Exception:  # noqa: BLE001
        return None


def kept_allowance(a: Asset, mesh_v: Version | None, prep_v: Version | None, pick: str | None) -> int | None:
    """The triangles kept for the hair on the kept body: the kept library Clean up's hair_allowance, else what Clean up
    would work out from the kept bald 3D model's candidates (and the pick); None when unknown."""
    if prep_v is not None and (prep_v.params or {}).get("hair_source") == LIBRARY and (prep_v.params or {}).get("hair_allowance") is not None:
        return int(prep_v.params["hair_allowance"])
    if mesh_v is None or (mesh_v.params or {}).get("hair_source") != LIBRARY:
        return None
    mm = mesh_v.metrics or {}
    if not ((mm.get("hair_library") or {}).get("candidates")) and not pick:
        return None
    value = allowance(body_limit_source(a, mesh_v), mm, pick or (mm.get("hair_library") or {}).get("best"))
    return value or None


def has_bald(v: Version | None) -> bool:
    return v is not None and bool((v.files or {}).get("bald_front_cutout"))


# The panel's bald_state: where the bald pictures of the kept Check all sides version stand.
BALD_STATES = ("none", "missing", "drawing", "stopped", "failed", "ready")
BALD_STATE_SENTENCES = {
    "missing": "The bald picture (the character without hair, used to build the 3D model) has not been drawn yet. "
               "Turn Hair from the library off and on again to draw it.",
    "drawing": "The bald picture (the character without hair, used to build the 3D model) is being drawn. The 3D model "
               "can be built again once it is ready.",
    "stopped": "Drawing the bald picture (the character without hair, used to build the 3D model) was stopped. Press "
               "Resume in Check all sides to finish it; the 3D model can be built again after that.",
    "failed": "The bald picture (the character without hair, used to build the 3D model) could not be drawn. Make it "
              "again in Check all sides, or use the hair built with the 3D model.",
}


def bald_state(db: Session, a: Asset) -> str:
    """none (today's hair, or no Check all sides version kept), missing (kept and done, made without the bald pictures),
    drawing (queued or running), stopped (stopped, waiting for Resume), failed (ended in an error, or set aside after
    a stop) or ready (done, with the bald pictures)."""
    from .db import is_stopped

    if asset_source(a) != LIBRARY:
        return "none"
    v = _selected(db, a.id, "turnaround")
    if v is None:
        return "none"
    if v.status in ("queued", "running"):
        return "drawing"
    if is_stopped(v):
        return "stopped"
    if v.status != "done":
        return "failed"
    files = v.files or {}
    return "ready" if files.get("bald_front_cutout") and files.get("bald_quarter_cutout") else "missing"


def rebuild_need(db: Session, a: Asset) -> dict[str, Any] | None:
    """{from_stage, minutes} when what is kept was built with the other hair, else None. With the library on, a
    rebuild is offered only once the kept Check all sides version is done with its bald pictures (bald_state ready)."""
    src = asset_source(a) or MODEL
    mesh_v = _selected(db, a.id, "mesh")
    if mesh_v is None:
        return None
    built = (mesh_v.params or {}).get("hair_source") or MODEL
    if src == LIBRARY and available(a) is None:
        if bald_state(db, a) != "ready":
            return None
        if built != LIBRARY:
            stage = "mesh"
        else:
            prep_v = _selected(db, a.id, "prep")
            pick = (choice(a) or {}).get("pick")
            used = ((prep_v.metrics or {}).get("hair_library") or {}).get("style") if prep_v is not None else None
            stage = "prep" if prep_v is not None and pick and used and used != pick else None
    else:
        stage = "mesh" if built == LIBRARY else None
    return {"from_stage": stage, "minutes": REBUILD_MINUTES.get(stage)} if stage else None


def queue_bald_only(db: Session, a: Asset) -> list[dict[str, Any]]:
    """Turning the library on after Check all sides: an adjust of the kept turnaround that keeps every view and draws
    only the bald pictures. The new version is kept at once, so the next 3D build uses it."""
    from . import pipeline
    from .db import job_to_dict, version_to_dict

    kept = _selected(db, a.id, "turnaround")
    if kept is None or kept.status != "done" or has_bald(kept):
        return []
    extra = {"bald_only": True, "bald_views": True, "bald_seed": None}
    v = pipeline.new_version(db, a, "turnaround", kept, {**(kept.params or {}), **extra})
    job = pipeline.submit(db, a, v, "adjust", {"params": extra})
    pipeline.select(db, v)
    return [{"job": job_to_dict(job), "version": version_to_dict(v)}]


@router.get("/api/assets/{aid}/hair")
def get_hair(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return panel(db, _asset_or_404(db, aid))


@router.put("/api/assets/{aid}/hair")
def put_hair(aid: str, body: HairPut, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _asset_or_404(db, aid)
    if body.source == LIBRARY:
        reason = available(a)
        if reason:
            raise HTTPException(409, reason)
    set_choice(db, a, body.source, changed_by="page")
    started = queue_bald_only(db, a) if body.source == LIBRARY else []
    db.refresh(a)
    need = rebuild_need(db, a)
    return {"panel": panel(db, a), "started": started, "rebuild_from": (need or {}).get("from_stage")}


@router.post("/api/assets/{aid}/hair/pick")
async def pick_hair(aid: str, body: HairPick, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _asset_or_404(db, aid)
    try:
        picked = _hairstyles().style(body.style)
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "that hairstyle is not in the library") from None
    if not fits_budget(picked, _platform_triangles(a, a.platform_target)):
        raise HTTPException(409, "That hairstyle has more triangles than this platform leaves for hair. Choose a lighter one.")
    set_choice(db, a, pick=body.style, changed_by="page")
    started = None
    if body.rebuild:
        prep_v = _selected(db, a.id, "prep")
        hl = ((prep_v.metrics or {}).get("hair_library") or {}) if prep_v is not None else {}
        if prep_v is not None and (prep_v.params or {}).get("hair_source") == LIBRARY and hl.get("style") != body.style:
            started = rebuild_with_style(db, a, prep_v, body.style)
    db.refresh(a)
    return {"panel": panel(db, a), "autopilot": started}


def rebuild_with_style(db: Session, a: Asset, kept: Version, style_id: str) -> dict[str, Any]:
    """The pick-and-rebuild: an adjust of the kept Clean up with the new style (only the hair is fitted again when it
    fits the allowance), kept at once, so the Autopilot builds the moves and the hand-off on it.

    Not an Autopilot Fix: after a Fix the Autopilot ranks every finished version of the step again, and the old Clean
    up, whose hair_fit may pass where the new style's fails, would win and be kept again (the live run of 11-06). The
    person chose this style, so the new version is kept whatever its checks say; the old one stays in the history,
    and is taken again only when the new one fails to build."""
    from . import autopilot, pipeline
    from .reference import STAGE_ORDER

    later = STAGE_ORDER[STAGE_ORDER.index("prep") + 1:]
    for v in a.versions:
        if v.stage in later:
            v.selected = False
            if v.status == "done":
                v.status = "rejected"  # built on the old style: rebuilt from the new Clean up
    db.commit()
    extra = {"hair_style": style_id}
    v = pipeline.new_version(db, a, "prep", kept, {**(kept.params or {}), **extra})
    pipeline.submit(db, a, v, "adjust", {"params": extra})
    pipeline.select(db, v)
    return autopilot.start(a.id)


def used_triangles(style: dict[str, Any], platform: str | None) -> tuple[int, bool]:
    """(triangles the style really has on that platform, whether that is its lighter version): on Phone a shell
    with a lite variant uses it (blender/hairfit.py _style_job does the same)."""
    full = int(style.get("triangles") or 0)
    if platform == PHONE_PLATFORM and style.get("lite") and style.get("lite_triangles"):
        return int(style["lite_triangles"]), True
    return full, False


@router.get("/api/hair-library")
def hair_library(platform: str | None = None, asset_id: str | None = None, db: Session = Depends(get_session)) -> dict[str, Any]:
    """The catalogue. With platform (or asset_id, which gives the asset's platform and look): only the styles offered
    there, each with triangles_used (the count that would really be used) and lite_used (it is the lighter version)."""
    look = None
    if asset_id:
        a = _asset_or_404(db, asset_id)
        platform = platform or a.platform_target
        look = a.style_preset
    try:
        hs = _hairstyles()
        cat = hs.catalogue()
        cat = list((cat.get("styles") or {}).values()) if isinstance(cat, dict) else list(cat)
        offered = None
        if platform or look:
            offered = {s.get("id") for s in hs.styles_for(None, platform, look) or []}
    except Exception:  # noqa: BLE001
        return {"styles": [], "families": [], "installed": False, "platform": platform}
    budget = None
    if platform:
        try:
            from . import reference

            budget = int(reference.budget("humanoid", platform).get("triangles") or 0) or None
        except Exception:  # noqa: BLE001
            budget = None
    styles = []
    for s in cat:
        if offered is not None and s.get("id") not in offered:
            continue
        used, lite = used_triangles(s, platform)
        if budget and used + CAP_MARGIN > int(HAIR_SHARE_MAX * budget):
            continue  # over the hair's share of this platform: not offered
        thumbs = {k: f"/api/hair-library/files/{p}" for k, p in (s.get("thumbs") or {}).items() if p}
        styles.append({"id": s.get("id"), "name": s.get("name"), "family": s.get("family"), "kind": s.get("kind"),
                       "alpha": s.get("alpha"), "triangles": s.get("triangles"), "triangles_used": used, "lite_used": lite,
                       "phone_ok": s.get("phone_ok"), "fringe": s.get("fringe"), "parting": s.get("parting"), "thumbs": thumbs,
                       "own": bool(s.get("own")),
                       "credit": _credit(str(s.get("id")))})
    families = sorted({str(s["family"]) for s in styles if s.get("family")})
    return {"styles": styles, "families": families, "installed": _installed(hs), "platform": platform,
            "how_to_install": None if _installed(hs) else NOT_INSTALLED}


@router.get("/api/hair-library/files/{path:path}")
def hair_library_file(path: str) -> FileResponse:
    if path.startswith("mine/"):  # the person's own hairstyles: my_hair.resolve keeps it inside their folder
        from . import my_hair

        try:
            own = my_hair.resolve(path)
        except ValueError:
            raise HTTPException(404, "file not found")
        if not own.is_file():
            raise HTTPException(404, "file not found")
        return FileResponse(own)
    try:
        hs = _hairstyles()
        root = Path(hs.library_dir()).resolve() if hasattr(hs, "library_dir") else DATA_DIR.resolve()
    except Exception:  # noqa: BLE001
        root = DATA_DIR.resolve()
    p = (root / path).resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(p)

