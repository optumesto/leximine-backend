# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Table test for the villager trade judge.

Needs a local model (Ollama by default). Judging is a model's opinion rather
than a function, so this is a sanity check on the prompt, not a proof: the
cases here are ones where a reasonable judge should not disagree.

    python scripts/test_trade_judge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.llm import TradeJudge  # noqa: E402

# (sentence, item, should it be accepted?)
CASES: list[tuple[str, str, bool]] = [
    # Good requests. The verb should not matter -- buying, trading and asking
    # are all reasonable ways to get the thing.
    ("Quiero comprar una esmeralda, por favor", "Esmeralda", True),
    ("Me gustaría cambiar esto por una esmeralda", "Esmeralda", True),
    ("¿Me das una esmeralda?", "Esmeralda", True),
    ("Necesito pan, por favor", "Pan", True),
    # Speech recognition drops punctuation and capitals; that must not fail.
    ("quiero comprar una esmeralda por favor", "Esmeralda", True),
    # Not requests.
    ("esmeralda", "Esmeralda", False),
    ("la esmeralda es verde", "Esmeralda", False),
    # Wrong language.
    ("I would like to buy an emerald please", "Esmeralda", False),
    # Wrong item.
    ("Quiero comprar un pan, por favor", "Esmeralda", False),
    # Not a sentence at all.
    ("eh", "Esmeralda", False),
]


def main() -> int:
    judge = TradeJudge(settings.llm_base_url, settings.llm_model, settings.llm_timeout_s)
    print(f"model: {settings.llm_model} at {settings.llm_base_url}\n")

    failures = 0
    for sentence, item, expected in CASES:
        verdict = judge.judge(sentence, item, "es_es")
        if not verdict.available:
            print(f"!! no model reachable: {verdict.reason}")
            print("   Start Ollama, or set LINGUA_LLM_URL / LINGUA_LLM_MODEL.")
            return 2
        ok = verdict.accepted == expected
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'MISS'} want={'accept' if expected else 'reject':6} "
              f"got={'accept' if verdict.accepted else 'reject':6} "
              f"{verdict.elapsed_ms:5}ms  {sentence!r}")
        if not ok:
            print(f"       reason: {verdict.reason}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} judged as expected")
    # A model is allowed one odd call without failing the suite; two means the
    # prompt needs work.
    return 1 if failures > 1 else 0


if __name__ == "__main__":
    sys.exit(main())
