# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Table test for the fuzzy matcher. No GPU, no server, runs in milliseconds.

This is where tuning happens: if a real pronunciation gets rejected in game,
add the transcript whisper actually produced as a row here first.

    python scripts/test_matching.py
    python scripts/test_matching.py -v
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.matching import match  # noqa: E402

THRESHOLD = 80

# Confusions observed in scripts/benchmark_asr.py against real synthesised
# speech rather than invented. These are what the model actually does to short
# Spanish nouns, and so the cases most worth not regressing.
OBSERVED_CASES: list[tuple[str, str, bool]] = [
    # Whisper split one word in two. Spanish "h" is silent, so "o no" is
    # "horno" with a space in it -- the same sounds -- and it used to fail.
    ("o no.", "Horno", True),
    ("O no", "Horno", True),

    # The same split, but inside a multi-word name: "Espada" came back as
    # "Es para" with the rest of the name intact. Comparing the target word
    # against the *whole* transcript re-joined gave 67; against the two
    # adjacent words it was actually split into, 83.
    ("Es para de hierro.", "Espada de hierro", True),
    ("es para de oro", "Espada de oro", True),
    # ...and the window is still contiguous and length-guarded, so a
    # transcript cannot be re-joined until something fits.
    ("es de hierro para mi", "Espada de hierro", False),
    ("de hierro", "Espada de hierro", False),

    # These stay rejected. One vowel in a four-letter word is the whole
    # difference between two real items, and no fuzzy threshold separates
    # them: better to be asked again than handed the wrong word.
    ("Cuba.", "Cubo", False),
    ("Bueno.", "Cuero", False),
    ("¡BUN!", "Pan", False),
    ("¡Fun!", "Pan", False),
    ("Fletcher.", "Flecha", False),
    ("¡Fuelva!", "Polvo", False),

    # "o" is the Portuguese article and the Spanish word for "or". Pooling
    # every language's articles stripped it from Spanish, destroying the
    # transcript before anything scored it.
    ("o diamante", "Diamante", True),

    # A sentence prompt has to be attempted as a sentence. Saying only the
    # item used to pass or fail depending on how long the frame happened to
    # be -- "Lingote de netherita" cleared "Tengo un lingote de netherita"
    # at 83, while the same shortcut against a longer frame scored 67.
    ("Lingote de netherita", "Tengo un lingote de netherita", False),
    ("Mesa de encantamientos", "Aquí hay una mesa de encantamientos", False),
    ("Espada de diamante", "Tengo una espada de diamante en la mano", False),
    ("élitros", "Nunca pensé que iba a encontrar unos élitros", False),
    # ...but saying it, even imperfectly, still passes.
    ("Tengo un lingote de netherita", "Tengo un lingote de netherita", True),
    ("tengo una espada de diamante en la mano",
     "Tengo una espada de diamante en la mano", True),
    # One word lost to the microphone is not the same as not trying.
    ("Tengo una espada de diamante en mano",
     "Tengo una espada de diamante en la mano", True),
]

