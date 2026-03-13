from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.config import config


class ModelSqliteStore:
    def __init__(self, db_path: Path | None = None):
        root = Path(config.root_path)
        self._db_path = db_path or (root / "logs" / "bff" / "models.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_models (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = conn.execute("PRAGMA table_info(workspace_models)").fetchall()
            column_names = {str(row[1]) for row in columns}
            if "api_key" not in column_names:
                conn.execute(
                    "ALTER TABLE workspace_models ADD COLUMN api_key TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.commit()

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "provider": row["provider"],
            "baseUrl": row["base_url"],
            "apiKey": row["api_key"],
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_models(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, provider, base_url, api_key, enabled, created_at, updated_at
                FROM workspace_models
                ORDER BY datetime(updated_at) DESC, rowid DESC
                """
            ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def create_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspace_models (id, name, provider, base_url, api_key, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["name"],
                    payload["provider"],
                    payload["baseUrl"],
                    payload["apiKey"],
                    1 if payload["enabled"] else 0,
                    payload["createdAt"],
                    payload["updatedAt"],
                ),
            )
            conn.commit()
        return payload

    def update_model(self, model_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workspace_models
                SET name = ?, provider = ?, base_url = ?, api_key = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["name"],
                    payload["provider"],
                    payload["baseUrl"],
                    payload["apiKey"],
                    1 if payload["enabled"] else 0,
                    payload["updatedAt"],
                    model_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                """
                SELECT id, name, provider, base_url, api_key, enabled, created_at, updated_at
                FROM workspace_models
                WHERE id = ?
                """,
                (model_id,),
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return self._row_to_model(row)

    def delete_model(self, model_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM workspace_models WHERE id = ?", (model_id,))
            conn.execute(
                "DELETE FROM workspace_settings WHERE key = 'active_model_id' AND value = ?",
                (model_id,),
            )
            conn.commit()
        return cursor.rowcount > 0

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, provider, base_url, api_key, enabled, created_at, updated_at
                FROM workspace_models
                WHERE id = ?
                """,
                (model_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_model(row)

    def get_active_model_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM workspace_settings WHERE key = 'active_model_id'"
            ).fetchone()
        if row is None:
            return None
        return row["value"]

    def set_active_model_id(self, model_id: str | None) -> None:
        with self._connect() as conn:
            if model_id is None:
                conn.execute("DELETE FROM workspace_settings WHERE key = 'active_model_id'")
            else:
                conn.execute(
                    """
                    INSERT INTO workspace_settings (key, value)
                    VALUES ('active_model_id', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (model_id,),
                )
            conn.commit()
