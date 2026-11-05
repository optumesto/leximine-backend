# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Turn an item name into what the player is actually asked to say.

Four tiers, scaled to how valuable the item is:

  1. the bare noun          "Espada"
  2. the full item name     "Espada de hierro"
  3. a sentence             "He encontrado una espada de diamante"
  4. a harder sentence      "Después de mucho tiempo, he conseguido una élitra"

Tier 2 is simply the name Minecraft already ships, so the work here is tier 1
(cutting a name down) and tiers 3-4 (building a sentence around it).

**The sentence is not fixed.** Each tier and context has a pool of frames, and
which of them are eligible depends on how much the player has already got
right. Saying one memorised sentence a hundred times teaches that sentence,
not the language; the pool widens and the grammar gets more demanding as the
count goes up, so the same diamond sword asks for "He encontrado una espada de
diamante" early on and "Mientras exploraba, encontre una espada de diamante"
later.

Every frame carries its own English translation, because the player has to
know what they are saying. Glossing only the item name tells them what a
diamond sword is and leaves "estoy fabricando" as noise to be mimicked.

Three things constrain this more than they first appear:

* **Tier 1 cannot always simplify.** Romance names are "noun + connector +
  material" and cut cleanly, but German builds compounds -- "Eisenschwert" is
  one word, and reducing it to "Schwert" needs real morphology. Where a name
  cannot be cut safely it is left whole, which costs nothing: a tier-1 item
  has a short name anyway.
* **Tiers 3-4 need grammar, not a format string.** "un espada" is wrong and
  teaching it would be worse than not having the feature. Sentences are only
  produced for languages with a phrase pack below, where the article can be
  chosen correctly; everything else falls back to tier 2.
* **The frames avoid the subjunctive on purpose.** "No puedo creer que tengo"
  is the kind of thing a learner will hear and a grammarian will mark wrong.
  Everything here stays in constructions that are safe to imitate.
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass, field

# Words that join a noun to its material: "espada DE hierro", "épée EN bois".
_CONNECTORS: dict[str, frozenset[str]] = {
    "es": frozenset({"de", "del", "en"}),
    "fr": frozenset({"de", "d", "en", "du", "des", "à"}),
    "it": frozenset({"di", "in", "del", "della", "da"}),
    "pt": frozenset({"de", "do", "da", "em"}),
    "en": frozenset({"of"}),
}


@dataclass(frozen=True)
class Expected:
    """What to say, and which parts of it actually have to be right."""

    text: str
    """The full prompt shown to the player and matched against."""

    key_tokens: tuple[str, ...] = ()
    """
    Words that must be present for a pass, normalised.

    For a sentence this is the item's own words, not the frame around them:
    getting "espada de diamante" right while fumbling "estoy fabricando" is a
    better attempt than the reverse, and gating every word of a long sentence
    equally makes tier 3 unreasonable.
    """

    tier: int = 1
    item_name: str = ""
    """The plain item name, always, for the HUD and the vocabulary list."""

    english: str = ""
    """
    What the prompt means, in English.

    The whole sentence, not just the item. A player shown "Estoy fabricando
    una espada de diamante" and glossed only "Diamond Sword" has been told
    what one word means and left to mimic the rest as noise.
    """

    level: int = 0
    """Which rung of the frame pool this prompt was drawn from."""

    simplified: bool = False
    templated: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


def _base(language: str) -> str:
    return (language or "").split("_")[0].split("-")[0].lower()


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


# ----------------------------------------------------------------------
# Tier 1: cut a name down to its head noun
# ----------------------------------------------------------------------
def head_noun(name: str, language: str) -> tuple[str, bool]:
    """("Espada de hierro", "es") -> ("Espada", True).

    Returns the name unchanged with False when it cannot be cut safely.
    """
    connectors = _CONNECTORS.get(_base(language))
    if not connectors:
        return name, False

    tokens = name.split()
    if len(tokens) < 3:
        # "Manzana dorada" is an adjective, not a material, and "Pan" has
        # nothing to cut. Dropping a trailing adjective would quietly change
        # which item the player is naming.
        return name, False

    for index, token in enumerate(tokens[1:], start=1):
        bare = _strip_accents(token.lower()).rstrip("'")
        if bare in connectors:
            return " ".join(tokens[:index]), True
    return name, False


# ----------------------------------------------------------------------
# Tier 3: build a sentence
# ----------------------------------------------------------------------
# Spanish gender. The -o/-a rule covers most item names; these are the ones it
# gets wrong, and a wrong article is exactly the thing this feature must not
# teach.
_ES_MASCULINE_IN_A = frozenset({
    "dia", "mapa", "problema", "idioma", "clima", "tema", "sistema",
    "planeta", "sofa", "tranvia", "pijama", "programa",
})
_ES_FEMININE_NOT_IN_A = frozenset({
    "carne", "leche", "llave", "nieve", "sangre", "fuente", "noche", "parte",
    "gente", "muerte", "suerte", "torre", "nube", "red", "sal", "miel",
    "piel", "flor", "luz", "nariz", "pared", "ciudad", "col", "liebre",
})
# Feminine nouns beginning with a stressed "a" take the masculine article:
# "un hacha", "el agua". Getting this wrong is the classic tell.
_ES_STRESSED_A = frozenset({"agua", "hacha", "alma", "aula", "arma", "ala", "ancla"})

