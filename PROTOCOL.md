# Leximine websocket protocol

`ws://127.0.0.1:8760/ws`

The mod opens **one** connection at startup and keeps it for the whole session.
Opening a socket per item would pay TCP + handshake cost inside the player's
5-second window.

## Audio contract

| property | value |
|---|---|
| sample rate | 16000 Hz |
| channels | 1 (mono) |
| encoding | signed 16-bit PCM, **little-endian** |
| framing | raw samples, no WAV/RIFF header |
| chunk size | 20–100 ms (640–3200 bytes); 50 ms recommended |

In Java this is:

```java
new AudioFormat(16000f, 16, 1, true, false)
//                           ^signed  ^bigEndian=false
```

Send audio as **binary** websocket frames. Everything else is **text** (JSON).
Binary frames always belong to the connection's currently open challenge; there
is at most one at a time, which is why binary frames need no id.

## Message flow

```
client                               server
  |  ---- connect ---------------->    |
  |  <--- hello -------------------    |
  |                                    |
  |  ---- challenge --------------->   |   (player picks up an item)
  |  <--- challenge_ready ---------    |   mod shows the target word + timer
  |                                    |
  |  ==== binary PCM ============>     |   mic streaming starts
  |  <--- partial -----------------    |   0..n, as speculative passes land
  |  ==== binary PCM ============>     |
  |  <--- result (success) --------    |   <-- may arrive mid-stream
  |                                    |
  |  ---- end --------------------->   |   (or: push-to-talk released)
  |  <--- result ------------------    |
```

Exactly **one** `result` is emitted per challenge. It may arrive without the
client ever sending `end`:

* a speculative pass already cleared the threshold (`reason: "early"`), or
* the server-side deadline elapsed (`reason: "timeout"`).

So the mod must treat `result` as the single source of truth and must not
assume it comes only in response to `end`.

---

## Client → server

### `challenge`
Open a challenge. Supersedes any challenge still open on this connection
(the old one gets a `result` with `reason: "superseded"`).

```json
{
  "type": "challenge",
  "id": "a3f1",
  "translation_key": "item.minecraft.diamond",
  "lang": "es_es",
  "timeout_ms": 5000,
  "threshold": 80
}
```

| field | required | notes |
|---|---|---|
| `id` | yes | echoed on every frame for this challenge; any string |
| `translation_key` | one of | from `ItemStack.getItem().getTranslationKey()` |
| `item_id` | one of | e.g. `minecraft:diamond`; tries `item.` / `block.` / `entity.` prefixes |
| `lang` | no | Minecraft language code; defaults to the backend's own setting |
| `tier` | no | 1 bare noun, 2 full item name, 3 a sentence. Default 1 |
| `context` | no | `craft` or `pickup`; picks the sentence frame at tier 3 |
| `difficulty` | no | `lenient` / `normal` / `strict` |
| `timeout_ms` | no | default 6000, clamped to 500–60000 |
| `threshold` | no | overrides the difficulty preset |
| `mode` | no | `word` (default) or `trade` — see below |

**Tiers scale what has to be said, not how accurately.** Given
`item.minecraft.diamond_sword` in `es_es`:

| tier | target |
|---|---|
| 1 | `Espada` |
| 2 | `Espada de hierro` |
| 3 | `Estoy fabricando una espada de diamante` |

Two limits are reported rather than faked. Tier 1 can only shorten a name
built as "noun + connector + material"; German compounds (`Diamantschwert`)
are left whole. Tier 3 only produces sentences for languages with a phrase
pack, because picking the wrong article would teach bad grammar — anything
else quietly falls back to tier 2 and says so in `notes`.

At tier 3 only the item's own words are required for a pass; the sentence
frame counts towards overall similarity but is not individually gated, so
fumbling "estoy fabricando" is not the same as naming the wrong item.

**Language is per challenge, not per server.** The mod sends the language it
wants with every request, so a player changing it in the mod's settings takes
effect on the next item with nothing restarted. A language the backend has
never served is loaded on first use, extracted from the local Minecraft assets
if it is not already in `lang/` — so the first challenge in a new language
takes an extra moment and the rest are instant. An unusable code comes back as
`error` with code `unknown_language`.

### Trade mode

`"mode": "trade"` changes **who decides**. There is no target string to match:
the transcript goes to a local language model, which is asked whether this is a
sensible way to ask a merchant for the item, in the right language. "Quiero
comprar pan, por favor", "¿Me das pan?" and "Me gustaría cambiar esto por pan"
all pass; "pan" on its own does not, and neither does a request for something
else.

