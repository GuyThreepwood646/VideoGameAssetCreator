"""Live check of a character made with carried items: the character is painted empty-handed, each item is made as
its own asset, attached, and the character's files are rebuilt with them on.

Usage: python tools/e2e_items.py [--base http://127.0.0.1:8765]
Writes tools/e2e_items_report.json.
"""
import argparse
import json
import time
import urllib.request

PROMPT = ("A stylized fantasy knight for a video game, a sturdy young woman with short red hair, steel plate armour over a blue "
          "tabard, carrying a short sword and a round wooden shield, about 1.7 m tall.")


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()
    base = a.base
    t0 = time.time()
    report = {"steps": []}

    def note(name, **kw):
        row = {"t": round(time.time() - t0), "step": name, **kw}
        report["steps"].append(row)
        print(json.dumps(row)[:3000], flush=True)
        json.dump(report, open("tools/e2e_items_report.json", "w"), indent=1)

    found = call(base, "POST", "/api/items/detect", {"prompt": PROMPT, "category": "humanoid"})
    note("words found", items=[(i["label"], i["place"]) for i in found.get("items", [])], worn=found.get("worn"), sentence=found.get("sentence"))
    project = call(base, "POST", "/api/projects", {"name": "Items check " + time.strftime("%m-%d %H%M"), "platform_target": "pc_mid"})
    asset = call(base, "POST", f"/api/projects/{project['id']}/assets", {"name": "Knight", "category": "humanoid", "style_preset": "painted",
                                                                          "platform_target": "pc_mid", "prompt": PROMPT, "items": "separate"})
    aid = asset["id"]
    report["asset"] = aid
    eq = asset.get("equipment") or {}
    note("character created", set_aside=[(s["label"], s.get("item_asset_id")) for s in eq.get("set_aside", [])], choice=eq.get("choice"))
    call(base, "POST", f"/api/assets/{aid}/autopilot", {"stop_on": "never"})

    last = ""
    while time.time() - t0 < a.timeout:
        full = call(base, "GET", f"/api/assets/{aid}")
        st = full["autopilot"]
        build = (full.get("equipment") or {}).get("build")
        line = f"{st['status']} {st.get('stage')} build={json.dumps(build)[:160]}"
        if line != last:
            print(f"   [{int(time.time() - t0)} s] {line}", flush=True)
            last = line
        state = build.get("state") if isinstance(build, dict) else build
        if st["status"] in ("paused", "error"):
            break
        if st["status"] == "done" and state in ("done", "error", "stopped", None):
            break
        time.sleep(6)

    full = call(base, "GET", f"/api/assets/{aid}")
    note("character finished", autopilot=full["autopilot"]["status"], reason=full["autopilot"].get("reason"), build=(full.get("equipment") or {}).get("build"),
         counts={s: [v["status"] for v in vs] for s, vs in full["stages"].items()})
    concept = next((v for v in full["stages"]["concept"] if v["selected"]), None)
    if concept:
        note("concept", items_set_aside=concept["metrics"].get("items_set_aside"), prompt=(concept["metrics"].get("prompt_final") or "")[:400],
             failed=[(c.get("name"), (c.get("help") or "")[:100]) for c in (concept["metrics"].get("checks") or []) if not c.get("passed")])
    rig = next((v for v in full["stages"]["rig"] if v["selected"]), None)
    if rig:
        m = rig["metrics"]
        note("rig", version=rig["id"], clips=len(m.get("clips") or []), clips_source=m.get("clips_source"), rig_restore=(m.get("rig_restore") or {}).get("ok"),
             failed=[(c.get("name"), (c.get("help") or "")[:120]) for c in (m.get("checks") or []) if not c.get("passed")])
    exp = next((v for v in full["stages"]["export"] if v["selected"]), None)
    if exp:
        m = exp["metrics"]
        ur = m.get("unity_report") or {}
        note("export", version=exp["id"], checks=f"{m.get('checks_passed')}/{len(m.get('checks') or [])}", sockets=(m.get("sockets") or {}).get("count"),
             equipped=m.get("equipped"), beauty=bool(exp["files"].get("beauty")), files=sorted(exp["files"]),
             unity={k: ur.get(k) for k in ("ok", "humanoidValid", "clipCount", "importer_version", "equippedPrefab", "equippedItems", "showcase", "hitboxMaxBoneError", "socketMaxError")},
             failed=[(c.get("name"), (c.get("help") or "")[:140]) for c in (m.get("checks") or []) if not c.get("passed")])
    panel = call(base, "GET", f"/api/assets/{aid}/items")
    note("items", items={k: {"name": v.get("name"), "status": v.get("status"), "grip": v.get("grip")} for k, v in (panel.get("items") or {}).items()},
         needs_update=panel.get("needs_update"), saved=panel.get("saved"))
    print("ITEMS CHECK FINISHED")


if __name__ == "__main__":
    main()
