# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Find the matcher's best operating point, from both kinds of error.

Everything measured so far answers one half of the question: how often is a
player who said the right word accepted. On its own that is a useless target
-- a threshold of zero scores 100%. The other half is how often a player who
said the *wrong* word is accepted too, and nothing has ever measured it.

So this builds both corpora:

  positives   the target word, spoken, degraded
  negatives   a *different* item spoken against that target, chosen to be the
              most confusable other name in the dictionary -- not a random
              one, because rejecting "madera" when you wanted "diamante" is
              not the test that matters

Audio is decoded once and the transcripts cached, then thresholds are swept
purely in the matcher. That makes a thousand operating points cost about as
much as one.

    python scripts/tune_thresholds.py
    python scripts/tune_thresholds.py --snr clean,18,10 --out run/roc.csv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402

load_env()  # before app.config, which reads the environment at import time

from app.lang import DictionaryRegistry, whisper_language_for  # noqa: E402
from app.matching import match, normalize  # noqa: E402
from app.stt import SttEngine, pcm_to_float32  # noqa: E402
from app.tts import Pronouncer  # noqa: E402
from app.config import settings  # noqa: E402

from benchmark_asr import CORPUS_ITEMS, degrade, load_wav, to_pcm  # noqa: E402
from benchmark_voices import DEFAULT_VOICES, say  # noqa: E402

CACHE_DIR = Path(settings.tts_cache_dir).parent


def cache_path(voices: list[str]) -> Path:
    """One cache per voice set, so the single-voice numbers stay reproducible."""
    if not voices:
        return CACHE_DIR / "tune_cache.json"
    return CACHE_DIR / f"tune_cache_{len(voices)}voice.json"


def confusable_with(name: str, pool: list[tuple[str, str]]) -> str | None:
    """The other item most likely to be mistaken for this one, by key.

    A hard negative, deliberately. Any threshold rejects "madera" when the
    answer was "diamante"; the question is whether it rejects the name that
    actually sounds like it.

    Several kinds of candidate are refused, because accepting them is not a
    bug and counting them as leaks poisons the measurement:

    * **The plural of the same word.** "Zanahorias" for "Zanahoria" is the
      same item, and the first version of this counted every one as a false
      accept.
    * **One name contained in the other.** At tier 3 the target is a sentence
      built around the item, so the bare item name scores well against it by
      design -- that is the documented leniency, not a leak.
    """
    from thefuzz import fuzz

    target = normalize(name, language="es_es")
    stem = target.rstrip("s")
    best, best_score = None, -1
    for key, other in pool:
        other_norm = normalize(other, language="es_es")
        if other_norm == target or other_norm.rstrip("s") == stem:
            continue
        if other_norm in target or target in other_norm:
            continue
        if not 0.6 <= len(other_norm) / max(1, len(target)) <= 1.6:
            continue
        score = fuzz.ratio(target, other_norm)
        if score > best_score:
            best, best_score = key, score
    return best


