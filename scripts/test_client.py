# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Exercise the full websocket challenge flow without Minecraft.

This is the Phase 1 acceptance test: it does exactly what the mod will do in
Phase 4 -- open a socket, declare a challenge, stream 16 kHz s16le PCM in
real time, and print the verdict.

    # does the server answer at all, and does silence correctly fail?
    python scripts/test_client.py --silence --expect fail

    # speak into your microphone for 4 seconds
    python scripts/test_client.py --item minecraft:diamond --mic 4

    # offline synthesized speech via Windows SAPI (no network, no GPU needed)
    python scripts/test_client.py --item minecraft:diamond --say-target --expect success

    # a word that is definitely wrong should be rejected
    python scripts/test_client.py --item minecraft:diamond --say "elephant sandwich" --expect fail

    # a pre-recorded file of any sample rate / channel count
    python scripts/test_client.py --item minecraft:apple --wav C:\\tmp\\manzana.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import websockets

SAMPLE_RATE = 16_000
CHUNK_MS = 50
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000


# ----------------------------------------------------------------------
# audio sources
# ----------------------------------------------------------------------
def load_wav(path: Path) -> bytes:
    """Any wav -> 16 kHz mono s16le."""
    with wave.open(str(path), "rb") as wf:
        channels, width, rate, frames = (
            wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        )
        raw = wf.readframes(frames)

    if width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise SystemExit(f"unsupported sample width: {width * 8} bit")

    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n_out = int(round(samples.size * SAMPLE_RATE / rate))
        samples = np.interp(
            np.linspace(0, samples.size - 1, n_out, dtype=np.float64),
            np.arange(samples.size),
            samples,
        ).astype(np.float32)
    print(f"  wav: {path.name}  {rate} Hz x{channels} {width*8}-bit -> "
          f"{samples.size / SAMPLE_RATE:.2f}s mono 16 kHz")
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def record_mic(seconds: float, device: int | None) -> bytes:
    try:
        import sounddevice as sd
    except ImportError:
        raise SystemExit("pip install sounddevice  (needed for --mic)")
    print(f"  recording {seconds:.1f}s -- speak now...")
    frames = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1,
        dtype="int16", device=device,
    )
    sd.wait()
    print("  done recording")
    return frames.tobytes()


_SAPI_SCRIPT = r"""
param([string]$TextFile, [string]$OutFile, [string]$Culture)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, `
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, `
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$voice = $null
if ($Culture) {
    $voice = $synth.GetInstalledVoices() | Where-Object {
        $_.VoiceInfo.Culture.Name -like "$Culture*"
    } | Select-Object -First 1
}
if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name); Write-Output ("voice: " + $voice.VoiceInfo.Name) }
else { Write-Output ("voice: default (" + $synth.Voice.Name + ") -- no $Culture voice installed") }
$synth.SetOutputToWaveFile($OutFile, $fmt)
$synth.Speak($text)
$synth.Dispose()
"""


def synth_wav(text: str, culture: str = "es") -> bytes:
    """Offline TTS through Windows SAPI. Text goes via a file so PowerShell
    never has to parse quotes or accents off the command line."""
    tmp = Path(tempfile.mkdtemp(prefix="lingua_tts_"))
    script, txt, wav = tmp / "say.ps1", tmp / "text.txt", tmp / "out.wav"
    script.write_text(_SAPI_SCRIPT, encoding="utf-8")
    txt.write_text(text, encoding="utf-8")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-TextFile", str(txt), "-OutFile", str(wav), "-Culture", culture],
        capture_output=True, text=True,
    )
    if proc.stdout.strip():
        print("  " + proc.stdout.strip())
    if not wav.is_file() or wav.stat().st_size < 1000:
        raise SystemExit(f"SAPI synthesis failed:\n{proc.stderr.strip()}")
    return load_wav(wav)


def silence(seconds: float) -> bytes:
    return b"\x00" * int(seconds * SAMPLE_RATE * 2)