_ES_FEMININE_SUFFIXES = ("cion", "sion", "dad", "tad", "umbre", "ez", "triz")


def _es_is_plural(bare: str) -> bool:
    return len(bare) > 3 and bare.endswith("s")


def _es_is_feminine(bare: str) -> bool:
    singular = bare[:-2] if bare.endswith("es") else bare.rstrip("s")
    if singular in _ES_MASCULINE_IN_A:
        return False
    if singular in _ES_FEMININE_NOT_IN_A:
        return True
    if singular.endswith(_ES_FEMININE_SUFFIXES):
        return True
    return singular.endswith("a")


def _es_article(name: str) -> str:
    bare = _strip_accents(name.split()[0].lower())
    feminine = _es_is_feminine(bare)
    plural = _es_is_plural(bare)
    if plural:
        return "unas" if feminine else "unos"
    if feminine and _strip_accents(bare).rstrip("s") in _ES_STRESSED_A:
        return "un"
    return "una" if feminine else "un"


def _en_article(english_name: str, plural: bool) -> str:
    """"a" / "an" / "some", to match the article the target language chose.

    Vowel *letter*, not vowel sound -- "an hour" and "a unicorn" are both
    wrong by this rule. No Minecraft item name hits either case, and the
    alternative is a pronunciation dictionary for a gloss.
    """
    if plural:
        return "some"
    first = _strip_accents((english_name or "x").strip().lower())[:1]
    return "an" if first in "aeiou" else "a"


# Sentence frames, per language, per context, as
# (level, target template, English template).
#
# `level` is the lowest rung that unlocks the frame, and every frame at or
# below the player's level stays in the pool -- so progress widens the range
# rather than swapping one memorised sentence for another.
#
# Written to be safe to imitate: no subjunctive, no construction a learner
# would be marked wrong for repeating.
_ES_SENTENCES: dict[str, list[tuple[int, str, str]]] = {
    "craft": [
        (0, "Hago {article} {name}", "I make {en_article} {en_name}"),
        (0, "Quiero fabricar {article} {name}", "I want to craft {en_article} {en_name}"),
        (1, "Estoy fabricando {article} {name}", "I am crafting {en_article} {en_name}"),
        (1, "Voy a fabricar {article} {name}", "I am going to craft {en_article} {en_name}"),
        (2, "Acabo de fabricar {article} {name}",
         "I have just crafted {en_article} {en_name}"),
        (2, "He fabricado {article} {name} con mis propias manos",
         "I have crafted {en_article} {en_name} with my own hands"),
        (3, "Cuando termine, tendré {article} {name}",
         "When I finish, I will have {en_article} {en_name}"),
        (3, "Trabajé mucho para fabricar {article} {name}",
         "I worked hard to craft {en_article} {en_name}"),
    ],
    "pickup": [
        (0, "Tengo {article} {name}", "I have {en_article} {en_name}"),
        # "hay" is invariable in Spanish; the English "is/are" is not.
        (0, "Aquí hay {article} {name}", "Here {is_en} {en_article} {en_name}"),
        (1, "He encontrado {article} {name}", "I have found {en_article} {en_name}"),
        (1, "Mira, {article} {name}", "Look, {en_article} {en_name}"),
        (2, "Acabo de encontrar {article} {name}",
         "I have just found {en_article} {en_name}"),
        (2, "No esperaba encontrar {article} {name}",
         "I was not expecting to find {en_article} {en_name}"),
        (3, "Mientras exploraba, encontré {article} {name}",
         "While I was exploring, I found {en_article} {en_name}"),
        (3, "Guardaré {article} {name} para más tarde",
         "I will keep {en_article} {en_name} for later"),
    ],
    "default": [
        (0, "Quiero {article} {name}", "I want {en_article} {en_name}"),
        (0, "{this} {is} {article} {name}",
         "{this_en} {is_en} {en_article} {en_name}"),
        (1, "Necesito {article} {name}", "I need {en_article} {en_name}"),
        (1, "Tengo {article} {name} en la mano", "I have {en_article} {en_name} in my hand"),
        (2, "Me gustaría tener {article} {name}",
         "I would like to have {en_article} {en_name}"),
        (2, "{article_capital} {name} siempre es útil",
         "{en_article_capital} {en_name} is always useful"),
        (3, "Si lo consigo, tendré {article} {name}",
         "If I get it, I will have {en_article} {en_name}"),
        (3, "Nunca salgo de casa sin {article} {name}",
         "I never leave home without {en_article} {en_name}"),
    ],
}

