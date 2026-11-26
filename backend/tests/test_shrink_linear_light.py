"""The base-colour shrink averages colour in linear light, inside each UV island: a black and white checker inside one
island shrinks to the light-correct grey (about 188/255), not the darker 128/255 an average of the encoded values gives,
and two islands still never blend."""

import numpy as np
from PIL import Image

from vgac import imaging
from vgac.stages import prep


def _one_island() -> dict:
    uv = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    return {"uv": uv, "faces": np.array([[0, 1, 2], [0, 2, 3]]), "island": np.array([0, 0]), "up": np.zeros(2)}


def _checker(size: int) -> np.ndarray:
    base = np.zeros((size, size, 3), np.float32)
    base[::2, ::2] = 1.0
    base[1::2, 1::2] = 1.0
    return base


def test_linear_checker_is_light_correct():
    out = imaging.shrink_by_island(_checker(64), _one_island(), 32, linear=True)
    want = float(imaging.linear_to_srgb(np.float32(0.5)))  # 0.7354
    assert abs(float(out[16, 16, 0]) - want) < 0.01
    plain = imaging.shrink_by_island(_checker(64), _one_island(), 32)
    assert abs(float(plain[16, 16, 0]) - 0.5) < 0.01  # data maps keep the encoded average


def test_linear_fallback_for_non_multiple_size():
    out = imaging.shrink_by_island(_checker(64), _one_island(), 40, linear=True)
    assert out.shape == (40, 40, 3)


def test_apply_look_shrinks_colour_in_linear_light(tmp_path):
    base = _checker(64)
    src = tmp_path / "base_color.png"
    imaging.write_map(base, src)
    files: dict = {}
    prep.apply_look({"cap": 32, "normal_map": False}, _one_island(), {"base_color": src}, files, base, 64, tmp_path)
    got = np.asarray(Image.open(files["base_color"]).convert("RGB"), dtype=np.float32) / 255.0
    assert abs(float(got[16, 16, 0]) - 0.7354) < 0.02
