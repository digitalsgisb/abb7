# Connect ABB7 to Smart Checksheet

The AI PC hosts the Checksheet backend. The ABB7 Pi keeps running its existing
Python sensor service, local MQTT broker, Google Sheets and analytics integration.
Pulling this update does not enable a connection by itself.
The repository is https://github.com/digitalsgisb/abb7.

The existing sensor service and analytics sender continue using their current
configuration. Install the relay separately after configuring the AI PC.

## Update the existing Python service

Before updating, identify the actual service name, working directory, service
user and live SQLite path (including any `ABB7_SQLITE_PATH` override). Back up
the source, service environment and database with the sensor service stopped.
Preserve all existing credentials, runtime data and local production settings.

In the ABB7 checkout, inspect `git status` and resolve local changes before
running `git pull --ff-only`. Restart the existing service after updating; do
not launch a second sensor process. The update includes `line_code: ABB7` in
live MQTT snapshots and supplies an independent durable event relay. ABB7 already
has its prefixed persistence and shift modules. The sensor remains on BCM GPIO 17;
keep the existing wiring.

### Raspberry Pi 5 GPIO dependency

The ABB7 installation uses a Raspberry Pi 5. Use `rpi-lgpio`, which supplies the
existing `RPi.GPIO` import. Do not install the separate `RPi.GPIO` package in the
same environment: both packages provide that module and conflict.

If both were installed, stop the existing sensor service, then repair its venv:

```bash
.venv/bin/python -m pip uninstall -y RPi.GPIO rpi-lgpio
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Run these commands from the updated ABB7 checkout, then restart the existing
sensor service. The sensor pin stays at BCM GPIO 17. Follow the
[rpi-lgpio installation documentation](https://rpi-lgpio.readthedocs.io/en/latest/install.html).

## Private MQTT access

Allow the AI PC to reach ABB7's broker using an authenticated private listener,
private VPN/forwarding, or verified TLS. Preserve Python and Node-RED access.
ABB7 must use its own broker: generic command topics are shared across lines.

The Checksheet broker account needs write access to `nodered/newshift`,
`nodered/modeltarget`, `nodered/mode`, `nodered/adjust_count`, `nodered/reject`,
`noderedparam/condition` and `nodered/downtime_session`; it needs read access to
`smartchecksheet/live`, `smartchecksheet/hourly` and `abb7/#`.

## AI PC configuration

Update the AI PC Checksheet backend to the version supporting per-line machine
connections. Preserve existing attendance settings and other commissioned lines.
Add this entry to its machine configuration, substituting the actual address:

```json
{
  "line": "ABB7",
  "mqttUrl": "mqtt://ABB7_PI_PRIVATE_IP:1883",
  "catalogPath": "abb7-catalog.json",
  "productionLine": "MF"
}
```

The configuration file is an array of entries. ABB7 is MF: retain
`productionLine: "MF"` to preserve shift IDs such as `20261106-Day-MF`.
Supply a verified ABB7 model/manpower catalog beside that file.

Set backend-only environment values:

```dotenv
APP_MODE=live
MACHINE_CONNECTIONS_PATH=/app/config/machines.json
ABB7_MQTT_USERNAME=checksheet
ABB7_MQTT_PASSWORD=REPLACE_WITH_PRIVATE_BROKER_PASSWORD
ABB7_INGEST_TOKEN=REPLACE_WITH_UNIQUE_LONG_RANDOM_TOKEN
```

The shown path is for the Checksheet Docker config mount; a native installation
must use its actual machine config path. Use a production database separate from
demo and a bootstrap password of at least 12 characters. Rebuild/restart the
backend with the updated environment and assign the operator account ABB7 access.

## Durable events relay

The independent relay reads ABB7's existing `outbox_events` and adds its own
`checksheet_deliveries` ledger in the same SQLite database. It does not change
analytics delivery statuses. It retries events after outages and requires an
acknowledgement from the Checksheet receiver. It never publishes machine commands.

Copy the supplied relay and unit on the ABB7 Pi:

```bash
sudo install -d /opt/smartchecksheet
sudo install -m 644 deploy/checksheet_event_relay.py /opt/smartchecksheet/checksheet_event_relay.py
sudo install -m 644 deploy/checksheet-relay@.service /etc/systemd/system/checksheet-relay@.service
```

Create `/etc/smartchecksheet-relay.env` privately, with these actual values:

```dotenv
CHECKSHEET_LINE=ABB7
CHECKSHEET_SQLITE_PATH=/actual/path/to/existing/abb7_state.db
CHECKSHEET_EVENTS_URL=http://AI_PC_PRIVATE_IP:3700/api/integrations/events
CHECKSHEET_TOKEN=THE_SAME_VALUE_AS_ABB7_INGEST_TOKEN
```

The service user must have access to the database and its directory. Run one
relay per database. The Pi must reach the AI PC endpoint over a private network
or HTTPS; a loopback-only server/port bind is not reachable from the Pi.

```bash
sudo chmod 600 /etc/smartchecksheet-relay.env
sudo systemctl daemon-reload
sudo systemctl enable --now checksheet-relay@YOUR_EXISTING_SERVICE_USER
sudo journalctl -u checksheet-relay@YOUR_EXISTING_SERVICE_USER -n 40 --no-pager
```

Do not replace the existing `ABB7_API_EVENTS_URL` destination: analytics delivery
remains independent. Keep tokens, populated env files and runtime databases out
of Git.

## Verify during commissioning

Check `/api/health` on the AI PC and open ABB7 in the tablet app. Confirm actual
counts and status appear only under ABB7 before testing writes. Then verify
setup, mode, reject, parameter and downtime actions against Python. Controls
require fresh telemetry (within eight seconds) and production commands need
confirmation. Check the machine before resubmitting an unconfirmed command.
Confirm existing OEE/Sheets remain operational and relay delivery recovers after
a network interruption. This update has not been tested on the physical Pi.

Local tests, with mocked hardware/network:

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s deploy -p 'test_*.py' -v
```
