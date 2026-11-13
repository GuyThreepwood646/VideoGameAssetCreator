"""Offline, read-only check of D2 items 9 and 16 (docs/AFTER-TEST-CHANGES.md) against the stored clean-up runs of the
projects "RCT Realistic PC", "RCT Realistic Showcase" and "RCT2 Realistic PC" in library/vgac.sqlite. Nothing is written or
run again. Per run it prints the fit score, whether the face paint would now be held (fit under FIT_HOLD_MIN), whether
the face close-up was rejected and why, and whether the new code would have drawn it again.

    cd backend && .venv/Scripts/python.exe ../tools/verify_face_fit_hold.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from vgac.stages import face_detail as fd  # noqa: E402

PROJECTS = ("RCT Realistic PC", "RCT Realistic Showcase", "RCT2 Realistic PC")


def prep_runs(project: str) -> dict[str, dict]:
    """{asset name: the selected finished clean-up version's metrics}."""
    con = sqlite3.connect(f"file:{ROOT / 'library' / 'vgac.sqlite'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: dict[str, dict] = {}
    for a in con.execute("select a.id, a.name from asset a join project p on p.id=a.project_id where p.name=?", (project,)):
        for v in con.execute("select metrics_json from version where asset_id=? and stage='prep' and status='done' and selected=1",
                             (a["id"],)):
            out[a["name"]] = json.loads(v["metrics_json"] or "{}")
    return out


def judge(metrics: dict) -> dict:
    d = (metrics or {}).get("face_detail") or {}
    cu = d.get("closeup") or {}
    score = (d.get("alignment") or {}).get("score")
    held = score is not None and float(score) < fd.FIT_HOLD_MIN
    wanted = bool(cu.get("wanted"))
    rejected = wanted and cu.get("used") is False
    reason = cu.get("reason")
    # what the new code does: a retry for a reason in retry_wanted, unless the model's own face was not found (not stored;
    # the stored reason "no_features" is then ambiguous, so it counts as a retry here)
    retry = rejected and fd.retry_wanted({"ok": False, "reason": reason})
    old_tries = len(cu.get("tries") or []) or (1 if wanted else 0)
    return {"score": score, "held": held, "ran": bool(d.get("ran")), "skipped": d.get("skipped"), "wanted": wanted,
            "rejected": rejected, "reason": reason, "likeness": cu.get("likeness"), "retry": retry, "old_draws": old_tries,
            "check": next((c for c in (metrics.get("checks") or []) if c.get("name") == "face_matches_picture"), None)}


def main() -> None:
    held_names: list[str] = []
    for project in PROJECTS:
        print(f"\n{project}")
        print(f"  {'run':22s} {'fit':>6s}  {'item 9':8s} {'close-up':28s} item 16")
        for name, m in sorted(prep_runs(project).items()):
            j = judge(m)
            fit = "-" if j["score"] is None else f"{float(j['score']):.3f}"
            nine = "HOLD" if j["held"] else "paint"
            if not j["wanted"]:
                cu = "not wanted"
            elif j["rejected"]:
                cu = f"rejected: {j['reason']} ({j['likeness']})"
            else:
                cu = "used" if (m.get("face_detail") or {}).get("closeup", {}).get("used") else "not drawn"
            sixteen = "would retry (up to 2 more)" if j["retry"] else ("rejected, no retry for this reason" if j["rejected"] else "-")
            print(f"  {name:22s} {fit:>6s}  {nine:8s} {cu:28s} {sixteen}")
            if j["held"]:
                held_names.append(f"{project.split()[-1]}/{name}")
    print("\nHeld by item 9:", ", ".join(held_names) or "none")


if __name__ == "__main__":
    main()
