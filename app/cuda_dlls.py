# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Make the pip-installed CUDA libraries findable on Windows.

`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` drops the DLLs under
site-packages/nvidia/*/bin, which is on nobody's search path. CTranslate2
loads them lazily at the first GPU kernel launch, so without this the server
starts fine, reports `device=cuda`, and then dies mid-game with

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

Both mechanisms are used on purpose: `os.add_dll_directory` is what Python 3.8+
extension modules honour, and the PATH prepend covers CTranslate2's own
LoadLibrary call, which does not use the user-directory search flags.

Import this before `ctranslate2` / `faster_whisper`. No-op off Windows, where
the wheels resolve through RPATH.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_registered: list[str] = []


def register() -> list[str]:
    """Add every bundled NVIDIA bin directory to the DLL search path."""
    if _registered or sys.platform != "win32":
        return _registered

    for site_dir in {Path(p) for p in sys.path if p}:
        nvidia = site_dir / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in sorted(nvidia.glob("*/bin")):
            if not any(bin_dir.glob("*.dll")):
                continue
            path = str(bin_dir)
            try:
                os.add_dll_directory(path)
            except (OSError, AttributeError):
                pass
            if path not in os.environ.get("PATH", ""):
                os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
            _registered.append(path)
    return _registered


register()
