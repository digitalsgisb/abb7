import ast
import json
import threading
from types import SimpleNamespace
from datetime import datetime, timedelta
import os
import unittest


SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "abb7.py",
)

RESET_COUNTERS = [
    "run_time",
    "loading_time",
    "delay_time",
    "downtime",
    "total_rest_time",
    "planned_stop_time",
    "model_change_time",
    "total_machine_time",
    "total_output",
    "hourly_output",
    "shift_total_output",
    "total_rejects",
    "hourly_rest_time",
    "lost_time_this_hour",
    "base_time_this_hour",
    "real_operating_time",
    "total_real_operating_time",
    "current_cycle_time",
    "batch_run_time",
]


def load_reset_function():
    with open(SCRIPT_PATH, encoding="utf-8") as script_file:
        source = script_file.read()
    tree = ast.parse(source, filename=SCRIPT_PATH)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "reset_production_runtime_counters"
    )
    namespace = {name: 123.0 for name in RESET_COUNTERS}
    namespace.update(
        {
            "sensor_blocked": False,
            "last_sent_hour": 17,
            "current_shift": {"shift_id": "NO PROD", "model": "TEST"},
        }
    )
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), SCRIPT_PATH, "exec"),
        namespace,
    )
    return source, namespace


def load_boundary_functions(current_shift):
    with open(SCRIPT_PATH, encoding="utf-8") as script_file:
        source = script_file.read()
    tree = ast.parse(source, filename=SCRIPT_PATH)
    function_names = {
        "production_day_key",
        "current_shift_matches_production_day",
        "force_production_day_reset",
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in function_names
    ]
    calls = {
        "counter_reset": 0,
        "shift_reset": 0,
        "checkpoint": 0,
        "window_clear": 0,
        "window_start": 0,
    }

    def reset_counters():
        calls["counter_reset"] += 1

    def reset_shift():
        calls["shift_reset"] += 1

    def checkpoint(**_kwargs):
        calls["checkpoint"] += 1

    def clear_window(**_kwargs):
        calls["window_clear"] += 1

    def start_window(_boundary_time):
        calls["window_start"] += 1
        return True

    namespace = {
        "datetime": datetime,
        "timedelta": timedelta,
        "PRODUCTION_DAY_BOUNDARY_HOUR": 8,
        "current_shift": current_shift,
        "last_sent_hour": -1,
        "shift_entry_window_started_at": None,
        "parse_shift_entry_datetime": lambda _value: None,
        "shift_entry_window_is_active": lambda _moment=None: False,
        "clear_shift_entry_window": clear_window,
        "start_shift_entry_window": start_window,
        "reset_production_runtime_counters": reset_counters,
        "reset_shift_data": reset_shift,
        "checkpoint_runtime_state": checkpoint,
    }
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), SCRIPT_PATH, "exec"),
        namespace,
    )
    return source, namespace, calls


def load_entry_window_functions():
    with open(SCRIPT_PATH, encoding="utf-8") as script_file:
        source = script_file.read()
    tree = ast.parse(source, filename=SCRIPT_PATH)
    function_names = {
        "parse_shift_entry_datetime",
        "shift_entry_window_is_active",
        "clear_shift_entry_window",
        "start_shift_entry_window",
        "expire_shift_entry_window",
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in function_names
    ]
    calls = {"counter_reset": 0, "checkpoint": 0}

    def reset_counters():
        calls["counter_reset"] += 1

    def checkpoint(**_kwargs):
        calls["checkpoint"] += 1

    namespace = {
        "datetime": datetime,
        "timedelta": timedelta,
        "SHIFT_ENTRY_GRACE_MINUTES": next(
            node.value.value for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "SHIFT_ENTRY_GRACE_MINUTES"
                    for target in node.targets)
        ),
        "shift_entry_window_started_at": None,
        "shift_entry_window_ends_at": None,
        "shift_total_output": 5,
        "reset_production_runtime_counters": reset_counters,
        "checkpoint_runtime_state": checkpoint,
    }
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), SCRIPT_PATH, "exec"),
        namespace,
    )
    return source, namespace, calls


