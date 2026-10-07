import os
import runpy
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta


ABB7_DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ABB7_DIRECTORY not in sys.path:
    sys.path.insert(0, ABB7_DIRECTORY)

from abb7_persistence import ABB7SQLiteStore


class FakeMQTTClient:
    def __init__(self):
        self.on_connect = None
        self.on_message = None

    def will_set(self, *args, **kwargs):
        return None

    def connect(self, *args, **kwargs):
        return None

    def loop_start(self):
        return None

    def loop_stop(self):
        return None

    def disconnect(self):
        return None

    def publish(self, *args, **kwargs):
        return None

    def subscribe(self, *args, **kwargs):
        return None


class FakeHTTPResponse:
    text = '{"status":"success"}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"status": "success"}


class ABB7StartupSmokeTest(unittest.TestCase):
    def test_script_restores_saved_counter_before_main_loop(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = os.path.join(temporary_directory, "abb7-smoke.db")
            production_date = (datetime.now() - timedelta(hours=8)).date()
            store = ABB7SQLiteStore(database_path)
            store.save_runtime_state(
                "ABB7",
                {
                    "current_shift": {
                        "shift_id": f"{production_date:%Y%m%d}-Day-Line1",
                        "line": "Line 1",
                        "date": production_date.isoformat(),
                        "shift": "Day",
                        "model": "TEST-MODEL",
                        "lot_number": "TEST-LOT",
                        "overtime": True,
                        "workingTime": "8:00 AM to 8:00 PM",
                        "scheduled_end_at": "2099-07-22T20:00:00",
                    },
                    "counters": {
                        "hourly_output": 73,
                        "total_output": 191,
                        "shift_total_output": 428,
                        "total_rejects": 2,
                    },
                    "downtime_session": {"revision": 4, "status": "stopped", "id": "session",
                                         "data": {}, "elapsed_ms": 120000},
                    "pending_shift_form": {"prodDate": "2099-07-22", "shift": "Night",
                                           "workingTime": "8:00 PM to 8:00 AM", "overtime": True},
                    "timers": {},
                    "cycle": {},
                    "current_mode": "NORMAL",
                    "current_status": 0,
                    "last_sent_hour": datetime.now().hour,
                    "last_reset_day": -1,
                    "force_delay": False,
                },
                "ACTIVE",
            )
            store.close()

            gpio_module = types.ModuleType("RPi.GPIO")
            gpio_module.BCM = 1
            gpio_module.IN = 1
            gpio_module.PUD_UP = 1
            gpio_module.setmode = lambda *args, **kwargs: None
            gpio_module.setup = lambda *args, **kwargs: None
            gpio_module.cleanup = lambda *args, **kwargs: None

            # Stop on the first attempted sensor read, after startup restoration.
            def stop_test_loop(*args, **kwargs):
                raise KeyboardInterrupt()

            gpio_module.input = stop_test_loop
            rpi_module = types.ModuleType("RPi")
            rpi_module.GPIO = gpio_module

            mqtt_client_module = types.ModuleType("paho.mqtt.client")
            mqtt_client_module.Client = FakeMQTTClient
            mqtt_package = types.ModuleType("paho.mqtt")
            mqtt_package.client = mqtt_client_module
            paho_package = types.ModuleType("paho")
            paho_package.mqtt = mqtt_package

            requests_module = types.ModuleType("requests")
            requests_module.sent_payloads = []

            def fake_post(*args, **kwargs):
                requests_module.sent_payloads.append(kwargs.get("json"))
                return FakeHTTPResponse()

            requests_module.post = fake_post

            replacements = {
                "RPi": rpi_module,
                "RPi.GPIO": gpio_module,
                "paho": paho_package,
                "paho.mqtt": mqtt_package,
                "paho.mqtt.client": mqtt_client_module,
                "requests": requests_module,
            }
            previous_modules = {
                name: sys.modules.get(name) for name in replacements
            }
            previous_database_path = os.environ.get("ABB7_SQLITE_PATH")
            previous_api_enabled = os.environ.get("ABB7_API_ENABLED")

            try:
                sys.modules.update(replacements)
                os.environ["ABB7_SQLITE_PATH"] = database_path
                os.environ["ABB7_API_ENABLED"] = "false"
                namespace = runpy.run_path(
                    os.path.join(ABB7_DIRECTORY, "abb7.py"),
                    run_name="__main__",
                )
            finally:
                for name, previous_module in previous_modules.items():
                    if previous_module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous_module

                if previous_database_path is None:
                    os.environ.pop("ABB7_SQLITE_PATH", None)
                else:
                    os.environ["ABB7_SQLITE_PATH"] = previous_database_path

                if previous_api_enabled is None:
                    os.environ.pop("ABB7_API_ENABLED", None)
                else:
                    os.environ["ABB7_API_ENABLED"] = previous_api_enabled

            self.assertEqual(namespace["hourly_output"], 73)
            self.assertEqual(namespace["total_output"], 191)
            self.assertEqual(namespace["shift_total_output"], 428)
            self.assertTrue(namespace["recovered_from_sqlite"])
            self.assertEqual(namespace["pending_shift_form"]["prodDate"], "2099-07-22")
            self.assertEqual(namespace["downtime_session"]["elapsed_ms"], 120000)

    def test_shift_end_queues_partial_hour_before_pdf(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = os.path.join(temporary_directory, "abb7-end-test.db")
            store = ABB7SQLiteStore(database_path)

            module_globals = {
                "downtime_session": {"status": "idle"},
                "current_shift": {
                    "shift_id": "20260724-Day-Line1",
                    "line": "Line 1",
                    "date": "2026-07-24",
                    "shift": "Day",
                    "group": "-",
                    "model": "TEST-MODEL",
                    "lot_number": "TEST-LOT",
                    "target": 100,
                    "standard_cycle": 1.0,
                    "supervisor": "-",
                    "leader": "-",
                    "workingTime": "8:00 AM to 4:15 PM",
                    "overtime": False,
                    "scheduled_end_at": "2026-07-24T16:15:00",
                    "forming": "-",
                    "waterjet": "-",
                    "assembly": "-",
                    "quality": "-",
                }
            }

            # Load the script using the existing smoke-test fakes, then replace
            # its closed test store with a fresh store for this function test.
            gpio_module = types.ModuleType("RPi.GPIO")
            gpio_module.BCM = 1
            gpio_module.IN = 1
            gpio_module.PUD_UP = 1
            gpio_module.setmode = lambda *args, **kwargs: None
            gpio_module.setup = lambda *args, **kwargs: None
            gpio_module.cleanup = lambda *args, **kwargs: None
            gpio_module.input = lambda *args, **kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt()
            )
            rpi_module = types.ModuleType("RPi")
            rpi_module.GPIO = gpio_module

            mqtt_client_module = types.ModuleType("paho.mqtt.client")
            mqtt_client_module.Client = FakeMQTTClient
            mqtt_package = types.ModuleType("paho.mqtt")
            mqtt_package.client = mqtt_client_module
            paho_package = types.ModuleType("paho")
            paho_package.mqtt = mqtt_package

            requests_module = types.ModuleType("requests")
            requests_module.sent_payloads = []

            def fake_post(*args, **kwargs):
                requests_module.sent_payloads.append(kwargs.get("json"))
                return FakeHTTPResponse()

            requests_module.post = fake_post
            replacements = {
                "RPi": rpi_module,
                "RPi.GPIO": gpio_module,
                "paho": paho_package,
                "paho.mqtt": mqtt_package,
                "paho.mqtt.client": mqtt_client_module,
                "requests": requests_module,
            }
            previous_modules = {
                name: sys.modules.get(name) for name in replacements
            }
            previous_database_path = os.environ.get("ABB7_SQLITE_PATH")
            previous_api_enabled = os.environ.get("ABB7_API_ENABLED")

            try:
                sys.modules.update(replacements)
                os.environ["ABB7_SQLITE_PATH"] = database_path
                os.environ["ABB7_API_ENABLED"] = "false"
                namespace = runpy.run_path(
                    os.path.join(ABB7_DIRECTORY, "abb7.py"),
                    run_name="__main__",
                )

                script_globals = namespace["execute_end_shift"].__globals__
                script_globals["persistence_store"] = store
                script_globals["current_shift"] = module_globals["current_shift"]
                script_globals["hourly_output"] = 7
                script_globals["total_output"] = 20
                script_globals["shift_total_output"] = 50
                script_globals["base_time_this_hour"] = 15 * 60
                script_globals["lost_time_this_hour"] = 0.0
                script_globals["hourly_rest_time"] = 0.0
                script_globals["google_sheets_queue"] = []

                namespace["execute_end_shift"](
                    hour_slot_override="16.00-17.00",
                    end_reason="SCHEDULED SHIFT END",
                )

                queue = script_globals["google_sheets_queue"]
                self.assertEqual(queue[0]["tab"], "Hourly_Data")
                self.assertEqual(queue[0]["row"][3], "16.00-17.00")
                self.assertEqual(queue[0]["row"][5], 7)
                self.assertEqual(queue[1]["action"], "GENERATE_PDF")

                namespace["process_gsheets_queue"]()
                namespace["process_gsheets_queue"]()
                self.assertEqual(
                    [payload["action"] for payload in requests_module.sent_payloads],
                    ["APPEND_ROW", "GENERATE_PDF"],
                )
            finally:
                store.close()
                for name, previous_module in previous_modules.items():
                    if previous_module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous_module
                if previous_database_path is None:
                    os.environ.pop("ABB7_SQLITE_PATH", None)
                else:
                    os.environ["ABB7_SQLITE_PATH"] = previous_database_path
                if previous_api_enabled is None:
                    os.environ.pop("ABB7_API_ENABLED", None)
                else:
                    os.environ["ABB7_API_ENABLED"] = previous_api_enabled


if __name__ == "__main__":
    unittest.main()
