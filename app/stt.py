# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""faster-whisper wrapper.

A single WhisperModel instance guarded by a lock. One player means one
utterance at a time, so serialising inference costs nothing and keeps VRAM
flat. All calls go through `asyncio.to_thread` from the websocket layer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import cuda_dlls  # noqa: F401  -- must precede the ctranslate2 import
from .config import Settings

log = logging.getLogger(__name__)


@dataclass
class Transcript:
    text: str
    language: str
    language_probability: float
    no_speech_prob: float
    avg_logprob: float
    duration_s: float
    infer_ms: int
    rms: float
    rejected: str | None = None  # set when gated out before/after decoding


# A sentence of context, in the target language, prepended to every decode.
#
# Short utterances are where this system lives, and a lone word gives whisper
# almost nothing to condition on -- measured, it answered "Pan" with "¡BUN!"
# and "Flecha" with "Fletcher", drifting into English despite the language
# being forced. A line of ordinary prose in the right language pulls it back.
#
# Deliberately *prose*, and deliberately naming no items. A bare vocabulary
# list changes whisper's output style (it starts dropping punctuation and
# mimicking the list), and naming the words it might hear would be leaking
# the answer into the thing that is supposed to be judging it.
_CONTEXT_PROMPTS: dict[str, str] = {
    "es": "Estoy jugando a Minecraft y digo en voz alta el nombre de un objeto.",
    "fr": "Je joue à Minecraft et je dis à voix haute le nom d'un objet.",
    "pt": "Estou jogando Minecraft e digo em voz alta o nome de um objeto.",
    "it": "Sto giocando a Minecraft e dico ad alta voce il nome di un oggetto.",
    "de": "Ich spiele Minecraft und sage den Namen eines Gegenstands laut.",
    "nl": "Ik speel Minecraft en zeg de naam van een voorwerp hardop.",
    "ru": "Я играю в Майнкрафт и вслух называю предмет.",
    "ja": "マインクラフトをしながら、アイテムの名前を声に出して言います。",
    "ko": "마인크래프트를 하면서 아이템 이름을 소리내어 말합니다.",
    "zh": "我在玩我的世界，大声说出一个物品的名字。",
}


def context_prompt(language: str | None) -> str | None:
    base = (language or "").split("_")[0].split("-")[0].lower()
    return _CONTEXT_PROMPTS.get(base)


def pcm_to_float32(raw: bytes) -> np.ndarray:
    """s16le bytes -> float32 mono in [-1, 1]."""
    if len(raw) % 2:
        raw = raw[:-1]
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def normalise_level(samples: np.ndarray, target: float) -> np.ndarray:
    """Bring a quiet take up to a workable level before decoding.

    Input level is the thing players get wrong most and cannot see themselves
    getting wrong -- a headset at a third of the gain is still perfectly
    intelligible to a person and measurably worse to the model. Gain is
    capped so that a near-silent buffer is not amplified into pure hiss, and
    a peak limiter keeps a loud take from clipping into distortion.
    """
    level = rms(samples)
    if level <= 1e-6:
        return samples
    gain = min(target / level, MAX_NORMALISE_GAIN)
    if gain <= 1.0:
        return samples
    boosted = samples * gain
    peak = float(np.max(np.abs(boosted))) if boosted.size else 0.0
    if peak > 0.99:
        boosted *= 0.99 / peak
    return boosted.astype(np.float32)


#: Never amplify by more than this. Past it the signal is noise anyway.
MAX_NORMALISE_GAIN = 8.0


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


class SttEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()
        self.device = settings.device
        self.compute_type = settings.compute_type

    # ------------------------------------------------------------------
    def load(self) -> None:
        from faster_whisper import WhisperModel

        s = self.settings
        attempts = [(s.device, s.compute_type)]
        if s.device == "cuda":
            # If the GPU path is unusable (wrong ctranslate2 build for this
            # card, driver mismatch), int8 on CPU still turns a single word
            # around fast enough to play with.
            attempts.append(("cpu", "int8"))

        last_error: Exception | None = None
        for device, compute_type in attempts:
            try:
                log.info("loading whisper '%s' on %s/%s", s.model_size, device, compute_type)
                t0 = time.perf_counter()
                model = WhisperModel(
                    s.model_size,
                    device=device,
                    compute_type=compute_type,
                    download_root=s.download_root,
                )
                self._model = model
                self.device = device
                self.compute_type = compute_type
                log.info("model loaded in %.1fs", time.perf_counter() - t0)
                self._warmup()
                return
            except Exception as exc:  # noqa: BLE001 - we genuinely want any failure
                last_error = exc
                log.error("load/warmup failed on %s/%s: %s", device, compute_type, exc)
                self._model = None
        raise RuntimeError(f"could not initialise whisper: {last_error}")

    def _warmup(self) -> None:
        """Force a real decode so a broken GPU path fails here, not mid-game.

        VAD must be off for this: it rejects the synthetic tone before any
        kernel launches, which means cuBLAS is never loaded and a missing
        cublas64_12.dll goes unnoticed until the first player speaks. The
        warmup also absorbs first-call kernel autotuning.
        """
        t0 = time.perf_counter()
        tone = (
            0.05
            * np.sin(
                2 * np.pi * 220 * np.arange(self.settings.sample_rate, dtype=np.float32)
                / self.settings.sample_rate
            )
        ).astype(np.float32)
        saved = self.settings.vad_filter
        self.settings.vad_filter = False
        try:
            segments, _ = self._decode(tone)
            list(segments)  # generator: nothing runs until it is consumed
        finally:
            self.settings.vad_filter = saved
        log.info("warmup decode: %.0f ms", (time.perf_counter() - t0) * 1000)

    @property
    def ready(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------
    def _decode(self, samples: np.ndarray, hotwords: str | None = None,
                language: str | None = None):
        s = self.settings
        spoken = (language or s.whisper_language) or None
        return self._model.transcribe(
            samples,
            # Per call, not per server: the mod chooses the language for each
            # challenge, so one backend serves whatever the player picks.
            language=spoken,
            initial_prompt=context_prompt(spoken) if s.context_prompt else None,
            beam_size=s.beam_size,
            # A single fixed temperature means a decode that trips whisper's
            # own confidence checks is simply accepted as-is. The ladder lets
            # it try again, and only runs when the first pass was poor.
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0) if s.temperature_fallback else 0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=s.vad_filter,
            vad_parameters={"min_silence_duration_ms": 250},
            hotwords=hotwords,
        )

    def transcribe(self, raw_pcm: bytes, hotwords: str | None = None,
                   language: str | None = None) -> Transcript:
        """Blocking. Call via asyncio.to_thread."""
        s = self.settings
        samples = pcm_to_float32(raw_pcm)
        duration = samples.size / s.sample_rate
        level = rms(samples)

        empty = Transcript(
            text="",
            language=s.whisper_language,
            language_probability=0.0,
            no_speech_prob=1.0,
            avg_logprob=-10.0,
            duration_s=duration,
            infer_ms=0,
            rms=level,
        )

        if samples.size == 0:
            empty.rejected = "no_audio"
            return empty
        if level < s.silence_rms:
            # Cheapest and most effective hallucination guard there is.
            empty.rejected = "silence"
            return empty

        decoded = normalise_level(samples, s.target_rms) if s.normalise_input else samples

        t0 = time.perf_counter()
        with self._lock:
            segments, info = self._decode(decoded, hotwords=hotwords, language=language)
            collected = list(segments)
        infer_ms = int((time.perf_counter() - t0) * 1000)

        text = " ".join(seg.text.strip() for seg in collected).strip()
        if collected:
            no_speech = max(seg.no_speech_prob for seg in collected)
            avg_logprob = max(seg.avg_logprob for seg in collected)
        else:
            no_speech, avg_logprob = 1.0, -10.0

        result = Transcript(
            text=text,
            language=getattr(info, "language", s.whisper_language) or s.whisper_language,
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            no_speech_prob=float(no_speech),
            avg_logprob=float(avg_logprob),
            duration_s=duration,
            infer_ms=infer_ms,
            rms=level,
        )

        if not text:
            result.rejected = "empty"
        elif no_speech > s.no_speech_prob_max:
            result.rejected = "no_speech"
        elif avg_logprob < s.avg_logprob_min:
            result.rejected = "low_confidence"
        return result