def load_shift_form_runtime():
    source, namespace, _ = load_entry_window_functions()
    tree = ast.parse(source, filename=SCRIPT_PATH)
    clock = {"now": datetime(2026, 10, 6, 8, 0)}

    class Clock(datetime):
        @classmethod
        def now(cls):
            return clock["now"]

    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"on_message", "reset_shift_data",
                                   "reset_production_runtime_counters", "production_day_key",
                                   "current_shift_matches_production_day",
                                   "force_production_day_reset", "apply_shift_form", "receive_shift_form",
                                   "advance_shift_schedule", "get_current_shift_end_datetime"}]
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name)
                         and (target.id.startswith("MQTT_TOPIC_") or target.id == "LINE_CODE")
                         for target in node.targets)]
    events = []
    namespace.update({
        "datetime": Clock,
        "PRODUCTION_DAY_BOUNDARY_HOUR": 8,
        "json": json,
        "google_sheets_queue": [],
        "coerce_bool": __import__("abb7_shift_schedule").coerce_bool,
        "calculate_shift_start_datetime": __import__("abb7_shift_schedule").calculate_shift_start_datetime,
        "pending_shift_form": None,
        "shift_state_lock": threading.RLock(),
        "publish_shift_form_result": lambda *args, **kwargs: None,
        "hour_slot_for_shift_end": __import__("abb7_shift_schedule").hour_slot_for_shift_end,
        "last_production_day": datetime(2026, 10, 6, 8).date(),
        "calculate_shift_end_datetime": __import__("abb7_shift_schedule").calculate_shift_end_datetime,
        "checkpoint_and_queue_event": lambda *args, **kwargs: events.append(args),
    })
    exec(compile(ast.Module(body=constants + functions, type_ignores=[]),
                 SCRIPT_PATH, "exec"), namespace)
    namespace["reset_shift_data"]()
    namespace["execute_end_shift"] = lambda **kwargs: namespace["reset_shift_data"]()
    return namespace, clock, events


def submit_shift_form(namespace):
    namespace["on_message"](None, None, SimpleNamespace(
        topic=namespace["MQTT_TOPIC_SHIFT_FORM"],
        payload=json.dumps({"prodDate": "2026-10-06", "shift": "Day",
                            "productionLine": "Line 1",
                            "workingTime": "8:00 AM to 4:15 PM"}).encode(),
    ))


