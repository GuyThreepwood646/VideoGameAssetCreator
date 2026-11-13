"""Step 2 on two cards (stages/turnaround.py _both_sides; docs/AFTER-TEST-CHANGES.md D2 items 17 and 19): the front
first, then the left side (quarter, left) on the job's card and the right side (quarter2, right) on a lent card at the
same time, then the back. Fake engines record which card drew what and when; no ComfyUI, no database."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from vgac import imaging, jobs
from vgac.comfy import ComfyError
from vgac.stages import turnaround

from test_stage_offloop import FakeDB, FakeSC, _lineage, _rgba

VIEWS = ["front", "left", "back", "right", "quarter", "quarter2"]
DRAW_SECONDS = 0.15


class LentClient:
    def __init__(self) -> None:
        self.interrupts = 0

    async def interrupt(self) -> None:
        self.interrupts += 1


def make_lease(given_back: list) -> jobs.Lease:
    return jobs.Lease(instance="b", job_id="job1", client=LentClient(), gpu="RTX 3080", _give_back=given_back.append)


class TwoCardSC(FakeSC):
    """FakeSC with a job context: runs on card "a" and may borrow (the queue's answer is given)."""

    def __init__(self, tmp, version, db, answer):
        super().__init__(tmp, version, db)

        async def borrow():
            return answer

        self.ctx = SimpleNamespace(instance="a", borrow=borrow if answer is not None else None)
        self.messages: list[str] = []
        self.staged_on: list[tuple[str, str | None]] = []

    async def stage_input(self, src, name, instance=None):
        self.staged_on.append((name, instance))
        return await super().stage_input(src, name)

    def progress(self, *a):
        super().progress(*a)
        if len(a) > 1 and a[1]:
            self.messages.append(a[1])


@pytest.fixture
def engines(monkeypatch):
    """A fake _generate_view per card: sleeps, writes a picture, records (view, card, seed, prompt words, start,
    end). `fail` maps (view, card) to an exception raised once."""
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, settings: v.image if key == "image" else None)
    state = {"calls": [], "fail": {}}

    async def fake_generate(sc, src_name, view, seed, lora_strength, extra, base, span, name=None, instance=None):
        card = instance or "a"
        t0 = time.monotonic()
        await asyncio.sleep(DRAW_SECONDS)
        err = state["fail"].pop((view, card), None)
        if err is not None:
            raise err
        state["calls"].append({"view": view, "card": card, "seed": seed, "extra": extra, "src": src_name,
                               "lora": lora_strength, "start": t0, "end": time.monotonic()})
        raw = _rgba(sc.dir / f"{view}_raw.png")
        cut = _rgba(sc.dir / f"{view}.png")
        return {"raw": raw, "cutout": cut, "white": imaging.white_composite(cut), "stats": None}

    monkeypatch.setattr(turnaround, "_generate_view", fake_generate)
    return state


def run_views(tmp_path, answer, params=None):
    concept, ta, new = _lineage(tmp_path)
    sc = TwoCardSC(tmp_path, new, FakeDB(concept, ta, new), answer)
    asyncio.run(turnaround._run_views(sc, list(VIEWS), dict(params or {"seed": 100}), concept))
    return sc


def by_view(calls):
    return {c["view"]: c for c in calls}


