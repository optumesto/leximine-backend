# Contributing

The most useful thing anyone can do here is **fix a sentence**.

Outside Spanish, the sentences this game asks for were written by someone who
does not speak the language. They have been checked by machine — register,
particles, article agreement, and a model that was itself tested against
planted errors first — and that catches a wrong particle. It does not catch a
sentence that is grammatical and simply not what anyone would say.

If you speak Japanese, Russian or French and something read oddly, you are the
only person who can tell us.

---

## Reporting a sentence

In game, press **N** while the prompt is up, or just after. It writes the exact
prompt to `config/leximine-reports.txt` and prints it in chat:

```
2026-11-03 14:02   ja_jp   tier 3   ダイヤモンドの剣   ダイヤモンドの剣を後で使うために取っておきます   I will keep a diamond sword to use later
```

Open an issue with that line. You do not need to know what is wrong with it —
"this sounds odd" from someone who speaks the language is worth more than a
confident guess from someone who does not.

---

## Where the sentences live

All of it is in `app/phrases.py`, in plain Python, one block per language.

```python
_JA_SENTENCES = {
    "pickup": [
        (0, "{name}を持っています", "I have {en_article} {en_name}"),
        ...
```

Each frame is `(level, what to say, what it means)`. `level` is the lowest
rung that unlocks it — 0 appears immediately, 3 only once the player has a lot
of correct answers behind them — so the pool widens with progress rather than
swapping one memorised sentence for another.

`{name}` is the item. The English is shown to the player, so it has to match
what the sentence actually says, not just name the item.

### Rules each pack is written around

These are enforced by checks, and they exist because getting them wrong
teaches the mistake to everybody who plays in that language.

- **Japanese**: polite register (です/ます) throughout, and no spaces. Mixing
  plain and polite across frames teaches that they are interchangeable.
- **Russian**: every frame keeps the item in the **nominative**. Russian
  declines the noun *and* its adjectives, and Minecraft ships the nominative,
  so «У меня есть X» and «Это X» are safe where «Я нашёл X» is not.
- **French**: the frames need the item's gender, which Minecraft does not
  ship, so `_FR_GENDER` maps head nouns to `m` / `f` / `mp` / `fp`. Note the
  partitive: an indefinite article after a negation becomes *de*, so
  "personne n'a **une** épée" is wrong. Two frames were rewritten for exactly
  that.

### Adding a language

A pack is a dict of frames plus an `article` hook. Register it in
`_PHRASE_PACKS` and tiers 3–4 start producing sentences; without one, every
tier asks for the bare item name.

If the language needs data the game does not provide — gender, case — add a
`known` hook so an item the pack cannot handle **falls back to tier 2 rather
than guessing**. Partial coverage is fine. Wrong output is not: French covers
90% of items and stops there deliberately.

German is the open one, and it is genuinely hard: it compounds (so there is
no head noun to cut), compound gender follows the final element and needs a
lexicon to split, it needs case, and the renderer lowercases the item name —
which is wrong for a language that capitalises its nouns.

---

## Checking your change

```powershell
.venv\Scripts\python scripts\test_phrases.py
```

Then look at the result in game, or ask the backend directly:

```
http://127.0.0.1:8760/translate?key=item.minecraft.diamond_sword&lang=ja_jp&tier=3
```

**Please do not generate grammar data with a language model.** It was measured
here before being trusted: on French noun genders a local 12B scored 47/50,
and the three it missed (`oeuf`, `pierre`, `or`) head over a hundred items
between them. As a reviewer of finished work it earns its keep; as the author
of it, it ships "une œuf" to every player.

---

## The rest of the code

`PROTOCOL.md` is the wire format. `README.md` covers the matcher, the
thresholds and why they are what they are — most of the numbers in it were
measured rather than chosen, and the reasoning is written down next to them.
If you are changing one, the measurement that produced it is the thing to
argue with.

Licence: MPL-2.0. The Minecraft mod half is MIT, in its own repository.
