# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Which words keep going wrong, so they can be asked again on purpose.

The backend decides every verdict, so it can keep this record itself — no
extra reporting path from the mod, and no way for the two to disagree about
what happened.

Deliberately separate from the mod's own `leximine-progress.json`, which
answers a different question: that file is per player and decides *when* a
word is due, while this is the cross-session tally of what is actually hard.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    lang          TEXT    NOT NULL,
    item_key      TEXT    NOT NULL,
    item_name     TEXT    NOT NULL DEFAULT '',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    last_seen     REAL    NOT NULL DEFAULT 0,
    last_correct  REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (lang, item_key)
);
CREATE INDEX IF NOT EXISTS attempts_by_difficulty
    ON attempts (lang, fail_count DESC);
"""


class AttemptStats:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because writes arrive from the websocket
        # thread and reads from request handlers; the lock below serialises
        # them, which is ample for one player's worth of traffic.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()
        log.info("attempt history: %s (%d words)", self.path, self.count())

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def count(self, lang: str | None = None) -> int:
        if self._db is None:
            return 0
        with self._lock:
            if lang:
                row = self._db.execute(
                    "SELECT COUNT(*) AS n FROM attempts WHERE lang = ?", (lang,)).fetchone()
            else:
                row = self._db.execute("SELECT COUNT(*) AS n FROM attempts").fetchone()
        return int(row["n"])

    #: Total correct answers needed to reach each rung. Deliberately shallow
    #: at the start -- the first widening should arrive while the novelty is
    #: still there, not after an hour of the same sentence.
    LEVEL_THRESHOLDS = (0, 15, 50, 120)

    def successes(self, lang: str) -> int:
        """How many words this language has had right, counting repeats."""
        if self._db is None:
            return 0
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(success_count), 0) AS n FROM attempts "
                "WHERE lang = ?", (lang,)).fetchone()
        return int(row["n"])

    def level(self, lang: str) -> int:
        """0-3: how demanding a sentence this player has earned.

        Driven by correct answers rather than time played or items seen, so
        it only moves when the player actually says something right. Note
        that, like the rest of this table, it is per *language* and not per
        player -- fine alone, shared on a server.
        """
        earned = self.successes(lang)
        rung = 0
        for index, needed in enumerate(self.LEVEL_THRESHOLDS):
            if earned >= needed:
                rung = index
        return rung

    # ------------------------------------------------------------------
    def record(self, lang: str, item_key: str, item_name: str, success: bool) -> None:
        if self._db is None or not item_key:
            return
        now = time.time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO attempts (lang, item_key, item_name, success_count,
                                      fail_count, last_seen, last_correct)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lang, item_key) DO UPDATE SET
                    item_name     = COALESCE(NULLIF(excluded.item_name, ''), attempts.item_name),
                    success_count = attempts.success_count + excluded.success_count,
                    fail_count    = attempts.fail_count + excluded.fail_count,
                    last_seen     = excluded.last_seen,
                    last_correct  = MAX(attempts.last_correct, excluded.last_correct)
                """,
                (lang, item_key, item_name or "", 1 if success else 0,
                 0 if success else 1, now, now if success else 0.0),
            )
            self._db.commit()

    # ------------------------------------------------------------------
    def pick_review(self, lang: str, exclude: str | None = None) -> dict | None:
        """A word worth being asked again.

        Weighted rather than simply "the worst one": always serving the single
        highest fail count would drill one word forever and never revisit the
        near-misses behind it. Misses dominate the weight, with a nudge for
        anything not got right in a while.
        """
        if self._db is None:
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM attempts WHERE lang = ?", (lang,)).fetchall()
        candidates = [row for row in rows if row["item_key"] != exclude]
        if not candidates:
            return None

        now = time.time()
        weights = []
        for row in candidates:
            weight = 1.0 + 3.0 * row["fail_count"]
            if row["success_count"] == 0:
                # Never once correct: the most worth revisiting.
                weight += 4.0
            stale_hours = (now - (row["last_correct"] or 0)) / 3600.0
            weight += min(stale_hours, 48.0) / 12.0
            weights.append(weight)

        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return {
            "key": chosen["item_key"],
            "item_name": chosen["item_name"],
            "lang": chosen["lang"],
            "success_count": chosen["success_count"],
            "fail_count": chosen["fail_count"],
        }

    def hardest(self, lang: str, limit: int = 20) -> list[dict]:
        if self._db is None:
            return []
        with self._lock:
            rows = self._db.execute(
                """
                SELECT * FROM attempts WHERE lang = ?
                ORDER BY fail_count DESC, success_count ASC, last_seen DESC
                LIMIT ?
                """,
                (lang, limit)).fetchall()
        return [
            {
                "key": row["item_key"],
                "item_name": row["item_name"],
                "success_count": row["success_count"],
                "fail_count": row["fail_count"],
            }
            for row in rows
        ]

    def forget(self, lang: str | None = None) -> int:
        """Throw away the history, for one language or all of it.

        There has to be a way to do this. The mod's `/leximine forget all`
        wipes the vocabulary list it keeps itself, but the drills a door asks
        come from here -- so without this, "start that language over" left the
        backend still quizzing you on everything you had ever fumbled.

        Returns how many words were dropped.
        """
        if self._db is None:
            return 0
        with self._lock:
            if lang:
                cursor = self._db.execute("DELETE FROM attempts WHERE lang = ?", (lang,))
            else:
                cursor = self._db.execute("DELETE FROM attempts")
            self._db.commit()
            return cursor.rowcount if cursor.rowcount > 0 else 0


# One store for the process. Opened in the app's lifespan; recording is a
# no-op until then, so importing this module never touches the disk.
stats = AttemptStats(Path(__file__).resolve().parent.parent / "data" / "attempts.db")
