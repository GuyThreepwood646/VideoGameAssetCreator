"""The minimum versions of the programs the person installs by hand, read from data/minimum_versions.json.

The same file is read by setup/Check-Setup.ps1, so the numbers live in one place. A version at or above the minimum is
accepted, whatever is newer; a version that cannot be read is "unknown" (the caller shows a warning, never a block).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from .config import DATA_DIR

FILE = DATA_DIR / "minimum_versions.json"
_NUM = re.compile(r"\d+")


@lru_cache(maxsize=1)
def _table() -> dict[str, dict[str, str]]:
    data = json.loads(FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def entry(key: str) -> dict[str, str]:
    return _table()[key]


def parse(text: Any) -> tuple[int, ...] | None:
    """The leading numbers of a version text ("v22.18.0", "Python 3.13.7", "6000.4.3f1" -> (6000, 4, 3)); None if no digits.
    A trailing letter group such as Unity's "f1" is ignored."""
    if text is None:
        return None
    m = re.search(r"\d+(?:\.\d+)*", str(text))
    return tuple(int(x) for x in _NUM.findall(m.group(0))) if m else None


def floor(key: str) -> tuple[int, ...]:
    return parse(entry(key)["min"]) or (0,)


def tested(key: str) -> str:
    return entry(key)["tested"]


def check(key: str, found: Any) -> str:
    """"ok" at or above the minimum (any newer version too), "old" below it, "unknown" when the version cannot be read."""
    v = parse(found)
    if v is None:
        return "unknown"
    want = floor(key)
    n = max(len(v), len(want))
    return "ok" if v + (0,) * (n - len(v)) >= want + (0,) * (n - len(want)) else "old"


def phrase(key: str) -> str:
    """"Blender 5.0 or newer (tested with 5.2.3)" """
    e = entry(key)
    return f"{e['name']} {e['min']} or newer (tested with {e['tested']})"
