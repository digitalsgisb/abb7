#!/usr/bin/python3
"""Read-only summary of the ABB7 SQLite recovery database."""

import os
import json
import sqlite3
import sys


LINE_CODE = "ABB7"
SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.environ.get(
    "ABB7_SQLITE_PATH",
    os.path.join(SCRIPT_DIRECTORY, "runtime", "abb7_state.db"),
)


def main():
    if not os.path.exists(DATABASE_PATH):
        print(f"No ABB7 SQLite database exists yet: {DATABASE_PATH}")
        print("Run abb7.py once to create its first recovery snapshot.")
        return 1

    database_uri = f"file:{os.path.abspath(DATABASE_PATH)}?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        state_row = connection.execute(
            """
            SELECT state_json, state_status, updated_at
            FROM runtime_state
            WHERE line_code = ?
            """,
            (LINE_CODE,),
        ).fetchone()
        count_rows = connection.execute(
            "SELECT status, COUNT(*) AS total FROM outbox_events GROUP BY status"
        ).fetchall()
        counts = {row["status"]: row["total"] for row in count_rows}

        print("ABB7 SQLite status")
        print(f"Database: {DATABASE_PATH}")
        if state_row is None:
            print("Runtime state: no ABB7 snapshot")
        else:
            state = json.loads(state_row["state_json"])
            shift = state.get("current_shift", {})
            counters = state.get("counters", {})
            print(f"Snapshot time: {state_row['updated_at']}")
            print(f"State status: {state_row['state_status']}")
            print(f"Shift: {shift.get('shift_id', 'NO PROD')}")
            print(f"Model: {shift.get('model', 'NO PROD')}")
            print(f"Lot: {shift.get('lot_number', 'NO PROD')}")
            print(f"Hourly output: {counters.get('hourly_output', 0)}")
            print(f"Model output: {counters.get('total_output', 0)}")
            print(f"Shift output: {counters.get('shift_total_output', 0)}")
            print(f"Reject total: {counters.get('total_rejects', 0)}")
            print(f"Scheduled reset: {shift.get('scheduled_end_at')}")
            pending = state.get("pending_shift_form")
            print(f"Pending shift: {pending.get('shift') if isinstance(pending, dict) else 'None'}")
            if isinstance(pending, dict):
                print(f"Pending date/hours: {pending.get('prodDate')} / {pending.get('workingTime')}")

        print("Outbox events:")
        for status in ("PENDING", "SENDING", "SENT", "DEAD_LETTER"):
            print(f"  {status}: {counts.get(status, 0)}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
