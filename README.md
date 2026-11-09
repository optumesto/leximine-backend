# leximine-backend

Pronunciation-checking server for the
[**Leximine**](https://github.com/optumesto/leximine) Minecraft mod. The
mod holds a picked-up item in limbo and asks the player to say its name in the
target language; this server decides whether they did. The two halves are
separate repositories and neither works alone; the
[release](https://github.com/optumesto/leximine/releases/latest) ships
both.

> **Just want to play?** Follow `INSTALL.md` in the Leximine folder, or run
> **`setup.cmd`** here and then **`check-setup.cmd`** if anything looks wrong.
> The rest of this file is about how the server works.

It is named after the mod it serves, but it is not written around it: this is
a generic speech backend that takes any language and any client, and nothing
in it is specific to Minecraft except where it reads its dictionaries from.
It carried the generic name `lingua-stt-backend` until 2026-11-05 — GitHub
redirects that URL, and `LINGUA_*` settings are still honoured.

**Licence: [MPL-2.0](LICENSE)** — weak, file-level copyleft. Use it in
anything, including commercial and closed-source work; if you modify these
files and ship the result, those files stay open. Files you add are yours.
The [mod half](https://github.com/optumesto/leximine) is MIT, separately.

Not under that licence, and not shipped: `lang/*.json` are Minecraft's own
translation strings, extracted from your local game install at first run.
They belong to Mojang.

It owns the dictionary and the verdict. The mod sends a translation key and
microphone audio, and gets back `success` or `fail` — so switching target
language is a server restart, not a mod rebuild.

**Stack:** FastAPI + websockets, faster-whisper on CUDA, `thefuzz` for the
comparison. See `PROTOCOL.md` for the wire format the mod implements.

---

## Quick start

Run **`setup.cmd`** once. It does both halves:

* builds the Python environment,
* puts a **Leximine Backend** shortcut on your desktop, and
* installs the mod into Minecraft as its own **Leximine** launcher profile,
  downloading Fabric API and checking it against its published checksum.

After that, playing is two steps, every time:

1. double-click **Leximine Backend**, wait for READY
2. launch Minecraft and pick the **Leximine** profile

That's it. `setup.cmd -Uninstall` reverses both.

**`check-setup.cmd`** inspects every piece — Minecraft, Fabric, the mod,
Python, the model, the GPU, this server, Ollama — and names whatever is
missing along with the fix. It reads and changes nothing, so it is the right
first move whenever something is not working.

### What READY looks like

```
  ======================================================
   READY - start Minecraft and pick something up.
  ======================================================

  language   es_es   (5710 words)
  model      small on cuda
  listening  ws://127.0.0.1:8760/ws
```

Keep that window open while you play; closing it stops the backend.

**The first launch does more.** On a machine that has never run this, the same
double-click creates the virtual environment, installs dependencies, extracts
the Spanish dictionary from your own Minecraft assets and downloads the speech
model — a few minutes and several hundred megabytes, once. Every launch after
that reaches READY in a second or two. Nothing is installed system wide and
nothing is added to startup.

Running it when it is already up is harmless: it reports the current model and
language and exits.

### The scripts

| | |
|---|---|
| `setup.cmd` | one-time: desktop shortcut + Minecraft profile |
| `setup.cmd -Uninstall` | removes both; worlds and this folder untouched |
| `start-backend.cmd` | what the shortcut runs; safe to double-click directly |
| `stop-backend.cmd` | only if the window was force-closed and the launcher keeps saying "already running" |

### Changing language

Edit `LEXIMINE_TARGET_LANG` in `.env` and restart, or pass it once with
`start_backend.ps1 -Language fr_fr`. The dictionary comes out of your own
Minecraft assets, so launch the game once with that language selected first.
In the game it is easier: the title screen has a picker.

**A dictionary is not the same as full support.** Tiers 3 and 4 ask for a
sentence, and a sentence needs a *phrase pack* in `app/phrases.py` — frames
plus whatever grammar that language needs to slot an item name into them.
Without one, every tier asks for the bare item name and the difficulty ladder
is flat. `/languages` reports which is which, and the in-game picker tags
them **full** or **words only**.

| | needs | state |
|---|---|---|
| Spanish | gender + number agreement | full |
| Japanese | nothing — no articles, gender or number | full |
| Russian | frames that keep the noun nominative | full |
| French | a table of head-noun genders (90% of items) | full, partial table |
| German | compound splitting, case, and noun capitalisation | **words only** |

Adding one is a dict of frames and an `article` hook. Two rules learned the
hard way, both now enforced by checks:

* **Do not generate the grammar data with a model.** Measured on French head
  nouns, the local judge got 47/50 — and the three it missed (`oeuf`,
  `pierre`, `or`) head over a hundred items between them. Used the other way
  round it earns its keep: asked for a second opinion on all 228 finished
  entries it disagreed 17 times, each one worth re-examining, and each one
  resolved in favour of the table.
* **Let the game settle it where it can.** French adjectives agree, and
  Minecraft ships the adjective — "Morue crue", "Andésite polie", "Nylium
  biscornu". Where one exists the gender is in the data, not in anyone's
  memory.
* **Partial is fine; wrong is not.** A pack may declare a `known` hook; an
  item it does not know falls back to tier 2 rather than guessing an article.
  French is at 90% of reachable items and stops there on purpose: the tail is
  163 head words at one or two items each.
* **Count the right denominator.** `block.minecraft.banner.*` are tooltip
  descriptions of banner patterns, not items, and 688 of them sitting in the
  count made French look like 65% when it was 80%.

---

## Verified on this machine

| | |
|---|---|
| GPU | RTX 5060 Ti 16 GB (Blackwell, sm_120), driver 610.88 |
| Runtime | `cuda` / `float16`, ctranslate2 4.8.4, faster-whisper 1.2.1 |
| Model | `small`, cached in `./models` |
| Decode latency | **60–68 ms** for a 1.5 s utterance |
| Model load + warmup | ~3.4 s (14.6 s on the first run, which downloads weights) |
| Dictionary | 5,710 usable es_es entries, extracted from 1.20.2 assets |

Single-word round trip measured end to end: **verdict at 1516 ms**, which was
*before the audio finished streaming* — 850 ms of speech consumed, 110 ms of
inference. The player gets their item the instant they finish the word.

---

## Setup

```powershell
git clone https://github.com/optumesto/leximine-backend.git
cd leximine-backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env

# pull the real Spanish strings out of your own Minecraft install
.\.venv\Scripts\python.exe scripts\extract_lang.py --langs es_es

# prove the GPU path actually decodes (not just that it enumerates)
.\.venv\Scripts\python.exe scripts\check_gpu.py

.\run.ps1
```

The venv is deliberate: installing FastAPI globally pulls a newer
`opentelemetry-api` than other projects on this machine tolerate.

### Checking it works

```powershell
.\.venv\Scripts\python.exe scripts\test_matching.py                    # 71 cases, no GPU needed
.\.venv\Scripts\python.exe scripts\test_phrases.py                     # tier 1-3 prompts, no GPU
.\.venv\Scripts\python.exe scripts\test_trade_judge.py                 # 10 cases, needs Ollama
.\.venv\Scripts\python.exe scripts\test_client.py --silence --expect fail
.\.venv\Scripts\python.exe scripts\test_client.py --item minecraft:diamond --mic 4

# negotiate like a villager: a whole sentence, judged by the model
.\.venv\Scripts\python.exe scripts\test_client.py --trade --item minecraft:bread --mic 6
```

`--say` / `--say-target` synthesize speech through Windows SAPI. Useful for a
smoke test, but **this machine has no Spanish voice installed**, so Microsoft
David reads Spanish with English phonetics and whisper correctly hears
nonsense ("lingote de hierro" → "Lingotero"). A `--say-target` failure on a
multi-word name is the voice, not the backend. Use `--mic` for real checks.

Two runs on 2026-11-09 put numbers on that, and the numbers disagree. A
synthesized Spanish `Diamante` fed over the websocket on one machine came back
as "Yamante." — Whisper clipping the soft D on a robotic voice — and scored
**80 against a threshold of 80**, a pass with no margin at all, in ~6.3 s on
CPU. The same word from `run/testaudio/diamante.wav`, which `make_test_audio.ps1`
synthesizes through Windows SAPI using an *English* voice because this machine
has no Spanish one, came back as "Diamante." and scored **100**.

Same word, same matcher, same threshold; two synthesizers, 80 and 100. So the
lesson is not that synthetic speech sits near the boundary — it is that a
synthesized score says almost nothing about a human's margin, in either
direction. Do not read a synthesized pass as headroom, do not read a
synthesized near-miss as a threshold that needs lowering, and do not tune
80 / 70 against either. The sweep over six voices and 2,886 clips below is
what that number rests on.

---

## Difficulty, and why it is two numbers

`LEXIMINE_MATCH_THRESHOLD` (default 80) is overall similarity. On its own it is
not enough, because Minecraft item names are templates:

```
Lingote de hierro     Pico de hierro      Bloque de diamante
Lingote de oro        Pico de madera      Bloque de hierba
```

Two of these are ~84% similar as whole strings purely because of the shared
prefix. Said plainly: with one threshold, **saying "lingote de oro" hands you
an iron ingot**. That was a real failure in this repo's own test table before
the fix.

So `LEXIMINE_TOKEN_GATE` (default 70) additionally requires every
*distinguishing* word of the name to be matched on its own. Function words
(`de`, `of`, `y`) are excluded — but short meaningful ones are not, because
`oro` is exactly the word that separates gold from iron. Measured separations:

| | coverage | verdict |
|---|---|---|
| "fierro" for "hierro" (sloppy but right) | 83 | accept |
| "hierro" vs "hierba" (real confusable) | 67 | reject |
| "oro" vs "hierro" | 44 | reject |

The gate sits at 70, in the gap. Lower it below ~65 and genuine confusables
start passing.

### Calibrating to your voice

The defaults came from predicted transcripts. To set them from measured ones:

```powershell
.\.venv\Scripts\python.exe scripts\calibrate.py -n 10
```

It records you saying words you intend to get right and reports the score
distribution with suggested values. If a fair attempt scores low, add the
transcript whisper actually produced to `CASES` in `scripts/test_matching.py`
*before* lowering a threshold — that way the change is held against the
confusable pairs instead of quietly letting them through.

### Easy mode

`LEXIMINE_EASY_MODE=true` passes the expected word to the decoder as a hotword.
It makes whisper far more likely to produce the target word, including from
mumbling, so it largely defeats the exercise. Off by default; it is there for
a first-session difficulty option.

---

## What is this backend actually running?

```powershell
.\.venv\Scripts\python.exe scripts\show_config.py
```

Prints the effective settings **and** which of them `.env` overrode. Worth
running before trusting any measurement: `.env` wins over the code defaults,
and for a while it pinned `LEXIMINE_BEAM_SIZE=1` while the tuned default was 5
— so the benchmarks were measuring a configuration the server did not use.
Nothing surfaced that from either side alone.

## Both kinds of error

`scripts/benchmark_asr.py` answers one half of the question: how often a
player who said the right word is accepted. On its own that is a useless
target — a threshold of zero scores 100%. `scripts/tune_thresholds.py`
answers the other half by adding a **negative corpus**: for each target, the
most confusable *different* item in the dictionary, spoken against it.

```powershell
.\.venv\Scripts\python.exe scripts\tune_thresholds.py
.\.venv\Scripts\python.exe scripts\tune_thresholds.py --leaks 80 70
```

At the shipped thresholds, across six voices (2,886 clips):

```
accepted when correct   90.3%
accepted when WRONG      0.2%
```

The operating point itself was found against a single speaker, which is the
easy case, so `--voices` re-ran the sweep with all six. **80 / 70 is still the
optimum.** The accuracy was partly a property of one voice; the threshold was
not.

The sweep says the shipped 80 / 70 is already at the optimum, which is worth
knowing — they were chosen by intuition and had never been checked.

**The first version of this reported 16.7% wrong-accepted and was my
generator's fault**, not the matcher's. It was pairing plurals of the same
word (`Zanahorias` against `Zanahoria`) and items whose tier-1 head noun is
identical (`Tablones de roble` and `Tablones de abeto` both reduce to
`Tablones`, so the "wrong" answer was the right one). Both are now refused
when the corpus is built. A measurement harness can be wrong in the
direction that makes you break something that works.

`--leaks` lists the remaining ones by name, which is the only way to tell a
real confusion from an artefact of how the corpus was built.

### Short names are decided against the vocabulary

Listing the remaining leaks by name showed they were nearly all one thing: a
*different short item*, said correctly, scoring above the threshold. `Hielo`
(ice) against the target `Hilo` (string) scores 86 and was accepted.

The reverse error has the same cause. Similarity is a percentage of the
word's length, so a four-letter name has no room for a single mis-heard
letter: `Cubo` heard as `Cuba` is a 75 against a threshold of 80, and the
player who said it perfectly is told they were wrong. Short words are the
ones speech recognition gets wrong most often — there is no surrounding
context to constrain them — and they were being held to the strictest
standard in the game.

Neither is answerable from the target alone, because the question is not "is
this `Hilo`?" but "**which** of these is it?". So for a one-word target of six
characters or less, the matcher is handed the other short item names
(`Entry.rivals`, built once per language from the Minecraft dictionary) and:

* if the transcript fits a different name *better* than the target, it fails,
  whatever it scored — and the result says which name, which the mod shows on
  the failure flash ("that sounded like Hielo");
* otherwise, if the target is at least `SHORT_TARGET_MARGIN` ahead of the
  nearest rival, the threshold eases by `SHORT_TARGET_SLACK`, floored just
  above that rival — so one lost letter is forgiven while `Pala` / `Palo`
  stays shut.

Measured on the six-voice corpora (`scripts/ab_matcher.py` re-scores the
decoded transcripts, so the GPU does not have to run again):

```
                        without     with
accepted when correct     89.4%    90.3%
accepted when WRONG        2.9%     0.2%
  short names only        79.2%    81.3%   accepted when correct
                           5.8%     0.4%   accepted when wrong
```

The two halves need each other, and that is measurable rather than asserted
(`scripts/sweep_rivals.py`):

```
                 accepted   wrong
off                 89.2%    2.9%
veto only           88.7%    0.1%     costs half a point of real accepts
slack only          90.0%    0.4%     lets a few wrong ones back in
both (8 / 10)       90.1%    0.2%
```

The sweep is a plateau — slack 6–12 against margin 10–14 all land on
90.1 / 0.2 — so the shipped pair is not balanced on a knife edge. (Both
sweep tables here were run before the adjacent-window split fix below, which
is worth 0.2 points everywhere and does not move the shape of either.) Margin 20
is too strict (89.0%) and margin 0 leaks twice as much.

`--length` sweeps the third constant, and found a real bug by doing it. The
pool of names was being built to roughly the target's own length, while the
matcher compares over a window of **half to twice** it — so a rival long
enough to veto the answer could be missing from the list it was supposed to
be in. At a cutoff of 5 that leaked 2.3% against 0.2%, for no reason visible
in the code. The pool now runs to twice `SHORT_TARGET_LEN`:

```
max length  4   pool 135   accepted 90.3%   wrong 0.5%
max length  6   pool 169   accepted 90.1%   wrong 0.2%    <- shipped
max length 10   pool 170   accepted 90.0%   wrong 0.2%
```

With no vocabulary passed in the behaviour is exactly as before, which is
what `/translate` and the test table check.

## Recognition accuracy, measured

Tuning this by ear is how you end up confidently wrong, so
`scripts/benchmark_asr.py` builds a fixed corpus — 42 prompts weighted towards
single nouns, each at three noise levels and two input gains, 252 clips — and
scores configurations against it.

**The metric is the pass rate, not the word error rate.** What matters is not
whether whisper produced the exact string but whether the player would have
been *accepted*, so every clip goes through the real matcher with the real
thresholds.

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_asr.py
.\.venv\Scripts\python.exe scripts\benchmark_asr.py --models small,medium --beams 1,5
```

Every flag defaults to what the live settings say, and the script applies
`.env` the way the launcher does, so a bare run measures **the configuration
the server is running**. (It did not always: the defaults were beam 1 with
the context prompt on, neither of which ships.)

Measured on this machine, starting from 86.9% and ending at 92.9%:

| change | pass | exact | note |
|---|---|---|---|
| baseline (`small`, greedy) | 86.9% | 65.5% | |
| **`beam_size` 1 → 5** | 88.9% | 65.9% | +3 ms. Kept. |
| **language-aware articles** | | | see below |
| **split-word matching** | 89.7% | 73.0% | kept |
| **sentence completeness** | 90.5% | 72.9% | kept; a sentence must be attempted |
| **lenient stops** | 91.3% | 72.6% | kept; p↔b, t↔d |
| **short names vs the vocabulary** | **92.9%** | 72.2% | kept; see above |
| **adjacent-window split** | 92.9% | 72.2% | kept; +0.2 across six voices |
| `medium` model | 87.1% | 69.7% | **worse**, and 2.3× slower |
| context prompt | 88.5% | 61.9% | no real gain; hurts exact |
| input normalisation | 88.6% | 57.6% | worse — amplifies the noise too |
| whisper's VAD off | 89.7% | 72.2% | no difference either way |

Four of those are negative results and are kept here deliberately, because
each one is the obvious thing to try.

**A bigger model is worse.** `medium` answered "Cubo" with "Google." and
"Huevo" with "Wibble." — stronger language priors make it *more* willing to
turn a lone word into confident English. It is also 143 ms against 61 ms,
which the 200 ms speculative-pass loop cannot afford.

**Two real bugs came out of the corpus.** Articles were pooled across every
language, so Spanish lost "o" — which is Portuguese for "the" and Spanish for
"or" — and a transcript of "o no" was reduced to "no" before anything scored
it. And whisper splitting one word into two ("Horno" → "o no", identical
sounds since the h is silent) had no handling, though the reverse case of
running words together did.

**What is left is irreducible.** The remaining failures are four-letter words
separated by one phoneme: `Cubo`/`Cuba`, `Pan`/`Fun`, `Flecha`/`Fletcher`.
A fixed threshold is systematically harsher on short words — one wrong
character in a four-letter word is 75% similar, the same error in a sixteen
character phrase is 94% — and loosening it to accept `Cuba` for `Cubo` also
accepts `Oro` for `Horno`, which are two real items. Being asked again is the
better failure.

Absolute numbers are optimistic: the corpus is synthesised speech with added
hiss, which is cleaner than a headset in a room. The *ranking* between
configurations is what it is for.

### It depends who is talking

All of the above used one synthetic voice, which is a real blind spot — a
configuration can be tuned into fitting a single speaker.
`scripts/benchmark_voices.py` runs the corpus through six edge-tts voices
from Spain, Mexico, Argentina and Colombia:

`--wide` doubles that to twelve, adding Chile, Peru, the Dominican Republic
and US Spanish, because six speakers is a small sample to quote a worst case
from. Full corpus, 1,008 clips:

```
es-DO-RamonaNeural   98.8%    es-PE-CamilaNeural   92.9%
es-ES-ElviraNeural   97.6%    es-CO-GonzaloNeural  90.5%
es-AR-TomasNeural    94.0%    es-AR-ElenaNeural    90.5%
es-MX-DaliaNeural    92.9%    es-CO-SalomeNeural   90.5%
es-CL-LorenzoNeural  92.9%    es-MX-JorgeNeural    85.7%
es-ES-AlvaroNeural   86.9%    es-US-AlonsoNeural   83.3%
```

**The honest range is 83–99% depending on the speaker**, and the worst case
is the number worth caring about. Quoting the average would be flattering
nonsense.

Two things about reading that table. `--items N` takes the *first* N and
`CORPUS_ITEMS` is ordered shortest-name-first, so a cut is not a sample — it
is a selection of the hardest prompts, and a 30-item run put the worst voice
eight points lower than the whole corpus does. And per-voice run-to-run
spread is about three points at 84 clips, so the middle of the table is not a
ranking; the distance between the ends is the finding.

The spread is **not** the tuning fitting one speaker — that was the obvious
worry and it was checked directly (`tune_thresholds.py --voices`, below): the
operating point is the same whether it is chosen on one voice or on all six.
It is a property of the speech recogniser, so the answer is to quote the
worst case rather than to go hunting for a threshold that closes the gap.

That spread is also what found the last real win. Alvaro's failures clustered
on unaspirated stops — `'Pan'` came back as `'¡Ban!'`, `'¡BEN!'`, `'¡Pen!'` —
because Spanish /p t k/ are unaspirated and an English-trained decoder hears
them as /b d g/. Merging p↔b and t↔d phonetically (`LEXIMINE_LENIENT_STOPS`,
on by default) gains about two points on four of the six voices and costs
**nothing** on the negative corpus: the Minecraft item vocabulary has almost
no p/b or t/d minimal pairs, so the merge discards a distinction this app
never has to make.

### Measuring your own voice

None of the above measures *your* microphone, and that is the number that
decides whether the game feels fair. `scripts/calibrate.py` is the one tool
that does:

```powershell
.\.venv\Scripts\python.exe scripts\calibrate.py -n 12
```

It records you saying words you mean to get right, reports how often you
would have been understood in the same terms as the table above, and suggests
thresholds that sit just under your worst fair attempt.

**It prices the suggestion too.** Tuning on attempts you meant to get right
can only ever argue for looser thresholds, so the suggestion is run against
the negative corpus and the cost printed beside it:

```
   wrong answers accepted, shipped 80/70      0.2%   (1374 confusable pairs)
   wrong answers accepted, suggested 74/66    4.9%
   !! that is a real loosening.
```

Run `scripts/tune_thresholds.py` once first, so that corpus exists to price
against; `--voices` builds a bigger one and `calibrate.py` prefers it, since
a single speaker prices a loosening badly — the voices that lose accuracy are
the same ones whose transcripts drift closest to a different item.

## Anti-hallucination

Whisper invents text when fed silence or room noise — "Gracias por ver el
video", "Subtítulos realizados por...". Three guards, in order of how much
they do:

1. **RMS gate** (`LEXIMINE_SILENCE_RMS`) — quiet buffers never reach the model
   at all. Cheapest and most effective.
2. **Decoder metrics** — `no_speech_prob` and `avg_logprob` rejection.
3. **Phrase blocklist** — `matching.HALLUCINATIONS`, for what survives 1 and 2.

These produce `reason: "no_speech"` rather than `"low_score"`, which matters in
game: "I didn't hear anything" is the right message for a muted mic, and
"wrong word" is not.

---

## Layout

```
app/
  main.py        FastAPI app, /ws endpoint, REST helpers
  session.py     one challenge: buffering, utterance split, verdict
  stt.py         faster-whisper wrapper (locked, single instance)
  matching.py    normalisation, fuzzy scoring, the token gate
  phonetics.py   per-language spelling rules, so "b" and "v" stop mattering
  phrases.py     tier 1-3 prompts and the trade sentence frames
  lang.py        Minecraft .json loading, key resolution
  stats.py       SQLite attempt history; what /review draws from
  tts.py         edge-tts pronunciation, cached to disk
  llm.py         the villager trade judge (Ollama, OpenAI-compatible)
  config.py      env-driven settings
  cuda_dlls.py   puts pip-installed CUDA DLLs on the Windows search path
  mcassets.py    shared Minecraft asset-index extraction
  fallback_dict.py  ~55 approximate es_es entries, so it runs with no setup
scripts/
  check_gpu.py        proves the GPU actually decodes
  extract_lang.py     pulls lang files out of your Minecraft assets
  test_client.py      full websocket flow without Minecraft; --trade for a villager
  test_matching.py    71-case matcher table, runs in milliseconds
  test_phrases.py     tier 1-3 prompt generation, including the article rules
  test_trade_judge.py 10-case judge table, needs a local model
  calibrate.py        threshold tuning against your own mic
lang/              extracted es_es.json + en_us.json
models/            whisper weight cache
stats.db           attempt history (created on first run)
tts-cache/         synthesised pronunciations
```

### Spaced repetition

`stats.py` records every attempt — which word, which language, how many times
right, how many wrong, when it was last seen and last correct. `GET /review`
draws from it, weighted towards trouble: a word's chance rises with its failure
count, rises further if it has never once been right, and rises slowly the
longer since it was last asked.

That is what lets the mod's doors and chests ask about something you missed
twenty minutes ago. **The mod has no idea which words those are** — it asks for
"one I keep missing" and shows what comes back. An empty history answers `404`,
which is honest: there is nothing to review yet.

Trade challenges are deliberately **not** recorded: a refused sentence says
nothing about whether you can pronounce "pan", so counting it would send a door
off to drill a word you already know.

To start over:

```powershell
# this language
Invoke-RestMethod -Method Post "http://127.0.0.1:8760/review/reset?lang=es_es"
# everything
Invoke-RestMethod -Method Post "http://127.0.0.1:8760/review/reset?all=true"
```

It is a separate, deliberate call rather than something `/leximine forget
all` does for you, because the table has **no player column** — one pool per
language, shared by everyone on the backend. Fine for playing alone; it would
be wrong to let one player on a shared server wipe it for the rest.

### The trade judge

`llm.py` asks a local model (Ollama, `gemma3:12b` by default) whether a
sentence is a sensible way to ask a merchant for an item, in the right
language. It is prompted to accept asking, requesting, buying, trading and
offering to exchange, and to ignore the punctuation and capitalisation that
speech recognition does not produce. Naming the item is not asking for it.

**Keeping it resident is a correctness concern, not a speed one.** Measured
here: a cold call is **30.8s** while 7.5 GB of weights are read from disk, 4s
if they are still in the OS page cache, and **0.1s** once resident. A trade
allows the player twice the normal timeout — 12 seconds — so a cold judge
cannot answer in time, and the player is refused however well they asked.

So the model is warmed when a **client connects**, which buys the half-minute
the load actually needs, and re-checked every ten minutes while someone is
playing. Warming when a trade *starts* is far too late, which is what the
first version did.

Two traps found doing this, both silent:

* **Ollama's OpenAI-compatible endpoint ignores `keep_alive`.** A `/v1` call
  asking for 2h left the window at the 30 minutes `OLLAMA_KEEP_ALIVE` was set
  to; the identical native call moved it to 2h. Residency is therefore set
  through `/api/generate`, and judging stays on `/v1` so any OpenAI-compatible
  server still works.
* **"Warmed" cannot be a flag.** Anything else on the machine can take the
  VRAM — this one also runs a 30B model for another project — so a latched
  boolean means the backend believes it is warm forever, and a timer cannot
  see an eviction it did not cause. The periodic call refreshes unconditionally
  instead.

If nothing answers at all, the trade is **allowed** — the player did their
part.

`scripts/test_trade_judge.py` is a table of ten cases a reasonable judge should
not disagree about. It tolerates one odd call and fails on two: a judgement is
an opinion, not a function.

---

## Gotchas worth knowing

**A GPU that enumerates is not a GPU that works.** CTranslate2 loads cuBLAS
lazily at the first kernel launch. `get_cuda_device_count()` returning 1 proved
nothing here — the first real decode died with `cublas64_12.dll is not found`.
Worse, the warmup pass had VAD enabled, so it discarded its own test audio
before reaching the decoder and reported success. The warmup now forces a real
decode with VAD off, specifically so a broken GPU path fails at startup instead
of on the first player. `scripts/check_gpu.py` does the same.

**faster-whisper returns a generator.** `model.transcribe()` does no work until
the segments are consumed. A warmup that ignores the return value warms
nothing.

**1.20.1 is not installed on this machine**, so `extract_lang.py` fell back to
1.20.2's assets. Vanilla item and block names are stable within a 1.x line, so
this is fine; the file just also contains strings for things added in 1.20.2.

**Model size.** `small` is the default because 60 ms keeps the loop feeling
instant. `medium` is noticeably better on heavy accents at roughly 3× the
latency, which is still well inside a 5-second timer — worth trying if real
attempts get rejected during calibration.
