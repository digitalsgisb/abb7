# ABB7 Production Tracker

ABB7 is a Raspberry Pi production-tracking service for the ABB 7 line. It reads
the production sensor, exchanges commands and live values with Node-RED over
MQTT, writes production records to Google Sheets, keeps restart-safe state in
SQLite, and forwards durable events to the central production data platform on
the AI TOP ATOM PC.

## How it works

```text
Production sensor (GPIO 17)
  -> abb7.py
  -> local MQTT broker (Node-RED controls and live dashboard)
  -> Google Apps Script (production checksheet and PDF)
  -> local SQLite state and event outbox
  -> HTTP POST /api/v1/events
  -> AI TOP ATOM ingest API
  -> PostgreSQL ingest.events, identified by line_code = ABB7
```

The SQLite outbox is important: an Atom/network outage does not discard queued
events. The sender retries them after connectivity returns. On restart, the
latest saved production state is restored while sensor counting remains safely
blocked until the sensor is observed clear.

## Repository contents

- `abb7.py` — sensor loop, production state, MQTT, Google queue, and event creation.
- `abb7_persistence.py` — SQLite recovery state and durable HTTP outbox.
- `abb7_shift_schedule.py` — shift-end and time-slot calculations.
- `abb7_sqlite_status.py` — read-only SQLite health/status command.
- `abb7appscript.js` — Google Apps Script checksheet and PDF logic.
- `deploy/abb7@.service` — templated systemd service for Raspberry Pi OS.
- `tests/` — Python and Apps Script regression tests.
- `SQLITE_RECOVERY_GUIDE.md` — recovery/outbox details and manual recovery steps.

The local Excel workbook and Node-RED export are intentionally excluded from
Git. The Node-RED export currently contains populated secret fields. Do not use
`git add -f` on it. Transfer it privately or export a sanitized version first.

## Late shift entry (30-minute window)

After an automatic shift end, the 08:00 production-day reset, or a manual
Node-RED End Shift command, the service buffers machine timers and sensor counts
for 30 minutes while waiting for the next shift form. Submitting the form during
that window assigns the buffered production to the new shift without resetting
its counters. For example, a reset at 08:00 followed by entry at 08:02 keeps the
two minutes already recorded, together with any detected products.

The window ends exactly 30 minutes after the reset (08:30 in this example).
If no form arrives before then, unassigned timers and counts are discarded and
tracking waits for a new form. The service must be running to record machine
activity; this does not reconstruct sensor activity during a power outage.

## Runtime assumptions

- Raspberry Pi OS with Python 3.
- The production sensor uses BCM GPIO pin `17`.
- Mosquitto is available at `localhost:1883`.
- Node-RED and InfluxDB run locally when their dashboard/history features are used.
- The Atom ingestion endpoint is `http://172.19.3.62:8088/api/v1/events`.
- The Atom health endpoint is `http://172.19.3.62:8088/health`.

Most MQTT command topics are intentionally local and generic. This is safe when
each Raspberry Pi runs its own broker. Namespace the topics before using a
single shared broker for multiple ABB systems.

## First installation on the Raspberry Pi

Log in as the normal service user. The systemd unit expects the repository at
`/home/<user>/abb7`.

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip mosquitto
sudo systemctl enable --now mosquitto

cd "$HOME"
git clone https://github.com/digitalsgisb/abb7.git abb7
cd abb7

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

If Node-RED and InfluxDB are needed, install and configure them separately.
Import the private ABB7 Node-RED flow only after checking its API key, Google
URLs, InfluxDB buckets, and MQTT broker settings.

## Configure the private environment

Create a local `.env`; it is ignored by Git and must never be committed:

```bash
cd "$HOME/abb7"
touch .env
chmod 600 .env
nano .env
```

Add:

```env
ABB7_API_ENABLED=true
ABB7_API_EVENTS_URL=http://172.19.3.62:8088/api/v1/events
ABB7_API_KEY=<same private value as INGEST_API_KEY on the Atom PC>
ABB7_SQLITE_PATH=/var/lib/abb7/abb7_state.db
```

