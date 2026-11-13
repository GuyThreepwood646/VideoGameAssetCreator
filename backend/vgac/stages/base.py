"""StageContext: everything a stage operation needs, plus shared helpers (ComfyUI runs, Blender, lineage files)."""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import library, reference
from ..comfy import ComfyClient, ComfyError
from ..db import Asset, Project, Version
from ..graphs.birefnet import cutout_graph
from ..imaging import alpha_bbox, white_composite
from ..jobs import JobContext
from ..tools import run_blender, run_unity


def _lineage_entry(v: Version) -> dict[str, Any]:
    return {"stage": v.stage, "version_id": v.id, "params": v.params, "files": v.files}


def manifest_provenance(sc: "StageContext") -> dict[str, Any]:
    """What an export manifest records about where the asset came from, in one lineage walk: models (every model used
    along the lineage, then by this run, by id), seeds (per stage, plus the turnaround's per-view seeds) and lineage
    (concept first)."""
    models: dict[str, dict[str, Any]] = {}
    seeds: dict[str, Any] = {}
    chain = list(reversed(list(sc.iter_lineage())))
    for v in chain:
        if any(isinstance(m, dict) and m.get("role") == "moves" for m in v.metrics.get("models") or []):
            # A rig version lists every source of its moves ("Add a move"): a newer one replaces an older list.
            models = {k: m for k, m in models.items() if m.get("role") != "moves"}
        for m in v.metrics.get("models") or []:
            models[m.get("id", m.get("name"))] = m
        if v.params.get("seed") is not None:
            seeds[v.stage] = v.params.get("seed")
        if v.stage == "turnaround":
            seeds["turnaround_views"] = {k: info.get("seed") for k, info in (v.metrics.get("views") or {}).items()}
    for m in sc.models_used.values():
        models[m.get("id", m.get("name"))] = m
    return {"models": models, "seeds": seeds, "lineage": [_lineage_entry(v) for v in chain]}


def random_seed() -> int:
    return random.randint(1, 2**31 - 1)


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return s or "asset"