def test_two_free_cards_draw_the_two_sides_at_once(tmp_path, engines):
    given_back: list = []
    lease = make_lease(given_back)
    sc = run_views(tmp_path, (lease, ""))
    calls = engines["calls"]
    v = by_view(calls)
    assert [c["card"] for c in calls if c["view"] in ("quarter", "left")] == ["a", "a"]
    assert [c["card"] for c in calls if c["view"] in ("quarter2", "right")] == ["b", "b"]
    assert v["front"]["card"] == "a" and v["back"]["card"] == "a"
    # front before both sides, the sides overlapping, the back after both
    sides = [v[x] for x in ("quarter", "left", "quarter2", "right")]
    assert all(s["start"] >= v["front"]["end"] for s in sides)
    assert v["quarter2"]["start"] < v["quarter"]["end"] and v["quarter"]["start"] < v["quarter2"]["end"]
    assert v["back"]["start"] >= max(s["end"] for s in sides)
    # each side in its own order
    assert v["left"]["start"] >= v["quarter"]["end"] and v["right"]["start"] >= v["quarter2"]["end"]
    # the lent card is handed back once, before the back is drawn, without an interrupt
    assert given_back == [lease] and lease.ended and lease.client.interrupts == 0
    cards = sc.metrics["cards"]
    assert cards == {"mode": "two cards", "card": "a", "second_card": "b"}
    views = sc.metrics["views"]
    assert {k: views[k]["card"] for k in VIEWS} == {"front": "a", "left": "a", "back": "a", "right": "b",
                                                     "quarter": "a", "quarter2": "b"}
    assert any("at the same time" in m for m in sc.messages)
    # the lent card got its own copy of the start picture; the pair checks ran on the right side's views
    assert ("vgac_nnnnnnnn_concept_b.png", "b") in sc.staged_on
    assert "pair_as_drawn" in views["right"] and "pair_as_drawn" in views["quarter2"]
    assert "pair_as_drawn" not in views["left"] and "pair_as_drawn" not in views["quarter"]
    assert all(views[k]["passed"] for k in VIEWS)


def test_same_seeds_prompts_and_settings_one_card_or_two(tmp_path, engines):
    (tmp_path / "x").mkdir()
    run_views(tmp_path / "x", (make_lease([]), ""))
    two = {c["view"]: (c["seed"], c["extra"], c["lora"]) for c in engines["calls"]}
    engines["calls"].clear()
    (tmp_path / "y").mkdir()
    run_views(tmp_path / "y", (None, "this computer has one graphics card"))
    one = {c["view"]: (c["seed"], c["extra"], c["lora"]) for c in engines["calls"]}
    assert two == one
    # the seed is the base seed plus the view's place in the usual order
    order = ["front", "left", "back", "right", "quarter", "quarter2"]
    assert {v: s for v, (s, _, _) in one.items()} == {v: 100 + i for i, v in enumerate(order)}


def test_one_card_draws_one_after_another_in_the_usual_order(tmp_path, engines):
    sc = run_views(tmp_path, (None, "this computer has one graphics card"))
    calls = engines["calls"]
    assert [c["view"] for c in calls] == ["front", "left", "back", "right", "quarter", "quarter2"]
    assert {c["card"] for c in calls} == {"a"}
    assert all(b["start"] >= a["end"] for a, b in zip(calls, calls[1:]))
    assert sc.metrics["cards"] == {"mode": "one after another", "why": "this computer has one graphics card", "card": "a"}
    assert not any("at the same time" in m for m in sc.messages)


@pytest.mark.parametrize("why", ["RTX 3060 is busy with another step", "RTX 3060 has work waiting",
                                 "RTX 3060 has 3.1 GB of memory for drawing, 7 GB are needed"])
def test_no_free_second_card_records_why(tmp_path, engines, why):
    sc = run_views(tmp_path, (None, why))
    assert [c["view"] for c in engines["calls"]] == ["front", "left", "back", "right", "quarter", "quarter2"]
    assert sc.metrics["cards"]["mode"] == "one after another" and sc.metrics["cards"]["why"] == why


def test_a_step_without_a_queue_runs_as_before(tmp_path, engines):
    sc = run_views(tmp_path, None)
    assert [c["view"] for c in engines["calls"]] == ["front", "left", "back", "right", "quarter", "quarter2"]
    assert sc.metrics["cards"]["why"] == "this step cannot ask for a second card"