async def build(dictionary: DictionaryRegistry, lang: str, snr_values, gains,
                voices: list[str] | None = None):
    """Decode every clip once; return (positives, negatives) transcript rows.

    With `voices`, every prompt is spoken by each of them. The operating point
    was originally found against a single synthetic speaker, and the six-voice
    benchmark showed a 12-point spread between the easiest and hardest -- so a
    threshold chosen on one voice is a threshold chosen on the easy case.
    """
    pronouncer = Pronouncer(Path(settings.tts_cache_dir), settings.sample_rate)
    table = dictionary.table(lang)
    pool = [(k, v) for k, v in table.items()
            if k.startswith(("item.", "block.")) and 3 <= len(v) <= 22]

    engine = SttEngine(settings)
    engine.load()
    rng = np.random.default_rng(99)

    positives: list[dict] = []
    negatives: list[dict] = []

    for item_id, tier in CORPUS_ITEMS:
        entry = dictionary.resolve(item_id=item_id, lang=lang, tier=tier,
                                   context="pickup", level=0)
        if not entry:
            continue
        # Resolved at the *same tier and context*, so a tier-3 negative is a
        # different item in the same sentence frame rather than a bare noun
        # against a sentence -- which is a different question entirely.
        impostor_key = confusable_with(entry.item_name, pool)
        impostor = (dictionary.resolve(translation_key=impostor_key, lang=lang,
                                       tier=tier, context="pickup", level=0)
                    if impostor_key else None)
        impostor_name = impostor.target if impostor else None

        # Checked on the *resolved prompt*, not the item name. "Tablones de
        # roble" and "Tablones de abeto" are different items that both reduce
        # to "Tablones" at tier 1, so the negative was the correct answer and
        # every one counted as a false accept. Same for a plural that only
        # appears after the name is shortened.
        if impostor_name:
            a = normalize(impostor_name, language=lang)
            b = normalize(entry.target, language=lang)
            if a == b or a.rstrip("s") == b.rstrip("s"):
                impostor_name = None

        for spoken, bucket in ((entry.target, positives),
                               (impostor_name, negatives)):
            if not spoken:
                continue
            for voice in (voices or [""]):
                if voice:
                    clip = Path(tempfile.gettempdir()) / "tune_voice.wav"
                    clip.unlink(missing_ok=True)
                    if not await say(spoken, voice, clip):
                        continue
                    path = clip
                else:
                    cached = await pronouncer.wav_for(lang, spoken)
                    if not cached:
                        continue
                    path = Path(cached)
                samples, rate = load_wav(path)
                if rate != settings.sample_rate or samples.size == 0:
                    continue
                for snr in snr_values:
                    for gain in gains:
                        audio = degrade(samples, snr, rng, gain)
                        transcript = engine.transcribe(
                            to_pcm(audio), None, whisper_language_for(lang))
                        bucket.append({
                            "target": entry.target,
                            "spoken": spoken,
                            "heard": transcript.text,
                            "lang": entry.lang,
                            "voice": voice or "default",
                            "key_tokens": list(entry.key_tokens or ()),
                        })
        print(f"  {entry.target:24} vs {impostor_name!r}")
    return positives, negatives


def scored(row: dict, threshold: int, gate: int,
           dictionary: DictionaryRegistry | None):
    """One row through the matcher the server actually runs.

    The vocabulary is passed in when there is one: short names are decided
    against the other short names, so a sweep that leaves it out is a sweep
    of a matcher nobody ships.
    """
    rivals = (dictionary.rivals_for(row["target"], row["lang"])
              if dictionary is not None else ())
    return match(row["heard"], row["target"], threshold=threshold,
                 token_gate=gate, language=row["lang"],
                 key_tokens=tuple(row["key_tokens"]) or None, rivals=rivals)


