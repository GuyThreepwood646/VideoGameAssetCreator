"""WP-91 part 1: the per-step time log (vgac/steptime.py) and the read-only report (backend/tools/timing_report.py)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.test_same_side import _run
from tests.test_stage_offloop import FakeSC
from vgac import db as dbm
from vgac import steptime

_spec = importlib.util.spec_from_file_location("timing_report", Path(__file__).resolve().parents[1] / "tools" / "timing_report.py")
timing_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(timing_report)


class Bare:
    """A stage context with only what steptime uses."""

    def __init__(self):
        self.metrics = {}

    def set_metrics(self, **values):
        self.metrics.update(values)


def test_parts_add_up_and_the_total_is_written_last():
    sc = Bare()
    steptime.begin(sc)
    steptime.add(sc, "a", 1.04)
    steptime.add(sc, "a", 1.04)
    with steptime.part(sc, "b"):
        pass
    steptime.finish(sc)
    t = sc.metrics["timing"]
    assert t["a"] == 2.1 and "b" in t and list(t)[-1] == "total" and t["total"] >= 0


def test_a_failing_timer_never_fails_the_job():
    class Broken(Bare):
        def set_metrics(self, **values):
            raise RuntimeError("database is locked")

    sc = Broken()
    steptime.begin(sc)
    with steptime.part(sc, "x"):
        pass
    steptime.add(sc, "y", 2)
    steptime.finish(sc)   # nothing raised

    class NoAttrs:
        __slots__ = ()

        def set_metrics(self, **values):
            pass

    steptime.add(NoAttrs(), "z", 1)   # cannot even keep a timer: still silent


def test_a_part_that_raises_is_still_timed_and_the_error_passes_through():
    sc = Bare()
    steptime.begin(sc)
    with pytest.raises(ValueError):
        with steptime.part(sc, "boom"):
            raise ValueError("x")
    assert "boom" in sc.metrics["timing"]


def test_step_2_run_writes_a_draw_and_a_redraw_per_view(tmp_path, monkeypatch):
    made = []
    orig = FakeSC.__init__

    def keep(self, *a, **k):
        orig(self, *a, **k)
        made.append(self)

    monkeypatch.setattr(FakeSC, "__init__", keep)
    views, draws = _run(tmp_path, monkeypatch, right_faces=[False])   # the right view fails, is redrawn once, stays flagged
    timing = made[-1].metrics["timing"]
    assert draws["right"] == 2 and views["right"]["rerolls"] == 1
    assert {"draw_front", "draw_left", "draw_right", "redraw_right"} <= set(timing)
    assert "redraw_front" not in timing and all(isinstance(v, float) for v in timing.values())


# --- the report -------------------------------------------------------------------------------------------------------

T0 = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)


def _library(tmp_path: Path, runs: list[dict]) -> Path:
    """A small made-up library: each run is {stage, job_seconds, parts, project, category, card, day, status}."""
    path = tmp_path / "vgac.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    dbm.Base.metadata.create_all(engine)
    with Session(engine) as s:
        projects, assets = {}, {}
        for r in runs:
            pname, cat = r.get("project", "Real work"), r.get("category", "humanoid")
            if pname not in projects:
                projects[pname] = dbm.Project(name=pname)
                s.add(projects[pname])
                s.flush()
            if (pname, cat) not in assets:
                assets[(pname, cat)] = dbm.Asset(project_id=projects[pname].id, name="a", category=cat)
                s.add(assets[(pname, cat)])
                s.flush()
            start = T0 + timedelta(days=r.get("day", 0))
            job = dbm.Job(instance=r.get("card", "b"), kind=r["stage"], status="done", started_at=start,
                          finished_at=start + timedelta(seconds=r["job_seconds"]))
            s.add(job)
            s.flush()
            metrics = {"timing": r["parts"]} if r.get("parts") else {}
            s.add(dbm.Version(asset_id=assets[(pname, cat)].id, stage=r["stage"], status=r.get("status", "done"), job_id=job.id,
                              metrics_json=json.dumps(metrics), created_at=start))
        s.commit()
    engine.dispose()
    return path


def _row(rows, step, part):
    return next(r for r in rows if r["step"] == step and r["part"] == part)


def test_the_report_gives_the_right_medians_and_upper_quartiles(tmp_path):
    runs = [{"stage": "prep", "job_seconds": 100 + i, "parts": {"pose_search": float(v), "total": 100.0 + i}}
            for i, v in enumerate([10, 20, 30, 40, 50])]
    rows = timing_report.summarise(timing_report.collect(_library(tmp_path, runs)))
    ps = _row(rows, "prep", "pose_search")
    assert (ps["n"], ps["median"], ps["upper_quartile"]) == (5, 30.0, 40.0)
    job = _row(rows, "prep", timing_report.JOB)
    assert (job["median"], job["upper_quartile"]) == (102.0, 103.0)
    assert timing_report.quantile([1, 2, 3, 4], 0.5) == 2.5   # an even count sits between the middle two
    assert [r["part"] for r in rows if r["step"] == "prep"][:2] == [timing_report.JOB, "total"]


def test_the_report_filters_by_date_category_card_and_test_projects(tmp_path):
    runs = [
        {"stage": "rig", "job_seconds": 10, "parts": {"skintoken": 1.0}, "day": 0, "category": "humanoid", "card": "b"},
        {"stage": "rig", "job_seconds": 20, "parts": {"skintoken": 2.0}, "day": 5, "category": "humanoid", "card": "a"},
        {"stage": "rig", "job_seconds": 30, "parts": {"skintoken": 3.0}, "day": 5, "category": "prop", "card": "b"},
        {"stage": "rig", "job_seconds": 40, "parts": {"skintoken": 4.0}, "day": 5, "category": "humanoid", "card": "b",
         "project": "Pose check card test"},
        {"stage": "rig", "job_seconds": 99, "parts": {"skintoken": 99.0}, "day": 5, "status": "rejected"},
    ]
    db = _library(tmp_path, runs)

    def parts(**kw):
        got = timing_report.collect(db, **kw)
        return sorted(got["rig"]["skintoken"]) if got else []

    assert parts() == [1.0, 2.0, 3.0, 4.0]                       # a rejected version is never counted
    assert parts(since="2026-11-22") == [2.0, 3.0, 4.0]
    assert parts(until="2026-11-21") == [1.0]
    assert parts(category="prop") == [3.0]
    assert parts(card="a") == [2.0]
    assert parts(tests="exclude") == [1.0, 2.0, 3.0]
    assert parts(tests="only") == [4.0]


def test_versions_without_timing_count_by_job_time_and_the_database_is_not_written(tmp_path):
    db = _library(tmp_path, [{"stage": "mesh", "job_seconds": 60}, {"stage": "mesh", "job_seconds": 80}])
    before = db.read_bytes()
    rows = timing_report.summarise(timing_report.collect(db))
    assert _row(rows, "mesh", timing_report.JOB)["median"] == 70.0 and len(rows) == 1
    assert db.read_bytes() == before
    con = timing_report.connect_read_only(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("delete from version")
    con.close()


def test_a_planted_slow_part_shows_up_in_the_report(tmp_path):
    """The report can catch a slow part: the same 12 runs, then one part made 10 times slower in 4 of them. Without the
    plant the part's upper quartile is 30; with it the upper quartile is far higher, and the printed table shows it."""
    base = [{"stage": "prep", "job_seconds": 200, "parts": {"projection": 30.0, "face_detail": 40.0, "total": 200.0}} for _ in range(12)]
    slow = [dict(r, parts={**r["parts"], "projection": 300.0}) if i < 4 else r for i, r in enumerate(base)]
    (tmp_path / "plain").mkdir()
    (tmp_path / "planted").mkdir()
    plain = timing_report.summarise(timing_report.collect(_library(tmp_path / "plain", base)))
    planted = timing_report.summarise(timing_report.collect(_library(tmp_path / "planted", slow)))
    assert _row(plain, "prep", "projection")["upper_quartile"] == 30.0
    got = _row(planted, "prep", "projection")
    assert got["median"] == 30.0 and got["upper_quartile"] > 100.0
    text = timing_report.format_rows(planted)
    assert f"{got['upper_quartile']:.1f}" in text


def test_the_report_runs_from_the_command_line(tmp_path, capsys):
    db = _library(tmp_path, [{"stage": "export", "job_seconds": 33, "parts": {"unity": 20.0}}])
    assert timing_report.main(["--db", str(db), "--step", "export"]) == 0
    out = capsys.readouterr().out
    assert "export" in out and "unity" in out and "20.0" in out
