# Changelog — leximine-backend

The record of what changed and why, in order. The README and PROTOCOL.md
carry the reasoning in full; this is the chronology.

---

## 2026-11-09 (later) — 0.1.2, so the download carries the installer fixes

### Changed

- **Version 0.1.2.** 0.1.1's zip was published at 07:36 and the installer work
  landed after it, so the download contained none of it: no `ensure_env.ps1`,
  no root `check-setup.cmd`, Python still a hard prerequisite, and a setup step
  that still finished in seconds and left 2.3 GB for the first launch. Checked
  against the published asset rather than assumed — zero matches for either
  file. Replacing 0.1.1's bytes in place was the alternative and was rejected
  for the same reason it was rejected yesterday: that version is public and
  already downloaded, and its tag would stop describing its contents.
- The mod jar is unchanged in substance. The 0.1.1 and 0.1.2 jars were compared
  entry by entry: all 60 class files byte-identical, one line different in
  `fabric.mod.json`. This is a backend-and-installer release.

---

## 2026-11-09 — links follow the mod's rename

### Changed

- **The mod repository is now `leximine`, not `leximine-mod`**, and every link
  here points at it: the README's three references, `bug.yml`, and the
  `config.yml` contact link that routes game-side reports away from this
  repository. GitHub redirects the old URL, so none of them were broken in the
  meantime.
- **`setup.ps1` and `make_icon.py` look for a sibling folder called `leximine`
  first**, then `leximine-mod`, then the older `lingua-craft-mod`. A clone from
  before either rename still resolves, and the release layout (`..\mod`) is
  unaffected.
- **The README setup block starts with `git clone`.** It opened with a `cd` into
  an absolute path on one machine, which is not a step anyone else can follow.
- **Version 0.1.1**, which is the release this half ships in. The handshake and
  `PROTOCOL.md`'s example follow it.

### Fixed

- **`check-setup.cmd` had LF line endings in this working copy**, and so in the
  0.1.0 download — the one file both issue templates and INSTALL.md tell people
  to double-click first. `.gitattributes` has declared `*.cmd eol=crlf` all
  along, which is why the repository copy is right and a fresh clone is fine;
  this working copy predates the rule, and `git status` compares normalised
  content, so nothing ever reported it. Refreshed from the index, and
  `make_release.ps1` in the mod repository now fails a release that contains an
  LF `.cmd` rather than publishing one.

### Verified

- **The CPU fallback is real.** Tested 2026-11-09 on a machine with no usable
  GPU: CUDA was attempted, failed with "CUDA driver version is insufficient",
  and the loader fell through to cpu/int8 exactly as INSTALL.md promises. The
  server came up on 8760 and answered.
- **PROTOCOL.md matches the wire**, checked frame by frame against a fresh
  server by someone reading only the document: `hello`, a challenge for a
  diamond returning `challenge_ready` with target `Diamante`, and a clean
  timeout with a proper result frame when no audio arrives.
- **Two synthesizers, the same word, 80 and 100.** A synthesized Spanish
  `Diamante` scored exactly 80 against a threshold of 80 on one machine — heard
  as "Yamante.", ~6.3 s on CPU. The repository's own fixture for the same word,
  synthesized through Windows SAPI with an English voice, scored 100 in the
  e2e run the same night. The README said the first number meant synthetic
  voices sit near the boundary; the second number says that was a
  generalisation from one sample. What it actually means is that a synthesized
  score carries no information about a human's margin in either direction, and
  the six-voice sweep is what 80 / 70 rests on. Corrected there.
- matching 99/99, phrases PASSED, rivals PASSED, and the trade judge reports
  Ollama unavailable and degrades instead of crashing.

### Changed — setup does the waiting now

The install did not do what the documentation said it did. INSTALL.md promised
that `setup.cmd` "builds the Python environment"; it did not. `setup.ps1` did
the icon, the desktop shortcut and the mod, and the venv plus 2.3 GB of wheels
were built by `start_backend.ps1` instead — so setup finished in seconds
saying "Setup complete", and the real work ambushed the player the first time
they double-clicked what they thought was a launcher. `check-setup.cmd` made
it worse by answering a missing venv with "Run setup.cmd - it builds .venv",
which sent them in a circle.

