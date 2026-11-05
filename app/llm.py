# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Judge a spoken trade request with a local language model.

Fuzzy matching answers "did they say this word". Negotiating a trade is a
different question -- "is this a sensible, grammatical way to ask for that
item" -- which has no single right answer to match against, so it goes to a
model instead.

Talks to any OpenAI-compatible endpoint; Ollama serves one at /v1 by default,
so nothing extra needs running beyond the model the machine already has.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Enough to name the language in the prompt. Anything missing falls back to
# the code itself, which the model generally still understands.
_LANGUAGE_NAMES = {
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "pl": "Polish", "sv": "Swedish",
    "tr": "Turkish", "uk": "Ukrainian", "cs": "Czech", "da": "Danish",
    "fi": "Finnish", "no": "Norwegian", "el": "Greek", "he": "Hebrew",
    "hi": "Hindi", "ar": "Arabic", "id": "Indonesian", "vi": "Vietnamese",
}

# Why this wording: an earlier version asked whether the sentence was "a
# request to trade for" the item, and the model rejected "Quiero comprar una
# esmeralda" -- a perfectly good sentence -- on the grounds that buying is not
# trading. The goal is a grammatical request, not a particular verb. The note
# about punctuation matters too: this text came from speech recognition, which
# does not reliably produce any.
_TRADE_PROMPT = """\
You judge a language learner's spoken sentence.

They are talking to a merchant and want the item named below. Decide whether \
their sentence is a reasonable and grammatically correct way to ask for it in \
{language}. Asking, requesting, buying, trading or offering to exchange all \
count equally.

Answer FALSE if the sentence is in a different language, asks for a different \
item, or is not a request at all (a bare noun is not a request).

The text came from speech recognition: ignore missing punctuation, missing \
capitals and minor spelling slips. Judge the words and the grammar.

Reply with exactly TRUE or FALSE as your first word, then at most eight words \
of reason."""

# The same judgement with the merchant taken out of it, for callers that set a
# `task`. Kept as a separate string rather than one template with a hole in it:
# the trade wording was argued into its current shape one clause at a time (see
# the note above about "comprar"), and a shared template would mean every
# future task edit quietly re-litigating it.
_TASK_PROMPT = """\
You judge a language learner's spoken sentence.

Their task: {task}

Decide whether their sentence does that, in {language}, grammatically. Any \
reasonable phrasing counts -- judge whether it works, not whether it is the \
wording you would have chosen.

Answer FALSE if the sentence is in a different language, is about something \
else, or is a bare noun or fragment rather than a sentence.

The text came from speech recognition: ignore missing punctuation, missing \
capitals and minor spelling slips. Judge the words and the grammar.

Reply with exactly TRUE or FALSE as your first word, then at most eight words \
of reason."""


@dataclass
class Verdict:
    accepted: bool
    reason: str
    model: str
    elapsed_ms: int
    available: bool = True


def language_name(code: str) -> str:
    base = (code or "").split("_")[0].split("-")[0].lower()
    return _LANGUAGE_NAMES.get(base, code or "the target language")


