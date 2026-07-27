"""Durable ABB7 runtime state and API outbox.

This module deliberately uses only Python's standard library so it can run on
the Raspberry Pi without adding another package.  SQLite is used for two jobs:

1. ``runtime_state`` remembers the latest in-memory ABB7 state.
2. ``outbox_events`` remembers important events until the API confirms them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib import error, request


OUTBOX_PENDING = "PENDING"
OUTBOX_SENDING = "SENDING"
OUTBOX_SENT = "SENT"
OUTBOX_DEAD_LETTER = "DEAD_LETTER"


def utc_now_iso() -> str:
    """Return a timezone-aware UTC timestamp suitable for storage."""

    return datetime.now(timezone.utc).isoformat()


class ABB7SQLiteStore:
    """Thread-safe SQLite store shared by the main loop and sender thread."""

    def __init__(self, database_path: str) -> None:
        self.database_path = os.path.abspath(database_path)
        database_directory = os.path.dirname(self.database_path)
        if database_directory:
            os.makedirs(database_directory, exist_ok=True)

        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            timeout=5.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._configure_database()
        self._create_schema()
        self.recover_interrupted_deliveries()

    def _configure_database(self) -> None:
        with self._lock:
            # WAL allows reads and short writes to coexist. FULL asks SQLite to
            # wait for durable disk confirmation before reporting a commit.
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")

    def _create_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    line_code TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    state_status TEXT NOT NULL,
                    state_version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS outbox_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    line_code TEXT NOT NULL,
                    shift_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'DEAD_LETTER')),
                    attempt_count INTEGER NOT NULL DEFAULT 0
                        CHECK (attempt_count >= 0),
                    next_attempt_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    sent_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_outbox_delivery
                    ON outbox_events(status, next_attempt_at, created_at);

                CREATE INDEX IF NOT EXISTS idx_outbox_shift
                    ON outbox_events(shift_id, event_type);
                """
            )

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.execute("COMMIT")

    def _rollback(self) -> None:
        self._connection.execute("ROLLBACK")

    def save_runtime_state(
        self,
        line_code: str,
        state: Dict[str, Any],
        state_status: str,
    ) -> None:
        """Replace the one current snapshot for a production line."""

        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        now = utc_now_iso()
        with self._lock:
            self._begin()
            try:
                self._connection.execute(
                    """
                    INSERT INTO runtime_state (
                        line_code, state_json, state_status, state_version, updated_at
                    ) VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(line_code) DO UPDATE SET
                        state_json = excluded.state_json,
                        state_status = excluded.state_status,
                        state_version = runtime_state.state_version + 1,
                        updated_at = excluded.updated_at
                    """,
                    (line_code, state_json, state_status, now),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def load_runtime_state(self, line_code: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT state_json, state_status, state_version, updated_at
                FROM runtime_state
                WHERE line_code = ?
                """,
                (line_code,),
            ).fetchone()

        if row is None:
            return None

        state = json.loads(row["state_json"])
        state["_sqlite_state_status"] = row["state_status"]
        state["_sqlite_state_version"] = row["state_version"]
        state["_sqlite_updated_at"] = row["updated_at"]
        return state

    def _insert_event(
        self,
        event_type: str,
        line_code: str,
        shift_id: str,
        payload: Dict[str, Any],
        event_id: Optional[str],
        occurred_at: Optional[str],
    ) -> str:
        created_at = utc_now_iso()
        stable_event_id = event_id or str(uuid.uuid4())
        event_time = occurred_at or created_at
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self._connection.execute(
            """
            INSERT INTO outbox_events (
                event_id, event_type, line_code, shift_id, occurred_at,
                payload_json, status, attempt_count, next_attempt_at,
                last_error, created_at, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, NULL, ?, NULL)
            """,
            (
                stable_event_id,
                event_type,
                line_code,
                shift_id,
                event_time,
                payload_json,
                created_at,
                created_at,
            ),
        )
        return stable_event_id

    def enqueue_event(
        self,
        event_type: str,
        line_code: str,
        shift_id: str,
        payload: Dict[str, Any],
        event_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> str:
        """Durably add one important event to the delivery queue."""

        with self._lock:
            self._begin()
            try:
                stable_event_id = self._insert_event(
                    event_type,
                    line_code,
                    shift_id,
                    payload,
                    event_id,
                    occurred_at,
                )
                self._commit()
                return stable_event_id
            except Exception:
                self._rollback()
                raise

    def save_state_and_enqueue(
        self,
        line_code: str,
        state: Dict[str, Any],
        state_status: str,
        event_type: str,
        shift_id: str,
        payload: Dict[str, Any],
        event_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> str:
        """Save a state change and its event in one all-or-nothing transaction."""

        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        now = utc_now_iso()
        with self._lock:
            self._begin()
            try:
                self._connection.execute(
                    """
                    INSERT INTO runtime_state (
                        line_code, state_json, state_status, state_version, updated_at
                    ) VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(line_code) DO UPDATE SET
                        state_json = excluded.state_json,
                        state_status = excluded.state_status,
                        state_version = runtime_state.state_version + 1,
                        updated_at = excluded.updated_at
                    """,
                    (line_code, state_json, state_status, now),
                )
                stable_event_id = self._insert_event(
                    event_type,
                    line_code,
                    shift_id,
                    payload,
                    event_id,
                    occurred_at,
                )
                self._commit()
                return stable_event_id
            except Exception:
                self._rollback()
                raise

    def recover_interrupted_deliveries(self) -> int:
        """Return abandoned SENDING rows to PENDING after a restart."""

        now = utc_now_iso()
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE outbox_events
                SET status = 'PENDING',
                    next_attempt_at = ?,
                    last_error = CASE
                        WHEN last_error IS NULL THEN 'Delivery interrupted by restart'
                        ELSE last_error
                    END
                WHERE status = 'SENDING'
                """,
                (now,),
            )
            return cursor.rowcount

    def claim_next_event(self) -> Optional[Dict[str, Any]]:
        """Atomically reserve the oldest event whose retry time has arrived."""

        now = utc_now_iso()
        with self._lock:
            self._begin()
            try:
                row = self._connection.execute(
                    """
                    SELECT *
                    FROM outbox_events
                    WHERE status = 'PENDING'
                      AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    ORDER BY created_at, event_id
                    LIMIT 1
                    """,
                    (now,),
                ).fetchone()
                if row is None:
                    self._commit()
                    return None

                self._connection.execute(
                    """
                    UPDATE outbox_events
                    SET status = 'SENDING',
                        attempt_count = attempt_count + 1,
                        last_error = NULL
                    WHERE event_id = ?
                    """,
                    (row["event_id"],),
                )
                claimed = self._connection.execute(
                    "SELECT * FROM outbox_events WHERE event_id = ?",
                    (row["event_id"],),
                ).fetchone()
                self._commit()
            except Exception:
                self._rollback()
                raise

        return {
            "event_id": claimed["event_id"],
            "event_type": claimed["event_type"],
            "line_code": claimed["line_code"],
            "shift_id": claimed["shift_id"],
            "occurred_at": claimed["occurred_at"],
            "payload": json.loads(claimed["payload_json"]),
            "attempt_count": claimed["attempt_count"],
        }

    def mark_sent(self, event_id: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE outbox_events
                SET status = 'SENT', sent_at = ?, next_attempt_at = NULL,
                    last_error = NULL
                WHERE event_id = ?
                """,
                (utc_now_iso(), event_id),
            )

    def mark_retry(self, event_id: str, message: str, delay_seconds: int) -> None:
        next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        with self._lock:
            self._connection.execute(
                """
                UPDATE outbox_events
                SET status = 'PENDING', next_attempt_at = ?, last_error = ?
                WHERE event_id = ?
                """,
                (next_attempt.isoformat(), message[:1000], event_id),
            )

    def mark_dead_letter(self, event_id: str, message: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE outbox_events
                SET status = 'DEAD_LETTER', next_attempt_at = NULL,
                    last_error = ?
                WHERE event_id = ?
                """,
                (message[:1000], event_id),
            )

    def get_outbox_counts(self) -> Dict[str, int]:
        counts = {
            OUTBOX_PENDING: 0,
            OUTBOX_SENDING: 0,
            OUTBOX_SENT: 0,
            OUTBOX_DEAD_LETTER: 0,
        }
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS total FROM outbox_events GROUP BY status"
            ).fetchall()
        for row in rows:
            counts[row["status"]] = row["total"]
        return counts

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class ABB7OutboxSender(threading.Thread):
    """Background HTTP sender; it never blocks the sensor/main-loop thread."""

    def __init__(
        self,
        store: ABB7SQLiteStore,
        api_url: str,
        api_key: str,
        enabled: bool,
        poll_seconds: float = 2.0,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(name="abb7-api-outbox", daemon=True)
        self.store = store
        self.api_url = api_url.strip()
        self.api_key = api_key
        self.enabled = enabled and bool(self.api_url)
        self.poll_seconds = poll_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if not self.enabled:
            return

        while not self._stop_event.is_set():
            event_record = self.store.claim_next_event()
            if event_record is None:
                self._stop_event.wait(self.poll_seconds)
                continue

            event_id = event_record["event_id"]
            try:
                body = json.dumps(event_record, ensure_ascii=False).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                api_request = request.Request(
                    self.api_url,
                    data=body,
                    headers=headers,
                    method="POST",
                )
                with request.urlopen(
                    api_request,
                    timeout=self.request_timeout_seconds,
                ) as response:
                    status_code = response.getcode()
                    if 200 <= status_code < 300:
                        self.store.mark_sent(event_id)
                        print(f"[OUTBOX] Sent {event_record['event_type']} ({event_id}).")
                    else:
                        self._retry(event_record, f"HTTP {status_code}")
            except error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                message = f"HTTP {exc.code}: {error_body}".strip()
                if exc.code in (408, 425, 429) or exc.code >= 500:
                    self._retry(event_record, message)
                else:
                    self.store.mark_dead_letter(event_id, message)
                    print(f"[OUTBOX] Rejected permanently: {message}")
            except Exception as exc:
                self._retry(event_record, str(exc))

    def _retry(self, event_record: Dict[str, Any], message: str) -> None:
        attempts = int(event_record.get("attempt_count", 1))
        delay_seconds = min(300, 2 ** min(attempts, 8))
        self.store.mark_retry(event_record["event_id"], message, delay_seconds)
        print(
            f"[OUTBOX] Delivery failed; retrying in {delay_seconds}s: {message}"
        )

