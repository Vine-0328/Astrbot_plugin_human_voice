from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Collection, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

INITIAL_SCORE = 4000
MAX_SCORE = 10000
SCORE_DELTAS = {
    "casual": 1,
    "praise": 2,
    "kind_correction": 2,
    "help_request": 2,
    "hostile": -2,
}
EMOTION_RULES = {
    "lively": (80, 3, 3),
    "cool": (60, 0, 1),
    "neutral": (70, 2, 2),
}
CARE_THRESHOLDS = range(50, 101, 10)
MEMORY_LIMIT = 20
MEMORY_TEXT_LIMIT = 240
LONG_TERM_MEMORY_LIMIT = 6000
ATTITUDE_LIMIT = 1000
RELATIONSHIP_LIMIT = 100
PSYCHOLOGY_LIMIT = 320
DIALOGUE_LIMIT = 12
EVENT_TTL_SECONDS = 7 * 24 * 60 * 60
CLAIM_TTL_SECONDS = 5 * 60


@dataclass(frozen=True)
class StateSnapshot:
    score: float = INITIAL_SCORE / 100
    relationship: str = "初识"
    memories: tuple[str, ...] = ()
    psychology: str = ""
    dialogue: tuple[tuple[str, str], ...] = ()
    awaiting_clarification: bool = False
    milestones: tuple[int, ...] = ()
    emotion: int = 70
    long_term_memory: str = ""
    attitude: str = ""


def _clean(value: object, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _memories(value: object) -> list[str]:
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, (list, tuple)):
        return []
    items = [_clean(item, MEMORY_TEXT_LIMIT) for item in value]
    return list(dict.fromkeys(item for item in items if item))[-MEMORY_LIMIT:]


def _normalise_overrides(overrides: Mapping[str, object]) -> tuple[dict, dict]:
    user, session = {}, {}
    if "score" in overrides:
        try:
            score = Decimal(str(overrides["score"]))
            if score.is_finite():
                score = max(Decimal(0), min(Decimal(100), score))
                user["score"] = int((score * 100).to_integral_value(ROUND_HALF_UP))
        except InvalidOperation:
            pass
    if isinstance(overrides.get("relationship"), str):
        user["relationship"] = _clean(overrides["relationship"], RELATIONSHIP_LIMIT)
    if isinstance(overrides.get("memories"), (str, list, tuple)):
        session["memories"] = _memories(overrides["memories"])
    if isinstance(overrides.get("psychology"), str):
        session["psychology"] = _clean(overrides["psychology"], PSYCHOLOGY_LIMIT)
    return user, session


def _emotion_default(style: str) -> int:
    return EMOTION_RULES.get(style, EMOTION_RULES["neutral"])[0]


def _next_emotion(current: int, style: str, category: str) -> int:
    _, everyday, reaction = EMOTION_RULES.get(style, EMOTION_RULES["neutral"])
    if category == "hostile":
        return max(0, current - reaction)
    if category == "praise":
        return min(100, current + reaction)
    if category in {"casual", "kind_correction", "help_request"}:
        return min(100, current + everyday)
    return current


