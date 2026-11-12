"""UI-08 guard: raw stage and platform keys must not be rendered as a label or as the only text.

The plain layer shows "Clean up for the game", never "prep", and "PC and console", never "pc_mid". Technical names are
allowed as secondary text (small and muted, after the plain label), so this scan only flags the shapes where a key
stands alone or is interpolated straight into a sentence. It reads the frontend source as text, so it needs no new package.

What it flags, per line of frontend/src (mock data, api.ts types and tests are skipped):
  1. JSX text that is only a key:                      <span>prep</span>
  2. A label-like property or attribute set to a key:  label: 'mesh'   title="pc_mid"   placeholder={'rig'}
  3. A stage or platform value interpolated into text: {asset.platform_target}   `${stage}` in a sentence
     (lines that build URLs, keys, class names or API calls, and lines that already go through stagePlain /
     platformPlain / plainJargon, are not text and are skipped.)
Add an entry to ALLOWED only with a reason a reviewer can check.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
KEYS = r"(?:concept|turnaround|mesh|prep|rig|export|mobile_low|pc_mid|high)"
SKIP_DIRS = {"mock"}
SKIP_FILES = {"api.ts"}

# (file name, substring of the line) -> reason. The substring must still match, so stale entries fail the test.
ALLOWED: dict[tuple[str, str], str] = {
    ("GlossaryPage.tsx", "${STAGE_TEXT[s].plain} (${s})"):
        "plain name first, technical key second in brackets: the decided rule for technical names",
    ("AssetPage.tsx", "(technical name: ${s})"):
        "tooltip that leads with the plain name and gives the key as the technical name",
}

JSX_ONLY_KEY = re.compile(r">\s*" + KEYS + r"\s*<")
LABEL_PROP = re.compile(r"""\b(?:label|title|text|placeholder|plain|heading|caption|aria-label|ariaLabel)\b\s*(?::|=)\s*\{?\s*(['"`])""" + KEYS + r"\1")
INTERP = re.compile(r"""(?:\{|\$\{)\s*(?:[\w$]+(?:\?)?\.)*(?:platform_target|platform|stage|stageId|apStage)\s*\}""")
ATTR_INTERP = re.compile(r"""\b(?:title|aria-label|placeholder|alt)=\{\s*(?:[\w$]+(?:\?)?\.)*(?:platform_target|platform|stage)\s*\}""")
NOT_TEXT = re.compile(r"""key=|to=|href=|navigate\(|\bapi\b|/api/|/assets/|/projects/|className|data-|\bid=|\bvalue=|\bname=|"""
                      r"""useState|set[A-Z]\w*\(|stage=\{|platform=\{|aria-current|console\.|Record<|\bcase\b|===|!==|\?\?|"""
                      r"""stagePlain\(|platformPlain\(|plainJargon\(|STAGE_TEXT\[|\.includes\(|\.indexOf\(|STAGES\[""")


_PROP = re.compile(r"(?<![\w-])(?!(?:title|aria-label|placeholder|alt|label)=)[\w-]+=\{[^{}]*\}")
_KEYLIKE = re.compile(r"`[^`]*\}\|[^`]*`|^\s*(?:const|let|return)\b.*\bid\s*:")


def _text_part(code: str) -> str:
    """The line without props handed to components (upTo={apStage}) and without cache-key strings (`${a}|${b}`)."""
    return "" if _KEYLIKE.search(code) else _PROP.sub("", code)


def _files():
    for p in sorted(SRC.rglob("*")):
        if p.suffix not in (".ts", ".tsx") or p.name in SKIP_FILES or p.name.endswith((".test.ts", ".test.tsx")):
            continue
        if SKIP_DIRS & set(p.relative_to(SRC).parts):
            continue
        yield p


def _findings() -> list[str]:
    out = []
    for p in _files():
        for no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.strip()
            if code.startswith(("//", "*", "/*", "import ")):
                continue
            hit = None
            if JSX_ONLY_KEY.search(code):
                hit = "a raw key is the only text"
            elif LABEL_PROP.search(code):
                hit = "a raw key is used as a label"
            elif ATTR_INTERP.search(code):
                hit = "a stage or platform value is printed in a tooltip or label"
            elif INTERP.search(_text_part(code)) and not NOT_TEXT.search(code):
                hit = "a stage or platform value is printed as text"
            if not hit:
                continue
            if any(name == p.name and frag in line for (name, frag) in ALLOWED):
                continue
            out.append(f"{p.relative_to(SRC.parent.parent).as_posix()}:{no}: {hit}: {code[:140]}")
    return out


def test_no_raw_stage_or_platform_key_is_rendered_as_text():
    found = _findings()
    assert not found, "Use stagePlain / platformPlain, with the key as small muted secondary text:\n" + "\n".join(found)


def test_allowed_entries_are_still_needed():
    text = {p.name: p.read_text(encoding="utf-8") for p in _files()}
    stale = [k for k in ALLOWED if k[1] not in text.get(k[0], "")]
    assert not stale, stale


def test_the_scan_catches_the_shapes_it_claims_to():
    assert JSX_ONLY_KEY.search("<span>prep</span>")
    assert LABEL_PROP.search("label: 'mesh'") and LABEL_PROP.search('title="pc_mid"')
    assert INTERP.search("<b>{asset.platform_target}</b>") and INTERP.search("`Step ${stage} failed`")
    assert not JSX_ONLY_KEY.search("<span>Prepare</span>")
