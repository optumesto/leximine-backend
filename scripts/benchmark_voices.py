# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Does the tuning hold up across accents, or only the one voice it was tuned on?

Everything measured so far used a single Spanish neural voice. That is a real
blind spot: a configuration can be tuned into fitting one speaker and fall
over on the next. Minecraft's Spanish players are not all Castilian.

This runs the same corpus through several edge-tts voices from different
regions and reports the pass rate for each. It cannot tell you how the model
handles *your* microphone -- only recording yourself does that -- but it does
say whether 90% was a property of the system or of one synthetic speaker.

    python scripts/benchmark_voices.py
    python scripts/benchmark_voices.py --voices es-ES-AlvaroNeural,es-MX-JorgeNeural
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402

load_env()  # before app.config, which reads the environment at import time

from app.config import difficulty_for, settings  # noqa: E402
from app.lang import DictionaryRegistry, whisper_language_for  # noqa: E402
from app.matching import match  # noqa: E402
from app.stt import SttEngine  # noqa: E402

from benchmark_asr import CORPUS_ITEMS, degrade, load_wav, to_pcm  # noqa: E402

# Different countries, and a male and a female voice in each where possible.
DEFAULT_VOICES = [
    "es-ES-AlvaroNeural",     # Spain, male
    "es-ES-ElviraNeural",     # Spain, female
    "es-MX-JorgeNeural",      # Mexico, male
    "es-MX-DaliaNeural",      # Mexico, female
    "es-AR-TomasNeural",      # Argentina, male
    "es-CO-GonzaloNeural",    # Colombia, male
]

# `--wide`. Six voices is a small sample to quote a worst case from, and the
# worst case is the whole point of this script -- so this doubles it, adds
# Caribbean, Andean and US Spanish, and balances the genders. Slower (every
# clip is synthesised per voice), run it when the number is going in a README.
WIDE_VOICES = DEFAULT_VOICES + [
    "es-AR-ElenaNeural",      # Argentina, female
    "es-CL-LorenzoNeural",    # Chile, male
    "es-CO-SalomeNeural",     # Colombia, female
    "es-PE-CamilaNeural",     # Peru, female
    "es-US-AlonsoNeural",     # US Spanish, male
    "es-DO-RamonaNeural",     # Dominican Republic, female
]


async def say(text: str, voice: str, out: Path) -> bool:
    """Speak `text` in `voice` into a 16 kHz mono wav. False if unavailable.

    Also imported by `tune_thresholds.py`, which needs the same voices to
    measure the operating point against more than one speaker.
    """
    import edge_tts

    mp3 = out.with_suffix(".mp3")
    try:
        await edge_tts.Communicate(text, voice).save(str(mp3))
    except Exception:  # noqa: BLE001
        return False
    if not mp3.is_file() or mp3.stat().st_size < 512:
        return False
    try:
        import av

        with av.open(str(mp3)) as container, av.open(
                str(out), "w", format="wav") as sink:
            stream = sink.add_stream("pcm_s16le", rate=settings.sample_rate,
                                     layout="mono")
            for frame in container.decode(audio=0):
                frame.pts = None
                for packet in stream.encode(frame):
                    sink.mux(packet)
            for packet in stream.encode(None):
                sink.mux(packet)
    except Exception:  # noqa: BLE001
        return False
    finally:
        mp3.unlink(missing_ok=True)
    return out.is_file()


async def run(args) -> int:
    lang = settings.target_lang.lower()
    threshold, gate = difficulty_for(settings.difficulty, settings.difficulty)
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]

    dictionary = DictionaryRegistry(
        Path(settings.lang_dir), lang,
        mc_root=Path(settings.mc_root) if settings.mc_root else None,
        mc_version=settings.mc_version)
    dictionary.load_default()

    entries = []
    for item_id, tier in CORPUS_ITEMS[:args.items]:
        entry = dictionary.resolve(item_id=item_id, lang=lang, tier=tier,
                                   context="pickup", level=0)
        if entry:
            entries.append(entry)

    engine = SttEngine(settings)
    engine.load()
    rng = np.random.default_rng(7)
    snr_values = [None, 14.0]

    print(f"\n{len(entries)} prompts x {len(snr_values)} noise levels "
          f"x {len(voices)} voices\n")

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for voice in voices:
            passes = attempts = 0
            scores: list[int] = []
            worst: list[str] = []
            for entry in entries:
                wav = Path(tmp) / "clip.wav"
                wav.unlink(missing_ok=True)
                if not await say(entry.target, voice, wav):
                    continue
                samples, rate = load_wav(wav)
                if rate != settings.sample_rate or samples.size == 0:
                    continue
                for snr in snr_values:
                    audio = degrade(samples, snr, rng, 1.0)
                    transcript = engine.transcribe(
                        to_pcm(audio), None, whisper_language_for(lang))
                    result = match(transcript.text, entry.target,
                                   threshold=threshold, token_gate=gate,
                                   language=entry.lang,
                                   key_tokens=entry.key_tokens or None,
                                   rivals=entry.rivals)
                    attempts += 1
                    scores.append(result.score)
                    if result.passed:
                        passes += 1
                    else:
                        worst.append(f"{entry.target!r} -> {transcript.text!r}")
            if not attempts:
                print(f"-- {voice:26} no audio (voice unavailable?)")
                continue
            rate_pct = 100.0 * passes / attempts
            results.append((voice, rate_pct, statistics.mean(scores), attempts))
            print(f"-- {voice:26} {rate_pct:5.1f}%  mean {statistics.mean(scores):.0f}"
                  f"  ({attempts} clips)")
            for miss in worst[:args.show_misses]:
                print(f"     miss: {miss}")

    if not results:
        return 1
    print("\n" + "=" * 62)
    for voice, rate_pct, mean, _ in sorted(results, key=lambda r: -r[1]):
        print(f"{voice:30}{rate_pct:>8.1f}%{mean:>8.0f}")
    spread = max(r[1] for r in results) - min(r[1] for r in results)
    print(f"\nspread across voices: {spread:.1f} points")
    if spread > 12:
        # Measured, so this is no longer the open question it reads as: the
        # operating point was re-swept over all six voices at once
        # (`tune_thresholds.py --voices`) and 80 / 70 is still the optimum.
        # The spread is a property of the speech recogniser, not of the
        # tuning -- which means the answer is to quote the worst case, not to
        # go looking for a threshold that closes the gap.
        print("Quote the worst case, not the average. The threshold is not")
        print("what causes this -- see tune_thresholds.py --voices.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", default=",".join(DEFAULT_VOICES))
    ap.add_argument("--wide", action="store_true",
                    help="twelve voices instead of six, for a quotable worst case")
    # The whole corpus by default. `--items N` takes the FIRST N and
    # CORPUS_ITEMS is ordered shortest-name-first, so a cut is not a sample --
    # it is a selection of the hardest prompts, and it reported the worst
    # voice eight points lower than the full run does.
    ap.add_argument("--items", type=int, default=len(CORPUS_ITEMS))
    ap.add_argument("--show-misses", type=int, default=4)
    args = ap.parse_args()
    if args.wide:
        args.voices = ",".join(WIDE_VOICES)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
