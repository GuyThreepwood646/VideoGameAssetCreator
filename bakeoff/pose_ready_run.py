"""Run side views, 3D build and clean-up on an existing asset's selected concept picture, with pose_ready on or off.

    python bakeoff/pose_ready_run.py ASSET_ID on|off [concept_version_id]

New versions only (nothing is selected, replaced or deleted). Prints the three version ids as JSON. The backend must be
running on 127.0.0.1:8765.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def call(method: str, path: str, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def wait(job_id: str) -> dict:
    while True:
        jobs = call("GET", "/api/jobs")
        j = next((x for x in jobs if x["id"] == job_id), None)
        if j and j["status"] not in ("queued", "running"):
            return j
        time.sleep(15)


def stage(aid: str, name: str, parent: str, params: dict) -> str:
    r = call("POST", f"/api/assets/{aid}/stages/{name}/run", {"parent_version_id": parent, "params": params})
    job = r["job"] if "job" in r else r[0]["job"]
    vid = r["version"]["id"] if "version" in r else r[0]["version"]["id"]
    j = wait(job["id"])
    print(name, vid, j["status"], j.get("error"), flush=True)
    if j["status"] != "done":
        raise SystemExit(f"{name} {j['status']}: {j.get('error')}")
    return vid


def main(aid: str, mode: str, concept: str | None = None):
    a = call("GET", f"/api/assets/{aid}")
    if concept is None:
        concept = (a.get("selected") or {}).get("concept") or next(v["id"] for v in a["stages"]["concept"] if v["status"] == "done")
    params = {"pose_ready": True} if mode == "on" else {}
    t = stage(aid, "turnaround", concept, params)
    m = stage(aid, "mesh", t, {})
    p = stage(aid, "prep", m, {})
    print(json.dumps({"asset": aid, "mode": mode, "concept": concept, "turnaround": t, "mesh": m, "prep": p}))


if __name__ == "__main__":
    main(*sys.argv[1:])
