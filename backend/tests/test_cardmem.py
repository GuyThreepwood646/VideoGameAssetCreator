"""The card's memory as the driver sees it (vgac.cardmem), for the lending guard and the panels. Measured 2026-11-13
21:38: the 3060 at 11.2 of 12 GB used and 99 percent busy with another ComfyUI, while the app's engine on it (ComfyUI
0.45.3, DynamicVRAM) reported 10.89 GB free: its figure is its own process's view. nvidia-smi is faked here; the
conftest keeps the real one out of every test."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vgac import cardmem, jobs
from test_card_lending import KIND, make_queue, queued_job
from test_gpus import client, fake_execute  # noqa: F401  (the app client fixture)

GB = 1024**3
SMI_BUSY = ("0, 00000000:01:00.0, NVIDIA GeForce RTX 3080, 10240, 3597, 6435, 20\n"
            "1, 00000000:05:00.0, NVIDIA GeForce RTX 3060, 12288, 11438, 686, 98\n")
SMI_IDLE = ("0, 00000000:01:00.0, NVIDIA GeForce RTX 3080, 10240, 3597, 6435, 4\n"
            "1, 00000000:05:00.0, NVIDIA GeForce RTX 3060, 12288, 1833, 10281, 0\n")


class Engine:
    """An engine whose own figures are its process's view (DynamicVRAM): total minus what it holds; torch sees 0."""

    def __init__(self, card: str, total_gb: float, own_gb: float, queue=None, up: bool = True) -> None:
        self.card, self.total_gb, self.own_gb, self.up = card, total_gb, own_gb, up
        self.queue = queue or {"queue_running": [], "queue_pending": []}
        self.frees = 0

    def summary(self) -> dict[str, Any]:
        if not self.up:
            return {"reachable": False, "devices": []}
        return {"reachable": True, "devices": [{
            "name": f"cuda:0 NVIDIA GeForce {self.card} : cudaMallocAsync", "vram_total": self.total_gb * GB,
            "vram_free": (self.total_gb - self.own_gb) * GB, "torch_vram_total": 0.06 * GB,
            "torch_vram_free": 0.05 * GB}]}

    async def cached_vram_summary(self, max_age: float = 5.0):
        return self.summary()

    async def vram_summary(self):
        return self.summary()

    def last_vram_summary(self):
        return self.summary()

    async def queue_state(self):
        return self.queue

    async def free(self) -> bool:
        self.frees += 1
        return True

    async def interrupt(self) -> None:
        pass


@pytest.fixture
def named(settings, monkeypatch):
    a = settings.instances["a"].model_copy(update={"gpu": "RTX 3060"})
    b = settings.instances["b"].model_copy(update={"gpu": "RTX 3080"})
    monkeypatch.setitem(settings.instances, "a", a)
    monkeypatch.setitem(settings.instances, "b", b)
    monkeypatch.setattr(jobs, "LEND_FREE_SETTLE_SECONDS", 0.0)
    return settings


def smi(monkeypatch, *texts):
    """Fake nvidia-smi: each call answers the next text (the last one again after that)."""
    answers = list(texts)
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        return answers.pop(0) if len(answers) > 1 else answers[0]

    cardmem.forget()
    monkeypatch.setattr(cardmem, "runner", run)
    return calls


# --- reading and matching ------------------------------------------------------------------------------------------

def test_parse_and_cache(monkeypatch):
    calls = smi(monkeypatch, SMI_BUSY)
    cards = cardmem.read()
    assert [c.name for c in cards] == ["NVIDIA GeForce RTX 3080", "NVIDIA GeForce RTX 3060"]
    c = cards[1]
    assert c.index == 1 and c.total_gb == 12.0 and c.free_gb == pytest.approx(0.67, abs=0.01) and c.util == 98
    cardmem.read()
    assert calls["n"] == 1                       # cached
    cardmem.read(0.0)
    assert calls["n"] == 2
    assert cardmem.parse("garbage\n1, x, y, z, 1, 2, 3") == []


