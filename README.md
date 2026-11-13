# Datouboss MQTT Bridge

A local, read-only RS232-to-MQTT bridge for inverter telemetry, with automatic
Home Assistant MQTT discovery. Runs in Docker without an ESP32, vendor cloud,
or Wi-Fi dongle.

Used with a **Datouboss DT4862S** inverter. The bridge accepts the `HPVINV02` and
`HPVINV08` protocol identities and decodes their H-command responses. Other
models or firmware versions need to be checked with `--probe`, as a matching
identity alone does not establish telemetry compatibility.

## Features

- Battery, PV, AC input/output, temperature, status, and generation telemetry.
- PV queries every second by default, with slower background polling.
- JSON snapshots over MQTT, QoS 1, and retained online/offline availability.
- 24 Home Assistant sensors with stable entity identifiers.
- Fixed read-only command allowlist, with no MQTT command subscriptions.
- Automatic retry after serial or MQTT failures and graceful shutdown.
- Hardware-free integration tests using a simulated serial device and Mosquitto.

## Requirements

- A Linux host with Docker Engine and the Docker Compose plugin.
- A real **USB-to-RS232 adapter** and a cable wired for your inverter.
- An MQTT broker reachable from the container.
- Optional: Home Assistant with its MQTT integration connected to the same broker.

### Serial connection

The serial settings are **2400 baud, 8 data bits, no parity, 1 stop bit**, with
no flow control. Use an RS232 adapter, not a USB-to-TTL adapter.
Use the inverter's RS232 communication port, and verify its pinout against the
manual for your exact device before connecting it. An RJ45 connector does not
imply Ethernet, and dongle-side and inverter-side pinouts may differ.

Disconnect the Wi-Fi monitoring dongle before polling. Only one active poller
should use the serial connection at a time. Do not share the adapter with another
service.

## Quick start

Get the deployment files:

```bash
git clone https://github.com/ZFF85/datouboss-mqtt-bridge.git
cd datouboss-mqtt-bridge
cp .env.example .env
ls -l /dev/serial/by-id/
```

Edit `.env`: set `SERIAL_DEVICE` to the inverter adapter's full, stable
`/dev/serial/by-id/...` path, then set `MQTT_HOST` and any broker credentials.
The example values are placeholders. Keep `.env` private, and leave it excluded
from Git and the Docker build context.

### Use the published image

[compose.image.yaml](compose.image.yaml) uses
`ghcr.io/zff85/datouboss-mqtt-bridge:latest`, with AMD64 and ARM64 variants.
Docker selects the matching platform automatically. No local build is needed.

```bash
docker compose -f compose.image.yaml pull
docker compose -f compose.image.yaml run --rm datouboss-mqtt-bridge python bridge.py --probe
docker compose -f compose.image.yaml up -d
docker compose -f compose.image.yaml logs -f --tail=50
```

`--probe` queries the identity and all telemetry commands, prints JSON, and exits
with status 0 if all telemetry queries succeeded, or 1 if any failed. It does not
connect to MQTT. Stop an already running bridge before probing the same adapter.

To update a running installation:

```bash
docker compose -f compose.image.yaml pull
docker compose -f compose.image.yaml up -d
```

To stop it:

```bash
docker compose -f compose.image.yaml down
```

Only `compose.image.yaml` and a configured `.env` are needed for this deployment.
The image can also be pinned to a release tag or digest using `INVERTER_IMAGE`.
See [Container images and updates](docs/containers.md) for available tags,
publication, package visibility, and rollback instructions.

### Build locally

The default [compose.yaml](compose.yaml) builds the image from this checkout:

```bash
docker compose build
docker compose run --rm datouboss-mqtt-bridge python bridge.py --probe
docker compose up -d
docker compose logs -f --tail=50
```

Use `docker compose up -d --build` after source changes, or
`docker compose build --pull` followed by `docker compose up -d` to refresh the
base image. Stop a local-build deployment with `docker compose down` before
switching to the published-image deployment.

## Configuration

Compose reads `.env` and maps `SERIAL_DEVICE` on the host to `/dev/inverter` in
the container. `SERIAL_PORT` is set by Compose, so change `SERIAL_DEVICE`, not
`SERIAL_PORT`, for a Docker installation.

