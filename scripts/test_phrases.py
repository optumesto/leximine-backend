# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Table test for tiered prompts. No GPU, no server, runs in milliseconds.

    python scripts/test_phrases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.phrases import (  # noqa: E402
    MAX_LEVEL, expected_for, frames_for, head_noun, sentence_for,
)

_ARTICLES = {"un", "una", "unos", "unas"}

# (item name, language, expected tier-1 text)
HEAD_NOUN_CASES = [
    ("Espada de hierro", "es_es", "Espada"),
    ("Pico de piedra", "es_es", "Pico"),
    ("Tablones de roble", "es_es", "Tablones"),
    ("Mesa de trabajo", "es_es", "Mesa"),
    ("Botas de diamante", "es_es", "Botas"),
    ("Épée en bois", "fr_fr", "Épée"),
    ("Planches de chêne", "fr_fr", "Planches"),
    # Nothing safe to cut: already one word, or an adjective rather than a
    # material, or a compound language.
    ("Piedra", "es_es", "Piedra"),
    ("Pan", "es_es", "Pan"),
    ("Manzana dorada", "es_es", "Manzana dorada"),
    ("Eisenschwert", "de_de", "Eisenschwert"),
    ("Diamantschwert", "de_de", "Diamantschwert"),
]

# (item name, expected article) -- the part that must not teach bad grammar
ARTICLE_CASES = [
    ("Espada de diamante", "una"),
    ("Pico de netherita", "un"),
    ("Casco de hierro", "un"),
    ("Túnica de cuero", "una"),
    ("Manzana dorada", "una"),
    ("Pan", "un"),
    ("Botas de diamante", "unas"),
    ("Tablones de roble", "unos"),
    ("Leche", "una"),           # -e but feminine
    ("Llave", "una"),           # -e but feminine
    ("Hacha de diamante", "un"),  # feminine, stressed initial a
    ("Agua", "un"),               # same rule
    ("Mapa", "un"),               # -a but masculine
    ("Problema", "un"),
    ("Diamante", "un"),
    ("Piedra", "una"),
]


