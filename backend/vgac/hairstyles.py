"""The hair library as app data (docs/HAIR-LIBRARY-PLAN.md section 4): loader for backend/vgac/data/hairlib.

The library is built offline by tools/hairlib_build.py. It is read once and cached (`lru_cache`): a data change needs a
backend restart. Tests point it at another folder with `use_dir(path)`.

    catalogue()                          -> {"styles": {id: entry}, "refused": [...], "left_out": {...}, "version": 1}
    style(id)                            -> one catalogue entry (KeyError when unknown)
    styles_for(family, platform, look)   -> entries offered for that family (None: every family), platform and look
    family_of(prompt)                    -> (family | None, [the words that decided it])
    paths(id)                            -> {npz, lite, grey, normal, thumb_front, thumb_quarter} absolute Paths (missing keys: None)
    credit(id)                           -> {id, name (the pack's own name), author, licence, source_url}
Catalogue entries carry `name` (the display name the owner sees) and `pack_name` (the pack's own name).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import hairpic

DATA_DIR = Path(__file__).resolve().parent / "data" / "hairlib"
_dir = [DATA_DIR]

FAMILIES = ("bob", "short", "long", "ponytail", "bun", "braid", "afro")
PHONE_PLATFORMS = ("mobile_low",)
PHONE_TRIANGLES = 750
LOOKS_OFF = ("pixel", "retro64", "lowpoly_flat")

# Hair words per family, checked in this order (a tied style wins over a length word: "long hair in a ponytail").
FAMILY_WORDS: list[tuple[str, list[str]]] = [
    ("ponytail", ["ponytail", "pony tail", "ponytails"]),
    ("bun", ["bun", "buns", "top knot", "topknot", "chignon", "hair bun"]),
    ("braid", ["braid", "braids", "braided", "plait", "plaits", "plaited"]),
    ("afro", ["afro"]),
    ("bob", ["bob", "bob cut", "bobbed", "pageboy", "bob haircut"]),
    ("long", ["long hair", "long loose hair", "waist length", "hip length", "shoulder length", "flowing hair"]),
    ("short", ["short hair", "buzz cut", "crew cut", "crop", "cropped", "pixie", "pixie cut", "short cut", "undercut", "buzzed"]),
]


def use_dir(path: Path | str | None) -> None:
    """Point the loader at another library folder (tests); None goes back to the app's own."""
    _dir[0] = Path(path) if path else DATA_DIR
    _load.cache_clear()


def library_dir() -> Path:
    return _dir[0]


@lru_cache(maxsize=4)
def _load(d: str) -> dict[str, Any]:
    p = Path(d) / "catalogue.json"
    if not p.is_file():
        return {"styles": {}, "refused": [], "left_out": {}, "version": 0}
    data = json.loads(p.read_text(encoding="utf-8"))
    if Path(d) == DATA_DIR:
        data = _only_chosen(data)
    return data


def chosen_packs() -> set[tuple[str, str]] | None:
    """The (pack, author) pairs the person kept in setup (<library>/hair-packs.json, written by
    setup/Setup-Extras.ps1 -Packs). None = no file or unreadable = every style, as before. Read once per start."""
    try:
        from .config import get_settings

        f = get_settings().library_path / "hair-packs.json"
        if not f.is_file():
            return None
        packs = json.loads(f.read_text(encoding="utf-8-sig")).get("packs")
        if not isinstance(packs, list):
            return None
        return {(str(x.get("catalogue_pack", "")), str(x.get("catalogue_author", ""))) for x in packs if isinstance(x, dict)}
    except Exception:  # a bad picks file must never take the hair library away
        return None


def _only_chosen(data: dict[str, Any]) -> dict[str, Any]:
    keep = chosen_packs()
    if keep is None:
        return data
    out = dict(data)
    out["styles"] = {k: v for k, v in data.get("styles", {}).items() if (v.get("pack", ""), v.get("author", "")) in keep}
    return out


def catalogue() -> dict[str, Any]:
    return _load(str(_dir[0]))


def style(style_id: str) -> dict[str, Any]:
    return catalogue()["styles"][style_id]


def styles_for(family: str | None, platform: str | None = None, look: str | None = None) -> list[dict[str, Any]]:
    """Styles that can be offered: the family (None: all), never for looks without hair styles (Pixel, Retro 64,
    Low-poly flat); on Phone only shells whose lite variant is at most 750 triangles (cards are never offered there).
    Shells first, then by name."""
    if (look or "").lower() in LOOKS_OFF:
        return []
    out = []
    for e in catalogue()["styles"].values():
        if family and e["family"] != family:
            continue
        if platform in PHONE_PLATFORMS and not e.get("phone_ok"):
            continue
        out.append(e)
    return sorted(out, key=lambda e: (e["kind"] != "shell", e["name"]))


def family_of(prompt: str | None) -> tuple[str | None, list[str]]:
    """The hairstyle family the description names, with the words that decided it. Long and short also use the
    phrase rules of hairpic.py ("long red hair", "hair falling to her waist", "cropped hair")."""
    t = hairpic.norm(prompt)
    for fam, words in FAMILY_WORDS:
        hit = [w for w in words if hairpic.has(t, w)]
        if hit:
            return fam, hit
        if fam == "long" and hairpic.long_loose(t):
            return "long", ["long loose hair"]
        if fam == "short" and (hairpic.short_hair(t) or _short_before_hair(t)):
            return "short", ["short hair"]
    return None, []


def wants_curls(prompt: str | None) -> bool:
    t = hairpic.norm(prompt)
    return any(hairpic.has(t, w) for w in ("curly", "curls", "curled", "wavy", "waves", "ringlets", "afro", "frizzy", "permed"))


def _short_before_hair(t: str) -> bool:
    """A short word up to five words before "hair" ("short neat dark brown hair")."""
    w = t.split()
    for i, x in enumerate(w):
        if x == "hair" and any(y in ("short", "cropped") for y in w[max(0, i - 5):i]):
            return True
    return False


def wants_fringe(prompt: str | None) -> bool:
    t = hairpic.norm(prompt)
    return any(hairpic.has(t, w) for w in ("fringe", "bangs", "fringed"))


def paths(style_id: str) -> dict[str, Path | None]:
    e = style(style_id)
    d = _dir[0]

    def p(rel: str | None) -> Path | None:
        return (d / rel) if rel else None
    return dict(npz=p(f"styles/{style_id}.npz"), lite=p(e.get("lite")), grey=p(e["textures"]["grey"]),
                normal=p(e["textures"].get("normal")), thumb_front=p(e["thumbs"]["front"]), thumb_quarter=p(e["thumbs"]["quarter"]))


def credit(style_id: str) -> dict[str, Any]:
    e = style(style_id)
    return dict(id=style_id, name=e.get("pack_name") or e["name"], author=e["author"], licence=e["licence"], source_url=e["source_url"])


def file_path(rel: str) -> Path:
    """A library file by its path relative to the library folder, refused when it would leave the folder."""
    d = _dir[0].resolve()
    p = (d / rel).resolve()
    if d not in p.parents and p != d:
        raise ValueError("path outside the hair library")
    return p
