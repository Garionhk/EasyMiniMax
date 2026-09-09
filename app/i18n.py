"""Translating the interface, without turning the source into puzzle pieces.

Strings are looked up by their English text rather than by an invented key:

    label.setText(t("Generate"))

That choice costs a little at lookup time and buys a lot everywhere else. The
source stays readable, a missing translation falls back to perfectly good
English instead of showing "ui.button.generate" to a user, and a translator
can work from a file where both halves are sentences.

Catalogues live in lang/<code>.json as a flat {english: translated} map, so
adding a language means adding one file - no code change, and no build step of
the kind Qt's own .ts/.qm pipeline would need.

Both EasyAI and EasyAI Setup import this. It holds no Qt dependency and no
reference to either program's settings, so neither owns it.
"""
from __future__ import annotations

import json
import locale
import os
from pathlib import Path

from app.paths import resource_dir

#: Catalogues ship with the program, so they come from the bundle.
LANG_DIR = resource_dir() / "lang"

#: Code -> the name of the language written in that language, because someone
#: who has landed in the wrong one must still be able to find their way out.
LANGUAGES = {
    "en": "English",
    "zh-Hant": "繁體中文",
    "zh-Hans": "简体中文",
}

DEFAULT = "en"

_current = DEFAULT
_catalogue: dict[str, str] = {}
_missing: set[str] = set()


def available() -> dict[str, str]:
    """Languages that can actually be selected: English plus any file present."""
    found = {"en": LANGUAGES["en"]}
    for code, name in LANGUAGES.items():
        if code != "en" and (LANG_DIR / f"{code}.json").is_file():
            found[code] = name
    return found


def detect() -> str:
    """Guess from Windows, so a Chinese machine opens in Chinese unprompted.

    Script matters more than country here: zh-TW, zh-HK and zh-MO are
    Traditional, zh-CN and zh-SG are Simplified, and a bare "zh" is Simplified
    by weight of numbers.
    """
    raw = ""
    try:
        raw = locale.getdefaultlocale()[0] or ""
    except (ValueError, TypeError):
        pass
    raw = (raw or os.environ.get("LANG", "")).replace("_", "-").lower()

    if raw.startswith("zh"):
        if "hant" in raw or any(r in raw for r in ("-tw", "-hk", "-mo")):
            return "zh-Hant"
        return "zh-Hans"
    code = raw.split("-")[0]
    return code if code in LANGUAGES else DEFAULT


def load(code: str) -> str:
    """Switch language. Returns the code actually in use."""
    global _current, _catalogue
    if code not in LANGUAGES:
        code = DEFAULT
    _catalogue = {}
    if code != DEFAULT:
        path = LANG_DIR / f"{code}.json"
        try:
            # utf-8-sig, not utf-8: Windows Notepad saves JSON with a byte
            # order mark, and json.loads rejects it. A translator editing a
            # catalogue in the most obvious editor would otherwise find their
            # whole language silently ignored.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            # Entries starting with "_" are notes to translators, not strings.
            _catalogue = {k: v for k, v in raw.items()
                          if not k.startswith("_") and isinstance(v, str) and v}
        except (OSError, json.JSONDecodeError):
            # A damaged catalogue must not stop the program opening; English
            # is always a working fallback.
            code = DEFAULT
    _current = code
    return code


def current() -> str:
    return _current


def t(text: str, **fields) -> str:
    """Translate ``text``, then fill in any {placeholders}.

    Formatting happens after lookup so translators can move a placeholder to
    wherever the sentence needs it - word order differs, and a translation
    that cannot reorder is a translation that reads badly.
    """
    out = _catalogue.get(text, text)
    if out is text and _current != DEFAULT:
        _missing.add(text)
    if fields:
        try:
            out = out.format(**fields)
        except (KeyError, IndexError, ValueError):
            # A translator typo in a placeholder should degrade to the English
            # sentence, never crash the window that was about to show it.
            try:
                out = text.format(**fields)
            except (KeyError, IndexError, ValueError):
                return text
    return out


def _preference_file() -> Path:
    """Where the chosen language is remembered.

    Deliberately its own small file rather than a key in EasyAI's settings:
    EasyAI Setup must not write to anything EasyAI owns, and the language is
    a preference about the person, not about either program.
    """
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
        or str(Path.home())
    return Path(base) / "EasyMiniMax" / "language.json"


def remember(code: str) -> None:
    path = _preference_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"language": code}), encoding="utf-8")
    except OSError:
        pass            # a read-only profile is not a reason to fail


def remembered() -> str | None:
    try:
        text = _preference_file().read_text(encoding="utf-8-sig")
        code = json.loads(text)["language"]
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    return code if code in LANGUAGES else None


def start() -> str:
    """Choose the language for this run: the saved one, else what Windows says."""
    return load(remembered() or detect())


def plural(count: int, one: str, many: str, **fields) -> str:
    """Pick the singular or plural sentence, then translate the whole thing.

    English needs two forms; Chinese needs one and will simply map both to the
    same translation. Choosing the English sentence first and translating it
    whole - rather than translating "model" and gluing on an "s" - is what
    lets a language do something completely different with number and word
    order.
    """
    return t(one if count == 1 else many, n=count, **fields)


def N(text: str) -> str:
    """Mark text for translation without translating it yet.

    Table data - a mode's label, a ratio's name - is defined once at import
    and shown much later, possibly after the language has changed. Calling t()
    at definition time would freeze the wrong language into the table, so it
    is marked here and translated at the point it is displayed.

    The extractor scans for this exactly as it scans for t().
    """
    return text


def missing() -> set[str]:
    """Strings asked for but not in the current catalogue - used by the tests."""
    return set(_missing)


load(DEFAULT)