def rate(rows: list[dict], threshold: int, gate: int,
         dictionary: DictionaryRegistry | None = None) -> float:
    if not rows:
        return 0.0
    accepted = sum(1 for row in rows
                   if scored(row, threshold, gate, dictionary).passed)
    return 100.0 * accepted / len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snr", default="clean,18,10")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-decode instead of using the cached transcripts")
    ap.add_argument("--out", default=None, help="write the full sweep as CSV")
    ap.add_argument("--leaks", nargs=2, type=int, metavar=("THRESHOLD", "GATE"),
                    help="list the wrong answers accepted at this operating point")
    ap.add_argument("--voices", default="", nargs="?", const=",".join(DEFAULT_VOICES),
                    help="speak every prompt in each of these voices "
                         "(bare --voices uses all six); cached separately")
    args = ap.parse_args()

    lang = settings.target_lang.lower()
    snr_values = [None if s.strip().lower() in ("clean", "none") else float(s)
                  for s in args.snr.split(",")]
    gains = (1.0, 0.35)
    voices = [v.strip() for v in (args.voices or "").split(",") if v.strip()]
    CACHE = cache_path(voices)

    dictionary = DictionaryRegistry(
        Path(settings.lang_dir), lang,
        mc_root=Path(settings.mc_root) if settings.mc_root else None,
        mc_version=settings.mc_version)
    dictionary.load_default()

    if CACHE.is_file() and not args.rebuild:
        payload = json.loads(CACHE.read_text(encoding="utf8"))
        positives, negatives = payload["positives"], payload["negatives"]
        print(f"using {len(positives)} cached positives, {len(negatives)} negatives "
              f"(--rebuild to re-decode)\n")
    else:
        print("decoding the corpus once (cached afterwards)...")
        positives, negatives = asyncio.run(
            build(dictionary, lang, snr_values, gains, voices))
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(
            {"positives": positives, "negatives": negatives}), encoding="utf8")
        print(f"\n{len(positives)} positives, {len(negatives)} negatives\n")

    if args.leaks:
        threshold, gate = args.leaks
        print(f"wrong answers accepted at threshold {threshold}, gate {gate}:\n")
        seen: set[tuple[str, str, str]] = set()
        for row in negatives:
            result = scored(row, threshold, gate, dictionary)
            if not result.passed:
                continue
            signature = (row["spoken"], row["target"], row["heard"])
            if signature in seen:
                continue
            seen.add(signature)
            print(f"  said {row['spoken']!r:28} wanted {row['target']!r:28}")
            print(f"       heard {row['heard']!r:26} score {result.score} "
                  f"coverage {result.coverage}")
        print(f"\n{len(seen)} distinct leaks")
        return 0

    rows = []
    for threshold in range(60, 96, 2):
        for gate in range(50, 92, 4):
            accept = rate(positives, threshold, gate, dictionary)
            leak = rate(negatives, threshold, gate, dictionary)
            rows.append((threshold, gate, accept, leak, accept - leak))

    current = next(r for r in rows if r[0] == 80 and r[1] == 70)
    print(f"{'thr':>4}{'gate':>6}{'accept':>9}{'wrong-accepted':>16}{'margin':>9}")
    print(f"{current[0]:>4}{current[1]:>6}{current[2]:>8.1f}%{current[3]:>15.1f}%"
          f"{current[4]:>8.1f}   <- current")
    print()

    # Ranked by margin, but a configuration that waves wrong answers through
    # is not a better product however good its margin looks.
    for row in sorted(rows, key=lambda r: -r[4])[:12]:
        flag = "  <- current" if (row[0], row[1]) == (80, 70) else ""
        print(f"{row[0]:>4}{row[1]:>6}{row[2]:>8.1f}%{row[3]:>15.1f}%"
              f"{row[4]:>8.1f}{flag}")

    # Per voice, at the shipped point. An average hides the speaker the model
    # finds hardest, and that player is the one who gives up on the mod.
    by_voice = sorted({row.get("voice", "default") for row in positives})
    if len(by_voice) > 1:
        print("\nat the shipped operating point, per voice:")
        print(f"  {'voice':28}{'accepted':>10}{'wrong-accepted':>16}")
        for voice in by_voice:
            pos = [r for r in positives if r.get("voice") == voice]
            neg = [r for r in negatives if r.get("voice") == voice]
            print(f"  {voice:28}{rate(pos, 80, 70, dictionary):>9.1f}%"
                  f"{rate(neg, 80, 70, dictionary):>15.1f}%")

    strict = [r for r in rows if r[3] <= current[3]]
    if strict:
        best_strict = max(strict, key=lambda r: r[2])
        print(f"\nbest without letting more wrong answers through "
              f"(<= {current[3]:.1f}%):")
        print(f"  threshold {best_strict[0]}, gate {best_strict[1]}: "
              f"{best_strict[2]:.1f}% accepted, {best_strict[3]:.1f}% wrong")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("threshold,gate,accept,wrong_accepted,margin\n" + "\n".join(
            f"{t},{g},{a:.2f},{l:.2f},{m:.2f}" for t, g, a, l, m in rows),
            encoding="utf8")
        print(f"\nfull sweep -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