Send `"context": "trade"` alongside it so the dictionary picks the trade phrase
frame; `item_name` in `challenge_ready` is then what the player is asking for.

A refusal partway through does **not** end the challenge. Like a missed word it
comes back as an `attempt` — with the model's reason in a `note` field — and
the player can start the sentence again until the clock runs out. Only an
accepted attempt, the final pass or the deadline is terminal.

If no model answers, the trade is **allowed** (`reason: "judge_unavailable"`).
The player did their part; the infrastructure did not. A client that wants to
distinguish "passed" from "nobody was judging" must read `reason`, not
`status`.

### `end`
"The player stopped talking, decide now." Runs a final pass over everything
buffered.

```json
{ "type": "end", "id": "a3f1" }
```

### `cancel`
Abandon the challenge (player died, closed the screen, dropped the item).
Produces a `result` with `status: "fail"`, `reason: "cancelled"`.

```json
{ "type": "cancel", "id": "a3f1" }
```

### `ping`
Keepalive / liveness check → `{"type": "pong"}`.

---

## Server → client

### `hello`
Sent immediately on connect. If `ready` is `false` the model is still loading —
show "connecting" in game rather than starting challenges.

```json
{
  "type": "hello",
  "server": "leximine-backend",
  "version": "0.1.1",
  "ready": true,
  "model": "small",
  "device": "cuda",
  "target_lang": "es_es",
  "default_timeout_ms": 6000,
  "threshold": 80,
  "audio": { "sample_rate": 16000, "channels": 1, "sample_format": "s16le", "recommended_chunk_ms": 50 }
}
```

### `challenge_ready`
The word to display. **The server owns the dictionary** — the mod never needs
a copy of `es_es.json`, which also means changing target language is a server
restart and not a mod rebuild.

```json
{
  "type": "challenge_ready",
  "id": "a3f1",
  "key": "item.minecraft.diamond_sword",
  "target": "Estoy fabricando una espada de diamante",
  "item_name": "Espada de diamante",
  "english": "Diamond Sword",
  "lang": "es_es",
  "tier": 3,
  "timeout_ms": 5000,
  "threshold": 80
}
```

`target` is what to say; `item_name` is the plain name, which is what the
vocabulary list should record. At tiers 1 and 2 they differ only in length.

Start the on-screen countdown when this arrives, not when `challenge` is sent.

### `partial`
Progress feedback, zero or more per challenge. Safe to ignore entirely; it is
there so the HUD can show what the player is being heard as saying.

```json
{
  "type": "partial",
  "id": "a3f1",
  "transcript": "el quick brown fox jumps",
  "stable": "el quick brown fox",
  "attempt": 1,
  "confidence": 42,
  "remaining_ms": 3100
}
```

Roughly five per second while someone is speaking. Each pass re-reads the
whole attempt, so whisper revises itself constantly — "dia" becomes
"diamante" becomes "el diamante". `stable` is the leading run of words two
successive passes agreed on; the remainder of `transcript` is still moving.

Rendering those two differently is what makes a live transcript readable
instead of a flicker, and it is the point of showing it at all: the audience
gets to watch the model change its mind. `transcript` is capped at 160
characters, because a decoder stuck in a repetition loop will cheerfully
produce a kilobyte of "la ropa de la ropa de" several times a second.

### `attempt`
One complete try fell short, and the next one starts from nothing.

```json
{ "type": "attempt", "id": "a3f1", "attempt": 1, "transcript": "dia", "confidence": 44, "remaining_ms": 3100 }
```

Audio is split into attempts at the pauses between them: roughly half a second
of silence ends one. Without that, a second try is transcribed together with
the first and the two blur into a single run-on utterance — a good attempt
gets dragged under by a bad one, and the extra text hands a wrong answer more
chances to match something. The player is judged on their **best** attempt.

In trade mode the frame carries an extra `note`: the model's own words on why
that sentence was turned down. `confidence` there is a word-match score against
the item's name and means very little about a sentence — the `note` is the part
worth showing.

A client that ignores this frame still works; it just cannot tell the player
which try they are on.

### `result`
Terminal. Exactly one per challenge.

```json
{
  "type": "result",
  "id": "a3f1",
  "status": "success",
  "reason": "early",
  "confidence": 97,
  "transcript": "diamante",
  "target": "Diamante",
  "key": "item.minecraft.diamond",
  "threshold": 80,
  "elapsed_ms": 1240,
  "audio_ms": 1150,
  "debug": {
    "infer_ms": 148,
    "rms": 0.0412,
    "no_speech_prob": 0.01,
    "avg_logprob": -0.23,
    "detected_language": "es",
    "scores": { "ratio": 97, "token_sort": 97, "token_set": 97, "window": 97, "coverage": 97 }
  }
}
```

