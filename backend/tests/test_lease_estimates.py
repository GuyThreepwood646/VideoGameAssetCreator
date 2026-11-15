"""Step estimates with and without a second card (live check 2026-11-13: step 2 took 4.0 minutes with both cards and 16.0
with one, and the history held one number for both). The queue writes on the borrowing job how long it had a card
({"lent": [...]} in its result); the estimates keep the two kinds of run apart."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from vgac import estimates, jobs
from vgac.db import Asset, Job, Project, session_factory, utcnow
from test_card_lending import KIND, make_queue
from test_gpus import client, fake_execute  # noqa: F401
from test_lend_frees_memory import running_job, no_settle  # noqa: F401


def add_runs(kind: str, seconds: list[float], lent: float | None = None, category: str = "humanoid") -> None:
    with session_factory()() as db:
        p = Project(name="P")
        db.add(p)
        db.flush()
        a = Asset(project_id=p.id, name="x", category=category)
        db.add(a)
        db.flush()
        t = utcnow()
        for i, s in enumerate(seconds):
            end = t - timedelta(minutes=len(seconds) - i)
            j = Job(instance="b", kind=kind, asset_id=a.id, status="done", payload_json="{}", message="",
                    started_at=end - timedelta(seconds=s), finished_at=end)
            if lent is not None:
                j.result_json = json.dumps({"lent": [{"card": "a", "seconds": lent}]})
            db.add(j)
        db.commit()


def test_the_queue_writes_how_long_the_job_had_a_card(settings):
    q = make_queue(settings)
    jid = running_job()
    lease, why = asyncio.run(q.lend("b", jid, KIND))
    assert lease is not None
    asyncio.run(lease.end())
    asyncio.run(lease.end())  # twice is safe: one entry
    with session_factory()() as db:
        res = db.get(Job, jid).result
    assert [e["card"] for e in res["lent"]] == ["a"] and res["lent"][0]["seconds"] >= 0
    # a second lease on the same job adds an entry
    lease2, _ = asyncio.run(q.lend("b", jid, KIND))
    asyncio.run(lease2.end())
    with session_factory()() as db:
        assert len(db.get(Job, jid).result["lent"]) == 2


def test_a_job_that_borrowed_nothing_has_no_result(settings):
    q = make_queue(settings)
    jid = running_job()
    with session_factory()() as db:
        assert db.get(Job, jid).result is None


def test_lent_seconds_reads_the_result():
    assert estimates.lent_seconds(None) == 0 and estimates.lent_seconds("not json") == 0
    assert estimates.lent_seconds(json.dumps({"lent": [{"card": "a", "seconds": 40}, {"card": "a", "seconds": 5.5}]})) == 45.5


def test_runs_with_and_without_a_second_card_are_kept_apart(settings):
    add_runs("turnaround.run", [240, 250, 260], lent=200)           # both cards
    add_runs("turnaround.run", [900, 950, 1000], lent=None)         # one card (nothing recorded counts as one)
    with session_factory()() as db:
        assert estimates.stage_seconds(db, "turnaround", lent=True)[0] == 255
        assert estimates.stage_seconds(db, "turnaround", lent=False)[0] == 975
        assert estimates.stage_seconds(db, "turnaround")[0] > 255        # all runs: unchanged for callers that ask nothing
        assert estimates.split_estimates(db, "turnaround") == (255, 975)


def test_too_few_runs_of_a_kind_fall_back_to_all_runs(settings):
    add_runs("turnaround.run", [240, 250], lent=200)
    add_runs("turnaround.run", [900, 950, 1000], lent=None)
    with session_factory()() as db:
        assert estimates.split_estimates(db, "turnaround") == (None, 975)
        got = estimates.stage_seconds(db, "turnaround", lent=True)
        assert got is not None and got[1] == 5                           # the figure over every run


def test_the_stage_list_carries_both_figures_and_the_sentence(settings, client, monkeypatch):
    add_runs("turnaround.run", [240, 250, 260], lent=200)
    add_runs("turnaround.run", [900, 950, 1000], lent=None)
    monkeypatch.setattr(estimates, "second_card_free", lambda queue=None: True)
    s = next(x for x in client.get("/api/stages").json() if x["id"] == "turnaround")
    assert s["estimate_two_cards"] == 255 and s["estimate_one_card"] == 975
    assert s["estimate_seconds"] == 255                                   # a second card is free: the two-card figure
    assert s["estimate_note"] == "about 5 minutes with both cards, about 17 minutes with one"
    monkeypatch.setattr(estimates, "second_card_free", lambda queue=None: False)
    s = next(x for x in client.get("/api/stages").json() if x["id"] == "turnaround")
    assert s["estimate_seconds"] == 975


def test_no_sentence_when_the_two_figures_are_close(settings, client):
    add_runs("rig.run", [60, 61, 62], lent=40)
    add_runs("rig.run", [63, 64, 65], lent=None)
    s = next(x for x in client.get("/api/stages").json() if x["id"] == "rig")
    assert "estimate_note" not in s and s["estimate_two_cards"] is not None


def test_second_card_free_counts_idle_enabled_cards(settings):
    q = make_queue(settings)
    assert estimates.second_card_free(q) is True
    q.workers["a"].current_job_id = "x"
    assert estimates.second_card_free(q) is False
    q.workers["a"].current_job_id = None
    q.disabled.add("b")
    assert estimates.second_card_free(q) is False
