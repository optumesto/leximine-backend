# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""One pronunciation challenge: audio buffering, speculative passes, verdict.

Lifecycle, driven entirely by the websocket layer:

    challenge  -> Challenge(...)  + start()
    <binary>   -> feed()          -> may finalise early on a passing pass
    end        -> request_final()
    (deadline) -> finalise as timeout

`finalize` is idempotent, so exactly one `result` frame is ever emitted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .config import Settings
from .lang import Entry, whisper_language_for
from . import matching
from .matching import MatchResult, match
from . import romanize
from .llm import TradeJudge
from .stats import stats
from .stt import SttEngine, pcm_to_float32, rms

log = logging.getLogger(__name__)

Sender = Callable[[dict[str, Any]], Awaitable[None]]


def _clip(text: str, limit: int = 160) -> str:
    """Display text only; the overlay cannot show more than this anyway.

    A decoder stuck in a repetition loop is cut right back rather than merely
    truncated. The matcher already refuses to score one, but the raw text was
    still going out for display, and a line reading "la ropa de la ropa de la
    ropa de la ropa..." tells the player nothing except that something is
    broken. Six words is enough to see it is babbling.
    """
    collapsed = text
    if matching.is_repetition_loop(matching.normalize(text)):
        words = text.split()
        collapsed = " ".join(words[:6]) + "..."
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + "..."


def _stable_prefix(previous: str, current: str) -> str:
    """The leading words two successive passes agree on.

    Whisper re-reads the whole attempt every pass and often revises what it
    produced before ("dia" -> "diamante" -> "el diamante"). The agreed prefix
    is the part worth drawing as settled.
    """
    if not previous or not current:
        return ""
    agreed = 0
    for before, after in zip(previous.split(), current.split()):
        if before.lower() != after.lower():
            break
        agreed += 1
    return " ".join(current.split()[:agreed])


