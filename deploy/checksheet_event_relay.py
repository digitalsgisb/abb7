#!/usr/bin/env python3
"""Deliver Pi events to Checksheet with an independent SQLite delivery ledger.

Reads the existing outbox, including events already delivered to the analytics
API. Never changes its status or replays machine commands. Standard library only.
"""
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from urllib import request
from urllib.parse import urlsplit


class EventRelay:
    def __init__(self, database_path, line, url, token, opener=request.urlopen):
        if line not in {"ABB2", "ABB3", "ABB7"}:
            raise ValueError("CHECKSHEET_LINE must be ABB2, ABB3 or ABB7")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Set a valid private HTTP or HTTPS Checksheet events URL")
        if not token:
            raise ValueError("CHECKSHEET_TOKEN is required")
        # mode=rw refuses to create an empty DB if the source path is wrong.
        self.db = sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("SELECT event_id, line_code, payload_json FROM outbox_events LIMIT 1")
        self.db.execute("""CREATE TABLE IF NOT EXISTS checksheet_deliveries (
            event_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, delivered_at REAL, last_error TEXT
        )""")
        self.db.commit()
        self.line, self.url, self.token, self.opener = line, url, token, opener

    def deliver_one(self, now=None):
        now = time.time() if now is None else now
        row = self.db.execute("""
            SELECT e.*, COALESCE(d.attempts,0) AS relay_attempts,
                   COALESCE(d.next_attempt,0) AS relay_next_attempt
            FROM outbox_events e LEFT JOIN checksheet_deliveries d ON d.event_id=e.event_id
            WHERE e.line_code=? AND d.delivered_at IS NULL
            ORDER BY e.rowid LIMIT 1
        """, (self.line,)).fetchone()
        if row is None or row["relay_next_attempt"] > now:
            return False
        envelope = {key: row[key] for key in ("event_id", "event_type", "line_code", "shift_id", "occurred_at")}
        try:
            envelope["payload"] = json.loads(row["payload_json"])
            req = request.Request(self.url, data=json.dumps(envelope).encode("utf-8"),
                                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token}, method="POST")
            # The response must explicitly acknowledge the envelope before delivery.
            with self.opener(req, timeout=5) as response:
                if not 200 <= response.status < 300 or json.loads(response.read(65536)).get("ok") is not True:
                    raise ValueError("Checksheet did not acknowledge the event")
            self.db.execute("""INSERT INTO checksheet_deliveries(event_id,attempts,delivered_at)
                VALUES(?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                attempts=excluded.attempts, delivered_at=excluded.delivered_at, last_error=NULL""",
                            (row["event_id"], row["relay_attempts"] + 1, now))
            self.db.commit()
            return True
        except Exception as exc:
            attempts = row["relay_attempts"] + 1
            delay = min(300, 2 ** min(attempts, 9))
            # Do not log endpoint URLs, credentials or event payloads.
            message = type(exc).__name__
            self.db.execute("""INSERT INTO checksheet_deliveries(event_id,attempts,next_attempt,last_error)
                VALUES(?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                attempts=excluded.attempts, next_attempt=excluded.next_attempt, last_error=excluded.last_error""",
                            (row["event_id"], attempts, now + delay, message))
            self.db.commit()
            logging.warning("%s event %s delivery failed (%s); retry in %ss", self.line, row["event_id"], message, delay)
            return False

    def close(self):
        self.db.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    relay = EventRelay(os.environ["CHECKSHEET_SQLITE_PATH"], os.environ["CHECKSHEET_LINE"],
                       os.environ["CHECKSHEET_EVENTS_URL"], os.environ["CHECKSHEET_TOKEN"])
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.info("%s Checksheet event relay started", relay.line)
    try:
        while not stop.is_set():
            if not relay.deliver_one():
                stop.wait(2)
    finally:
        relay.close()


if __name__ == "__main__":
    main()
