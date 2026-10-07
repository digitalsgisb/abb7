# ABB7 SQLite Recovery and Outbox

## What changed

`abb7.py` still performs its existing sensor, MQTT, Node-RED, InfluxDB and Google
Sheets work. SQLite adds local recovery and reliable future API delivery.

The default database file on the Raspberry Pi is:

```text
ABB 7 System/runtime/abb7_state.db
```

The database contains two application tables:

1. `runtime_state` — one current ABB7 snapshot used after a restart.
2. `outbox_events` — important events waiting for the Express API.

## What is saved

Product counters are saved immediately after every accepted sensor detection.
Shift, model, mode, reject and adjustment changes are also saved immediately.
Continuously changing timers are checkpointed every five seconds.

The shift snapshot also stores the Node-RED `overtime`, `workingTime`, and
calculated `scheduled_end_at` values. A restart therefore keeps the same
automatic shift-end schedule.

Important events placed in the outbox are:

- shift started or updated;
- model configured;
- hourly production finalized;
- reject recorded;
- downtime recorded;
- parameters recorded;
- manual count adjustment;
- shift ended.

## What happens after a restart

Before MQTT connects, Python reads `runtime_state` and restores the last shift,
model, counters and accumulated timers. The first MQTT snapshot therefore uses
the restored values instead of resetting the dashboard to zero.

Any outbox row left as `SENDING` is returned to `PENDING`, because the previous
delivery may have been interrupted.

## API settings

API sending is disabled by default. Events are still saved as `PENDING` while
the future Express service is unavailable.

The Raspberry Pi environment settings will be:

```text
ABB7_API_ENABLED=true
ABB7_API_EVENTS_URL=http://172.19.3.62:8088/api/v1/events
ABB7_API_KEY=replace-with-a-private-key
```

An optional database location can also be supplied:

```text
ABB7_SQLITE_PATH=/var/lib/abb7/abb7_state.db
```

Do not put the real API key in `abb7.py` or commit it to the project.

## View the current SQLite status

Run this on the Raspberry Pi from the ABB7 folder:

```text
python3 abb7_sqlite_status.py
```

It displays the current shift and counters plus the number of `PENDING`,
`SENDING`, `SENT` and `DEAD_LETTER` outbox events. It does not modify production
counters or send any data.

## Safety behavior

- API/network failure does not stop the sensor loop.
- Pending events remain on disk and retry later when API sending is enabled.
- Every event has a stable event ID, so a later Express API can prevent a retry
  from creating a duplicate PostgreSQL row.
- Hourly reset and the corresponding outbox event are committed together.
- Shift reset and the corresponding shift-end event are committed together.

## Automatic shift end and final hourly row

Python uses the `workingTime` received from Node-RED as the primary schedule:

| Shift | Overtime | Working time | Scheduled end |
|---|---|---|---|
| Day | No | 8:00 AM to 4:15 PM | 4:15 PM |
| Day | Yes | 8:00 AM to 8:00 PM | 8:00 PM |
| Night | No | 4:15 PM to 12:30 AM | 12:30 AM next day |
| Night | Yes | 8:00 PM to 8:00 AM | 8:00 AM next day |

When the scheduled end is reached, Python finalizes any remaining hourly values
before resetting the shift. Partial end times use the containing PRS hour:

- 4:15 PM goes to `16.00-17.00`;
- 12:30 AM goes to `0.00-1.00`;
- an exact 8:00 PM boundary goes to `19.00-20.00`;
- an exact 8:00 AM boundary goes to `7.00-8.00`.

The final `Hourly_Data` row is placed into the Google queue before the PDF
request. Apps Script therefore appends all pending rows first and generates the
PDF last.
