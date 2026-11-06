# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tune the difficulty thresholds against your own voice, mic and room.

Every other measurement in this project uses synthesised speech. This is the
only one that measures *you* -- your voice, your microphone, your room -- and
it is the number that actually decides whether the game feels fair.

It records a handful of words you *intend* to get right, reports how often you
would have been understood, and suggests thresholds that would sit just below
your worst fair attempt.

**It also prices the suggestion.** Tuning on attempts you meant to get right
is one-sided by construction: it can only ever argue for looser thresholds.
So the suggestion is run against the negative corpus from
scripts/tune_thresholds.py -- the most confusable *other* item spoken against
each target -- and the cost is printed next to it. A suggestion of 74/66 takes
wrong answers accepted from 2.1% to 17.1%, which you want to see before you
take it, not afterwards.

    python scripts/calibrate.py                  # 8 random words, 3 s each
    python scripts/calibrate.py -n 15 --seconds 4
    python scripts/calibrate.py --device 2       # specific input device

Run it with the server already up. Say each word the way you actually would in
game -- not extra-carefully, or you will tune for a voice you do not use.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env  # noqa: E402

load_env()  # before app.config, which reads the environment at import time

from scripts.test_client import SAMPLE_RATE, record_mic  # noqa: E402

# Deliberately mixed: short single words, and multi-word names that share a
# template prefix (where the token gate is the binding constraint).
DEFAULT_WORDS = [
    "item.minecraft.diamond",
    "item.minecraft.apple",
    "item.minecraft.stick",
    "item.minecraft.bread",
    "block.minecraft.stone",
    "item.minecraft.iron_ingot",
    "block.minecraft.diamond_block",
    "item.minecraft.diamond_pickaxe",
    "block.minecraft.crafting_table",
    "item.minecraft.gold_ingot",
    "block.minecraft.oak_planks",
    "item.minecraft.cooked_beef",
]


async def run(args: argparse.Namespace) -> int:
    url = f"ws://{args.host}:{args.port}/ws"
    keys = DEFAULT_WORDS[: args.n]
    rows: list[dict] = []

    async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
        hello = json.loads(await ws.recv())
        if not hello.get("ready"):
            print("server model not loaded yet")
            return 2
        print(f"model={hello['model']} device={hello['device']} lang={hello['target_lang']}")
        print(f"current settings: threshold={hello['threshold']}\n")

        for i, key in enumerate(keys, 1):
            # threshold 100 keeps all but perfect transcripts from resolving
            # early, so the full-utterance score is what gets measured.
            await ws.send(json.dumps({
                "type": "challenge", "id": f"c{i}", "translation_key": key,
                "timeout_ms": int(args.seconds * 1000) + 15_000, "threshold": 100,
            }))
            ready = json.loads(await ws.recv())
            while ready.get("type") == "partial":
                ready = json.loads(await ws.recv())
            if ready.get("type") == "error":
                print(f"[{i}/{len(keys)}] skip {key}: {ready.get('message')}")
                continue

            target = ready["target"]
            print(f"[{i}/{len(keys)}] say: {target!r}   (en: {ready.get('english')})")
            pcm = record_mic(args.seconds, args.device)

            chunk = SAMPLE_RATE * 2 * 50 // 1000
            for off in range(0, len(pcm), chunk):
                await ws.send(pcm[off : off + chunk])
            await ws.send(json.dumps({"type": "end", "id": f"c{i}"}))

            result = json.loads(await ws.recv())
            while result.get("type") == "partial":
                result = json.loads(await ws.recv())

            scores = (result.get("debug") or {}).get("scores") or {}
            row = {
                "target": target,
                "heard": result.get("transcript", ""),
                "score": result.get("confidence", 0),
                "coverage": scores.get("coverage"),
                "rms": (result.get("debug") or {}).get("rms"),
                "multiword": len(target.split()) > 1,
            }
            rows.append(row)
            cov = "n/a" if row["coverage"] is None else row["coverage"]
            print(f"          heard {row['heard']!r}  score={row['score']} coverage={cov}\n")

    if not rows:
        print("nothing recorded")
        return 1

    # ---------------------------------------------------------------
    print("=" * 72)
    print(f"{'target':28} {'heard':24} {'score':>5} {'cov':>5}")
    print("-" * 72)
    for r in rows:
        cov = "" if r["coverage"] is None else str(r["coverage"])
        print(f"{r['target'][:28]:28} {r['heard'][:24]:24} {r['score']:5} {cov:>5}")

    scores = [r["score"] for r in rows]
    covs = [r["coverage"] for r in rows if r["coverage"] is not None]
    quiet = [r for r in rows if (r["rms"] or 0) < 0.01]

    print("\nscore    min=%d median=%d max=%d" % (min(scores), statistics.median(scores), max(scores)))
    if covs:
        print("coverage min=%d median=%d max=%d" % (min(covs), statistics.median(covs), max(covs)))

    # Suggest thresholds a few points under the worst intended-correct attempt,
    # but never so low that genuine confusables (which land in the 60s) pass.
    sugg_threshold = max(70, min(90, min(scores) - 3))
    sugg_gate = max(62, min(80, min(covs) - 3)) if covs else None

    # How often you would have been understood, in the same terms the
    # benchmarks report, so this number can be compared with them.
    accepted = sum(1 for r in rows if r["score"] >= 80
                   and (r["coverage"] is None or r["coverage"] >= 70))
    print(f"\nat the shipped thresholds you would have been understood "
          f"{100.0 * accepted / len(rows):.0f}% of the time "
          f"({accepted} of {len(rows)})")
    print("for reference, six synthetic Spanish voices score 73-94%")

    print("\nsuggested:")
    print(f"  LEXIMINE_MATCH_THRESHOLD={sugg_threshold}")
    if sugg_gate is not None:
        print(f"  LEXIMINE_TOKEN_GATE={sugg_gate}")

    # And what that suggestion would cost. Tuning on correct attempts alone
    # only ever argues for looser thresholds -- the reject side has to be
    # priced at the same time or this quietly recommends a setting that waves
    # wrong answers through.
    report_leak_cost(sugg_threshold, sugg_gate if sugg_gate is not None else 70)

    worst = min(rows, key=lambda r: r["score"])
    if worst["score"] < 80:
        print(
            f"\n!! {worst['target']!r} only scored {worst['score']} "
            f"(heard {worst['heard']!r})."
        )
        print("   If that was a fair attempt, add the heard text to CASES in")
        print("   scripts/test_matching.py before lowering anything, so the")
        print("   change is held against the confusable pairs too.")
    if quiet:
        print(f"\n!! {len(quiet)} recording(s) were very quiet (rms < 0.01).")
        print("   Raise mic input gain, or lower LEXIMINE_SILENCE_RMS.")
    print("\nAfter editing .env, restart the server and re-run")
    print("scripts/test_matching.py to confirm the confusables still fail.")
    return 0


