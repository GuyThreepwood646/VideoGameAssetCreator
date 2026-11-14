"""Fixing several failed checks on the first picture with one redraw (data/fixes.yaml).

`compose` joins the picture-editor wording of the ticked checks into ONE instruction: their changes in the file's order,
the things to keep named once, and nothing in the keep list that a change alters. It is a pure function (no model call),
so the same ticks always give the same words. The redraw itself is the ordinary concept refine; the new version records
`params.fix_for` (the ticked check ids) so a later look at the history can tell a fix from a hand edit.
"""
from __future__ import annotations

import functools
from typing import Any

import yaml


from .config import DATA_DIR

BACKGROUND = "the plain light grey background"  # always the last thing on the keep list

REASON_TEXT = {
    "no_snippet": "There are no words for fixing {check} in a redraw.",
    "withheld": "{check} is not fixed by this redraw, because in our trials it changed the character. Use its own button instead.",
    "wrong_category": "Fixing {check} by redrawing is not offered for this kind of asset.",
    "conflict": "Fixing {check} and {other} together would pull against each other, so {check} was left out. Fix one, then the other.",
    "too_many": "A redraw takes at most {n} changes at once, so {check} was left out. Fix it after this redraw.",
    "too_long": "The redraw would be too long for the picture editor, so {check} was left out. Fix it after this redraw.",
}


@functools.lru_cache(maxsize=1)
def data() -> dict[str, Any]:
    with open(DATA_DIR / "fixes.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def snippet_for(check_id: str, d: dict[str, Any] | None = None, withheld: bool = False) -> str | None:
    """The id of the snippet that fixes a check, or None. A snippet marked `offered: false` (its trial redraws changed the
    character or missed the check, see the file) is not returned unless `withheld` is true."""
    for sid, s in ((d or data()).get("snippets") or {}).items():
        if check_id in (s.get("checks") or []) and (withheld or s.get("offered", True)):
            return sid
    return None


def known_checks(d: dict[str, Any] | None = None) -> list[str]:
    """The checks a redraw is offered for (not the withheld ones)."""
    return [c for s in ((d or data()).get("snippets") or {}).values() if s.get("offered", True) for c in (s.get("checks") or [])]


def subject_word(category: dict[str, Any] | str | None) -> str:
    """"person" for characters (the words the existing redraws use), else the category's own word (readiness.SUBJECT_WORDS)."""
    from . import readiness

    cid = category.get("id") if isinstance(category, dict) else category
    if not cid or cid == "humanoid":
        return "person"
    return readiness.SUBJECT_WORDS.get(cid, "subject")


def _fill(text: str, subject: str) -> str:
    from . import buildpose  # late: this module is imported while the checks are described

    return text.replace("{subject}", subject).replace("{a_pose}", buildpose.A_POSE)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _words(text: str) -> int:
    return len(text.split())


def _drop(dropped: list[dict[str, Any]], check: str, reason: str, **kw: Any) -> None:
    dropped.append({"check": check, "reason": reason, "text": REASON_TEXT[reason].format(check=check.replace("_", " "), **kw)})


def compose(check_ids: list[str], category: dict[str, Any] | str | None = None, d: dict[str, Any] | None = None,
            withheld: bool = False) -> dict[str, Any]:
    """One redraw instruction for the ticked checks.

    Returns {instruction, used, snippets, dropped, words}: `used` the check ids the instruction covers, `snippets` the
    snippet ids in order, `dropped` the ticks that could not be joined ({check, reason, text}), `words` its length.
    A check with no snippet, a snippet marked as conflicting with one already joined, more than max_changes changes, or
    an instruction over max_words is dropped, never silently changed. Two ticks that share a snippet (arms_away and
    pose_for_3d) count once. The order of `check_ids` does not matter."""
    d = d or data()
    snippets: dict[str, Any] = d.get("snippets") or {}
    max_changes = int(d.get("max_changes") or 3)
    max_words = int(d.get("max_words") or 260)
    subject = subject_word(category)
    cid = category.get("id") if isinstance(category, dict) else category
    wanted = list(dict.fromkeys(check_ids))
    dropped: list[dict[str, Any]] = []
    by_snippet: dict[str, list[str]] = {}
    for c in wanted:
        sid = snippet_for(c, d, withheld)
        if sid is None:
            _drop(dropped, c, "withheld" if snippet_for(c, d, True) else "no_snippet")
            continue
        cats = snippets[sid].get("categories")
        if cats and (cid or "humanoid") not in cats:
            _drop(dropped, c, "wrong_category")
            continue
        by_snippet.setdefault(sid, []).append(c)
    chosen: list[str] = []
    for sid in snippets:  # the file's order, not the order of the ticks
        if sid not in by_snippet:
            continue
        clash = next((o for o in chosen if o in (snippets[sid].get("conflicts") or []) or sid in (snippets[o].get("conflicts") or [])), None)
        if clash is not None:
            for c in by_snippet[sid]:
                _drop(dropped, c, "conflict", other=snippets[clash]["checks"][0].replace("_", " "))
            continue
        if len(chosen) >= max_changes:
            for c in by_snippet[sid]:
                _drop(dropped, c, "too_many", n=max_changes)
            continue
        chosen.append(sid)
    text = _instruction(chosen, snippets, subject)
    while chosen and _words(text) > max_words:  # take the last one off until it fits
        last = chosen.pop()
        for c in by_snippet[last]:
            _drop(dropped, c, "too_long")
        text = _instruction(chosen, snippets, subject)
    used = [c for sid in chosen for c in by_snippet[sid]]
    return {"instruction": text or None, "used": used, "snippets": chosen, "dropped": dropped, "words": _words(text) if text else 0}


def _instruction(chosen: list[str], snippets: dict[str, Any], subject: str) -> str:
    if not chosen:
        return ""
    changes = [_fill(snippets[s]["change"], subject).strip() for s in chosen]
    drops = {x for s in chosen for x in (snippets[s].get("drops") or [])}
    keep: list[str] = []
    for s in chosen:
        for item in snippets[s].get("keep") or []:
            item = _fill(item, subject)
            if item not in keep and item not in drops:
                keep.append(item)
    if BACKGROUND in keep:  # the background stays last whatever order the snippets list it in
        keep.remove(BACKGROUND)
        keep.append(BACKGROUND)
    tail = f"Keep everything else exactly the same: {_join(keep)}." if keep else ""
    if len(changes) == 1:
        return f"{changes[0]} {tail}".strip()
    numbered = " ".join(f"({i}) {c}" for i, c in enumerate(changes, 1))
    return f"Make these {len(changes)} changes: {numbered} {tail}".strip()


def check_fix_for(ids: list[str]) -> list[str]:
    """The ticked ids as they are stored on a version (params.fix_for): known checks only, in the order given, no repeats."""
    known = set(known_checks())
    return [i for i in dict.fromkeys(ids) if i in known]
