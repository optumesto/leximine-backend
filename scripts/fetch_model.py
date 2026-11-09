# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Download the configured speech model, so first play is not a long wait.

`setup.ps1` runs this. Without it the 461 MB download happens the first time
someone double-clicks the desktop shortcut, in a window they think is just a
launcher -- which is where people force-close and end up with a half-written
model cache.

Loading on cpu/int8 rather than the configured device on purpose: this only
has to pull the weights, and a machine with no CUDA must be able to run it.
The real device is chosen at play time by `app/stt.py`.

Idempotent and offline-safe: if the weights are already in the cache it
returns in seconds without touching the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _env import load_env  # noqa: E402

load_env()

from app.config import settings  # noqa: E402


def main() -> int:
    size = settings.model_size
    root = settings.download_root
    print(f"  model '{size}' -> {root}")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  !! faster-whisper is not installed yet; skipping the model.")
        return 1

    try:
        WhisperModel(size, device="cpu", compute_type="int8", download_root=root)
    except Exception as error:  # noqa: BLE001 - any failure here is non-fatal
        # Deliberately not fatal to setup. A failed pre-download costs the
        # player a wait on first launch; a failed *setup* costs them the
        # install. start_backend.ps1 downloads it again if it is missing.
        print(f"  !! could not fetch the model: {error}")
        print("  Setup will continue; it downloads on first start instead.")
        return 1

    print("  model ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