# ----------------------------------------------------------------------
async def run(args: argparse.Namespace) -> int:
    url = f"ws://{args.host}:{args.port}/ws"
    print(f"connecting to {url}")
    async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
        hello = json.loads(await ws.recv())
        print(f"< hello  model={hello.get('model')} device={hello.get('device')} "
              f"lang={hello.get('target_lang')} threshold={hello.get('threshold')} "
              f"ready={hello.get('ready')}")
        if not hello.get("ready"):
            print("!! server says the model is not loaded yet; wait for the warmup log line")
            return 2

        challenge = {"type": "challenge", "id": "t1", "timeout_ms": args.timeout_ms}
        if args.key:
            challenge["translation_key"] = args.key
        else:
            challenge["item_id"] = args.item
        if args.threshold:
            challenge["threshold"] = args.threshold
        if args.trade:
            # Judged by a language model instead of matched against a string,
            # so there is no target to score towards -- any sensible way of
            # asking for the item counts.
            challenge["mode"] = "trade"
            challenge["context"] = "trade"
        await ws.send(json.dumps(challenge))

        ready = json.loads(await ws.recv())
        if ready.get("type") == "error":
            print(f"< error  {ready.get('code')}: {ready.get('message')}")
            return 2
        target = ready["target"]
        print(f"< challenge_ready  say {target!r}  (en: {ready.get('english')})  "
              f"within {ready['timeout_ms']} ms")

        # Build the audio only now, so --mic prompts after the target is known.
        if args.silence:
            pcm = silence(args.timeout_ms / 1000 + 0.5)
        elif args.wav:
            pcm = load_wav(Path(args.wav))
        elif args.mic:
            pcm = record_mic(args.mic, args.device)
        elif args.say_target:
            pcm = synth_wav(target, args.culture)
        elif args.say:
            pcm = synth_wav(args.say, args.culture)
        else:
            raise SystemExit("pick one of --silence / --wav / --mic / --say / --say-target")

        verdict: dict | None = None
        partials = 0
        stream_done = asyncio.Event()

        async def pump() -> None:
            """Real-time paced so the server's speculative passes behave the
            way they will with a live microphone."""
            t0 = time.perf_counter()
            for i in range(0, len(pcm), CHUNK_BYTES):
                if stream_done.is_set():
                    return
                await ws.send(pcm[i : i + CHUNK_BYTES])
                if not args.fast:
                    target_t = (i + CHUNK_BYTES) / (SAMPLE_RATE * 2)
                    await asyncio.sleep(max(0.0, target_t - (time.perf_counter() - t0)))
            if not stream_done.is_set():
                await ws.send(json.dumps({"type": "end", "id": "t1"}))

        pump_task = asyncio.create_task(pump())
        t0 = time.perf_counter()
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                dt = (time.perf_counter() - t0) * 1000
                if msg["type"] == "partial":
                    # Show what the model has settled on versus what it is
                    # still revising -- the split the overlay renders in two
                    # colours.
                    full = msg["transcript"]
                    stable = msg.get("stable", "")
                    if stable and full.startswith(stable):
                        shown = f"{stable}│{full[len(stable):]}"
                    else:
                        shown = f"│{full}"
                    partials += 1
                    print(f"< partial  [{dt:6.0f} ms] {shown!r} "
                          f"score={msg['confidence']}")
                elif msg["type"] == "attempt":
                    print(f"< attempt  [{dt:6.0f} ms] #{msg.get('attempt')} "
                          f"{msg['transcript']!r} score={msg['confidence']}"
                          f"  -- scored on its own, next try starts clean")
                elif msg["type"] == "result":
                    verdict = msg
                    stream_done.set()
                    break
                elif msg["type"] == "error":
                    print(f"< error  {msg.get('code')}: {msg.get('message')}")
                    stream_done.set()
                    return 2
        except asyncio.TimeoutError:
            print("!! no verdict within 30 s")
            return 2
        finally:
            stream_done.set()
            pump_task.cancel()

    print()
    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    status = verdict["status"]
    icon = "+" if status == "success" else "x"
    print(f"\n{icon} {status.upper()}  ({verdict['reason']})  "
          f"confidence={verdict['confidence']}  heard={verdict['transcript']!r}  "
          f"wanted={verdict['target']!r}  in {verdict['elapsed_ms']} ms")
    if verdict["audio_ms"]:
        rate = partials / (verdict["audio_ms"] / 1000)
        print(f"  live transcript: {partials} update(s) over "
              f"{verdict['audio_ms'] / 1000:.1f}s of audio ({rate:.1f}/s)")

    if args.expect and args.expect != status:
        print(f"\nFAILED EXPECTATION: wanted {args.expect}, got {status}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8760)
    ap.add_argument("--item", default="minecraft:diamond", help="item id")
    ap.add_argument("--key", default=None, help="translation key (overrides --item)")
    ap.add_argument("--timeout-ms", type=int, default=6000)
    ap.add_argument("--threshold", type=int, default=None)
    ap.add_argument("--trade", action="store_true",
                    help="negotiate like a villager: a whole sentence, judged by the model")

    src = ap.add_argument_group("audio source")
    src.add_argument("--silence", action="store_true", help="stream digital silence")
    src.add_argument("--wav", default=None)
    src.add_argument("--mic", type=float, default=None, metavar="SECONDS")
    src.add_argument("--device", type=int, default=None, help="sounddevice input index")
    src.add_argument("--say", default=None, metavar="TEXT", help="synthesize TEXT via Windows SAPI")
    src.add_argument("--say-target", action="store_true", help="synthesize the correct answer")
    src.add_argument("--culture", default="es", help="SAPI voice culture prefix")

    ap.add_argument("--fast", action="store_true", help="skip real-time pacing")
    ap.add_argument("--expect", choices=["success", "fail"], default=None,
                    help="exit non-zero unless the verdict matches")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return 0

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
