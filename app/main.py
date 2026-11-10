# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""FastAPI app: websocket challenge endpoint + a few REST helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .config import DIFFICULTY_PRESETS, SAMPLE_RATE, difficulty_for, settings
from . import phrases
from .lang import DictionaryRegistry, Entry
from .session import Challenge
from .llm import TradeJudge
from .stats import stats
from .stt import SttEngine
from .tts import Pronouncer

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("leximine")

VERSION = "0.1.3"

engine = SttEngine(settings)
dictionary = DictionaryRegistry(
    Path(settings.lang_dir), settings.target_lang,
    mc_root=Path(settings.mc_root) if settings.mc_root else None,
    mc_version=settings.mc_version,
)
pronouncer = Pronouncer(Path(settings.tts_cache_dir), SAMPLE_RATE)
judge = TradeJudge(settings.llm_base_url, settings.llm_model, settings.llm_timeout_s,
                   settings.llm_keep_alive)


@asynccontextmanager
async def lifespan(_: FastAPI):
    stats.open()
    dictionary.load_default()
    # Model load + warmup is seconds of blocking work; keep the loop free so
    # uvicorn can still answer /health while it happens.
    await asyncio.to_thread(engine.load)
    log.info("listening on ws://%s:%d/ws", settings.host, settings.port)
    try:
        yield
    finally:
        stats.close()


app = FastAPI(title="Leximine speech backend", version=VERSION, lifespan=lifespan)


def _audio_contract() -> dict[str, Any]:
    return {
        "sample_rate": SAMPLE_RATE,
        "channels": settings.channels,
        "sample_format": "s16le",
        "recommended_chunk_ms": 50,
    }


# ----------------------------------------------------------------------
# REST helpers (debugging / mod development)
# ----------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": engine.ready,
        "version": VERSION,
        "model": settings.model_size,
        "device": engine.device,
        "compute_type": engine.compute_type,
        "target_lang": settings.target_lang,
        "entries": len(dictionary.table(settings.target_lang)),
        "languages": [entry["code"] for entry in dictionary.available()],
        "threshold": settings.match_threshold,
        "difficulty": settings.difficulty,
        "difficulties": sorted(DIFFICULTY_PRESETS),
        "easy_mode": settings.leak_target_to_decoder,
        "audio": _audio_contract(),
    }


@app.get("/languages")
async def languages() -> dict[str, Any]:
    """What the mod's language setting can be set to.

    `ready` is what is on disk now; `installable` is everything the local
    Minecraft install could provide, which is fetched automatically the first
    time a challenge asks for it.

    Each ready entry also carries `sentences`: whether tiers 3 and 4 actually
    produce a sentence in that language, or quietly fall back to the bare item
    name. Having a dictionary is not the same as being playable -- without a
    phrase pack the difficulty ladder is flat and a netherite ingot asks
    exactly what a stick does. The picker says which is which rather than
    labelling both "ready" and letting someone find out.
    """
    ready = []
    for entry in dictionary.available():
        row = dict(entry)
        row["sentences"] = phrases.supports_sentences(str(entry.get("code", "")))
        ready.append(row)
    return {
        "default": settings.target_lang,
        "ready": ready,
        "installable": dictionary.installable(),
        "sentence_languages": sorted(phrases.sentence_languages()),
    }


@app.get("/review")
async def review(
    lang: str | None = Query(default=None),
    exclude: str | None = Query(default=None, description="a key not to pick"),
) -> dict[str, Any]:
    """A word worth being asked again, chosen from what keeps going wrong.

    The mod calls this when the player touches something lockable, so a door
    becomes a spot quiz on their own weak spots rather than a random word.
    404 means nothing has been attempted yet in this language, and the caller
    should simply let the block open.
    """
    code = (lang or settings.target_lang).lower()
    picked = await asyncio.to_thread(stats.pick_review, code, exclude)
    if not picked:
        raise HTTPException(status_code=404, detail="no attempt history yet")

    entry = dictionary.resolve(translation_key=picked["key"], lang=code, tier=1)
    if not entry:
        raise HTTPException(status_code=404, detail="word is no longer in the dictionary")
    return {
        "key": entry.key,
        "target": entry.target,
        "item_name": entry.item_name,
        "english": entry.english,
        "lang": entry.lang,
        "success_count": picked["success_count"],
        "fail_count": picked["fail_count"],
    }


