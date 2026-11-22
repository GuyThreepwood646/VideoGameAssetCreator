"""Read-only report of where the pipeline's time goes (WP-91 part 1).

Reads `metrics.timing` (see vgac/steptime.py) from finished versions in the library database, opened read-only
(sqlite `mode=ro`, never written), and prints, per step and per part, how many runs, the median and the upper quartile
(75th percentile) in seconds. Every step also gets a `job (start to finish)` line from the job's own start and finish
times, which exists for versions made before the timing was recorded.

    python backend/tools/timing_report.py --db library/vgac.sqlite
    python backend/tools/timing_report.py --since 2026-11-20 --category humanoid --card b --exclude-tests

Filters: --since / --until (date of the version, YYYY-MM-DD, inclusive), --category, --card (the card the job ran on:
a, b or host), --step. Projects whose names look like tests, checks or trials (--test-pattern) are included by
default; --exclude-tests leaves them out and --only-tests keeps only them.

Read the numbers with care: a part's seconds are wall time, so a part that waits for a card or the host's one Blender
includes the wait, parts drawn on two cards at once overlap (their sum can exceed the step's total), and a few parts sit
inside another (see the notes in vgac/steptime.py and ARCHITECTURE.md).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_TEST_PATTERN = r"(?i)\b(test|tests|check|checks|trial|trials|research|demo|proof|bakeoff|rct2?|wig)\b"
JOB = "job (start to finish)"
TOTAL = "total"
STEP_ORDER = ["concept", "turnaround", "mesh", "prep", "rig", "export"]

SQL = """
select v.id, v.stage, v.created_at, v.metrics_json, a.category, p.name as project, j.instance, j.started_at, j.finished_at
from version v
join asset a on a.id = v.asset_id
join project p on p.id = a.project_id
left join job j on j.id = v.job_id
where v.status = 'done'
"""


def quantile(values: list[float], q: float) -> float:
    """The q quantile (0 to 1) with linear steps between neighbours; one value is its own quantile."""
    xs = sorted(values)
    if not xs:
        raise ValueError("no values")
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _seconds(started: str | None, finished: str | None) -> float | None:
    try:
        if not started or not finished:
            return None
        return (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return None


def connect_read_only(db: Path) -> sqlite3.Connection:
    uri = Path(db).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def collect(db: Path, since: str | None = None, until: str | None = None, category: str | None = None,
            card: str | None = None, step: str | None = None, tests: str = "include",
            test_pattern: str = DEFAULT_TEST_PATTERN) -> dict[str, dict[str, list[float]]]:
    """{step: {part: [seconds, ...]}} for the finished versions that pass the filters. `tests`: include, exclude, only."""
    pat = re.compile(test_pattern)
    out: dict[str, dict[str, list[float]]] = {}
    con = connect_read_only(db)
    try:
        for vid, stage, created, metrics_json, cat, project, instance, started, finished in con.execute(SQL):
            day = str(created or "")[:10]
            if since and day < since or until and day > until:
                continue
            if category and cat != category or card and instance != card or step and stage != step:
                continue
            is_test = bool(pat.search(project or ""))
            if tests == "exclude" and is_test or tests == "only" and not is_test:
                continue
            parts = out.setdefault(stage, {})
            secs = _seconds(started, finished)
            if secs is not None:
                parts.setdefault(JOB, []).append(secs)
            try:
                timing = (json.loads(metrics_json or "{}") or {}).get("timing")
            except ValueError:
                timing = None
            if isinstance(timing, dict):
                for name, value in timing.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        parts.setdefault(name, []).append(float(value))
    finally:
        con.close()
    return out


def summarise(data: dict[str, dict[str, list[float]]]) -> list[dict[str, Any]]:
    """One row per step and part: {step, part, n, median, upper_quartile}, steps in pipeline order, parts by median, the
    total and the job line first."""
    rows: list[dict[str, Any]] = []
    steps = sorted(data, key=lambda s: STEP_ORDER.index(s) if s in STEP_ORDER else len(STEP_ORDER))
    for step in steps:
        parts = data[step]
        head = [p for p in (JOB, TOTAL) if p in parts]
        rest = sorted((p for p in parts if p not in head), key=lambda p: -quantile(parts[p], 0.5))
        for part in head + rest:
            vals = parts[part]
            rows.append({"step": step, "part": part, "n": len(vals), "median": round(quantile(vals, 0.5), 1),
                         "upper_quartile": round(quantile(vals, 0.75), 1)})
    return rows


def format_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No finished versions match.\n"
    width = max(len(r["part"]) for r in rows)
    lines = []
    last = None
    for r in rows:
        if r["step"] != last:
            lines.append("")
            lines.append(f"{r['step']}")
            lines.append(f"  {'part'.ljust(width)}  {'runs':>5}  {'median s':>9}  {'upper quartile s':>17}")
            last = r["step"]
        lines.append(f"  {r['part'].ljust(width)}  {r['n']:>5}  {r['median']:>9.1f}  {r['upper_quartile']:>17.1f}")
    return "\n".join(lines).lstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default="library/vgac.sqlite", help="the library database (opened read-only)")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--category")
    ap.add_argument("--card", help="a, b or host")
    ap.add_argument("--step")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--exclude-tests", action="store_true")
    g.add_argument("--only-tests", action="store_true")
    ap.add_argument("--test-pattern", default=DEFAULT_TEST_PATTERN)
    a = ap.parse_args(argv)
    tests = "exclude" if a.exclude_tests else "only" if a.only_tests else "include"
    data = collect(Path(a.db), a.since, a.until, a.category, a.card, a.step, tests, a.test_pattern)
    sys.stdout.write(format_rows(summarise(data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
