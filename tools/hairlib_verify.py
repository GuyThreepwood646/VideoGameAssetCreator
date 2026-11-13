"""Check a built hair library folder (the one setup/Setup-Extras.ps1 -Packs builds into library/hairlib).

    backend/.venv/Scripts/python.exe -I tools/hairlib_verify.py <library folder> [--against <other library folder>]

Offline, no Blender. Checks: catalogue.json reads; every style lists files that exist and match the SHA-256 the
catalogue recorded; each style's .npz holds the arrays the app reads (q, rho_r, tris, uv, role, flow); every texture
and thumbnail named is there; CREDITS.txt credits every style. With --against: the same style ids, and every catalogue
field equal to the other library's except the pack file path (source_file, which names the build machine's folder) and
the thumbnail checksums (renders differ by noise from run to run); the style and texture files must be byte-equal or,
for the .npz arrays, equal to 1e-6.
Exit 0 when everything passes, 1 otherwise. (tools/hairlib_check.py is a different thing: it fits styles on saved
characters and needs the wig-trial folders.)"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ARRAYS = ("q", "rho_r", "tris", "uv", "role", "flow")
SKIP_FIELDS = ("source_file", "sha256")


def file_map(sid: str, e: dict) -> dict[str, str]:
    m = {"npz": f"styles/{sid}.npz", "thumb_front": e["thumbs"]["front"], "thumb_quarter": e["thumbs"]["quarter"],
         "grey": e["textures"]["grey"]}
    if e["textures"].get("normal"):
        m["normal"] = e["textures"]["normal"]
    if e.get("lite"):
        m["lite"] = e["lite"]
    return m


def check(lib: Path) -> tuple[dict, list[str]]:
    bad: list[str] = []
    cp = lib / "catalogue.json"
    if not cp.is_file():
        return {}, [f"{cp} not found"]
    try:
        cat = json.loads(cp.read_text(encoding="utf-8"))
    except ValueError as ex:
        return {}, [f"catalogue.json does not read: {ex}"]
    styles = cat.get("styles") or {}
    if not styles:
        bad.append("catalogue.json lists no styles")
    for sid, e in styles.items():
        try:
            fm = file_map(sid, e)
        except (KeyError, TypeError) as ex:
            bad.append(f"{sid}: catalogue entry is missing {ex}")
            continue
        recorded = e.get("sha256") or {}
        for key, rel in fm.items():
            p = lib / rel
            if not p.is_file():
                bad.append(f"{sid}: {rel} is missing")
                continue
            want = recorded.get(key)
            if want and hashlib.sha256(p.read_bytes()).hexdigest() != want:
                bad.append(f"{sid}: {rel} does not match the recorded checksum")
        for key in ("npz", "lite"):
            if key in fm and (lib / fm[key]).is_file():
                try:
                    z = np.load(lib / fm[key])
                    miss = [a for a in ARRAYS if a not in z.files]
                    if miss:
                        bad.append(f"{sid}: {fm[key]} lacks arrays {miss}")
                except Exception as ex:  # noqa: BLE001
                    bad.append(f"{sid}: {fm[key]} does not read: {ex}")
    cr = lib / "CREDITS.txt"
    if not cr.is_file():
        bad.append("CREDITS.txt is missing")
    else:
        txt = cr.read_text(encoding="utf-8")
        for sid in styles:
            if f"{sid}:" not in txt:
                bad.append(f"{sid}: not credited in CREDITS.txt")
    return cat, bad


def compare(lib: Path, other: Path) -> list[str]:
    a, _ = check(lib)
    b, _ = check(other)
    bad: list[str] = []
    sa, sb = a.get("styles", {}), b.get("styles", {})
    if set(sa) != set(sb):
        bad.append(f"styles differ: only here {sorted(set(sa) - set(sb))}, only there {sorted(set(sb) - set(sa))}")
    for sid in sorted(set(sa) & set(sb)):
        for k in sorted(set(sa[sid]) | set(sb[sid])):
            if k not in SKIP_FIELDS and sa[sid].get(k) != sb[sid].get(k):
                bad.append(f"{sid}: field {k} differs")
        fa, fb = file_map(sid, sa[sid]), file_map(sid, sb[sid])
        for key in fa:
            if key.startswith("thumb") or key not in fb:
                continue
            if not (lib / fa[key]).is_file() or not (other / fb[key]).is_file():
                bad.append(f"{sid}: {fa[key]} is missing on one side")
                continue
            if key in ("npz", "lite"):
                za, zb = np.load(lib / fa[key]), np.load(other / fb[key])
                for arr in ARRAYS:
                    x, y = za[arr], zb[arr]
                    if x.shape != y.shape or (np.abs(x.astype(float) - y.astype(float)).max() if x.size else 0) > 1e-6:
                        bad.append(f"{sid}: {fa[key]} array {arr} differs")
            elif (lib / fa[key]).read_bytes() != (other / fb[key]).read_bytes():
                bad.append(f"{sid}: {fa[key]} differs from the other library")
    for k in ("version", "refused", "left_out"):
        if a.get(k) != b.get(k):
            bad.append(f"catalogue field {k} differs")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--against", default=None)
    a = ap.parse_args()
    lib = Path(a.lib)
    cat, bad = check(lib)
    if a.against:
        bad += compare(lib, Path(a.against))
    for line in bad:
        print("FAIL", line)
    n = len(cat.get("styles") or {})
    if bad:
        print(f"{len(bad)} problems in {lib}")
        return 1
    print(f"OK {n} styles in {lib}" + (f", same as {a.against}" if a.against else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
