# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Minecraft localisation files -> (translation key -> target word) lookup.

The mod sends the stack's translation key (`item.minecraft.diamond`), which is
exactly what `ItemStack.getItem().getTranslationKey()` returns, so no guessing
is needed on the Java side. Raw item ids (`minecraft:diamond`) are also
accepted for curl-level debugging.

Languages are loaded on demand rather than one at startup, because the mod
sends the language it wants with each challenge -- that is what lets the
player change language in the mod's settings without restarting anything. A
language that is not on disk is extracted from the local Minecraft assets the
first time it is asked for.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from . import matching, mcassets, phrases
from .fallback_dict import FALLBACK

log = logging.getLogger(__name__)

# Minecraft format codes (§a) and java format placeholders (%s, %1$s). A value
# containing a placeholder is a sentence template, not a noun, so it is not
# something a player can pronounce.
_FORMAT_CODE = re.compile(r"§.")
_PLACEHOLDER = re.compile(r"%(\d+\$)?[sd]")

# Key prefixes tried when resolving a bare item id, in priority order.
_ID_PREFIXES = ("item", "block", "entity")

_LANG_CODE = re.compile(r"^[a-z]{2,3}_[a-z0-9]{2,8}$")


@dataclass(frozen=True)
class Entry:
    key: str
    target: str
    """What the player must say. At tier 1 a shortened name, at tier 3 a sentence."""
    english: str
    lang: str
    item_name: str = ""
    """The plain item name, for the HUD and the vocabulary list."""
    tier: int = 1
    level: int = 0
    """Which rung of the sentence pool this prompt was drawn from."""
    key_tokens: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    rivals: tuple[str, ...] = ()
    """Other item names close enough to this one to be the real answer.

    Only filled for short one-word targets, which are the ones the matcher
    cannot decide on similarity alone -- see `matching.SHORT_TARGET_LEN`.
    """

    def __post_init__(self) -> None:
        if not self.item_name:
            object.__setattr__(self, "item_name", self.target)


def _clean(value: str) -> str:
    return _FORMAT_CODE.sub("", value).strip()


def _english_from_key(key: str) -> str:
    """Last-resort English display name: item.minecraft.gold_ingot -> Gold Ingot."""
    return key.rsplit(".", 1)[-1].replace("_", " ").title()


