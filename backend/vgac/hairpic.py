"""Hair-friendly pictures (docs/HAIR-RESEARCH.md stage 1, option h).

The 3D builder turns whatever outline the picture shows into one solid shape: wisps become flakes, a see-through
fringe becomes a saw-tooth edge, hair lying on a collar fuses with it. The research guessed that a few large, separated
clumps would convert best; tested (runtime/tmp/hairpics), separated clumps gave saw-tooth lock ends and a smooth simple
shape converted better, so the painted look asks for one smooth shape with both eyes visible, Photoreal for tidy hair,
the plain looks for one simple shape, and cel and clay characters get no sentence (every wording changed them).

1. hair_sentence: the hair (or fur) sentence for a description, a style and a category. The wording lives in
   data/styles.yaml (each style's `hair`: clauses for `head` and `fur`, and `negative` terms); which kind of hair a
   category has lives in its category file (`hair: head | fur | fur_if_named`). The description wins: a clause lists
   the wishes (`drop`) that remove it and the wishes (`swap`) that reword it. A bald, helmeted or hooded character,
   a robot or a skeleton gets no sentence.
2. notes: the plain notes the asset form shows under the description (docs/HAIR-RESEARCH.md 5.4, reworded so they
   say what the app does today). frontend/src/hairNotes.ts is a copy of the word lists and the notes for the form;
   tests/test_hair_pictures.py checks they stay the same.
3. hair_over_eyes: the readiness check on the concept picture, "hair covers the eyes", from the cut-out alone.

Word matching works on the description in lower case with everything but letters turned into spaces, so "waist-length"
is "waist length". A phrase matches whole words only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

# --- what the description says --------------------------------------------------------------------------------------

# Words that say the character has visible head hair.
HAIR_WORDS = [
    "hair", "haired", "hairstyle", "haircut", "ponytail", "pony tail", "braid", "braids", "braided", "plait", "plaits",
    "plaited", "bun", "topknot", "top knot", "fringe", "bangs", "mohawk", "mohican", "mullet", "dreadlocks", "dreads",
    "locs", "cornrows", "afro", "curls", "ringlets", "pigtails", "bob", "crew cut", "buzz cut", "quiff", "pompadour",
    "undercut", "locks", "tresses",
]
# No head hair at all.
BALD_WORDS = ["bald", "shaved head", "shaven head", "hairless", "no hair", "bald headed", "clean shaven head"]
# Something on the head that hides the hair. "no helmet", "without a hood" and "hood down" do not count.
COVER_WORDS = ["helmet", "helm", "helmeted", "hood", "hooded", "cowl", "hijab", "headscarf", "head scarf", "turban",
               "balaclava", "great helm", "full helm"]
COVER_NEGATORS = ["no", "without", "not", "removed", "off", "bare", "carrying", "holding"]
COVER_AFTER = ["down", "lowered", "off", "under"]
# Humanoids with no hair of their own.
NO_HAIR_BODIES = ["robot", "android", "skeleton", "skeletal", "golem", "automaton", "mech", "droid", "mummy"]

# Wishes that change the block. Each is a list of phrases; "long_loose" is worked out by long_loose().
WISHES: dict[str, list[str]] = {
    "fringe": ["fringe", "bangs"],
    "locs": ["dreadlocks", "dreads", "locs", "box braids", "cornrows", "twists"],
    "wild": ["messy", "wild hair", "unkempt", "windswept", "wind swept", "tousled", "shaggy", "dishevelled", "disheveled",
             "bedhead", "frizzy", "wispy", "scruffy", "untamed"],
    "curly": ["curly", "curls", "afro", "coily", "ringlets", "kinky"],
    "styled": ["mohawk", "mohican", "mullet", "afro", "spiky", "spiked", "undercut", "pompadour", "quiff", "liberty spikes"],
}
LONG_WORDS = ["long", "flowing", "loose", "cascading", "untied", "waist length", "hip length", "shoulder length",
              "knee length", "free flowing", "lank"]
LONG_NOUNS = ["hair", "locks", "tresses", "mane"]
LONG_AFTER = ["down", "loose", "flowing", "falls", "falling", "cascading", "cascades", "reaching", "hanging", "spilling"]
SHORT_WORDS = ["short", "cropped", "close cropped", "chin length", "ear length"]
SHORT_STYLES = ["bob", "pixie", "pixie cut", "crew cut", "buzz cut", "bob cut"]
TIED_WORDS = ["ponytail", "pony tail", "braid", "braids", "braided", "plait", "plaits", "plaited", "bun", "tied",
              "topknot", "top knot", "updo", "pigtails", "tied back"]
OVER_EYE_VERBS = ["over", "cover", "covering", "covers", "covered", "hide", "hiding", "hides", "hidden", "across", "obscuring",
                  "obscures", "veiling", "in front of"]
OVER_EYE_TARGETS = ["eye", "eyes", "face"]
OVER_EYE_HAIR = ["hair", "fringe", "bangs", "lock", "locks", "strand", "strands", "tresses"]
PEEKABOO = ["peek a boo", "peekaboo"]
# Something other than hair over the eyes: the hair_off_eyes check cannot tell it from hair, so it does not run.
EYE_COVER_WORDS = ["eyepatch", "eye patch", "blindfold", "blindfolded", "mask", "masked", "visor", "glasses",
                   "spectacles", "goggles", "sunglasses", "monocle"]

# Creatures.
FUR_WORDS = ["fur", "furry", "furred", "fluffy", "mane", "shaggy", "woolly", "wooly", "hairy", "pelt", "fleece"]
NO_FUR_WORDS = ["scales", "scaly", "scaled", "reptile", "lizard", "crocodile", "alligator", "turtle", "tortoise",
                "dragon", "dinosaur", "frog", "toad", "salamander", "newt", "robot", "robotic", "mechanical", "metal",
                "stone", "skeleton", "skeletal", "undead bones", "hairless", "slime", "insect", "beetle", "armadillo",
                "rhino", "rhinoceros", "elephant", "hippo", "hippopotamus", "chitin", "golem", "crystal", "bony"]
LONG_FUR = ["long fur", "shaggy", "fluffy", "woolly", "wooly", "long haired", "long hair", "thick coat", "fleece"]


def norm(text: str | None) -> str:
    """Lower case, everything but letters turned into single spaces, padded with a space at each end."""
    return " " + " ".join(re.sub(r"[^a-z]+", " ", (text or "").lower()).split()) + " "


def has(t: str, phrase: str) -> bool:
    return f" {phrase} " in t


def any_of(t: str, phrases: list[str]) -> bool:
    return any(has(t, p) for p in phrases)


def _words(t: str) -> list[str]:
    return t.split()


def _find(words: list[str], phrase: str) -> list[int]:
    """Start indices of a phrase (one or more words) in a word list."""
    p = phrase.split()
    n = len(p)
    return [i for i in range(len(words) - n + 1) if words[i:i + n] == p]


def covered(t: str) -> bool:
    """Something on the head hides the hair: a cover word not negated in the three words before it ("no helmet",
    "without a hood", "holding a helmet") and not followed by "down", "lowered", "off" or "under"."""
    words = _words(t)
    for c in COVER_WORDS:
        for i in _find(words, c):
            before = words[max(0, i - 3): i]
            after = words[i + len(c.split()): i + len(c.split()) + 1]
            if any(w in COVER_NEGATORS for w in before) or any(w in COVER_AFTER for w in after):
                continue
            return True
    return False


def long_loose(t: str) -> bool:
    """The description asks for long loose hair: a length word up to three words before a hair noun ("long red hair",
    "shoulder-length hair"), or a hair noun followed within three words by a falling word ("hair down", "hair falling
    to her waist"), and nothing that ties it (ponytail, braid, bun, tied)."""
    if any_of(t, TIED_WORDS):
        return False
    words = _words(t)
    for noun in LONG_NOUNS:
        for i in _find(words, noun):
            before = " " + " ".join(words[max(0, i - 3): i]) + " "
            if any(has(before, w) for w in LONG_WORDS):
                return True
            after = words[i + 1: i + 4]
            if any(w in LONG_AFTER for w in after):
                return True
    return False


def short_hair(t: str) -> bool:
    """The description asks for short hair: a short word up to three words before a hair noun ("short red hair",
    "cropped hair") or a short cut by name (a bob, a pixie cut, a crew cut)."""
    if any_of(t, SHORT_STYLES):
        return True
    words = _words(t)
    for noun in ("hair", "haircut"):
        for i in _find(words, noun):
            before = " " + " ".join(words[max(0, i - 3): i]) + " "
            if any(has(before, w) for w in SHORT_WORDS):
                return True
    return False


def over_eye(t: str) -> bool:
    """The description asks for hair over an eye or the face: a covering word followed within three words by eye,
    eyes or face, with a hair word in the three words before it ("a fringe falling over one eye", "hair covering
    her face"); or "peek-a-boo". A scar or an eyepatch over an eye does not count."""
    if any_of(t, PEEKABOO):
        return True
    words = _words(t)
    for v in OVER_EYE_VERBS:
        for i in _find(words, v):
            j = i + len(v.split())
            if not any(w in OVER_EYE_TARGETS for w in words[j: j + 3]):
                continue
            if any(w in OVER_EYE_HAIR for w in words[max(0, i - 3): i]):
                return True
    return False


def wishes(text: str | None) -> set[str]:
    """The hairstyle wishes in a description: long_loose, short, fringe, over_eye, locs, wild, curly, styled; for fur
    long_fur."""
    t = norm(text)
    out = {k for k, phrases in WISHES.items() if any_of(t, phrases)}
    if long_loose(t):
        out.add("long_loose")
    if over_eye(t):
        out.add("over_eye")
    if short_hair(t):
        out.add("short")
    if any_of(t, LONG_FUR):
        out.add("long_fur")
    return out


def hair_kind(text: str | None, category: dict[str, Any] | None) -> str | None:
    """'head' (a character with head hair), 'fur' (a creature with fur) or None (no hair sentence) for a description
    and a category (its `hair` key: head, fur or fur_if_named)."""
    kind = (category or {}).get("hair")
    t = norm(text)
    if kind == "head":
        if any_of(t, BALD_WORDS):
            return None
        hair = any_of(t, HAIR_WORDS)
        if not hair and (covered(t) or any_of(t, NO_HAIR_BODIES)):
            return None
        return "head"
    if kind in ("fur", "fur_if_named"):
        if any_of(t, NO_FUR_WORDS) and not any_of(t, FUR_WORDS):
            return None
        if kind == "fur_if_named" and not any_of(t, FUR_WORDS):
            return None
        return "fur"
    return None


def _clauses(items: list[Any] | None, wanted: set[str]) -> list[str]:
    out = []
    for c in items or []:
        if isinstance(c, str):
            out.append(c)
            continue
        if not isinstance(c, dict) or not c.get("text"):
            continue
        if wanted & set(c.get("drop") or []):
            continue
        text = c["text"]
        for wish, alt in (c.get("swap") or {}).items():
            if wish in wanted:
                text = alt
                break
        out.append(" ".join(str(text).split()))
    return out


def _sentence(parts: list[str]) -> str:
    if not parts:
        return ""
    s = ", ".join(parts)
    s = s[0].upper() + s[1:]
    return s if s.endswith(".") else s + "."


def block(style: dict[str, Any] | None) -> dict[str, Any]:
    return (style or {}).get("hair") or {}


def hair_sentence(text: str | None, style: dict[str, Any] | None, category: dict[str, Any] | None) -> str:
    """The hair or fur sentence for the concept prompt, or "" when the asset needs none."""
    kind = hair_kind(text, category)
    if not kind:
        return ""
    return _sentence(_clauses(block(style).get(kind), wishes(text)))


def hair_negative(text: str | None, style: dict[str, Any] | None, category: dict[str, Any] | None) -> str:
    """The matching negative terms (for painters that take a negative prompt), or ""."""
    kind = hair_kind(text, category)
    if not kind:
        return ""
    neg = block(style).get("negative") or {}
    return ", ".join(_clauses(neg.get(kind) if isinstance(neg, dict) else None, wishes(text)))


def parts_record(text: str | None, style: dict[str, Any] | None, category: dict[str, Any] | None) -> dict[str, Any]:
    """What the prompt got and why, for the version's metrics: {kind, wishes, sentence, negative}."""
    return {"kind": hair_kind(text, category), "wishes": sorted(wishes(text)),
            "sentence": hair_sentence(text, style, category), "negative": hair_negative(text, style, category)}


# --- notes for the person -------------------------------------------------------------------------------------------

NOTES = {
    "photoreal_hair": ("Hair is built as one solid shape with its colour and strands painted on from the picture. It looks "
                       "right from a few steps away and simpler up close. Short, tied-back or braided hair comes out "
                       "best; long loose hair and wispy fringes come out worst."),
    "long_loose": ("Long loose hair is joined to the shoulders in the 3D model and moves with them; it does not swing on "
                   "its own. Hair that is tied back, braided or cut short keeps a cleaner shape."),
    "fur": ("Fur is painted onto the surface of the model, not made of separate hairs. It reads as fur at game distance "
            "and looks smoother up close."),
}


def notes(text: str | None, style_id: str | None, category: dict[str, Any] | None) -> list[dict[str, str]]:
    """The notes for the asset form: [{id, text}]. photoreal_hair when the look is Photoreal and the description names
    hair; long_loose when it asks for long loose hair; fur for a creature with fur."""
    t = norm(text)
    kind = hair_kind(text, category)
    out = []
    if kind == "head" and style_id == "realistic_pbr" and any_of(t, HAIR_WORDS):
        out.append("photoreal_hair")
    if kind == "head" and long_loose(t):
        out.append("long_loose")
    if kind == "fur":
        out.append("fur")
    return [{"id": i, "text": NOTES[i]} for i in out]


# --- the check: hair over the eyes ----------------------------------------------------------------------------------

FIX_INSTRUCTION = ("Move the hair off the face: sweep the fringe and any locks away from the forehead and both eyes, to "
                   "the sides or behind the ears, so both eyes and eyebrows are fully visible. Keep everything else "
                   "exactly the same: the same person, face, hair colour, hair length, clothes, colours, pose, framing "
                   "and the plain light grey background.")
COVER_LIMIT = 0.75  # hair against hair plus skin in one eye's area (on 60 test pictures: hidden eyes 0.78 to 1.0, visible eyes 0.61 at most)
_K = np.array([0.5, 1.0, 1.0])


def _dist(lab: np.ndarray, ref: np.ndarray) -> np.ndarray:
    return np.linalg.norm((lab - ref) * _K, axis=-1)


def eye_cover(cutout: Any, size: int = 256) -> dict[str, Any] | None:
    """How much of each eye's area hair hides on a standing character's cut-out (RGBA path, PIL image or (H, W, 4)
    floats). The head is cut from the top of the figure and enlarged to `size`; the hair colour is sampled at the
    crown, the skin colour is the most common skin-like colour (warm, not deep red) in the middle of the head that differs from it, and
    each pixel is hair or skin by Lab distance (lightness counted half, face.hair_and_skin's rule). Hair is only the
    hair joined to the crown. The eye area of each side is 58 to 80 percent of the way from the crown to the jaw (where
    the face narrows below three quarters of its widest), between the face's middle and its edge.
    Returns {sides: {left, right: {hair, skin, cover}}, cover, covered: [sides]} (left and right as seen in the
    picture), or None when it cannot tell: no figure, no skin colour, or hair and skin too alike."""
    from PIL import Image

    from . import face

    if isinstance(cutout, np.ndarray):
        im = cutout.astype(np.float32)
    else:
        img = cutout if isinstance(cutout, Image.Image) else Image.open(cutout)
        im = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0
    a = im[..., 3] > 0.5
    rows = np.where(a.any(axis=1))[0]
    if len(rows) < 50:
        return None
    top, bot = int(rows[0]), int(rows[-1])
    hb = 0.16 * (bot - top)
    ys, xs = np.where(a[top: top + max(2, int(0.6 * hb))])
    if len(xs) < 20:
        return None
    cx = 0.5 * (np.percentile(xs, 5) + np.percentile(xs, 95))
    x0, x1 = cx - 0.75 * hb, cx + 0.75 * hb
    y0, y1 = top - 0.05 * hb, top + 1.45 * hb
    S = size
    X, Y = np.meshgrid(x0 + (np.arange(S) + 0.5) / S * (x1 - x0), y0 + (np.arange(S) + 0.5) / S * (y1 - y0))
    crop = face.sample(im, X, Y)
    rgb, fg = crop[..., :3], crop[..., 3] > 0.5
    lab = face.to_lab(rgb)
    Yi, Xi = np.mgrid[0:S, 0:S]
    crown_row = int((top - y0) / (y1 - y0) * S)
    mid = S // 2
    crown = fg & (Yi < crown_row + S // 14) & (np.abs(Xi - mid) < S // 8)
    if crown.sum() < 30:
        return None
    hair_lab = np.median(lab[crown], axis=0)
    cand = (fg & (Yi > crown_row + S // 6) & (Yi < crown_row + int(0.7 * S)) & (np.abs(Xi - mid) < S // 6)
            & (_dist(lab, hair_lab) > 20) & (lab[..., 0] > 30) & (lab[..., 1] > 4) & (lab[..., 1] < 30)
            & (lab[..., 2] > 6))
    if cand.sum() < 40:
        return None
    step = np.array([8.0, 6.0, 6.0])
    q = np.round(lab[cand] / step).astype(int)
    keys, counts = np.unique(q, axis=0, return_counts=True)
    near = cand & (np.abs(np.round(lab / step) - keys[counts.argmax()]).max(axis=-1) <= 1)
    skin_lab = np.median(lab[near], axis=0)
    if float(np.linalg.norm((hair_lab - skin_lab) * _K)) < 15:
        return None
    dh, ds = _dist(lab, hair_lab), _dist(lab, skin_lab)
    hairlike = fg & (dh < ds - 6)
    skinlike = fg & (ds < dh - 6) & (ds < 22)
    hair = face.grow(crown & hairlike, hairlike)
    seed = skinlike & (np.abs(Xi - mid) < 4) & (Yi > crown_row + S // 5) & (Yi < crown_row + int(0.75 * S))
    if seed.sum() < 5:
        return None
    facem = face.fill_holes(face.grow(seed, skinlike))
    widths = facem.sum(axis=1)
    # Only the head's rows: bare shoulders joined to the face through the neck are wider than any face.
    widths[min(S, crown_row + int(0.65 * S)):] = 0
    rw = int(widths.argmax())
    W = int(widths[rw])
    if W < S // 12 or rw > crown_row + int(0.8 * S):
        return None
    jaw = rw
    while jaw + 1 < S and widths[jaw + 1] >= 0.75 * W:
        jaw += 1
    cols = np.where(facem[rw])[0]
    xl, xr = int(cols.min()), int(cols.max())
    fm = (xl + xr) // 2
    span = jaw - crown_row
    e0, e1 = int(crown_row + 0.58 * span), int(crown_row + 0.80 * span)
    if e1 - e0 < 3:
        return None
    sides: dict[str, dict[str, float]] = {}
    for name, (c0, c1) in {"left": (xl + int(0.08 * W), fm - int(0.06 * W)), "right": (fm + int(0.06 * W), xr - int(0.08 * W))}.items():
        reg = np.zeros((S, S), dtype=bool)
        reg[e0:e1, max(0, c0):max(0, c1)] = True
        reg &= fg
        n = max(1, int(reg.sum()))
        h = float((hair & reg).sum()) / n
        s = float((skinlike & reg).sum()) / n
        sides[name] = {"hair": round(h, 3), "skin": round(s, 3), "cover": round(h / max(1e-6, h + s), 3)}
    covered_sides = [k for k, v in sides.items() if v["cover"] >= COVER_LIMIT]
    return {"sides": sides, "cover": max(v["cover"] for v in sides.values()), "covered": covered_sides}


def eyes_check(cutout: Any, description: str | None, category: dict[str, Any] | None,
               style: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The readiness check `hair_off_eyes` for a concept cut-out, or None when it does not apply: not a character
    with head hair (a bald, helmeted or hooded one, a creature, a prop), a description that asks for hair over the
    eyes (the description wins) or names something else over the eyes (an eyepatch, a blindfold, a mask, a visor,
    glasses), a look it was not tested on reliably (`hair_eye_check: false` on the style), or a
    picture where hair and skin cannot be told apart."""
    from . import quality

    if hair_kind(description, category) != "head" or "over_eye" in wishes(description):
        return None
    if any_of(norm(description), EYE_COVER_WORDS):
        return None
    if (style or {}).get("hair_eye_check") is False:
        return None
    try:
        r = eye_cover(cutout)
    except (OSError, ValueError):
        return None
    if r is None:
        return None
    cov = r["covered"]
    side_text = "both eyes" if len(cov) == 2 else (f"the eye on the {cov[0]} of the picture" if cov else "")
    value = round(max((r["sides"][s]["cover"] for s in cov), default=min(r["cover"], COVER_LIMIT - 0.01)), 2)
    return quality.check("hair_off_eyes", not cov, value, COVER_LIMIT,
                         vars={"redraw": FIX_INSTRUCTION, "side_text": side_text, "sides": r["sides"]})