- **`scripts/ensure_env.ps1`** is new and owns the environment: interpreter,
  dependencies, and optionally the model. `setup.ps1` runs it first, as step
  1 of 3, with `-WithModel`. `start_backend.ps1` runs it too and is now purely
  the safety net for anyone who skipped setup — identical logic, one copy of
  it. Every piece is checked before it is fetched, so a second run costs a
  second: measured at 0.9 s on an environment that is already built.
- **The dependency install is no longer silent.** It ran `pip --quiet` while
  announcing "a few hundred MB" for what is actually 2.3 GB. Ten silent
  minutes is where someone force-closes, and a force-close mid-pip leaves a
  half-written environment that has to be deleted by hand. Progress bars are
  on, the figure is honest, and the window says not to close it.
- **The speech model is fetched during setup** (`scripts/fetch_model.py`),
  not on first play. Loaded on cpu/int8 purely to pull the weights, so a
  machine with no CUDA can run it, and non-fatal: a failed pre-fetch costs a
  wait, where a failed setup costs the install.
- **`doctor.ps1` no longer gives circular advice** for a missing venv.

### Added — Python is no longer a prerequisite

The documented number-one failure was the "Add python.exe to PATH" checkbox in
someone else's installer. It is gone as a failure mode: when no Python 3.11+
is on PATH, `ensure_env.ps1` fetches the official embeddable build from
python.org (11 MB) into `.venv`, pinned and checksum-verified the way
`install_mod.ps1` pins Fabric API, and bootstraps pip into it. Nothing is
installed system-wide, nothing touches PATH or the registry, and deleting the
folder removes it. A machine that already has Python still gets an ordinary
venv.

It is laid out as `.venv\Scripts\python.exe`, which is the path every other
script here already used, so nothing else had to learn about it.

Three things that had to be got right, each found by running it rather than
reasoning about it:

- The embeddable build ships without `venv` **and** without `ensurepip`, so
  pip can neither install itself nor be asked to. A wheel is a zip and pip is
  pure Python, so the wheel is unpacked into site-packages directly.
- `Expand-Archive` refuses any extension but `.zip` — the pip wheel is saved
  as `.zip` for that reason alone.
- The `._pth` file is written with `WriteAllText` and explicit ASCII.
  `Set-Content` would add a UTF-8 BOM, Python would read it as part of the
  first path, and site-packages would silently never be found.

**Verified end to end, by installing it.** From the published `Leximine.zip`,
extracted fresh, with every Python hidden from PATH so the embeddable branch
was the only route:

- the interpreter is fetched and checksum-verified, pip bootstraps and
  self-upgrades
- all 56 packages install — 2.3 GB, including `ctranslate2`, `faster-whisper`,
  `av`, `onnxruntime`, `tokenizers` and the NVIDIA CUDA runtime wheels
- it is genuinely the embeddable build and not a venv created from some Python
  that escaped the PATH filter: `pyvenv.cfg` absent, `python311._pth` present,
  Python 3.11.9
- `python311._pth` carries no BOM, which would have silently cost it
  site-packages
- the backend **serves** from it: uvicorn came up and `/health` answered in two
  seconds with `"device":"cuda","compute_type":"float16"`, 5,710 dictionary
  entries, version 0.1.2

That last line is the one that mattered. An embeddable layout resolves DLLs
differently from a venv, and `app/cuda_dlls.py` has to put the CUDA runtime on
the search path for ctranslate2's first kernel launch. It does.

- **The dependency check no longer throws instead of installing.** Under
  `$ErrorActionPreference = "Stop"`, PowerShell 5.1 turns a native command's
  stderr into a terminating error even with `2>$null` — so the import probe,
  whose entire purpose is to fail on a fresh environment, killed the script
  rather than returning a code. Probes now run through a helper that drops the
  preference for the length of the call.
