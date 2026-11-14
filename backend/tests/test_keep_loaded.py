"""Keep the picture models loaded between graphs (prefs keep_models_loaded, on by default). Measured 2026-11-14 on
the 3080: an edit draw right after a small other graph (an upscale) took 62 to 80 s against 32 s back to back, because
ComfyUI 0.45.3 drops every cached node output the current prompt does not use until the PC's free RAM is above its
"inactive" threshold, which defaults to all the RAM. `--cache-ram <active> <inactive>` with a lower inactive threshold
keeps the loaded models."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vgac import instances, jobs, prefs
from vgac.config import EngineConfig
from vgac.db import Job, session_factory
from test_card_lending import KIND, make_queue
from test_gpus import client, fake_execute  # noqa: F401
from test_lend_frees_memory import HoldingClient, running_job, no_settle  # noqa: F401


# --- the numbers ----------------------------------------------------------------------------------------------------


def test_the_thresholds_follow_the_pcs_memory():
    f = instances.cache_ram_gb
    assert f(96, 24) == (9, 14)    # this PC: kept until free RAM falls under 14 GB (24 GB dropped them too often)
    assert f(64, 24) == (6, 9)
    assert f(32, 24) == (3, 6)     # a smaller PC: 15 percent of its RAM, at least 6 GB
    assert f(16, 24) == (2, 6)
    assert f(8, 24) == (2, 6)
    assert f(96, 10) == (9, 10)    # a lower floor in machine.json lowers it
    for total in (8, 16, 32, 64, 96, 128):
        active, inactive = f(total, 24)
        assert inactive >= active and inactive <= max(24, 6)


# --- the launch command ---------------------------------------------------------------------------------------------


@pytest.fixture
def engine(settings, tmp_path, monkeypatch):
    comfy = tmp_path / "comfy"
    (comfy / "ComfyUI" / "comfy").mkdir(parents=True)
    (comfy / "ComfyUI" / "comfy" / "cli_args.py").write_text('parser.add_argument("--cache-ram", nargs="*")\n')
    inst = settings.instances["b"].model_copy(update={"engine": EngineConfig(comfy_dir=comfy, extra_args=["--lowvram"])})
    monkeypatch.setitem(settings.instances, "b", inst)

    monkeypatch.setattr(settings, "min_free_ram_gb", 24)

    class VM:
        total = 96 * 1024 ** 3

    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: VM)
    return comfy


def test_off_adds_nothing_and_on_adds_the_flag_after_the_rest(settings, engine):
    off, _, _ = instances.engine_command(settings, "b")
    assert "--cache-ram" not in off and off[-1] == "--lowvram"
    on, _, _ = instances.engine_command(settings, "b", keep_loaded=True)
    assert on[: len(off)] == off and on[len(off):] == ["--cache-ram", "9", "14"]


def test_the_person_s_own_cache_choice_wins_and_an_old_comfyui_gets_no_flag(settings, engine):
    inst = settings.instances["b"]
    own = inst.model_copy(update={"engine": inst.engine.model_copy(update={"extra_args": ["--cache-lru", "20"]})})
    settings.instances["b"] = own
    argv, _, _ = instances.engine_command(settings, "b", keep_loaded=True)
    assert "--cache-ram" not in argv
    settings.instances["b"] = inst
    (engine / "ComfyUI" / "comfy" / "cli_args.py").write_text("parser.add_argument('--lowvram')\n")
    argv, _, _ = instances.engine_command(settings, "b", keep_loaded=True)
    assert "--cache-ram" not in argv


# --- the setting ----------------------------------------------------------------------------------------------------


def test_on_by_default_and_remembered(settings):
    q = make_queue(settings)
    assert q.keep_loaded == {"a": True, "b": True}
    q.configure("b", keep_loaded=False)
    assert prefs.load(settings)["keep_models_loaded"] == {"a": True, "b": False}
    q2 = make_queue(settings)
    q2._load_prefs()
    assert q2.keep_loaded == {"a": True, "b": False}


def test_the_supervisor_asks_the_queues_setting(settings, engine):
    q = make_queue(settings)
    q.supervisor = instances.Supervisor(settings, q.clients)
    q.supervisor.keep_loaded = lambda n: q.keep_loaded.get(n, True)
    assert q.supervisor.keep_loaded("b") is True
    q.configure("b", keep_loaded=False)
    assert q.supervisor.keep_loaded("b") is False


def test_the_card_row_says_how_it_applies(settings, engine):
    q = make_queue(settings)
    rows = {r["instance"]: r for r in q.gpus()}
    assert rows["b"]["keep_loaded"] is True and rows["b"]["keep_loaded_how"] == "app"
    assert rows["b"]["keep_loaded_flag"] == "--cache-ram 9 14"
    assert rows["a"]["keep_loaded_how"] == "none" and rows["a"]["keep_loaded_flag"] == ""  # no engine settings


def test_the_row_of_an_engine_started_without_it_says_so(settings, engine):
    q = make_queue(settings)
    q.clients["b"].last_argv = lambda: ["ComfyUI\\main.py", "--port", "8189"]
    assert {r["instance"]: r for r in q.gpus()}["b"]["keep_loaded_applied"] is False
    q.clients["b"].last_argv = lambda: ["ComfyUI\\main.py", "--cache-ram", "9", "14"]
    assert {r["instance"]: r for r in q.gpus()}["b"]["keep_loaded_applied"] is True


def test_the_api_sets_it(client):
    r = client.put("/api/gpus/b", json={"keep_loaded": False})
    assert r.status_code == 200
    assert {g["instance"]: g for g in r.json()}["b"]["keep_loaded"] is False
    r = client.put("/api/gpus/b", json={"keep_loaded": True})
    assert {g["instance"]: g for g in r.json()}["b"]["keep_loaded"] is True


# --- it never stands in the way of lending a card -------------------------------------------------------------------


@pytest.mark.parametrize("keep", [True, False])
def test_an_idle_engine_that_keeps_its_models_still_lets_go_for_a_lend(settings, keep):
    """The setting only changes how the engine is started; the lending guard still asks an idle engine short of room to
    let go of its models, whatever the setting says, so the card is never held back from another step."""
    a = HoldingClient("a", free_gb=3.0, own_gb=8.3)
    q = make_queue(settings, a=a)
    q.keep_loaded["a"] = keep
    jid = running_job()
    lease, why = asyncio.run(q.lend("b", jid, KIND))
    assert lease is not None and why == "" and a.frees == 1
    with session_factory()() as db:
        assert db.get(Job, jid).message == lease.note


def test_free_after_each_step_still_wins(settings):
    q = make_queue(settings)
    q.configure("b", free_after_job=True, keep_loaded=True)
    assert q.workers["b"].free_after_job is True and q.keep_loaded["b"] is True


# --- it never holds back a job that waits for RAM -------------------------------------------------------------------


def test_a_job_held_for_ram_makes_idle_engines_give_back_their_cached_models(settings, monkeypatch):
    a = HoldingClient("a", free_gb=8.0, own_gb=0.0)
    b = HoldingClient("b", free_gb=8.0, own_gb=0.0)
    q = make_queue(settings, a=a, b=b)
    b.queue = {"queue_running": [{"x": 1}], "queue_pending": []}   # b is drawing: never asked
    asyncio.run(q.release_cached_ram())
    assert a.frees == 1 and b.frees == 0
    asyncio.run(q.release_cached_ram())                           # at most once a minute
    assert a.frees == 1
    q._ram_released_at = -1e9
    q.keep_loaded["a"] = False                                    # the person turned it off: nothing is cached to give back
    asyncio.run(q.release_cached_ram())
    assert a.frees == 1


def test_a_lent_or_working_card_is_left_alone(settings):
    a = HoldingClient("a", free_gb=8.0)
    q = make_queue(settings, a=a)
    q.workers["a"].lent_to = "some-job"
    asyncio.run(q.release_cached_ram())
    assert a.frees == 0


def test_the_worker_asks_for_it_while_it_holds_a_job_for_ram(settings, monkeypatch):
    from vgac.db import Asset, Project
    with session_factory()() as db:
        p = Project(name="P")
        db.add(p)
        db.flush()
        asset = Asset(project_id=p.id, name="x", category="humanoid")
        db.add(asset)
        db.flush()
        db.add(Job(instance="host", kind="prep.run", asset_id=asset.id, status="queued", payload_json="{}", message=""))
        db.commit()
    monkeypatch.setattr(settings, "min_free_ram_gb", 1e6)         # never enough
    calls = []

    async def ram_short():
        calls.append(1)

    async def main():
        w = jobs.InstanceWorker(name=jobs.HOST, settings=settings, clients={}, ram_short=ram_short)
        w.start()
        w.wake.set()
        for _ in range(60):
            if calls:
                break
            await asyncio.sleep(0.05)
        await w.stop()

    asyncio.run(main())
    assert calls