# Tier 4 is for the handful of things a world only yields once. The frames are
# longer on purpose -- by the time a player is holding a nether star they have
# said a great many short sentences.
_ES_LEGENDARY: list[tuple[int, str, str]] = [
    (0, "Por fin tengo {article} {name}", "At last I have {en_article} {en_name}"),
    (0, "{this} {is} {article} {name}", "{this_en} {is_en} {en_article} {en_name}"),
    (1, "He conseguido {article} {name}", "I have obtained {en_article} {en_name}"),
    (1, "No todos los días se {finds} {article} {name}",
     "You do not find {en_article} {en_name} every day"),
    (2, "Después de mucho tiempo, he conseguido {article} {name}",
     "After a long time, I have obtained {en_article} {en_name}"),
    (2, "Casi nadie llega a tener {article} {name}",
     "Almost nobody gets to have {en_article} {en_name}"),
    (3, "Nunca pensé que iba a encontrar {article} {name}",
     "I never thought I was going to find {en_article} {en_name}"),
    (3, "Me costó mucho trabajo conseguir {article} {name}",
     "It took me a lot of work to get {en_article} {en_name}"),
]

# ----------------------------------------------------------------------
# Japanese.
#
# The language that made tiers 3-4 worth generalising, and by luck the
# cheapest one to do correctly: no articles, no grammatical gender, and no
# number agreement on nouns. `_es_article` exists to choose between el/la/un/
# una; the Japanese equivalent has nothing to choose, so the frames below use
# only {name} and the article hook returns "".
#
# Two things they are written around:
#
# * **No spaces.** A frame is "{name}を作ります", not "{name} を 作ります".
#   Japanese does not space its words and inserting them would teach a
#   spacing convention that does not exist.
# * **Polite form throughout** (〜ます / 〜です). It is the register a learner
#   meets first, it is safe to say to anyone, and mixing it with plain form
#   across frames would teach that the two are interchangeable. They are not.
#
# The item name is dropped in unmodified. Japanese does not inflect nouns for
# case, so "ダイヤモンドの剣" is the same string as a subject, an object or on
# its own -- which is exactly why this pack needs no gender or case data and
# French, German and Russian will.
# ----------------------------------------------------------------------
_JA_SENTENCES: dict[str, list[tuple[int, str, str]]] = {
    "craft": [
        (0, "{name}を作ります", "I make {en_article} {en_name}"),
        (0, "{name}を作りたいです", "I want to craft {en_article} {en_name}"),
        (1, "{name}を作っています", "I am crafting {en_article} {en_name}"),
        (1, "これから{name}を作ります", "I am going to craft {en_article} {en_name}"),
        (2, "{name}を作ったばかりです", "I have just crafted {en_article} {en_name}"),
        (2, "自分で{name}を作りました", "I crafted {en_article} {en_name} myself"),
        (3, "時間をかけて{name}を作りました",
         "I took my time crafting {en_article} {en_name}"),
        (3, "もうすぐ{name}ができます", "{en_article_capital} {en_name} will be ready soon"),
    ],
    "pickup": [
        (0, "{name}を持っています", "I have {en_article} {en_name}"),
        (0, "ここに{name}があります", "Here is {en_article} {en_name}"),
        (1, "{name}を見つけました", "I have found {en_article} {en_name}"),
        (1, "見てください、{name}です", "Look, {en_article} {en_name}"),
        (2, "{name}を見つけたばかりです", "I have just found {en_article} {en_name}"),
        (2, "{name}が見つかると思いませんでした",
         "I was not expecting to find {en_article} {en_name}"),
        (3, "探検していた時に{name}を見つけました",
         "While I was exploring, I found {en_article} {en_name}"),
        # 後のために was flagged as unnatural; 後で使うために is how it is said.
        (3, "{name}を後で使うために取っておきます",
         "I will keep {en_article} {en_name} to use later"),
    ],
    "default": [
        (0, "{name}が欲しいです", "I want {en_article} {en_name}"),
        (0, "これは{name}です", "This is {en_article} {en_name}"),
        (1, "{name}が必要です", "I need {en_article} {en_name}"),
        (1, "手に{name}を持っています", "I have {en_article} {en_name} in my hand"),
        (2, "{name}を持ちたいです", "I would like to have {en_article} {en_name}"),
        (2, "{name}はいつも役に立ちます", "{en_article_capital} {en_name} is always useful"),
        (3, "もし手に入れたら、{name}を使います",
         "If I get it, I will use {en_article} {en_name}"),
        (3, "{name}なしでは出かけません", "I never go out without {en_article} {en_name}"),
    ],
}