| Variable | Default | Description |
| --- | --- | --- |
| `SERIAL_DEVICE` | Required by Compose | Host path to the inverter's RS232 adapter. |
| `INVERTER_IMAGE` | `ghcr.io/zff85/datouboss-mqtt-bridge:latest` | Image reference used by `compose.image.yaml`. |
| `MQTT_HOST` | Required | Broker hostname or IP reachable from the container. |
| `MQTT_PORT` | `1883` | MQTT TCP port. |
| `MQTT_USERNAME` | Empty | Optional broker username. |
| `MQTT_PASSWORD` | Empty | Password used when a username is set. |
| `MQTT_CLIENT_ID` | `datouboss-mqtt-bridge` | Unique client ID on the broker. |
| `MQTT_BASE_TOPIC` | `inverter_rs232` | Base topic, without MQTT wildcards. |
| `POLL_INTERVAL` | `1` | Target PV query interval in seconds, with a positive value required. |
| `SLOW_POLL_INTERVAL` | `30` | Background command interval in seconds, with a positive value required. |
| `SERIAL_TIMEOUT` | `3` | Serial response timeout in seconds. |
| `COMMAND_DELAY` | `0.3` | Delay after each query in seconds. |
| `LOG_LEVEL` | `INFO` | Python logging level, with raw serial replies included at `DEBUG`. |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant discovery topic prefix. |
| `HA_DEVICE_NAME` | `Datouboss inverter` | Device display name, set to `Datouboss DT4862S` in the example. |

The bridge currently uses plain MQTT TCP without TLS. Use a trusted network or a
private tunnel. If the broker runs on the Docker host, `host.docker.internal` is
available through the Compose host-gateway mapping. `localhost` inside the
container refers to the container itself.

For multiple inverters, use separate adapters, `MQTT_CLIENT_ID` values, and
`MQTT_BASE_TOPIC` values for each instance.

### Running without Docker

