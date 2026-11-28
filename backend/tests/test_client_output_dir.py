"""The ComfyUI client of a card gets the folder SkinToken's rigged GLB is read from: the instance's own output_dir, or
else its engine's output_dir. A card with only the engine's folder (the app's own card A setting) used to get none, and
the rig step then failed with "rigged GLB not found"."""

from __future__ import annotations

from pathlib import Path

from vgac import jobs
from vgac.config import EngineConfig, InstanceConfig


def _engine(output: str | None) -> EngineConfig:
    return EngineConfig(comfy_dir=Path("comfy"), output_dir=Path(output) if output else None)


def test_an_instance_with_only_the_engines_output_dir_gets_that_folder():
    inst = InstanceConfig(url="http://127.0.0.1:8188", engine=_engine("runtime/comfy-a-output"))
    assert jobs.client_output_dir(inst) == Path("runtime/comfy-a-output")


def test_an_instance_with_its_own_output_dir_keeps_it_over_the_engine_one():
    inst = InstanceConfig(url="http://127.0.0.1:8189", output_dir=Path("runtime/comfy-b-output"),
                          engine=_engine("runtime/comfy-other-output"))
    assert jobs.client_output_dir(inst) == Path("runtime/comfy-b-output")


def test_an_instance_with_its_own_output_dir_and_no_engine_keeps_it():
    inst = InstanceConfig(url="http://127.0.0.1:8189", output_dir=Path("runtime/comfy-b-output"))
    assert jobs.client_output_dir(inst) == Path("runtime/comfy-b-output")


def test_an_instance_with_neither_has_no_folder():
    assert jobs.client_output_dir(InstanceConfig(url="http://127.0.0.1:8188")) is None
    assert jobs.client_output_dir(InstanceConfig(url="http://127.0.0.1:8188", engine=_engine(None))) is None
