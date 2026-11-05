# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The short-name vocabulary check must not misfire in other languages.

`rivals` was designed against Spanish, where item names are short and the
phonetic layer is real. The same code runs for every language the mod can
ask for, and two of them break the assumptions it was written under: German
item names are compounds (so almost nothing is "short"), and Japanese has no
spaces at all, so a whole name can be two characters and the candidate pool
is enormous.

The property that has to hold everywhere is simple: **saying the name
correctly must still pass.** The vocabulary is only ever allowed to choose
between candidates, never to reject the right answer.

    python scripts/test_rivals_langs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402

load_env()

from app.config import settings  # noqa: E402
from app.lang import DictionaryRegistry  # noqa: E402
from app.matching import match  # noqa: E402

LANGUAGES = ["es_es", "de_de", "fr_fr", "ja_jp", "ru_ru"]

# Deliberately a mix of very short names and long ones, across the tiers.
ITEMS = [
    ("minecraft:string", 1),
    ("minecraft:stone", 1),
    ("minecraft:bread", 1),
    ("minecraft:bucket", 1),
    ("minecraft:iron_ingot", 2),
    ("minecraft:diamond_sword", 2),
]


def main() -> int:
    failures = 0
    for lang in LANGUAGES:
        dictionary = DictionaryRegistry(
            Path(settings.lang_dir), lang,
            mc_root=Path(settings.mc_root) if settings.mc_root else None,
            mc_version=settings.mc_version)
        if not dictionary.is_available(lang):
            print(f"-- {lang:8} not installed, skipped")
            continue
        dictionary._english = dictionary._load_english()

        pool = len(dictionary._short_names(lang))
        checked = 0
        for item_id, tier in ITEMS:
            entry = dictionary.resolve(item_id=item_id, lang=lang, tier=tier)
            if not entry:
                continue
            checked += 1
            # Said perfectly, which must always pass -- and said perfectly but
            # lower-cased and punctuated the way a decoder writes it.
            for spoken in (entry.target, entry.target.lower() + "."):
                result = match(spoken, entry.target,
                               threshold=settings.match_threshold,
                               token_gate=settings.token_gate, language=lang,
                               key_tokens=entry.key_tokens or None,
                               rivals=entry.rivals)
                if not result.passed:
                    failures += 1
                    print(f"   FAIL {lang} {entry.target!r} heard as {spoken!r}: "
                          f"score {result.score}, rival {result.rival!r}")
        print(f"-- {lang:8} {pool:>4} short names, {checked} items checked")

    print(f"\n{'FAILED' if failures else 'PASSED'}"
          f"{f' ({failures} rejections of a correct answer)' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
