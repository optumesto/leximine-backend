# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Measure how often a player would actually be understood.

Tuning speech recognition by ear is how you end up confidently wrong, so this
builds a fixed corpus and scores configurations against it.

**The metric is the pass rate, not the word error rate.** What matters is not
whether whisper produced the exact string but whether the player would have
been accepted -- so every clip is run through the real matcher, with the real
thresholds, exactly as a challenge would.

The corpus is spoken by the backend's own neural voice and then deliberately
degraded, because a studio-clean clip flatters every configuration equally and
tells you nothing about a headset in a room with a fan. Absolute numbers here
are optimistic against a real microphone; the *ranking* between configurations
is what this is for.

    python scripts/benchmark_asr.py
    python scripts/benchmark_asr.py --models small,medium --beams 1,5
    python scripts/benchmark_asr.py --snr clean,20,12
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402

load_env()  # before app.config, which reads the environment at import time

from app.config import Settings, difficulty_for, settings  # noqa: E402
from app.lang import DictionaryRegistry, whisper_language_for  # noqa: E402
from app.matching import match  # noqa: E402
from app.stt import SttEngine, pcm_to_float32  # noqa: E402
from app.tts import Pronouncer  # noqa: E402

CORPUS_DIR = Path(settings.tts_cache_dir).parent / "benchmark"

# Weighted towards single words on purpose: tier 1 is what a player meets most,
# and a lone noun is the hardest thing to recognise -- whisper's language model
# has almost no context to lean on and will happily invent a sentence.
CORPUS_ITEMS = [
    # Tier 1 is two thirds of the corpus because it is most of what a player
    # meets, and because a lone noun is where this is hardest. A sample this
    # size also matters: beam search on the GPU is not bit-deterministic, so
    # a small corpus moves a point or two between identical runs and
    # differences that size mean nothing.
    ("minecraft:diamond", 1), ("minecraft:stone", 1), ("minecraft:bread", 1),
    ("minecraft:apple", 1), ("minecraft:coal", 1), ("minecraft:oak_planks", 1),
    ("minecraft:iron_ingot", 1), ("minecraft:stick", 1), ("minecraft:bucket", 1),
    ("minecraft:torch", 1), ("minecraft:arrow", 1), ("minecraft:egg", 1),
    ("minecraft:dirt", 1), ("minecraft:sand", 1), ("minecraft:glass", 1),
    ("minecraft:book", 1), ("minecraft:feather", 1), ("minecraft:leather", 1),
    ("minecraft:wheat", 1), ("minecraft:carrot", 1), ("minecraft:potato", 1),
    ("minecraft:emerald", 1), ("minecraft:redstone", 1), ("minecraft:bone", 1),
    ("minecraft:string", 1), ("minecraft:ladder", 1), ("minecraft:chest", 1),
    ("minecraft:furnace", 1), ("minecraft:saddle", 1), ("minecraft:shears", 1),
    ("minecraft:iron_sword", 2), ("minecraft:golden_apple", 2),
    ("minecraft:diamond_pickaxe", 2), ("minecraft:iron_helmet", 2),
    ("minecraft:oak_door", 2), ("minecraft:fishing_rod", 2),
    ("minecraft:golden_helmet", 2), ("minecraft:stone_axe", 2),
    ("minecraft:diamond_sword", 3), ("minecraft:netherite_ingot", 3),
    ("minecraft:diamond_chestplate", 3), ("minecraft:enchanting_table", 3),
]


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    return pcm_to_float32(raw), rate


