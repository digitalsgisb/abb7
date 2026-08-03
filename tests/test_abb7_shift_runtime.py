import ast
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
        "SHIFT_ENTRY_GRACE_MINUTES": 45,
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

    def test_shift_entry_window_buffers_for_exactly_45_minutes(self):
        _, namespace, calls = load_entry_window_functions()
        boundary = datetime.now()

        self.assertTrue(namespace["start_shift_entry_window"](boundary))
        self.assertTrue(
            namespace["shift_entry_window_is_active"](
                boundary + timedelta(minutes=44, seconds=59)
            )
        )
        self.assertFalse(
            namespace["shift_entry_window_is_active"](
                boundary + timedelta(minutes=45)
            )
        )
        self.assertEqual(calls["counter_reset"], 0)

    def test_expired_shift_entry_window_discards_unassigned_counts(self):
        _, namespace, calls = load_entry_window_functions()
        boundary = datetime.now()
        namespace["start_shift_entry_window"](boundary)

        self.assertTrue(
            namespace["expire_shift_entry_window"](
                boundary + timedelta(minutes=45)
            )
        )
        self.assertEqual(calls["counter_reset"], 1)
        self.assertIsNone(namespace["shift_entry_window_started_at"])
        self.assertIsNone(namespace["shift_entry_window_ends_at"])

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
        self.assertIn("current_production_day != last_production_day", source)
        self.assertIn("reset_stale_shift_after_startup()", source)


if __name__ == "__main__":
    unittest.main()