Create the persistent runtime directory:

```bash
sudo install -d -m 0750 -o "$USER" -g "$USER" /var/lib/abb7
```

The Google Apps Script web-app URL is currently configured as `WEB_APP_URL` in
`abb7.py`. When using a different spreadsheet deployment, update that URL and
confirm that `abb7appscript.js` is installed in the matching Google Sheet.

## Install and start the service

```bash
cd "$HOME/abb7"
sudo install -m 0644 deploy/abb7@.service /etc/systemd/system/abb7@.service
sudo systemctl daemon-reload
sudo systemctl enable --now "abb7@$USER.service"
```

Check it:

```bash
systemctl status "abb7@$USER.service" --no-pager
journalctl -u "abb7@$USER.service" -n 100 --no-pager
```

## Verify the complete data path

Check Atom connectivity from the Raspberry Pi:

```bash
curl --fail --silent --show-error http://172.19.3.62:8088/health
```

Check the local MQTT status topic:

```bash
mosquitto_sub -h localhost -t 'abb7/status' -v
```

Check SQLite state and pending events:

```bash
cd "$HOME/abb7"
.venv/bin/python abb7_sqlite_status.py
```

On the Atom PC, verify stored ABB7 events:

```bash
cd /srv/apps/production-data
docker compose exec postgres \
  psql -U production_admin -d production_analytics \
  -c "SELECT line_code, event_type, occurred_at, received_at
      FROM ingest.events
      WHERE line_code = 'ABB7'
      ORDER BY received_at DESC
      LIMIT 20;"
```

## Pull an update on the Raspberry Pi

The `.env` and `/var/lib/abb7` data remain untouched because they are not
tracked by Git.

```bash
cd "$HOME/abb7"
git status
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart "abb7@$USER.service"
systemctl status "abb7@$USER.service" --no-pager
```

If `git status` shows local source-code changes, stop and review them before
pulling. Do not reset or discard Raspberry Pi changes without making a backup.

## Run tests

Python tests:

```bash
cd "$HOME/abb7"
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Apps Script aggregation tests require Node.js:

```bash
node tests/test_abb7_appscript.js
```

## Push changes from the Windows PC

Open PowerShell:

```powershell
Set-Location 'C:\Users\haffizol\Desktop\Code\AI Production Analytics\ABB 7 System'
git status
git pull --rebase origin main
git add <files-you-reviewed>
git diff --cached
git commit -m "Describe the ABB7 change"
git push origin main
```

Never commit `.env`, API keys, SQLite databases, generated reports, or the
unsanitized Node-RED export. After pushing, update the Raspberry Pi using the
pull procedure above.

## Common troubleshooting

- `401 unauthorized` from Atom: the Pi `ABB7_API_KEY` does not match Atom's `INGEST_API_KEY`.
- Connection refused or timeout: check the Atom IP, port `8088`, firewall, and Docker stack.
- Events remain pending: run `abb7_sqlite_status.py` and inspect the systemd journal.
- No Node-RED activity: check Mosquitto and confirm Node-RED uses `localhost:1883`.
- Service restart loop: inspect `journalctl`, `.env` permissions, GPIO access, and Python dependencies.
- Google data missing: verify `WEB_APP_URL`, Apps Script deployment access, and Apps Script logs.

## Update Node-RED for automatic shift endings

The Python service ends shifts from the submitted working hours: Day without OT
at 16:15, Day with OT at 20:00, Night without OT at 00:30 the following day, and
Night with OT at 08:00 the following day. Overnight idle hours do not generate
production rows; the 08:00 boundary starts the new day's counters from zero.

Update your private Node-RED export locally:

```bash
python3 deploy/update_nodered_shift_end.py original-flow.json updated-flow.json
```

This removes the End Shift button and duplicate MQTT reset signals, while keeping
automatic dashboard cleanup and session-end notification. Python broadcasts shift-end decisions to Node-RED; the dashboard does not run
a separate reset clock. Machine
counter finalization and the 30-minute entry window are owned by Python.

Back up your deployed flow, then replace the existing flow with the generated
export in Node-RED and deploy it; do not add a second copy alongside the old flow.
Deploy the updated Python service as well. Keep exports containing credentials
out of Git. Run the Pi and Node-RED in the local Malaysia timezone.

## Test forms, early shifts, and resend

The Test mode switch and test-preview button are hidden on the production
shift form. Confirm submits a live shift. Python and the Node-RED submission
gate still ignore explicitly test-marked commands, so test messages cannot
change production state.

Forms entered before their scheduled start are saved as pending in SQLite when
persistence is available. Python finalizes the outgoing shift first, enforces the
08:00 production-day reset, then activates the pending shift at its scheduled
start. Overlapping schedules, expired forms, and working hours inconsistent with
the Day/Night and OT selections are rejected. Pending forms do not update active
Node-RED shift memory or production services before Python accepts activation.

**Resend active shift details** resubmits the current accepted form and preserves
timers and counts. It sends shift metadata; it does not reconstruct lost sensor
counts or replay all historical data. The dashboard shows the active shift,
scheduled reset time, next pending shift, and whether SQLite saving is disabled.

Deploy the Python update and regenerate/import the Node-RED flow together:

```bash
python3 deploy/update_nodered_shift_end.py original-flow.json updated-flow.json
node tests/test_nodered_shift_ui.cjs updated-flow.json
```

The Node-RED check is optional during installation and validates the generated
flow's routing and preview behavior without contacting production services.
Keep the original export as a backup. Updating only Python does not add the new
Test mode or routing to the dashboard. Pending-state recovery requires a working,
writable SQLite database.

## Home and automatic shift-end navigation

At shift end Python broadcasts the end decision. Node-RED returns connected
operators to **Smart Checksheet**, alongside the existing dashboard cleanup.
The Home button on **Hourly Checksheet** opens Smart Checksheet. Home on Shift
Details, Condition, Reject, and Downtime opens Hourly Checksheet. Home only
navigates; it does not submit a form, change machine mode, or reset production.
Regenerate and deploy your private Node-RED export to apply these buttons.

Home and automatic return messages use the actual `ui-page.name` values required
by Dashboard `ui-control`: Smart Checksheet and Hourly Checksheet. URL paths are
not used as page names. Existing menu labels are preserved.

## Shared downtime timer

Deploy the updated Python service and regenerate/import the private Node-RED flow
**together**, preferably between shifts. All PCs and tablets using the same ABB
dashboard share one downtime session; ABB2 and ABB7 remain separate machines.

Opening Downtime or selecting a reason leaves machine mode unchanged. Start
Timer starts downtime. Stop Timer immediately returns to Normal and keeps the
stopped duration available to log. Log or Cancel also returns to Normal. A stopped
session must be logged or cancelled before another can start. Historical manual
logs remain available with a positive duration and action taken.

Python owns countdown completion and closes/logs an active session at shift end,
so closing the browser does not interrupt the timer. Devices render the same
server timestamp, reason, action, and session state. Stale concurrent commands
are rejected, repeated command IDs cannot produce a second log, and received
mode updates do not echo new mode commands. The dashboard reports connection
errors and disables timer controls when server updates are missing.

Session recovery requires writable SQLite persistence. The elapsed timer uses
wall-clock time across restarts; it does not reconstruct sensor counts or machine
operating time while the service was off. Check the persistence warning before
relying on restart recovery.

Validate a generated flow without contacting production services:

```bash
node tests/test_nodered_downtime_sync.cjs updated-flow.json
```

The checks simulate two devices and verify timer sync, shared stop, navigation
without a mode change, broadcast targeting, and duplicate-log protection. Also
verify PC/tablet operation on the deployed dashboard after importing the flow.
