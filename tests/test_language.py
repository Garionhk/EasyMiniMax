"""Traditional Chinese, and the ways a translation quietly breaks.

A catalogue is not code, so nothing fails when it goes wrong - the interface
just shows English, or crashes at the moment someone opens a particular dialog.
Three faults are worth guarding against:

* a string added to the source and never translated (it silently stays English);
* a translation that drops a {placeholder} (KeyError, at run time, only on that
  one message);
* a catalogue saved by an editor that adds a byte-order mark (the whole
  language disappears).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import i18n

LANG_DIR = Path(__file__).resolve().parent.parent / "lang"
CATALOGUES = sorted(LANG_DIR.glob("*.json"))


@pytest.fixture(autouse=True)
def _english_afterwards():
    """Every test leaves the process in English, whatever it did."""
    yield
    i18n.load("en")


def _entries(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# -- the catalogue is there and is offered --------------------------------

def test_traditional_chinese_ships():
    assert (LANG_DIR / "zh-Hant.json").is_file()


def test_it_is_offered_in_the_language_list():
    available = i18n.available()
    assert "zh-Hant" in available
    # Named in its own script, so somebody who has landed in the wrong language
    # can still find their way out.
    assert available["zh-Hant"] == "繁體中文"


def test_english_is_always_there():
    assert i18n.available()["en"] == "English"


# -- completeness ----------------------------------------------------------

@pytest.mark.parametrize("path", CATALOGUES, ids=lambda p: p.stem)
def test_every_string_is_translated(path):
    """An untranslated entry shows English with no warning anywhere."""
    blank = [k for k, v in _entries(path).items() if not str(v).strip()]
    assert not blank, (
        f"{path.stem}: {len(blank)} untranslated, first: {blank[:3]}")


@pytest.mark.parametrize("path", CATALOGUES, ids=lambda p: p.stem)
def test_nothing_is_left_in_english(path):
    """Not a hard rule - some entries are names, sizes or shortcuts that should
    not change - so this only checks that most of it actually got translated."""
    entries = _entries(path)
    same = [k for k, v in entries.items() if k == v]
    assert len(same) < len(entries) * 0.2, (
        f"{path.stem}: {len(same)} of {len(entries)} entries are still the "
        f"English text")


def test_the_catalogue_covers_what_the_program_asks_for():
    """Walks the source the way tools/make_lang.py does, so a string added to
    the code without being translated is caught here rather than by a user."""
    import ast

    project = LANG_DIR.parent
    wanted: set[str] = set()
    for folder in ("app", "setup"):
        for source in (project / folder).rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or \
                    getattr(node.func, "attr", None)
                if name not in ("t", "N"):
                    continue
                if node.args and isinstance(node.args[0], ast.Constant) and \
                        isinstance(node.args[0].value, str):
                    wanted.add(node.args[0].value)

    have = set(_entries(LANG_DIR / "zh-Hant.json"))
    missing = sorted(wanted - have)
    assert not missing, (
        f"{len(missing)} strings in the source are not in zh-Hant.json. "
        f"Run: python tools/make_lang.py --write\nFirst few: {missing[:3]}")


# -- placeholders ----------------------------------------------------------

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.mark.parametrize("path", CATALOGUES, ids=lambda p: p.stem)
def test_every_placeholder_survives_translation(path):
    """A dropped {name} is a KeyError at run time, on that message only - so it
    shows up when a user hits one particular error, and not before."""
    problems = []
    for english, translated in _entries(path).items():
        if set(PLACEHOLDER.findall(english)) != set(PLACEHOLDER.findall(translated)):
            problems.append(english[:60])
    assert not problems, f"{path.stem}: {problems}"


def test_a_translated_message_still_formats():
    i18n.load("zh-Hant")
    text = i18n.t("Engine running · {free} of {total} GB free",
                  free="7.2", total="16")
    assert "7.2" in text and "16" in text
    assert "{" not in text


def test_a_translated_message_with_a_path_still_formats():
    i18n.load("zh-Hant")
    text = i18n.t("The picture for person {n} is no longer there:\n{path}",
                  n=2, path=r"D:\gone.png")
    assert "2" in text and r"D:\gone.png" in text


# -- the file itself -------------------------------------------------------

@pytest.mark.parametrize("path", CATALOGUES, ids=lambda p: p.stem)
def test_the_catalogue_is_valid_json_however_it_was_saved(path):
    """utf-8-sig, because Notepad adds a byte-order mark and json.loads rejects
    it - and a rejected catalogue means the whole language silently vanishes."""
    assert _entries(path)
    with_bom = b"\xef\xbb\xbf" + path.read_bytes().lstrip(b"\xef\xbb\xbf")
    assert json.loads(with_bom.decode("utf-8-sig"))


def test_switching_language_actually_changes_the_words():
    i18n.load("en")
    english = i18n.t("Create")
    i18n.load("zh-Hant")
    chinese = i18n.t("Create")
    assert english != chinese
    assert chinese == "開始製作"


def test_switching_back_returns_to_english():
    i18n.load("zh-Hant")
    i18n.load("en")
    assert i18n.t("Create") == "Create"


def test_an_unknown_language_falls_back_to_english():
    assert i18n.load("kw-Latn") == "en"
    assert i18n.t("Create") == "Create"


# -- what should not be translated ----------------------------------------

@pytest.mark.parametrize("term", [
    "ComfyUI", "Ollama", "EasyAI", "EasyMiniMax", "LoRA", "Turbo",
])
def test_names_are_left_alone(term):
    """Product and file names have to survive: they appear in paths, commands
    and folder names that a translated word would not match."""
    entries = _entries(LANG_DIR / "zh-Hant.json")
    carrying = {k: v for k, v in entries.items() if term in k}
    dropped = [k[:50] for k, v in carrying.items() if term not in v]
    assert not dropped, f"{term} was translated away in: {dropped[:3]}"


# -- the two ways a string escapes translation entirely -------------------

def test_no_module_level_t_call():
    """t() at import time freezes the language before one is chosen.

    app/ui/subjects.py built its dropdown labels this way, so every kind of
    subject read "A person", "An animal" and so on in every language. Mark with
    N() at module level and translate with t() where it is shown.
    """
    import ast

    project = LANG_DIR.parent
    offenders = []
    for folder in ("app", "setup"):
        for source in (project / folder).rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    continue                      # runs when called, not now
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and \
                            getattr(sub.func, "id", "") == "t":
                        offenders.append(
                            f"{source.relative_to(project)}:{sub.lineno}")
    assert not offenders, (
        "t() runs at import, before a language is loaded - use N() here and "
        f"t() where it is shown: {offenders}")


def test_the_summary_line_is_translated():
    """It was assembled with f-strings and stayed English in every language -
    which only showed up in a screenshot."""
    from app.h3.timeline import Shot, Storyboard, describe

    i18n.load("zh-Hant")
    board = Storyboard(shots=[Shot("a", 60), Shot("b", 64)], frames=124)
    summary = describe(board)
    for english in ("shot", "frames", " s "):
        assert english not in summary, f"{english!r} left untranslated in {summary!r}"
    assert "鏡頭" in summary and "格" in summary


def test_the_subject_kinds_are_translated():
    from app.ui.subjects import KIND_LABELS

    i18n.load("zh-Hant")
    for english in KIND_LABELS.values():
        assert i18n.t(english) != english, f"{english!r} is not translated"
