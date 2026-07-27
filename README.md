# ABB7 Production Tracker

ABB7 is a Raspberry Pi production-tracking service for the ABB 7 line. It reads
the production sensor, exchanges commands and live values with Node-RED over
MQTT, writes production records to Google Sheets, keeps restart-safe state in
SQLite, and forwards durable events to the central production data platform on
the AI TOP ATOM PC.

## How it works

```text
Production sensor (GPIO 22)
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

## Runtime assumptions

- Raspberry Pi OS with Python 3.
- The production sensor uses BCM GPIO pin `22`.
- Mosquitto is available at `localhost:1883`.
- Node-RED and InfluxDB run locally when their dashboard/history features are used.
- The Atom ingestion endpoint is `http://172.19.3.72:8088/api/v1/events`.
- The Atom health endpoint is `http://172.19.3.72:8088/health`.

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
ABB7_API_EVENTS_URL=http://172.19.3.72:8088/api/v1/events
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
curl --fail --silent --show-error http://172.19.3.72:8088/health
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