# (transcript heard, target word, should it pass?)
CASES: list[tuple[str, str, bool]] = [
    # --- clean hits ---------------------------------------------------
    ("diamante", "Diamante", True),
    ("Diamante", "Diamante", True),
    ("Diamante.", "Diamante", True),
    ("  diamante!  ", "Diamante", True),
    ("un diamante", "Diamante", True),            # leading article dropped
    ("eh... diamante", "Diamante", True),         # filler before the word
    ("diamante por favor", "Diamante", True),     # filler after the word
    ("carbon", "Carbón", True),                   # accent not pronounced back
    ("Carbón", "Carbón", True),
    ("lapislazuli", "Lapislázuli", True),
    ("manzana", "Manzana", True),
    # --- multi-word targets ------------------------------------------
    ("bloque de diamante", "Bloque de diamante", True),
    ("bloque diamante", "Bloque de diamante", True),     # dropped preposition
    ("el bloque de diamante", "Bloque de diamante", True),
    ("lingote de hierro", "Lingote de hierro", True),
    ("espada de diamante", "Espada de diamante", True),
    # --- near misses that must NOT pass ------------------------------
    ("dinamita", "Diamante", False),
    ("diablo", "Diamante", False),
    ("elefante", "Diamante", False),
    ("esmeralda", "Diamante", False),
    # Shared-template names: everything but the last word is identical, so
    # these are the cases that matter most for not handing out wrong items.
    ("pico de madera", "Pico de diamante", False),
    ("lingote de oro", "Lingote de hierro", False),
    ("lingote de hierro", "Lingote de oro", False),
    ("lingote de cobre", "Lingote de hierro", False),
    ("bloque de hierro", "Bloque de hierba", False),
    ("mena de carbon", "Mena de hierro", False),
    ("espada de hierro", "Espada de diamante", False),
    # ...but a recognisably sloppy rendering of the right word still counts.
    ("lingote de fierro", "Lingote de hierro", True),
    ("bloquediamante", "Bloque de diamante", True),   # words run together
    ("pala", "Palo", False),
    ("pin", "Pan", False),
    ("pandemia", "Pan", False),
    ("tierra", "Piedra", False),
    # --- nothing was said --------------------------------------------
    ("", "Diamante", False),
    ("   ", "Diamante", False),
    ("...", "Diamante", False),
    # --- whisper hallucinating over silence --------------------------
    ("Gracias por ver el video", "Diamante", False),
    ("Subtítulos realizados por la comunidad de Amara.org", "Diamante", False),
    ("Thanks for watching!", "Diamante", False),
    ("[Música]", "Diamante", False),
    # Whisper stuck in a repetition loop. A real capture produced this shape
    # fifty phrases long, which both floods the overlay and gives the matcher
    # far more chances to find something.
    ("la ropa de la ropa de la ropa de la ropa de la ropa de la ropa",
     "Diamante", False),
    ("diamante diamante diamante diamante diamante diamante "
     "diamante diamante diamante diamante diamante diamante", "Diamante", False),
]


# Spelled differently by whisper, but the same sound. These are the "I said it
# right and it did not count" cases, and they only pass once the comparison is
# phonetic rather than orthographic.
SOUNDALIKE_CASES: list[tuple[str, str, bool]] = [
    ("yave", "Llave", True),              # yeismo
    ("sapato", "Zapato", True),           # seseo
    ("aber", "Haber", True),              # silent h
    ("baca", "Vaca", True),               # b and v are one phoneme
    ("bidrio", "Vidrio", True),
    ("jente", "Gente", True),
    ("karbon", "Carbón", True),
    ("sanaoria", "Zanahoria", True),
    ("lingote de fierro", "Lingote de hierro", True),
    # ...without making genuinely different words match.
    ("dinamita", "Diamante", False),
    ("pala", "Palo", False),
    ("tierra", "Piedra", False),
    ("lingote de oro", "Lingote de hierro", False),
    ("bloque de hierro", "Bloque de hierba", False),
]

# A rambling or multi-attempt transcript must not get extra chances to match.
# These are the "I said something totally wrong and it counted" cases.
RAMBLE_CASES: list[tuple[str, str, bool]] = [
    ("eh no se como se dice esto en espanol", "Diamante", False),
    ("pues la verdad no tengo ni idea", "Piedra", False),
    ("uno dos tres cuatro cinco seis siete", "Diamante", False),
    ("the quick brown fox jumps over the lazy dog", "Palo", False),
    ("mesa silla ventana puerta ordenador", "Manzana", False),
    # Two muddled attempts, which is what meshing used to produce.
    ("dia dia diamante", "Diamante", True),
    ("piedra no espera diamante", "Diamante", True),
]

