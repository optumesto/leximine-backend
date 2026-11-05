# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Put the drill history at a chosen progress level, for screenshots.

Saying 120 words correctly to see what a level-3 prompt looks like is not a
reasonable way to check a layout.

    python scripts/seed_level.py 3
    python scripts/seed_level.py 0      # back to the start
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.stats import stats  # noqa: E402


def main() -> int:
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    lang = sys.argv[2] if len(sys.argv) > 2 else settings.target_lang

    stats.open()
    stats.forget(lang)
    if want > 0:
        needed = stats.LEVEL_THRESHOLDS[min(want, len(stats.LEVEL_THRESHOLDS) - 1)]
        # Spread over a few words so /review still has something sensible to
        # draw from, rather than one word with a hundred successes.
        words = [("item.minecraft.diamond", "Diamante"),
                 ("item.minecraft.bread", "Pan"),
                 ("item.minecraft.stone", "Piedra")]
        for index in range(needed):
            key, name = words[index % len(words)]
            stats.record(lang, key, name, True)

    print(f"{lang}: {stats.successes(lang)} successes -> level {stats.level(lang)}")
    stats.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
