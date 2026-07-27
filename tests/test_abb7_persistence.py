import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ABB7_DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ABB7_DIRECTORY not in sys.path:
    sys.path.insert(0, ABB7_DIRECTORY)

from abb7_persistence import ABB7OutboxSender, ABB7SQLiteStore


class ABB7PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(
            self.temporary_directory.name,
            "abb7-test.db",
        )
        self.store = ABB7SQLiteStore(self.database_path)

    def tearDown(self):
        if self.store is not None:
            self.store.close()
        self.temporary_directory.cleanup()

    def test_schema_contains_only_two_application_tables(self):
        connection = sqlite3.connect(self.database_path)
        try:
            rows = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            [row[0] for row in rows],
            ["outbox_events", "runtime_state"],
        )

    def test_runtime_state_survives_reopen(self):
        state = {
            "current_shift": {"shift_id": "20260722-Day-Line1"},
            "counters": {"hourly_output": 73, "shift_total_output": 428},
        }
        self.store.save_runtime_state("ABB7", state, "ACTIVE")
        self.store.close()
        self.store = ABB7SQLiteStore(self.database_path)

        restored = self.store.load_runtime_state("ABB7")

        self.assertEqual(restored["current_shift"]["shift_id"], "20260722-Day-Line1")
        self.assertEqual(restored["counters"]["hourly_output"], 73)
        self.assertEqual(restored["_sqlite_state_status"], "ACTIVE")

    def test_state_and_event_roll_back_together(self):
        original_state = {"counters": {"hourly_output": 10}}
        first_event_id = self.store.save_state_and_enqueue(
            line_code="ABB7",
            state=original_state,
            state_status="ACTIVE",
            event_type="hourly.finalized",
            shift_id="shift-1",
            payload={"actual": 10},
            event_id="fixed-event-id",
        )
        self.assertEqual(first_event_id, "fixed-event-id")

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_state_and_enqueue(
                line_code="ABB7",
                state={"counters": {"hourly_output": 999}},
                state_status="ACTIVE",
                event_type="hourly.finalized",
                shift_id="shift-1",
                payload={"actual": 999},
                event_id="fixed-event-id",
            )

        restored = self.store.load_runtime_state("ABB7")
        self.assertEqual(restored["counters"]["hourly_output"], 10)

    def test_interrupted_sending_event_returns_to_pending(self):
        self.store.enqueue_event(
            "reject.recorded",
            "ABB7",
            "shift-1",
            {"ng_quantity": 2},
            event_id="event-recovery",
        )
        claimed = self.store.claim_next_event()
        self.assertEqual(claimed["event_id"], "event-recovery")
        self.assertEqual(self.store.get_outbox_counts()["SENDING"], 1)

        self.store.close()
        self.store = ABB7SQLiteStore(self.database_path)

        counts = self.store.get_outbox_counts()
        self.assertEqual(counts["PENDING"], 1)
        self.assertEqual(counts["SENDING"], 0)

    def test_background_sender_marks_successful_event_sent(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", "0"))
                received.append(json.loads(self.rfile.read(content_length)))
                self.send_response(201)
                self.end_headers()

            def log_message(self, format_string, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        self.store.enqueue_event(
            "downtime.recorded",
            "ABB7",
            "shift-1",
            {"duration_minutes": 12},
            event_id="event-http",
        )
        sender = ABB7OutboxSender(
            self.store,
            f"http://127.0.0.1:{server.server_port}/api/v1/events",
            "test-key",
            enabled=True,
            poll_seconds=0.05,
            request_timeout_seconds=1.0,
        )
        sender.start()
        try:
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if self.store.get_outbox_counts()["SENT"] == 1:
                    break
                time.sleep(0.05)
        finally:
            sender.stop()
            sender.join(timeout=2.0)
            server.shutdown()
            server.server_close()

        self.assertEqual(self.store.get_outbox_counts()["SENT"], 1)
        self.assertEqual(received[0]["event_id"], "event-http")
        self.assertEqual(received[0]["event_type"], "downtime.recorded")


if __name__ == "__main__":
    unittest.main()