def report_leak_cost(threshold: int, gate: int) -> None:
    """What a suggested loosening would let through.

    Uses the negative corpus `scripts/tune_thresholds.py` caches -- the most
    confusable *other* item spoken against each target. Without this the
    suggestion above is one-sided by construction: it is derived purely from
    attempts the speaker meant to get right, so it can only ever argue for
    lower thresholds.
    """
    from app.config import settings
    from app.matching import match

    # The six-voice cache when it exists, because a loosening is priced badly
    # by a single speaker: the voices that lose accuracy are also the ones
    # whose transcripts drift closest to a different item.
    folder = Path(settings.tts_cache_dir).parent
    caches = sorted(folder.glob("tune_cache*.json"))
    if not caches:
        print("\n   (run scripts/tune_thresholds.py once to price the reject")
        print("    side of this suggestion against the confusable pairs)")
        return

    negatives: list[dict] = []
    for cache in caches:
        try:
            rows = json.loads(cache.read_text(encoding="utf8"))["negatives"]
        except Exception:  # noqa: BLE001
            continue
        if len(rows) > len(negatives):
            negatives = rows
    if not negatives:
        return

    # The vocabulary, because the server passes it: short names are decided
    # against the other short names, and leaving it out here would price a
    # matcher nobody runs.
    from app.lang import DictionaryRegistry

    dictionary = DictionaryRegistry(
        Path(settings.lang_dir), settings.target_lang.lower(),
        mc_root=Path(settings.mc_root) if settings.mc_root else None,
        mc_version=settings.mc_version)
    dictionary.load_default()

    def wrong_rate(t: int, g: int) -> float:
        hits = sum(1 for row in negatives
                   if match(row["heard"], row["target"], threshold=t, token_gate=g,
                            language=row["lang"],
                            key_tokens=tuple(row["key_tokens"]) or None,
                            rivals=dictionary.rivals_for(row["target"],
                                                         row["lang"])).passed)
        return 100.0 * hits / len(negatives)

    now = wrong_rate(80, 70)
    then = wrong_rate(threshold, gate)
    print(f"\n   wrong answers accepted, {'shipped 80/70':<16}"
          f"{now:>6.1f}%   ({len(negatives)} confusable pairs)")
    print(f"   wrong answers accepted, {f'suggested {threshold}/{gate}':<16}"
          f"{then:>6.1f}%")
    if then > now + 2.0:
        print("   !! that is a real loosening. Worth re-recording a few words")
        print("      before taking it -- a bad take should fail.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8760)
    ap.add_argument("-n", type=int, default=8, help="how many words")
    ap.add_argument("--seconds", type=float, default=3.0, help="recording window per word")
    ap.add_argument("--device", type=int, default=None, help="sounddevice input index")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
