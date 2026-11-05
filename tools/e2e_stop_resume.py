"""Live check of Stop, Resume and "resume from an earlier step" on the real backend and engines.

Usage: python tools/e2e_stop_resume.py [--base http://127.0.0.1:8765]
One humanoid through the autopilot: stop while the 3D model is being built, resume (the same version must come
back), stop again during clean-up, go back to the side views (they and the 3D model must be made again), then let
it finish and print the rig, export and Unity results. Writes tools/e2e_stop_resume_report.json.
"""
import argparse
import json
import time
import urllib.request

PROMPT = ("A stylized fantasy ranger for a video game, a lean young man with a short brown beard, green hooded cloak over "
          "worn leather armour, tall boots, about 1.8 m tall.")


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    a = ap.parse_args()
    base = a.base
    t0 = time.time()
    report = {"steps": []}

    def note(name, **kw):
        row = {"t": round(time.time() - t0), "step": name, **kw}
        report["steps"].append(row)
        print(json.dumps(row), flush=True)
        json.dump(report, open("tools/e2e_stop_resume_report.json", "w"), indent=1)

    def asset():
        return call(base, "GET", f"/api/assets/{aid}")

    def versions(stage):
        return asset()["stages"][stage]

    def wait(cond, what, timeout=1500):
        t = time.time()
        while time.time() - t < timeout:
            out = cond()
            if out:
                return out
            time.sleep(3)
        raise SystemExit(f"TIMEOUT waiting for {what}")

    def ap_state():
        return call(base, "GET", f"/api/assets/{aid}/autopilot")

    project = call(base, "POST", "/api/projects", {"name": "Stop and Resume check " + time.strftime("%m-%d %H%M"), "platform_target": "pc_mid"})
    aid = call(base, "POST", f"/api/projects/{project['id']}/assets", {"name": "Ranger", "category": "humanoid", "style_preset": "painted",
                                                                        "platform_target": "pc_mid", "prompt": PROMPT})["id"]
    report["asset"] = aid
    call(base, "POST", f"/api/assets/{aid}/autopilot", {"stop_on": "never"})
    note("autopilot started")

    # 1. Stop while the 3D model is being built (wait until its job really runs and has made some progress).
    def mesh_running():
        vs = [v for v in versions("mesh") if v["status"] == "running"]
        if not vs:
            return None
        j = call(base, "GET", f"/api/jobs/{vs[0]['job_id']}")
        return (vs[0], j) if j["status"] == "running" and j["progress"] >= 0.1 else None

    v, j = wait(mesh_running, "the mesh step to be running")
    mesh_id, mesh_job, mesh_params = v["id"], j["id"], v["params"]
    note("mesh running", version=mesh_id, progress=j["progress"], instance=j["instance"])
    ts = time.time()
    out = call(base, "POST", f"/api/assets/{aid}/stop", {})
    note("stop pressed", stopped=out["stopped"], autopilot=out["autopilot"]["status"], stopped_by_user=out["autopilot"].get("stopped_by_user"))
    wait(lambda: next((x for x in versions("mesh") if x["id"] == mesh_id and x.get("stopped")), None), "the mesh version to read stopped", 180)
    q = call(base, "GET", "/api/health")["queue"]
    note("mesh stopped", seconds_to_stop=round(time.time() - ts, 1), job=call(base, "GET", f"/api/jobs/{mesh_job}")["status"],
         queue={k: (v["current_job_id"], v["queued"]) for k, v in q.items()}, autopilot=ap_state()["status"],
         kept={s: bool(asset()["selected"].get(s)) for s in ("concept", "turnaround")})
    time.sleep(8)  # nothing may restart on its own
    assert ap_state()["status"] == "paused" and not [x for x in versions("mesh") if x["status"] in ("queued", "running")], "something restarted"

    # 2. Resume: the same version and job come back.
    out = call(base, "POST", f"/api/assets/{aid}/resume", {})
    note("resume pressed", autopilot=out["autopilot"]["status"], resumed=[x["id"] for x in out["resumed"]])
    wait(lambda: next((x for x in versions("mesh") if x["id"] == mesh_id and x["status"] == "done"), None), "the resumed mesh to finish")
    ms = versions("mesh")
    note("mesh finished after resume", versions=len(ms), same_version=ms[-1]["id"] == mesh_id, same_settings=ms[-1]["params"] == mesh_params,
         same_job=ms[-1]["job_id"] == mesh_job, checks=f"{ms[-1]['metrics'].get('checks_passed')}/{len(ms[-1]['metrics'].get('checks') or [])}")

    # 3. Stop again during clean-up, then go back to the side views.
    wait(lambda: [x for x in versions("prep") if x["status"] in ("queued", "running")] or ap_state()["stage"] in ("rig", "export"), "prep to start")
    old_turn, old_mesh = asset()["selected"].get("turnaround"), asset()["selected"].get("mesh")
    call(base, "POST", f"/api/assets/{aid}/stop", {})
    wait(lambda: not [x for x in sum((versions(s) for s in ("prep", "rig", "export")), []) if x["status"] in ("queued", "running")], "the second stop", 180)
    note("stopped again", autopilot=ap_state()["status"], stage=ap_state()["stage"], prep=[(x["status"], x.get("stopped")) for x in versions("prep")])
    st = call(base, "POST", f"/api/assets/{aid}/autopilot", {"stop_on": "never", "from_stage": "turnaround"})
    note("resume from the side views", autopilot=st["status"])
    st = wait(lambda: (s := ap_state())["status"] in ("done", "paused", "error") and s, "the autopilot to finish", 2400)
    full = asset()
    by_id = {x["id"]: x for s in full["stages"].values() for x in s}
    note("finished", autopilot=st["status"], reason=st.get("reason"), seconds=round(time.time() - t0),
         concept_kept=bool(full["selected"].get("concept")),
         turnaround_remade=full["selected"].get("turnaround") != old_turn, mesh_remade=full["selected"].get("mesh") != old_mesh,
         old_turnaround=by_id[old_turn]["status"] if old_turn else None, old_mesh=by_id[old_mesh]["status"] if old_mesh else None,
         counts={s: [x["status"] for x in vs] for s, vs in full["stages"].items()})
    rig = next((x for x in full["stages"]["rig"] if x["selected"]), None)
    if rig:
        m = rig["metrics"]
        note("rig", clips=m.get("clips"), clips_source=m.get("clips_source"), library=len(m.get("library_clips") or []), procedural=m.get("procedural_clips"),
             retarget_seconds=m.get("retarget_seconds"), retarget_error=m.get("retarget_error"),
             failed=[(c.get("name"), (c.get("help") or "")[:120]) for c in (m.get("checks") or []) if not c.get("passed")])
    exp = next((x for x in full["stages"]["export"] if x["selected"]), None)
    if exp:
        m = exp["metrics"]
        ur = m.get("unity_report") or {}
        note("export", checks=f"{m.get('checks_passed')}/{len(m.get('checks') or [])}", sockets=m.get("sockets"), files=sorted(exp["files"]),
             unity={k: ur.get(k) for k in ("ok", "humanoidValid", "clipCount", "importer_version", "sockets", "socketMaxError", "hitboxCount")},
             failed=[(c.get("name"), (c.get("help") or "")[:120]) for c in (m.get("checks") or []) if not c.get("passed")])
    print("STOP AND RESUME CHECK FINISHED")


if __name__ == "__main__":
    main()
