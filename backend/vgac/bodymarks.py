"""Mark the body (step 2): where the chin, shoulders, elbows, wrists, groin, knees, ankles, heels, toes and fingertips are on
the front picture and the two side pictures, found automatically, judged sure or not, and editable by the person.

The marks are found on the processor with the MediaPipe pose, hand and face landmarkers the app already has (buildpose.py,
lipmarks.py) and two outline rules (head top, groin). The pose finder scores a joint hidden under cloth as seen (a robe's
knees at 0.92 to 0.95, offline proof 2026-11-15), so a mark is sure only when four more tests agree (`judge`): it is
inside the figure, the legs are apart up to a knee's height, its height matches the side picture after one scale and
offset are fitted between the two, and the pictures' left, right and facing agree.

One document per step 2 version, `marks.json` in the version's folder (file key "marks"):
  format       FORMAT
  views        {front|left|right: the automatic marks of that picture (`find`): size [w, h], file (the cut-out's key), found,
               figure [top, bottom], legs_split, marks {joint: {x, y, score, how}}, hands {left|right: {found, score}}}
  rule         `judge` of the automatic marks: state "sure" | "check", per view and joint {sure, guessed, why [codes]}, tests
  edited       {view: {joint: {x, y, at, stale}}}: the person's moves, kept apart; never written over by the automatic layer
  accepted     {at} when the person said the marks look right, else None
  computed_at  when the automatic layer was found; redrawn {view: version id} for views found again after a redraw
Pixel positions are in the cut-out picture's own pixels (x right, y down). Joint names are always the person's own
left and right: on the back picture the person's left is on the picture's left (mirrored from the front). Off the front
picture, a mark the app cannot see (hidden by the body, a face turned away, no gap between the legs) has x and y null
and how "not_seen" (`hide_unseen`); the person can place it. A redraw recomputes only the redrawn views'
automatic marks; edits on a redrawn view are kept and marked stale (`carry`).

Nothing reads the marks yet. STOP_WHEN_UNSURE (off) makes autopilot stop and ask on an unsure set (autopilot.STOP_AND_ASK);
a version's `body_marks_stop` param overrides it. For the rig and painter: `lift` (3D joints from marks and cameras) and
`finger_build_marks` (the file blender/finger_build.py --marks reads).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

FORMAT = 1
MARK_VIEWS = ("front", "quarter", "quarter2", "left", "right", "back")
SIDE_VIEWS = ("left", "right")          # in profile: the far side is behind the body
QUARTER_VIEWS = ("quarter", "quarter2")
# The side each picture shows more of: the left side picture and the three-quarter picture turned toward the person's
# left (stages/turnaround TURN_SIDE) show the person's left; the others their right. The front and back show both.
NEAR = {"left": "L", "quarter": "L", "right": "R", "quarter2": "R"}
HIDE_BELOW = 0.5     # off the front picture, a pose mark seen less than this is "not seen": no position, the person places it
STOP_WHEN_UNSURE = False   # off until the rig or the painter reads the marks; a version's body_marks_stop overrides it
CATEGORIES = ("humanoid",)

FINGERS = ("thumb", "index", "middle", "ring", "little")
REQUIRED = ("chin", "shoulder_L", "shoulder_R", "elbow_L", "elbow_R", "wrist_L", "wrist_R", "groin",
            "knee_L", "knee_R", "ankle_L", "ankle_R")
OPTIONAL = ("head_top", "hip_L", "hip_R", "heel_L", "heel_R", "toe_L", "toe_R")
TIPS = tuple(f"tip_{f}_{s}" for s in "LR" for f in FINGERS)
JOINTS = ("head_top", "chin", "shoulder_L", "shoulder_R", "elbow_L", "elbow_R", "wrist_L", "wrist_R", "hip_L", "hip_R",
          "groin", "knee_L", "knee_R", "ankle_L", "ankle_R", "heel_L", "heel_R", "toe_L", "toe_R") + TIPS
# test-only points, kept with the automatic marks but never shown or edited
EXTRA = ("nose", "ear_L", "ear_R")

POSE_INDEX = {"shoulder_L": 11, "shoulder_R": 12, "elbow_L": 13, "elbow_R": 14, "wrist_L": 15, "wrist_R": 16,
              "hip_L": 23, "hip_R": 24, "knee_L": 25, "knee_R": 26, "ankle_L": 27, "ankle_R": 28,
              "heel_L": 29, "heel_R": 30, "toe_L": 31, "toe_R": 32, "nose": 0, "ear_L": 7, "ear_R": 8}
TIP_INDEX = {"thumb": 4, "index": 8, "middle": 12, "ring": 16, "little": 20}

SEEN = 0.8            # a pose mark seen at least this well
INSIDE_PX = 6         # a mark this close to the figure counts as on it
HEIGHT_TOL = 0.03     # of the figure's height: a joint's height in the front and the side picture after the fitted scale
NEAR_MARGIN = 0.2     # a side picture sees its own side better than the other by this much (at least 3 of 4 joints)
CHIN_FALLBACK = 0.3   # score of a chin placed from the mouth when no face was found


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def plain_joint(joint: str) -> str:
    """"left elbow", "right index fingertip", "groin"."""
    side = {"L": "left ", "R": "right "}.get(joint[-1], "") if joint[-2:] in ("_L", "_R") else ""
    base = joint[:-2] if side else joint
    if base.startswith("tip_"):
        return f"{side}{base[4:]} fingertip".replace("thumb fingertip", "thumb tip")
    return side + {"head_top": "top of the head", "toe": "toes"}.get(base, base)


# --- finding --------------------------------------------------------------------------------------------------------


def _load(path: Path | str) -> dict[str, Any]:
    """The cut-out cropped to the figure with a margin (as buildpose.load), and where the crop sits in the picture."""
    a = np.asarray(Image.open(path).convert("RGBA"))
    m = a[..., 3] > 127
    ys, xs = np.nonzero(m)
    if not len(ys):
        raise ValueError("the cut-out is empty")
    h = int(ys.max() - ys.min() + 1)
    pad = int(0.08 * h)
    y0, y1 = max(0, ys.min() - pad), min(a.shape[0], ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(a.shape[1], xs.max() + 1 + pad)
    c = a[y0:y1, x0:x1]
    al = c[..., 3:4].astype(np.float64) / 255.0
    rgb = (c[..., :3] * al + 255.0 * (1 - al)).round().astype(np.uint8)
    return {"rgb": np.ascontiguousarray(rgb), "mask": c[..., 3] > 127, "height": h, "off": (int(x0), int(y0)),
            "full": m, "top": int(ys.min()), "bottom": int(ys.max()), "size": [int(a.shape[1]), int(a.shape[0])]}


def _face_points(rgb: np.ndarray, P: np.ndarray) -> np.ndarray | None:
    """The face mesh's 478 points on a crop round the pose's nose (crop pixels), or None."""
    from . import lipmarks

    lm = lipmarks._get_landmarker()
    if lm is None:
        return None
    import mediapipe as mp

    half = int(max(0.9 * float(np.linalg.norm(P[11, :2] - P[12, :2])), 60))
    x0, y0 = int(P[0, 0] - half), int(P[0, 1] - half)
    H, W = rgb.shape[:2]
    canvas = np.full((2 * half, 2 * half, 3), 255, np.uint8)
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(W, x0 + 2 * half), min(H, y0 + 2 * half)
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    canvas[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = rgb[cy0:cy1, cx0:cx1]
    img = np.asarray(Image.fromarray(canvas).resize((512, 512), Image.Resampling.BICUBIC))
    res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img)))
    if not res.face_landmarks:
        return None
    k = 2 * half / 512
    return np.array([[p.x * 512 * k + x0, p.y * 512 * k + y0] for p in res.face_landmarks[0]])


def _head_top(mask: np.ndarray, cx: float, half_w: float) -> float | None:
    cols = mask[:, max(0, int(cx - half_w)):int(cx + half_w) + 1]
    rows = np.nonzero(cols.any(axis=1))[0]
    return float(rows.min()) if len(rows) else None


def _groin(mask: np.ndarray, P: np.ndarray) -> tuple[np.ndarray, bool]:
    """The top of the gap between the legs straight down from the hips' middle, before the knees; else a third of the
    way from the hips to the knees (not seen)."""
    mid = (P[23, :2] + P[24, :2]) / 2
    kn = (P[25, :2] + P[26, :2]) / 2
    x = int(round(mid[0]))
    for y in range(int(mid[1]), min(mask.shape[0], int(kn[1]))):
        if 0 <= x < mask.shape[1] and not mask[y, max(0, x - 1):x + 2].any():
            return np.array([float(x), float(y)]), True
    return np.array([mid[0], mid[1] + 0.3 * (kn[1] - mid[1])]), False


def find(path: Path | str, key: str = "") -> dict[str, Any]:
    """The automatic marks of one cut-out (see the module's document format). found False when no person was read;
    marks then come from `template` and every one is unsure."""
    from . import buildpose

    L = _load(path)
    out: dict[str, Any] = {"size": L["size"], "file": key, "found": False, "figure": [L["top"], L["bottom"]],
                           "legs_split": None, "marks": {}, "hands": {}}
    try:
        out["legs_split"] = float(buildpose.legs_outline(L["full"])["split"])
    except Exception:  # noqa: BLE001  (an outline too small to read: the knee test then has no answer)
        pass
    P = buildpose.pose_points(L["rgb"])
    if P is None:
        return out
    out["found"] = True
    ox, oy = L["off"]
    M = out["marks"]

    def put(name: str, xy: Any, score: float, how: str) -> None:
        M[name] = {"x": round(float(xy[0]) + ox, 1), "y": round(float(xy[1]) + oy, 1), "score": round(float(score), 3), "how": how}

    for n, i in POSE_INDEX.items():
        put(n, P[i, :2], P[i, 2], "pose")
    F = _face_points(L["rgb"], P)
    if F is not None:
        put("chin", F[152], 0.9, "face")
        cx, half = float(F[[10, 152], 0].mean()), 0.25 * float(np.linalg.norm(F[234] - F[454]))
    else:
        mouth = (P[9, :2] + P[10, :2]) / 2
        put("chin", mouth + 0.8 * (mouth - P[0, :2]), CHIN_FALLBACK * min(P[9, 2], P[10, 2]), "mouth")
        cx, half = float(P[0, 0]), 0.15 * float(np.linalg.norm(P[11, :2] - P[12, :2]))
    top = _head_top(L["mask"], cx, half)
    if top is not None:
        put("head_top", (cx, top), 0.9 if F is not None else 0.5, "outline")
    g, gap = _groin(L["mask"], P)
    put("groin", g, min(P[23, 2], P[24, 2]) if gap else CHIN_FALLBACK, "outline" if gap else "hips")
    # At each knee's own height: two separate legs in the outline (a robe or a long skirt makes one shape there). Read on
    # the knee's row, not by scanning up from the soles, which a foot set in front of the other cuts short.
    out["knees_apart"] = {}
    for k in ("knee_L", "knee_R"):
        y = int(round(M[k]["y"]))
        if 0 <= y < L["full"].shape[0]:
            out["knees_apart"][k] = len(buildpose._runs(L["full"][y], 3)) >= 2
    for side, hd in buildpose.hands_at(L["rgb"], P, L["height"]).items():
        S = "L" if side == "left" else "R"
        if "points" not in hd:
            out["hands"][side] = {"found": False}
            continue
        out["hands"][side] = {"found": True, "score": hd["score"]}
        for f, i in TIP_INDEX.items():
            put(f"tip_{f}_{S}", hd["points"][i], hd["score"], "hand")
    return out


# Where each mark goes when the finder read no person: shares of the figure's height, x from the figure's middle (positive
# toward the picture's right), y down from its top. Front: the person's left is on the picture's right.
TEMPLATE_FRONT = {"head_top": (0, 0), "chin": (0, 0.12), "shoulder": (0.11, 0.19), "elbow": (0.17, 0.33), "wrist": (0.22, 0.46),
                  "hip": (0.06, 0.51), "groin": (0, 0.53), "knee": (0.07, 0.73), "ankle": (0.08, 0.93), "heel": (0.08, 0.97),
                  "toe": (0.10, 0.99)}


def template(view: str, size: list[int], figure: list[int], width_px: float | None = None) -> dict[str, dict[str, Any]]:
    """Placeholder marks for a picture the finder could not read: drawn where a standing figure's joints usually are, all
    unsure, so the person drags them into place."""
    top, bottom = figure
    h = max(1.0, float(bottom - top))
    cx = size[0] / 2 if width_px is None else width_px
    face = {"left": -1, "right": 1}.get(view)    # a side picture: which way the face points (left: the picture's left)
    mirror = -1 if view == "back" else 1         # the back: the person's left on the picture's left
    out: dict[str, dict[str, Any]] = {}
    for base, (dx, dy) in TEMPLATE_FRONT.items():
        sides = ("",) if base in ("head_top", "chin", "groin") else ("_L", "_R")
        for s in sides:
            if face is None:
                x = cx + mirror * (dx if s == "_L" else -dx if s == "_R" else 0) * h
            else:
                x = cx + (0.04 * face * h if base in ("toe", "chin") else -0.02 * face * h if base == "heel" else 0)
            out[base + s] = {"x": round(x, 1), "y": round(top + dy * h, 1), "score": 0.0, "how": "placeholder"}
    return out


# --- the rule --------------------------------------------------------------------------------------------------------

WHY = {
    "not_found": "The app could not find this one, so it placed it where it usually is.",
    "hidden": "The app guessed this one, because clothing or hair hides it.",
    "not_seen": "The app could not see this one clearly.",
    "outside": "This mark is off the body.",
    "height": "This mark is at a different height in the side picture.",
    "not_seen_here": "The app could not see this one in this picture. Place it if you can see it.",
}
GUESSED = ("not_found", "hidden")


def _norm_y(v: dict[str, Any], y: float) -> float:
    top, bottom = v["figure"]
    return (y - top) / max(1.0, float(bottom - top))


def _xy(m: dict[str, Any]) -> tuple[float, float]:
    """A mark's place for the tests between pictures: its own, or where the finder had it before it was hidden."""
    return (m["x"], m["y"]) if m.get("x") is not None else tuple(m.get("at") or (math.nan, math.nan))


def _inside(mask: np.ndarray | None, m: dict[str, Any]) -> bool:
    if mask is None or m.get("x") is None:
        return True
    x, y = int(m["x"]), int(m["y"])
    return bool(mask[max(0, y - INSIDE_PX):y + INSIDE_PX + 1, max(0, x - INSIDE_PX):x + INSIDE_PX + 1].any())


def height_fit(front: dict[str, Any], side: dict[str, Any], near: str) -> np.ndarray:
    """front height = a * side height + b, fitted over the near side's joints both pictures see well (the side views are
    often drawn a little taller or shorter in the legs); (1, 0) with fewer than four."""
    pairs = []
    for n in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle"):
        a, b = front["marks"].get(f"{n}_{near}"), side["marks"].get(f"{n}_{near}")
        if a and b and a["score"] >= SEEN and b["score"] >= SEEN and a["how"] == b["how"] == "pose":
            pairs.append((_norm_y(side, b["y"]), _norm_y(front, a["y"])))
    if len(pairs) < 4:
        return np.array([1.0, 0.0])
    A = np.array(pairs)
    return np.polyfit(A[:, 0], A[:, 1], 1)


def judge(views: dict[str, dict[str, Any]], masks: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    """Which automatic marks are sure. A front-picture mark is sure when it is found and seen (score SEEN, a face for the
    chin, the outline's gap for the groin), on the figure, not hidden (knees: the legs are apart up to its height in the
    outline), and at the height the side picture puts it. A side picture's marks: its own side found and seen; the far side
    is never asked about. The set is sure when every REQUIRED front mark is and these tests pass: front_left_right (the
    person's left shoulder and hip on the picture's right), <side>_near (the side picture sees its own side better), <side>_facing
    (its nose and toes point the way it should face), front_face (a face was found)."""
    masks = masks or {}
    front = views.get("front")
    out: dict[str, Any] = {"state": "check", "views": {}, "tests": {}}
    if not front:
        out["tests"]["front_found"] = False
        return out
    fits = {s: height_fit(front, views[s], "L" if s == "left" else "R") for s in SIDE_VIEWS if views.get(s) and views[s].get("found")}
    for name, v in views.items():
        per: dict[str, Any] = {}
        for joint, m in v.get("marks", {}).items():
            if joint in EXTRA:
                continue
            why: list[str] = []
            if m["how"] == "placeholder":
                why.append("not_found")
            elif m["how"] == "not_seen":
                why.append("not_seen_here")
            else:
                if joint == "groin":
                    if m["how"] != "outline":
                        why.append("hidden")
                elif joint == "chin":
                    if m["how"] != "face":
                        why.append("not_seen")
                elif m["score"] < SEEN:
                    why.append("not_seen")
                if not _inside(masks.get(name), m):
                    why.append("outside")
                # The outline tests for clothing over the legs: on the front and the back only (in profile the legs are one
                # shape anyway, and on a three-quarter picture one knee is often behind the other); the three-quarter
                # pictures take the front's answer below, the clothing being the same.
                legs = name in ("front", "back")
                if legs and joint.startswith("hip") and v.get("marks", {}).get("groin", {}).get("how") not in ("outline", None):
                    why.append("hidden")   # no gap between the legs: the hips are under the same clothing
                if legs and joint.startswith("knee"):
                    apart = (v.get("knees_apart") or {}).get(joint)
                    if apart is False:
                        why.append("hidden")
                    elif apart is None and v.get("legs_split") is not None and v["legs_split"] < 1 - _norm_y(v, m["y"]) - 0.02:
                        why.append("hidden")   # a document from before knees_apart
                if name == "front" and joint not in ("groin", "head_top") and not joint.startswith("tip_") and "not_found" not in why:
                    for s, fit in fits.items():
                        sm = views[s]["marks"].get(joint)
                        own = joint.endswith("_" + ("L" if s == "left" else "R")) or joint == "chin"
                        if own and sm and sm["score"] >= SEEN and sm["how"] == m["how"]:
                            dh = _norm_y(v, m["y"]) - float(np.polyval(fit, _norm_y(views[s], sm["y"])))
                            if abs(dh) > HEIGHT_TOL:
                                why.append("height")
                                break
            per[joint] = {"sure": not why, "guessed": any(w in GUESSED for w in why), "why": why,
                          "seen": m["how"] != "not_seen"}
        out["views"][name] = per
    fr = out["views"].get("front", {})
    for name in QUARTER_VIEWS:
        for joint, r in (out["views"].get(name) or {}).items():
            if r["seen"] and "hidden" in (fr.get(joint) or {}).get("why", []) and "hidden" not in r["why"]:
                r["why"].append("hidden")
                r.update(sure=False, guessed=True)
    T = out["tests"]
    T["front_found"] = bool(front.get("found"))
    fm = front.get("marks", {})
    if front.get("found"):
        T["front_left_right"] = all(fm[f"{p}_L"]["x"] > fm[f"{p}_R"]["x"] for p in ("shoulder", "hip"))
        T["front_face"] = fm.get("chin", {}).get("how") == "face"
    for s in SIDE_VIEWS:
        v = views.get(s)
        if not v or not v.get("found"):
            continue
        near, far = ("L", "R") if s == "left" else ("R", "L")
        sm = v["marks"]
        diffs = [sm[f"{p}_{near}"]["score"] - sm[f"{p}_{far}"]["score"] for p in ("elbow", "wrist", "knee", "ankle")]
        T[f"{s}_near"] = sum(d > NEAR_MARGIN for d in diffs) >= 3
        want = -1 if s == "left" else 1
        nose = np.sign(_xy(sm["nose"])[0] - _xy(sm[f"ear_{near}"])[0])
        toes = np.sign(_xy(sm[f"toe_{near}"])[0] - _xy(sm[f"heel_{near}"])[0])
        T[f"{s}_facing"] = bool(nose == want and toes == want)
    # Left and right on the three-quarter pictures (turned under 90 degrees: the person's left still on the picture's
    # right) and on the back (the person's left on the picture's left), from the shoulders and hips wherever the finder
    # put them (also when it saw one side poorly).
    for s, sign in (("quarter", 1), ("quarter2", 1), ("back", -1)):
        v = views.get(s)
        if not v or not v.get("found"):
            continue
        sm = v["marks"]
        pairs = [(_xy(sm[f"{p}_L"])[0], _xy(sm[f"{p}_R"])[0]) for p in ("shoulder", "hip") if f"{p}_L" in sm and f"{p}_R" in sm]
        if pairs:
            T[f"{s}_left_right"] = all(sign * (a - b) > 0 for a, b in pairs)
    req = out["views"].get("front", {})
    ok = all(req.get(j, {}).get("sure") for j in REQUIRED) and all(T.values())
    out["state"] = "sure" if ok else "check"
    return out


TEST_WORDS = {
    "front_found": "The app could not find the body in the front picture, so every mark is a guess.",
    "front_left_right": "Left and right look swapped in the front picture.",
    "front_face": "No face was found in the front picture, so the chin is a guess.",
    "left_near": "The left side picture may show the other side.",
    "right_near": "The right side picture may show the other side.",
    "left_facing": "In the left side picture the face and the feet do not point the same way.",
    "right_facing": "In the right side picture the face and the feet do not point the same way.",
    "quarter_left_right": "Left and right look swapped in the three-quarter picture.",
    "quarter2_left_right": "Left and right look swapped in the other three-quarter picture.",
    "back_left_right": "Left and right look swapped in the back picture (there the person's left is on the picture's left).",
}
VIEW_WORDS = {"front": "front picture", "left": "left side picture", "right": "right side picture",
              "quarter": "three-quarter picture", "quarter2": "other three-quarter picture", "back": "back picture"}


def to_check(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """What the person is asked to look at: [{view, joint, name, why, words}] for the front picture's unsure required marks
    (a moved mark counts as checked), then the failed tests (joint None). A side picture's unsure marks are shown as unsure
    but not asked about: the offline proof's rule judges the set by the front picture and the tests."""
    rule = doc.get("rule") or {}
    edited = doc.get("edited") or {}
    out = []
    for view in MARK_VIEWS:
        for joint, r in (rule.get("views", {}).get(view) or {}).items():
            if view != "front" or r["sure"] or joint in edited.get(view, {}) or joint in TIPS or joint in OPTIONAL:
                continue
            words = " ".join(WHY[w] for w in r["why"][:1])
            out.append({"view": view, "joint": joint, "name": plain_joint(joint), "why": r["why"], "guessed": r["guessed"],
                        "words": words})
    for t, ok in (rule.get("tests") or {}).items():
        if not ok:
            out.append({"view": "front" if t.startswith("front") else t.split("_")[0], "joint": None, "name": None, "why": [t],
                        "guessed": False, "words": TEST_WORDS.get(t, t)})
    return out


def stale(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"view": v, "joint": j, "name": plain_joint(j)} for v, js in (doc.get("edited") or {}).items()
            for j, e in js.items() if e.get("stale")]


def state(doc: dict[str, Any]) -> str:
    """"sure": the app placed the marks and is confident; "check": please check these (unsure marks, failed tests, or
    moved marks on a picture that was drawn again); "edited": the person moved marks or said they look right, and
    nothing is left to check."""
    if stale(doc):
        return "check"
    asked = to_check(doc)
    if doc.get("accepted"):
        return "edited"
    if asked:
        return "check"
    return "edited" if doc.get("edited") else "sure"


def summary(doc: dict[str, Any]) -> dict[str, Any]:
    """metrics.body_marks: state, the marks to check, the moved marks that may be out of date, the pictures."""
    return {"state": state(doc), "to_check": [{k: c[k] for k in ("view", "joint", "words")} for c in to_check(doc)],
            "stale": stale(doc), "views": [v for v in MARK_VIEWS if v in (doc.get("views") or {})],
            "edited": sum(len(js) for js in (doc.get("edited") or {}).values()), "accepted": bool(doc.get("accepted"))}


def stop_on(params: dict[str, Any] | None) -> bool:
    p = (params or {}).get("body_marks_stop")
    return STOP_WHEN_UNSURE if p is None else bool(p)


def check(doc: dict[str, Any], stop: bool) -> dict[str, Any]:
    """The body_marks check: passes unless the state is "check". vars: state, count, words (the first few things to
    check, plain), stop (autopilot stops and asks on it: autopilot.STOP_AND_ASK)."""
    from . import quality

    st = state(doc)
    asked = to_check(doc)
    st_list = stale(doc)
    bits = [f"{c['name']} in the {VIEW_WORDS[c['view']]}" if c["joint"] else c["words"].rstrip(".") for c in asked]
    if st_list:
        bits.insert(0, "the marks you moved on a picture that was drawn again")
    words = "; ".join(bits[:4]) + (f"; and {len(bits) - 4} more" if len(bits) > 4 else "")
    n = len(asked) + len(st_list)
    return quality.check("body_marks", st != "check", n, 0, vars={"state": st, "count": n, "words": words, "stop": bool(stop)})


# --- the document ----------------------------------------------------------------------------------------------------


def _masks(files: dict[str, Path]) -> dict[str, np.ndarray]:
    out = {}
    for v, p in files.items():
        try:
            out[v] = np.asarray(Image.open(p).convert("RGBA"))[..., 3] > 127
        except Exception:  # noqa: BLE001
            pass
    return out


def hide_unseen(view: str, v: dict[str, Any]) -> dict[str, Any]:
    """Off the front picture, the marks the app cannot see lose their position (how "not_seen"; the finder's spot is kept
    under "at" for the tests between pictures): a pose mark seen under HIDE_BELOW, the far side of a side picture (behind
    the body, whatever the finder scored), a chin with no face found (always on the back), a groin with no gap between
    the legs. Rather no mark than a guess: the person places what they can see."""
    if view == "front":
        return v
    far = {"left": "_R", "right": "_L"}.get(view)
    for joint, m in v.get("marks", {}).items():
        if joint in EXTRA or m.get("x") is None:
            continue
        hidden = ((far is not None and joint.endswith(far))
                  or (m["how"] == "pose" and m["score"] < HIDE_BELOW)
                  or (joint == "chin" and (m["how"] != "face" or view == "back"))
                  or (joint == "groin" and m["how"] != "outline")
                  or m["how"] == "placeholder")
        if hidden:
            m.update(at=[m["x"], m["y"]], x=None, y=None, how="not_seen")
    return v


def find_view(view: str, path: Path, finder: Callable[[Path, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    v = (finder or find)(path, f"{view}_cutout")
    if not v.get("found"):
        if view == "front":   # the front needs every mark: placeholders where a standing figure's joints usually are
            filled = template(view, v["size"], v["figure"])
            v["marks"] = {**filled, **{k: m for k, m in v.get("marks", {}).items()}}
        else:                 # any other picture: nothing placed, every mark for the person to place
            v["marks"] = {j: {"x": None, "y": None, "score": 0.0, "how": "not_seen"} for j in JOINTS if j not in TIPS}
            return v
    return hide_unseen(view, v)


def compute(files: dict[str, Path], finder: Callable[[Path, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """A new document from the cut-outs ({view: path} for the MARK_VIEWS a version has)."""
    views = {v: find_view(v, files[v], finder) for v in MARK_VIEWS if v in files}
    return {"format": FORMAT, "views": views, "rule": judge(views, _masks(files)), "edited": {}, "accepted": None,
            "computed_at": _now(), "redrawn": {}}


def carry(parent: dict[str, Any] | None, files: dict[str, Path], redrawn: list[str], version_id: str = "",
          finder: Callable[[Path, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """The document of a version made from `parent` with `redrawn` views drawn again: only those views' automatic marks
    are found again; the person's moves on every view are kept, and those on a redrawn view are marked stale (never
    dropped). Without a usable parent, everything is found afresh."""
    if not parent or parent.get("format") != FORMAT:
        return compute(files, finder)
    views = {}
    for v in MARK_VIEWS:
        if v not in files:
            continue
        if v in redrawn or v not in (parent.get("views") or {}):
            views[v] = find_view(v, files[v], finder)
        else:
            views[v] = parent["views"][v]
    edited = json.loads(json.dumps(parent.get("edited") or {}))
    for v in set(redrawn) | {v for v in edited if v not in files}:   # drawn again, or no longer in this version
        for e in (edited.get(v) or {}).values():
            e["stale"] = True
    touched = [v for v in redrawn if v in MARK_VIEWS]
    return {"format": FORMAT, "views": views, "rule": judge(views, _masks(files)), "edited": edited,
            "accepted": None if touched else parent.get("accepted"), "computed_at": _now(),
            "redrawn": {**(parent.get("redrawn") or {}), **{v: version_id for v in touched}}}


class MarkError(ValueError):
    pass


def apply_moves(doc: dict[str, Any], moves: list[dict[str, Any]]) -> dict[str, Any]:
    """The person's moves [{view, joint, x, y}] into the edited layer (a moved mark is no longer stale)."""
    for mv in moves:
        view, joint = mv.get("view"), mv.get("joint")
        if view not in (doc.get("views") or {}):
            raise MarkError(f"there is no {view!r} picture with marks on this version")
        if joint not in JOINTS:
            raise MarkError(f"{joint!r} is not a mark")
        w, h = doc["views"][view]["size"]
        try:
            x, y = float(mv["x"]), float(mv["y"])
        except (KeyError, TypeError, ValueError):
            raise MarkError("a moved mark needs x and y numbers")
        if not (math.isfinite(x) and math.isfinite(y)) or not (0 <= x <= w and 0 <= y <= h):
            raise MarkError(f"the {plain_joint(joint)} was moved off the picture")
        doc.setdefault("edited", {}).setdefault(view, {})[joint] = {"x": round(x, 1), "y": round(y, 1), "at": _now(), "stale": False}
    return doc


def accept(doc: dict[str, Any]) -> dict[str, Any]:
    """"These look right": every mark as it stands, the moved ones no longer stale."""
    for js in (doc.get("edited") or {}).values():
        for e in js.values():
            e["stale"] = False
    doc["accepted"] = {"at": _now()}
    return doc


def set_accepted(doc: dict[str, Any], on: bool) -> dict[str, Any]:
    """Undo and redo of "These look right": the flag on or off, as it was (no moves change)."""
    doc["accepted"] = {"at": _now()} if on else None
    return doc


def reset(doc: dict[str, Any], view: str | None = None, joint: str | None = None) -> dict[str, Any]:
    """Back to the app's marks: the person's moves dropped (one mark, one picture, or all), and any "these look right".
    Dropping one mark the app did not see takes it off the picture again, ready to be placed."""
    edited = doc.get("edited") or {}
    if view is None:
        doc["edited"] = {}
    elif joint is None:
        edited.pop(view, None)
    else:
        (edited.get(view) or {}).pop(joint, None)
        if view in edited and not edited[view]:
            edited.pop(view)
    doc["accepted"] = None
    return doc


def effective(doc: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """The marks to use: {view: {joint: {x, y, source ("app" | "you"), sure, guessed, stale}}}, the person's moves over the
    app's. Test-only points are left out."""
    rule = (doc.get("rule") or {}).get("views", {})
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for view, v in (doc.get("views") or {}).items():
        per = {}
        for joint, m in v.get("marks", {}).items():
            if joint in EXTRA:
                continue
            r = (rule.get(view) or {}).get(joint)
            per[joint] = {"x": m["x"], "y": m["y"], "source": "app", "sure": None if r is None else r["sure"],
                          "guessed": bool(r and r["guessed"]), "stale": False, "seen": m.get("x") is not None}
        for joint, e in ((doc.get("edited") or {}).get(view) or {}).items():
            per[joint] = {"x": e["x"], "y": e["y"], "source": "you", "sure": True, "guessed": False, "stale": bool(e.get("stale")),
                          "seen": True}
        out[view] = per
    return out


def placeholders(doc: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """Where to put a mark the app did not see when the person asks to place it: where that joint usually is on a standing
    figure in this picture (`template`, mirrored on the back), for them to drag the rest of the way."""
    eff = effective(doc)
    out: dict[str, dict[str, dict[str, float]]] = {}
    for view, v in (doc.get("views") or {}).items():
        t = template(view, v["size"], v["figure"])
        missing = {j: {"x": t[j]["x"], "y": t[j]["y"]} for j, m in (eff.get(view) or {}).items() if not m["seen"] and j in t}
        if missing:
            out[view] = missing
    return out


def complete(doc: dict[str, Any], files: dict[str, Path], finder: Callable[[Path, str], dict[str, Any]] | None = None) -> bool:
    """A document made before a picture had marks (the three-quarter and back pictures came later): finds the missing
    pictures' marks and judges the set again; the person's moves are kept. True when something was added."""
    missing = [v for v in MARK_VIEWS if v in files and v not in (doc.get("views") or {})]
    if not missing:
        return False
    for v in missing:
        doc["views"][v] = find_view(v, files[v], finder)
    doc["rule"] = judge(doc["views"], _masks({v: files[v] for v in doc["views"] if v in files}))
    return True


def read(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).is_file():
        return None
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("format") == FORMAT else None


def write(doc: dict[str, Any], path: Path) -> Path:
    path = Path(path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


# --- for the rig and the painter ---------------------------------------------------------------------------------------


def ray(cam: Any, x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
    """The line of sight through pixel (x, y) of an orthographic face.Camera: (a point on it, its direction)."""
    right, up, fwd = cam.basis
    s = cam.scale / cam.size
    return np.asarray(cam.target) + (x - cam.size / 2) * s * right - (y - cam.size / 2) * s * up, np.asarray(fwd)


def nearest_point(rays: list[tuple[np.ndarray, np.ndarray]], prior: np.ndarray | None = None) -> tuple[np.ndarray, list[float]]:
    """The point nearest all the rays (least squares), weakly pulled to `prior` so a joint seen along one direction only
    stays finite (default: the rays' own points, on the plane through each camera's target, so a joint one picture sees
    sits at the depth of the model's middle); and each ray's distance from it (metres)."""
    if prior is None:
        prior = np.mean([o for o, _ in rays], axis=0)
    A, b = np.zeros((3, 3)), np.zeros(3)
    for o, d in rays:
        M = np.eye(3) - np.outer(d, d)
        A += M
        b += M @ o
    A += 1e-4 * np.eye(3)
    b += 1e-4 * np.asarray(prior, float)
    p = np.linalg.solve(A, b)
    return p, [float(np.linalg.norm((np.eye(3) - np.outer(d, d)) @ (p - o))) for o, d in rays]


def lift(doc: dict[str, Any], to_camera: Callable[[str, float, float], tuple[Any, float, float] | None],
         prior: np.ndarray | None = None, max_miss_m: float = 0.03) -> dict[str, dict[str, Any]]:
    """Joints in 3D on the model: {joint: {xyz (model frame, metres), views, worst_m}}. `to_camera(view, x, y)` gives, for a
    mark at picture pixel (x, y), the face.Camera that picture was fitted with and the mark's pixel in that camera's frame
    (None when the picture was not fitted). Only sure marks (or the person's) count, a side picture's only on its own side;
    the ray furthest off is dropped while over max_miss_m and more than two remain."""
    eff = effective(doc)
    rays: dict[str, list[tuple[str, tuple[np.ndarray, np.ndarray]]]] = {}
    for view, marks in eff.items():
        near = {"left": "_L", "right": "_R"}.get(view)
        for joint, m in marks.items():
            if not m["sure"] or m["stale"]:
                continue
            if near and joint[-2:] in ("_L", "_R") and not joint.endswith(near):
                continue
            hit = to_camera(view, m["x"], m["y"])
            if hit is None:
                continue
            cam, px, py = hit
            rays.setdefault(joint, []).append((view, ray(cam, px, py)))
    out = {}
    for joint, rs in rays.items():
        rs = list(rs)
        while True:
            p, miss = nearest_point([r for _, r in rs], prior)
            w = int(np.argmax(miss))
            if miss[w] > max_miss_m and len(rs) > 2:
                rs.pop(w)
                continue
            break
        out[joint] = {"xyz": [round(float(c), 5) for c in p], "views": [v for v, _ in rs], "worst_m": round(max(miss), 4)}
    return out


def gltf_to_blender(p: Any) -> list[float]:
    """A point in the glTF frame (Y up, +Z the front) as Blender's (Z up, -Y the front): (x, -z, y)."""
    x, y, z = (float(c) for c in p)
    return [x, -z, y]


def finger_build_marks(joints3d: dict[str, dict[str, Any]], frame: str = "gltf") -> dict[str, Any]:
    """The file blender/finger_build.py --marks reads: {"Left"|"Right": {"fingertips": [index, middle, ring, little] world
    points in Blender's frame}}, only for a hand with all four (the thumb is not used there). The person's left is the
    rig's Left. A hand without them is left out, and the reason is under "missing"."""
    out: dict[str, Any] = {"missing": {}}
    for S, side in (("L", "Left"), ("R", "Right")):
        tips = [joints3d.get(f"tip_{f}_{S}") for f in ("index", "middle", "ring", "little")]
        if all(tips):
            out[side] = {"fingertips": [gltf_to_blender(t["xyz"]) if frame == "gltf" else list(t["xyz"]) for t in tips]}
        else:
            out["missing"][side] = "no hand was found, or not all four fingertips were sure, in the pictures"
    return out


def render_from_picture(qx: np.ndarray, qy: np.ndarray, x: float, y: float) -> tuple[float, float] | None:
    """A picture pixel's place in a fitted camera's frame, from the warp the fit sampled the picture with (qx, qy: the
    picture pixel each camera pixel reads, as srcproject.fit_view builds them): the camera pixel whose sample lands
    nearest (x, y), refined to a tenth of a pixel. None when no camera pixel reads within 3 picture pixels."""
    d = (qx - x) ** 2 + (qy - y) ** 2
    i = int(np.argmin(d))
    if d.flat[i] > 9.0:
        return None
    r, c = divmod(i, qx.shape[1])
    r0, r1, c0, c1 = max(0, r - 1), min(qx.shape[0] - 1, r + 1), max(0, c - 1), min(qx.shape[1] - 1, c + 1)
    J = np.array([[(qx[r, c1] - qx[r, c0]) / max(1, c1 - c0), (qx[r1, c] - qx[r0, c]) / max(1, r1 - r0)],
                  [(qy[r, c1] - qy[r, c0]) / max(1, c1 - c0), (qy[r1, c] - qy[r0, c]) / max(1, r1 - r0)]])
    try:
        dc, dr = np.linalg.solve(J, [x - qx[r, c], y - qy[r, c]])
    except np.linalg.LinAlgError:
        dc = dr = 0.0
    return float(c + 0.5 + np.clip(dc, -1, 1)), float(r + 0.5 + np.clip(dr, -1, 1))


def picture_to_prepared(x: float, y: float, size: list[int] | tuple[int, int], n: int) -> tuple[float, float]:
    """A mark's pixel (in the cut-out, `size` = [w, h]) in the square picture srcproject.prepare_picture makes of it at n
    pixels (padded to a square of the longer side, centred, then scaled). An upscaled copy of the same picture (the
    clean-up's up_<view>.png) has the same shape, so the same n gives the same place."""
    w, h = (int(size[0]), int(size[1]))
    s = max(w, h)
    k = n / s
    return (x + (s - w) // 2) * k, (y + (s - h) // 2) * k