def main() -> int:
    failures = 0

    print("-- tier 1: cutting a name down --")
    for name, lang, want in HEAD_NOUN_CASES:
        got, simplified = head_noun(name, lang)
        ok = got == want
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:22} {lang}  -> {got!r}"
              f"{'' if ok else f'  wanted {want!r}'}"
              f"{'  (unchanged)' if not simplified else ''}")

    # Checked across every frame at every level rather than at one fixed word
    # position: the frames vary now, so "the third word" stopped being the
    # article the moment the pool grew.
    print("\n-- tiers 3-4: the article has to be right, in every frame --")
    for name, want in ARTICLE_CASES:
        seen: set[str] = set()
        bad: list[str] = []
        for tier in (3, 4):
            for level in range(MAX_LEVEL + 1):
                for context in ("craft", "pickup", "default"):
                    for _ in range(len(frames_for("es_es", tier, context, level)) * 4):
                        text, _english, templated = sentence_for(
                            name, "es_es", context, tier=tier, level=level,
                            english_name="Thing")
                        if not templated:
                            bad.append("no frame used")
                            continue
                        found = [w for w in text.lower().split() if w in _ARTICLES]
                        if not found:
                            bad.append(f"no article in {text!r}")
                        else:
                            seen.update(found)
        ok = not bad and seen == {want}
        failures += 0 if ok else 1
        detail = f"  wanted {want!r}, saw {sorted(seen)}" if not ok else ""
        print(f"  {'ok  ' if ok else 'FAIL'} {name:22} -> {sorted(seen)}{detail}")
        for problem in bad[:2]:
            print(f"       {problem}")

    # A plural item must never be handed a singular verb. "Esto es unos
    # élitros" shipped and was only caught by looking at a screenshot.
    print("\n-- plural items get plural verbs --")
    _SINGULAR_VERBS = (" es ", " es.", "esto es", "se encuentra ")
    for name in ("Élitros", "Tablones de roble", "Botas de diamante"):
        bad: list[str] = []
        seen = 0
        for tier in (3, 4):
            for level in range(MAX_LEVEL + 1):
                for context in ("craft", "pickup", "default"):
                    for _ in range(len(frames_for("es_es", tier, context, level)) * 6):
                        text, english, templated = sentence_for(
                            name, "es_es", context, tier=tier, level=level,
                            english_name="Elytra")
                        if not templated:
                            continue
                        seen += 1
                        lowered = " " + text.lower() + " "
                        for singular in _SINGULAR_VERBS:
                            if singular in lowered:
                                bad.append(text)
                        if " is " in english.lower():
                            bad.append(english)
        ok = not bad
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:22} {seen} frames checked")
        for problem in sorted(set(bad))[:3]:
            print(f"       {problem!r}")

    print("\n-- progress widens the pool rather than swapping it --")
    previous = 0
    for level in range(MAX_LEVEL + 1):
        pool = frames_for("es_es", 3, "craft", level)
        grew = len(pool) > previous
        failures += 0 if grew else 1
        print(f"  {'ok  ' if grew else 'FAIL'} level {level}: {len(pool)} frame(s)")
        previous = len(pool)

    print("\n-- tiers end to end --")
    for tier in (1, 2, 3, 4):
        expected = expected_for("Espada de diamante", "es_es", tier, "craft",
                                english_name="Diamond Sword", level=MAX_LEVEL)
        print(f"  tier {tier}: {expected.text!r}")
        print(f"           en: {expected.english!r}  keys={list(expected.key_tokens)}")
        if not expected.key_tokens:
            failures += 1
            print("       FAIL: no key tokens")
        # The gloss has to describe the whole prompt. At tiers 3-4 a bare
        # "Diamond Sword" would mean the frame was never translated.
        if tier >= 3 and expected.english.lower() == "diamond sword":
            failures += 1
            print("       FAIL: sentence glossed as just the item name")
        if not expected.english:
            failures += 1
            print("       FAIL: no English gloss")

    # A language with no sentence pack must fall back rather than invent
    # grammar it cannot check. German used to be the example here; it has a
    # pack now, so this uses one that still does not. The invariant is the
    # point, not the language.
    nopack = expected_for("Spada di diamante", "it_it", 3, "craft")
    fallback_ok = nopack.tier == 2 and not nopack.templated
    failures += 0 if fallback_ok else 1
    print(f"\n  {'ok  ' if fallback_ok else 'FAIL'} it_it (no pack) tier 3 falls "
          f"back to tier {nopack.tier}: {nopack.text!r}")

    # German does have a pack now, and two things about it must hold.
    german = expected_for("Diamantschwert", "de_de", 3, "craft")
    made_sentence = german.tier == 3 and german.templated
    failures += 0 if made_sentence else 1
    print(f"  {'ok  ' if made_sentence else 'FAIL'} de_de tier 3 builds a "
          f"sentence: {german.text!r}")

    # German capitalises nouns mid-sentence. Lower-casing one is the most
    # visible spelling error this feature could teach, so it is asserted
    # rather than left to review.
    cap_ok = "Diamantschwert" in german.text
    failures += 0 if cap_ok else 1
    print(f"  {'ok  ' if cap_ok else 'FAIL'} de_de keeps the noun capitalised")

    # An item whose gender is not in the table must fall back, not guess.
    # "Schild" is der Schild (shield) and das Schild (sign); both exist in
    # Minecraft, so it is excluded on purpose.
    shield = expected_for("Schild", "de_de", 3, "craft")
    refused = shield.tier == 2 and not shield.templated
    failures += 0 if refused else 1
    print(f"  {'ok  ' if refused else 'FAIL'} de_de refuses ambiguous 'Schild': "
          f"tier {shield.tier}")

    # A genitive attribute follows its head, so the head of "Trank des
    # Befalls" is Trank. Taking the last word instead gets every German
    # compound right and this construction wrong, silently, with the article
    # agreeing with the wrong noun.
    print("")
    print("-- German: the head comes before a genitive attribute --")
    for name, want in (("Trank des Befalls", "ein"),
                       ("Trank der Heilung", "ein"),
                       ("Herz des Meeres", "ein"),
                       ("Buch des Wissens", "ein"),
                       ("Verweiltrank der Schwaeche", "ein")):
        built = expected_for(name, "de_de", 3, "craft", english_name=name)
        ok = built.templated and f" {want} " in f" {built.text} "
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:26} -> {built.text!r}")

    # der Schild is a shield, das Schild is a sign. Every one-word compound
    # in the game means a sign or a plate; the 17 names that mean a shield
    # are all two words ("Schwarzer Schild"), which the ambiguity guard
    # catches before the suffix table is read. Both halves are asserted,
    # because adding -schild to the table is what makes the guard
    # load-bearing rather than merely tidy.
    print("")
    print("-- German: -schild compounds vs the bare noun --")
    for name, should_build in (("Namensschild", True),
                               ("Eichenholzschild", True),
                               ("Hornschild", True),
                               ("Schwarzer Schild", False),
                               ("Schild", False)):
        built = expected_for(name, "de_de", 3, "craft", english_name=name)
        ok = built.templated is should_build
        failures += 0 if ok else 1
        verb = "builds " if should_build else "refuses"
        print(f"  {'ok  ' if ok else 'FAIL'} {verb} {name:20} tier {built.tier}")

    print(f"\n{'PASSED' if failures == 0 else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
