# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Transcript -> target-word comparison.

Deliberately generous: in a game, a false reject is far more annoying than a
false accept. The combination used here is
`max(ratio, token_sort_ratio, token_set_ratio, best_window_ratio)`, which means
"um... diamante, please" still clears the bar for the target "Diamante", while
genuinely different words ("dinamita") do not.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from thefuzz import fuzz

from . import phonetics

# Leading articles / determiners worth ignoring. Only stripped from the front:
# removing them mid-string would collapse "Bloque de diamante" into something
# that also matches "Bloque de diamante bruto".
#
# **Kept per language, not pooled.** One shared set looks harmless and is not:
# "o" is the Portuguese article and the Spanish word for "or", so a Spanish
# transcript of "o no" was being reduced to "no" before anything scored it --
# which is how "Horno" came back as a 67 against audio that had said it
# perfectly. "a" collides the same way between English and Spanish.
_ARTICLES_BY_LANGUAGE: dict[str, frozenset[str]] = {
    "es": frozenset({"el", "la", "los", "las", "un", "una", "unos", "unas"}),
    "pt": frozenset({"o", "os", "a", "as", "um", "uma"}),
    "en": frozenset({"the", "a", "an"}),
    "fr": frozenset({"le", "la", "les", "un", "une", "du", "des"}),
    "de": frozenset({"der", "die", "das", "ein", "eine"}),
    "it": frozenset({"il", "lo", "la", "gli", "un", "uno", "una"}),
}

#: Used only when the caller names no language, where guessing is worse than
#: being broad.
LEADING_ARTICLES = frozenset().union(*_ARTICLES_BY_LANGUAGE.values())


def articles_for(language: str) -> frozenset[str]:
    base = (language or "").split("_")[0].split("-")[0].lower()
    return _ARTICLES_BY_LANGUAGE.get(base, LEADING_ARTICLES)

def _depunctuate(text: str) -> str:
    """Replace everything that is not a letter or digit with a space.

    Category-based rather than a character whitelist, so Cyrillic, Greek,
    Hangul and CJK target languages all work without a regex edit.
    """
    return "".join(
        ch if unicodedata.category(ch)[0] in ("L", "N") else " " for ch in text
    )

# Whisper's favourite inventions when fed silence or room noise. Matching one
# of these means "nobody said anything", not "said the wrong word".
HALLUCINATIONS = (
    "subtitulos realizados por",
    "subtitulado por",
    "amara.org",
    "gracias por ver el video",
    "suscribete",
    "thanks for watching",
    "subscribe",
    "thank you for watching",
    "music",
    "musica",
    "applause",
    "aplausos",
    "silencio",
    "bip",
)


#: Kana, hiragana through katakana including the phonetic extensions.
_KANA_RANGE = (0x3040, 0x30FF)