A trade result carries two more fields: `mode: "trade"` and `note`, the model's
one-line reason, which is the only explanation a sentence ever gets.

`note` is also filled on a **word** failure when the matcher could say what was
said instead -- `"that sounded like Hielo"`. Short item names are decided
against the rest of the vocabulary rather than against the target alone (see
"short names" in the README), so when a short word is rejected the backend
usually knows which item the sounds really fitted, and naming it is worth more
to the player than "wrong".

`status` is `"success"` or `"fail"`. `reason` values:

| reason | status | meaning |
|---|---|---|
| `early` | success | a speculative pass passed mid-stream |
| `end` | success | passed on the final pass after `end` |
| `timeout_grace` | success | passed on the last-chance pass at the deadline |
| `attempt` | success | passed on a completed attempt, mid-challenge |
| `judge_unavailable` | success | trade mode, no language model answered — allowed |
| `low_score` | fail | speech was understood, but it was the wrong word |
| `rejected` | fail | trade mode: the model would not accept the sentence |
| `no_speech` | fail | silence, or below the RMS gate — nobody said anything |
| `low_confidence` | fail | whisper decoded but with very poor log-probability |
| `timeout` | fail | clock expired |
| `cancelled` | fail | client sent `cancel` |
| `superseded` | fail | a new `challenge` replaced this one |
| `stt_error` | fail | inference threw; check the server log |

For the mod's purposes only `status` matters: `success` → `insertStack` + XP
chime, anything else → discard + fizzle. `reason` is for the log line and for
telling the player *why* ("I didn't hear anything" reads much better than
"wrong!" when their mic was muted).

### `error`
Non-terminal problem with a request; no challenge was opened.

```json
{ "type": "error", "id": "a3f1", "code": "unknown_item", "message": "no translation for that key/item" }
```

Codes: `bad_json`, `unknown_type`, `unknown_item`, `not_ready`.

An `unknown_item` means the mod should just give the player the item normally —
failing a challenge that could never be displayed would be unfair. Expect this
for modded items and for keys with no translation in the target language.

---

## REST helpers

| endpoint | purpose |
|---|---|
| `GET /health` | readiness, active model, device, loaded languages |
| `GET /languages` | what the language setting can be: `ready` now, `installable` from local assets |
| `GET /translate?key=item.minecraft.diamond&lang=fr_fr` | single lookup |
| `GET /translate?item=minecraft:diamond` | same, by item id |
| `GET /vocab/random?n=10&lang=ja_jp` | sample entries, for building word lists |
| `GET /review?lang=es_es` | pick a word this player keeps getting wrong |
| `GET /review/hardest?lang=es_es&limit=20` | the worst-going words, for a progress screen |
| `POST /review/reset?lang=es_es` | forget the drill history (add `&all=true` for every language) |
| `GET /pronounce?key=item.minecraft.diamond` | the word spoken, 16 kHz mono wav |
| `GET /pronounce?word=Quiero%20pan&lang=es_es` | any text spoken, same format |

### Spaced repetition

The backend keeps a small SQLite table of every attempt — successes, failures
and when each word was last seen. `GET /review` draws from it, weighted towards
what keeps going wrong: a word's chance rises with its failure count, rises
further if it has never once been right, and rises slowly the longer it has
been since it was last asked. A language with no history yet answers `404`,
which is the honest answer — there is nothing to review.

This is what lets a door or a chest ask about something the player struggled
with twenty minutes ago. The mod has no idea which words those are; it asks.

**Trade challenges are not recorded.** The history answers one question —
which word does this player keep mispronouncing — and a trade is not evidence
about it. A refusal says the sentence was not a request, nothing about saying
"pan", so counting it would send a door off to drill a word they may know
perfectly; counting an accepted one is worse, because it credits them for a
word the judge was never scoring. Word and review challenges are both
recorded, since both are exactly that question.

**The history is per language, not per player.** There is no player id in the
table, so on a shared server everyone draws from — and contributes to — one
pool. That is fine for the single-player case this was built for, and it is
why `POST /review/reset` is a deliberate call rather than something the mod's
`/leximine forget all` triggers: one player tidying their own vocabulary
list should not wipe what the server knows about everyone else.

`GET /pronounce` is the other half of a miss: hearing the word said properly by
a real voice for that language. Synthesised once and cached on disk, so repeats
are instant and work with no network afterwards.
