# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Reduce a word to roughly how it sounds, so spelling stops mattering.

Whisper writes down what it hears, and for a correctly pronounced word that
spelling is often not the dictionary one: "llave" comes back as "yave",
"haber" as "aber", "zapato" as "sapato". Those are the same sound. Comparing
the written forms marks them wrong, which is the single biggest cause of a
good attempt being rejected.

Comparing phonetic forms also makes the test *more* honest, not less: Spanish
b and v really are the same phoneme, so accepting either is correct for a
pronunciation exercise, while "dinamita" and "diamante" stay as different as
they always were.

Only languages with rules here are transformed. Everything else falls through
unchanged rather than being mangled by rules meant for another language.
"""

from __future__ import annotations

import os
import re

# ----------------------------------------------------------------------
# Spanish. Applied after lowercasing and accent stripping, so the input is
# plain ascii-ish letters. Order matters: digraphs before single letters.
# ----------------------------------------------------------------------
_ES_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ch"), "C"),        # held apart from plain c
    (re.compile(r"ll"), "y"),        # yeismo: "llave" == "yave"
    (re.compile(r"qu(?=[ei])"), "k"),
    (re.compile(r"gu(?=[ei])"), "g"),  # silent u
    (re.compile(r"c(?=[ei])"), "s"),   # seseo: "cena" == "sena"
    (re.compile(r"z"), "s"),           # "zapato" == "sapato"
    (re.compile(r"c"), "k"),
    (re.compile(r"g(?=[ei])"), "x"),   # "gente" == "jente"
    (re.compile(r"j"), "x"),
    (re.compile(r"v"), "b"),           # b and v are one phoneme
    (re.compile(r"w"), "b"),
    (re.compile(r"h"), ""),            # silent, and only after ch is handled
    (re.compile(r"y(?![aeiou])"), "i"),  # "muy" == "mui"
    (re.compile(r"x"), "ks"),
)

# Portuguese and Italian share enough of the pattern to benefit from the
# obvious parts without pretending to be complete.
_PT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"lh"), "L"),
    (re.compile(r"nh"), "N"),
    (re.compile(r"ch"), "S"),
    (re.compile(r"ss"), "s"),
    (re.compile(r"c(?=[ei])"), "s"),
    (re.compile(r"c"), "k"),
    (re.compile(r"qu(?=[ei])"), "k"),
    (re.compile(r"h"), ""),
    (re.compile(r"z$"), "s"),
)

_IT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gli"), "L"),
    (re.compile(r"gn"), "N"),
    (re.compile(r"ch"), "k"),
    (re.compile(r"ci"), "C"),
    (re.compile(r"c(?=[ei])"), "C"),
    (re.compile(r"c"), "k"),
    (re.compile(r"h"), ""),
)

# Spanish /p t k/ are unaspirated, which an English-trained decoder hears as
# /b d g/: measured across six voices, "Pan" came back as "Ban", "BEN" and
# "Pen", and one voice lost twenty points to this class of error alone.
# Merging the pairs rescues those -- at the cost of making "pan" and "ban",
# "tos" and "dos" identical. Whether that trade is worth it is a measurement,
# not an opinion: scripts/benchmark_voices.py for what it gains and
# scripts/tune_thresholds.py for what it leaks.
_ES_LENIENT_STOPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"p"), "b"),
    (re.compile(r"t"), "d"),
)

_RULES: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "es": _ES_RULES,
    "pt": _PT_RULES,
    "it": _IT_RULES,
}

_DOUBLE = re.compile(r"(.)\1+")


def supported(language: str) -> bool:
    return _base(language) in _RULES


def _base(language: str) -> str:
    return (language or "").split("_")[0].split("-")[0].lower()


#: On by default. Measured across six Spanish voices it gains about two
#: points on four of them and costs **nothing** on the negative corpus --
#: within the Minecraft item vocabulary there are almost no p/b or t/d
#: minimal pairs, so the merge discards a distinction this app never has to
#: make. Set LINGUA_LENIENT_STOPS=0 to go back to strict.
LENIENT_STOPS = os.environ.get("LINGUA_LENIENT_STOPS", "1").strip().lower() in (
    "1", "true", "yes", "on")


def phonetic(text: str, language: str) -> str:
    """Sound-ish form of already-normalised (lowercase, unaccented) text.

    Returns the input unchanged for languages with no rules, so callers can
    apply this unconditionally.
    """
    rules = _RULES.get(_base(language))
    if not rules or not text:
        return text
    if LENIENT_STOPS and _base(language) == "es":
        rules = rules + _ES_LENIENT_STOPS

    out = []
    for word in text.split():
        for pattern, replacement in rules:
            word = pattern.sub(replacement, word)
        # "rr" and "ll" style doubling carries no extra sound once the
        # digraphs above are handled, and whisper is inconsistent about it.
        word = _DOUBLE.sub(r"\1", word)
        if word:
            out.append(word)
    return " ".join(out)