# Tier-3 prompts are whole sentences. Saying one word of one is not saying it,
# however right that word is -- token_set_ratio used to score exactly this
# case 100 because the transcript was a subset of the target.
SENTENCE_CASES: list[tuple[str, str, bool]] = [
    ("he encontrado un diamante", "He encontrado un diamante", True),
    ("eh he encontrado un diamante", "He encontrado un diamante", True),
    ("estoy fabricando una espada de diamante",
     "Estoy fabricando una espada de diamante", True),
    # ...and the subset loophole stays shut.
    ("diamante", "He encontrado un diamante", False),
    ("espada", "Estoy fabricando una espada de diamante", False),
    ("espada de diamante", "Estoy fabricando una espada de diamante", False),
    ("fabricando", "Estoy fabricando una espada de diamante", False),
    # Naming the wrong item in an otherwise fluent sentence still fails.
    ("estoy fabricando una espada de madera",
     "Estoy fabricando una espada de diamante", False),
]


# Short one-word names cannot be decided against the target alone: "pala" is
# a 75 against "Palo" and "palo" is a 75 against "Pala", and a four-letter
# word has no room for a single mis-heard letter at a threshold of 80. The
# vocabulary is what settles it, so each case carries the names in play.
#
# (transcript, target, other item names, should it pass?)
RIVAL_CASES: list[tuple[str, str, tuple[str, ...], bool]] = [
    # Said correctly, one letter lost in transcription, and nothing else in
    # the game is closer to what was heard. Measured: whisper really does
    # answer "Cubo" with "Cuba".
    ("Cuba.", "Cubo", ("Pala", "Palo", "Pan", "Hilo", "Tolva"), True),
    ("¡Cuba!", "Cubo", ("Pala", "Palo", "Pan", "Hilo", "Tolva"), True),
    # A different real item, scoring 86 against the target. This was the whole
    # of the measured false-accept rate.
    ("Hielo.", "Hilo", ("Hielo", "Cubo", "Pan"), False),
    # Both halves of a real minimal pair stay shut, in both directions.
    ("pala", "Palo", ("Pala", "Pan"), False),
    ("palo", "Pala", ("Palo", "Pan"), False),
    # No vocabulary, no slack: the strict behaviour is the fallback.
    ("Cuba.", "Cubo", (), False),
    # Longer names are outside the mechanism entirely.
    ("espada de hierro", "Espada de hierro", ("Espada de oro",), True),
    ("espada de oro", "Espada de hierro", ("Espada de oro",), False),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    failures = 0
    for transcript, target, rivals, expected in RIVAL_CASES:
        result = match(transcript, target, threshold=THRESHOLD, language="es_es",
                       rivals=rivals)
        ok = result.passed == expected
        if not ok:
            failures += 1
        if not ok or args.verbose:
            flag = "ok  " if ok else "FAIL"
            print(f"{flag} score={result.score:3d} "
                  f"want={'pass' if expected else 'fail'} "
                  f"got={'pass' if result.passed else 'fail'}  "
                  f"{transcript!r} vs {target!r} among {len(rivals)} names"
                  f"{' -> ' + result.rival if result.rival else ''}")
            if not ok or args.verbose:
                print(f"       {result.breakdown}")

    for transcript, target, expected in (CASES + SOUNDALIKE_CASES + RAMBLE_CASES
                                        + SENTENCE_CASES + OBSERVED_CASES):
        result = match(transcript, target, threshold=THRESHOLD, language="es_es")
        ok = result.passed == expected
        if not ok:
            failures += 1
        if not ok or args.verbose:
            flag = "ok  " if ok else "FAIL"
            note = " [hallucination]" if result.hallucination else ""
            print(
                f"{flag} score={result.score:3d} "
                f"want={'pass' if expected else 'fail'} got={'pass' if result.passed else 'fail'}  "
                f"{transcript!r} vs {target!r}{note}"
            )
            if not ok or args.verbose:
                print(f"       {result.breakdown}")

    total = (len(CASES) + len(SOUNDALIKE_CASES) + len(RAMBLE_CASES)
             + len(SENTENCE_CASES) + len(OBSERVED_CASES) + len(RIVAL_CASES))
    print(f"\n{total - failures}/{total} cases passed (threshold {THRESHOLD})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