_JA_LEGENDARY: list[tuple[int, str, str]] = [
    (0, "ついに{name}を手に入れました", "At last I have obtained {en_article} {en_name}"),
    (0, "これは{name}です", "This is {en_article} {en_name}"),
    (1, "{name}を手に入れました", "I have obtained {en_article} {en_name}"),
    (1, "{name}は毎日見つかるものではありません",
     "You do not find {en_article} {en_name} every day"),
    (2, "長い時間がかかりましたが、{name}を手に入れました",
     "It took a long time, but I obtained {en_article} {en_name}"),
    (2, "{name}を持っている人はほとんどいません",
     "Almost nobody has {en_article} {en_name}"),
    (3, "{name}を見つけられるとは思いませんでした",
     "I never thought I would be able to find {en_article} {en_name}"),
    (3, "{name}を手に入れるのはとても大変でした",
     "It was very hard to get {en_article} {en_name}"),
]


# ----------------------------------------------------------------------
# French.
#
# Needs what Japanese and Russian did not: the gender of the item. "un épée"
# is wrong and teaching it would be worse than having no sentences at all, and
# Minecraft ships no gender information -- "Épée en diamant" is just a string.
#
# So this table is hand-written. A local model was measured on the job first
# and got **47 of 50** common head nouns right, which sounds respectable and
# is not: it missed `oeuf`, `pierre` and `or`, between them the head of over a
# hundred items. Generating this automatically would have shipped "une œuf" to
# every French player.
#
# Keyed on the **head noun**, because the article agrees with that and not
# with the whole name: "Épée en diamant" and "Épée en fer" both take "une".
# That collapses 2,792 item names onto ~400 words, of which these are the
# common ones.
#
# **Partial coverage is the design, not a shortcut.** A head noun that is not
# here produces no sentence and the item falls back to tier 2, exactly as an
# unsupported language does. Adding a word turns sentences on for every item
# built from it; guessing one teaches bad French to everyone who plays it. So
# anything uncertain -- the heraldry vocabulary, the mangled extractions, the
# words that are adjectives rather than nouns -- is deliberately absent.
#
# "p" marks a name that is plural in French ("Bottes en fer"), which takes
# "des" rather than un/une. It is stored rather than guessed from a trailing
# s, because "bois", "grès" and "os" are singular and end in one.
_FR_GENDER: dict[str, str] = {
    # the big ones first
    "dalle": "f", "escalier": "m", "oeuf": "m", "œuf": "m", "potion": "f",
    "bloc": "m", "pancarte": "f", "tesson": "m", "terre": "f", "muret": "m",
    "champ": "m", "bordure": "f", "béton": "m", "flèche": "f", "carte": "f",
    "cuivre": "m", "disque": "m", "porte": "f", "trappe": "f", "crâne": "m",
    "bûche": "f", "bois": "m", "fleur": "f", "bateau": "m", "modèle": "m",
    "pousse": "f", "lit": "m", "gâteau": "m", "tapis": "m", "verre": "m",
    "minerai": "m", "ornement": "m", "motif": "m", "plaque": "f",
    "globe": "m", "symbole": "m", "barre": "f", "bannière": "f",
    "bougie": "f", "boîte": "f", "vitre": "f", "sac": "m", "harnais": "m",
    "bouclier": "m", "laine": "f", "coussin": "m", "teinture": "f",
    "bouton": "m", "barrière": "f", "pierre": "f", "portillon": "m",
    "étagère": "f", "armure": "f", "tête": "f", "tige": "f", "coffre": "m",
    "lanterne": "f", "corail": "m", "chaîne": "f", "champignon": "m",
    "ardoise": "f", "grès": "m", "ampoule": "f",
    # tools, armour, the things a player actually holds
    "épée": "f", "pioche": "f", "hache": "f", "pelle": "f", "houe": "f",
    "arc": "m", "casque": "m", "plastron": "m", "lingot": "m", "seau": "m",
    "livre": "m", "fiole": "f", "wagonnet": "m", "chaudron": "m",
    "enclume": "f", "cadre": "m", "cristal": "m", "éclat": "m",
    "écaille": "f", "soupe": "f", "pomme": "f", "buisson": "m",
    "torche": "f", "paratonnerre": "m", "tulipe": "f", "glace": "f",
    "statue": "f", "grille": "f", "roche": "f", "soufre": "m", "tuf": "m",
    "poudre": "f", "table": "f", "plante": "f", "pissenlit": "m",
    "sable": "m", "bâton": "m", "air": "m", "amas": "m", "basalte": "m",
    "capteur": "m", "feu": "m", "azalée": "f", "lance": "f",
    # food, mobs' drops, and the rest of the everyday vocabulary. Each of
    # these heads only one or two items, which undersells them: a carrot or a
    # compass is picked up far more often than the fifteenth variant of
    # copper stairs.
    "carotte": "f", "morue": "f", "saumon": "m", "poulet": "m", "mouton": "m",
    "lapin": "m", "côtelette": "f", "poisson": "m", "poisson-globe": "m",
    "tranche": "f", "pépite": "f", "oeil": "m", "œil": "m", "boule": "f",
    "poche": "f", "carapace": "f", "étoile": "f", "charge": "f", "clé": "f",
    "brique": "f", "boussole": "f", "canne": "f", "radeau": "m",
    "charbon": "m", "chorus": "m", "cacao": "m", "argile": "f", "toile": "f",
    "nid": "m", "ruche": "f", "cloche": "f", "balise": "f", "tonneau": "m",
    "meule": "f", "établi": "m", "composteur": "m", "comparateur": "m",
    "conduit": "m", "fabricateur": "m", "distributeur": "m", "dropper": "m",
    "entonnoir": "m", "jukebox": "m", "pupitre": "m", "levier": "m",
    "piston": "m", "générateur": "m", "fourneau": "m", "vase": "m",
    "chemin": "m", "passerelle": "f", "colonne": "f", "noyau": "m",
    "tas": "m", "ghast": "m", "bedrock": "m", "citrouille-lanterne": "f",
    "mosaïque": "f", "botte": "f", "lave": "f",
    # stone and terrain
    "obsidienne": "f", "prismarine": "f", "diorite": "f", "andésite": "f",
    "granite": "m", "calcite": "f", "gravier": "m", "neige": "f",
    "cinabre": "m", "nylium": "m", "druse": "f", "pointe": "f",
    "pilier": "m", "portail": "m", "vide": "m", "éponge": "f",
    "bibliothèque": "f", "alambic": "m", "pot": "m", "coeur": "m",
    "cœur": "m", "citrouille": "f", "lichen": "m",
    # plants, which Minecraft names with an agreeing adjective often enough
    # that the data confirms the gender itself: "Oeilchidée fermée",
    # "Houstonie bleue", "Nylium biscornu", "Andésite polie".
    "fleur": "f", "fougère": "f", "herbe": "f", "algue": "f", "allium": "m",
    "orchidée": "f", "houstonie": "f", "bleuet": "m", "muguet": "m",
    "marguerite": "f", "coquelicot": "m", "bambou": "m", "cactus": "m",
    "propagule": "f", "plant": "m", "gorgone": "f", "oeilchidée": "f",
    "grelampe": "f", "rose": "f",
    # plurals
    "feuilles": "fp", "planches": "fp", "briques": "fp", "barreaux": "mp",
    "bottes": "fp", "jambières": "fp", "graines": "fp", "racines": "fp",
    "rails": "mp", "lianes": "fp", "pierres": "fp", "herbes": "fp",
    "verrues": "fp", "baies": "fp", "betteraves": "fp", "carottes": "fp",
    "oeufs": "mp", "œufs": "mp", "débris": "mp", "hyphes": "fp",
}