def _load_file(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        value = _clean(value)
        if value and not _PLACEHOLDER.search(value):
            out[key] = value
    return out


class DictionaryRegistry:
    """Every language the backend can currently serve, loaded lazily."""

    def __init__(self, lang_dir: Path, default_lang: str,
                 mc_root: Path | None = None, mc_version: str = "1.20.4") -> None:
        self.lang_dir = lang_dir
        self.default_lang = default_lang
        self.mc_root = mc_root or mcassets.DEFAULT_MC_ROOT
        self.mc_version = mc_version
        self._tables: dict[str, dict[str, str]] = {}
        self._short_names_cache: dict[str, list[str]] = {}
        self._english: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def load_default(self) -> None:
        """Warm the configured language and the English glosses at startup."""
        self._english = self._load_english()
        table = self.table(self.default_lang)
        if not table:
            raise RuntimeError(
                f"No {self.default_lang} strings available. Run "
                f"scripts/extract_lang.py --langs {self.default_lang}")
        log.info("dictionary ready: %s (%d words), en_us (%d glosses)",
                 self.default_lang, len(table), len(self._english))

    def _load_english(self) -> dict[str, str]:
        path = self.lang_dir / "en_us.json"
        if not path.is_file():
            mcassets.extract_en_us(self.lang_dir, root=self.mc_root,
                                   version=self.mc_version)
        if path.is_file():
            return _load_file(path)
        log.warning("no en_us.json; English glosses fall back to the key name")
        return {}

    # ------------------------------------------------------------------
    def table(self, lang: str) -> dict[str, str]:
        """Strings for one language, loading or extracting it if needed."""
        lang = (lang or self.default_lang).lower()
        cached = self._tables.get(lang)
        if cached is not None:
            return cached
        if not _LANG_CODE.match(lang):
            # Rejected rather than passed to a path join: this value arrives
            # over the wire from the mod.
            log.warning("ignoring malformed language code %r", lang)
            self._tables[lang] = {}
            return {}

        with self._lock:
            cached = self._tables.get(lang)
            if cached is not None:
                return cached

            table: dict[str, str] = dict(FALLBACK.get(lang, {}))
            path = self.lang_dir / f"{lang}.json"
            if not path.is_file():
                log.info("%s not on disk; extracting from Minecraft assets", lang)
                mcassets.extract_language(lang, self.lang_dir, root=self.mc_root,
                                          version=self.mc_version)
            if path.is_file():
                table.update(_load_file(path))
                log.info("loaded %s (%d words)", lang, len(table))
            elif table:
                log.warning("%s falling back to the built-in word list (%d words)",
                            lang, len(table))
            else:
                log.warning("no strings available for %s", lang)

            self._tables[lang] = table
            return table

    def is_available(self, lang: str) -> bool:
        return bool(self.table(lang))

    def available(self) -> list[dict[str, object]]:
        """Languages ready to use right now, without extracting anything."""
        found: dict[str, int] = {}
        for path in sorted(self.lang_dir.glob("*.json")):
            code = path.stem.lower()
            if code == "en_us" or not _LANG_CODE.match(code):
                continue
            found[code] = self._tables[code].__len__() if code in self._tables else -1
        for code in FALLBACK:
            found.setdefault(code, -1)
        return [{"code": code, "loaded": count >= 0, "words": max(count, 0)}
                for code, count in sorted(found.items())]

    def installable(self) -> list[str]:
        """Everything the local Minecraft install could provide."""
        return mcassets.available_languages(self.mc_root, self.mc_version)

    # ------------------------------------------------------------------
    def _short_names(self, lang: str) -> list[str]:
        """Every one-word item name short enough to be mistaken for another.

        Built once per language and kept. The matcher uses it to answer "which
        of these did they say" instead of "is this one it", which is the only
        answerable question when the whole word is four letters long.
        """
        cached = self._short_names_cache.get(lang)
        if cached is not None:
            return cached
        names: dict[str, None] = {}
        for key, value in self.table(lang).items():
            if not key.startswith(("item.", "block.")):
                continue
            norm = matching.normalize(value, language=lang)
            # Up to *twice* the longest eligible target, because that is the
            # window the matcher itself compares over -- a name that is close
            # enough to be confused with a four-letter word can be eight
            # letters long. Capping this nearer the target length looked
            # tidier and measurably leaked: at a cutoff of 5 the wrong-accept
            # rate was 2.3% against 0.2%, purely because the rival that would
            # have vetoed the answer had been left out of the pool.
            if " " in norm or not 2 <= len(norm) <= 2 * matching.SHORT_TARGET_LEN:
                continue
            names[value] = None
        out = list(names)
        self._short_names_cache[lang] = out
        log.info("%s: %d short one-word names for disambiguation", lang, len(out))
        return out

    def rivals_for(self, target: str, lang: str) -> tuple[str, ...]:
        """Names that could plausibly be `target`; empty unless it is short."""
        norm = matching.normalize(target, language=lang)
        if " " in norm or len(norm) > matching.SHORT_TARGET_LEN:
            return ()
        return tuple(name for name in self._short_names(lang)
                     if matching.normalize(name, language=lang) != norm)

    def candidate_keys(self, *, translation_key: str | None, item_id: str | None) -> list[str]:
        if translation_key:
            return [translation_key]
        if not item_id:
            return []
        namespace, _, path = item_id.partition(":")
        if not path:
            namespace, path = "minecraft", namespace
        return [f"{prefix}.{namespace}.{path}" for prefix in _ID_PREFIXES]

    def resolve(self, *, translation_key: str | None = None,
                item_id: str | None = None, lang: str | None = None,
                tier: int = 1, context: str = "default",
                level: int = 0) -> Entry | None:
        code = (lang or self.default_lang).lower()
        table = self.table(code)
        if not table:
            return None
        for key in self.candidate_keys(translation_key=translation_key, item_id=item_id):
            item_name = table.get(key)
            if not item_name:
                continue
            english_name = self._english.get(key) or _english_from_key(key)
            # The dictionary gives the item's name; what the player is asked
            # to say depends on how valuable the item is and on how far along
            # they are. The English name goes in too, so a sentence prompt
            # can be glossed as a sentence rather than as one of its words.
            expected = phrases.expected_for(
                item_name, code, tier, context,
                english_name=english_name, level=level)
            return Entry(key=key, target=expected.text,
                         english=expected.english or english_name,
                         lang=code, item_name=item_name, tier=expected.tier,
                         level=expected.level,
                         key_tokens=expected.key_tokens, notes=expected.notes,
                         rivals=self.rivals_for(expected.text, code))
        return None


def whisper_language_for(lang: str) -> str:
    """es_es -> es. Good enough for every Minecraft language code."""
    return (lang or "en_us").split("_")[0].lower()