class Challenge:
    def __init__(
        self,
        *,
        challenge_id: str,
        entry: Entry,
        timeout_ms: int,
        threshold: int,
        token_gate: int,
        mode: str,
        judge: TradeJudge | None,
        send: Sender,
        engine: SttEngine,
        settings: Settings,
        task: str = "",
    ) -> None:
        self.id = challenge_id
        self.entry = entry
        self.timeout_ms = timeout_ms
        self.threshold = threshold
        self.token_gate = token_gate
        # "word" matches a target; "trade" asks a language model whether the
        # sentence does its job. With no task that job is "ask a merchant for
        # this item"; a client can send one to reuse the judge for a sentence
        # that has nothing to do with trading.
        self.mode = mode
        self.task = task
        self._judge = judge
        if mode == "trade" and judge is not None:
            # Start pulling the weights into VRAM now, while the player is
            # still drawing breath.
            judge.warm()
        self._send = send
        self._engine = engine
        self._s = settings

        self._buffer = bytearray()
        self._started = time.monotonic()
        self._done = False
        self._inflight = False
        self._last_infer_at = 0.0
        self._last_partial_text = ""
        self._last_partial_raw = ""
        self._deadline_task: asyncio.Task | None = None
        self._pass_tasks: set[asyncio.Task] = set()

        # Attempt tracking. The buffer above is one attempt; these describe
        # where that attempt is up to and the best one seen so far, which is
        # what the player is judged on if they never quite nail it.
        self._speech_ms = 0
        self._silence_ms = 0
        self._attempt = 0
        self._closing = False
        self._preroll = bytearray()
        self._best: MatchResult | None = None
        self._best_transcript = None
        # The last trade attempt the model turned down, kept so a refusal
        # partway through still has something to report when the clock
        # eventually runs out. The last one rather than the best one: a word
        # match score against a one-word target says nothing useful about a
        # sentence, so "best" would pick between attempts at random.
        self._trade_note = ""
        self._trade_last: MatchResult | None = None
        self._trade_last_transcript = None

    # ------------------------------------------------------------------
    @property
    def done(self) -> bool:
        return self._done

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    @property
    def audio_ms(self) -> int:
        return int(len(self._buffer) / (self._s.sample_rate * self._s.sample_width) * 1000)

    @property
    def _hotwords(self) -> str | None:
        return self.entry.target if self._s.leak_target_to_decoder else None

    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._started = time.monotonic()
        self._deadline_task = asyncio.create_task(self._watch_deadline())
        await self._send(
            {
                "type": "challenge_ready",
                "id": self.id,
                "key": self.entry.key,
                "target": self.entry.target,
                "english": self.entry.english,
                # How to say it, in Latin letters, for scripts an English
                # reader cannot sound out. Empty for Spanish and friends,
                # where the spelling already is the instruction, and empty
                # for anything containing kanji -- see app/romanize.py.
                "reading": romanize.reading(self.entry.target, self.entry.lang),
                "lang": self.entry.lang,
                # The plain name travels alongside the prompt: at tier 3 the
                # target is a whole sentence, and the vocabulary list still
                # wants to record "Espada de diamante".
                "item_name": self.entry.item_name,
                "tier": self.entry.tier,
                # Which rung of the sentence pool this came from, so the
                # overlay can show that the asking is getting harder rather
                # than leaving it to be felt.
                "level": self.entry.level,
                "timeout_ms": self.timeout_ms,
                "threshold": self.threshold,
            }
        )

    async def feed(self, chunk: bytes) -> None:
        """Collect audio, split into attempts at the pauses between them.

        The buffer holds *one* attempt, not the whole challenge. Accumulating
        everything meant a second try was transcribed together with the first
        -- "diamante" after a muffed "dia" came back as one run-on utterance,
        so a good attempt could be dragged under by a bad one, and a long
        rambling buffer gave a wrong answer several extra chances to match
        something.
        """
        if self._done:
            return

        level = rms(pcm_to_float32(chunk))
        chunk_ms = int(len(chunk) / (self._s.sample_rate * self._s.sample_width) * 1000)

        if level >= self._s.speech_rms:
            if self._speech_ms == 0 and self._preroll:
                # Put the run-up back. A word does not begin at full volume --
                # the "D" of "Diamante" sits under the speech threshold -- so
                # starting the recording at the first loud chunk cuts the
                # onset off and whisper hears "Dmonte" instead. Measured: the
                # same clip scored 71 clipped and 88 with its onset intact.
                self._buffer.extend(self._preroll)
                self._preroll.clear()
            self._buffer.extend(chunk)
            self._speech_ms += chunk_ms
            self._silence_ms = 0
        elif self._speech_ms > 0:
            # Keep a little trailing silence: whisper decodes a word with room
            # after it more reliably than one cut off at the final consonant.
            self._buffer.extend(chunk)
            self._silence_ms += chunk_ms
        else:
            # Quiet, and nothing started yet. Hold the most recent moment of
            # it as the run-up for whatever is said next.
            self._preroll.extend(chunk)
            if len(self._preroll) > self._s.preroll_bytes:
                del self._preroll[: len(self._preroll) - self._s.preroll_bytes]
            return

        if len(self._buffer) > self._s.max_audio_bytes:
            del self._buffer[: len(self._buffer) - self._s.max_audio_bytes]

        if (not self._closing
                and self._speech_ms >= self._s.min_speech_ms
                and self._silence_ms >= self._s.utterance_gap_ms):
            # They stopped talking: score this attempt on its own. The flag is
            # set here rather than inside the task, because more audio arrives
            # before a spawned task gets to run and would otherwise queue a
            # second close for the same attempt.
            self._closing = True
            self._spawn(self._close_utterance())
            return

        self._maybe_speculate()

    async def _close_utterance(self) -> None:
        """Score one complete attempt, then start the next with a clean slate."""
        if self._done or not self._buffer:
            return
        snapshot = bytes(self._buffer)
        self._buffer.clear()
        self._preroll.clear()
        self._speech_ms = 0
        self._silence_ms = 0
        self._attempt += 1
        self._closing = False
        self._last_partial_raw = ""

        try:
            transcript = await asyncio.to_thread(
                self._engine.transcribe, snapshot, self._hotwords,
                whisper_language_for(self.entry.lang)
            )
        except Exception:  # noqa: BLE001
            log.exception("utterance pass failed for challenge %s", self.id)
            return
        if self._done:
            return

        if transcript.rejected:
            return
        if self.mode == "trade":
            # A trade is judged whole, not word by word, so a completed
            # attempt goes straight to the model.
            await self._judge_trade(transcript, "attempt", final=False)
            return
        result = self._score(transcript.text)
        self._remember_best(result, transcript)

        if result.passed:
            await self._finalize("success", "attempt", result, transcript_meta=transcript)
            return

        # Tell the player what that attempt was heard as, and that it is over,
        # so the next try starts from nothing rather than appending to it.
        await self._send({
            "type": "attempt",
            "id": self.id,
            "attempt": self._attempt,
            "transcript": transcript.text,
            "confidence": result.score,
            "remaining_ms": max(0, self.timeout_ms - self.elapsed_ms),
        })
        self._last_partial_text = ""

    def _remember_best(self, result: MatchResult, transcript) -> None:
        if self._best is None or result.score > self._best.score:
            self._best = result
            self._best_transcript = transcript

    async def request_final(self) -> None:
        """Player released push-to-talk: decide now on everything collected."""
        if self._done:
            return
        await self._final_pass("end")

    async def cancel(self, reason: str = "cancelled") -> None:
        await self._finalize("fail", reason, None)

    # ------------------------------------------------------------------
    def _maybe_speculate(self) -> None:
        if self._done or self._inflight:
            return
        if len(self._buffer) < self._s.min_audio_bytes:
            return
        now = time.monotonic()
        if (now - self._last_infer_at) * 1000 < self._s.partial_interval_ms:
            return
        self._inflight = True
        self._last_infer_at = now
        self._spawn(self._speculative_pass(bytes(self._buffer)))

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._pass_tasks.add(task)
        task.add_done_callback(self._pass_tasks.discard)

    async def _speculative_pass(self, snapshot: bytes) -> None:
        try:
            transcript = await asyncio.to_thread(
                self._engine.transcribe, snapshot, self._hotwords,
                whisper_language_for(self.entry.lang)
            )
            if self._done:
                return
            if transcript.rejected:
                return
            if self.mode == "trade":
                # A model call takes about half a second; running one every
                # speculative pass would be pointless and would queue up
                # behind itself. Trades are judged once an attempt is
                # complete, so a partial here only feeds the live transcript.
                await self._send_partial(transcript, 0)
                return
            result = self._score(transcript.text)
            if result.passed:
                await self._finalize("success", "early", result, transcript_meta=transcript)
                return
            if result.transcript_norm and result.transcript_norm != self._last_partial_text:
                await self._send_partial(transcript, result.score,
                                         norm=result.transcript_norm)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("speculative pass failed for challenge %s", self.id)
        finally:
            self._inflight = False

    async def _send_partial(self, transcript, score: int, norm: str | None = None) -> None:
        """One update of the live transcript.

        Each pass re-transcribes the whole attempt, so whisper is free to
        rewrite what it already said. The words it has stopped changing its
        mind about go out separately, which lets the overlay hold them steady
        and show only the tail as still in flux -- otherwise the whole line
        flickers and is unreadable.
        """
        if norm is not None:
            self._last_partial_text = norm
        stable = _stable_prefix(self._last_partial_raw, transcript.text)
        self._last_partial_raw = transcript.text
        await self._send({
            "type": "partial",
            "id": self.id,
            # Capped: this is display text, and a decoder stuck in a
            # repetition loop will happily produce a kilobyte of it several
            # times a second.
            "transcript": _clip(transcript.text),
            "stable": _clip(stable),
            "attempt": self._attempt + 1,
            "confidence": score,
            "remaining_ms": max(0, self.timeout_ms - self.elapsed_ms),
        })

    async def _judge_trade(self, transcript, reason: str, *, final: bool) -> None:
        """Hand a finished attempt to the language model.

        A refusal partway through does not end the challenge. Word mode lets
        the player pause and try again, and a sentence is the harder thing to
        get out in one go -- trailing off halfway and starting over is the
        normal way it goes wrong, so ending it on the fragment would be
        stricter than naming a single word.
        """
        verdict = await asyncio.to_thread(
            self._judge.judge, transcript.text, self.entry.item_name, self.entry.lang,
            self.task)

        if not verdict.available:
            # No model reachable. Letting the trade through is the fair
            # outcome: the player did their part and the infrastructure did
            # not.
            await self._finalize("success", "judge_unavailable",
                                 self._score(transcript.text), transcript_meta=transcript,
                                 note=verdict.reason)
            return
        result = self._score(transcript.text)
        if verdict.accepted:
            await self._finalize("success", reason, result,
                                 transcript_meta=transcript, note=verdict.reason)
            return

        self._trade_note = verdict.reason
        self._trade_last = result
        self._trade_last_transcript = transcript
        if final:
            await self._finalize("fail", "rejected", result,
                                 transcript_meta=transcript, note=verdict.reason)
            return

        await self._send({
            "type": "attempt",
            "id": self.id,
            "attempt": self._attempt,
            "transcript": transcript.text,
            "confidence": result.score,
            "note": verdict.reason,
            "remaining_ms": max(0, self.timeout_ms - self.elapsed_ms),
        })
        self._last_partial_text = ""

    async def _final_pass(self, reason: str) -> None:
        if self._done:
            return
        snapshot = bytes(self._buffer)

        # Nothing in the current attempt: the player already finished speaking
        # and that attempt was scored when the pause closed it. Judge them on
        # their best try rather than on the silence they are sitting in.
        if self.mode == "trade" and self._speech_ms < self._s.min_speech_ms \
                and self._trade_last is not None:
            # A trade only reaches here having already been refused: an
            # accepted one finalizes on the spot. Saying "low score" about a
            # sentence nobody scored would be nonsense, so the model's own
            # reason stands.
            await self._finalize("fail", "rejected", self._trade_last,
                                 transcript_meta=self._trade_last_transcript,
                                 note=self._trade_note)
            return
        if self._speech_ms < self._s.min_speech_ms and self._best is not None:
            await self._finalize(
                "success" if self._best.passed else "fail",
                reason if self._best.passed else "low_score",
                self._best, transcript_meta=self._best_transcript)
            return

        try:
            transcript = await asyncio.to_thread(
                self._engine.transcribe, snapshot, self._hotwords,
                whisper_language_for(self.entry.lang)
            )
        except Exception:  # noqa: BLE001
            log.exception("final pass failed for challenge %s", self.id)
            await self._finalize("fail", "stt_error", None)
            return
        if self._done:
            return
        if transcript.rejected:
            await self._finalize(
                "fail",
                "no_speech" if transcript.rejected in ("silence", "no_audio", "empty") else transcript.rejected,
                self._score(""),
                transcript_meta=transcript,
            )
            return
        if self.mode == "trade":
            await self._judge_trade(transcript, reason, final=True)
            return

        result = self._score(transcript.text)
        self._remember_best(result, transcript)
        # The best attempt stands, not merely the last one: giving up on a
        # near-miss because the final half-second was mumbled would be unfair.
        if not result.passed and self._best is not None and self._best.score > result.score:
            result, transcript = self._best, self._best_transcript
        await self._finalize(
            "success" if result.passed else "fail",
            reason if result.passed else "low_score",
            result,
            transcript_meta=transcript,
        )

    async def _watch_deadline(self) -> None:
        try:
            await asyncio.sleep(self.timeout_ms / 1000)
        except asyncio.CancelledError:
            return
        if self._done:
            return
        if self._s.timeout_grace and len(self._buffer) >= self._s.min_audio_bytes:
            # One last look at what the player managed to say before the
            # clock ran out; costs ~100-250 ms and avoids robbing near-misses.
            await self._final_pass("timeout_grace")
            if self._done:
                return
        await self._finalize("fail", "timeout", None)

    # ------------------------------------------------------------------
    def _score(self, text: str) -> MatchResult:
        return match(
            text,
            self.entry.target,
            threshold=self.threshold,
            token_gate=self.token_gate,
            accents=self._s.strip_accents,
            articles=self._s.strip_articles,
            language=self.entry.lang,
            key_tokens=self.entry.key_tokens or None,
            rivals=self.entry.rivals,
        )

    async def _finalize(
        self,
        status: str,
        reason: str,
        result: MatchResult | None,
        transcript_meta=None,
        note: str = "",
    ) -> None:
        if self._done:
            return
        self._done = True
        if self._deadline_task and not self._deadline_task.done():
            self._deadline_task.cancel()

        # "Wrong" is not a lesson; "you named the ice, not the string" is. When
        # the matcher rejected a short word because the sounds fitted a
        # *different* item better, say which -- these are exactly the pairs a
        # learner cannot hear yet, and the overlay already has somewhere to
        # put a sentence of explanation.
        if not note and status != "success" and result is not None and result.rival:
            note = f"that sounded like {result.rival}"

        payload: dict[str, Any] = {
            "type": "result",
            "id": self.id,
            "status": status,
            "reason": reason,
            "confidence": result.score if result else 0,
            "transcript": result.transcript if result else "",
            "target": self.entry.target,
            "key": self.entry.key,
            "lang": self.entry.lang,
            "threshold": self.threshold,
            "elapsed_ms": self.elapsed_ms,
            "audio_ms": self.audio_ms,
            # Why a trade was turned down, in the model's words, so the
            # player learns something from being refused.
            "note": note,
            "mode": self.mode,
        }
        if transcript_meta is not None:
            payload["debug"] = {
                "infer_ms": transcript_meta.infer_ms,
                "rms": round(transcript_meta.rms, 5),
                "no_speech_prob": round(transcript_meta.no_speech_prob, 3),
                "avg_logprob": round(transcript_meta.avg_logprob, 3),
                "detected_language": transcript_meta.language,
                "scores": result.breakdown if result else {},
            }
        # The backend decides the verdict, so it keeps the tally itself --
        # nothing to report back and nothing that can drift out of step.
        #
        # Trades are deliberately left out of it. The history exists to answer
        # "which word does this player keep mispronouncing", and that is what
        # /review draws its drills from. A refused trade says the sentence was
        # not a request -- nothing at all about saying "pan" -- so counting it
        # as a failure would send a door off to drill a word the player may
        # know perfectly. Counting an accepted one is worse: it credits them
        # for a word the judge was never scoring.
        if self.mode != "trade":
            stats.record(self.entry.lang, self.entry.key, self.entry.item_name,
                         status == "success")

        log.info(
            "challenge %s -> %s (%s) target=%r transcript=%r score=%d in %dms",
            self.id,
            status,
            reason,
            self.entry.target,
            payload["transcript"],
            payload["confidence"],
            payload["elapsed_ms"],
        )
        await self._send(payload)

    async def close(self) -> None:
        if self._deadline_task and not self._deadline_task.done():
            self._deadline_task.cancel()
        for task in list(self._pass_tasks):
            task.cancel()
        self._done = True