#: Head words deliberately left out, so nobody "completes" the table with
#: them. They are not items:
#:   c418, lena, aaron          -- music disc artists
#:   s, vous, ce, ajoutez, en   -- format strings and UI messages
#:   projectile, pouvoir, durée, amélioration  -- tooltip labels
#:   bleu, gris, vert, haut, grande, petite, hautes  -- adjectives, not heads
#: and two whose gender I could not confirm from the data: `planturne` and
#: `torche-fleur`, both invented compounds with no agreeing adjective in any
#: string. Guessing them would be exactly the failure this table exists to
#: avoid; they cost two items each.


def _fr_head_word(name: str) -> str:
    head, _ = head_noun(name, "fr")
    first = head.split()[0] if head.split() else head
    return re.sub(r"[^\w'’-]", "", first, flags=re.UNICODE).lower()


def _fr_article(name: str) -> str:
    """un / une / des, or "" when the head noun is not in the table.

    Indefinite only. "le"/"la" elide to "l'" before a vowel, which is one more
    rule and one more way to be wrong; nothing here needs the definite form.
    """
    gender = _FR_GENDER.get(_fr_head_word(name), "")
    if gender.endswith("p"):
        return "des"
    if gender == "f":
        return "une"
    if gender == "m":
        return "un"
    return ""


def _fr_is_plural(name: str) -> bool:
    return _FR_GENDER.get(_fr_head_word(name), "").endswith("p")


def _fr_known(name: str) -> bool:
    return _fr_head_word(name) in _FR_GENDER


