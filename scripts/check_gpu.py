# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Verify the GPU path end to end before blaming the mod.

Enumerating the device is not proof of anything: ctranslate2 wheels ship a
fixed list of compiled architectures, and on an RTX 50-series card (sm_120) an
older wheel will happily report the device and then die at the first kernel
launch. So this script actually decodes audio.

    python scripts/check_gpu.py
    python scripts/check_gpu.py --model medium
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.config import Settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="override LINGUA_MODEL")
    ap.add_argument("--compute-type", default=None)
    args = ap.parse_args()

    from app import cuda_dlls

    registered = cuda_dlls.register()
    import ctranslate2

    print(f"ctranslate2      : {ctranslate2.__version__}")
    print(f"cuda dll dirs    : {len(registered)} registered")
    for path in registered:
        print(f"                   {path}")
    n = ctranslate2.get_cuda_device_count()
    print(f"cuda devices     : {n}")
    if n == 0:
        print("\n!! No CUDA device visible to ctranslate2.")
        print("   Driver present but no device usually means a CPU-only wheel:")
        print("   pip install --force-reinstall 'ctranslate2>=4.6'")
        return 1
    print(f"cuda compute     : {sorted(ctranslate2.get_supported_compute_types('cuda'))}")

    settings = Settings()
    if args.model:
        settings.model_size = args.model
    if args.compute_type:
        settings.compute_type = args.compute_type

    print(f"\nmodel            : {settings.model_size}")
    print(f"requested device : {settings.device}/{settings.compute_type}")
    print(f"model cache      : {settings.download_root}")

    from app.stt import SttEngine

    engine = SttEngine(settings)
    t0 = time.perf_counter()
    try:
        engine.load()
    except Exception as exc:  # noqa: BLE001
        print(f"\n!! model init failed: {exc}")
        return 1
    print(f"loaded+warmed    : {time.perf_counter() - t0:.1f}s")
    print(f"actual device    : {engine.device}/{engine.compute_type}")

    if engine.device != settings.device:
        print("\n!! FELL BACK TO CPU -- the GPU path is broken.")
        print("   Most likely an ctranslate2/cuDNN mismatch for this card. Try:")
        print("   pip install --force-reinstall 'ctranslate2>=4.6' 'nvidia-cudnn-cu12>=9.1'")

    # Timed passes on a 1.5 s buffer, the realistic length of one spoken word.
    # VAD and the RMS gate are disabled here: with them on, synthetic noise is
    # thrown away before the decoder runs and the number measures nothing.
    engine.settings.vad_filter = False
    engine.settings.silence_rms = 0.0
    noise = (np.random.default_rng(0).standard_normal(24_000) * 0.02).astype(np.float32)
    pcm = (noise * 32767).astype("<i2").tobytes()
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        engine.transcribe(pcm)
        times.append((time.perf_counter() - t0) * 1000)
    print(
        f"1.5s decode     : {min(times):.0f} / {sum(times)/len(times):.0f} / "
        f"{max(times):.0f} ms (min/avg/max, VAD off)"
    )

    print("\nOK" if engine.device == settings.device else "\nDEGRADED (running on CPU)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
