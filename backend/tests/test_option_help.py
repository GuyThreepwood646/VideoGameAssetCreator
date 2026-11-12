"""Every Fine-tune option shows a real hint (docs/UX-OPTIONS-REVIEW.md).

The hint under a setting is its PARAM_HELP meaning; an option missing from PARAM_HELP showed its own name ("pose ready")
instead of the words written for it. The hand and pose options carry warnings the person has to see without hovering.
"""

from __future__ import annotations

from vgac import guidance, reference

# Options that still have no help entry; new options must not join this list.
KNOWN_WITHOUT_HELP: set[str] = set()

REVIEWED = ("views", "pose_ready", "neutral_mouth", "hand_edit", "source_project", "head_triangles", "hand_triangles",
            "face_closeup_guided", "face_sides")


def _schema() -> dict[str, dict]:
    return {p["name"]: p for s in reference.stages() for p in s["adjust_schema"]}


def test_every_option_has_help_except_the_known_few():
    names = {p["name"] for s in reference.STAGE_ORDER for p in reference._schema(s)}
    missing = sorted(n for n in names if n not in guidance.PARAM_HELP)
    assert set(missing) <= KNOWN_WITHOUT_HELP, missing


def test_the_reviewed_options_show_their_own_words():
    schema = _schema()
    for name in REVIEWED:
        meaning = schema[name]["help"]["meaning"]
        assert meaning and meaning != name.replace("_", " "), name
        assert len(meaning) <= 400, (name, len(meaning))


def test_pose_for_3d_warns_on_the_hint_itself():
    meaning = guidance.PARAM_HELP["pose_ready"]["help"]["meaning"].lower()
    assert "arms, claws or tools" in meaning and "removes them" in meaning
    assert "black-and-white" in meaning and "gloves" in meaning


def test_the_pose_options_are_in_the_first_layer_and_the_rest_in_advanced():
    simple = {n: guidance.PARAM_HELP[n]["simple"] for n in ("pose_ready", "neutral_mouth", "hand_edit", "source_project",
                                                             "head_triangles", "hand_triangles")}
    assert simple == {"pose_ready": True, "neutral_mouth": True, "hand_edit": False, "source_project": False,
                      "head_triangles": False, "hand_triangles": False}


def test_the_pose_options_stay_off_unless_chosen():
    schema = _schema()
    for name in ("pose_ready", "neutral_mouth", "hand_edit"):
        assert schema[name]["default"] is None and schema[name]["categories"] == ["humanoid"], name


def test_the_triangle_pair_reads_as_a_pair():
    schema = _schema()
    head, hands = schema["head_triangles"]["label"], schema["hand_triangles"]["label"]
    assert head.replace("head", "hands") == hands