# Frames avoid two things on purpose. Adjectives, because they would have to
# agree with a gender the frame does not know at writing time. And "c'est",
# because it becomes "ce sont" for a plural item and the renderer's number
# words are Spanish -- "Voici" is true for both numbers and needs no help.
_FR_SENTENCES: dict[str, list[tuple[int, str, str]]] = {
    "craft": [
        (0, "Je fabrique {article} {name}", "I craft {en_article} {en_name}"),
        (0, "Je veux fabriquer {article} {name}",
         "I want to craft {en_article} {en_name}"),
        (1, "Je suis en train de fabriquer {article} {name}",
         "I am crafting {en_article} {en_name}"),
        (1, "Je vais fabriquer {article} {name}",
         "I am going to craft {en_article} {en_name}"),
        (2, "Je viens de fabriquer {article} {name}",
         "I have just crafted {en_article} {en_name}"),
        (2, "J'ai fabriqué {article} {name} moi-même",
         "I crafted {en_article} {en_name} myself"),
        (3, "J'ai mis du temps à fabriquer {article} {name}",
         "I took my time crafting {en_article} {en_name}"),
        (3, "Bientôt, j'aurai {article} {name}",
         "Soon I will have {en_article} {en_name}"),
    ],
    "pickup": [
        (0, "J'ai {article} {name}", "I have {en_article} {en_name}"),
        (0, "Voici {article} {name}", "Here is {en_article} {en_name}"),
        (1, "J'ai trouvé {article} {name}", "I have found {en_article} {en_name}"),
        (1, "Regardez, {article} {name}", "Look, {en_article} {en_name}"),
        (2, "Je viens de trouver {article} {name}",
         "I have just found {en_article} {en_name}"),
        (2, "Je ne pensais pas trouver {article} {name}",
         "I was not expecting to find {en_article} {en_name}"),
        (3, "Pendant que j'explorais, j'ai trouvé {article} {name}",
         "While I was exploring, I found {en_article} {en_name}"),
        (3, "Je garde {article} {name} pour plus tard",
         "I am keeping {en_article} {en_name} for later"),
    ],
    "default": [
        (0, "Je veux {article} {name}", "I want {en_article} {en_name}"),
        (0, "Voici {article} {name}", "Here is {en_article} {en_name}"),
        (1, "Il me faut {article} {name}", "I need {en_article} {en_name}"),
        (1, "J'ai {article} {name} dans la main",
         "I have {en_article} {en_name} in my hand"),
        (2, "J'aimerais avoir {article} {name}",
         "I would like to have {en_article} {en_name}"),
        (2, "Je garde toujours {article} {name} avec moi",
         "I always keep {en_article} {en_name} with me"),
        (3, "Si je le trouve, j'aurai {article} {name}",
         "If I find it, I will have {en_article} {en_name}"),
        (3, "Je ne pars jamais sans {article} {name}",
         "I never leave without {en_article} {en_name}"),
    ],
}

_FR_LEGENDARY: list[tuple[int, str, str]] = [
    (0, "J'ai enfin {article} {name}", "At last I have {en_article} {en_name}"),
    (0, "Voici {article} {name}", "Here is {en_article} {en_name}"),
    (1, "J'ai obtenu {article} {name}", "I have obtained {en_article} {en_name}"),
    # Both of the negative frames here were rewritten so the item is not the
    # direct object of a negated verb. French turns un/une into "de" in that
    # position -- "personne n'a d'épée", not "personne n'a une épée" -- and
    # the elision ("de" before a consonant, "d'" before a vowel) is a rule
    # the renderer has no way to apply. Avoiding the construction is cheaper
    # and cannot be subtly wrong.
    (1, "Ce n'est pas tous les jours qu'on trouve {article} {name}",
     "It is not every day that you find {en_article} {en_name}"),
    (2, "Après longtemps, j'ai obtenu {article} {name}",
     "After a long time, I obtained {en_article} {en_name}"),
    (2, "Très peu de gens ont {article} {name}",
     "Very few people have {en_article} {en_name}"),
    (3, "Je n'aurais jamais cru trouver {article} {name}",
     "I never thought I would find {en_article} {en_name}"),
    (3, "J'ai eu beaucoup de mal à obtenir {article} {name}",
     "It was very hard for me to obtain {en_article} {en_name}"),
]


