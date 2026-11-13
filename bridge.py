"""Read-only HPVINV02/HPVINV08 serial polling and MQTT bridge. No cloud access."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
import logging
import math
import os
import signal
import threading
import time

import paho.mqtt.client as mqtt
import serial

from vendor.decode_h_protocol import decode_payload, split_payload, strip_response_crc

LOG = logging.getLogger("inverter")
STOP = threading.Event()
# Only these commands may ever be transmitted. No setters or MQTT subscriptions.
COMMANDS = ("HSTS", "HGRID", "HOP", "HBAT", "HPV", "HTEMP", "HGEN", "HIMSG1")
MIN_FIELDS = {"HSTS": 3, "HGRID": 9, "HOP": 8, "HBAT": 7,
              "HPV": 3, "HTEMP": 7, "HGEN": 6, "HIMSG1": 1, "QPRTL": 1}
NUMERIC_FIELDS = {"HGRID": range(7), "HOP": range(8), "HBAT": range(6),
                  "HPV": range(3), "HTEMP": range(7), "HGEN": range(2, 6)}


def decode_response(command, frame):
    if command not in MIN_FIELDS:
        raise ValueError("Command is not in the read-only allowlist")
    if not frame.endswith(b"\r") or not frame.startswith(b"("):
        raise ValueError("Incomplete or invalid response frame")
    payload, *_ = strip_response_crc(frame[:-1])
    text = payload.decode("ascii", errors="strict")
    if not all(32 <= ord(c) <= 126 for c in text):
        raise ValueError("Non-ASCII payload / invalid CRC")
    if text.startswith(("(NAK", "(ACK")):
        raise ValueError("Inverter rejected the query")
    fields = split_payload(text)
    if len(fields) < MIN_FIELDS[command]:
        raise ValueError(f"Too few fields for {command}")
    for index in NUMERIC_FIELDS.get(command, ()):
        if not math.isfinite(float(fields[index])):
            raise ValueError("Invalid numeric field")
    decoded = decode_payload(command, text)
    # This upstream field remains an unconfirmed interpretation.
    decoded.pop("generation_power_kw", None)
    return decoded


def query(port, command):
    if command not in MIN_FIELDS:
        raise ValueError("Command is not in the read-only allowlist")
    port.reset_input_buffer()
    port.write(command.encode("ascii") + b"\r")
    port.flush()
    frame = port.read_until(b"\r", size=2048)
    LOG.debug("%s response: %r", command, frame)
    return decode_response(command, frame)


def open_port():
    return serial.Serial(
        os.getenv("SERIAL_PORT", "/dev/inverter"), baudrate=2400,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=float(os.getenv("SERIAL_TIMEOUT", "3")),
        write_timeout=3, xonxoff=False, rtscts=False, dsrdtr=False, exclusive=True,
    )


def poll(port, identity="HPVINV02", commands=COMMANDS):
    # Build each snapshot anew: a failed query must not reuse old values.
    data, errors = {}, {}
    for command in commands:
        if STOP.is_set():
            break
        try:
            data[command] = query(port, command)
        except (ValueError, UnicodeError) as exc:
            errors[command] = str(exc)
            LOG.warning("%s: %s", command, exc)
            # Let a late response finish before issuing the next query.
            STOP.wait(float(os.getenv("SERIAL_TIMEOUT", "3")))
        STOP.wait(float(os.getenv("COMMAND_DELAY", "0.3")))
    return {"timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol": identity, "complete": not errors,
            "data": data, "errors": errors}



def scheduled_snapshots(port, identity):
    """Refresh PV first, interleaving one due background query per PV cycle."""
    fast_interval = float(os.getenv("POLL_INTERVAL", "1"))
    slow_interval = float(os.getenv("SLOW_POLL_INTERVAL", "30"))
    if fast_interval <= 0 or slow_interval <= 0:
        raise ValueError("Polling intervals must be positive")
    data, errors, updated = {}, {}, {}
    due = {command: 0.0 for command in COMMANDS if command != "HPV"}

    def refresh(command):
        result = poll(port, identity, (command,))
        if command in result["data"]:
            data[command] = result["data"][command]
            errors.pop(command, None)
            updated[command] = result["timestamp"]
        else:
            data.pop(command, None)
            updated.pop(command, None)
            errors.update(result["errors"])
        return {"timestamp": result["timestamp"], "protocol": identity,
                "complete": not errors, "data": dict(data),
                "errors": dict(errors), "updated_at": dict(updated)}

    while not STOP.is_set():
        started = time.monotonic()
        yield refresh("HPV")
        if STOP.is_set():
            return
        command = min(due, key=due.get)
        if due[command] <= time.monotonic():
            yield refresh(command)
            due[command] = time.monotonic() + slow_interval
        STOP.wait(max(0, fast_interval - (time.monotonic() - started)))


def publish(client, topic, payload, retain=False):
    info = client.publish(topic, payload, qos=1, retain=retain)
    info.wait_for_publish(timeout=5)
    if not info.is_published():
        raise ConnectionError("MQTT publication not acknowledged")


# command, field, name, unit, device class
HA_SENSORS = (
    ("HSTS", "mode_label", "Operating mode", None, None),
    ("HGRID", "ac_input_voltage_v", "Grid voltage", "V", "voltage"),
    ("HGRID", "mains_frequency_hz", "Grid frequency", "Hz", "frequency"),
    ("HGRID", "mains_power_w", "Grid power", "W", "power"),
    ("HOP", "ac_output_voltage_v", "Output voltage", "V", "voltage"),
    ("HOP", "ac_output_frequency_hz", "Output frequency", "Hz", "frequency"),
    ("HOP", "output_active_power_w", "Output active power", "W", "power"),
    ("HOP", "output_apparent_power_va", "Output apparent power", "VA", "apparent_power"),
    ("HOP", "output_load_percent", "Output load", "%", None),
    ("HBAT", "battery_voltage_v", "Battery voltage", "V", "voltage"),
    ("HBAT", "battery_capacity_percent", "Battery charge", "%", "battery"),
    ("HBAT", "battery_charging_current_a", "Battery charging current", "A", "current"),
    ("HBAT", "battery_discharge_current_a", "Battery discharge current", "A", "current"),
    ("HPV", "pv_voltage_v", "PV voltage", "V", "voltage"),
    ("HPV", "pv_current_a", "PV current", "A", "current"),
    ("HPV", "pv_power_w", "PV power", "W", "power"),
    ("HTEMP", "inverter_temperature_c", "Inverter temperature", "°C", "temperature"),
    ("HTEMP", "boost_temperature_c", "Boost temperature", "°C", "temperature"),
    ("HTEMP", "transformer_temperature_c", "Transformer temperature", "°C", "temperature"),
    ("HTEMP", "pv_temperature_c", "PV temperature", "°C", "temperature"),
    ("HGEN", "daily_power_gen_kwh", "Daily generation", "kWh", "energy"),
    ("HGEN", "monthly_electricity_generation_kwh", "Monthly generation", "kWh", "energy"),
    ("HGEN", "yearly_electricity_generation_kwh", "Yearly generation", "kWh", "energy"),
    ("HGEN", "total_power_generation_kwh", "Total generation", "kWh", "energy"),
)


def discovery_configs(base, identity):
    """Keep entity IDs stable across firmware changes."""
    prefix = os.getenv("HA_DISCOVERY_PREFIX", "homeassistant").strip("/")
    if not prefix or any(c in prefix for c in "#+"):
        raise ValueError("Invalid HA_DISCOVERY_PREFIX")
    device_id = "inverter_rs232_" + hashlib.sha256(base.encode()).hexdigest()[:16]
    device = {"identifiers": [device_id],
              "name": os.getenv("HA_DEVICE_NAME", "Datouboss inverter"), "model": identity}
    expiry = max(60, math.ceil(max(float(os.getenv("POLL_INTERVAL", "1")),
                                  float(os.getenv("SLOW_POLL_INTERVAL", "30"))) * 3 +
                              float(os.getenv("SERIAL_TIMEOUT", "3")) * len(COMMANDS) * 2))
    for command, field, name, unit, device_class in HA_SENSORS:
        key = command.lower() + "_" + field
        config = {
            "name": name, "unique_id": device_id + "_" + key,
            "device": device, "state_topic": base + "/state",
            "value_template": "{{ value_json.data.get(\"" + command + "\", {}).get(\"" + field + "\") }}",
            "availability_topic": base + "/availability",
            "expire_after": expiry, "qos": 1,
        }
        if unit:
            config["unit_of_measurement"] = unit
            config["state_class"] = "total_increasing" if device_class == "energy" else "measurement"
        if device_class:
            config["device_class"] = device_class
        yield prefix + "/sensor/" + device_id + "/" + key + "/config", config


def publish_discovery(client, base, identity):
    for topic, config in discovery_configs(base, identity):
        publish(client, topic, json.dumps(config, ensure_ascii=False), retain=True)
    LOG.info("Home Assistant MQTT discovery published")



def run():
    base = os.getenv("MQTT_BASE_TOPIC", "inverter_rs232").strip("/")
    if not base or any(c in base for c in "#+"):
        raise ValueError("Invalid MQTT_BASE_TOPIC")
    connected = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=os.getenv("MQTT_CLIENT_ID", "datouboss-mqtt-bridge"))
    def on_connect(c, userdata, flags, reason, properties):
        if reason == 0:
            connected.set()
            # Serial must be revalidated before advertising online after reconnect.
            c.publish(base + "/availability", "offline", qos=1, retain=True)
        else:
            LOG.error("MQTT connection refused: %s", reason)
    def on_disconnect(c, userdata, flags, reason, properties):
        connected.clear()
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    username = os.getenv("MQTT_USERNAME", "")
    if username:
        client.username_pw_set(username, os.getenv("MQTT_PASSWORD", ""))
    client.will_set(base + "/availability", "offline", qos=1, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.max_queued_messages_set(20)
    client.connect_async(os.environ["MQTT_HOST"], int(os.getenv("MQTT_PORT", "1883")), 30)
    client.loop_start()
    try:
        while not STOP.is_set():
            if not connected.wait(timeout=1):
                continue
            try:
                with open_port() as port:
                    identity = query(port, "QPRTL").get("device_type")
                    if identity not in ("HPVINV02", "HPVINV08"):
                        raise ValueError(f"Unexpected inverter identity: {identity!r}")
                    LOG.info("%s detected; polling read-only telemetry", identity)
                    publish_discovery(client, base, identity)
                    for snapshot in scheduled_snapshots(port, identity):
                        if STOP.is_set() or not connected.is_set():
                            break
                        publish(client, base + "/state", json.dumps(snapshot, allow_nan=False))
                        publish(client, base + "/availability",
                                "online" if snapshot["complete"] else "offline", retain=True)
                        LOG.info("Published %d/%d responses; PV updated at %s",
                                 len(snapshot["data"]), len(COMMANDS),
                                 snapshot["updated_at"].get("HPV"))
            except (OSError, serial.SerialException, ValueError, RuntimeError, ConnectionError) as exc:
                LOG.warning("Bridge retry: %s", exc)
                if connected.is_set():
                    try:
                        publish(client, base + "/availability", "offline", retain=True)
                    except (RuntimeError, ConnectionError):
                        pass
                STOP.wait(5)
    finally:
        if connected.is_set():
            try:
                publish(client, base + "/availability", "offline", retain=True)
            except (RuntimeError, ConnectionError):
                pass
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="Query identity and telemetry without MQTT")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    if args.probe:
        with open_port() as port:
            identity = query(port, "QPRTL").get("device_type")
            if identity not in ("HPVINV02", "HPVINV08"):
                raise ValueError(f"Unexpected identity: {identity}")
            snapshot = poll(port, identity)
            print(json.dumps(snapshot, indent=2, allow_nan=False))
            return 0 if snapshot["complete"] else 1
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