@app.get("/review/hardest")
async def review_hardest(
    lang: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """What this language has gone worst, for a glance at progress."""
    code = (lang or settings.target_lang).lower()
    return {"lang": code, "tracked": stats.count(code),
            "words": await asyncio.to_thread(stats.hardest, code, limit)}


@app.post("/review/reset")
async def review_reset(
    lang: str | None = Query(default=None),
    all_languages: bool = Query(default=False, alias="all"),
) -> dict[str, Any]:
    """Throw the drill history away, for one language or all of it.

    POST rather than GET because it destroys data, and a GET would be one
    mistyped browser URL away from wiping someone's progress.
    """
    code = None if all_languages else (lang or settings.target_lang).lower()
    dropped = await asyncio.to_thread(stats.forget, code)
    return {"lang": code or "all", "forgotten": dropped,
            "tracked": stats.count(code)}


@app.get("/pronounce")
async def pronounce(
    key: str | None = Query(default=None),
    item: str | None = Query(default=None),
    word: str | None = Query(default=None, description="speak this text directly"),
    lang: str | None = Query(default=None),
) -> FileResponse:
    """A spoken example of the word, as 16 kHz mono wav.

    The mod plays this after a miss, which is the moment it is worth hearing.
    Cached on disk, so repeats are instant and work offline afterwards.
    """
    code = (lang or settings.target_lang).lower()
    text = word
    if not text:
        entry = dictionary.resolve(translation_key=key, item_id=item, lang=code)
        if not entry:
            raise HTTPException(status_code=404, detail="no translation for that key/item")
        text = entry.target

    path = await pronouncer.wav_for(code, text)
    if not path:
        raise HTTPException(
            status_code=503,
            detail="no text-to-speech engine available for " + code)
    return FileResponse(path, media_type="audio/wav",
                        headers={"X-Leximine-Word": text.encode("ascii", "replace").decode()})


@app.get("/translate")
async def translate(
    key: str | None = Query(default=None, description="translation key, e.g. item.minecraft.diamond"),
    item: str | None = Query(default=None, description="item id, e.g. minecraft:diamond"),
    lang: str | None = Query(default=None, description="language code, e.g. fr_fr"),
    # 4 exists as of the legendary tier. The websocket path reads the tier
    # straight off the message and so was never limited; this helper kept
    # rejecting the tier the game had already started sending.
    tier: int = Query(default=1, ge=1, le=4,
                      description="1 noun, 2 full name, 3 sentence, 4 a harder one"),
    level: int | None = Query(default=None, ge=0, le=3,
                              description="progress rung; omit to use the real one"),
    context: str = Query(default="default", description="craft | pickup"),
) -> dict[str, Any]:
    if not key and not item:
        raise HTTPException(status_code=400, detail="pass key= or item=")
    code = (lang or settings.target_lang).lower()
    entry = dictionary.resolve(
        translation_key=key, item_id=item, lang=lang, tier=tier, context=context,
        level=stats.level(code) if level is None else level)
    if not entry:
        raise HTTPException(status_code=404, detail="no translation for that key/item")
    return {"key": entry.key, "target": entry.target, "item_name": entry.item_name,
            "english": entry.english, "lang": entry.lang, "tier": entry.tier,
            "level": entry.level,
            "key_tokens": list(entry.key_tokens), "notes": list(entry.notes)}


@app.get("/vocab/random")
async def vocab_random(
    n: int = Query(default=10, ge=1, le=200),
    lang: str | None = Query(default=None),
) -> dict[str, Any]:
    """Handy for building word lists and for the test client."""
    table = dictionary.table(lang or settings.target_lang)
    pool = [k for k in table if k.startswith(("item.", "block."))]
    picked = random.sample(pool, min(n, len(pool)))
    return {"entries": [{"key": k, "target": table[k]} for k in picked]}


# ----------------------------------------------------------------------
# Websocket
# ----------------------------------------------------------------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    peer = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    log.info("client connected: %s", peer)

    # Pull the trade judge's weights into VRAM now, while the player is still
    # loading a world. A cold call measured 30.8s against a 12s trade
    # timeout, so warming when a trade *starts* can never arrive in time --
    # the first villager of the session would be refused however well they
    # asked. Returns immediately; the load happens on its own thread.
    judge.warm()

    async def keep_judge_warm() -> None:
        """Re-check residency for as long as this player is connected.

        Connecting is not enough on its own. Ollama's OpenAI endpoint ignores
        the keep_alive we ask for, so every judged trade resets the window to
        whatever OLLAMA_KEEP_ALIVE says -- 30 minutes on this machine. Mine
        for forty minutes and then walk into a village and the model would be
        gone again. Each tick is one /api/ps call and loads nothing unless it
        has actually lapsed.
        """
        try:
            while True:
                await asyncio.sleep(settings.llm_warm_interval_s)
                await asyncio.to_thread(judge.warm)
        except asyncio.CancelledError:
            pass

    warm_task = asyncio.create_task(keep_judge_warm())

    lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        # Serialised because speculative passes and the deadline watchdog can
        # both try to write at the same instant.
        async with lock:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))

    await send(
        {
            "type": "hello",
            "server": "leximine-backend",
            "version": VERSION,
            "ready": engine.ready,
            "model": settings.model_size,
            "device": engine.device,
            "target_lang": settings.target_lang,
            "default_timeout_ms": settings.default_timeout_ms,
            "threshold": settings.match_threshold,
            "audio": _audio_contract(),
        }
    )

    current: Challenge | None = None

    async def handle_text(raw: str) -> None:
        nonlocal current
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await send({"type": "error", "code": "bad_json", "message": "not JSON"})
            return
        if not isinstance(msg, dict):
            await send({"type": "error", "code": "bad_json", "message": "expected an object"})
            return

        mtype = msg.get("type")
        cid = str(msg.get("id", "") or "")

        if mtype == "ping":
            await send({"type": "pong"})
            return

        if mtype == "challenge":
            if not engine.ready:
                await send({"type": "error", "id": cid, "code": "not_ready",
                            "message": "model still loading"})
                return
            if current and not current.done:
                await current.cancel("superseded")
                await current.close()
            # Language comes from the mod's settings, per challenge, so one
            # backend serves whatever the player picks without a restart.
            requested_lang = (msg.get("lang") or settings.target_lang).lower()
            if not dictionary.is_available(requested_lang):
                await send({"type": "error", "id": cid, "code": "unknown_language",
                            "message": f"no dictionary for {requested_lang}"})
                return
            entry = dictionary.resolve(
                translation_key=msg.get("translation_key") or msg.get("key"),
                item_id=msg.get("item_id") or msg.get("item"),
                lang=requested_lang,
                # How much the player has to say scales with how valuable the
                # item is; the mod classifies that and sends the tier.
                tier=int(msg.get("tier") or 1),
                context=str(msg.get("context") or "default"),
                # How *hard* a sentence they get scales with what they have
                # already said correctly, which only this side knows. Stakes
                # follow the item, difficulty follows the player.
                level=stats.level(requested_lang),
            )
            task = str(msg.get("task", ""))[:300]
            if not entry and task:
                # A task challenge has no item by definition -- "say why you
                # are going to the Nether" is not about a thing in the
                # dictionary -- so there is nothing to look up and failing the
                # lookup would decline every one of them.
                #
                # Deliberately NOT solved by having the client send some real
                # item's key to get past this. `Challenge._finalize` records
                # every attempt against `entry.key` for the spaced-repetition
                # draw, so borrowing `block.minecraft.obsidian` would make a
                # fumbled portal sentence show up later as a word the player
                # keeps missing, and doors would start quizzing them on
                # "obsidiana" because of it.
                entry = Entry(
                    key=str(msg.get("translation_key") or msg.get("key") or "task"),
                    target="",
                    english="",
                    lang=requested_lang,
                    item_name="",
                    tier=int(msg.get("tier") or 3),
                )
            if not entry:
                await send(
                    {
                        "type": "error",
                        "id": cid,
                        "code": "unknown_item",
                        "message": "no translation for that key/item",
                    }
                )
                return
            timeout_ms = int(msg.get("timeout_ms") or settings.default_timeout_ms)
            # Difficulty moves the overall threshold and the per-word gate
            # together; tuning one without the other just trades a false
            # accept for a false reject.
            preset_threshold, preset_gate = difficulty_for(
                msg.get("difficulty"), settings.difficulty)
            threshold = int(msg.get("threshold") or preset_threshold)
            token_gate = int(msg.get("token_gate") or preset_gate)
            current = Challenge(
                challenge_id=cid or "0",
                entry=entry,
                timeout_ms=max(500, min(timeout_ms, 60_000)),
                threshold=max(1, min(threshold, 100)),
                token_gate=max(1, min(token_gate, 100)),
                mode="trade" if str(msg.get("mode", "")) == "trade" else "word",
                # Capped where it is read, above: it goes straight into a
                # system prompt, and Ollama truncates silently past its
                # context window rather than erroring, so an overlong task
                # would quietly take the rest of the instructions with it.
                task=task,
                judge=judge,
                send=send,
                engine=engine,
                settings=settings,
            )
            await current.start()
            return

        if mtype == "end":
            if current and not current.done:
                await current.request_final()
            return

        if mtype == "cancel":
            if current and not current.done:
                await current.cancel("cancelled")
            return

        await send({"type": "error", "id": cid, "code": "unknown_type",
                    "message": f"unknown message type {mtype!r}"})

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                if current and not current.done:
                    await current.feed(data)
                continue
            text = message.get("text")
            if text is not None:
                await handle_text(text)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket loop error")
    finally:
        warm_task.cancel()
        if current:
            await current.close()
        log.info("client disconnected: %s", peer)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        ws_max_size=16 * 1024 * 1024,
    )


if __name__ == "__main__":
    run()
