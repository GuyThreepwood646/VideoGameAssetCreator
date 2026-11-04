from vgac import library
from vgac.db import Asset, Job, Project, Version, asset_to_dict, job_to_dict, project_to_dict, session_factory, version_to_dict


def test_round_trip_project_asset_version_job(settings):
    Session = session_factory()
    with Session() as db:
        p = Project(name="p1", platform_target="pc_mid")
        p.style_lock = {"prompt_block": "painted", "model": "zturbo", "loras": [], "palette": ["#fff"], "negative": ""}
        db.add(p)
        db.commit()
        a = Asset(project_id=p.id, name="knight", category="humanoid", style_preset="painted", platform_target="pc_mid", prompt="a knight")
        a.parts = ["shield"]
        db.add(a)
        db.commit()
        v = Version(asset_id=a.id, stage="concept", status="queued")
        v.params = {"seed": 7}
        db.add(v)
        db.commit()
        v2 = Version(asset_id=a.id, stage="concept", parent_version_id=v.id, status="done", selected=True)
        v2.files = {"image": "projects/x/image.png"}
        v2.metrics = {"seed": 8, "checks": []}
        db.add(v2)
        db.flush()
        j = Job(instance="b", kind="concept.run", asset_id=a.id, version_id=v2.id)
        j.payload = {"version_id": v2.id}
        db.add(j)
        db.commit()
        ids = (p.id, a.id, v.id, v2.id, j.id)

    with Session() as db:
        p = db.get(Project, ids[0])
        assert p.style_lock["palette"] == ["#fff"]
        d = project_to_dict(p)
        assert d["created_at"].endswith("Z") and len(d["id"]) == 36
        a = db.get(Asset, ids[1])
        ad = asset_to_dict(a)
        assert ad["parts"] == ["shield"]
        assert [x["id"] for x in ad["stages"]["concept"]] == [ids[2], ids[3]]
        assert ad["selected"] == {"concept": ids[3]}
        assert set(ad["stages"]) == {"concept", "turnaround", "mesh", "prep", "rig", "export"}
        v2 = db.get(Version, ids[3])
        vd = version_to_dict(v2)
        assert vd["parent_version_id"] == ids[2] and vd["files"]["image"].endswith("image.png") and vd["selected"] is True
        j = db.get(Job, ids[4])
        jd = job_to_dict(j)
        assert jd["status"] == "queued" and jd["progress"] == 0.0 and jd["version_id"] == ids[3]
        assert set(jd) == {"id", "instance", "kind", "asset_id", "version_id", "status", "progress", "message", "created_at", "started_at", "finished_at", "error"}


def test_cascade_delete_and_library_paths(settings):
    Session = session_factory()
    with Session() as db:
        p = Project(name="p")
        db.add(p)
        db.commit()
        a = Asset(project_id=p.id, name="n", category="prop_rigid", style_preset="painted")
        db.add(a)
        db.commit()
        v = Version(asset_id=a.id, stage="mesh")
        db.add(v)
        db.commit()
        d = library.version_dir(a, v, settings)
        assert d.is_dir()
        assert d.relative_to(settings.library_path).as_posix() == f"projects/{p.id}/assets/{a.id}/mesh/{v.id}"
        f = d / "model.glb"
        f.write_bytes(b"glb")
        rel = library.register_file(v, "glb", f, settings)
        assert rel == f"projects/{p.id}/assets/{a.id}/mesh/{v.id}/model.glb"
        assert library.version_file(v, "glb", settings) == f.resolve()
        db.commit()
        db.delete(p)
        db.commit()
        assert db.get(Asset, a.id) is None and db.get(Version, v.id) is None
