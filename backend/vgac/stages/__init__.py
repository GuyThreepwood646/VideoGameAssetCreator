"""Stage registry and dispatcher.

Each stage module exposes async run(sc, params), refine(sc, instruction, view, mask_png, params) and
adjust(sc, params), and optionally judge(sc). execute() builds the StageContext for a job and calls the right
one. The tileable material category uses the material module for concept, mesh and export, and the prep and rig
modules record a skip.
"""

from __future__ import annotations

import base64
import binascii
import importlib
from pathlib import Path
from typing import Any

from ..db import Version, session_factory
from ..jobs import JobContext, parse_kind
from ..reference import STAGE_ORDER
from .base import StageContext

STAGE_MODULES = {s: f"vgac.stages.{s}" for s in STAGE_ORDER}
MATERIAL_STAGES = ("concept", "mesh", "export")


def module_for(stage: str, is_material: bool = False):
    if stage not in STAGE_MODULES:
        raise ValueError(f"unknown stage {stage!r}")
    if is_material and stage in MATERIAL_STAGES:
        return importlib.import_module("vgac.stages.material")
    return importlib.import_module(STAGE_MODULES[stage])


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_MASK_B64 = 6_000_000  # a 2048 px RGBA PNG mask is well under this


def decode_mask(mask_b64: str) -> bytes:
    """Strict base64 decode of a PNG mask (optionally a data: URL). Raises ValueError for anything else."""
    if len(mask_b64) > MAX_MASK_B64:
        raise ValueError("mask_png is too large")
    data = mask_b64.split(",", 1)[1] if mask_b64.startswith("data:") else mask_b64
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError("mask_png is not valid base64") from e
    if not raw.startswith(PNG_SIGNATURE):
        raise ValueError("mask_png is not a PNG image")
    return raw


def _write_mask(sc: StageContext, mask_b64: str | None) -> Path | None:
    if not mask_b64:
        return None
    p = sc.dir / "mask.png"
    p.write_bytes(decode_mask(mask_b64))
    sc.register("mask", p)
    return p


async def execute(ctx: JobContext, kind: str, payload: dict[str, Any]) -> None:
    stage, op, is_material = parse_kind(kind)
    mod = module_for(stage, is_material)
    with session_factory()() as db:
        version = db.get(Version, payload.get("version_id"))
        if version is None:
            raise ValueError(f"job {ctx.job_id}: version {payload.get('version_id')!r} not found")
        sc = StageContext(ctx, db, version)
        params = dict(payload.get("params") or {})
        if op == "run":
            await mod.run(sc, params)
        elif op == "refine":
            mask = _write_mask(sc, payload.get("mask_png"))
            await mod.refine(sc, payload.get("instruction") or "", payload.get("view"), mask, params)
        elif op == "adjust":
            await mod.adjust(sc, params)
        elif op == "judge":
            if (payload.get("mode") or "metrics") == "local":
                from . import judge as judge_mod

                await judge_mod.local(sc)
            else:
                judge = getattr(mod, "judge", None)
                if judge is not None:
                    await judge(sc)
        else:
            raise ValueError(f"unknown op {op!r}")
        sc.save()


async def judge_metrics(version_id: str, settings, clients: dict[str, Any] | None = None) -> None:
    """Recompute the measurable metrics for a version from its files (POST /versions/{id}/judge mode=metrics)."""
    from ..jobs import JobContext

    with session_factory()() as db:
        version = db.get(Version, version_id)
        if version is None:
            raise ValueError("version not found")
        from ..db import Asset

        asset = db.get(Asset, version.asset_id)
        mod = module_for(version.stage, asset.category == "material")
        ctx = JobContext(job_id="judge", instance="host", payload={}, settings=settings, clients=clients or {}, _progress_cb=lambda f, m: None)
        sc = StageContext(ctx, db, version)
        judge = getattr(mod, "judge", None)
        if judge is not None:
            await judge(sc)
        sc.save()
