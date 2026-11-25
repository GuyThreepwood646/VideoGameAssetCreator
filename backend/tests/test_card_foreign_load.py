"""A new job goes to the card with less waiting work when its usual card holds work another sender put in the engine
(a session's job), never waiting behind it while the other card is idle. Readings are stubbed; a card whose engine is
down, slow or stale counts as unknown and the choice stays as before."""

from __future__ import annotations

import asyncio
import time

from vgac import jobs
from vgac.comfy import ComfyClient
from vgac.db import Job
from test_host_lanes import add_job, make_assets


def fresh(q, **cards):
    now = time.monotonic()
    q.foreign = {n: (c, now) for n, c in cards.items()}


def place(q, stage="turnaround"):
    return q.place(stage, "")[0]


def test_one_card_with_foreign_work_sends_the_job_to_the_other(settings):
    q = jobs.JobQueue(settings)
    fresh(q, a=1, b=0)
    q.default_instance = "a"
    assert place(q) == "b"          # the default card holds another sender's job, b is free
    q.default_instance = None
    fresh(q, a=0, b=2)
    assert place(q) == "a"          # the table's card (b) holds work, a is free


def test_both_idle_keeps_todays_choice(settings):
    q = jobs.JobQueue(settings)
    fresh(q, a=0, b=0)
    assert place(q) == jobs.preferred_instance("turnaround", "", settings)
    q.default_instance = "a"
    assert place(q) == "a"


def test_both_busy_equal_keeps_todays_choice_and_more_busy_loses(settings):
    q = jobs.JobQueue(settings)
    fresh(q, a=2, b=2)
    assert place(q) == "b"
    fresh(q, a=3, b=1)
    assert place(q) == "b"          # the other card is worse
    fresh(q, a=1, b=3)
    assert place(q) == "a"          # the other card has less


def test_our_own_waiting_jobs_count_against_the_free_card(settings):
    q = jobs.JobQueue(settings)
    assets = make_assets(3)
    for a in assets:
        add_job("turnaround.run", a, instance="a")  # three of the app's own jobs wait on a
    fresh(q, a=0, b=1)
    assert place(q) == "b"          # b holds 1 foreign; a holds 0 foreign + 3 ours, no gain
    q.default_instance = "b"
    assert place(q) == "b"
    fresh(q, a=0, b=4)
    q.default_instance = None
    assert place(q) == "a"          # b holds 4, a holds 3 of ours: a is still less
    assert q.card_load()["a"] == (0, 3)


def test_unknown_or_stale_readings_keep_todays_choice(settings):
    q = jobs.JobQueue(settings)
    q.default_instance = "a"
    q.foreign = {"a": (3, time.monotonic())}                 # b has no reading
    assert place(q) == "a"
    q.foreign = {"a": (3, time.monotonic() - jobs.LOAD_STALE_SECONDS - 1), "b": (0, time.monotonic())}
    assert place(q) == "a"                                   # a's reading is stale
    q.foreign = {}
    assert place(q) == "a"


def test_mesh_and_host_and_off_cards_are_not_moved(settings):
    q = jobs.JobQueue(settings)
    fresh(q, a=5, b=0)
    assert place(q, "mesh") == "a"                           # the stage table's card for 3D builds stays
    assert place(q, "prep") == jobs.HOST
    q.disabled = {"b"}
    assert place(q, "concept") == "a"                        # b is off: nothing to move to


def test_allowed_kinds_are_kept(settings):
    q = jobs.JobQueue(settings)
    q.allowed_kinds["a"] = set()                             # a may do nothing
    fresh(q, a=0, b=3)
    assert place(q) == "b"


def test_route_without_a_reading_is_unchanged(settings):
    assert jobs.route("turnaround", "", settings) == "b"
    assert jobs.route("turnaround", "", settings, load={"a": None, "b": (2, 0)}) == "b"
    assert jobs.spread_by_load("b", ["a", "b"], {"a": (0, 0), "b": (0, 4)}) == "b"   # no foreign work on b: stay


def test_a_down_engine_loses_its_reading_quickly(settings):
    async def main():
        q = jobs.JobQueue(settings)
        q.clients = {"a": ComfyClient("http://127.0.0.1:1", name="a"), "b": ComfyClient("http://127.0.0.1:1", name="b")}
        q.foreign = {"a": (1, time.monotonic())}
        t = time.monotonic()
        await q.poll_foreign_once()
        assert time.monotonic() - t < 2.5
        assert q.foreign == {}
        assert place(q) == "b"
        for c in q.clients.values():
            await c.aclose()
    asyncio.run(main())


def test_a_slow_engine_is_given_up_within_two_seconds(settings):
    class Slow:
        async def get(self, *a, **k):
            await asyncio.sleep(30)

    async def main():
        q = jobs.JobQueue(settings)
        c = ComfyClient("http://127.0.0.1:1", name="a")
        c._client = lambda: Slow()
        q.clients = {"a": c}
        t = time.monotonic()
        await q.poll_foreign_once()
        assert time.monotonic() - t < 3.0
        assert "a" not in q.foreign
    asyncio.run(main())


def test_only_prompts_the_app_did_not_send_count(settings):
    class Reply:
        def raise_for_status(self): pass
        def json(self):
            return {"queue_running": [[1, "mine", {}], [2, "theirs", {}]], "queue_pending": [[3, "theirs2", {}]]}

    class Fake:
        async def get(self, *a, **k):
            return Reply()

    async def main():
        c = ComfyClient("http://127.0.0.1:1", name="a")
        c._client = lambda: Fake()
        c.sent_ids["mine"] = None
        try:
            assert await c.foreign_count() == 2
        finally:
            c.sent_ids.pop("mine", None)
    asyncio.run(main())


def test_a_prompt_one_client_sent_to_another_cards_address_is_ours(settings):
    class Post:
        status_code = 200
        def json(self):
            return {"prompt_id": "lent-1"}

    class SendClient:
        async def post(self, *a, **k):
            return Post()

    class Reply:
        def raise_for_status(self): pass
        def json(self):
            return {"queue_running": [[1, "lent-1", {}]], "queue_pending": []}

    class ReadClient:
        async def get(self, *a, **k):
            return Reply()

    async def main():
        a = ComfyClient("http://127.0.0.1:1", name="a")
        b = ComfyClient("http://127.0.0.1:2", name="b")
        a._client = lambda: SendClient()
        b._client = lambda: ReadClient()

        async def adapt(graph):
            return graph
        a._adapt_names = adapt
        try:
            await a.queue_prompt({"1": {}})          # card A's client sends to B's address (a lent card)
            assert await b.foreign_count() == 0
        finally:
            a.sent_ids.pop("lent-1", None)
    asyncio.run(main())


def test_the_poller_stops_with_the_queue(settings):
    async def main():
        q = jobs.JobQueue(settings)
        q._load_task = asyncio.create_task(q._load_loop())
        await asyncio.sleep(0.05)
        task = q._load_task
        await q.stop()
        assert task.done() and q._load_task is None and q.foreign == {}
    asyncio.run(main())


def test_a_failing_reading_never_stops_a_job(settings, monkeypatch):
    q = jobs.JobQueue(settings)

    def boom():
        raise RuntimeError("database busy")

    monkeypatch.setattr(q, "card_load", boom)
    assert place(q) == "b"
