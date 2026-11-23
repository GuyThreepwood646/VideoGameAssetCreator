"""vgac/srcproject.py and vgac/picture_view.py: a character's picture is expected at a turn only when the app drew it; the
person's own picture (an upload, an edit of one, a picture combined from their photos) is searched over every turn."""
from __future__ import annotations

import pytest

from vgac import picture_view, srcproject
from test_srcproject_objects import box, drawn


def test_only_the_persons_own_pictures_are_unknown():
    assert srcproject.picture_known_of({"picture_view": "front"})
    assert srcproject.picture_known_of({})          # drawn by the app before views were recorded: as before
    assert srcproject.picture_known_of(None)
    assert srcproject.picture_known_of({"source": "text"})
    for s in picture_view.PERSON_SOURCES:
        assert not srcproject.picture_known_of({"source": s}), s
        # a view carried over from an earlier painted version does not make the person's picture known
        assert not srcproject.picture_known_of({"source": s, "picture_view": "front"}), s


class _Stop(Exception):
    pass


def _picture_search(monkeypatch, **kw) -> list[float]:
    """The turns paint() searches the step 1 picture over (the search is stopped there)."""
    seen: list[float] = []
    real = srcproject._pose_pick

    def pick(*a, **k):
        if isinstance(a[5], str) and a[5] == "picture":
            seen.extend(a[12])
            raise _Stop
        return real(*a, **k)

    monkeypatch.setattr(srcproject, "_pose_pick", pick)
    mesh, tex = box()
    with pytest.raises(_Stop):
        srcproject.paint(mesh, tex.copy(), drawn(mesh, tex, 0.0, 15.0), None, mode="off", category="humanoid", **kw)
    return seen


def test_an_upload_keeps_the_wide_search_and_an_app_picture_its_expected_turn(monkeypatch):
    known = _picture_search(monkeypatch, built_from="front")
    assert max(abs(t) for t in known) <= srcproject.PICTURE_SEARCH_DEG + 1e-6   # around the expected 0, as before
    upload = _picture_search(monkeypatch, built_from="front", picture_known=False)
    assert max(upload) - min(upload) > 300                                    # the whole circle
    # facing decided: the front half
    half = _picture_search(monkeypatch, built_from="front", picture_known=False, front_known=True)
    assert 90 <= max(half) - min(half) <= 200
    # built from a drawn three-quarter view: the app chose its turn, so it stays expected even for an upload
    quarter = _picture_search(monkeypatch, built_from="quarter", picture_known=False)
    centre = srcproject.INTENDED_TURN["quarter"]
    assert max(abs(t - centre) for t in quarter) <= srcproject.PICTURE_SEARCH_DEG + 1e-6