def strip_accents(text: str) -> str:
    """Drop diacritics, except the ones that are not diacritics.

    An accent in Spanish marks stress; the word is the same word without it,
    which is why stripping them stops a missing accent from failing an
    otherwise correct answer.

    **Japanese dakuten are not accents.** NFKD decomposes ダ into タ plus a
    combining mark, so stripping blindly turned ダイヤモンド into タイヤモント
    and collapsed ハ, バ and パ -- three distinct sounds -- into one. The
    matcher would then accept "taiyamonto" for "daiyamondo", which is not
    leniency about spelling, it is accepting a different word.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    kept: list[str] = []
    for ch in decomposed:
        if unicodedata.combining(ch):
            previous = kept[-1] if kept else ""
            if previous and _KANA_RANGE[0] <= ord(previous) <= _KANA_RANGE[1]:
                kept.append(ch)
            continue
        kept.append(ch)
    # Recomposed, so ダ is one character again rather than two.
    return unicodedata.normalize("NFC", "".join(kept))


def normalize(text: str, *, accents: bool = True, articles: bool = True,
              language: str = "") -> str:
    """Lowercase, de-punctuate, optionally de-accent and drop a leading article."""
    out = unicodedata.normalize("NFKC", text).lower()
    if accents:
        out = strip_accents(out)
    out = _depunctuate(out)
    tokens = out.split()
    if articles:
        leading = articles_for(language)
        while len(tokens) > 1 and tokens[0] in leading:
            tokens.pop(0)
    return " ".join(tokens)


#: A target this many words or more is a sentence, not a name.
SENTENCE_TOKENS = 4

#: How much of a sentence has to be attempted before the score is docked.
#: 0.75 lets a word go missing without penalty and still fails the player who
#: says the noun and stops.
COMPLETENESS_SHARE = 0.75


def looks_like_hallucination(normalized_transcript: str) -> bool:
    if not normalized_transcript:
        return True
    for phrase in HALLUCINATIONS:
        if strip_accents(phrase) in normalized_transcript:
            return True
    return is_repetition_loop(normalized_transcript)


def is_repetition_loop(normalized_transcript: str) -> bool:
    """Whisper stuck repeating itself, which is noise rather than an answer.

    Fed unclear audio the decoder can latch onto a short phrase and emit it
    over and over -- a real capture produced "la ropa de la ropa de la ropa"
    fifty times in one pass. Left alone that floods the overlay, and its sheer
    length hands the fuzzy matcher far more chances to find something.

    Keyed on how few distinct words there are rather than on length, so a
    genuinely long tier-3 sentence is unaffected.
    """
    words = normalized_transcript.split()
    if len(words) < 12:
        return False
    return len(set(words)) / len(words) < 0.3


# Words that carry no identifying information in an item name, so they must
# not count towards "did they say the right thing". Everything else does --
# including three-letter words like "oro", which is exactly what separates
# "Lingote de oro" from "Lingote de hierro". A length cutoff would miss those.
FUNCTION_WORDS = LEADING_ARTICLES | {
    "de", "del", "y", "e", "con", "en", "al", "para",          # es
    "of", "and", "with", "in", "for",                          # en
    "et", "avec", "aux", "au",                                 # fr
    "von", "und", "mit", "im", "zum", "zur",                   # de
    "di", "da", "della", "dello",                              # it
    "do", "dos", "das", "com",                                 # pt
}

# Below this length, a token is too short for `partial_ratio` to be meaningful
# (it finds "pan" inside "pandemia" and reports a perfect match), so short
# content words are compared token-to-token only.
PARTIAL_MIN_LEN = 4

#: How many adjacent transcript words one target word may have been split
#: into. Three covers everything observed ("Espada" -> "Es para", "Horno" ->
#: "o no"); leaving it unbounded would let a long transcript be re-joined in
#: enough ways that something eventually fits.
MAX_SPLIT_WINDOW = 3


def _token_coverage(
    transcript_norm: str, transcript_tokens: list[str], target_tokens: list[str],
    required: tuple[str, ...] | None = None,
) -> int | None:
    """Worst per-token score across the target's *content* words.

    Minecraft item names are templates: "Lingote de hierro", "Lingote de oro",
    "Pico de hierro". Whole-string similarity between two of them is high
    purely because of the shared prefix, so "lingote de oro" scores 84 against
    "Lingote de hierro" -- enough to hand the player the wrong item. Gating on
    the weakest content token instead makes the distinguishing word decisive:
    "oro" vs "hierro" scores ~44 and is rejected, while sloppy-but-recognisable
    pronunciation ("fierro" for "hierro", ~83) still gets through.

    Returns None when the target has no content tokens (very short names like
    "Pan"), in which case the global scorers decide on their own.
    """
    # An explicit list wins: for a tier-3 sentence the caller gates on the
    # item's own words, so fumbling "estoy fabricando" costs similarity but
    # does not fail outright the way getting the item wrong does.
    content = ([tok for tok in required if tok]
               if required is not None
               else [tok for tok in target_tokens if tok not in FUNCTION_WORDS])
    if not content:
        return None

    worst = 100
    for tok in content:
        best = 0
        for other in transcript_tokens:
            if not _comparable_length(tok, other):
                continue
            best = max(best, fuzz.ratio(tok, other))
        # partial_ratio only when the transcript has FEWER words than the
        # target, i.e. whisper ran them together ("bloquediamante"). Used
        # unconditionally it scans a rambling transcript for anything
        # vaguely similar and hands a wrong answer a free pass.
        if (len(tok) >= PARTIAL_MIN_LEN and transcript_norm
                and len(transcript_tokens) < len(target_tokens)):
            best = max(best, fuzz.partial_ratio(tok, transcript_norm))
        # The mirror of that case: whisper split one word into several rather
        # than running several together. "Horno" came back as "o no", which
        # is the same sounds with a space in the middle, and the per-token
        # comparison above can never see it.
        #
        # Scanned over short runs of *adjacent* tokens rather than the whole
        # transcript at once. The first version joined everything, which only
        # works when the target is a single word: "Espada de hierro" heard as
        # "Es para de hierro" is one word split in two and the other two
        # intact, and comparing "espada" against "esparadehierro" scored 67
        # where comparing it against just "espara" scores 83. Still guarded
        # by the same size rule, and still contiguous, so this is "the word,
        # split up" and not "a long transcript scanned for anything vaguely
        # similar".
        if len(transcript_tokens) > len(target_tokens):
            limit = min(len(transcript_tokens), MAX_SPLIT_WINDOW)
            for width in range(2, limit + 1):
                for start in range(len(transcript_tokens) - width + 1):
                    joined = "".join(transcript_tokens[start:start + width])
                    if _comparable_length(tok, joined):
                        best = max(best, fuzz.ratio(tok, joined))
            whole = transcript_norm.replace(" ", "")
            if _comparable_length(tok, whole):
                best = max(best, fuzz.ratio(tok, whole))
        worst = min(worst, best)
    return worst


def _comparable_length(target_token: str, other: str) -> bool:
    """Is `other` even in the right size range to be this word?

    A long transcript otherwise gets one roll of the dice per word, and with
    enough words something eventually looks close enough. A word half or
    double the length is not the word.
    """
    if not other:
        return False
    ratio = len(other) / len(target_token)
    return 0.5 <= ratio <= 2.0


# A one-word name this short cannot survive the normal threshold.
#
# Fuzzy similarity is a percentage of the *word's* length, so the shorter the
# name the more each letter costs: one substitution in a four-letter word is a
# ratio of 75, and the shipped threshold is 80. "Cubo" heard as "Cuba", "Palo"
# as "Pano", "Hilo" as "Hido" -- all said correctly, all rejected, while the
# same single slip inside "Pepita de oro" is forgiven easily. Exactly backwards:
# short words are the ones speech recognition gets wrong most often, because
# there is no surrounding context to constrain them.
#
# So short one-word targets get one substitution's worth of slack -- but ONLY
# when a vocabulary was supplied to check it against, because the reason the
# threshold was tight here is real: "Pala" and "Palo" are both items, and
# loosening alone would hand over the wrong one.
SHORT_TARGET_LEN = 6
SHORT_TARGET_SLACK = 8

#: How far ahead of the nearest other name the target has to be before any
#: slack is given. Without it, a transcript that is merely *slightly* closer
#: to the target than to something else gets the benefit of the doubt -- and
#: "Palo" spoken at a target of "Pan", heard as "Bando", came back a 75
#: against a 67 and was waved through. Swept over the six-voice corpus.
SHORT_TARGET_MARGIN = 10


@lru_cache(maxsize=16384)
def _rival_form(name: str, language: str) -> str:
    """The comparable form of one candidate name, computed once ever.

    The candidate list is a property of the language, not of the attempt, so
    normalising it per scoring pass is pure waste -- and the lists are not
    always small: Japanese item names have no spaces, so 2,290 of them count
    as short, against 169 in Spanish. Five speculative passes a second over
    an unbounded list is the kind of cost that only shows up in the language
    nobody tested.
    """
    form = normalize(name, language=language)
    return phonetics.phonetic(form, language) if language else form


def _rival_best(
    transcript_norm: str, transcript_tokens: list[str], target_norm: str,
    rivals: tuple[str, ...], language: str,
) -> tuple[int, str]:
    """Highest score any *other* item name gets against this transcript.

    The matcher has only ever been asked "is this the target?", which has no
    answer for a short word on its own: "pala" is a 75 against "Palo" and so
    is "palo" against "Pala". The vocabulary settles it -- whichever name the
    sounds are closest to is the one that was said -- and that is what makes
    the slack above safe to give.
    """
    best, who = 0, ""
    stem = target_norm.rstrip("s")
    for rival in rivals:
        other = _rival_form(rival, language)
        # Not a rival if it is the same word: a plural, or a name that
        # contains the target ("Cubo" inside "Cubo de agua"), where scoring
        # well is correct rather than a competing answer.
        if not other or other == target_norm or other.rstrip("s") == stem:
            continue
        if other in target_norm or target_norm in other:
            continue
        if not _comparable_length(target_norm, other):
            continue
        score = fuzz.ratio(transcript_norm, other)
        for tok in transcript_tokens:
            if _comparable_length(other, tok):
                score = max(score, fuzz.ratio(tok, other))
        if score > best:
            best, who = score, rival
    return best, who


def _best_window(transcript_tokens: list[str], target_norm: str, width: int) -> int:
    """Highest plain ratio over every contiguous `width`-token window."""
    if not transcript_tokens or width <= 0:
        return 0
    best = 0
    for start in range(max(1, len(transcript_tokens) - width + 1)):
        window = " ".join(transcript_tokens[start : start + width])
        best = max(best, fuzz.ratio(window, target_norm))
    return best


@dataclass
class MatchResult:
    passed: bool
    score: int
    transcript: str
    transcript_norm: str
    target: str
    target_norm: str
    threshold: int
    hallucination: bool = False
    coverage: int | None = None
    breakdown: dict[str, int] = field(default_factory=dict)
    rival: str = ""
    """A different item name the transcript fitted better than the target."""


def match(
    transcript: str,
    target: str,
    *,
    threshold: int = 80,
    token_gate: int = 70,
    accents: bool = True,
    articles: bool = True,
    language: str = "",
    key_tokens: tuple[str, ...] | None = None,
    rivals: tuple[str, ...] = (),
) -> MatchResult:
    t_norm = normalize(transcript, accents=accents, articles=articles, language=language)
    g_norm = normalize(target, accents=accents, articles=articles, language=language)

    # Compare how it sounds, not how it is spelled. This replaces the written
    # forms rather than adding another scorer alongside them: every extra
    # scorer is another chance for a wrong answer to slip through, which is
    # the opposite of what is wanted here.
    required = key_tokens
    if language:
        t_norm = phonetics.phonetic(t_norm, language)
        g_norm = phonetics.phonetic(g_norm, language)
        if required:
            # The gate compares against the phonetic forms above, so the
            # required words have to be put through the same transform or
            # they would never match.
            required = tuple(phonetics.phonetic(token, language) for token in required)

    base = MatchResult(
        passed=False,
        score=0,
        transcript=transcript,
        transcript_norm=t_norm,
        target=target,
        target_norm=g_norm,
        threshold=threshold,
    )

    if not g_norm:
        return base
    if looks_like_hallucination(t_norm):
        base.hallucination = True
        return base

    if t_norm == g_norm:
        base.score = 100
        base.passed = True
        base.breakdown = {"exact": 100}
        return base

    target_tokens = g_norm.split()
    transcript_tokens = t_norm.split()
    breakdown = {
        "ratio": fuzz.ratio(t_norm, g_norm),
        "token_sort": fuzz.token_sort_ratio(t_norm, g_norm),
        "window": _best_window(transcript_tokens, g_norm, len(target_tokens)),
        # The same sounds, with the spaces taken out.
        #
        # Speech recognition puts word boundaries where it likes, and Spanish
        # is full of places where that is genuinely ambiguous: measured, the
        # model answered "Horno" with "o no" -- identical phonemes, because
        # the h is silent -- and the space alone dropped it from a pass to 67.
        # Comparing the run of sounds forgives the boundary without forgiving
        # the sounds.
        "joined": fuzz.ratio(t_norm.replace(" ", ""), g_norm.replace(" ", "")),
    }

    # token_set_ratio exists to forgive *extra* words ("um, diamante please").
    # It also returns 100 when the transcript is a strict subset of the
    # target, which means saying one word of a sentence scores perfectly:
    # "diamante" against "he encontrado un diamante" came back as 100 and
    # passed. Only consult it when nothing is missing.
    if len(transcript_tokens) >= len(target_tokens):
        breakdown["token_set"] = fuzz.token_set_ratio(t_norm, g_norm)

    # token_set_ratio hits 100 whenever the target's tokens are a subset of the
    # transcript's. That is what we want for filler words, but for a one-token
    # target it also rewards a transcript that merely *contains* a near miss,
    # so fall back to the stricter scorers in that case.
    if len(target_tokens) == 1 and len(transcript_tokens) > 1:
        breakdown["token_set"] = max(
            (fuzz.ratio(tok, g_norm) for tok in transcript_tokens
             if _comparable_length(g_norm, tok)),
            default=0,
        )

    coverage = _token_coverage(t_norm, transcript_tokens, target_tokens, required)
    if coverage is not None:
        breakdown["coverage"] = coverage

    base.breakdown = breakdown
    base.coverage = coverage
    # `score` stays the headline similarity so it reads sensibly on screen;
    # coverage is a veto, not part of the max.
    base.score = max(v for k, v in breakdown.items() if k != "coverage")

    # You have to attempt the whole sentence.
    #
    # At tiers 3 and 4 the prompt is a sentence, and the key-token gate
    # deliberately only requires the item's own words -- fumbling "estoy
    # fabricando" should not fail the way naming the wrong item does. But
    # that is about saying the frame *badly*, not about skipping it: saying
    # only "lingote de netherita" scored 83 against "Tengo un lingote de
    # netherita" and passed, while the same shortcut against a longer frame
    # scored 67 and failed. Whether the shortcut worked depended on which
    # frame the player happened to draw, which is no rule at all.
    #
    # Scaled rather than vetoed, so dropping one word of six costs a little
    # and saying three words of eight costs a lot.
    if len(target_tokens) >= SENTENCE_TOKENS and transcript_tokens:
        expected = COMPLETENESS_SHARE * len(target_tokens)
        if len(transcript_tokens) < expected:
            base.score = int(base.score * (len(transcript_tokens) / expected))
            base.breakdown = dict(breakdown, completeness=base.score)

    effective = threshold

    # Short one-word names, decided against the vocabulary rather than against
    # the threshold alone. Both halves need the other: the slack is what fixes
    # the correctly-spoken short word, and the rival check is what stops the
    # slack handing over "Pala" when the target was "Palo".
    short_name = (len(target_tokens) == 1 and len(g_norm) <= SHORT_TARGET_LEN
                  and len(transcript_tokens) <= 2)
    if rivals and short_name:
        rival_score, rival_name = _rival_best(
            t_norm, transcript_tokens, g_norm, rivals, language)
        base.breakdown = dict(base.breakdown, rival=rival_score)
        if rival_score > base.score:
            # Whatever was said, it is a better fit for something else.
            base.rival = rival_name
            base.passed = False
            return base
        if rival_score + SHORT_TARGET_MARGIN <= base.score:
            effective = max(threshold - SHORT_TARGET_SLACK, rival_score + 1)
        base.threshold = effective

    base.passed = base.score >= effective and (coverage is None or coverage >= token_gate)
    return base