def test_no_nvidia_smi_is_none(monkeypatch):
    monkeypatch.setattr(cardmem.shutil, "which", lambda name: None)
    monkeypatch.setattr(cardmem, "runner", cardmem._run_nvidia_smi)
    cardmem.forget()
    assert cardmem.read(0.0) is None and cardmem.last() is None


def test_cards_are_matched_by_name_and_identical_ones_by_cuda_device(named, monkeypatch):
    cards = cardmem.parse(SMI_BUSY)
    assert cardmem.card_of(named, "a", None, cards).index == 1
    assert cardmem.card_of(named, "b", None, cards).index == 0
    twins = cardmem.parse("0, x, NVIDIA GeForce RTX 3060, 12288, 1000, 11288, 0\n"
                          "1, y, NVIDIA GeForce RTX 3060, 12288, 9000, 3288, 50\n")
    assert cardmem.card_of(named, "a", None, twins) is None          # cannot tell which: the engine's figure is used
    eng = type("E", (), {"cuda_device": 1})()
    fake = type("S", (), {"instances": {"a": type("I", (), {"gpu": "RTX 3060", "discover": None, "engine": eng})()}})()
    assert cardmem.card_of(fake, "a", None, twins).index == 1


def test_one_card_one_instance_with_an_unhelpful_name(settings, monkeypatch):
    monkeypatch.delitem(settings.instances, "a")
    cards = cardmem.parse("0, x, GPU 0, 8192, 1000, 7192, 0\n")
    assert cardmem.card_of(settings, "b", None, cards).index == 0


# --- the lending guard ----------------------------------------------------------------------------------------------

def test_a_card_another_program_is_drawing_on_is_not_lent(named, monkeypatch):
    """The 21:38 case: the engine says 10.9 GB free, the card has 0.7: refused, and the idle engine is not unloaded."""
    smi(monkeypatch, SMI_BUSY)
    a = Engine("RTX 3060", 12.0, own_gb=1.11)
    q = make_queue(named, a=a)
    lease, why = asyncio.run(q.lend("b", "job1", KIND))
    assert lease is None and a.frees == 0
    assert "0.7 GB of memory for drawing, 7 GB are needed" in why and "another program is using the card" in why


def test_an_idle_card_is_lent_on_the_drivers_figure(named, monkeypatch):
    smi(monkeypatch, SMI_IDLE)
    q = make_queue(named, a=Engine("RTX 3060", 12.0, own_gb=1.11))
    lease, why = asyncio.run(q.lend("b", "job1", KIND))
    assert lease is not None and why == ""


def test_the_engines_own_model_is_unloaded_and_the_driver_read_again(named, monkeypatch):
    """The 17:50 case: the engine holds the drawing model (its own 9 GB), the card has 3.8 free; after /free the
    driver shows 10 GB free."""
    holding = "1, 00000000:05:00.0, NVIDIA GeForce RTX 3060, 12288, 8276, 3840, 0\n"
    calls = smi(monkeypatch, holding, SMI_IDLE)
    a = Engine("RTX 3060", 12.0, own_gb=8.96)
    q = make_queue(named, a=a)
    lease, why = asyncio.run(q.lend("b", "job1", KIND))
    assert lease is not None and a.frees == 1 and calls["n"] == 2
    assert "3.8 GB before, 10.0 GB after" in lease.note


def test_without_nvidia_smi_the_engines_figure_is_used_and_said(named, monkeypatch):
    # no figures (the conftest default): an engine that reads 3 GB is refused and the reason says whose figure it is
    q = make_queue(named, a=Engine("RTX 3060", 12.0, own_gb=9.0, queue={"queue_running": [["x"]], "queue_pending": []}))
    lease, why = asyncio.run(q.lend("b", "job1", KIND))
    assert lease is None and "as its engine reports it" in why
    q2 = make_queue(named, a=Engine("RTX 3060", 12.0, own_gb=1.0))
    assert asyncio.run(q2.lend("b", "job1", KIND))[0] is not None


