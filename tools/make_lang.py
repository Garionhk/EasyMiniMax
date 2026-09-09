"""Keep the translation files in step with the code.

    python tools/make_lang.py            report what is missing
    python tools/make_lang.py --write    add new strings to every catalogue

Finds every t("...") call in the source, then merges those strings into
lang/<code>.json. Existing translations are never touched; new strings arrive
with an empty value, which is the translator's to-do list. Strings no longer
in the code are moved to a "_retired" section rather than deleted, so a
rewording does not throw away work that a small edit could recover.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# This tool prints language names in their own script; the default Windows
# console code page cannot encode them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from app.i18n import LANGUAGES, LANG_DIR  # noqa: E402

#: Where translatable text lives. Tools and tests are deliberately excluded.
SOURCES = ["EasyMiniMax.py", "EasyMiniMaxSetup.py", "app", "setup"]


def strings_in(path: Path) -> list[str]:
    """Every literal passed as the first argument to t()."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        # t() and N() carry the text first; plural() carries the singular and
        # plural sentences in its second and third arguments, and both need
        # translating - missing them would leave every counted line in English.
        if name in ("t", "N"):
            wanted = node.args[:1]
        elif name == "plural":
            wanted = node.args[1:3]
        else:
            continue

        for argument in wanted:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                found.append(argument.value)
            elif isinstance(argument, ast.JoinedStr):
                print(f"  !! {path.name}: {name}() called on an f-string - the "
                      f"text must be a plain literal or it cannot be extracted")
    return found


def collect() -> list[str]:
    seen = {}
    for entry in SOURCES:
        target = ROOT / entry
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in files:
            if "__pycache__" in path.parts:
                continue
            for text in strings_in(path):
                seen.setdefault(text, None)
    return list(seen)


def main() -> int:
    write = "--write" in sys.argv
    found = collect()
    print(f"{len(found)} translatable strings in the source\n")

    for code, name in LANGUAGES.items():
        if code == "en":
            continue
        path = LANG_DIR / f"{code}.json"
        existing = {}
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))

        retired = existing.pop("_retired", {})
        notes = {k: v for k, v in existing.items() if k.startswith("_")}
        current = {k: v for k, v in existing.items() if not k.startswith("_")}

        merged, untranslated = {}, []
        for text in found:
            value = current.get(text) or retired.get(text) or ""
            merged[text] = value
            if not value:
                untranslated.append(text)

        gone = {k: v for k, v in current.items() if k not in merged and v}
        gone.update({k: v for k, v in retired.items() if k not in merged and v})

        done = len(found) - len(untranslated)
        pct = (done / len(found) * 100) if found else 100
        print(f"{name} ({code}): {done}/{len(found)} translated ({pct:.0f}%)"
              + (f", {len(gone)} retired" if gone else ""))
        for text in untranslated[:8]:
            print(f"    missing: {text[:72]}")
        if len(untranslated) > 8:
            print(f"    ... and {len(untranslated) - 8} more")

        if write:
            out = dict(notes)
            out.update(merged)
            if gone:
                out["_retired"] = gone
            LANG_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                            encoding="utf-8")
            print(f"    wrote {path.relative_to(ROOT)}")

    if not write:
        print("\n(no files changed - pass --write to update the catalogues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
