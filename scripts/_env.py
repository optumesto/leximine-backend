# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Apply `.env` the way `start_backend.ps1` does, before `app.config` loads.

Every measurement script has to do this. `app.config.Settings` reads the
environment at import time and the launcher exports `.env` into it, so a
script that skips this step builds a `Settings` from the code defaults and
then reports numbers for a configuration the server is not running. That is
not hypothetical: `.env` pinned `LINGUA_BEAM_SIZE=1` for a whole night of
tuning that had concluded 5, and nothing anywhere said so.

Import this **before** anything from `app`:

    from _env import load_env
    load_env()
    from app.config import settings
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> list[str]:
    """Set every key in the env file; returns what it applied, for reporting."""
    env_file = path or (ROOT / ".env")
    applied: list[str] = []
    if not env_file.is_file():
        return applied
    for line in env_file.read_text(encoding="utf8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"')
        os.environ[key] = value
        applied.append(f"{key}={value}")
    return applied
