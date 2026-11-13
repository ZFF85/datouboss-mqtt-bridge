"""Exercise real pyserial on a PTY and real MQTT on an isolated test broker."""
import json
import os
import pty
import queue
import select
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt

from bridge import HA_SENSORS, decode_response, discovery_configs, publish_discovery, query, scheduled_snapshots
from vendor.decode_h_protocol import adjust_crc_bytes, crc16_xmodem

RESPONSES = {
    "QPRTL": b"(HPVINV02",
    "HSTS": b"(00 L010000000000 11211001000L112000000",
    "HGRID": b"(240.2 49.9 280 090 70 40 +00291 0 06500 11+00000",
    "HOP": b"(240.2 49.9 00216 00177 003 006 06200 005.9 00107",
    "HBAT": b"(04 053.5 083 001 00000 393 101002010000 00000000",
    "HPV": b"(320.0 03.0 00960 00000.0 00000 0 060.0 027 08500",
    "HTEMP": b"(020 028 022 026 028 030 030 11000000000000000000",
    "HGEN": b"(260429 23:02 03.043 0059.4 0066.4 000000066.4 000000000000",
    "HIMSG1": b"(0040.05 20250923 12",
}


class ProtocolTests(unittest.TestCase):
    def test_discovery_fields_and_metadata(self):
        configs = list(discovery_configs("test/inverter", "HPVINV08"))
        self.assertEqual(len(configs), 24)
        self.assertEqual(len({c["unique_id"] for _, c in configs}), 24)
        for (command, field, _, unit, device_class), (topic, config) in zip(HA_SENSORS, configs):
            with self.subTest(field=field):
                self.assertIn(field, decode_response(command, RESPONSES[command] + bytes([13])))
                self.assertTrue(topic.startswith("homeassistant/sensor/"))
                self.assertTrue(topic.endswith("/config"))
                self.assertEqual(config["state_topic"], "test/inverter/state")
                self.assertEqual(config["availability_topic"], "test/inverter/availability")
                self.assertEqual(config["device"]["model"], "HPVINV08")
                self.assertGreaterEqual(config["expire_after"], 60)
                self.assertIn(".get(", config["value_template"])
                self.assertEqual(config.get("unit_of_measurement"), unit)
                self.assertEqual(config.get("device_class"), device_class)
                if device_class == "energy":
                    self.assertEqual(config["state_class"], "total_increasing")
        old = list(discovery_configs("test/inverter", "HPVINV02"))
        other = list(discovery_configs("other/inverter", "HPVINV08"))
        self.assertEqual([c["unique_id"] for _, c in configs], [c["unique_id"] for _, c in old])
        self.assertNotEqual(configs[0][1]["unique_id"], other[0][1]["unique_id"])

    def test_discovery_retained(self):
        client = Mock()
        with patch("bridge.publish") as send:
            publish_discovery(client, "test/inverter", "HPVINV08")
        self.assertEqual(send.call_count, len(HA_SENSORS))
        for call in send.call_args_list:
            self.assertTrue(call.kwargs["retain"])
            self.assertEqual(json.loads(call.args[2])["device"]["model"], "HPVINV08")


    def test_pv_priority_and_background_interval(self):
        clock = [0.0]
        calls = []
        def fake_poll(port, identity, commands):
            command, = commands
            calls.append(command)
            return {"timestamp": str(clock[0]), "data": {command: {}}, "errors": {}}
        with patch.dict(os.environ, POLL_INTERVAL="1", SLOW_POLL_INTERVAL="30"), \
             patch("bridge.poll", side_effect=fake_poll), \
             patch("bridge.time.monotonic", side_effect=lambda: clock[0]), \
             patch("bridge.STOP") as stop:
            stop.is_set.return_value = False
            stop.wait.side_effect = lambda delay: clock.__setitem__(0, clock[0] + delay)
            snapshots = scheduled_snapshots(None, "HPVINV08")
            for _ in range(20):
                next(snapshots)
            self.assertEqual(calls[0], "HPV")
            self.assertGreater(calls.count("HPV"), 7)
            self.assertEqual(calls.count("HBAT"), 1)
            self.assertTrue(all(calls[i - 1] == "HPV" for i, c in enumerate(calls) if c != "HPV"))
            self.assertIn("HPV", next(snapshots)["updated_at"])
            snapshots.close()

    def test_telemetry(self):
        self.assertEqual(decode_response("HBAT", RESPONSES["HBAT"] + b"\r")["battery_voltage_v"], 53.5)
        self.assertEqual(decode_response("HPV", RESPONSES["HPV"] + b"\r")["pv_power_w"], 960)
        self.assertNotIn("generation_power_kw", decode_response("HPV", RESPONSES["HPV"] + b"\r"))

    def test_crc(self):
        data = RESPONSES["HBAT"]
        frame = data + bytes(adjust_crc_bytes(crc16_xmodem(data))) + b"\r"
        self.assertEqual(decode_response("HBAT", frame)["battery_capacity_percent"], 83)

    def test_bad_frames(self):
        for frame in (b"(NAKss\r", b"(04 053.5\r", b"(04 nan 083 001 00000 393 flags\r",
                      RESPONSES["HBAT"], b"garbage\r", b"(04 \xff\r"):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                decode_response("HBAT", frame)

    def test_cannot_send_writes(self):
        with self.assertRaises(ValueError):
            query(None, "PCP00")

    def test_all_fixture_commands(self):
        for command, response in RESPONSES.items():
            with self.subTest(command=command):
                self.assertTrue(decode_response(command, response + b"\r"))


