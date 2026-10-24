from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvaluationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def create(self, evaluation_id: str) -> None:
        now = _now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO evaluations "
                "(id, status, created_at, updated_at) VALUES (?, 'queued', ?, ?)",
                (evaluation_id, now, now),
            )

    def get(self, evaluation_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_running(self, evaluation_id: str) -> None:
        now = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE evaluations SET status = 'running', started_at = ?, "
                "updated_at = ?, error = NULL WHERE id = ?",
                (now, now, evaluation_id),
            )

    def mark_finished(self, evaluation_id: str, *, error: str | None = None) -> None:
        status = "failed" if error is not None else "succeeded"
        now = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE evaluations SET status = ?, finished_at = ?, "
                "updated_at = ?, error = ? WHERE id = ?",
                (status, now, now, error, evaluation_id),
            )

    def recover(self) -> list[str]:
        now = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE evaluations SET status = 'queued', started_at = NULL, "
                "updated_at = ? WHERE status = 'running'", (now,)
            )
            rows = db.execute(
                "SELECT id FROM evaluations WHERE status = 'queued' "
                "ORDER BY created_at"
            ).fetchall()
        return [row["id"] for row in rows]
