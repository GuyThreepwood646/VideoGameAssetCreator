"""Beauty picture for the finish card (PHOTOREAL-PLAN phase 1): arguments, the info file, and that a failed render
never fails the export."""
import asyncio
import json
from pathlib import Path

from vgac import beauty
from vgac.stages import export


class FakeSC:
    def __init__(self, tmp: Path, glb: Path | None, blender=None):
        self.dir = tmp
        self._glb = glb
        self.files: dict[str, str] = {}
        self.metrics: dict = {}
        self.calls: list = []
        self._blender = blender

    def file(self, key):
        return self._glb if key == "glb" else None

    def progress(self, *_a, **_k):
        pass

    def set_metrics(self, **kw):
        self.metrics.update(kw)

    def register(self, key, path):
        self.files[key] = str(path)
        return str(path)

    async def blender(self, script, args, log_name=None, timeout=3600):
        self.calls.append((script, args, log_name, timeout))
        if self._blender:
            self._blender(args)
        return ""


def test_light_pictures_are_bundled_with_a_licence_file():
    assert beauty.OUTDOORS_HDRI.is_file()
    lic = (beauty.HDRI_DIR / "LICENCES.txt").read_text(encoding="utf-8")
    for p in beauty.HDRI_DIR.glob("*.hdr"):
        assert p.name in lic, p.name
    assert "CC0" in lic


def test_args_name_the_model_picture_and_light():
    a = beauty.beauty_args(Path("m.glb"), Path("o/beauty.png"))
    assert a[:3] == [Path("m.glb"), Path("o/beauty.png"), beauty.OUTDOORS_HDRI]
    assert a[a.index("--size") + 1] == beauty.BEAUTY_SIZE
    assert a[a.index("--sun-u") + 1] == beauty.OUTDOORS_SUN_U


def test_render_registers_the_picture_and_its_timing(tmp_path):
    glb = tmp_path / "a.glb"
    glb.write_bytes(b"glTF")

    def fake_blender(args):
        out = Path(args[1])
        out.write_bytes(b"png")
        out.with_suffix(".json").write_text(json.dumps({"seconds": 3.8, "size": 1024, "view_transform": "Khronos PBR Neutral", "hdri": "x.hdr", "dims": [1, 1, 1]}))

    sc = FakeSC(tmp_path, glb, fake_blender)
    out = asyncio.run(beauty.render_beauty(sc))
    assert out == tmp_path / "beauty.png"
    assert sc.files["beauty"] == str(out)
    assert sc.calls[0][0] == "beauty.py" and sc.calls[0][2] == "beauty.log"
    assert sc.metrics["beauty"] == {"seconds": 3.8, "size": 1024, "view_transform": "Khronos PBR Neutral", "hdri": "x.hdr"}
    assert "beauty_error" not in sc.metrics


def test_a_failed_render_is_noted_and_does_not_raise(tmp_path):
    glb = tmp_path / "a.glb"
    glb.write_bytes(b"glTF")

    def boom(_args):
        raise RuntimeError("Eevee could not start")

    sc = FakeSC(tmp_path, glb, boom)
    assert asyncio.run(beauty.render_beauty(sc)) is None
    assert "Eevee could not start" in sc.metrics["beauty_error"]
    assert "complete" in sc.metrics["beauty_note"] and beauty.BEAUTY_TIMEOUT_S <= 120
    assert "beauty" not in sc.files


def test_no_picture_written_is_noted(tmp_path):
    glb = tmp_path / "a.glb"
    glb.write_bytes(b"glTF")
    sc = FakeSC(tmp_path, glb)
    assert asyncio.run(beauty.render_beauty(sc)) is None
    assert sc.metrics["beauty_error"] == "Blender wrote no picture"


def test_no_model_means_no_render(tmp_path):
    sc = FakeSC(tmp_path, None)
    assert asyncio.run(beauty.render_beauty(sc)) is None
    assert sc.calls == [] and sc.metrics == {}


def test_bad_info_file_is_ignored(tmp_path):
    out = tmp_path / "beauty.png"
    out.with_suffix(".json").write_text("not json")
    assert beauty.read_beauty_info(out) is None


def test_export_step_renders_the_beauty_picture():
    assert export.render_beauty is beauty.render_beauty
    assert (Path(beauty.__file__).parent / "blender" / "beauty.py").is_file()
