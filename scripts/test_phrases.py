# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Table test for tiered prompts. No GPU, no server, runs in milliseconds.

    python scripts/test_phrases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Polish, German and Japanese frames all carry non-cp1252 characters,
# and the Windows console defaults to cp1252: without this the run
# dies partway through and reads as a failure in the pack.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

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


def _pl_pack() -> dict:
    """The registered Polish pack, for asserting on what it does NOT have."""
    from app.phrases import _PHRASE_PACKS

    return _PHRASE_PACKS["pl"]


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


    # ------------------------------------------------------------------
    # Polish
    # ------------------------------------------------------------------
    # The Polish pack's whole safety argument is that no frame touches the
    # item name and nothing in a frame agrees with it. That is not a claim
    # to take on trust -- it is checked mechanically below, because the
    # moment somebody adds "Mam {name}" the guarantee is gone and every
    # feminine item starts teaching a wrong accusative.
    print("\n-- Polish: the name is never inflected --")
    from app.phrases import _PL_LEGENDARY, _PL_SENTENCES  # noqa: E402

    # One of each class that would break if a frame governed a case or
    # agreed in number.
    PL_NAMES = [
        ("Akacjowa furtka", "feminine, accusative would be 'akacjowa,furtke,'"),
        ("Akacjowe liscie", "plural, 'jest' would have to become 'sa,'"),
        ("Akacjowy przycisk", "masculine inanimate"),
        ("Debowe deski", "plural"),
        ("Sztabka netherytu", "noun + genitive attribute"),
        ("Diamentowy miecz", "masculine, the common case"),
    ]
    for name, why in PL_NAMES:
        bad = []
        for tier in (3, 4):
            for level in range(MAX_LEVEL + 1):
                for context in ("craft", "pickup", "default"):
                    pool = frames_for("pl_pl", tier, context, level)
                    for _ in range(len(pool) * 6):
                        text, _en, templated = sentence_for(
                            name, "pl_pl", context, tier=tier, level=level,
                            english_name="Thing")
                        if not templated:
                            bad.append("fell back to tier 2")
                        elif name.lower() not in text:
                            bad.append(text)
        ok = not bad
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:20} survives verbatim"
              f"   ({why})"
              f"{'' if ok else '  e.g. ' + bad[0]}")

    print("\n-- Polish: no frame can agree with the item --")
    # Words that would force a choice the pack cannot make:
    #   jest/sa,  -- number agreement with the item
    #   mam/robie,/widze,/znalaz* -- govern the accusative, or mark the
    #                                speaker's gender in the past tense
    # Verbs that would agree with the item, or mark the speaker's gender.
    # Banned anywhere in a frame.
    PL_BANNED_ANYWHERE = ("jest", "sa,", "mam", "robie,", "widze,",
                          "znalaz", "zrobi", "czekalem", "czekalam")
    # Prepositions govern a case, but only over the noun they actually
    # introduce. "Dluga droga, a na koncu X" is fine: "na" governs "koncu"
    # and X is an apposition. So this is checked POSITIONALLY -- what
    # matters is the word immediately before the item.
    PL_PREPOSITIONS = {"na", "w", "we", "z", "ze", "do", "o", "po", "przez",
                       "pod", "nad", "za", "od", "u", "przy", "bez", "dla"}

    def _fold(text):
        trans = str.maketrans({"\u0105": "a,", "\u0119": "e,", "\u0142": "l",
                               "\u015b": "s", "\u0107": "c", "\u017c": "z",
                               "\u017a": "z", "\u0144": "n", "\u00f3": "o"})
        return text.lower().translate(trans)

    all_frames = [f for pool in _PL_SENTENCES.values() for f in pool]
    all_frames += _PL_LEGENDARY
    for _level, template, _en in all_frames:
        folded = _fold(template)
        hits = [w for w in PL_BANNED_ANYWHERE if w in folded.replace("{name}", "")]
        # A preposition is never separated from its noun by punctuation, so
        # anything after the last comma, colon or dash is a fresh phrase.
        # That is what tells the interjection in "O, {name}" ("Oh, a sword")
        # apart from the preposition in "o {name}" ("about a sword").
        lead = folded.split("{name}")[0]
        for mark in (",", ":", "-", ";"):
            lead = lead.rsplit(mark, 1)[-1]
        before = lead.split()
        if before and before[-1] in PL_PREPOSITIONS:
            hits.append("governed by '%s'" % before[-1])
        ok = not hits
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {template:42}"
              f"{'' if ok else '  governs/agrees: ' + str(hits)}")

    print("\n-- Polish: every item is covered, no gender table --")
    # French and German fall back to tier 2 for an unlisted head. Polish
    # must never do that, because it has no table to be missing from.
    pl_pack_has_known = "known" in _pl_pack()
    ok = not pl_pack_has_known
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} no 'known' hook, so coverage is total")

    for name in ("Zupa z gulaszu", "Plonaca rzecz", "Cokolwiek"):
        _t, _e, templated = sentence_for(name, "pl_pl", "default", tier=3,
                                         level=0, english_name="Thing")
        failures += 0 if templated else 1
        print(f"  {'ok  ' if templated else 'FAIL'} {name:20} still reaches tier 3")

    print("\n-- Polish: frames exist at every level and context --")
    for context in ("craft", "pickup", "default"):
        for level in range(MAX_LEVEL + 1):
            n = len(frames_for("pl_pl", 3, context, level))
            ok = n >= 2
            failures += 0 if ok else 1
            print(f"  {'ok  ' if ok else 'FAIL'} {context:8} level {level}"
                  f"  {n} frames")
    n_leg = len(frames_for("pl_pl", 4, "default", MAX_LEVEL))
    ok = n_leg >= 6
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} legendary pool  {n_leg} frames")

    print(f"\n{'PASSED' if failures == 0 else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