# ----------------------------------------------------------------------
# Russian.
#
# No articles, which sounds easy and is not: Russian declines the noun *and*
# its adjectives, so "I found a diamond pickaxe" needs the accusative
# "алмазную кирку" where Minecraft ships the nominative "Алмазная кирка".
# Getting that wrong is not a small error -- it is the wrong word.
#
# **So every frame here keeps the item in the nominative.** "У меня есть X",
# "Это X", "Вот X" and "у меня будет X" all take it, and they are ordinary,
# high-frequency constructions rather than contortions written to dodge
# grammar. The cost is a narrower set of verbs than Spanish gets; the
# alternative is a full morphological engine, or teaching people wrong
# Russian.
#
# Two rules the frames follow, both enforced by the checks in the test script:
#
# * **Nothing starts with {name}.** The renderer lowercases the item name, and
#   a sentence has to begin with a capital.
# * **No genitive contexts** ("без X", "нет X"), which would need the noun
#   changed rather than merely placed.
# ----------------------------------------------------------------------
_RU_SENTENCES: dict[str, list[tuple[int, str, str]]] = {
    "craft": [
        (0, "Вот {name}", "Here is {en_article} {en_name}"),
        (0, "У меня есть {name}", "I have {en_article} {en_name}"),
        (1, "Теперь у меня есть {name}", "Now I have {en_article} {en_name}"),
        (1, "Это {name}", "This is {en_article} {en_name}"),
        (2, "Наконец-то у меня есть {name}", "At last I have {en_article} {en_name}"),
        (2, "Скоро у меня будет {name}", "Soon I will have {en_article} {en_name}"),
        (3, "После долгой работы у меня есть {name}",
         "After a lot of work I have {en_article} {en_name}"),
        (3, "Если всё получится, у меня будет {name}",
         "If all goes well, I will have {en_article} {en_name}"),
    ],
    "pickup": [
        (0, "Вот {name}", "Here is {en_article} {en_name}"),
        (0, "У меня есть {name}", "I have {en_article} {en_name}"),
        (1, "Смотрите, {name}", "Look, {en_article} {en_name}"),
        (1, "Здесь есть {name}", "There is {en_article} {en_name} here"),
        (2, "Теперь у меня есть {name}", "Now I have {en_article} {en_name}"),
        (2, "Наконец-то у меня есть {name}", "At last I have {en_article} {en_name}"),
        (3, "После долгих поисков у меня есть {name}",
         "After a long search I have {en_article} {en_name}"),
        (3, "Скоро у меня будет {name}", "Soon I will have {en_article} {en_name}"),
    ],
    "default": [
        (0, "Это {name}", "This is {en_article} {en_name}"),
        (0, "Вот {name}", "Here is {en_article} {en_name}"),
        (1, "У меня есть {name}", "I have {en_article} {en_name}"),
        (1, "Мне нравится {name}", "I like {en_article} {en_name}"),
        (2, "У меня в руках {name}", "I have {en_article} {en_name} in my hands"),
        (2, "Теперь у меня есть {name}", "Now I have {en_article} {en_name}"),
        (3, "Если повезёт, у меня будет {name}",
         "If I am lucky, I will have {en_article} {en_name}"),
        (3, "Когда-нибудь у меня будет {name}",
         "One day I will have {en_article} {en_name}"),
    ],
}

_RU_LEGENDARY: list[tuple[int, str, str]] = [
    (0, "Наконец-то у меня есть {name}", "At last I have {en_article} {en_name}"),
    (0, "Это {name}", "This is {en_article} {en_name}"),
    (1, "Теперь у меня есть {name}", "Now I have {en_article} {en_name}"),
    (1, "У меня наконец есть {name}", "I finally have {en_article} {en_name}"),
    (2, "После долгого времени у меня есть {name}",
     "After a long time I have {en_article} {en_name}"),
    # An em dash, not a comma: Russian marks this apposition with тире.
    (2, "Это большая редкость — {name}",
     "It is a great rarity, {en_article} {en_name}"),
    (3, "Я не думал, что у меня будет {name}",
     "I did not think I would have {en_article} {en_name}"),
    (3, "Это был долгий путь, но теперь у меня есть {name}",
     "It was a long road, but now I have {en_article} {en_name}"),
]


def _ru_article(name: str) -> str:
    """Russian has no articles."""
    return ""


def _ru_is_plural(name: str) -> bool:
    """Not consulted: no frame here agrees with the item's number."""
    return False


def _ja_article(name: str) -> str:
    """Japanese has no articles. Kept so every pack has the same shape."""
    return ""


def _ja_is_plural(name: str) -> bool:
    """Japanese nouns do not mark number, so nothing here ever agrees."""
    return False


# Per language: how to pick the article, and the frames available.
# A language is only eligible for tiers 3-4 if it appears here.
_PHRASE_PACKS: dict[str, dict[str, object]] = {
    "es": {
        "article": _es_article,
        "plural": lambda name: _es_is_plural(_strip_accents(name.split()[0].lower())),
        "sentences": _ES_SENTENCES,
        "legendary": _ES_LEGENDARY,
    },
    "ja": {
        "article": _ja_article,
        "plural": _ja_is_plural,
        "sentences": _JA_SENTENCES,
        "legendary": _JA_LEGENDARY,
    },
    "ru": {
        "article": _ru_article,
        "plural": _ru_is_plural,
        "sentences": _RU_SENTENCES,
        "legendary": _RU_LEGENDARY,
    },
    "fr": {
        "article": _fr_article,
        "plural": _fr_is_plural,
        "sentences": _FR_SENTENCES,
        "legendary": _FR_LEGENDARY,
        # Optional. Only French has one: its frames need the item's gender,
        # and the table of genders is deliberately partial.
        "known": _fr_known,
    },
}

#: Levels run 0-3. Anything higher just stays at the top rung.
MAX_LEVEL = 3

#: Frames that only work for a singular noun, dropped when the item is
#: plural. Spanish agrees the verb and the adjective with the subject, so
#: "Unos tablones siempre es útil" needs "son útiles" -- two changes, not one,
#: and inventing adjective inflection here would be exactly the sort of guess
#: that teaches people wrong Spanish. Dropping the frame costs one option out
#: of eight; getting it wrong costs the point of the feature.
_SINGULAR_ONLY = frozenset({
    "{article_capital} {name} siempre es útil",
})


def supports_sentences(language: str) -> bool:
    return _base(language) in _PHRASE_PACKS