Python 3.12 is the version used by the Docker image and CI. On Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export SERIAL_PORT=/dev/serial/by-id/YOUR_INVERTER_ADAPTER
python bridge.py --probe
export MQTT_HOST=mqtt.example.com
python bridge.py
```

The script reads environment variables directly, and it does **not** load `.env`
automatically outside Docker Compose. Export any additional settings you need.
The user running the script must have permission to access the serial device.

## MQTT messages

| Topic (default) | Payload | Retained | QoS |
| --- | --- | --- | --- |
| `inverter_rs232/state` | JSON telemetry snapshot | No | 1 |
| `inverter_rs232/availability` | `online` or `offline` | Yes | 1 |

An abbreviated state message:

```json
{
  "timestamp": "2026-11-13T12:00:00+00:00",
  "protocol": "HPVINV08",
  "complete": true,
  "data": {
    "HPV": {
      "pv_voltage_v": 320.0,
      "pv_current_a": 3.0,
      "pv_power_w": 960
    }
  },
  "errors": {},
  "updated_at": {
    "HPV": "2026-11-13T12:00:00+00:00"
  }
}
```

A snapshot is published after each command response. PV is queried first, then
at most one due background command is queried per PV cycle. Background data
appears gradually during startup. Serial response times and delays can increase
the actual polling intervals.

- `timestamp` is the snapshot time in UTC.
- `protocol` is the identity returned by the inverter, not the product model.
- `data` holds the most recent successful result for each command. Full messages
  also include upstream diagnostic fields such as `raw_payload` and `tokens`.
- `updated_at` records the last successful update time for each command.
- If a query fails, that command's old data and update timestamp are removed,
  and the error is recorded in `errors`. A later successful query clears it.
- `complete` means there are no outstanding query errors. It can be `true`
  before every background command has been polled for the first time.

Availability becomes `online` after an error-free snapshot and `offline` on a
query or connection failure. The broker also publishes retained `offline` via
Last Will on an unexpected MQTT disconnect. After reconnecting, the bridge
checks the inverter identity again before resuming telemetry.

Useful JSON paths:

| Value | Path |
| --- | --- |
| Battery voltage | `data.HBAT.battery_voltage_v` |
| Battery charge | `data.HBAT.battery_capacity_percent` |
| PV power | `data.HPV.pv_power_w` |
| Output active power | `data.HOP.output_active_power_w` |
| Daily generation | `data.HGEN.daily_power_gen_kwh` |
| Total generation | `data.HGEN.total_power_generation_kwh` |

### Protocol scope

The bridge sends only `QPRTL` for identification and the telemetry commands
`HSTS`, `HGRID`, `HOP`, `HBAT`, `HPV`, `HTEMP`, `HGEN`, and `HIMSG1`. It does not
send setting changes or subscribe to control topics.

Decoding follows the vendored upstream implementation. Temperature and generation
field meanings may depend on firmware. The unconfirmed `generation_power_kw`
field is deliberately omitted. Both plain ASCII responses and responses with a
valid CRC suffix are accepted, so this is not a strict CRC-only transport.

## Home Assistant

Discovery is published automatically after successful inverter identification.
Connect the Home Assistant MQTT integration to the same broker, then open
**Settings → Devices & services → MQTT → Devices**. No manual sensor YAML is
required. The log reports `Home Assistant MQTT discovery published` when the
24 retained sensor configurations have been sent.

Sensors cover operating mode, grid and output measurements, battery voltage,
charge and current, PV measurements, four temperatures, and daily/monthly/yearly/
total generation. Names are in English. The device model shown in discovery is
the detected protocol identity, while `HA_DEVICE_NAME` sets the friendly name.

Entity unique IDs are derived from `MQTT_BASE_TOPIC` and remain stable across
protocol identity changes. Changing the base topic creates a different device, and old retained discovery
messages must be removed from the broker separately.

Sensor configurations are retained, but telemetry is not. Sensors use the
availability topic and also expire if state messages stop. The **Total
generation** sensor uses `kWh` and `total_increasing` and can be selected as solar
production in Home Assistant's Energy dashboard. Check the reported values
against your inverter before relying on them.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Adapter not found | Check `SERIAL_DEVICE` and `/dev/serial/by-id/`. |
| Adapter reconnected but bridge cannot read it | Run `docker compose up -d --force-recreate` to refresh the device mapping. |
| No reply or incomplete frames | Check the RS232 adapter, wiring, selected port, and competing pollers. Use `--probe` with the service stopped. |
| Unexpected inverter identity | The bridge accepts only `HPVINV02` and `HPVINV08`, so inspect the probe output and firmware compatibility. |
| MQTT never connects | Check the broker address, credentials, network access, and broker logs. |
| No Home Assistant device | Check the MQTT integration's broker and discovery prefix, then inspect bridge logs. |
| Some fields are missing | Inspect `errors` and `updated_at` in the state JSON, as failed queries do not retain old values. |

## Development and tests

Run protocol, discovery, and polling scheduler tests without hardware or a broker:

```bash
python -m unittest discover -s tests -p integration.py -v
```

Run the complete serial-to-MQTT integration suite with Docker:

```bash
docker compose -p datouboss-mqtt-test -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p datouboss-mqtt-test -f compose.test.yaml down --volumes
```

The test broker exposes no host ports and does not use your `.env` or inverter.
A pseudo-terminal simulates both supported identities, fragmented responses, a
failed battery query, omitted stale values, recovery, discovery, and shutdown.
GitHub Actions runs this same suite on pushes and pull requests.

## Security checks

The **Security scans** GitHub Actions workflow audits Python dependencies on
pushes and pull requests, can be started manually, and runs every Monday at
07:17 UTC on the default branch. Scheduled checks can detect newly published
advisories even when the project has not changed.

[pip-audit](https://github.com/pypa/pip-audit) resolves `requirements.txt` and
checks runtime dependencies against known vulnerability advisories. Any known
vulnerability or dependency collection error fails the audit.

Open **Actions → Security scans → a workflow run** to inspect the job logs.
The Python JSON report is also saved as a downloadable artifact for 14 days,
including when findings cause the job to fail. If a tool or network failure
prevents a report from being generated, the job log contains the error instead.

The audit uses public advisory databases and needs outbound network access,
but no repository secrets, local `.env`, or inverter connection. Actions are
pinned to commit SHAs and `pip-audit` to a version. Keep these pins updated when
maintaining the workflow.

This check covers known Python package vulnerabilities. Container OS packages
are not scanned. It does not replace source code review or cover all custom or
vendored code. Image publication is gated on integration tests, while the Python
audit runs independently and reports findings without blocking publication.

## License and acknowledgements

This project is licensed under the [MIT License](LICENSE).

The unmodified protocol decoder comes from
[kOld/solarplug-esphome](https://github.com/kOld/solarplug-esphome).
Its original copyright and MIT license are preserved in [vendor/LICENSE](vendor/LICENSE).
See [vendor/UPSTREAM.md](vendor/UPSTREAM.md) for the pinned source revision.
This is an independent project, unaffiliated with Datouboss.