class ABB7ShiftRuntimeTests(unittest.TestCase):
    def test_new_shift_reset_removes_idle_carryover(self):
        _, namespace = load_reset_function()

        namespace["reset_production_runtime_counters"]()

        for name in RESET_COUNTERS:
            self.assertEqual(namespace[name], 0, name)
        self.assertTrue(namespace["sensor_blocked"])
        self.assertEqual(namespace["last_sent_hour"], -1)
        self.assertEqual(namespace["current_shift"]["model"], "TEST")

    def test_idle_time_and_idle_sensor_counts_are_gated(self):
        source, _ = load_reset_function()

        self.assertIn(
            "production_loop_delta = loop_delta if production_tracking_enabled else 0.0",
            source,
        )
        self.assertIn("if not sensor_blocked and production_tracking_enabled:", source)
        self.assertIn(
            'previous_shift_id != "NO PROD" and previous_shift_id != new_shift_id',
            source,
        )
        self.assertIn("STATUS_WAITING_FOR_SHIFT = 7", source)

    def test_shift_entry_window_buffers_for_exactly_30_minutes(self):
        _, namespace, calls = load_entry_window_functions()
        boundary = datetime.now()

        self.assertTrue(namespace["start_shift_entry_window"](boundary))
        self.assertTrue(
            namespace["shift_entry_window_is_active"](
                boundary + timedelta(minutes=29, seconds=59)
            )
        )
        self.assertFalse(
            namespace["shift_entry_window_is_active"](
                boundary + timedelta(minutes=30)
            )
        )
        self.assertEqual(calls["counter_reset"], 0)

    def test_expired_shift_entry_window_discards_unassigned_counts(self):
        _, namespace, calls = load_entry_window_functions()
        boundary = datetime.now()
        namespace["start_shift_entry_window"](boundary)

        self.assertTrue(
            namespace["expire_shift_entry_window"](
                boundary + timedelta(minutes=30)
            )
        )
        self.assertEqual(calls["counter_reset"], 1)
        self.assertIsNone(namespace["shift_entry_window_started_at"])
        self.assertIsNone(namespace["shift_entry_window_ends_at"])


    def test_manual_end_starts_window_at_reset_time(self):
        namespace, clock, _ = load_shift_form_runtime()
        namespace["total_machine_time"] = 900
        namespace["on_message"](None, None, SimpleNamespace(
            topic=namespace["MQTT_TOPIC_NR_ENDSHIFT"], payload=b'{"value": true}',
        ))
        self.assertEqual(namespace["total_machine_time"], 0)
        self.assertEqual(namespace["shift_entry_window_started_at"], clock["now"].isoformat())
        self.assertEqual(namespace["shift_entry_window_ends_at"],
                         (clock["now"] + timedelta(minutes=30)).isoformat())

    def test_late_form_preserves_buffered_timers_and_output(self):
        for elapsed in (timedelta(minutes=2), timedelta(minutes=29, seconds=59)):
            with self.subTest(elapsed=elapsed):
                namespace, clock, events = load_shift_form_runtime()
                namespace["start_shift_entry_window"](clock["now"])
                clock["now"] += elapsed
                # Values recorded by the sensor loop while waiting for the form.
                recorded = {"total_machine_time": elapsed.total_seconds(),
                            "run_time": elapsed.total_seconds(),
                            "base_time_this_hour": elapsed.total_seconds(),
                            "total_real_operating_time": elapsed.total_seconds(),
                            "hourly_output": 3, "total_output": 3, "shift_total_output": 3}
                namespace.update(recorded)
                submit_shift_form(namespace)
                self.assertEqual(namespace["current_shift"]["shift_id"], "20261006-Day-Line1")
                self.assertIsNone(namespace["shift_entry_window_ends_at"])
                self.assertEqual(events[-1][0], "shift.started")
                for name, value in recorded.items():
                    self.assertEqual(namespace[name], value, name)
                # Re-submitting the same form must not reset adopted production.
                submit_shift_form(namespace)
                self.assertEqual(events[-1][0], "shift.updated")
                for name, value in recorded.items():
                    self.assertEqual(namespace[name], value, name)

    def test_form_at_or_after_deadline_does_not_adopt_expired_production(self):
        for minutes in (30, 31):
            with self.subTest(minutes=minutes):
                namespace, clock, events = load_shift_form_runtime()
                namespace["start_shift_entry_window"](clock["now"])
                namespace["total_machine_time"] = 120
                namespace["shift_total_output"] = 3
                clock["now"] += timedelta(minutes=minutes)
                submit_shift_form(namespace)
                self.assertEqual(namespace["total_machine_time"], 0)
                self.assertEqual(namespace["shift_total_output"], 0)
                self.assertEqual(events[-1][0], "shift.started")

    def test_no_overnight_rows_and_clean_monday_0800_start(self):
        namespace, clock, _ = load_shift_form_runtime()
        with open(SCRIPT_PATH, encoding="utf-8") as source_file:
            source = source_file.read()
        tree = ast.parse(source, filename=SCRIPT_PATH)
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.While))
        hourly_check = next(node for node in loop.body
                            if isinstance(node, ast.If)
                            and "now.minute" in ast.unparse(node.test))
        timer_start = next(i for i, node in enumerate(loop.body)
                           if isinstance(node, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == "active_shift"
                                   for t in node.targets))
        # Execute the real hourly guard and timer accumulation from the sensor loop.
        timer_nodes = []
        for node in loop.body[timer_start:]:
            if isinstance(node, ast.If):
                break
            timer_nodes.append(node)
        tick = compile(ast.Module(body=[hourly_check] + timer_nodes, type_ignores=[]),
                       SCRIPT_PATH, "exec")
        rows = []
        namespace["push_hourly_to_sheets"] = lambda **kw: rows.append(kw)
        # Sunday's night shift has finalized at Monday 00:30 and cleared its context.
        clock["now"] = datetime(2026, 10, 5, 0, 30)
        namespace["reset_shift_data"]()
        namespace["start_shift_entry_window"](clock["now"])
        namespace["total_machine_time"] = 1800  # Temporary, unassigned buffer.
        for hour in range(1, 8):
            clock["now"] = datetime(2026, 10, 5, hour)
            namespace["expire_shift_entry_window"](clock["now"])
            namespace.update(now=clock["now"], loop_delta=60)
            exec(tick, namespace)
            self.assertEqual(namespace["total_machine_time"], 0)
            self.assertEqual(namespace["base_time_this_hour"], 0)
        self.assertEqual(rows, [])
        clock["now"] = datetime(2026, 10, 5, 8)
        namespace["force_production_day_reset"](clock["now"])
        for name in RESET_COUNTERS:
            self.assertEqual(namespace[name], 0, name)
        self.assertEqual(namespace["shift_entry_window_started_at"], "2026-10-05T08:00:00")
        namespace.update(now=clock["now"] + timedelta(minutes=2), loop_delta=120)
        exec(tick, namespace)
        self.assertEqual(namespace["total_machine_time"], 120)
        self.assertEqual(rows, [])

    def test_early_night_form_waits_for_day_handover(self):
        ns, clock, events = load_shift_form_runtime()
        acknowledgements = []
        ns["publish_shift_form_result"] = lambda *args, **kwargs: acknowledgements.append((args, kwargs))
        submit_shift_form(ns)
        clock["now"] = datetime(2026, 10, 6, 15, 32)
        ns.update(total_machine_time=500, shift_total_output=8)
        night = {"prodDate": "2026-10-06", "shift": "Night", "productionLine": "Line 1",
                 "overtime": False, "workingTime": "4:15 PM to 12:30 AM"}
        ns["receive_shift_form"](night)
        self.assertEqual(ns["current_shift"]["shift"], "Day")
        self.assertEqual(ns["shift_total_output"], 8)
        self.assertEqual(ns["pending_shift_form"], night)
        self.assertEqual(len(events), 1)
        clock["now"] = datetime(2026, 10, 6, 16, 15)
        ns["advance_shift_schedule"](clock["now"])
        self.assertEqual(ns["current_shift"]["shift"], "Night")
        self.assertEqual(ns["current_shift"]["scheduled_end_at"], "2026-10-07T00:30:00")
        self.assertEqual(ns["shift_total_output"], 0)
        self.assertIsNone(ns["pending_shift_form"])
        self.assertEqual(len(events), 2)
        self.assertFalse(acknowledgements[-1][1]["navigate_to_hourly"])
        ns["advance_shift_schedule"](clock["now"])
        self.assertEqual(len(events), 2)

    def test_early_form_with_no_active_shift_does_not_activate(self):
        ns, clock, events = load_shift_form_runtime()
        clock["now"] = datetime(2026, 10, 6, 15, 32)
        ns["receive_shift_form"]({"prodDate": "2026-10-06", "shift": "Night",
                                  "productionLine": "Line 1", "workingTime": "4:15 PM to 12:30 AM"})
        self.assertEqual(ns["current_shift"]["shift_id"], "NO PROD")
        self.assertEqual(events, [])
        self.assertEqual(ns["google_sheets_queue"], [])

    def test_test_commands_do_not_change_live_or_pending_state(self):
        ns, clock, events = load_shift_form_runtime()
        submit_shift_form(ns)
        ns["shift_total_output"] = 8
        shift_before = dict(ns["current_shift"])
        for topic in (ns["MQTT_TOPIC_SHIFT_FORM"], ns["MQTT_TOPIC_SETUP"], ns["MQTT_TOPIC_NR_ENDSHIFT"]):
            ns["on_message"](None, None, SimpleNamespace(topic=topic, payload=json.dumps(
                {"testMode": "true", "value": True, "model": "TEST", "prodDate": "2026-10-06",
                 "shift": "Night", "workingTime": "4:15 PM to 12:30 AM"}).encode()))
        self.assertEqual(ns["current_shift"], shift_before)
        self.assertEqual(ns["shift_total_output"], 8)
        self.assertIsNone(ns["pending_shift_form"])
        self.assertEqual(len(events), 1)
        self.assertEqual(len(ns["google_sheets_queue"]), 1)

    def test_resend_preserves_counts_and_active_schedule(self):
        ns, clock, events = load_shift_form_runtime()
        submit_shift_form(ns)
        ns.update(shift_total_output=8, total_machine_time=120)
        ns["receive_shift_form"]({"action": "resend_active"})
        self.assertEqual(ns["shift_total_output"], 8)
        self.assertEqual(ns["total_machine_time"], 120)
        self.assertEqual(events[-1][0], "shift.updated")
        self.assertEqual(ns["current_shift"]["scheduled_end_at"], "2026-10-06T16:15:00")

    def test_expired_and_overlapping_forms_are_rejected(self):
        ns, clock, events = load_shift_form_runtime()
        submit_shift_form(ns)
        ns["shift_total_output"] = 8
        self.assertFalse(ns["receive_shift_form"]({"prodDate": "2026-10-05", "shift": "Day",
                                                  "workingTime": "8:00 AM to 4:15 PM"}))
        self.assertFalse(ns["receive_shift_form"]({"prodDate": "2026-10-06", "shift": "Day",
                                                  "workingTime": "4:15 PM to 12:30 AM"}))
        ns["current_shift"]["scheduled_end_at"] = "2026-10-06T20:00:00"
        clock["now"] = datetime(2026, 10, 6, 15, 32)
        self.assertFalse(ns["receive_shift_form"]({"prodDate": "2026-10-06", "shift": "Night",
                                                  "workingTime": "4:15 PM to 12:30 AM"}))
        self.assertEqual(ns["shift_total_output"], 8)
        self.assertIsNone(ns["pending_shift_form"])
        self.assertEqual(len(events), 1)

    def test_pending_day_activates_after_0800_reset(self):
        ns, clock, events = load_shift_form_runtime()
        clock["now"] = datetime(2026, 10, 6, 7, 50)
        ns["last_production_day"] = datetime(2026, 10, 5).date()
        form = {"prodDate": "2026-10-06", "shift": "Day", "productionLine": "Line 1",
                "workingTime": "8:00 AM to 4:15 PM"}
        ns["receive_shift_form"](form)
        clock["now"] = datetime(2026, 10, 6, 8, 0)
        ns["advance_shift_schedule"](clock["now"])
        ns["shift_total_output"] = 2
        ns["advance_shift_schedule"](clock["now"] + timedelta(minutes=2))
        self.assertEqual(ns["shift_total_output"], 2)
        self.assertIsNone(ns["pending_shift_form"])
        self.assertEqual(len(events), 1)

    def test_later_ot_change_does_not_stall_loop_on_pending_conflict(self):
        ns, clock, events = load_shift_form_runtime()
        submit_shift_form(ns)
        clock["now"] = datetime(2026, 10, 6, 15, 32)
        ns["receive_shift_form"]({"prodDate": "2026-10-06", "shift": "Night",
                                  "productionLine": "Line 1", "workingTime": "4:15 PM to 12:30 AM"})
        ns["receive_shift_form"]({"prodDate": "2026-10-06", "shift": "Day", "overtime": True,
                                  "productionLine": "Line 1", "workingTime": "8:00 AM to 8:00 PM"})
        clock["now"] = datetime(2026, 10, 6, 16, 15)
        ns["shift_total_output"] = 8
        ns["advance_shift_schedule"](clock["now"])
        self.assertIsNone(ns["pending_shift_form"])
        self.assertEqual(ns["current_shift"]["shift"], "Day")
        self.assertEqual(ns["shift_total_output"], 8)
        self.assertFalse(ns["advance_shift_schedule"](clock["now"]))

    def test_form_received_before_loop_handles_0800_is_not_reset_twice(self):
        ns, clock, events = load_shift_form_runtime()
        ns["last_production_day"] = datetime(2026, 10, 5).date()
        clock["now"] = datetime(2026, 10, 6, 8, 2)
        submit_shift_form(ns)
        ns.update(total_machine_time=120, shift_total_output=2)
        ns["advance_shift_schedule"](clock["now"])
        self.assertEqual(ns["total_machine_time"], 120)
        self.assertEqual(ns["shift_total_output"], 2)

    def test_production_day_changes_at_0800(self):
        _, namespace, _ = load_boundary_functions({"shift_id": "NO PROD"})

        before = namespace["production_day_key"](datetime(2026, 7, 31, 7, 59))
        at_boundary = namespace["production_day_key"](datetime(2026, 7, 31, 8, 0))

        self.assertEqual(str(before), "2026-07-30")
        self.assertEqual(str(at_boundary), "2026-07-31")

    def test_boundary_preserves_valid_day_form_but_resets_counters(self):
        shift = {
            "shift_id": "20260731-Day-Line1",
            "date": "2026-07-31",
            "shift": "Day",
        }
        _, namespace, calls = load_boundary_functions(shift)

        namespace["force_production_day_reset"](datetime(2026, 7, 31, 8, 0))

        self.assertEqual(calls["counter_reset"], 1)
        self.assertEqual(calls["shift_reset"], 0)
        self.assertEqual(calls["checkpoint"], 1)
        self.assertEqual(calls["window_clear"], 1)
        self.assertEqual(calls["window_start"], 0)
        self.assertEqual(namespace["last_sent_hour"], 8)

    def test_boundary_clears_previous_night_context(self):
        shift = {
            "shift_id": "20260730-Night-Line1",
            "date": "2026-07-30",
            "shift": "Night",
        }
        source, namespace, calls = load_boundary_functions(shift)

        namespace["force_production_day_reset"](datetime(2026, 7, 31, 8, 0))

        self.assertEqual(calls["counter_reset"], 0)
        self.assertEqual(calls["shift_reset"], 1)
        self.assertEqual(calls["window_start"], 1)
        self.assertIn("production_day != last_production_day", source)
        self.assertIn("reset_stale_shift_after_startup()", source)


if __name__ == "__main__":
    unittest.main()