class StageContext:
    def __init__(self, ctx: JobContext, db: Session, version: Version) -> None:
        self.ctx = ctx
        self.db = db
        self.version = version
        self.asset: Asset = db.get(Asset, version.asset_id)
        self.project: Project = db.get(Project, self.asset.project_id)
        self.settings = ctx.settings
        self.category = reference.category(self.asset.category)
        self.style = reference.style(self.asset.style_preset) if self.asset.style_preset in {s["id"] for s in reference.styles()} else {"id": self.asset.style_preset, "model": "zturbo", "prompt_block": ""}
        self.platform = self.asset.platform_target or self.project.platform_target
        self.budget = reference.budget(self.asset.category, self.platform)
        self.dir: Path = library.version_dir(self.asset, version, self.settings)
        self.models_used: dict[str, dict[str, Any]] = {}

    # --- progress and persistence ------------------------------------------------

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        self.ctx.progress(fraction, message)
        self.ctx.check_cancelled()

    def save(self) -> None:
        self.db.add(self.version)
        self.db.commit()

    def register(self, key: str, path: Path | str) -> str:
        rel = library.register_file(self.version, key, path, self.settings)
        self.save()
        return rel

    def publish(self) -> None:
        """Tell the open pages this version changed (its files, so a picture made mid-step shows at once)."""
        from ..db import version_to_dict
        from ..events import bus

        bus.publish_threadsafe({"type": "version", "version": version_to_dict(self.version)})

    def set_metrics(self, **values: Any) -> None:
        library.update_metrics(self.version, **values)
        self.save()

    def set_params(self, **values: Any) -> None:
        p = self.version.params
        p.update(values)
        self.version.params = p
        self.save()

    def use_model(self, model_id: str) -> None:
        self.models_used[model_id] = reference.model_record(model_id)
        self.set_metrics(models=list(self.models_used.values()))

    @property
    def params(self) -> dict[str, Any]:
        return self.version.params

    @property
    def parent(self) -> Version | None:
        if not self.version.parent_version_id:
            return None
        return self.db.get(Version, self.version.parent_version_id)

    def selected_version(self, stage: str) -> Version | None:
        return self.db.scalar(select(Version).where(Version.asset_id == self.asset.id, Version.stage == stage, Version.selected == True))  # noqa: E712

    def iter_lineage(self, include_self: bool = True) -> Iterator[Version]:
        """This version (unless include_self is False), then its parent, grandparent and so on up to the concept.
        The one lineage walk (CODE-AUDIT D9); a parent loop in the data ends the walk instead of hanging it."""
        v: Version | None = self.version if include_self else self.parent
        seen: set[str] = set()
        while v is not None and v.id not in seen:
            yield v
            seen.add(v.id)
            v = self.db.get(Version, v.parent_version_id) if v.parent_version_id else None

    def stage_version(self, stage: str) -> Version | None:
        """The version of `stage` in this version's lineage, else the selected one for the asset."""
        for v in self.iter_lineage(include_self=False):
            if v.stage == stage:
                return v
        return self.selected_version(stage)

    def lineage_file(self, key: str, stages: tuple[str, ...] | None = None) -> Path | None:
        """Walk up the lineage (then selected versions) for the first version that has file `key`."""
        for v in self.iter_lineage(include_self=False):
            if stages is None or v.stage in stages:
                p = library.version_file(v, key, self.settings)
                if p is not None:
                    return p
        for s in (stages or reference.STAGE_ORDER):
            sv = self.selected_version(s)
            if sv is not None:
                p = library.version_file(sv, key, self.settings)
                if p is not None:
                    return p
        return None

    def file(self, key: str) -> Path | None:
        return library.version_file(self.version, key, self.settings)

    def lineage(self) -> list[dict[str, Any]]:
        """The lineage from the concept down to this version, as the manifest records it."""
        return [_lineage_entry(v) for v in reversed(list(self.iter_lineage()))]

    # --- ComfyUI --------------------------------------------------------------

    def client(self, instance: str | None = None) -> ComfyClient:
        name = instance or self.ctx.instance
        c = self.ctx.clients.get(name)
        if c is None:
            raise RuntimeError(f"no ComfyUI client for instance {name!r}")
        return c

    def prefix(self, tag: str = "") -> str:
        p = f"vgac/{self.asset.id[:8]}/{self.version.stage}/{self.version.id[:8]}"
        return f"{p}/{tag}" if tag else p

    async def comfy(self, graph: dict[str, Any], instance: str | None = None, poll: float = 1.0, message: str = "",
                    base: float = 0.0, span: float = 1.0) -> dict[str, Any]:
        """Queue a graph and wait, reporting elapsed time as the job message."""
        client = self.client(instance)
        self.progress(base, f"{message or 'running'} on instance {client.name}")

        def on_poll(elapsed: float) -> None:
            self.ctx.progress(None, f"{message or 'running'} on instance {client.name}: {int(elapsed)} s")
            self.ctx.check_cancelled()

        try:
            return await client.run(graph, poll_seconds=poll, timeout=self.settings.job_timeout_seconds, on_poll=on_poll)
        finally:
            self.progress(base + span, None)

    async def stage_input(self, path: Path | str, name: str, instance: str | None = None) -> str:
        return await self.client(instance).stage_input(path, name)

    async def download_image(self, entry: dict[str, Any], node: str, dest: Path, instance: str | None = None) -> Path:
        imgs = ComfyClient.images_of(entry, node)
        if not imgs:
            raise ComfyError(f"node {node!r} produced no image; outputs: {json.dumps(entry.get('outputs', {}))[:800]}")
        return await self.client(instance).download_output(imgs[0], dest)

    async def download_mesh(self, entry: dict[str, Any], dest: Path, instance: str | None = None) -> Path:
        items = ComfyClient.files_of(entry, "3d") or ComfyClient.files_of(entry, "result") or ComfyClient.files_of(entry, "files")
        if not items:
            raise ComfyError(f"no 3d output found; outputs: {json.dumps(entry.get('outputs', {}))[:800]}")
        return await self.client(instance).download_output(items[0], dest)

    async def cutout(self, image_path: Path, name: str, instance: str | None = None, base: float = 0.0, span: float = 0.1) -> dict[str, Path]:
        """BiRefNet cutout of a local image: writes <name>.png (RGBA) and <name>_white.png next to it. Runs on the image
        card the job runs on (concept and turnaround jobs run on an image card, which may be the one that took over
        while the usual card is off), else the one Settings.stage_instances gives the concept stage."""
        if instance is None:
            instance = self.ctx.instance if self.ctx.instance in self.ctx.clients else self.settings.stage_instances["concept"]
        src_name = await self.stage_input(image_path, f"vgac_{self.version.id[:8]}_{name}_src.png", instance)
        entry = await self.comfy(cutout_graph(src_name, self.prefix(name)), instance=instance, message=f"cutout {name}", base=base, span=span)
        dest = self.dir / f"{name}.png"
        await self.download_image(entry, "save", dest, instance)
        return await self.finish_cutout(dest)

    async def finish_cutout(self, dest: Path) -> dict[str, Path]:
        """The white copy and the outline numbers of a cut-out already written to `dest` (by cutout(), or downloaded from
        a graph that holds the cut-out nodes itself)."""
        # Pure image work off the event loop; the session stays on this side.
        white = await asyncio.to_thread(white_composite, dest)
        stats = await asyncio.to_thread(alpha_bbox, dest)
        self.use_model("birefnet")
        return {"cutout": dest, "white": white, "stats": stats}

    # --- host tools ---------------------------------------------------------------

    async def blender(self, script: str, args: list[Any], log_name: str | None = None, timeout: float = 3600) -> str:
        self.ctx.check_cancelled()
        log_path = self.dir / (log_name or f"{Path(script).stem}.log")
        return await asyncio.to_thread(run_blender, self.settings, script, args, log_path, timeout, self.ctx.register_process)

    async def unity(self, method: str, args: dict[str, Any], flags: list[str], log_name: str, timeout: float = 3600,
                    project_path: Path | str | None = None) -> str:
        self.ctx.check_cancelled()
        return await asyncio.to_thread(run_unity, self.settings, method, args, flags, self.dir / log_name, timeout, self.ctx.register_process, project_path)

    def copy_in(self, src: Path | str, name: str) -> Path:
        dest = self.dir / name
        shutil.copy2(src, dest)
        return dest
