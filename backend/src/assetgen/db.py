"""SQLite storage.

Each table keeps the whole record as JSON in ``data`` and copies a few fields
into real columns so they can be filtered in SQL. One connection is shared by
the API and the worker thread, guarded by a lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

TABLES: dict[str, tuple[str, ...]] = {
    "projects": ("name",),
    "profiles": ("project_id", "name"),
    "loras": ("base_model", "name"),
    "assets": ("project_id", "kind", "status", "archetype", "training_candidate", "name", "queued_at"),
    # Side work the worker runs between asset stages: item fits on a hero, example renders.
    "jobs": ("kind", "status", "target_id", "queued_at"),
    # Saved combinations of a hero and the gear and weapon it wears.
    "outfits": ("project_id", "name"),
    # Generated skill animations (HY-Motion): clips any biped can play, by clip name.
    "motions": ("name", "status"),
    "sets": ("project_id", "name", "status"),
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        with self._lock:
            for table, columns in TABLES.items():
                cols = "".join(f", {c}" for c in columns)
                self._conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} ("
                    f"id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT{cols}, data TEXT NOT NULL)"
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row_values(self, table: str, record: dict) -> list:
        values = []
        for column in TABLES[table]:
            value = record.get(column)
            values.append(int(value) if isinstance(value, bool) else value)
        return values

    def insert(self, table: str, record: dict) -> dict:
        record = dict(record)
        record.setdefault("id", new_id())
        stamp = now()
        record.setdefault("created_at", stamp)
        record["updated_at"] = stamp
        columns = ("id", "created_at", "updated_at", *TABLES[table], "data")
        values = [record["id"], record["created_at"], record["updated_at"],
                  *self._row_values(table, record), json.dumps(record)]
        marks = ", ".join("?" for _ in columns)
        with self._lock:
            self._conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", values)
        return record

    def update(self, table: str, record_id: str, changes: dict) -> dict | None:
        with self._lock:
            record = self.get(table, record_id)
            if record is None:
                return None
            record.update(changes)
            record["updated_at"] = now()
            sets = ", ".join(f"{c} = ?" for c in ("updated_at", *TABLES[table], "data"))
            values = [record["updated_at"], *self._row_values(table, record), json.dumps(record), record_id]
            self._conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", values)
            return record

    def get(self, table: str, record_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(f"SELECT data FROM {table} WHERE id = ?", (record_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list(self, table: str, order: str = "created_at", **filters) -> list[dict]:
        where, values = [], []
        for column, value in filters.items():
            if value is None:
                continue
            if column not in TABLES[table]:
                raise ValueError(f"cannot filter {table} by {column}")
            where.append(f"{column} = ?")
            values.append(int(value) if isinstance(value, bool) else value)
        sql = f"SELECT data FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order}, id"
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def delete(self, table: str, record_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        return cur.rowcount > 0
