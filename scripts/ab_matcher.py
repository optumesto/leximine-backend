# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A/B a matcher change over the cached corpora, both error types at once.

`tune_thresholds.py` decodes the positive and negative corpora once and caches
the transcripts. That cache is the cheap way to ask "does this matcher change
pay?", because the expensive half -- the speech recognition -- does not have to
run again, and the answer comes back on both errors together instead of only
the flattering one.

    python scripts/ab_matcher.py
    python scripts/ab_matcher.py --voices --show 20

Reports accept-when-correct and accept-when-wrong with and without the
vocabulary (`rivals`) passed in, plus the rows whose verdict changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402

load_env()  # before app.config, which reads the environment at import time

from app.config import settings  # noqa: E402
from app.lang import DictionaryRegistry  # noqa: E402
from app.matching import match  # noqa: E402

from tune_thresholds import cache_path  # noqa: E402
from benchmark_voices import DEFAULT_VOICES  # noqa: E402


def load(voices: bool) -> tuple[list[dict], list[dict], Path]:
    path = cache_path(DEFAULT_VOICES if voices else [])
    if not path.is_file():
        raise SystemExit(f"no cache at {path}; run tune_thresholds.py first")
    payload = json.loads(path.read_text(encoding="utf8"))
    return payload["positives"], payload["negatives"], path


def verdicts(rows: list[dict], dictionary: DictionaryRegistry,
             use_rivals: bool) -> list[bool]:
    out = []
    for row in rows:
        rivals = (dictionary.rivals_for(row["target"], row["lang"])
                  if use_rivals else ())
        result = match(row["heard"], row["target"],
                       threshold=settings.match_threshold,
                       token_gate=settings.token_gate,
                       language=row["lang"],
                       key_tokens=tuple(row["key_tokens"]) or None,
                       rivals=rivals)
        out.append(result.passed)
    return out


def pct(flags: list[bool]) -> float:
    return 100.0 * sum(flags) / len(flags) if flags else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", action="store_true",
                    help="use the multi-voice cache instead of the single-voice one")
    ap.add_argument("--show", type=int, default=12,
                    help="how many changed rows to list")
    args = ap.parse_args()

    positives, negatives, path = load(args.voices)
    print(f"{path.name}: {len(positives)} positives, {len(negatives)} negatives\n")

    dictionary = DictionaryRegistry(
        Path(settings.lang_dir), settings.target_lang.lower(),
        mc_root=Path(settings.mc_root) if settings.mc_root else None,
        mc_version=settings.mc_version)
    dictionary.load_default()

    rows = []
    for label, data in (("accepted when correct", positives),
                        ("accepted when WRONG", negatives)):
        off = verdicts(data, dictionary, False)
        on = verdicts(data, dictionary, True)
        rows.append((label, data, off, on))

    print(f"{'':24}{'without':>10}{'with':>10}{'change':>9}")
    for label, _, off, on in rows:
        a, b = pct(off), pct(on)
        print(f"{label:24}{a:>9.1f}%{b:>9.1f}%{b - a:>+9.1f}")

    # Only short one-word targets can be affected at all, so the headline
    # numbers dilute the effect. Report that slice on its own as well.
    print()
    for label, data, off, on in rows:
        idx = [i for i, row in enumerate(data)
               if dictionary.rivals_for(row["target"], row["lang"])]
        if not idx:
            continue
        a = pct([off[i] for i in idx])
        b = pct([on[i] for i in idx])
        print(f"{label + ' (short names only)':40}{a:>8.1f}%{b:>8.1f}%"
              f"{b - a:>+9.1f}  n={len(idx)}")

    for label, data, off, on in rows:
        changed = [(data[i], off[i], on[i])
                   for i in range(len(data)) if off[i] != on[i]]
        if not changed:
            continue
        print(f"\n{len(changed)} changed rows in '{label}':")
        for row, was, now in changed[:args.show]:
            rivals = dictionary.rivals_for(row["target"], row["lang"])
            result = match(row["heard"], row["target"],
                           threshold=settings.match_threshold,
                           token_gate=settings.token_gate, language=row["lang"],
                           key_tokens=tuple(row["key_tokens"]) or None,
                           rivals=rivals)
            arrow = "now PASSES" if now else "now fails "
            print(f"  {arrow}  said {row['spoken']!r:18} want {row['target']!r:18}"
                  f" heard {row['heard']!r:20} score {result.score} "
                  f"rival {result.breakdown.get('rival', '-')} {result.rival!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