def integration(identity="HPVINV02"):
    messages = queue.Queue()
    ready = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="rs232-test-subscriber")
    def on_connect(c, *_):
        c.subscribe([(f"rs232_test/{identity}/#", 1), (f"ha_test/{identity}/#", 1)])
    def on_subscribe(*_):
        ready.set()
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = lambda c, u, msg: messages.put((msg.topic, msg.payload))
    for attempt in range(30):
        try:
            client.connect("broker", 1883)
            break
        except OSError:
            time.sleep(0.3)
    else:
        raise AssertionError("Test broker unavailable")
    client.loop_start()
    assert ready.wait(5), "MQTT subscription timeout"
    master, slave = pty.openpty()
    sim_stop = threading.Event()
    seen = []
    def simulate():
        buffer = b""
        battery_requests = 0
        while not sim_stop.is_set():
            if not select.select([master], [], [], 0.1)[0]:
                continue
            buffer += os.read(master, 4096)
            while b"\r" in buffer:
                request, buffer = buffer.split(b"\r", 1)
                command = request.decode("ascii")
                seen.append(command)
                response = b"(" + identity.encode() if command == "QPRTL" else RESPONSES.get(command, b"(NAKss")
                if command == "HBAT":
                    battery_requests += 1
                    if battery_requests == 2:
                        response = b"(04 053.5"  # Incomplete telemetry on second cycle.
                # Fragment response to exercise serial framing.
                os.write(master, response[:3])
                time.sleep(0.005)
                os.write(master, response[3:] + b"\r")
    simulator = threading.Thread(target=simulate, daemon=True)
    simulator.start()
    env = dict(os.environ, SERIAL_PORT=os.ttyname(slave), MQTT_HOST="broker",
               MQTT_BASE_TOPIC=f"rs232_test/{identity}", MQTT_CLIENT_ID="rs232-test-bridge",
               HA_DISCOVERY_PREFIX=f"ha_test/{identity}", POLL_INTERVAL="0.2", SLOW_POLL_INTERVAL="0.2", COMMAND_DELAY="0.01", SERIAL_TIMEOUT="0.2")
    process = subprocess.Popen([sys.executable, "/app/bridge.py"], env=env)
    states, statuses, configs = [], [], {}
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            topic, payload = messages.get(timeout=10)
            if topic.endswith("/config"):
                configs[topic] = json.loads(payload)
                continue

            if topic.endswith("/state"):
                states.append(json.loads(payload))
            else:
                statuses.append(payload.decode())
            failed = any("HBAT" in state["errors"] for state in states)
            if failed and states[-1]["complete"] and "HBAT" in states[-1]["data"] and statuses[-1:] == ["online"]:
                break
        assert len(states) >= 3, "Missing state publications"
        assert len(configs) == len(HA_SENSORS), "Missing MQTT discovery configs"
        assert all(c["device"]["model"] == identity for c in configs.values())
        assert all(state["protocol"] == identity for state in states)
        assert states[0]["complete"] is True
        assert states[0]["data"]["HPV"]["pv_power_w"] == 960
        failures = [state for state in states if "HBAT" in state["errors"]]
        assert failures, "Missing simulated battery failure"
        assert all("HBAT" not in state["data"] for state in failures), "Stale battery values were republished"
        assert states[-1]["complete"] is True and "HBAT" in states[-1]["data"], "Bridge failed to recover"
        assert "offline" in statuses and "online" in statuses
        assert sum("HPV" in state["updated_at"] for state in states) > 3
        assert set(seen) <= set(RESPONSES), "Unexpected serial write command"
    finally:
        process.terminate()
        process.wait(timeout=10)
        sim_stop.set()
        simulator.join(timeout=2)
        os.close(slave)
        os.close(master)
        client.disconnect()
        client.loop_stop()
    assert process.returncode == 0, process.returncode
    print("PASS: serial PTY -> bridge -> MQTT; partial failure -> offline -> recovery; graceful shutdown")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    for identity in ("HPVINV02", "HPVINV08"):
        integration(identity)
