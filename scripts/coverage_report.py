# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""How much of each language a phrase pack can actually build a sentence for.

The READMEs quote coverage percentages. This is where they come from, so
nobody has to take them on faith or re-derive the denominator by hand.

    python scripts/coverage_report.py              # the table the README uses
    python scripts/coverage_report.py de --missing # what is still uncovered

Two denominators, because the difference is large enough to mislead:

  all names        every item.*/block.minecraft.* name in the lang file
  reachable items  the same, minus block.minecraft.banner.*, which are
                   tooltip descriptions of banner patterns rather than
                   items a player can hold. There are 688 of them and
                   counting them understates every language by ~18 points.

A language with no `known` hook covers everything by construction: its
frames were written to need no gender, case or counter, so there is nothing
it can get wrong. That is reported as "n/a (no gender needed)" rather than
as 100%, which would invite comparison with the languages that do.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.lang import DictionaryRegistry  # noqa: E402
from app.phrases import (  # noqa: E402
    MAX_LEVEL, _PHRASE_PACKS, head_noun, sentence_for,
)

LANGS = {"es": "es_es", "ja": "ja_jp", "ru": "ru_ru", "de": "de_de", "fr": "fr_fr"}
BANNER_PREFIX = "block.minecraft.banner."


def item_names(lang_file: str) -> tuple[list[str], list[str]]:
    """(all names, reachable names) -- see the module docstring.

    Read through `DictionaryRegistry.table`, which is the same path the
    backend serves challenges from, so this counts what the mod can actually
    ask for. That filter already drops empty values and anything holding a
    `%s` placeholder -- those are sentence templates, not nouns, and there
    are 89 of them.
    """
    registry = DictionaryRegistry(ROOT / "lang", lang_file)
    table = registry.table(lang_file)
    every, reachable = [], []
    for key, value in table.items():
        if not key.startswith(("item.", "block.")):
            continue
        every.append(value)
        if not key.startswith(BANNER_PREFIX):
            reachable.append(value)
    return every, reachable


def covered(names: list[str], lang: str, lang_file: str) -> tuple[int, list[str]]:
    """How many names the backend actually builds a tier-3 sentence for.

    This asks `sentence_for` the same question the server asks at request
    time and believes the answer, rather than re-implementing the decision
    here. Anything that comes back untemplated is an item that falls back to
    tier 2 -- which is the behaviour the READMEs describe, so it is also the
    thing the percentage should count.
    """
    pack = _PHRASE_PACKS.get(lang)
    if not (pack and pack.get("known")):
        return len(names), []
    hit, missed = 0, []
    for name in names:
        _text, _en, templated = sentence_for(
            name, lang_file, "craft", tier=3, level=MAX_LEVEL,
            english_name=name)
        if templated:
            hit += 1
        else:
            missed.append(head_noun(name, lang_file)[0])
    return hit, missed


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
    show_missing = "--missing" in sys.argv

    print(f"{'lang':6} {'all names':>20} {'reachable items':>20}")
    for lang, lang_file in LANGS.items():
        if only and lang != only:
            continue
        every, reachable = item_names(lang_file)
        if not (_PHRASE_PACKS.get(lang) or {}).get("known"):
            print(f"{lang:6} {'n/a (no gender needed)':>41}"
                  f"   {len(reachable)} items")
            continue
        hit_all, _ = covered(every, lang, lang_file)
        hit_reach, missed = covered(reachable, lang, lang_file)
        print(f"{lang:6} {hit_all:5}/{len(every):5} = {hit_all/len(every):5.1%} "
              f"{hit_reach:5}/{len(reachable):5} = {hit_reach/len(reachable):5.1%}")
        if show_missing:
            print(f"\n  uncovered heads for {lang}, by items they would unlock:")
            for head, count in Counter(missed).most_common(40):
                print(f"    {count:4}  {head}")
            tail = sum(1 for _, c in Counter(missed).items() if c <= 2)
            print(f"    ... {len(Counter(missed))} distinct heads, "
                  f"{tail} of them at two items or fewer\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
