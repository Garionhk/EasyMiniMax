"""The prompt templates, and tidying up what comes back.

A vision model does not reliably do as it is told. The system prompt says "one
sentence, no labels, no preamble" and a 7B model still opens with "Sure, here is
a description:" perhaps one time in five. Every one of those wrappers would
otherwise be typed verbatim into the prompt that reaches H3, so cleaning them off
is cheaper than fighting the model over them.
"""
from __future__ import annotations

import pytest

from app.llm import prompts


# -- the templates ---------------------------------------------------------

def test_describing_a_person_asks_about_a_person():
    _system, prompt = prompts.describe_picture("person")
    assert "person" in prompt
    assert "hair colour" in prompt


def test_describing_a_place_does_not_ask_about_hair():
    """Asking for hair and clothing when the picture is a coffee shop produces
    exactly the answer you would expect."""
    _system, prompt = prompts.describe_picture("environment")
    assert "place" in prompt
    assert "hair" not in prompt
    assert "architecture" in prompt


def test_an_unknown_kind_falls_back_to_person():
    _system, prompt = prompts.describe_picture("spaceship")
    assert "person" in prompt


def test_describing_asks_for_one_sentence_and_nothing_else():
    system, prompt = prompts.describe_picture("animal")
    assert "one concise sentence" in prompt
    assert "no labels" in system.lower()


def test_expanding_carries_the_idea_and_the_length():
    _system, prompt = prompts.expand_idea("a fox in the snow", 5.17)
    assert "a fox in the snow" in prompt
    assert "5.17 seconds" in prompt


def test_expanding_insists_on_the_audio_line():
    """H3 makes its own sound; a description with no Audio: line gets whatever
    the model guesses."""
    system, _prompt = prompts.expand_idea("a fox", 5.0)
    assert "Audio:" in system


def test_expanding_forbids_lists_and_headings():
    system, _prompt = prompts.expand_idea("a fox", 5.0)
    assert "Never a list" in system
    assert "Never a heading" in system


def test_the_word_budget_reaches_the_template():
    system, _prompt = prompts.expand_idea("a fox", 5.0, words=90)
    assert "About 90 words" in system


# -- tidying what comes back ----------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    # The clean case is left alone.
    ("A red fox stands in deep snow.", "A red fox stands in deep snow."),
    # The preamble small models add however firmly they are told not to.
    ("Sure, here is the description. A red fox stands in deep snow.",
     "A red fox stands in deep snow."),
    ("Certainly, here you go. A red fox stands in deep snow.",
     "A red fox stands in deep snow."),
    # The labels the system prompt just forbade.
    ("DESCRIPTION: A red fox stands in deep snow.",
     "A red fox stands in deep snow."),
    ("Answer: A red fox stands in deep snow.",
     "A red fox stands in deep snow."),
    # Quotes around the whole thing.
    ('"A red fox stands in deep snow."', "A red fox stands in deep snow."),
    # Whitespace.
    ("  A red fox stands in deep snow.  ", "A red fox stands in deep snow."),
])
def test_wrappers_are_stripped(raw, expected):
    assert prompts.tidy(raw) == expected


def test_a_fenced_block_is_unwrapped():
    raw = "```\nA red fox stands in deep snow.\n```"
    assert prompts.tidy(raw) == "A red fox stands in deep snow."


def test_a_language_tagged_fence_is_unwrapped():
    raw = "```text\nA red fox stands in deep snow.\n```"
    assert prompts.tidy(raw) == "A red fox stands in deep snow."


def test_a_multi_line_answer_keeps_its_lines():
    """The expanded description is a paragraph plus an Audio: line, and that
    line break has to survive."""
    raw = "A red fox crosses deep snow at dawn.\nAudio: snow crunching, light wind."
    assert prompts.tidy(raw) == raw


def test_the_audio_line_is_never_mistaken_for_a_label():
    raw = "A fox in snow.\nAudio: crunching snow."
    assert "Audio: crunching snow." in prompts.tidy(raw)


def test_an_apostrophe_is_not_treated_as_a_wrapping_quote():
    """'a low, calm woman's voice' must not lose its first and last character."""
    raw = "A woman's coat and a fox's tail"
    assert prompts.tidy(raw) == raw


def test_empty_input_stays_empty():
    assert prompts.tidy("") == ""
    assert prompts.tidy(None) == ""


@pytest.mark.parametrize("kind, expected", [
    ("person", "a person"),
    ("animal", "an animal"),
    ("object", "an object"),
    ("environment", "a place"),
    ("interface", "an interface"),
    ("expression", "a facial expression"),
])
def test_the_article_agrees_with_the_noun(kind, expected):
    """"a animal" reads as carelessness and is noise to the model."""
    _system, prompt = prompts.describe_picture(kind)
    assert f"image of {expected}." in prompt


@pytest.mark.parametrize("raw", [
    "The image shows a red fox with a bushy tail.",
    "This image shows a red fox with a bushy tail.",
    "The picture shows a red fox with a bushy tail.",
    "The image depicts a red fox with a bushy tail.",
    "The image features a red fox with a bushy tail.",
    "This is a picture of a red fox with a bushy tail.",
    "In this image, a red fox with a bushy tail.",
    "This appears to be a red fox with a bushy tail.",
    # All observed from qwen2.5vl:7b on real runs.
    "The reference image depicts a red fox with a bushy tail.",
    "This reference image features a red fox with a bushy tail.",
    "In the provided photo, a red fox with a bushy tail.",
    "This is a close-up image of a red fox with a bushy tail.",
    "The image displays a red fox with a bushy tail.",
])
def test_commentary_about_the_picture_is_removed(raw):
    """Observed from qwen2.5vl:7b on the very first real run, despite the
    system prompt forbidding it.

    It matters more than it looks: this text is inserted into the compiled
    prompt as what <Subject 1> *is*, so leaving it in tells H3 the subject is a
    picture of a fox rather than a fox.
    """
    assert prompts.tidy(raw) == "A red fox with a bushy tail."


def test_a_description_that_merely_mentions_an_image_is_left_alone():
    """Only a leading wrapper is stripped, never words from the middle."""
    raw = "A poster showing the image of a fox, pinned to a wall."
    assert prompts.tidy(raw) == raw


def test_the_article_survives_when_there_is_no_picture_phrase():
    """"This appears to be a red fox" must not become "Red fox".

    The article belongs to the subject, not to the wrapper, and is only eaten
    as part of "a picture of".
    """
    assert prompts.tidy("This appears to be a red fox.") == "A red fox."
    assert prompts.tidy("This is a red fox.") == "A red fox."


def test_a_picture_phrase_takes_its_own_article_with_it():
    assert prompts.tidy("This is an image of a red fox.") == "A red fox."
    assert prompts.tidy("This is a picture of a red fox.") == "A red fox."
