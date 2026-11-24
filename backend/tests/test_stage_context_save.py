"""A stage's saves keep what another job added to the same version row meanwhile. The 3D job's StageContext holds a
copy of the version's file list and metrics; a second session (another job on the same version) adds a key; the first
context's later register and set_metrics must not write its older whole copy back."""

from __future__ import annotations

from vgac import jobs
from vgac.db import Asset, Project, Version, session_factory
from vgac.stages import base as stage_base


def test_a_key_another_session_adds_survives_the_contexts_register_and_set_metrics(settings):
    with session_factory()() as db:
        p = Project(name="P")
        db.add(p)
        db.commit()
        a = Asset(project_id=p.id, name="crate", category="prop_rigid", style_preset="painted",
                  platform_target="mobile_low", prompt="x")
        db.add(a)
        db.commit()
        v = Version(asset_id=a.id, stage="mesh", status="running")
        db.add(v)
        db.commit()
        vid = v.id
    ctx = jobs.JobContext(job_id="j", instance="a", payload={}, settings=settings, clients={}, _progress_cb=lambda f, m: None)
    with session_factory()() as first_db, session_factory()() as second_db:
        first = stage_base.StageContext(ctx, first_db, first_db.get(Version, vid))
        folder = first.dir
        first.register("input", folder / "input_image.png")  # the first context now holds its copy
        second = stage_base.StageContext(ctx, second_db, second_db.get(Version, vid))
        second.register("extra_file", folder / "extra.json")
        second.set_metrics(extra_metric={"n": 6})
        first.register("glb", folder / "model.glb")  # the first context saves with its older copy
        first.set_metrics(atlas_used=0.4)
    with session_factory()() as db:
        row = db.get(Version, vid)
        assert {"input", "glb", "extra_file"} <= set(row.files)
        assert row.metrics["extra_metric"] == {"n": 6} and row.metrics["atlas_used"] == 0.4
