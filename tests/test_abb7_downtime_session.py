import copy
import os
import sys
import unittest
import ast
import json
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from abb7_downtime_session import empty_session, transition


class SharedDowntimeTests(unittest.TestCase):
    def command(self, state, action, data=None, command_id=None, now=1000):
        return transition(state, {"action": action, "revision": state["revision"],
                                 "session_id": state["id"], "data": data or {},
                                 "command_id": command_id}, now, "SHIFT")

    def start(self, preset=0):
        return self.command(empty_session(), "start", {"downtimeCategory": "Machine",
                           "machineIssue": "Sensor", "presetTime": preset}, command_id="start")[0]

    def test_cancel_idle_emits_navigation_once_without_changing_mode(self):
        before = empty_session()
        before["data"] = {"downtimeCategory": "Machine"}
        state, effects = self.command(before, "cancel", command_id="cancel-idle")
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["data"], {})
        self.assertEqual(effects["event"], "cancelled")
        self.assertIsNone(effects["mode"])
        self.assertIsNone(effects["log"])
        replay, repeated = self.command(state, "cancel", command_id="cancel-idle")
        self.assertEqual(replay, state)
        self.assertIsNone(repeated["event"])

    def test_editing_category_does_not_change_machine_mode(self):
        state, effects = self.command(empty_session(), "update", {"downtimeCategory": "Machine"})
        self.assertEqual(state["status"], "idle")
        self.assertIsNone(effects["mode"])
        self.assertIsNone(effects["log"])

    def test_start_stop_log_returns_normal_and_logs_once(self):
        state = self.start()
        stopped, effects = self.command(state, "stop", now=121000)
        self.assertEqual(effects["mode"], "NORMAL")
        self.assertEqual(stopped["elapsed_ms"], 120000)
        logged, effects = self.command(stopped, "log", {"actionTaken": "Fixed"}, "log", now=241000)
        self.assertEqual(effects["mode"], "NORMAL")
        self.assertEqual(effects["log"]["durationMinutes"], 2)
        self.assertEqual(logged["status"], "idle")
        replay, effects = transition(logged, {"action": "log", "command_id": "log"}, 300000, "SHIFT")
        self.assertEqual(replay, logged)
        self.assertIsNone(effects["log"])

    def test_concurrent_device_start_and_stale_stop_are_rejected(self):
        state = self.start()
        original = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            transition(state, {"action": "start", "revision": 0}, 2000, "SHIFT")
        with self.assertRaises(ValueError):
            transition(state, {"action": "stop", "revision": state["revision"], "session_id": "old"}, 2000, "SHIFT")
        self.assertEqual(state, original)

    def test_countdown_completion_is_a_single_server_log(self):
        state = self.start(preset=2)
        logged, effects = transition(state, {"action": "expire"}, 181000, "SHIFT", automatic=True)
        self.assertEqual(effects["log"]["durationMinutes"], 2)
        self.assertEqual(effects["mode"], "NORMAL")
        self.assertEqual(logged["status"], "idle")
        with self.assertRaises(ValueError):
            transition(logged, {"action": "expire"}, 200000, "SHIFT", automatic=True)

    def test_log_validation_does_not_stop_live_timer(self):
        state = self.start()
        with self.assertRaises(ValueError):
            self.command(state, "log", now=61000)
        self.assertEqual(state["status"], "running")

    def test_cancel_and_shift_end_close_the_shared_session(self):
        state = self.start()
        cancelled, effects = self.command(state, "cancel", now=61000)
        self.assertEqual(cancelled["status"], "idle")
        self.assertEqual(effects["mode"], "NORMAL")
        ended, effects = transition(state, {"action": "shift_end"}, 61000, "SHIFT", automatic=True)
        self.assertEqual(ended["status"], "idle")
        self.assertEqual(effects["log"]["durationMinutes"], 1)

    def test_no_shift_and_other_modes_cannot_override_a_running_timer(self):
        with self.assertRaises(ValueError):
            transition(empty_session(), {"action": "start", "revision": 0,
                       "data": {"downtimeCategory": "Machine", "machineIssue": "Fault"}}, 0, "NO PROD")
        with self.assertRaises(ValueError):
            self.command(self.start(), "mode", {"mode": "REST"})

    def test_action_can_be_updated_from_another_device_while_running(self):
        state = self.start()
        updated, effects = self.command(state, "update", {"actionTaken": "Replacing sensor"})
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["data"]["actionTaken"], "Replacing sensor")
        self.assertIsNone(effects["mode"])
        with self.assertRaises(ValueError):
            self.command(updated, "update", {"downtimeCategory": "Quality"})

    def test_historic_manual_log_does_not_start_a_timer(self):
        state, effects = self.command(empty_session(), "log", {"downtimeCategory": "Machine",
                                     "machineIssue": "Fault", "durationMinutes": 3,
                                     "actionTaken": "Repaired"}, command_id="manual")
        self.assertEqual(state["status"], "idle")
        self.assertEqual(effects["mode"], "NORMAL")
        self.assertEqual(effects["log"]["durationMinutes"], 3)

    def test_runtime_persists_shared_session_and_one_log_without_resetting_counts(self):
        from test_abb7_shift_runtime import load_shift_form_runtime, submit_shift_form, SCRIPT_PATH
        from abb7_persistence import ABB7SQLiteStore
        ns, clock, _ = load_shift_form_runtime()
        submit_shift_form(ns)
        with open(SCRIPT_PATH, encoding="utf-8") as source:
            tree = ast.parse(source.read())
        names = {"process_downtime_command", "record_downtime", "build_runtime_snapshot",
                 "checkpoint_runtime_state", "checkpoint_and_queue_event", "runtime_state_status"}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        published = []
        now = [1000]
        with tempfile.TemporaryDirectory() as directory:
            store = ABB7SQLiteStore(os.path.join(directory, "state.db"))
            ns.update(downtime_session=empty_session(), downtime_transition=transition,
                      time=SimpleNamespace(time=lambda: now[0]), current_mode="NORMAL", current_status=0,
                      MODE_NORMAL="NORMAL", MODE_MODEL_CHANGE="MODEL CHANGE", force_delay=False,
                      shift_state_lock=threading.RLock(), last_runtime_checkpoint=0,
                      STATE_CHECKPOINT_INTERVAL_SECONDS=5, persistence_store=store,
                      mqtt_client=SimpleNamespace(publish=lambda topic, data, **kw: published.append(json.loads(data))),
                      get_hour_slot=lambda: "8.00-9.00", shift_total_output=8)
            exec(compile(ast.Module(body=functions, type_ignores=[]), SCRIPT_PATH, "exec"), ns)
            try:
                started = ns["process_downtime_command"]({"action": "start", "revision": 0,
                    "command_id": "start", "data": {"downtimeCategory": "Machine", "machineIssue": "Fault"}})
                self.assertEqual(started["error"], "")
                self.assertEqual(ns["current_mode"], "DOWN")
                saved = store.load_runtime_state("ABB7")
                self.assertEqual(saved["downtime_session"]["id"], started["state"]["id"])
                now[0] += 120
                stopped = ns["process_downtime_command"]({"action": "stop", "revision": 1,
                    "command_id": "stop", "session_id": started["state"]["id"]})
                self.assertEqual(stopped["error"], "")
                self.assertEqual(ns["current_mode"], "NORMAL")
                command = {"action": "log", "revision": 2, "command_id": "log",
                           "session_id": started["state"]["id"], "data": {"actionTaken": "Fixed"}}
                logged = ns["process_downtime_command"](command)
                self.assertEqual(logged["error"], "")
                self.assertEqual(logged["log"]["durationMinutes"], 2)
                replay = ns["process_downtime_command"](command)
                self.assertIsNone(replay["log"])
                self.assertEqual(ns["shift_total_output"], 8)
                self.assertEqual(sum(row.get("tab") == "Downtime_Data" for row in ns["google_sheets_queue"]), 1)
                self.assertEqual(store.load_runtime_state("ABB7")["downtime_session"]["status"], "idle")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
