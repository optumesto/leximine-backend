# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A Latin-script reading, so a learner knows what sound to make.

Showing someone `石` and the gloss "Stone" tells them what the word *means*
and nothing at all about what to *say*. For Spanish that gap does not exist --
the written form is the instruction -- which is why it went unnoticed until
the first person tried Japanese and Russian.

Only scripts with an exact, rule-based reading are handled here:

    Cyrillic   table           covers 99% of the Russian dictionary
    Greek      table
    Hangul     decomposed algorithmically into jamo, then tabled
    Kana       table           hiragana and katakana

**Kanji is deliberately absent.** A reading for 石 is a dictionary lookup, not
a rule -- the same character is `ishi`, `seki` or `shaku` depending on the
word -- and 84% of the Japanese entries contain at least one. Guessing would
produce confident nonsense, so anything containing kanji returns nothing and
the overlay falls back to the spoken example instead, which is correct for
100% of them.
"""

from __future__ import annotations

import unicodedata

# ----------------------------------------------------------------------
# Cyrillic. Close to BGN/PCGN, but tuned for "an English speaker reading this
# aloud should land near the right sound" rather than for reversibility.
# ----------------------------------------------------------------------
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "ye", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Ukrainian and friends, so those dictionaries are not left blank.
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ѐ": "e", "ў": "w",
}

_GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y",
    "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
}

# Hangul decomposes arithmetically, so this needs three small tables rather
# than one enormous one.
_JAMO_INITIAL = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss",
                 "", "j", "jj", "ch", "k", "t", "p", "h"]
_JAMO_VOWEL = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa",
               "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_JAMO_FINAL = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l",
               "l", "l", "p", "l", "m", "p", "t", "t", "ng", "t", "t", "k",
               "t", "p", "t"]

# Kana. Digraphs (きゃ) must be tried before their base character, so these
# are matched longest-first.
_KANA = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo", "しゃ": "sha", "しゅ": "shu",
    "しょ": "sho", "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho", "にゃ": "nya",
    "にゅ": "nyu", "にょ": "nyo", "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo", "りゃ": "rya", "りゅ": "ryu",
    "りょ": "ryo", "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo", "じゃ": "ja",
    "じゅ": "ju", "じょ": "jo", "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゔ": "vu",
}
# Katakana is the same sound set one Unicode block up, so the table is
# derived rather than typed out twice -- two hand-written tables would be two
# places for the same sound to be wrong in different ways.
_KANA.update({
    "".join(chr(ord(c) + 0x60) for c in kana): roman
    for kana, roman in list(_KANA.items())
})
_KANA_KEYS = sorted(_KANA, key=len, reverse=True)

_SOKUON = {"っ", "ッ"}      # doubles the next consonant
_CHOONPU = {"ー"}           # lengthens the previous vowel


def _is_kanji(ch: str) -> bool:
    return 0x4E00 <= ord(ch) <= 0x9FFF


def _is_latin(ch: str) -> bool:
    """Latin script, accents and all -- not merely ascii."""
    try:
        return unicodedata.name(ch).startswith("LATIN")
    except ValueError:
        return False


#: After one of these, Cyrillic е is "ye"; elsewhere it is plain "e".
#: Without this every Russian word came out with a y wedged into the middle
#: of it -- "Камень" as "kamyen" rather than "kamen".
_CYRILLIC_VOWELS = set("аеёиоуыэюяaeiouy")
_CYRILLIC_SIGNS = set("ъь")


def _romanize_hangul(ch: str) -> str | None:
    index = ord(ch) - 0xAC00
    if not 0 <= index < 11172:
        return None
    initial, rest = divmod(index, 588)
    vowel, final = divmod(rest, 28)
    return _JAMO_INITIAL[initial] + _JAMO_VOWEL[vowel] + _JAMO_FINAL[final]


def _romanize_kana(text: str) -> str | None:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _SOKUON:
            # Peek at what follows and double its first consonant.
            for key in _KANA_KEYS:
                if text.startswith(key, i + 1):
                    nxt = _KANA[key]
                    if nxt and nxt[0] not in "aeiou":
                        out.append(nxt[0])
                    break
            i += 1
            continue
        if ch in _CHOONPU:
            # Long vowel: repeat whatever vowel we last produced.
            for prev in reversed("".join(out)):
                if prev in "aeiou":
                    out.append(prev)
                    break
            i += 1
            continue
        matched = False
        for key in _KANA_KEYS:
            if text.startswith(key, i):
                out.append(_KANA[key])
                i += len(key)
                matched = True
                break
        if matched:
            continue
        if ch.isspace() or ch in "・=-":
            out.append(" ")
            i += 1
            continue
        return None     # kanji, or something else we have no rule for
    return "".join(out).strip()


def reading(text: str, lang: str = "") -> str:
    """A Latin reading of `text`, or "" when there is nothing useful to say.

    Returns "" rather than a guess in three cases, all deliberate: the text is
    already Latin and the reading would just repeat it; the script has no
    rule here; or part of it does (kanji) and a partial reading would be
    worse than none — "stone no tsurugi" reads as a mistake, not a hint.
    """
    text = (text or "").strip()
    if not text:
        return ""

    letters = [c for c in text if c.isalpha()]
    if not letters:
        return ""
    # Latin SCRIPT, not ascii. Checking for ascii let every Spanish word with
    # an ñ or an ó through, and the "reading" produced was the same word with
    # the accents filed off -- "Pocion" under "Poción", which is noise at
    # best and at worst teaches the wrong spelling.
    if all(_is_latin(c) for c in letters):
        return ""       # already readable

    if any(_is_kanji(c) for c in text):
        return ""       # see the module note

    # Kana first: a Japanese string is the one case that needs its own
    # scanner rather than a per-character map.
    if any(0x3040 <= ord(c) <= 0x30FF for c in text):
        kana = _romanize_kana(text)
        return kana or ""

    out: list[str] = []
    previous = ""
    for ch in text:
        lower = ch.lower()
        if lower in _CYRILLIC:
            if lower == "е":
                initial = previous == "" or not previous.isalpha()
                out.append("ye" if initial
                           or previous in _CYRILLIC_VOWELS
                           or previous in _CYRILLIC_SIGNS else "e")
            else:
                out.append(_CYRILLIC[lower])
            previous = lower
            continue
        previous = lower
        if lower in _GREEK:
            out.append(_GREEK[lower])
            continue
        hangul = _romanize_hangul(ch)
        if hangul is not None:
            out.append(hangul)
            continue
        if ch.isascii() or ch.isspace() or not ch.isalpha():
            out.append(ch)
            continue
        # Last resort: strip the accent and see whether a Latin letter falls
        # out. Covers stray diacritics without inventing anything.
        plain = "".join(
            c for c in unicodedata.normalize("NFD", ch)
            if not unicodedata.combining(c))
        if plain.isascii():
            out.append(plain)
            continue
        return ""       # something we have no rule for; say nothing

    result = " ".join("".join(out).split())
    # If it came back identical, there was nothing to transliterate.
    return "" if result.lower() == text.lower() else result