def sentence_languages() -> list[str]:
    """Base codes with a phrase pack, so callers need not reach into the dict."""
    return list(_PHRASE_PACKS)


def frames_for(language: str, tier: int, context: str, level: int,
               plural: bool = False) -> list[tuple[int, str, str]]:
    """Every frame this player has unlocked for this tier and context.

    Exposed so the tests can assert on the pool rather than on whichever
    member of it a given roll happened to produce.
    """
    pack = _PHRASE_PACKS.get(_base(language))
    if not pack:
        return []
    if tier >= 4:
        pool: list[tuple[int, str, str]] = pack["legendary"]  # type: ignore[assignment]
    else:
        by_context: dict[str, list] = pack["sentences"]  # type: ignore[assignment]
        pool = by_context.get(context) or by_context["default"]
    ceiling = max(0, min(int(level), MAX_LEVEL))
    return [frame for frame in pool
            if frame[0] <= ceiling and not (plural and frame[1] in _SINGULAR_ONLY)]


def sentence_for(name: str, language: str, context: str = "default",
                 tier: int = 3, level: int = 0,
                 english_name: str = "") -> tuple[str, str, bool]:
    """-> (what to say, what it means, whether a frame was actually used).

    One frame is drawn at random from everything unlocked. Random rather than
    fixed per item: the point is that the same diamond sword does not always
    ask the same thing.
    """
    pack = _PHRASE_PACKS.get(_base(language))
    if not pack:
        return name, english_name or name, False

    # A pack may not know every item. French needs the head noun's gender and
    # the table of genders is partial on purpose, so an unlisted word falls
    # back to tier 2 rather than producing "Je fabrique  épée" with the
    # article silently missing -- or worse, guessed.
    knows = pack.get("known")
    if knows is not None and not knows(name):  # type: ignore[operator]
        return name, english_name or name, False

    plural = bool(pack["plural"](name))  # type: ignore[operator]
    pool = frames_for(language, tier, context, level, plural)
    if not pool:
        return name, english_name or name, False

    _, template, english_template = random.choice(pool)
    article = pack["article"](name)  # type: ignore[operator]
    en_article = _en_article(english_name, plural)

    # Spanish agrees the verb with the subject, so a frame built around "es"
    # is simply wrong for a plural item -- "Esto es unos élitros" was on
    # screen before this existed. The verb forms are substituted rather than
    # written into the templates so one frame covers both numbers.
    target = template.format(
        article=article,
        article_capital=article.capitalize(),
        name=name.lower(),
        **{"is": "son" if plural else "es",
           "this": "Estos" if plural else "Esto",
           "finds": "encuentran" if plural else "encuentra"})
    english = english_template.format(
        en_article=en_article,
        en_article_capital=en_article.capitalize(),
        en_name=(english_name or name).lower(),
        **{"is_en": "are" if plural else "is",
           "this_en": "These" if plural else "This"})
    return target, english, True


# ----------------------------------------------------------------------
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _content_tokens(text: str) -> tuple[str, ...]:
    from .matching import FUNCTION_WORDS, normalize

    return tuple(token for token in normalize(text).split()
                 if token not in FUNCTION_WORDS)


def expected_for(item_name: str, language: str, tier: int,
                 context: str = "default", english_name: str = "",
                 level: int = 0) -> Expected:
    """What the player has to say for this item at this tier, and what it means.

    `level` is how far along the player is; it widens the pool of sentence
    frames rather than changing which tier an item falls into. Stakes follow
    the item, difficulty follows the player, and the two are deliberately
    separate.
    """
    tier = max(1, min(int(tier or 1), 4))
    level = max(0, min(int(level or 0), MAX_LEVEL))
    english_name = english_name or item_name
    notes: list[str] = []

    if tier == 1:
        text, simplified = head_noun(item_name, language)
        if not simplified and len(item_name.split()) > 1:
            notes.append("name could not be shortened for this language")
        return Expected(text=text, key_tokens=_content_tokens(text), tier=1,
                        item_name=item_name, english=english_name,
                        level=level, simplified=simplified, notes=tuple(notes))

    if tier == 2:
        return Expected(text=item_name, key_tokens=_content_tokens(item_name),
                        tier=2, item_name=item_name, english=english_name,
                        level=level)

    text, english, templated = sentence_for(
        item_name, language, context, tier=tier, level=level,
        english_name=english_name)
    if not templated:
        notes.append(f"no sentence pack for {language}; using the item name")
    return Expected(
        text=text,
        # Only the item's own words are required. The frame still counts
        # towards overall similarity, it just is not individually gated --
        # fumbling "estoy fabricando" is not the same as naming the wrong
        # item, and gating every word of a long sentence equally would make
        # the upper tiers unreasonable rather than hard.
        key_tokens=_content_tokens(item_name),
        tier=tier if templated else 2,
        item_name=item_name,
        english=english,
        level=level,
        templated=templated,
        notes=tuple(notes),
    )
