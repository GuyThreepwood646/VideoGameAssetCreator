"""Scenery pictures: lettering is asked for only when the description puts words in quotes."""
from vgac import reference
from vgac.stages.concept import pose_block_for

ENV = reference.category("environment")


def test_plain_description_keeps_the_no_text_block():
    block = pose_block_for(ENV, "a small corner shop with a neon sign and an awning")
    assert block == ENV["pose_block"] and "No text, no logos" in block


def test_quoted_words_use_the_lettered_block():
    block = pose_block_for(ENV, 'a small corner shop with a neon sign reading "CORNER SHOP" and an awning')
    assert block == ENV["pose_block_lettered"]
    assert "exactly the words written in quotes" in block and "No text, no logos" not in block
    assert pose_block_for(ENV, "a shop with a sign reading \u201cBAKERY\u201d") == ENV["pose_block_lettered"]


def test_quotes_without_letters_do_not_count():
    assert pose_block_for(ENV, 'a gate with a "" plate') == ENV["pose_block"]


def test_category_without_a_lettered_block_is_unchanged():
    humanoid = reference.category("humanoid")
    assert pose_block_for(humanoid, 'a knight with "KING" on the shield') == humanoid["pose_block"]