- **pip's output encoding is pinned to UTF-8.** `rich` draws progress with box
  characters and dies with a `UnicodeEncodeError` on a cp1252 console; seen
  while testing this.

---

## 2026-11-05 — renamed to leximine-backend

### Changed

- **The project is now `leximine-backend`**, on GitHub and in every document.
  "lingua-stt" dated from before the mod had its name, and two halves of one
  product called different things is a cost paid by everyone who installs it.
  GitHub redirects the old repository URL, so existing clones keep working.
- **Settings are read from `LEXIMINE_*`**, and `LINGUA_*` is still honoured.
  The fallback is not politeness: the 0.1.0 release shipped an `.env` full of
  `LINGUA_*` keys, and a silently-ignored key does not raise an error — it
  reverts that setting to its default, which is exactly how a night of ASR
  benchmarking was once spent measuring a configuration nobody was running.
  `app/config.py` resolves each name under the new prefix first, then the old;
  `doctor.ps1` and `start_backend.ps1` read both the same way.
- Informational strings follow the name: the logger, the FastAPI title, the
  `server` field in the handshake (`leximine-backend`, documented in
  PROTOCOL.md) and the `X-Leximine-Word` header on `/pronounce`. Nothing in
  the mod reads any of them — they are for a human watching the window.

### Added

- **CI** (`.github/workflows/tests.yml`): the two table tests that need no
  GPU, no model and no server, on Python 3.11-3.13, plus `compileall` over
  everything. It installs thefuzz and rapidfuzz only — `requirements.txt`
  pulls ~2 GB of faster-whisper and CUDA wheels that these tests never import.
- **Issue templates**: this repository had none, and install problems land
  here rather than on the mod. `bug.yml` asks for `doctor.ps1` output first;
  `config.yml` routes sentence reports to CONTRIBUTING.md and anything
  in-game to leximine-mod.

---

## 2026-10-30 (overnight, later) — short names are decided against the vocabulary

### Fixed

- **A short word could not be judged against its target alone.** Listing the
  remaining false accepts by name showed they were nearly all the same shape:
  a *different* short item, said correctly. `Hielo` (ice) scores 86 against
  the target `Hilo` (string) and was accepted. The mirror error has the same
  cause — similarity is a percentage of the word's length, so `Cubo` heard as
  `Cuba` is a 75 against a threshold of 80, and a four-letter name has no room
  for one mis-heard letter. Short words are the ones recognition gets wrong
  most often and were being held to the strictest standard in the game.

  `match()` now takes `rivals`, the other short item names (`Entry.rivals`,
  built once per language from the Minecraft dictionary — 134 names for
  es_es, 11 ms). If the transcript fits a different name better the attempt
  fails whatever it scored; otherwise the threshold eases by 8 points,
  floored just above the nearest rival. Slack alone would hand over the wrong
  item and the veto alone would reject too much, so neither ships without the
  other. With no vocabulary passed in, behaviour is unchanged.

  ```
                          without     with
  accepted when correct     89.4%    90.3%     six voices, 2,886 clips
  accepted when WRONG        2.9%     0.2%
  ```

  Both constants were then swept rather than argued (`sweep_rivals.py`): the
  veto alone costs half a point of real accepts, the slack alone lets a few
  wrong answers back in, and the pair sits on a plateau — slack 6–12 against
  margin 10–14 all land on 90.1 / 0.2.

- **A word split in two inside a longer name had no handling.** The
  split-word rule compared the target word against the *whole* transcript
  re-joined, which only works when the target is a single word: "Espada de
  hierro" heard as "Es para de hierro" is one word split in two with the
  other two intact, and "espada" against "esparadehierro" scored 67 where
  "espada" against just "espara" scores 83. It now scans runs of up to three
  **adjacent** tokens (`MAX_SPLIT_WINDOW`), still contiguous and still
  length-guarded, so a long transcript cannot be re-joined until something
  fits. +0.2 points across six voices at no cost on the negative corpus.

