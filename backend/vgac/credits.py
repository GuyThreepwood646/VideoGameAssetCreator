"""The Credits answer (GET /api/credits): who made what the app builds on, under which licence, in plain words.

One source for the page and CREDITS.md. Hand-kept entries live in data/credits.yaml; AI models come from models.yaml,
add-ons from addons.yaml and hairstyles from the hair library catalogue, so a new entry there appears here by itself.
"""
from __future__ import annotations

import json
from typing import Any

import yaml

from .config import DATA_DIR

NOT_CONFIRMED = "not confirmed"
HAIR_DOES = "A hairstyle you can give a character. It is fitted to the head and exported with the character."


def _yaml(name: str) -> Any:
    with open(DATA_DIR / name, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _entry(name: str, does: str, author: str, licence: str, link: str, *, attribution_required: bool = False,
           in_your_assets: bool = False, note: str = "", confirmed: str = "records") -> dict[str, Any]:
    return {"name": name, "does": does, "author": author or "not recorded", "licence": licence or NOT_CONFIRMED, "link": link or "",
            "attribution_required": bool(attribution_required), "in_your_assets": bool(in_your_assets), "note": note, "confirmed": confirmed}


def _from_hand(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in items:
        out.append(_entry(e["name"], e.get("does", ""), e.get("author", ""), e.get("licence", ""), e.get("link", ""),
                          attribution_required=e.get("attribution_required", False), in_your_assets=e.get("in_your_assets", False),
                          note=e.get("note", ""), confirmed=e.get("confirmed", "records")))
    return out


def _models(hand: dict[str, Any]) -> list[dict[str, Any]]:
    over = hand.get("models", {})
    out = []
    for mid, m in (_yaml("models.yaml").get("models") or {}).items():
        o = over.get(mid, {})
        if o.get("skip"):
            continue
        does = o.get("does") or m.get("description") or ""
        out.append(_entry(m.get("name", mid), does, o.get("author", ""), m.get("licence", ""), o.get("link", ""),
                          attribution_required=o.get("attribution_required", False), in_your_assets=False,
                          note=o.get("note", ""), confirmed=o.get("confirmed", "records")))
    return out


def _addons(hand: dict[str, Any]) -> list[dict[str, Any]]:
    over = hand.get("addons", {})
    out = []
    for a in (_yaml("addons.yaml").get("addons") or []):
        o = over.get(a["id"], {})
        link = o.get("link") or (a.get("source") if str(a.get("source", "")).startswith("http") else "")
        out.append(_entry(a.get("label", a["id"]), (a.get("help") or {}).get("meaning", ""), o.get("author", ""), a.get("licence", ""), link))
    return out


def _hair() -> list[dict[str, Any]]:
    with open(DATA_DIR / "hairlib" / "catalogue.json", "r", encoding="utf-8") as f:
        styles = json.load(f).get("styles", {})
    out = []
    for s in styles.values():
        out.append(_entry("Hairstyle: " + s.get("name", s["id"]), HAIR_DOES, s.get("author", ""), s.get("licence", ""), s.get("source_url", ""),
                          in_your_assets=True))
    return out


def credits() -> dict[str, Any]:
    hand = _yaml("credits.yaml")
    groups = [
        {"id": "models", "title": "AI models (the trained files that make pictures and 3D shapes)",
         "intro": "These run on your computer. Their files are not part of your game; what they make is.", "entries": _models(hand)},
        {"id": "addons", "title": "Add-ons (LoRAs: small files that steer a model toward a look)",
         "intro": "Each one is loaded on top of a model above.", "entries": _addons(hand)},
        {"id": "data", "title": "Ready-made data (moves, light pictures)",
         "intro": "Files that come with the app. Moves can end up inside your characters.", "entries": _from_hand(hand.get("data", []))},
        {"id": "hair", "title": "Hairstyles (the hair library)",
         "intro": "Every style is CC0 (public domain dedication) and is fitted onto characters you export.", "entries": _hair()},
        {"id": "tools", "title": "Tools (outside programs the app calls)",
         "intro": "None of these is included in the app.", "entries": _from_hand(hand.get("tools", []))},
        {"id": "libraries", "title": "Software libraries (code the app is built from)",
         "intro": "Their licence notices must travel with the app if you pass it on.", "entries": _from_hand(hand.get("libraries", []))},
    ]
    return {"summary": hand.get("summary", {}), "groups": groups}
