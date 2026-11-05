# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Print the settings this backend will actually run with.

Written because the running server and the code defaults had quietly drifted
apart: `.env` pinned `LINGUA_BEAM_SIZE=1` while the tuned default was 5, so
every benchmark had been measuring a configuration the server was not using.
The `.env` wins, and nothing says so anywhere.

    python scripts/show_config.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402


def main() -> int:
    applied = load_env()

    from app import phonetics
    from app.config import Settings

    s = Settings()
    print("speech recognition")
    print(f"  model             {s.model_size} on {s.device}/{s.compute_type}")
    print(f"  beam size         {s.beam_size}")
    print(f"  vad               {s.vad_filter}")
    print(f"  context prompt    {s.context_prompt}")
    print(f"  normalise input   {s.normalise_input}")
    print(f"  temp fallback     {s.temperature_fallback}")

    print("\nmatching")
    print(f"  threshold / gate  {s.match_threshold} / {s.token_gate}")
    print(f"  difficulty        {s.difficulty}")
    print(f"  lenient stops     {phonetics.LENIENT_STOPS}")
    print(f"  easy mode         {s.leak_target_to_decoder}")

    print("\ntrade judge")
    print(f"  model             {s.llm_model} at {s.llm_base_url}")
    print(f"  keep alive        {s.llm_keep_alive}, re-checked every "
          f"{s.llm_warm_interval_s:.0f}s")

    print(f"\nlanguage            {s.target_lang}")
    if applied:
        print(f"\n.env overrode {len(applied)} setting(s):")
        for line in applied:
            print(f"  {line}")
    else:
        print("\nno .env; code defaults throughout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