- **"Wrong" is not a lesson.** When a short word is rejected for fitting
  another item, the `result` frame's `note` says which — the mod's failure
  flash reads "that sounded like Hielo" above "listen: Hilo". These are
  exactly the pairs a learner cannot hear yet.

### Added

- **`scripts/ab_matcher.py`** — A/Bs a matcher change over the cached
  positive *and* negative transcripts, so a candidate is judged on both
  errors at once and the GPU never has to re-decode.
- `tune_thresholds.py --voices` — the operating point was found against a
  single synthetic speaker, which is the easy case; the corpus can now be
  spoken by all six voices and the report breaks the two error rates down per
  voice. **The shipped 80 / 70 survives it**: on 2,886 multi-voice clips the
  sweep still puts the optimum there, so the threshold was not fitted to one
  speaker even though the accuracy partly was.
- `scripts/sweep_rivals.py`, which measures the two short-name constants
  instead of defending them, and `--length` for the third.
- `scripts/test_rivals_langs.py` — the property that has to hold in every
  language, not just the one this was designed against: **saying the name
  correctly still passes**. German item names are compounds so almost nothing
  counts as short, and Japanese has no spaces at all, which makes 2,290 names
  short against Spanish's 169. The candidate forms are now memoised
  (`_rival_form`), which took a scoring pass from 1.28 ms to 0.21 ms and
  removes the scaling hazard in the language nobody was testing.

### Also fixed — measuring what actually ships, structurally

Three separate versions of the same mistake, so it is now fixed in one place
rather than a fourth time:

- **Four harnesses were scoring a matcher the server does not run.**
  `benchmark_asr`, `benchmark_voices`, `tune_thresholds` and `calibrate` all
  called `match()` without the vocabulary, so from the moment `rivals`
  existed every one of them measured something stricter than the real thing.
  All four now pass `entry.rivals`.
- **`benchmark_asr`'s flag defaults were beam 1 with the context prompt on**,
  neither of which ships — a bare run reported 90.9% pass / 54.4% exact for a
  configuration nobody uses. Every default now reads from the live settings.
- **No measurement script applied `.env`**, which is exactly what beat the
  code defaults the first time. There is now one `scripts/_env.py`, called
  before `app.config` is imported in every script that measures anything;
  `show_config.py` uses the same helper and reports the same values from the
  other end.

With all three fixed, the single-voice corpus at the shipped configuration is
**92.9%** (86.9% at the start of the day), and the six-voice range is
**85.7%–98.8%** — the worst voice gained 4.8 points and the spread closed
from 15.5 to 13.1.
- 8 more cases in `test_matching.py` (95 now, was 87): both directions of the
  `Pala`/`Palo` minimal pair, the observed `Cubo`/`Cuba` transcription, and
  the no-vocabulary fallback.
- `e2e_test.py --note-shot` and `make_rival_audio.py` (mod repo), which
  photograph the new failure message beside the punishment wheel. They land
  on the same frame and have collided before.

### Notes

- Full-corpus figures replace the earlier subset ones, and
  `benchmark_voices.py --wide` doubles the speaker sample to twelve (Chile,
  Peru, the Dominican Republic and US Spanish added): **83.3% to 98.8%**
  over 1,008 clips. `--items N` takes the *first* N and `CORPUS_ITEMS` is
  ordered shortest-name-first, so the 30-item runs quoted earlier were 30 of
  the hardest prompts and put the worst voice eight points low; `--items`
  now defaults to the whole corpus.
- The spread is **not** the tuning fitting one speaker — checked directly,
  not assumed. It is the recogniser, so the honest move is to quote the worst
  case rather than hunt for a threshold that closes the gap, and the script's
  own warning line now says that instead of raising the question.

---

## 2026-10-30 (overnight) — both kinds of error, and sentence completeness

### Added

- **`scripts/benchmark_voices.py`** — the same corpus through six edge-tts
  voices from four countries. Everything had been tuned against a *single*
  synthetic voice, which turned out to matter: the spread is **23 points**
  (es-MX-Dalia 93.8%, es-ES-Alvaro 70.8%). The honest figure is a range,
  71–94% by speaker, not an average.
