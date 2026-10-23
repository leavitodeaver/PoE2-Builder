"""Saved build requests in a local SQLite file (data/requests.sqlite)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from builder.data import DATA_ROOT
from builder.request.schema import BuildRequest


class RequestStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_ROOT / "requests.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, updated_at REAL NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def list(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT id, name, updated_at FROM requests ORDER BY updated_at DESC").fetchall()
        return [{"id": r[0], "name": r[1], "updated_at": r[2]} for r in rows]

    def get(self, request_id: int) -> BuildRequest | None:
        with self._connect() as db:
            row = db.execute("SELECT body FROM requests WHERE id = ?", (request_id,)).fetchone()
        return BuildRequest.model_validate_json(row[0]) if row else None

    def save(self, request: BuildRequest, request_id: int | None = None) -> int:
        body = request.model_dump_json()
        with self._connect() as db:
            if request_id is not None:
                db.execute("UPDATE requests SET name = ?, body = ?, updated_at = ? WHERE id = ?",
                           (request.name, body, time.time(), request_id))
                return request_id
            cursor = db.execute("INSERT INTO requests (name, body, updated_at) VALUES (?, ?, ?)",
                                (request.name, body, time.time()))
            return cursor.lastrowid

    def delete(self, request_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM requests WHERE id = ?", (request_id,))
