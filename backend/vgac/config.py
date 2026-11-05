"""Load backend/config.yaml into typed settings.

The config file lives next to the backend package (backend/config.yaml). Set the
VGAC_CONFIG environment variable to use a different file. Tests call reset_settings()
after pointing VGAC_CONFIG at a temporary file.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config.yaml"
DATA_DIR = PACKAGE_DIR / "data"
BLENDER_SCRIPTS_DIR = PACKAGE_DIR / "blender"


class InstanceConfig(BaseModel):
    url: str
    gpu: str = ""
    role: str = ""
    # Local output directory of that ComfyUI instance, for nodes that save files without
    # reporting them through /history (SkinToken). Optional.
    output_dir: Path | None = None
    # On-demand start (instances.py): a PowerShell script that starts this instance, relative to the project root
    # (or absolute). Without one the backend never starts the instance and a job on it runs (and fails) as before.
    start_script: Path | None = None
    # Seconds to wait for a started instance to answer /system_stats before the start counts as failed.
    start_timeout_seconds: float = Field(default=180, gt=0)
    # A piece of the command line that identifies an already running copy (for example one started by the desktop
    # launcher or ComfyUI Desktop), so the backend waits for it instead of starting a second one.
    process_match: str | None = None
    # Who usually runs this instance when the backend did not start it, for the card's note ("ComfyUI Desktop").
    external_owner: str = ""


class Settings(BaseModel):
    instances: dict[str, InstanceConfig]
    library_path: Path
    export_path: Path | None = None
    blender_exe: Path | None = None
    unity_exe: Path | None = None
    unity_project: Path | None = None
    comfy_input_dir: Path | None = None
    clips_libraries: dict[str, Path] = Field(default_factory=dict)
    min_free_ram_gb: float = Field(default=24, ge=0)
    # Seconds a ComfyUI job may run before the worker gives up on it.
    job_timeout_seconds: float = Field(default=3600, ge=1)
    # Which instance runs each stage ("host" is Blender and Unity on this machine). Concept, turnaround and rig need
    # the image/rig GPU (b); mesh needs the 3D GPU (a). The material category is images only, so it stays on b.
    # jobs.check_stage_instances validates these against reference.STAGE_ORDER and the instances above at start.
    stage_instances: dict[str, str] = Field(default_factory=lambda: {
        "concept": "b", "turnaround": "b", "mesh": "a", "prep": "host", "rig": "b", "export": "host"})
    material_stage_instances: dict[str, str] = Field(default_factory=lambda: {
        "concept": "b", "turnaround": "b", "mesh": "b", "prep": "host", "rig": "host", "export": "host"})
    # The instance that runs the local vision judge (POST /api/versions/{id}/judge mode=local).
    judge_instance: str = "b"
    # What each graphics card can run when the card a step prefers is turned off in the app (jobs.route). Names are
    # stages plus "judge" (the local vision judge) and "material" (every GPU step of the material category). Rig is
    # b only: the SkinToken node finds Blender only through SKINTOKEN_BLENDER_BIN, which runtime/start-comfy-b.ps1
    # sets. "host" (prep and export) is never a graphics card and always runs. An instance missing from this table
    # can run the stages the tables above give it.
    instance_capabilities: dict[str, list[str]] = Field(default_factory=lambda: {
        "a": ["concept", "turnaround", "mesh", "material", "judge"],
        "b": ["concept", "turnaround", "mesh", "rig", "judge", "material"]})
    # Seconds a routine Blender step (rig repair, rig inspection, clip baking and rendering) may run.
    blender_timeout_seconds: float = Field(default=1800, ge=1)

    @property
    def db_path(self) -> Path:
        return self.library_path / "vgac.sqlite"


def load_settings(path: Path | str | None = None) -> Settings:
    config_path = Path(path or os.environ.get("VGAC_CONFIG") or DEFAULT_CONFIG_PATH)
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    settings = Settings.model_validate(raw)
    settings.library_path.mkdir(parents=True, exist_ok=True)
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reset_settings() -> None:
    """Forget the cached settings (tests switch config files)."""
    get_settings.cache_clear()
