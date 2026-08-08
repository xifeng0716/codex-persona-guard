"""Thread-safe SQLite persistence for Persona Guard."""

import json
import os
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .defaults import DEFAULT_DETECTOR_PROMPT
from .state_machine import GuardSnapshot


def state_db_path(environ: Optional[dict] = None) -> Path:
    """Return the confirmed XDG/HOME location for the live database."""

    variables = os.environ if environ is None else environ
    state_home = variables.get("XDG_STATE_HOME")
    if not state_home:
        home = variables.get("HOME")
        if not home:
            home = os.path.expanduser("~")
        state_home = os.path.join(home, ".local", "state")
    return Path(state_home) / "persona-guard" / "guard.db"


def normalize_cwd(cwd: str) -> str:
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd must be a non-empty string")
    return os.path.normpath(os.path.abspath(cwd))


@dataclass(frozen=True)
class Binding:
    id: int
    name: str
    target_type: str
    target_value: str
    enabled: bool
    reminder: str
    created_at: float
    updated_at: float

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "enabled": self.enabled,
            "reminder": self.reminder,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def __getitem__(self, key: str):
        return self.as_dict()[key]


def _binding_from_row(row: sqlite3.Row) -> Binding:
    return Binding(
        id=int(row["id"]),
        name=row["name"],
        target_type=row["target_type"],
        target_value=row["target_value"],
        enabled=bool(row["enabled"]),
        reminder=row["reminder"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str, fallback: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class Storage:
    """A single SQLite connection protected for use by threaded handlers."""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self.path = Path(path) if path is not None else state_db_path()
        self._lock = threading.RLock()
        self._memory = str(self.path) == ":memory:"
        if not self._memory:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.path.parent, stat.S_IRWXU)
        self._connection = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()
        if not self._memory and self.path.exists():
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    target_type TEXT NOT NULL CHECK(target_type IN ('thread', 'workspace')),
                    target_value TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    reminder TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(target_type, target_value)
                );
                CREATE TABLE IF NOT EXISTS guard_states (
                    session_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    hot_remaining INTEGER NOT NULL,
                    clean_none_streak INTEGER NOT NULL,
                    previous_watch_type TEXT,
                    recent_hit_type TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discoveries (
                    session_id TEXT PRIMARY KEY,
                    cwd TEXT NOT NULL,
                    last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    current_prompt TEXT NOT NULL,
                    policy_text TEXT NOT NULL,
                    policy_revision INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    decision_type TEXT,
                    error_category TEXT,
                    state_before_json TEXT NOT NULL,
                    state_after_json TEXT NOT NULL,
                    binding_id INTEGER,
                    binding_snapshot_json TEXT,
                    injected INTEGER NOT NULL CHECK(injected IN (0, 1)),
                    model TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_created_idx ON records(id DESC);
                CREATE INDEX IF NOT EXISTS records_binding_idx ON records(binding_id, id DESC);
                """
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES('enabled', '1')"
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES('policy_text', ?)",
                (DEFAULT_DETECTOR_PROMPT,),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES('policy_revision', '1')"
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]

    def is_enabled(self) -> bool:
        with self._lock:
            return self._setting("enabled", "1") == "1"

    def set_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        with self._lock:
            self._connection.execute(
                "INSERT INTO settings(key, value) VALUES('enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if enabled else "0",),
            )
            self._connection.commit()

    def get_policy(self) -> dict:
        with self._lock:
            return {
                "text": self._setting("policy_text", DEFAULT_DETECTOR_PROMPT),
                "revision": int(self._setting("policy_revision", "1")),
            }

    def update_policy(self, text: str) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("policy text cannot be empty")
        with self._lock:
            revision = int(self._setting("policy_revision", "1")) + 1
            self._connection.execute(
                "INSERT INTO settings(key, value) VALUES('policy_text', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (text,),
            )
            self._connection.execute(
                "INSERT INTO settings(key, value) VALUES('policy_revision', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(revision),),
            )
            self._connection.execute(
                "UPDATE guard_states SET state = 'NORMAL', hot_remaining = 0, "
                "clean_none_streak = 0, previous_watch_type = NULL, "
                "recent_hit_type = NULL, updated_at = ?",
                (time.time(),),
            )
            self._connection.commit()
            return {"text": text, "revision": revision}

    def get_state(self, session_id: str) -> GuardSnapshot:
        with self._lock:
            row = self._connection.execute(
                "SELECT state, hot_remaining, clean_none_streak, previous_watch_type, "
                "recent_hit_type FROM guard_states WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return GuardSnapshot.normal()
            return GuardSnapshot(
                state=row["state"],
                hot_remaining=int(row["hot_remaining"]),
                clean_none_streak=int(row["clean_none_streak"]),
                previous_watch_type=row["previous_watch_type"],
                recent_hit_type=row["recent_hit_type"],
            )

    def save_state(self, session_id: str, snapshot: GuardSnapshot) -> None:
        if not session_id:
            raise ValueError("session_id cannot be empty")
        if not isinstance(snapshot, GuardSnapshot):
            raise TypeError("snapshot must be a GuardSnapshot")
        with self._lock:
            self._connection.execute(
                "INSERT INTO guard_states(session_id, state, hot_remaining, clean_none_streak, "
                "previous_watch_type, recent_hit_type, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET state = excluded.state, "
                "hot_remaining = excluded.hot_remaining, clean_none_streak = excluded.clean_none_streak, "
                "previous_watch_type = excluded.previous_watch_type, "
                "recent_hit_type = excluded.recent_hit_type, updated_at = excluded.updated_at",
                (
                    session_id,
                    snapshot.state,
                    snapshot.hot_remaining,
                    snapshot.clean_none_streak,
                    snapshot.previous_watch_type,
                    snapshot.recent_hit_type,
                    time.time(),
                ),
            )
            self._connection.commit()

    def reset_states(self) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE guard_states SET state = 'NORMAL', hot_remaining = 0, "
                "clean_none_streak = 0, previous_watch_type = NULL, "
                "recent_hit_type = NULL, updated_at = ?",
                (time.time(),),
            )
            self._connection.commit()

    def delete_state(self, session_id: str) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM guard_states WHERE session_id = ?", (session_id,))
            self._connection.commit()

    def upsert_discovery(self, session_id: str, cwd: str, last_seen: Optional[float] = None) -> None:
        if not session_id:
            raise ValueError("session_id cannot be empty")
        normalized = normalize_cwd(cwd)
        seen = time.time() if last_seen is None else float(last_seen)
        with self._lock:
            self._connection.execute(
                "INSERT INTO discoveries(session_id, cwd, last_seen) VALUES(?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET cwd = excluded.cwd, last_seen = excluded.last_seen",
                (session_id, normalized, seen),
            )
            self._connection.commit()

    def list_discoveries(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT session_id, cwd, last_seen FROM discoveries ORDER BY last_seen DESC, session_id"
            ).fetchall()
            return [
                {"session_id": row["session_id"], "cwd": row["cwd"], "last_seen": row["last_seen"]}
                for row in rows
            ]

    def list_workspaces(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT cwd FROM discoveries ORDER BY cwd"
            ).fetchall()
            return [row["cwd"] for row in rows]

    def create_binding(
        self,
        name: str,
        target_type: str,
        target_value: str,
        enabled: bool,
        reminder: str,
    ) -> Binding:
        self._validate_binding_values(name, target_type, target_value, enabled, reminder)
        target_value = self._normalize_target(target_type, target_value)
        now = time.time()
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO bindings(name, target_type, target_value, enabled, reminder, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (name, target_type, target_value, int(enabled), reminder, now, now),
            )
            self._connection.commit()
            return self.get_binding(int(cursor.lastrowid))

    def get_binding(self, binding_id: int) -> Binding:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, name, target_type, target_value, enabled, reminder, created_at, updated_at "
                "FROM bindings WHERE id = ?",
                (int(binding_id),),
            ).fetchone()
            if row is None:
                raise KeyError("binding not found")
            return _binding_from_row(row)

    def list_bindings(self) -> list[Binding]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, name, target_type, target_value, enabled, reminder, created_at, updated_at "
                "FROM bindings ORDER BY id"
            ).fetchall()
            return [_binding_from_row(row) for row in rows]

    def update_binding(self, binding_id: int, **changes: Any) -> Binding:
        allowed = {"name", "target_type", "target_value", "enabled", "reminder"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError("unknown binding fields")
        current = self.get_binding(binding_id)
        values = current.as_dict()
        values.update(changes)
        self._validate_binding_values(
            values["name"],
            values["target_type"],
            values["target_value"],
            values["enabled"],
            values["reminder"],
        )
        target_value = self._normalize_target(values["target_type"], values["target_value"])
        now = time.time()
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE bindings SET name = ?, target_type = ?, target_value = ?, enabled = ?, "
                "reminder = ?, updated_at = ? WHERE id = ?",
                (
                    values["name"],
                    values["target_type"],
                    target_value,
                    int(values["enabled"]),
                    values["reminder"],
                    now,
                    int(binding_id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("binding not found")
            self._connection.commit()
            return self.get_binding(binding_id)

    def delete_binding(self, binding_id: int) -> None:
        binding = self.get_binding(binding_id)
        with self._lock:
            self._connection.execute("DELETE FROM bindings WHERE id = ?", (binding.id,))
            if binding.target_type == "thread":
                self._connection.execute(
                    "DELETE FROM guard_states WHERE session_id = ?", (binding.target_value,)
                )
            self._connection.commit()

    def find_binding(self, session_id: str, cwd: str) -> Optional[Binding]:
        normalized = normalize_cwd(cwd)
        with self._lock:
            row = self._connection.execute(
                "SELECT id, name, target_type, target_value, enabled, reminder, created_at, updated_at "
                "FROM bindings WHERE target_type = 'thread' AND target_value = ?",
                (session_id,),
            ).fetchone()
            if row is not None:
                return _binding_from_row(row)
            row = self._connection.execute(
                "SELECT id, name, target_type, target_value, enabled, reminder, created_at, updated_at "
                "FROM bindings WHERE target_type = 'workspace' AND target_value = ?",
                (normalized,),
            ).fetchone()
            return None if row is None else _binding_from_row(row)

    @staticmethod
    def _validate_binding_values(
        name: str,
        target_type: str,
        target_value: str,
        enabled: bool,
        reminder: str,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("binding name cannot be empty")
        if target_type not in {"thread", "workspace"}:
            raise ValueError("target_type must be thread or workspace")
        if not isinstance(target_value, str) or not target_value:
            raise ValueError("target_value cannot be empty")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        if not isinstance(reminder, str) or not reminder:
            raise ValueError("reminder cannot be empty")

    @staticmethod
    def _normalize_target(target_type: str, target_value: str) -> str:
        return normalize_cwd(target_value) if target_type == "workspace" else target_value

    def record_attempt(
        self,
        *,
        session_id: str,
        history: Iterable[dict],
        current_prompt: str,
        policy_text: str,
        policy_revision: int,
        result: str,
        decision_type: Optional[str],
        error_category: Optional[str],
        state_before: GuardSnapshot,
        state_after: GuardSnapshot,
        binding: Optional[Binding],
        injected: bool,
        model: str,
        latency_ms: float,
        created_at: Optional[float] = None,
    ) -> int:
        if not isinstance(current_prompt, str):
            current_prompt = ""
        if not isinstance(policy_text, str):
            raise ValueError("policy_text must be a string")
        if not isinstance(state_before, GuardSnapshot) or not isinstance(state_after, GuardSnapshot):
            raise TypeError("record states must be GuardSnapshot values")
        history_value = list(history)
        timestamp = time.time() if created_at is None else float(created_at)
        binding_id = None if binding is None else binding.id
        snapshot = None if binding is None else binding.as_dict()
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO records(session_id, history_json, current_prompt, policy_text, policy_revision, "
                "result, decision_type, error_category, state_before_json, state_after_json, binding_id, "
                "binding_snapshot_json, injected, model, latency_ms, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    _json(history_value),
                    current_prompt,
                    policy_text,
                    int(policy_revision),
                    result,
                    decision_type,
                    error_category,
                    _json(state_before.as_dict()),
                    _json(state_after.as_dict()),
                    binding_id,
                    None if snapshot is None else _json(snapshot),
                    int(bool(injected)),
                    model,
                    float(latency_ms),
                    timestamp,
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def list_records(
        self,
        *,
        binding_id: Optional[int] = None,
        result: Optional[str] = None,
        limit: int = 50,
        before_id: Optional[int] = None,
    ) -> list[dict]:
        if result is not None and result not in {"HIT", "WATCH", "NONE", "ERROR"}:
            raise ValueError("invalid record result")
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        limit = min(limit, 200)
        clauses = []
        parameters: list[Any] = []
        if binding_id is not None:
            clauses.append("binding_id = ?")
            parameters.append(int(binding_id))
        if result is not None:
            clauses.append("result = ?")
            parameters.append(result)
        if before_id is not None:
            clauses.append("id < ?")
            parameters.append(int(before_id))
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, session_id, history_json, current_prompt, policy_text, policy_revision, "
                "result, decision_type, error_category, state_before_json, state_after_json, binding_id, "
                "binding_snapshot_json, injected, model, latency_ms, created_at "
                "FROM records" + where + " ORDER BY id DESC LIMIT ?",
                parameters,
            ).fetchall()
            records = []
            for row in rows:
                history = _load_json(row["history_json"], [])
                state_before = _load_json(row["state_before_json"], {})
                state_after = _load_json(row["state_after_json"], {})
                binding_snapshot = (
                    None
                    if row["binding_snapshot_json"] is None
                    else _load_json(row["binding_snapshot_json"], None)
                )
                records.append(
                    {
                        "id": int(row["id"]),
                        "session_id": row["session_id"],
                        "history": history,
                        "current_prompt": row["current_prompt"],
                        "detector_history": history,
                        "latest_user_prompt": row["current_prompt"],
                        "policy": {"text": row["policy_text"], "revision": int(row["policy_revision"])},
                        "policy_text": row["policy_text"],
                        "policy_revision": int(row["policy_revision"]),
                        "result": row["result"],
                        "type": row["decision_type"],
                        "decision_type": row["decision_type"],
                        "error_category": row["error_category"],
                        "state_before": state_before,
                        "state_after": state_after,
                        "binding_id": row["binding_id"],
                        "binding": binding_snapshot,
                        "binding_snapshot": binding_snapshot,
                        "injected": bool(row["injected"]),
                        "model": row["model"],
                        "latency_ms": row["latency_ms"],
                        "created_at": row["created_at"],
                    }
                )
            return records

    def clear_records(self, binding_id: Optional[int] = None) -> None:
        with self._lock:
            if binding_id is None:
                self._connection.execute("DELETE FROM records")
            else:
                self._connection.execute("DELETE FROM records WHERE binding_id = ?", (int(binding_id),))
            self._connection.commit()

    def count_bindings(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT COUNT(*) FROM bindings").fetchone()[0])

    def count_records(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])
