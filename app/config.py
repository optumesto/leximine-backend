# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration for the Lingua STT backend.

Everything is overridable through environment variables (see .env.example).
Plain dataclass instead of pydantic-settings: one less dependency, and the
defaults below are already tuned for an RTX 5060 Ti.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Audio contract with the Java client. Changing these means changing the
# AudioFormat in the mod too -- see PROTOCOL.md.
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes, signed 16-bit little-endian
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH


def _s(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _i(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw in (None, "") else int(raw)


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw in (None, "") else float(raw)


def _b(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- server ---------------------------------------------------------
    host: str = field(default_factory=lambda: _s("LINGUA_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _i("LINGUA_PORT", 8760))
    log_level: str = field(default_factory=lambda: _s("LINGUA_LOG_LEVEL", "INFO"))

    # --- whisper --------------------------------------------------------
    # "small" keeps the round trip under ~250 ms, which is what makes the
    # game feel responsive. "medium" / "large-v3" are more accurate on
    # mangled pronunciation but add latency; 16 GB of VRAM fits any of them.
    model_size: str = field(default_factory=lambda: _s("LINGUA_MODEL", "small"))
    device: str = field(default_factory=lambda: _s("LINGUA_DEVICE", "cuda"))
    compute_type: str = field(default_factory=lambda: _s("LINGUA_COMPUTE_TYPE", "float16"))
    download_root: str = field(default_factory=lambda: _s("LINGUA_MODEL_DIR", str(ROOT / "models")))
    # 5, not 1. Greedy decoding was costing about six points of exact
    # transcription and a point and a half of pass rate for three
    # milliseconds -- measured over 252 clips with scripts/benchmark_asr.py.
    beam_size: int = field(default_factory=lambda: _i("LINGUA_BEAM_SIZE", 5))
    # Retry a bad decode at rising temperatures instead of accepting it.
    # Costs nothing on a clean utterance -- the retry only happens when the
    # first pass trips whisper's own confidence checks -- and rescues the
    # mumbled ones. Measure with scripts/benchmark_asr.py before changing.
    temperature_fallback: bool = field(
        default_factory=lambda: _b("LINGUA_TEMPERATURE_FALLBACK", False))
    # Prepend a sentence of context in the target language, so a lone word
    # has something to be conditioned on. See _CONTEXT_PROMPTS in stt.py.
    #
    # **Off**, and measured: no gain on the pass rate and ten points off
    # exact transcription over 252 clips. An early 132-clip run said it won
    # by 6.8 points, which was small-sample noise. Kept because it is the
    # obvious thing to try and the numbers should be here when someone does.
    context_prompt: bool = field(
        default_factory=lambda: _b("LINGUA_CONTEXT_PROMPT", False))
    # Bring a quiet microphone up to a workable level before decoding.
    normalise_input: bool = field(
        default_factory=lambda: _b("LINGUA_NORMALISE_INPUT", False))
    target_rms: float = field(default_factory=lambda: _f("LINGUA_TARGET_RMS", 0.06))

    # Forced decode language. Empty string => autodetect (slower, and it
    # loves to guess wrong on a single word).
    whisper_language: str = field(default_factory=lambda: _s("LINGUA_WHISPER_LANG", ""))

    # --- dictionary -----------------------------------------------------
    # Fallback only: the mod sends the language it wants with each challenge,
    # so this is what gets used when it does not say.
    target_lang: str = field(default_factory=lambda: _s("LINGUA_TARGET_LANG", "es_es"))
    lang_dir: str = field(default_factory=lambda: _s("LINGUA_LANG_DIR", str(ROOT / "lang")))

    # Where to find language files that are not in lang/ yet. Blank means the
    # default %APPDATA%\.minecraft.
    mc_root: str = field(default_factory=lambda: _s("LINGUA_MC_ROOT", ""))
    mc_version: str = field(default_factory=lambda: _s("LINGUA_MC_VERSION", "1.20.4"))

    # Spoken examples played after a miss. Cached here on first use, so a word
    # you keep missing is instant and works offline afterwards.
    # --- villager trade judging ------------------------------------------
    # Any OpenAI-compatible endpoint. Ollama serves one at /v1, so a machine
    # that already runs Ollama needs nothing else.
    llm_base_url: str = field(
        default_factory=lambda: _s("LINGUA_LLM_URL", "http://127.0.0.1:11434/v1"))
    llm_model: str = field(default_factory=lambda: _s("LINGUA_LLM_MODEL", "gemma3:12b"))
    llm_timeout_s: float = field(default_factory=lambda: _f("LINGUA_LLM_TIMEOUT", 60.0))
    # How long Ollama holds the judge in VRAM after a call. Long enough to
    # survive the gaps between villagers in one session, short enough that it
    # lets go afterwards -- this machine has far larger models for other
    # projects, and pinning 7.5 GB indefinitely would push them off the card.
    # "-1" never releases it; "0" unloads immediately, which costs 30s on the
    # next trade.
    llm_keep_alive: str = field(
        default_factory=lambda: _s("LINGUA_LLM_KEEP_ALIVE", "2h"))
    # How often to re-check that the judge is still in VRAM while someone is
    # playing. One tiny request; it loads nothing unless the model has gone.
    llm_warm_interval_s: float = field(
        default_factory=lambda: _f("LINGUA_LLM_WARM_INTERVAL", 600.0))

    tts_cache_dir: str = field(
        default_factory=lambda: _s("LINGUA_TTS_CACHE", str(ROOT / "cache" / "tts")))

    # --- matching -------------------------------------------------------
    match_threshold: int = field(default_factory=lambda: _i("LINGUA_MATCH_THRESHOLD", 80))
    difficulty: str = field(default_factory=lambda: _s("LINGUA_DIFFICULTY", "normal"))
    # Minimum score for each *distinguishing* word of a multi-word item name.
    # This is what stops "lingote de oro" from being accepted as "Lingote de
    # hierro"; see matching._token_coverage. Lower it for lenient play.
    token_gate: int = field(default_factory=lambda: _i("LINGUA_TOKEN_GATE", 70))
    strip_accents: bool = field(default_factory=lambda: _b("LINGUA_STRIP_ACCENTS", True))
    strip_articles: bool = field(default_factory=lambda: _b("LINGUA_STRIP_ARTICLES", True))

    # --- streaming behaviour -------------------------------------------
    # How often to run a speculative pass over the audio collected so far.
    # A pass that already clears the threshold ends the challenge early,
    # so the player gets their item the instant they finish the word.
    # How often to re-read the attempt so far. This is what makes the live
    # transcript move: at 350 ms a one-second word produced about two updates,
    # which reads as a jump rather than as the model thinking. A pass costs
    # 60-150 ms on `small`, so 200 ms keeps the GPU comfortably ahead.
    partial_interval_ms: int = field(default_factory=lambda: _i("LINGUA_PARTIAL_INTERVAL_MS", 200))
    min_audio_ms: int = field(default_factory=lambda: _i("LINGUA_MIN_AUDIO_MS", 300))

    # --- splitting one attempt from the next ----------------------------
    # A pause this long ends an attempt: it is scored on its own and the next
    # try starts from silence. Without this, a second attempt is transcribed
    # together with the first and the two get muddled into one utterance.
    utterance_gap_ms: int = field(default_factory=lambda: _i("LINGUA_UTTERANCE_GAP_MS", 450))
    # Below this, a blip is noise rather than an attempt worth scoring.
    min_speech_ms: int = field(default_factory=lambda: _i("LINGUA_MIN_SPEECH_MS", 220))
    # Level at which a chunk counts as speech rather than room tone. Slightly
    # above silence_rms so a quiet room does not keep an attempt open forever.
    speech_rms: float = field(default_factory=lambda: _f("LINGUA_SPEECH_RMS", 0.012))
    # How much of the quiet run-up to keep in front of an attempt. Words do
    # not start at full volume, and recording from the first loud chunk cuts
    # the onset off: the same clip scored 71 clipped and 88 with it intact.
    preroll_ms: int = field(default_factory=lambda: _i("LINGUA_PREROLL_MS", 300))
    max_audio_ms: int = field(default_factory=lambda: _i("LINGUA_MAX_AUDIO_MS", 30_000))
    default_timeout_ms: int = field(default_factory=lambda: _i("LINGUA_DEFAULT_TIMEOUT_MS", 6_000))
    # Run one last pass when the clock hits zero instead of failing instantly.
    timeout_grace: bool = field(default_factory=lambda: _b("LINGUA_TIMEOUT_GRACE", True))

    # --- speech gating --------------------------------------------------
    # Below this RMS the buffer is treated as silence and never reaches
    # whisper, which is both faster and the single best hallucination guard.
    silence_rms: float = field(default_factory=lambda: _f("LINGUA_SILENCE_RMS", 0.0045))
    no_speech_prob_max: float = field(default_factory=lambda: _f("LINGUA_NO_SPEECH_MAX", 0.85))
    avg_logprob_min: float = field(default_factory=lambda: _f("LINGUA_AVG_LOGPROB_MIN", -1.25))
    vad_filter: bool = field(default_factory=lambda: _b("LINGUA_VAD", True))

    # --- difficulty knob ------------------------------------------------
    # Off by default on purpose: feeding the expected word to the decoder as
    # a hotword makes whisper *produce* that word from mush, which turns the
    # whole exercise into a participation trophy. Flip it on for easy mode.
    leak_target_to_decoder: bool = field(default_factory=lambda: _b("LINGUA_EASY_MODE", False))

    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    sample_width: int = SAMPLE_WIDTH

    def __post_init__(self) -> None:
        if not self.whisper_language:
            # "es_es" -> "es", "pt_br" -> "pt". Good enough for every
            # Minecraft language code; override the env var for the ones
            # where the region matters to you.
            self.whisper_language = self.target_lang.split("_")[0].lower()

    @property
    def min_audio_bytes(self) -> int:
        return int(self.min_audio_ms / 1000 * BYTES_PER_SECOND)

    @property
    def max_audio_bytes(self) -> int:
        return int(self.max_audio_ms / 1000 * BYTES_PER_SECOND)

    @property
    def preroll_bytes(self) -> int:
        return int(self.preroll_ms / 1000 * BYTES_PER_SECOND)


# (overall similarity, per-word gate). The mod names one of these per
# challenge; a single pair of numbers could not suit both a beginner and
# someone who already speaks the language.
DIFFICULTY_PRESETS: dict[str, tuple[int, int]] = {
    "lenient": (70, 60),
    "normal": (80, 70),
    "strict": (88, 80),
}


def difficulty_for(name: str | None, fallback: str = "normal") -> tuple[int, int]:
    key = (name or fallback).strip().lower()
    return DIFFICULTY_PRESETS.get(key, DIFFICULTY_PRESETS[fallback])


settings = Settings()
