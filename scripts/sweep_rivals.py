# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sweep the two short-name constants over the cached corpora.

`SHORT_TARGET_SLACK` is how much the threshold eases for a short one-word
name, and `SHORT_TARGET_MARGIN` is how far ahead of the nearest *other* name
the target has to be before any of it is given. Both were picked by argument
and then measured here, on both errors at once.

    python scripts/sweep_rivals.py --voices
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402

load_env()  # before app.config, which reads the environment at import time

from app import matching  # noqa: E402
from app.config import settings  # noqa: E402
from app.lang import DictionaryRegistry  # noqa: E402

from ab_matcher import load, pct, verdicts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", action="store_true")
    ap.add_argument("--length", action="store_true",
                    help="sweep SHORT_TARGET_LEN instead of slack/margin")
    args = ap.parse_args()

    positives, negatives, path = load(args.voices)
    print(f"{path.name}: {len(positives)} positives, {len(negatives)} negatives\n")

    dictionary = DictionaryRegistry(
        Path(settings.lang_dir), settings.target_lang.lower(),
        mc_root=Path(settings.mc_root) if settings.mc_root else None,
        mc_version=settings.mc_version)
    dictionary.load_default()

    base_accept = pct(verdicts(positives, dictionary, False))
    base_leak = pct(verdicts(negatives, dictionary, False))
    print(f"{'slack':>6}{'margin':>8}{'accepted':>11}{'wrong':>9}{'margin':>9}")
    print(f"{'off':>6}{'-':>8}{base_accept:>10.1f}%{base_leak:>8.1f}%"
          f"{base_accept - base_leak:>9.1f}")

    # With no slack the margin cannot matter, so that row is run once: it is
    # the veto on its own, which is worth seeing separately.
    grid = [(0, 0)] + [(slack, margin)
                       for slack in (4, 6, 8, 10, 12)
                       for margin in (0, 6, 10, 14, 20)]
    if args.length:
        # How short is "short"? Below five characters a single mis-heard
        # letter already costs more than the threshold allows, so the slack
        # cannot matter above that -- but the *veto* still can, and widening
        # the net is not obviously free. Measured rather than assumed.
        print()
        for length in (3, 4, 5, 6, 8, 10):
            matching.SHORT_TARGET_LEN = length
            dictionary._short_names_cache.clear()
            pool = len(dictionary.rivals_for("x" * length, settings.target_lang.lower()))
            accept = pct(verdicts(positives, dictionary, True))
            leak = pct(verdicts(negatives, dictionary, True))
            print(f"  max length {length:>2}  pool {pool:>4}  "
                  f"accepted {accept:>5.1f}%  wrong {leak:>4.1f}%")
        return 0

    best = None
    for slack, margin in grid:
        matching.SHORT_TARGET_SLACK = slack
        matching.SHORT_TARGET_MARGIN = margin
        accept = pct(verdicts(positives, dictionary, True))
        leak = pct(verdicts(negatives, dictionary, True))
        flag = ""
        if best is None or (accept - leak) > best[0]:
            best = (accept - leak, slack, margin)
            flag = "  <- best so far"
        print(f"{slack:>6}{margin:>8}{accept:>10.1f}%{leak:>8.1f}%"
              f"{accept - leak:>9.1f}{flag}")
    print(f"\nbest separation: slack {best[1]}, margin {best[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