- **`calibrate.py` now prices its own suggestion.** It tunes on words the
  speaker meant to get right, which is one-sided by construction and can only
  ever argue for looser thresholds — it would happily have recommended 74/66,
  which takes wrong answers accepted from 2.1% to **17.1%**. The suggestion
  is now run against the negative corpus and the cost printed next to it.
- **Lenient stops**, on by default (`LINGUA_LENIENT_STOPS=0` to disable).
  Spanish /p t k/ are unaspirated and an English-trained decoder hears them
  as /b d g/ — `'Pan'` came back as `'¡Ban!'`, `'¡BEN!'`, `'¡Pen!'`. Merging
  p↔b and t↔d gains ~2 points on four of six voices and costs **nothing**
  measured on the negative corpus, because the item vocabulary has almost no
  p/b or t/d minimal pairs.

- **`scripts/tune_thresholds.py`** — a negative corpus (the most confusable
  *other* item, spoken against each target), decoded once and cached, then a
  threshold × gate sweep run purely in the matcher. This measures the error
  nobody had measured: how often a **wrong** word is accepted. At the shipped
  80 / 70 it is **2.1%**, against 87.7% accepted when correct, and the sweep
  puts 80 / 70 at the optimum.
- `scripts/seed_level.py`, for putting the drill history at a chosen rung.

### Fixed

- **The server was not running the configuration being benchmarked.** `.env`
  pinned `LINGUA_BEAM_SIZE=1` and wins over the code default, so every
  measurement had been made against a setting the actual server did not use.
  `.env` and `.env.example` are now current, the measured-and-rejected
  options are listed there with their numbers, and **`scripts/show_config.py`**
  prints what the backend will really run with — including which settings
  `.env` overrode. That gap is invisible otherwise.
- `context_prompt` defaulted to `True` in code while being documented as off.
  It is off, which is what was measured.
- **A sentence prompt has to be attempted as a sentence.** At tier 3, saying
  only the item passed or failed depending on how long the frame happened to
  be: "Lingote de netherita" cleared "Tengo un lingote de netherita" at 83,
  while the same shortcut against a longer frame scored 67. The score is now
  scaled by how much of the sentence was attempted — one word lost to the
  microphone costs little, saying the noun and stopping fails. Accept rate
  went *up* (89.7% → 90.5%), so this cost nothing.
- `GET /translate` accepts `tier=4` and takes an optional `level`.
- **A repetition loop is collapsed before it is displayed.** The matcher
  already refused to score one, but the raw text still went out for display,
  so the overlay could read "la ropa de la ropa de la ropa de la ropa..." —
  which tells the player nothing except that something is broken. Cut to six
  words and an ellipsis.

### Notes

- The first negative corpus reported **16.7% wrong-accepted**, which was the
  generator's fault: it paired plurals of the same word and items whose
  tier-1 head nouns are identical, so the "wrong" answer was often the right
  one. Fixed, and the real figure is 2.1%. Worth remembering that a
  measurement harness can be wrong in the direction that makes you break
  something that works.
- `int8_float16` and `float32` both measured: the first is inside the noise
  and slower, the second is byte-identical output at 50% more latency.
  Staying on `float16`.
- Run-to-run variance measured directly at about **1.2 points**; smaller
  differences than that are not real.

---

## 2026-10-30 — recognition accuracy

Measured, not guessed: `scripts/benchmark_asr.py` scores configurations
against 252 clips and reports how often the player would have been
**accepted**, through the real matcher at the real thresholds. 86.9% → 89.7%,
exact transcription 65.5% → 73.0%.

### Fixed

- **Articles were pooled across every language.** "o" is the Portuguese
  article and the Spanish word for "or", so a Spanish transcript of "o no"
  was reduced to "no" before anything scored it — "Horno" came back as a 67
  against audio that said it perfectly. Now per language.