def test_a_failing_view_is_redrawn_on_its_own_side(tmp_path, engines, monkeypatch):
    real = turnaround.view_checks
    seen = {"right": 0}

    def right_fails_once(view, *a, **kw):
        info = real(view, *a, **kw)
        if view == "right":
            seen["right"] += 1
            if seen["right"] == 1:
                info["passed"] = info["ok"] = False
        return info

    monkeypatch.setattr(turnaround, "view_checks", right_fails_once)
    sc = run_views(tmp_path, (make_lease([]), ""))
    rights = [c for c in engines["calls"] if c["view"] == "right"]
    assert [c["card"] for c in rights] == ["b", "b"]
    assert rights[1]["seed"] != rights[0]["seed"]     # a redraw takes a new seed, as before
    assert sc.metrics["views"]["right"]["rerolls"] == 1 and sc.metrics["views"]["right"]["card"] == "b"
    back = by_view(engines["calls"])["back"]
    assert back["start"] >= rights[1]["end"]


def test_the_lent_engine_going_away_finishes_the_right_side_on_the_jobs_card(tmp_path, engines):
    given_back: list = []
    lease = make_lease(given_back)
    engines["fail"][("right", "b")] = httpx.ConnectError("refused")
    sc = run_views(tmp_path, (lease, ""))
    v = by_view(engines["calls"])
    assert v["quarter2"]["card"] == "b" and v["right"]["card"] == "a"
    assert v["right"]["seed"] == 103                   # the same seed, now on the job's card
    assert v["right"]["start"] >= v["left"]["end"]     # after the left side, on the same card
    assert v["back"]["start"] >= v["right"]["end"]
    assert given_back == [lease] and lease.client.interrupts == 0
    note = sc.metrics["cards"]["fallback"]
    assert "stopped answering" in note and "right side was finished on the" in note
    assert sc.metrics["views"]["right"]["card"] == "a"


def test_turning_the_lent_card_off_moves_the_rest_of_the_right_side_home(tmp_path, engines, monkeypatch):
    lease = make_lease([])
    real = turnaround._generate_view

    async def revoke_after_quarter2(sc, src_name, view, *a, **kw):
        out = await real(sc, src_name, view, *a, **kw)
        if view == "quarter2":
            lease.revoked, lease.revoked_reason = True, "RTX 3080 was turned off"
        return out

    monkeypatch.setattr(turnaround, "_generate_view", revoke_after_quarter2)
    sc = run_views(tmp_path, (lease, ""))
    v = by_view(engines["calls"])
    assert v["quarter2"]["card"] == "b" and v["right"]["card"] == "a" and lease.ended
    assert sc.metrics["cards"]["fallback"].startswith("RTX 3080 was turned off")


def test_another_failure_on_the_lent_card_stops_the_step(tmp_path, engines):
    lease = make_lease([])
    engines["fail"][("quarter2", "b")] = ComfyError("prompt failed: out of memory")
    concept, ta, new = _lineage(tmp_path)
    sc = TwoCardSC(tmp_path, new, FakeDB(concept, ta, new), (lease, ""))
    with pytest.raises(ComfyError, match="out of memory"):
        asyncio.run(turnaround._run_views(sc, list(VIEWS), {"seed": 100}, concept))
    assert lease.ended and lease.client.interrupts == 1
    assert "back" not in by_view(engines["calls"])     # nothing after the failure
    assert sc.metrics["cards"]["mode"] == "two cards"


def test_stopping_the_job_hands_the_lent_card_back(tmp_path, engines):
    given_back: list = []
    lease = make_lease(given_back)
    concept, ta, new = _lineage(tmp_path)
    sc = TwoCardSC(tmp_path, new, FakeDB(concept, ta, new), (lease, ""))

    async def go():
        task = asyncio.create_task(turnaround._run_views(sc, list(VIEWS), {"seed": 100}, concept))
        await asyncio.sleep(DRAW_SECONDS * 1.5)        # the front is done, both sides are drawing
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert given_back == [lease] and lease.client.interrupts == 1
    drawn = {c["view"] for c in engines["calls"]}
    assert drawn == {"front"}
