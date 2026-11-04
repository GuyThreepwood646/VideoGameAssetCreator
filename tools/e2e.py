"""End-to-end driver: push one asset through every stage over the real API and report.

Usage: python tools/e2e.py [--base http://127.0.0.1:8765] [--category humanoid] [--platform mobile_low]
       [--prompt "..."] [--stages concept,turnaround,mesh,prep,rig,export] [--count 2] [--name knight]
Waits for each job, selects the first finished version of each stage, prints files and metrics, and exits non-zero
on the first failed stage. Writes tools/e2e_report.json.
"""
import argparse
import json
import sys
import time
import urllib.request

PROMPTS = {
    "humanoid": "A stylized fantasy knight character for a video game, a sturdy young woman with short dark hair, worn steel plate armour with brass trim over a deep blue padded tunic, leather gloves, heavy boots.",
    "quadruped": "A stylized fantasy wolf creature for a video game, lean grey wolf with a thick cream chest ruff, dark stripes along the back, bushy tail, pointed ears, amber eyes.",
    "prop_rigid": "A stylized wooden treasure chest prop for a video game, curved lid, dark oak planks, chunky iron bands and rivets, brass padlock.",
    "material": "Worn medieval cobblestone street, grey and brown stones with moss in the gaps, stylized painted game texture.",
}


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def wait_job(base, job_id, timeout=3600):
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        j = call(base, "GET", f"/api/jobs/{job_id}")
        msg = f"{j['status']} {j.get('progress', 0):.2f} {j.get('message', '')}"
        if msg != last:
            print("   ", msg, flush=True)
            last = msg
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(3)
    raise SystemExit("timeout waiting for job " + job_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--category", default="humanoid")
    ap.add_argument("--platform", default="mobile_low")
    ap.add_argument("--style", default="painted")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--stages", default="concept,turnaround,mesh,prep,rig,export")
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--name", default=None)
    ap.add_argument("--project", default=None, help="existing project id; otherwise one is created")
    ap.add_argument("--asset", default=None, help="existing asset id; skips creation and runs only --stages")
    a = ap.parse_args()
    base = a.base
    report = {"stages": {}}
    pid = a.project
    if a.asset:
        asset = call(base, "GET", f"/api/assets/{a.asset}")
        pid = asset["project_id"]
    elif not pid:
        p = call(base, "POST", "/api/projects", {"name": "e2e " + time.strftime("%H%M%S"), "platform_target": a.platform})
        pid = p["id"]
    asset = asset if a.asset else call(base, "POST", f"/api/projects/{pid}/assets", {
        "name": a.name or f"e2e-{a.category}", "category": a.category, "style_preset": a.style,
        "platform_target": a.platform, "prompt": a.prompt or PROMPTS.get(a.category, PROMPTS["prop_rigid"])})
    aid = asset["id"]
    print("asset", aid, a.category, a.platform)
    report["asset_id"] = aid
    for stage in a.stages.split(","):
        stage = stage.strip()
        t0 = time.time()
        count = a.count if stage == "concept" else 1
        print(f"== {stage} (count {count})", flush=True)
        res = call(base, "POST", f"/api/assets/{aid}/stages/{stage}/run", {"count": count})
        items = res if isinstance(res, list) else [res]
        results = []
        for it in items:
            j = wait_job(base, it["job"]["id"])
            v = call(base, "GET", f"/api/versions/{it['version']['id']}")
            results.append((j, v))
        ok = [v for j, v in results if j["status"] == "done" and v["status"] == "done"]
        secs = round(time.time() - t0, 1)
        if not ok:
            errs = [j.get("error") or v.get("error") for j, v in results]
            print(f"   FAILED after {secs}s: {errs}")
            report["stages"][stage] = {"ok": False, "seconds": secs, "errors": errs}
            json.dump(report, open("tools/e2e_report.json", "w"), indent=1)
            sys.exit(1)
        v = ok[0]
        call(base, "POST", f"/api/versions/{v['id']}/select")
        print(f"   ok in {secs}s; files: {sorted(v['files'].keys())}")
        m = v.get("metrics") or {}
        brief = {k: m[k] for k in m if k in ("triangles", "bones", "silhouette_iou", "views_passed", "views_total", "rigid", "clips", "checks_passed", "checks_failed")}
        if "checks" in m:
            brief["checks"] = f"{sum(1 for c in m['checks'] if c.get('passed'))}/{len(m['checks'])} passed"
            brief["failed"] = [c["name"] for c in m["checks"] if not c.get("passed")]
        print("   metrics:", json.dumps(brief)[:600])
        report["stages"][stage] = {"ok": True, "seconds": secs, "version": v["id"], "files": v["files"], "metrics_brief": brief}
        json.dump(report, open("tools/e2e_report.json", "w"), indent=1)
    print("ALL STAGES OK")


if __name__ == "__main__":
    main()