- **A word split in two had no handling.** Whisper rendered "Horno" as
  "o no" — the same sounds, since the h is silent — and the per-token
  coverage gate could never see it. The reverse case (words run together)
  was already handled; this is its mirror, with the same length guard so it
  cannot scan a rambling transcript for anything vaguely similar.
- `beam_size` 1 → 5: +6 points of exact transcription for 3 ms.

### Added

- `scripts/benchmark_asr.py`, and nine real observed confusions as permanent
  cases in `test_matching.py` (80 now, was 71).
- `LINGUA_CONTEXT_PROMPT`, `LINGUA_NORMALISE_INPUT`,
  `LINGUA_TEMPERATURE_FALLBACK` — all measured, all **off** by default
  because none of them helped. Kept with the numbers so the next person does
  not re-try them blind.

### Notes

- **A bigger model is worse here.** `medium` scored 87.1% against `small`'s
  89.7%, answering "Cubo" with "Google." and "Huevo" with "Wibble." — a
  stronger language prior is more willing to turn a lone word into confident
  English. It is also 143 ms against 61 ms, which the 200 ms speculative-pass
  loop cannot afford.
- A small corpus lies. An early 132-clip run showed the context prompt
  winning by 6.8 points; at 252 clips the gap was 0.4 and inside the
  run-to-run noise of non-deterministic GPU beam search.

---

## 2026-10-29 — the trade judge, the drill history, and a performance pass

### Added

- **`app/llm.py` — the villager trade judge.** Asks a local model (Ollama,
  `gemma3:12b`) whether a sentence is a sensible way to ask a merchant for an
  item, in the right language. Prompted to accept asking, requesting, buying,
  trading and offering to exchange, and to ignore the punctuation speech
  recognition does not produce. `scripts/test_trade_judge.py` is a ten-case
  table that tolerates one odd call and fails on two — a judgement is an
  opinion, not a function.
- **`app/stats.py` — the drill history.** SQLite record of every attempt;
  `GET /review` weights the draw towards what keeps going wrong. This is what
  lets a door quiz you on something you missed twenty minutes ago.
- `POST /review/reset?lang=…` (`&all=true` for everything).
- `mode: "trade"` on the websocket, with `note` on both the `attempt` and
  `result` frames.
- `scripts/test_client.py --trade` — negotiate like a villager without
  launching Minecraft.

### Fixed

- **A refused trade ended the challenge immediately.** Now it comes back as
  an `attempt` carrying the model's reason, and the player can start the
  sentence again until the clock runs out. The reported transcript is the
  *last* refused attempt rather than the highest-scoring one, because a word
  match score against a one-word target says nothing about a sentence and
  "best" was picking between attempts at random.
- **Trade outcomes were polluting the pronunciation history.** `_finalize`
  recorded every challenge, so a refused trade raised an item's review weight
  and an accepted one lowered it — but the judge scores sentence
  construction, not pronunciation. A door was being sent off to drill words
  the player says fine. Now gated on `mode != "trade"`; word and review
  challenges are still recorded, since both really are that question.
- **The judge was cold exactly when it mattered.** Measured: **30.8s** cold
  (7.5 GB from disk), 4s with the weights still in the page cache, **0.1s**
  resident — against a 12-second trade timeout. A cold judge cannot answer in
  time and the player is refused however well they asked. `warm()` had fired
  when a trade *began*, and latched a boolean afterwards so it became a
  permanent no-op once the model was ever evicted. It now warms when a
  **client connects** and re-checks every ten minutes while someone plays.

### Found

- **Ollama's OpenAI-compatible endpoint silently ignores `keep_alive`.** A
  `/v1` call asking for 2h left the window at the 30 minutes
  `OLLAMA_KEEP_ALIVE` was set to; the identical native call moved it to 2h.
  No error either way. Residency is therefore set through `/api/generate`
  while judging stays on `/v1`, so any OpenAI-compatible server still works.
- Measured headroom with Minecraft, whisper and the 7.5 GB judge all
  resident: **10.7 / 16.3 GB VRAM, 0–8% GPU**. No pressure, so there is no
  reason to drop to a smaller judge model.