def test_one_card_machine(named, monkeypatch):
    smi(monkeypatch, "0, x, NVIDIA GeForce RTX 3080, 10240, 3000, 7240, 0\n")
    monkeypatch.delitem(named.instances, "a")
    q = make_queue(named)
    lease, why = asyncio.run(q.lend("b", "job1", KIND))
    assert lease is None and why == "this computer has one graphics card"


# --- the panels -----------------------------------------------------------------------------------------------------

def test_gpus_show_the_drivers_figures_and_work_outside_the_app(named, monkeypatch):
    smi(monkeypatch, SMI_BUSY)
    cardmem.read()
    q = make_queue(named, a=Engine("RTX 3060", 12.0, own_gb=1.11), b=Engine("RTX 3080", 10.0, own_gb=2.3))
    rows = {r["instance"]: r for r in q.gpus()}
    a = rows["a"]
    assert a["memory"]["source"] == "card" and abs(a["vram_free_mb"] - 686) <= 6 and a["busy_outside"] is True
    assert a["memory"]["others_gb"] == pytest.approx(10.06, abs=0.05)
    b = rows["b"]
    assert abs(b["vram_free_mb"] - 6435) <= 6 and b["busy_outside"] is False
    # the app's own job on the card: not "outside"
    q.workers["a"].current_job_id = "j"
    assert {r["instance"]: r for r in q.gpus()}["a"]["busy_outside"] is False


def test_gpus_without_figures_say_they_are_the_engines(named):
    q = make_queue(named, a=Engine("RTX 3060", 12.0, own_gb=1.11))
    a = {r["instance"]: r for r in q.gpus()}["a"]
    assert a["memory"]["source"] == "engine" and a["busy_outside"] is False
    assert a["vram_free_mb"] == round(10.89 * 1024)


def test_health_carries_the_figures(client, monkeypatch):
    smi(monkeypatch, "0, x, NVIDIA GeForce test, 10240, 3000, 7240, 5\n")
    h = client.get("/api/health").json()
    for name in ("a", "b"):
        inst = h["instances"][name]
        assert "memory" in inst and inst["working"] is False and "busy_outside" in inst


def test_the_card_panel_and_the_checks_list_show_one_figure(client, named, monkeypatch):
    """2026-11-14: /api/gpus read the idle loop's snapshot (up to 15 s old) and /api/health the 3 s one, so the two
    showed 9.5 and 4.7 GB for the same card in the same minute. Both now read through the same cache."""
    engines = {"a": Engine("RTX 3060", 12.0, own_gb=1.11), "b": Engine("RTX 3080", 10.0, own_gb=0.8)}
    for n, e in engines.items():
        monkeypatch.setattr(jobs.active_queue().clients[n], "cached_vram_summary", e.cached_vram_summary)
        monkeypatch.setattr(jobs.active_queue().clients[n], "last_vram_summary", e.last_vram_summary)
    from vgac import main
    monkeypatch.setattr(main, "_health_client", lambda n, url: engines[n])
    smi(monkeypatch, SMI_IDLE)
    cardmem.read(0.0)
    cardmem.runner = lambda: SMI_BUSY  # the card gets busy; the old snapshot is what the idle loop would still hold
    health = client.get("/api/health").json()["instances"]
    gpus = {g["instance"]: g for g in client.get("/api/gpus").json()}
    for n in ("a", "b"):
        assert health[n]["memory"]["source"] == gpus[n]["memory"]["source"] == "card"
        assert health[n]["memory"]["used_gb"] == gpus[n]["memory"]["used_gb"]
        # total minus free is not the driver's used figure (they differ by about 0.2 GB): the panel shows memory.used_gb


def test_gpus_takes_the_reading_it_is_given(named):
    q = make_queue(named, a=Engine("RTX 3060", 12.0, own_gb=1.11))
    smi_cards = cardmem.parse(SMI_BUSY)
    cardmem.forget()
    a = {r["instance"]: r for r in q.gpus(None, smi_cards)}["a"]
    assert a["memory"]["source"] == "card" and a["memory"]["used_gb"] == round(11438 / 1024, 2)