def _validate_edit(changes: Mapping[str, object], creator: bool) -> dict:
    text_fields = (
        ("relationship", "人物关系", RELATIONSHIP_LIMIT),
        ("psychology", "心理活动", PSYCHOLOGY_LIMIT),
        ("long_term_memory", "长期记忆", LONG_TERM_MEMORY_LIMIT),
        ("attitude", "态度", ATTITUDE_LIMIT),
    )
    editable = {"affection", "memory", "emotion"} | {
        field for field, _, _ in text_fields
    }
    if not isinstance(changes, Mapping) or not changes or changes.keys() - editable:
        raise ValueError("请填写需要修改的字段")
    validated = {}
    for field, label, digits in (("affection", "好感度", 2), ("emotion", "情绪", 0)):
        if field not in changes:
            continue
        value = changes[field]
        if type(value) not in {int, float}:
            raise ValueError(f"{label}需要是数字")
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= 100:
            raise ValueError(f"{label}必须在 0 到 100 之间")
        points = number * 10**digits
        if points != points.to_integral_value():
            raise ValueError(
                f"{label}最多保留两位小数" if digits else f"{label}需要是整数"
            )
        if field == "affection" and creator:
            if number != 100:
                raise ValueError("创建者的好感度固定为 100")
            continue
        validated[field] = int(points)

    for field, label, limit in text_fields:
        if field not in changes:
            continue
        value = changes[field]
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise ValueError(f"{label}最多 {limit} 字")
        validated[field] = value.strip()

    if "memory" in changes:
        memories = changes["memory"]
        if not isinstance(memories, list) or len(memories) > MEMORY_LIMIT:
            raise ValueError(f"记忆最多 {MEMORY_LIMIT} 条")
        if any(
            not isinstance(memory, str) or len(memory.strip()) > MEMORY_TEXT_LIMIT
            for memory in memories
        ):
            raise ValueError(f"每条记忆最多 {MEMORY_TEXT_LIMIT} 字")
        validated["memory"] = json.dumps(_memories(memories), ensure_ascii=False)
    return validated


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    score INTEGER NOT NULL DEFAULT 4000,
                    relationship TEXT NOT NULL DEFAULT '初识',
                    overrides TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    memories TEXT NOT NULL DEFAULT '[]',
                    psychology TEXT NOT NULL DEFAULT '',
                    dialogue TEXT NOT NULL DEFAULT '[]',
                    awaiting_clarification INTEGER NOT NULL DEFAULT 0,
                    overrides TEXT NOT NULL DEFAULT '{}',
                    emotion INTEGER,
                    long_term_memory TEXT NOT NULL DEFAULT '',
                    attitude TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, scope)
                );
                CREATE INDEX IF NOT EXISTS sessions_by_time ON sessions(updated_at);
                CREATE TABLE IF NOT EXISTS processed_events (
                    user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (user_id, scope, message_id)
                );
                CREATE INDEX IF NOT EXISTS events_by_time ON processed_events(created_at);
                CREATE TABLE IF NOT EXISTS care (
                    user_id TEXT NOT NULL,
                    milestone INTEGER NOT NULL,
                    scope TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0,
                    claim_token TEXT,
                    claim_until REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, milestone)
                );
                """
            )
        with self._connect(write=True) as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            if "emotion" not in columns:
                # Old rows adopt the configured starting emotion on their next turn.
                db.execute("ALTER TABLE sessions ADD COLUMN emotion INTEGER")
            if "long_term_memory" not in columns:
                db.execute(
                    "ALTER TABLE sessions ADD COLUMN long_term_memory TEXT NOT NULL DEFAULT ''"
                )
            if "attitude" not in columns:
                db.execute(
                    "ALTER TABLE sessions ADD COLUMN attitude TEXT NOT NULL DEFAULT ''"
                )

    @contextmanager
    def _connect(self, *, write: bool = False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    def reset(self) -> None:
        """Restore runtime defaults while retaining the dashboard's conversation entries."""
        with self._connect(write=True) as db:
            db.execute("DELETE FROM care")
            db.execute("DELETE FROM processed_events")
            db.execute(
                "UPDATE users SET score = ?, relationship = '初识', overrides = '{}'",
                (INITIAL_SCORE,),
            )
            db.execute(
                """UPDATE sessions SET memories = '[]', psychology = '', dialogue = '[]',
                   awaiting_clarification = 0, overrides = '{}', emotion = NULL,
                   long_term_memory = '', attitude = ''"""
            )

    @staticmethod
    def _rows(db, user_id: str, scope: str):
        user = db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        session = db.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND scope = ?", (user_id, scope)
        ).fetchone()
        return user, session

    @staticmethod
    def _snapshot(
        db, user_id: str, scope: str, creator: bool, emotion_style: str
    ) -> StateSnapshot:
        user, session = StateStore._rows(db, user_id, scope)
        milestones = ()
        if not creator:
            pending = db.execute(
                """SELECT milestone FROM care WHERE user_id = ? AND scope = ?
                   AND delivered = 0 AND claim_until <= ? ORDER BY milestone""",
                (user_id, scope, time.time()),
            )
            milestones = tuple(row[0] for row in pending)
        score = MAX_SCORE if creator else user["score"] if user else INITIAL_SCORE
        return StateSnapshot(
            score=score / 100,
            relationship=user["relationship"] if user else "初识",
            memories=tuple(json.loads(session["memories"])) if session else (),
            psychology=session["psychology"] if session else "",
            dialogue=tuple(map(tuple, json.loads(session["dialogue"])))
            if session
            else (),
            awaiting_clarification=bool(session and session["awaiting_clarification"]),
            milestones=milestones,
            emotion=session["emotion"]
            if session and session["emotion"] is not None
            else _emotion_default(emotion_style),
            long_term_memory=session["long_term_memory"] if session else "",
            attitude=session["attitude"] if session else "",
        )

    @staticmethod
    def _apply_overrides(db, user_id, scope, overrides) -> None:
        user_values, session_values = _normalise_overrides(overrides)
        user, session = StateStore._rows(db, user_id, scope)
        previous = json.loads(user["overrides"]) if user else {}
        if user_values != previous:
            score = user["score"] if user else INITIAL_SCORE
            relationship = user["relationship"] if user else "初识"
            if "score" in user_values and user_values["score"] != previous.get("score"):
                score = user_values["score"]
            if "relationship" in user_values and user_values[
                "relationship"
            ] != previous.get("relationship"):
                relationship = user_values["relationship"]
            db.execute(
                """INSERT INTO users (user_id, score, relationship, overrides) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET score = excluded.score,
                   relationship = excluded.relationship, overrides = excluded.overrides""",
                (
                    user_id,
                    score,
                    relationship,
                    json.dumps(user_values, ensure_ascii=False),
                ),
            )

        previous = json.loads(session["overrides"]) if session else {}
        if session_values != previous:
            memories = session["memories"] if session else "[]"
            psychology = session["psychology"] if session else ""
            if "memories" in session_values and session_values[
                "memories"
            ] != previous.get("memories"):
                memories = json.dumps(session_values["memories"], ensure_ascii=False)
            if "psychology" in session_values and session_values[
                "psychology"
            ] != previous.get("psychology"):
                psychology = session_values["psychology"]
            db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            db.execute(
                """INSERT INTO sessions (user_id, scope, memories, psychology, overrides, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, scope) DO UPDATE SET
                   memories = excluded.memories, psychology = excluded.psychology,
                   overrides = excluded.overrides, updated_at = excluded.updated_at""",
                (
                    user_id,
                    scope,
                    memories,
                    psychology,
                    json.dumps(session_values, ensure_ascii=False),
                    time.time(),
                ),
            )

    def snapshot(
        self,
        user_id: str,
        scope: str,
        *,
        creator: bool = False,
        overrides: Mapping[str, object] | None = None,
        emotion_style: str = "neutral",
    ) -> StateSnapshot:
        with self._connect(write=overrides is not None) as db:
            if overrides is not None:
                self._apply_overrides(db, user_id, scope, overrides)
            return self._snapshot(db, user_id, scope, creator, emotion_style)

    def is_recorded(self, user_id: str, scope: str, message_id: str | None) -> bool:
        if not message_id:
            return False
        with self._connect() as db:
            return (
                db.execute(
                    """SELECT 1 FROM processed_events
                   WHERE user_id = ? AND scope = ? AND message_id = ? AND created_at >= ?""",
                    (user_id, scope, message_id, time.time() - EVENT_TTL_SECONDS),
                ).fetchone()
                is not None
            )

    def record(
        self,
        user_id: str,
        scope: str,
        message_id: str | None,
        *,
        category: str,
        creator: bool = False,
        memory: str = "",
        psychology: str = "",
        user_text: str = "",
        reply_text: str = "",
        clarifying: bool = False,
        emotion_style: str = "neutral",
        long_term_memory: str | None = None,
        attitude: str | None = None,
    ) -> StateSnapshot:
        now = time.time()
        with self._connect(write=True) as db:
            if message_id:
                db.execute(
                    "DELETE FROM processed_events WHERE created_at < ?",
                    (now - EVENT_TTL_SECONDS,),
                )
                inserted = db.execute(
                    "INSERT OR IGNORE INTO processed_events VALUES (?, ?, ?, ?)",
                    (user_id, scope, message_id, now),
                ).rowcount
                if not inserted:
                    return self._snapshot(db, user_id, scope, creator, emotion_style)

            user, session = self._rows(db, user_id, scope)
            old_score = user["score"] if user else INITIAL_SCORE
            delta = 0 if creator else SCORE_DELTAS.get(category, 0)
            score = max(0, min(MAX_SCORE, old_score + delta))
            db.execute(
                """INSERT INTO users (user_id, score) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET score = excluded.score""",
                (user_id, score),
            )
            for milestone in CARE_THRESHOLDS:
                if old_score < milestone * 100 <= score:
                    db.execute(
                        "INSERT OR IGNORE INTO care (user_id, milestone, scope) VALUES (?, ?, ?)",
                        (user_id, milestone, scope),
                    )

            memories = json.loads(session["memories"]) if session else []
            memory = _clean(memory, MEMORY_TEXT_LIMIT)
            if memory:
                if memory in memories:
                    memories.remove(memory)
                memories.append(memory)
            dialogue = json.loads(session["dialogue"]) if session else []
            user_text, reply_text = _clean(user_text, 600), _clean(reply_text, 800)
            if user_text and reply_text:
                dialogue.append((user_text, reply_text))
            psychology = _clean(psychology, PSYCHOLOGY_LIMIT) or (
                session["psychology"] if session else ""
            )
            current_emotion = (
                session["emotion"]
                if session and session["emotion"] is not None
                else _emotion_default(emotion_style)
            )
            emotion = _next_emotion(current_emotion, emotion_style, category)
            summary = (
                _clean(long_term_memory, LONG_TERM_MEMORY_LIMIT)
                if long_term_memory is not None
                else session["long_term_memory"]
                if session
                else ""
            )
            updated_attitude = (
                _clean(attitude, ATTITUDE_LIMIT)
                if attitude is not None
                else session["attitude"]
                if session
                else ""
            )
            db.execute(
                """INSERT INTO sessions (user_id, scope, memories, psychology, dialogue,
                   awaiting_clarification, emotion, long_term_memory, attitude, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, scope) DO UPDATE SET memories = excluded.memories,
                   psychology = excluded.psychology, dialogue = excluded.dialogue,
                   awaiting_clarification = excluded.awaiting_clarification,
                   emotion = excluded.emotion, long_term_memory = excluded.long_term_memory,
                   attitude = excluded.attitude, updated_at = excluded.updated_at""",
                (
                    user_id,
                    scope,
                    json.dumps(memories[-MEMORY_LIMIT:], ensure_ascii=False),
                    psychology,
                    json.dumps(dialogue[-DIALOGUE_LIMIT:], ensure_ascii=False),
                    int(clarifying),
                    emotion,
                    summary,
                    updated_attitude,
                    now,
                ),
            )
            return self._snapshot(db, user_id, scope, creator, emotion_style)

    def claim_care(self, user_id: str, scope: str, milestone: int) -> str | None:
        token = uuid4().hex
        now = time.time()
        with self._connect(write=True) as db:
            claimed = db.execute(
                """UPDATE care SET claim_token = ?, claim_until = ? WHERE user_id = ?
                   AND scope = ? AND milestone = ? AND delivered = 0 AND claim_until <= ?""",
                (token, now + CLAIM_TTL_SECONDS, user_id, scope, milestone, now),
            ).rowcount
        return token if claimed else None

    def finish_care(
        self, user_id: str, milestone: int, token: str, *, sent: bool, text: str = ""
    ) -> None:
        with self._connect(write=True) as db:
            care = db.execute(
                """UPDATE care SET delivered = ?, claim_token = NULL, claim_until = 0
                   WHERE user_id = ? AND milestone = ? AND claim_token = ? RETURNING scope""",
                (int(sent), user_id, milestone, token),
            ).fetchone()
            if not (care and sent and text):
                return
            scope = care["scope"]
            _, session = self._rows(db, user_id, scope)
            dialogue = json.loads(session["dialogue"]) if session else []
            if dialogue:
                question, reply = dialogue[-1]
                dialogue[-1] = (question, f"{reply}\n{text}")
                db.execute(
                    "UPDATE sessions SET dialogue = ? WHERE user_id = ? AND scope = ?",
                    (json.dumps(dialogue, ensure_ascii=False), user_id, scope),
                )

    @staticmethod
    def _status_row(user, session, creator: bool, emotion_style: str) -> dict:
        relationship = user["relationship"]
        if creator and relationship in {"", "初识"}:
            relationship = "创建者"
        emotion = session["emotion"]
        return {
            "user_id": user["user_id"],
            "scope": session["scope"],
            "affection": 100.0 if creator else user["score"] / 100,
            "relationship": relationship,
            "memory": json.loads(session["memories"]),
            "psychology": session["psychology"],
            "emotion": _emotion_default(emotion_style) if emotion is None else emotion,
            "creator": creator,
            "long_term_memory": session["long_term_memory"],
            "attitude": session["attitude"],
        }

    def list_status(
        self,
        *,
        limit: int = 100,
        creators: Collection[str] = (),
        emotion_style: str = "neutral",
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT users.user_id, score, relationship, scope, memories, psychology, emotion,
                   long_term_memory, attitude
                   FROM users JOIN sessions USING(user_id)
                   ORDER BY updated_at DESC, users.user_id, scope LIMIT ?""",
                (max(1, min(limit, 250)),),
            )
            return [
                self._status_row(row, row, row["user_id"] in creators, emotion_style)
                for row in rows
            ]

    def edit_status(
        self,
        user_id: str,
        scope: str,
        *,
        changes: Mapping[str, object],
        creator: bool = False,
        emotion_style: str = "neutral",
    ) -> dict:
        values = _validate_edit(changes, creator)
        with self._connect(write=True) as db:
            user, session = self._rows(db, user_id, scope)
            if user is None or session is None:
                raise KeyError((user_id, scope))
            if values:
                db.execute(
                    """UPDATE users SET score = COALESCE(?, score),
                       relationship = COALESCE(?, relationship) WHERE user_id = ?""",
                    (values.get("affection"), values.get("relationship"), user_id),
                )
                db.execute(
                    """UPDATE sessions SET memories = COALESCE(?, memories),
                       psychology = COALESCE(?, psychology), emotion = COALESCE(?, emotion),
                       long_term_memory = COALESCE(?, long_term_memory),
                       attitude = COALESCE(?, attitude),
                       updated_at = ? WHERE user_id = ? AND scope = ?""",
                    (
                        values.get("memory"),
                        values.get("psychology"),
                        values.get("emotion"),
                        values.get("long_term_memory"),
                        values.get("attitude"),
                        time.time(),
                        user_id,
                        scope,
                    ),
                )
                user, session = self._rows(db, user_id, scope)
            return self._status_row(user, session, creator, emotion_style)
