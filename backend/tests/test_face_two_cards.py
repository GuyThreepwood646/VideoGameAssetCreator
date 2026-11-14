"""Item 20b: the face close-up drawn twice at once when the queue lends a second card (the lending step 2 uses), the better
one kept by the guard's own scores, one after another when no card is free. Drawing and the guard are faked."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image

from vgac.jobs import JobCancelled
from vgac.stages import face_detail as fd
from test_face_sharper import REC, _SC, stubbed  # noqa: F401  (the stubbed pass and its fixture)


class Lease:
    def __init__(self):
        self.instance, self.gpu = "c", "RTX 3080"
        self.revoked, self.revoked_reason, self.ended = False, "", False
        self.ends: list[bool] = []

    async def end(self, interrupt: bool = False):
        self.ends.append(interrupt)
        self.ended = True


class Card(_SC):
    def __init__(self, tmp, lease=None, why="this computer has one graphics card"):
        super().__init__(tmp, 2048)
        self.ctx = SimpleNamespace(borrow=self._borrow)
        self._lease, self._why = lease, why

    async def _borrow(self):
        return (self._lease, "") if self._lease is not None else (None, self._why)


def _guard(name_to_verdict):
    def guard(glb, base, crop, cu, *a):
        v = name_to_verdict.get(cu.name, (None, 0.9, 0.9))
        reason, likeness, feat = v
        return {"ok": reason is None, "reason": reason, "likeness": likeness, "feature_scores": {"eye_l": feat},
                "feature_score_min": feat, "source_face_px": 459.0}
    return guard


@pytest.fixture
def draws(stubbed, monkeypatch):
    """Replace the stubbed drawing with one that records the card and how many drawings ran at once."""
    calls, glb, maps = stubbed
    state = {"log": [], "now": 0, "peak": 0, "lost": set(), "stop_on": None}

    async def redraw(sc, instance, crop, seed, dest, prompt=fd.REDRAW_PROMPT, key="face_redraw", message="", reference=None, margin=1.0):
        state["log"].append((key, seed, instance))
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        try:
            await asyncio.sleep(0.02)
            if instance == "c" and "c" in state["lost"]:
                raise RuntimeError("engine lost")
            if state["stop_on"] == key:
                raise JobCancelled()
        finally:
            state["now"] -= 1
        Image.new("RGB", (8, 8)).save(dest)
        return dest

    monkeypatch.setattr(fd, "redraw", redraw)
    monkeypatch.setattr(fd, "closeup_source", lambda crop, cu, px, dest: (Image.new("RGB", (8, 8)).save(dest), dest)[1])
    return state, glb, maps


def _run(sc, glb, maps, params=None):
    return asyncio.run(fd.run(sc, glb, maps, REC, {"face_density": "standard", "face_detail_seed": 100, **(params or {})}))


def test_two_close_ups_are_drawn_at_once_one_per_card_and_the_nearer_one_is_kept(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    monkeypatch.setattr(fd, "closeup_guard", _guard({"painted_head_closeup.png": (None, 0.95, 0.90),
                                                     "painted_head_closeup_2.png": (None, 0.99, 0.97)}))
    lease = Lease()
    r = _run(Card(tmp_path, lease), glb, maps)["metrics"]["face_detail"]
    assert state["peak"] == 2  # both were in flight together
    assert {k: c for k, _, c in state["log"]} == {"face_closeup": "b", "face_closeup_2": "c"}  # "b" is the job's own card
    assert [t["seed"] for t in r["closeup"]["tries"]] == [100, 101] and r["closeup"]["draws"] == 2
    assert r["closeup"]["used"] and r["closeup"]["file"] == "painted_head_closeup_2.png"  # the nearer of the two
    assert r["closeup"]["cards"]["two"] is True and "RTX 3080" in r["closeup"]["cards"]["note"]
    assert lease.ended and lease.ends == [False]


def test_only_one_that_passes_is_kept_and_no_second_round_is_drawn(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    monkeypatch.setattr(fd, "closeup_guard", _guard({"painted_head_closeup.png": ("likeness", 0.85, 0.9),
                                                     "painted_head_closeup_2.png": (None, 0.97, 0.95)}))
    r = _run(Card(tmp_path, Lease()), glb, maps)["metrics"]["face_detail"]
    assert len(state["log"]) == 2 and r["closeup"]["used"] and r["closeup"]["file"] == "painted_head_closeup_2.png"


def test_both_refused_a_second_round_of_two_waits_for_the_first_verdicts(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    verdicts = {"painted_head_closeup.png": ("likeness", 0.85, 0.9), "painted_head_closeup_2.png": ("features", 0.95, 0.4),
                "painted_head_closeup_3.png": ("likeness", 0.86, 0.9), "painted_head_closeup_4.png": (None, 0.96, 0.93)}
    monkeypatch.setattr(fd, "closeup_guard", _guard(verdicts))
    lease = Lease()
    r = _run(Card(tmp_path, lease), glb, maps)["metrics"]["face_detail"]
    keys = [k for k, _, _ in state["log"]]
    assert set(keys[:2]) == {"face_closeup", "face_closeup_2"}
    assert set(keys[2:]) == {"face_closeup_3", "face_closeup_4"}  # a retry round draws two more, after the first round
    assert sorted(s for _, s, _ in state["log"]) == [100, 101, 102, 103]
    assert r["closeup"]["draws"] == 4 and r["closeup"]["file"] == "painted_head_closeup_4.png"
    assert lease.ended


def test_a_refusal_a_new_seed_cannot_change_is_not_drawn_again(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    monkeypatch.setattr(fd, "closeup_guard", _guard({"painted_head_closeup.png": ("skin_colour", 0.97, 0.9),
                                                     "painted_head_closeup_2.png": ("eye_colour", 0.97, 0.9)}))
    r = _run(Card(tmp_path, Lease()), glb, maps)["metrics"]["face_detail"]
    assert len(state["log"]) == 3 and r["closeup"]["used"] is False  # two close-ups, then the sharpening redraw


def test_no_second_card_is_one_after_another_with_the_reason_recorded(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    monkeypatch.setattr(fd, "closeup_guard", _guard({"painted_head_closeup.png": ("likeness", 0.85, 0.9)}))
    r = _run(Card(tmp_path, None, "RTX 3080 is busy with another step"), glb, maps)["metrics"]["face_detail"]
    assert state["peak"] == 1 and [k for k, _, _ in state["log"]] == ["face_closeup", "face_closeup_2"]
    assert r["closeup"]["cards"] == {"two": False, "note": "RTX 3080 is busy with another step"}
    assert len(r["closeup"]["tries"]) == 2 and r["closeup"]["file"] == "painted_head_closeup_2.png"


def test_a_step_that_cannot_ask_draws_as_before(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    monkeypatch.setattr(fd, "closeup_guard", _guard({}))
    sc = _SC(tmp_path, 2048)  # no ctx at all
    r = _run(sc, glb, maps)["metrics"]["face_detail"]
    assert state["peak"] == 1 and r["closeup"]["cards"]["two"] is False


def test_the_lent_engine_stopping_finishes_the_second_close_up_on_the_jobs_card(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    state["lost"].add("c")
    import vgac.jobs as jobs

    monkeypatch.setattr(jobs, "is_engine_lost", lambda e: "engine lost" in str(e))
    monkeypatch.setattr(fd, "closeup_guard", _guard({"painted_head_closeup.png": (None, 0.9, 0.9),
                                                     "painted_head_closeup_2.png": (None, 0.99, 0.99)}))
    lease = Lease()
    r = _run(Card(tmp_path, lease), glb, maps)["metrics"]["face_detail"]
    second = [c for k, _, c in state["log"] if k == "face_closeup_2"]
    assert second == ["c", "b"]  # tried on the lent card, then drawn on the job's card
    assert r["closeup"]["file"] == "painted_head_closeup_2.png" and "so the second close-up was drawn on the job's card" in r["closeup"]["cards"]["note"]
    assert lease.ended


def test_a_taken_back_card_is_noted_and_the_rest_runs_one_at_a_time(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    lease = Lease()
    lease.revoked, lease.revoked_reason = True, "The RTX 3080 was turned off"
    monkeypatch.setattr(fd, "closeup_guard", _guard({"painted_head_closeup.png": ("likeness", 0.85, 0.9)}))
    _run(Card(tmp_path, lease), glb, maps)
    assert state["peak"] == 1 and lease.ended


def test_a_stop_hands_the_lent_card_back_and_interrupts_what_is_left_on_it(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    state["stop_on"] = "face_closeup"
    monkeypatch.setattr(fd, "closeup_guard", _guard({}))
    lease = Lease()
    with pytest.raises(JobCancelled):
        _run(Card(tmp_path, lease), glb, maps)
    assert lease.ended and lease.ends[-1] is True


def test_the_better_close_up_by_the_guards_scores_and_the_fourth_seed():
    a = {"ok": True, "likeness": 0.95, "feature_score_min": 0.90, "file": "a"}
    b = {"ok": True, "likeness": 0.99, "feature_score_min": 0.97, "file": "b"}
    c = {"ok": False, "likeness": 0.999, "feature_score_min": 0.99, "file": "c"}
    assert fd.pick_closeup([a, b, c]) is b and fd.pick_closeup([c, a]) is a
    assert fd.pick_closeup([c, {"ok": False, "file": "d"}])["file"] == "d"  # none passed: the last one drawn
    sc = _SC.__new__(_SC)
    sc.params = {}
    assert fd.picked_seed(sc, {"face_closeup_seed_2": 8}, "face_closeup_seed_4") == 10
    assert fd.closeup_spare(None)[0] == "face_closeup_seed_4"


def test_a_card_lent_that_is_the_one_drawing_the_first_close_up_is_handed_straight_back(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    lease = Lease()
    lease.instance = "b"  # the job's own drawing card
    monkeypatch.setattr(fd, "closeup_guard", _guard({}))
    r = _run(Card(tmp_path, lease), glb, maps)["metrics"]["face_detail"]
    assert state["peak"] == 1 and lease.ended
    assert r["closeup"]["cards"] == {"two": False, "note": "the only card free is the one drawing the first close-up"}


def test_a_queue_that_takes_a_kind_and_a_card_to_avoid_is_asked_for_a_picture_card(tmp_path, draws, monkeypatch):
    state, glb, maps = draws
    asked = {}

    async def borrow(kind=None, avoid=None):
        asked.update(kind=kind, avoid=avoid)
        return Lease(), ""

    sc = Card(tmp_path, None)
    sc.ctx = SimpleNamespace(borrow=borrow)
    monkeypatch.setattr(fd, "closeup_guard", _guard({}))
    _run(sc, glb, maps)
    assert asked == {"kind": "turnaround.run", "avoid": "b"} and state["peak"] == 2


def test_the_card_of_the_first_close_up_is_claimed_and_handed_back(tmp_path, draws, monkeypatch):
    """A queue that takes `only`: the first close-up's card is taken as a lease before drawing, the second card asked
    for beside it, and both handed back at the end (and on a stop)."""
    state, glb, maps = draws
    home, second, asked = Lease(), Lease(), []
    home.instance = "b"

    async def borrow(kind=None, avoid=None, only=None):
        asked.append((kind, avoid, only))
        return (home, "") if only is not None else (second, "")

    sc = Card(tmp_path, None)
    sc.ctx = SimpleNamespace(borrow=borrow)
    monkeypatch.setattr(fd, "closeup_guard", _guard({}))
    _run(sc, glb, maps)
    assert asked[0] == ("turnaround.run", None, "b") and asked[1] == ("turnaround.run", "b", None)
    assert home.ended and second.ended and state["peak"] == 2


def test_no_claim_is_todays_drawing(tmp_path, draws, monkeypatch):
    """The queue cannot give the card (busy with a job): drawn there all the same, as before."""
    state, glb, maps = draws

    async def borrow(kind=None, avoid=None, only=None):
        return None, "RTX 3080 is busy with another step"

    sc = Card(tmp_path, None)
    sc.ctx = SimpleNamespace(borrow=borrow)
    monkeypatch.setattr(fd, "closeup_guard", _guard({}))
    r = _run(sc, glb, maps)["metrics"]["face_detail"]
    assert state["log"] and all(card == "b" for _, _, card in state["log"]) and state["peak"] == 1
    assert r["closeup"]["cards"]["two"] is False