def to_pcm(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def degrade(samples: np.ndarray, snr_db: float | None, rng: np.random.Generator,
            gain: float) -> np.ndarray:
    """Quieten it and add hiss, the way a real headset does."""
    out = samples * gain
    if snr_db is None:
        return out
    speech_power = float(np.mean(np.square(out))) or 1e-9
    noise_power = speech_power / (10 ** (snr_db / 10.0))
    return out + rng.normal(0.0, float(np.sqrt(noise_power)), out.shape).astype(np.float32)


# ----------------------------------------------------------------------
class Case:
    def __init__(self, key: str, tier: int, target: str, entry) -> None:
        self.key = key
        self.tier = tier
        self.target = target
        self.entry = entry
        self.samples: np.ndarray | None = None


async def build_corpus(dictionary: DictionaryRegistry, lang: str) -> list[Case]:
    pronouncer = Pronouncer(Path(settings.tts_cache_dir), settings.sample_rate)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    cases: list[Case] = []
    for item_id, tier in CORPUS_ITEMS:
        entry = dictionary.resolve(item_id=item_id, lang=lang, tier=tier,
                                   context="pickup", level=0)
        if not entry:
            print(f"  !! no dictionary entry for {item_id}")
            continue
        path = await pronouncer.wav_for(lang, entry.target)
        if not path:
            print(f"  !! no voice for {entry.target!r}")
            continue
        samples, rate = load_wav(Path(path))
        if rate != settings.sample_rate:
            print(f"  !! {path} is {rate} Hz, expected {settings.sample_rate}")
            continue
        case = Case(entry.key, tier, entry.target, entry)
        case.samples = samples
        cases.append(case)
    return cases


def run_config(cases: list[Case], model_size: str, beam: int, temperature_fallback: bool,
               snr_values: list[float | None], threshold: int, gate: int,
               context_prompt: bool = True, vad: bool = True,
               normalise: bool = False, compute_type: str | None = None) -> dict:
    """Load one configuration and score the whole corpus through it."""
    tuned = Settings()
    tuned.model_size = model_size
    if compute_type:
        tuned.compute_type = compute_type
    tuned.beam_size = beam
    tuned.temperature_fallback = temperature_fallback
    tuned.context_prompt = context_prompt
    tuned.vad_filter = vad
    tuned.normalise_input = normalise

    engine = SttEngine(tuned)
    engine.load()

    rng = np.random.default_rng(1234)
    passes = 0
    attempts = 0
    exact = 0
    scores: list[int] = []
    latencies: list[int] = []
    misses: list[str] = []

    for snr in snr_values:
        for case in cases:
            # A quiet speaker and a loud one, since input level is the thing
            # players get wrong most and cannot see themselves getting wrong.
            for gain in (1.0, 0.35):
                audio = degrade(case.samples, snr, rng, gain)
                transcript = engine.transcribe(
                    to_pcm(audio), None, whisper_language_for(case.entry.lang))
                result = match(transcript.text, case.target, threshold=threshold,
                               token_gate=gate, language=case.entry.lang,
                               key_tokens=case.entry.key_tokens or None,
                               # The vocabulary goes in because the server puts
                               # it in. A harness that leaves it out measures a
                               # stricter matcher than the one players meet.
                               rivals=case.entry.rivals)
                attempts += 1
                scores.append(result.score)
                latencies.append(transcript.infer_ms)
                if result.passed:
                    passes += 1
                else:
                    misses.append(f"{case.target!r} -> {transcript.text!r} ({result.score})")
                if transcript.text.strip().lower().rstrip(".!?") == case.target.lower():
                    exact += 1

    return {
        "model": model_size,
        "beam": beam,
        "fallback": temperature_fallback,
        "attempts": attempts,
        "pass_rate": 100.0 * passes / max(1, attempts),
        "exact_rate": 100.0 * exact / max(1, attempts),
        "mean_score": statistics.mean(scores) if scores else 0,
        "median_ms": statistics.median(latencies) if latencies else 0,
        "misses": misses,
    }


def main() -> int:
    # Every default comes from the live settings, so a bare run measures the
    # configuration the server is running rather than whatever was being
    # compared the day the flag was added. These had drifted: the defaults
    # were beam 1 with the context prompt on, which is neither what the code
    # ships nor what `.env` sets, and a plain `benchmark_asr.py` was quietly
    # reporting a configuration nobody runs. `scripts/show_config.py` prints
    # the same values from the other end.
    on_off = lambda flag: "on" if flag else "off"  # noqa: E731

    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=settings.model_size)
    ap.add_argument("--beams", default=str(settings.beam_size))
    ap.add_argument("--fallback", default=on_off(settings.temperature_fallback),
                    choices=["off", "on", "both"])
    ap.add_argument("--snr", default="clean,18,10",
                    help="signal-to-noise ratios in dB, or 'clean'")
    ap.add_argument("--prompt", default=on_off(settings.context_prompt),
                    choices=["off", "on", "both"],
                    help="prepend a sentence of context in the target language")
    ap.add_argument("--vad", default=on_off(settings.vad_filter),
                    choices=["off", "on", "both"],
                    help="whisper's own voice-activity trimming")
    ap.add_argument("--normalise", default=on_off(settings.normalise_input),
                    choices=["off", "on", "both"],
                    help="bring the input up to a target level before decoding")
    ap.add_argument("--compute", default=None,
                    help="comma-separated compute types, e.g. float16,float32")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--show-misses", type=int, default=6)
    args = ap.parse_args()

    lang = (args.lang or settings.target_lang).lower()
    threshold, gate = difficulty_for(settings.difficulty, settings.difficulty)

    print("building the corpus (cached after the first run)...")
    dictionary = DictionaryRegistry(
        Path(settings.lang_dir), lang,
        mc_root=Path(settings.mc_root) if settings.mc_root else None,
        mc_version=settings.mc_version)
    dictionary.load_default()
    cases = asyncio.run(build_corpus(dictionary, lang))
    if not cases:
        print("!! no corpus; is the text-to-speech voice reachable?")
        return 2

    snr_values: list[float | None] = []
    for token in args.snr.split(","):
        token = token.strip().lower()
        snr_values.append(None if token in ("clean", "none", "") else float(token))

    fallbacks = {"off": [False], "on": [True], "both": [False, True]}[args.fallback]

    print(f"\n{len(cases)} prompts x {len(snr_values)} noise levels x 2 gains "
          f"= {len(cases) * len(snr_values) * 2} clips per configuration")
    print(f"matcher: threshold {threshold}, token gate {gate} "
          f"(difficulty '{settings.difficulty}')\n")

    results = []
    choices = {"off": [False], "on": [True], "both": [False, True]}
    prompts = choices[args.prompt]
    vads = choices[args.vad]
    normalisers = choices[args.normalise]

    computes = ([c.strip() for c in args.compute.split(",") if c.strip()]
                if args.compute else [None])

    for model_size in [m.strip() for m in args.models.split(",") if m.strip()]:
      for compute in computes:
        for beam in [int(b) for b in args.beams.split(",") if b.strip()]:
          for fallback in fallbacks:
            for prompt in prompts:
              for vad in vads:
                for normalise in normalisers:
                  label = (f"{model_size}/{compute or settings.compute_type} b{beam} "
                         f"fb={'on' if fallback else 'off'} "
                         f"pr={'on' if prompt else 'off'} "
                         f"vad={'on' if vad else 'off'} "
                         f"norm={'on' if normalise else 'off'}")
                  print(f"-- {label}")
                  started = time.perf_counter()
                  try:
                    outcome = run_config(cases, model_size, beam, fallback,
                                         snr_values, threshold, gate, prompt,
                                         vad, normalise, compute)
                  except Exception as exc:  # noqa: BLE001
                    print(f"   FAILED: {exc}\n")
                    continue
                  outcome["label"] = label
                  outcome["wall_s"] = time.perf_counter() - started
                  results.append(outcome)
                  print(f"   would pass {outcome['pass_rate']:.1f}%  "
                        f"exact {outcome['exact_rate']:.1f}%  "
                        f"mean score {outcome['mean_score']:.0f}  "
                        f"median {outcome['median_ms']}ms  "
                        f"({outcome['wall_s']:.0f}s)")
                  for miss in outcome["misses"][:args.show_misses]:
                      print(f"     miss: {miss}")
                  print()

    if not results:
        return 1

    print("=" * 78)
    print(f"{'configuration':<34}{'pass':>8}{'exact':>8}{'score':>8}{'ms':>8}")
    for outcome in sorted(results, key=lambda r: -r["pass_rate"]):
        print(f"{outcome['label']:<34}{outcome['pass_rate']:>7.1f}%"
              f"{outcome['exact_rate']:>7.1f}%{outcome['mean_score']:>8.0f}"
              f"{outcome['median_ms']:>8}")
    best = max(results, key=lambda r: r["pass_rate"])
    print(f"\nbest: {best['label']}  ({best['pass_rate']:.1f}% would be understood)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
