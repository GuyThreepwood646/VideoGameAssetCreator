"""Live check: run assets through autopilot on the real backend and print every stage's checks.

Usage: python tools/e2e_autopilot.py [--base http://127.0.0.1:8765] [--only humanoid,prop_rigid] [--stop-on block]
Creates one project and one asset per category, starts autopilot, waits, and writes tools/e2e_autopilot_report.json.
"""
import argparse
import json
import time
import urllib.request

ASSETS = {
    "humanoid": ("Ranger", "pc_mid", "A stylized fantasy ranger for a video game, a lean young man with a short brown beard, green hooded cloak over worn leather armour, a quiver on his back, tall boots, about 1.8 m tall."),
    "prop_rigid": ("Market cart", "pc_mid", "A stylized wooden market cart prop for a video game, two large spoked wheels, an open flat bed with low side rails, two long handles at the front, weathered oak planks and iron fittings."),
    "quadruped": ("Fox", "mobile_low", "A stylized red fox for a video game, slim body, bushy tail with a white tip, pointed ears, black legs, amber eyes."),
    "environment": ("Stone wall", "pc_mid", "A stylized medieval stone wall section for a video game, 3 m tall and 3 m wide, large grey blocks with moss in the gaps, flat ends so copies join."),
}


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def brief(v):
    m = v.get("metrics") or {}
    checks = m.get("checks") or []
    failed = [(c.get("name"), c.get("severity"), (c.get("help") or "")[:110]) for c in checks if not c.get("passed")]
    keys = ("triangles", "bones", "silhouette_iou", "size_source", "size_extent_m", "face_yaw_applied", "rebaked", "atlas_used",
            "light_score", "delight_applied", "motion", "clips", "missing_clips", "collider_count", "collider_shape", "delivered_to")
    out = {k: m[k] for k in keys if k in m}
    for k in ("rig_restore", "hitboxes"):
        if isinstance(m.get(k), dict):
            out[k] = {kk: vv for kk, vv in m[k].items() if kk in ("ok", "scale_factor", "size_m", "bone_names", "count", "mode", "fallback", "cover", "overreach")}
    ur = m.get("unity_report") or {}
    if ur:
        out["unity"] = {k: ur.get(k) for k in ("ok", "humanoidValid", "avatar_mapping", "avatar_mapping_by", "clipCount", "texturesAssigned", "hitboxCount", "movementShape",
                                               "colliderCount", "colliderKinds", "secondaryUV", "importer_version") if k in ur}
        ctrl = ur.get("controller")
        if isinstance(ctrl, dict):
            out["unity"]["controller"] = {"states": ctrl.get("states"), "parameters": ctrl.get("parameters")}
    return {"status": v["status"], "checks": f"{sum(1 for c in checks if c.get('passed'))}/{len(checks)}", "failed": failed,
            "files": sorted(v.get("files") or {}), "metrics": out, "error": v.get("error")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--only", default="humanoid,prop_rigid")
    ap.add_argument("--stop-on", default="block")
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args()
    base = a.base
    project = call(base, "POST", "/api/projects", {"name": "Live check " + time.strftime("%m-%d %H%M"), "platform_target": "pc_mid"})
    report = {"project": project["id"], "assets": {}}
    for cat in [c.strip() for c in a.only.split(",") if c.strip()]:
        name, platform, prompt = ASSETS[cat]
        asset = call(base, "POST", f"/api/projects/{project['id']}/assets", {"name": name, "category": cat, "style_preset": "painted",
                                                                             "platform_target": platform, "prompt": prompt})
        aid = asset["id"]
        t0 = time.time()
        print(f"== {cat}: {name} ({aid})", flush=True)
        call(base, "POST", f"/api/assets/{aid}/autopilot", {"stop_on": a.stop_on})
        last = ""
        while time.time() - t0 < a.timeout:
            st = call(base, "GET", f"/api/assets/{aid}/autopilot")
            line = f"{st['status']} {st.get('stage')} {st.get('reason') or ''}"[:160]
            if line != last:
                print(f"   [{int(time.time() - t0)} s] {line}", flush=True)
                last = line
            if st["status"] in ("done", "paused", "error"):
                break
            time.sleep(5)
        full = call(base, "GET", f"/api/assets/{aid}")
        entry = {"seconds": round(time.time() - t0), "autopilot": st["status"], "reason": st.get("reason"), "stages": {}}
        for stage, versions in full["stages"].items():
            kept = [v for v in versions if v["selected"]] or versions[-1:]
            if kept:
                entry["stages"][stage] = brief(kept[0])
        report["assets"][cat] = entry
        print(json.dumps(entry, indent=1)[:6000], flush=True)
        json.dump(report, open("tools/e2e_autopilot_report.json", "w"), indent=1)
    print("LIVE CHECK FINISHED")


if __name__ == "__main__":
    main()
