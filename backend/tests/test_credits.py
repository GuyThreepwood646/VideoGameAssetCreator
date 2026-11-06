"""Credits answer: every model, add-on and hairstyle is named; nothing lacks a licence or a plain description; no emoji."""
from __future__ import annotations

import json
import re

import yaml

from vgac import credits
from vgac.config import DATA_DIR

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def _all_entries(ans):
    return [e for g in ans["groups"] for e in g["entries"]]


def _walk_strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _walk_strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk_strings(v)


def test_every_model_and_addon_appears():
    ans = credits.credits()
    names = {e["name"] for e in _all_entries(ans)}
    models = yaml.safe_load((DATA_DIR / "models.yaml").read_text(encoding="utf-8"))["models"]
    skipped = {k for k, v in yaml.safe_load((DATA_DIR / "credits.yaml").read_text(encoding="utf-8")).get("models", {}).items() if v.get("skip")}
    for mid, m in models.items():
        if mid in skipped:
            continue  # recorded under tools or data instead
        assert m["name"] in names, mid
    # the skipped ones must be named elsewhere
    for mid in skipped:
        assert any(models[mid]["name"].split(" (")[0].split(" ")[0].lower() in n.lower() for n in names), mid
    for a in yaml.safe_load((DATA_DIR / "addons.yaml").read_text(encoding="utf-8"))["addons"]:
        assert a["label"] in names, a["id"]


def test_every_hairstyle_appears():
    names = {e["name"] for e in _all_entries(credits.credits())}
    styles = json.loads((DATA_DIR / "hairlib" / "catalogue.json").read_text(encoding="utf-8"))["styles"]
    assert len(styles) >= 26
    for s in styles.values():
        assert "Hairstyle: " + s["name"] in names, s["id"]


def test_no_entry_lacks_licence_or_description():
    ans = credits.credits()
    assert [g["id"] for g in ans["groups"]] == ["models", "addons", "data", "hair", "tools", "libraries"]
    for e in _all_entries(ans):
        assert e["licence"].strip(), e["name"]
        assert len(e["does"].strip()) >= 15, e["name"]
        assert e["author"].strip(), e["name"]
        assert isinstance(e["attribution_required"], bool) and isinstance(e["in_your_assets"], bool)
        assert e["confirmed"] in ("lookup", "records", "not confirmed"), e["name"]
        if e["confirmed"] == "not confirmed":
            assert e["licence"] == "not confirmed", e["name"]


def test_summary_names_the_exceptions_and_disclaimer():
    s = credits.credits()["summary"]
    text = " ".join(_walk_strings(s)).lower()
    for key in ("game_credit", "passing_on", "before_sharing"):
        assert all(isinstance(p, str) for p in s[key]["points"])
    assert "gpl-3.0-or-later" in text
    assert "not legal advice" in text
    assert "hunyuan3d" in text and "anima" in text and "blender" in text and "comfyui" in text


def test_no_emoji_anywhere():
    for s in _walk_strings(credits.credits()):
        assert not EMOJI.search(s), s
    assert not EMOJI.search((DATA_DIR / "credits.yaml").read_text(encoding="utf-8"))
