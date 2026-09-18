"""Persistence.

Local mode uses SQLite with a single-table, DynamoDB-shaped access pattern
(partition key + sort key + JSON document) so the query surface we exercise
locally is the same one the DynamoDB table exposes in AWS mode. That keeps
the migration honest: no local-only queries that DynamoDB could not serve.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from .config import settings
from .models import Incident


class IncidentStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or settings.db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    pk   TEXT NOT NULL,   -- e.g. JOB#<job>
                    sk   TEXT NOT NULL,   -- e.g. INCIDENT#<opened_at>#<id>
                    doc  TEXT NOT NULL,
                    PRIMARY KEY (pk, sk)
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sk ON items(sk)")

    # --- incident access patterns -------------------------------------------
    def put_incident(self, incident: Incident) -> None:
        pk = f"JOB#{incident.job}"
        sk = f"INCIDENT#{incident.opened_at:.6f}#{incident.id}"
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO items (pk, sk, doc) VALUES (?, ?, ?)",
                (pk, sk, incident.model_dump_json()),
            )

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        with self._lock:
            row = self._conn.execute(
                "SELECT doc FROM items WHERE sk LIKE ?", (f"INCIDENT#%#{incident_id}",)
            ).fetchone()
        return Incident.model_validate_json(row["doc"]) if row else None

    def list_incidents(self, limit: int = 100) -> list[Incident]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc FROM items WHERE sk LIKE 'INCIDENT#%' ORDER BY sk DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Incident.model_validate_json(r["doc"]) for r in rows]

    def list_by_job(self, job: str, limit: int = 50) -> list[Incident]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc FROM items WHERE pk = ? AND sk LIKE 'INCIDENT#%' ORDER BY sk DESC LIMIT ?",
                (f"JOB#{job}", limit),
            ).fetchall()
        return [Incident.model_validate_json(r["doc"]) for r in rows]

    def resolved_incidents(self) -> list[Incident]:
        """Past, resolved incidents used as the RAG corpus."""
        return [i for i in self.list_incidents(limit=500) if i.status == "RESOLVED" and i.diagnosis]

    def close(self) -> None:
        self._conn.close()


store = IncidentStore()
