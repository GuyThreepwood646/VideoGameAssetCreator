"""Which graphics cards a job used, written on its record (Job.result), so the person and the job table tell the truth.

Job.instance is the card a job is queued on; it is not the whole story. Job.result carries two lists:

- "cards": every card the job drew on, in order. Each entry is {card, role, kind, step, started_at, ended_at, seconds}.
  role "planned": the card the job was queued on when it was submitted (replaced when it starts);
  role "own": the card the job itself runs on (host for the clean-up and export), from its start to its end;
  role "lent": a second card the queue lent to the job for part of its work (a step that borrows, such as the side views);
  role "drew": a card the job held for its first close-up (the clean-up's face pass), not a loan.
- "moved": the moves of the job between cards, {from, to, at, why}: sharing, the default card, or a card turned off.

"lent" (only lent cards, with card and seconds) is kept as before: the estimates count a run as having had two cards
from it. Only this module writes these keys; the functions take the job row and the caller commits.
"""

from __future__ import annotations

from typing import Any

from .db import Job, utcnow

CARDS = "cards"
MOVED = "moved"
LENT_KEY = "lent"
PLANNED, OWN, LENT, DREW = "planned", "own", "lent", "drew"


def _now() -> str:
    return utcnow().isoformat()


def _result(job: Job) -> dict[str, Any]:
    return dict(job.result) if isinstance(job.result, dict) else {}


def _cards(res: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(c) for c in res.get(CARDS) or [] if isinstance(c, dict)]


def planned(job: Job) -> None:
    """At submit: the card the job is queued on, until it starts."""
    res = _result(job)
    res[CARDS] = [{"card": job.instance, "role": PLANNED}]
    job.result = res


def start_own(job: Job) -> None:
    """The job starts on its card: the planned entry is replaced by the card it really runs on."""
    res = _result(job)
    cards = [c for c in _cards(res) if c.get("role") != PLANNED]
    cards.append({"card": job.instance, "role": OWN, "kind": job.kind, "step": "", "started_at": _now(), "ended_at": None})
    res[CARDS] = cards
    job.result = res


def end_own(job: Job) -> None:
    """The job ends (done, failed, stopped or requeued): its own card's entry gets an end time."""
    res = _result(job)
    cards = _cards(res)
    for c in reversed(cards):
        if c.get("role") == OWN and not c.get("ended_at"):
            c["ended_at"] = _now()
            break
    res[CARDS] = cards
    job.result = res


def add_move(job: Job, source: str, target: str, why: str = "") -> None:
    """The job moves from card `source` to `target` while queued: its planned entry follows it, and the move is kept."""
    res = _result(job)
    cards = _cards(res)
    for c in cards:
        if c.get("role") == PLANNED:
            c["card"] = target
    res[CARDS] = cards
    moved = [dict(m) for m in res.get(MOVED) or [] if isinstance(m, dict)]
    moved.append({"from": source, "to": target, "at": _now(), "why": why})
    res[MOVED] = moved
    job.result = res


def add_use(job: Job, *, card: str, role: str, kind: str, step: str, started_at: str, seconds: float) -> None:
    """A card the job drew on as a lease (role LENT or DREW), once it is handed back."""
    res = _result(job)
    cards = _cards(res)
    cards.append({"card": card, "role": role, "kind": kind, "step": step, "started_at": started_at, "ended_at": _now(),
                  "seconds": seconds})
    res[CARDS] = cards
    if role == LENT:
        res[LENT_KEY] = list(res.get(LENT_KEY) or []) + [{"card": card, "seconds": seconds}]
    job.result = res
