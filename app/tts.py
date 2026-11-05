# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Speak the target word back, so a miss is a lesson rather than a buzzer.

Two engines, tried in order, because neither is good enough alone:

1. **edge-tts** -- Microsoft's neural voices, free, 100+ languages, genuinely
   native pronunciation. Needs the network.
2. **Windows SAPI** -- offline, but only for languages whose voice is actually
   installed. This machine has no Spanish voice, so SAPI would read Spanish
   with English phonetics, which is worse than silence when the whole point is
   showing the player how the word sounds.

Everything is cached to disk on first use, so a word you keep missing is
instant and works offline afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^a-z0-9_]+")


def to_bcp47(minecraft_lang: str) -> str:
    """es_es -> es-ES, pt_br -> pt-BR, zh_cn -> zh-CN."""
    parts = (minecraft_lang or "en_us").split("_")
    if len(parts) == 1:
        return parts[0].lower()
    return f"{parts[0].lower()}-{parts[1].upper()}"


class Pronouncer:
    def __init__(self, cache_dir: Path, sample_rate: int = 16_000) -> None:
        self.cache_dir = cache_dir
        self.sample_rate = sample_rate
        self._voices: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._edge_unavailable = False

    # ------------------------------------------------------------------
    def cache_path(self, lang: str, text: str) -> Path:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        safe_lang = _SAFE.sub("", lang.lower()) or "unknown"
        return self.cache_dir / safe_lang / f"{digest}.wav"

    async def wav_for(self, lang: str, text: str) -> Path | None:
        """Path to a wav of `text` spoken in `lang`, or None if impossible."""
        text = (text or "").strip()
        if not text:
            return None

        path = self.cache_path(lang, text)
        if path.is_file() and path.stat().st_size > 1000:
            return path

        async with self._lock:
            # Another request may have produced it while we waited.
            if path.is_file() and path.stat().st_size > 1000:
                return path
            path.parent.mkdir(parents=True, exist_ok=True)

            if await self._synth_edge(lang, text, path):
                return path
            if self._synth_sapi(lang, text, path):
                return path
        log.warning("no text-to-speech engine could say %r in %s", text, lang)
        return None

    # ------------------------------------------------------------------
    async def _voices_for(self, lang: str) -> list[str]:
        """Candidate voices for a language, best first.

        Several are returned on purpose: the service intermittently answers
        "No audio was received" for a particular voice, and the next one
        usually works. Russian failed exactly this way on the first run.
        """
        locale = to_bcp47(lang)
        cached = self._voices.get(locale)
        if cached:
            return cached.split(",")
        try:
            import edge_tts
        except ImportError:
            self._edge_unavailable = True
            return []
        try:
            manager = await edge_tts.VoicesManager.create()
        except Exception as exc:  # noqa: BLE001 - offline, DNS, anything
            log.info("edge-tts voice list unavailable (%s); falling back", exc)
            self._edge_unavailable = True
            return []

        matches = manager.find(Locale=locale)
        if not matches:
            # No exact region: any voice for the language still pronounces it
            # far better than a wrong-language voice would.
            matches = manager.find(Language=locale.split("-")[0])
        names = [match["ShortName"] for match in matches][:4]
        if names:
            self._voices[locale] = ",".join(names)
            log.info("pronunciation voices for %s: %s", lang, ", ".join(names))
        return names

    async def _synth_edge(self, lang: str, text: str, dest: Path) -> bool:
        if self._edge_unavailable:
            return False
        voices = await self._voices_for(lang)
        if not voices:
            return False
        try:
            import edge_tts
        except ImportError:
            return False

        tmp_mp3 = dest.with_suffix(".mp3")
        for voice in voices:
            try:
                await edge_tts.Communicate(text, voice).save(str(tmp_mp3))
            except Exception as exc:  # noqa: BLE001
                log.info("edge-tts voice %s failed for %r (%s)", voice, text, exc)
                tmp_mp3.unlink(missing_ok=True)
                continue

            if self._mp3_to_wav(tmp_mp3, dest):
                tmp_mp3.unlink(missing_ok=True)
                # Remember the voice that actually worked for this locale.
                self._voices[to_bcp47(lang)] = voice
                return True
            tmp_mp3.unlink(missing_ok=True)
        return False

    def _mp3_to_wav(self, source: Path, dest: Path) -> bool:
        """Decode to the 16 kHz mono wav the mod can play without a codec.

        Uses PyAV, which faster-whisper already depends on, so this adds no
        new requirement. ffmpeg is used if it happens to be on PATH.
        """
        try:
            import av
        except ImportError:
            return self._mp3_to_wav_ffmpeg(source, dest)

        try:
            import numpy as np

            with av.open(str(source)) as container:
                resampler = av.audio.resampler.AudioResampler(
                    format="s16", layout="mono", rate=self.sample_rate)
                chunks: list[bytes] = []
                for frame in container.decode(audio=0):
                    for resampled in resampler.resample(frame):
                        chunks.append(resampled.to_ndarray().astype(np.int16).tobytes())
                # Flush whatever the resampler is still holding.
                for resampled in resampler.resample(None):
                    chunks.append(resampled.to_ndarray().astype(np.int16).tobytes())

            pcm = self._trim_silence(b"".join(chunks))
            if len(pcm) < 1000:
                return False
            with wave.open(str(dest), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(self.sample_rate)
                out.writeframes(pcm)
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("PyAV decode failed (%s); trying ffmpeg", exc)
            return self._mp3_to_wav_ffmpeg(source, dest)

    def _trim_silence(self, pcm: bytes, threshold: float = 0.01,
                      pad_ms: int = 60) -> bytes:
        """Cut the dead air off both ends.

        The service pads a one-word clip out to roughly two seconds, most of
        it silence. Played back after a miss that reads as lag -- the player
        hears nothing and assumes it is broken -- so the padding goes.
        """
        try:
            import numpy as np
        except ImportError:
            return pcm

        samples = np.frombuffer(pcm, dtype="<i2")
        if samples.size == 0:
            return pcm
        window = max(1, self.sample_rate // 100)
        usable = samples[: samples.size - samples.size % window]
        if usable.size == 0:
            return pcm
        blocks = usable.reshape(-1, window).astype(np.float32) / 32768.0
        loud = np.sqrt(np.mean(blocks ** 2, axis=1)) > threshold
        if not loud.any():
            return pcm

        pad = max(1, (pad_ms * self.sample_rate // 1000) // window)
        first = max(0, int(np.argmax(loud)) - pad)
        last = min(len(loud), len(loud) - int(np.argmax(loud[::-1])) + pad)
        return usable[first * window: last * window].tobytes()

    def _mp3_to_wav_ffmpeg(self, source: Path, dest: Path) -> bool:
        if not shutil.which("ffmpeg"):
            return False
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-ar", str(self.sample_rate),
             "-ac", "1", "-f", "wav", str(dest)],
            capture_output=True, check=False)
        return result.returncode == 0 and dest.is_file()

    # ------------------------------------------------------------------
    _SAPI_SCRIPT = r"""
param([string]$TextFile, [string]$OutFile, [string]$Culture)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object {
    $_.VoiceInfo.Culture.Name -like "$Culture*"
} | Select-Object -First 1
if (-not $voice) { Write-Output "NOVOICE"; exit 1 }
$synth.SelectVoice($voice.VoiceInfo.Name)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, `
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, `
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile($OutFile, $fmt)
$synth.Speak($text)
$synth.Dispose()
"""

    def _synth_sapi(self, lang: str, text: str, dest: Path) -> bool:
        """Offline, but only when a voice for that exact language exists.

        Refusing to fall back to the default voice is deliberate: an English
        voice reading Spanish teaches the wrong pronunciation, which is worse
        than playing nothing at all.
        """
        culture = to_bcp47(lang).split("-")[0]
        work = Path(tempfile.mkdtemp(prefix="lingua_tts_"))
        try:
            script = work / "say.ps1"
            text_file = work / "text.txt"
            script.write_text(self._SAPI_SCRIPT, encoding="utf-8")
            text_file.write_text(text, encoding="utf-8")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(script), "-TextFile", str(text_file),
                 "-OutFile", str(dest), "-Culture", culture],
                capture_output=True, text=True, check=False)
            if "NOVOICE" in (result.stdout or ""):
                log.info("no installed %s voice for offline pronunciation", culture)
                return False
            return dest.is_file() and dest.stat().st_size > 1000
        except Exception as exc:  # noqa: BLE001
            log.info("SAPI synthesis failed (%s)", exc)
            return False
        finally:
            shutil.rmtree(work, ignore_errors=True)