class TradeJudge:
    """
    Timing, measured on this machine: a cold call is **30.8 seconds** while
    7.5 GB of weights are read into VRAM. Once resident it is 0.1 seconds.
    A trade challenge allows the player twice the normal timeout -- 12
    seconds by default -- so a cold judge cannot possibly answer in time, and
    the unanswered trade fails open. The villager deals without being asked
    and nothing says why.

    That makes keeping the model resident a correctness concern, not a
    performance one, which is why warming is driven from a client connecting
    rather than from a trade starting.
    """

    def __init__(self, base_url: str, model: str, timeout_s: float = 60.0,
                 keep_alive: str = "2h") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive
        self._warmed_at = 0.0
        self._warming = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    #: Only used when residency cannot be checked directly. Repeated warms
    #: inside this window are then free.
    REWARM_AFTER_S = 300.0

    def warm(self) -> None:
        """Make sure the model is in VRAM, loading it in the background.

        Called when a client connects, which buys the ~31 seconds a cold load
        actually needs, and again when a trade opens, which costs nothing
        once it is resident.

        Two earlier versions of this were wrong in instructive ways. The
        first latched a boolean, so once the model was evicted the backend
        believed it was warm for the rest of its life. The second used a
        timer, which cannot see an eviction it did not cause -- and on this
        machine a 30B model for another project will happily take the VRAM.
        So it asks what is actually resident.
        """
        with self._lock:
            if self._warming:
                return
            self._warming = True

        def run() -> None:
            import time as _time
            try:
                # The native preload both loads the model if it is gone and
                # pushes its expiry back out if it is not, so this is the
                # whole job in one call. Doing it unconditionally is what
                # keeps a long session safe: every judged trade resets the
                # window to Ollama's own default, and a check that only
                # reloaded on absence would let it lapse and then wait up to
                # a full tick to notice.
                elapsed_ms = self._preload()
                if elapsed_ms is None:
                    # Not Ollama. No way to set residency explicitly, so fall
                    # back to a plain completion, rate-limited by a timer.
                    now = _time.monotonic()
                    if self._warmed_at and now - self._warmed_at < self.REWARM_AFTER_S:
                        return
                    _, elapsed_ms = self._post([{"role": "user", "content": "hi"}],
                                               max_tokens=1)
                self._warmed_at = _time.monotonic()
                # Only worth saying when it actually had to load something; a
                # refresh of an already-resident model returns immediately,
                # and logging that every tick is noise.
                if elapsed_ms > 1000:
                    log.info("trade judge model %s is resident (%.1fs)",
                             self.model, elapsed_ms / 1000)
            except Exception as exc:  # noqa: BLE001
                log.info("could not warm %s: %s", self.model, exc)
            finally:
                with self._lock:
                    self._warming = False

        thread = threading.Thread(target=run, name="lingua-llm-warm", daemon=True)
        thread.start()

    def _preload(self) -> int | None:
        """Load the model and set how long it stays, via Ollama's own API.

        **The OpenAI-compatible endpoint silently ignores `keep_alive`** --
        measured: a /v1 call asking for 2h left the window at the 30 minutes
        Ollama's own OLLAMA_KEEP_ALIVE was set to, while the identical native
        call moved it to 2h. So the setting has to be sent here or it is
        decorative.

        A native generate with no prompt is Ollama's documented way to load a
        model without asking it anything. Returns the load time, or None if
        this is not Ollama, in which case the caller sends an ordinary
        completion instead and residency is whatever the server decides.
        """
        import time

        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        body = json.dumps({"model": self.model, "keep_alive": self.keep_alive}).encode()
        request = urllib.request.Request(
            f"{root}/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response.read()
        except Exception:  # noqa: BLE001
            return None
        return int((time.perf_counter() - started) * 1000)

    # ------------------------------------------------------------------
    def judge(self, sentence: str, item_name: str, lang: str,
              task: str = "") -> Verdict:
        """Blocking. Call via asyncio.to_thread.

        With no `task` this is the merchant question: is this a way to ask for
        `item_name`. With one it is that task instead, which is how a gate
        that is not a trade -- lighting a nether portal, say -- reuses the
        judge without the merchant wording leaking into it.
        """
        sentence = (sentence or "").strip()
        if not sentence:
            return Verdict(False, "nothing was said", self.model, 0)

        task = (task or "").strip()
        if task:
            system = _TASK_PROMPT.format(language=language_name(lang), task=task)
            user = f'Sentence: "{sentence}"'
        else:
            system = _TRADE_PROMPT.format(language=language_name(lang))
            user = f'Item: {item_name}\nSentence: "{sentence}"'
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            reply, elapsed_ms = self._post(messages, max_tokens=40)
        except urllib.error.URLError as exc:
            log.warning("trade judge unreachable at %s: %s", self.base_url, exc)
            return Verdict(False, "judge unavailable", self.model, 0, available=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("trade judge failed: %s", exc)
            return Verdict(False, "judge failed", self.model, 0, available=False)

        text = reply.strip()
        accepted = bool(re.match(r"\s*true\b", text, re.IGNORECASE))
        reason = re.sub(r"^\s*(true|false)\b[.:,\s-]*", "", text,
                        flags=re.IGNORECASE).strip()
        log.info("trade judge: %s (%s) in %dms for %r",
                 "accept" if accepted else "reject", reason or "no reason", elapsed_ms,
                 sentence)
        return Verdict(accepted, reason or ("accepted" if accepted else "rejected"),
                       self.model, elapsed_ms)

    # ------------------------------------------------------------------
    def _post(self, messages: list[dict], max_tokens: int) -> tuple[str, int]:
        import time

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            # Deterministic: the same sentence should not pass on a retry.
            "temperature": 0,
            "max_tokens": max_tokens,
            "keep_alive": self.keep_alive,
        }).encode("utf-8")

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        usage = payload.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        if prompt_tokens and prompt_tokens > 1500:
            # Ollama truncates silently past its context window and answers
            # confidently from whatever survived.
            log.warning("trade judge prompt was %d tokens; check it was not truncated",
                        prompt_tokens)
        return payload["choices"][0]["message"]["content"], elapsed_ms
