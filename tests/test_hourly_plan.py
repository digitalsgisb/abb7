"""Fixed hourly plan: minutes of the hour (or model/lot segment) inside the shift, less planned time, / cycle."""
import importlib
import os
import sys
import unittest
from datetime import datetime

SYSTEM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SYSTEM_DIR not in sys.path:
    sys.path.insert(0, SYSTEM_DIR)
MODULE = next(name[:-3] for name in os.listdir(SYSTEM_DIR) if name.endswith("_hourly_plan.py"))
plan = importlib.import_module(MODULE)
at = lambda h, m=0, s=0, day=24: datetime(2026, 11, day, h, m, s)


class FixedHourlyPlanTests(unittest.TestCase):
    def test_a_full_hour_is_fixed_from_its_first_minute(self):
        # 1-minute cycle: 60 at 10:00, still 60 at 10:05 and 10:59 (not 5, not 59).
        for now in (at(10, 0, 1), at(10, 5), at(10, 59, 59)):
            self.assertEqual(plan.segment_plan(now, None, at(8), at(20), 0, 1.0)[0], 60)
        self.assertEqual(plan.segment_plan(at(10, 5), None, at(8), at(20), 0, 2.0)[0], 30)

    def test_only_planned_time_lowers_the_plan(self):
        self.assertEqual(plan.segment_plan(at(10, 30), None, at(8), at(20), 15 * 60, 1.0)[0], 45)
        self.assertEqual(plan.PLANNED_MODES, ("REST", "PLANNED STOP", "MODEL CHANGE"))

    def test_part_hours_at_shift_start_and_end_count_only_minutes_inside_the_shift(self):
        self.assertEqual(plan.segment_plan(at(16, 20), None, at(16, 15), at(0, 30, day=25), 0, 1.0)[0], 45)
        self.assertEqual(plan.segment_plan(at(0, 10, day=25), None, at(16, 15), at(0, 30, day=25), 0, 1.0)[0], 30)
        # The OT night keeps full hours past midnight.
        self.assertEqual(plan.segment_plan(at(0, 10, day=25), None, at(20), at(8, day=25), 0, 1.0)[0], 60)

    def test_a_model_change_splits_the_hour_by_minutes(self):
        closed, end = plan.segment_plan(at(10, 20), None, at(8), at(20), 0, 1.0, closing=True)
        self.assertEqual((closed, end), (20, at(10, 20)))
        self.assertEqual(plan.segment_plan(at(10, 25), end.isoformat(), at(8), at(20), 0, 1.0)[0], 40)
        self.assertEqual(plan.segment_plan(at(11, 0, 1), end.isoformat(), at(8), at(20), 0, 1.0, standard=True)[0], 40)

    def test_hourly_push_closes_the_hour_that_just_ended_and_ignores_a_stale_segment_start(self):
        self.assertEqual(plan.segment_plan(at(11, 0, 2), None, at(8), at(20), 0, 1.0, standard=True), (60, at(11)))
        self.assertEqual(plan.segment_plan(at(11, 30), at(9, 40).isoformat(), at(8), at(20), 0, 1.0)[0], 60)

    def test_shift_end_closes_at_the_scheduled_end_and_bad_input_gives_zero(self):
        self.assertEqual(plan.segment_plan(at(20, 0, 1), None, at(8), at(20), 0, 1.0, closing=True), (60, at(20)))
        self.assertEqual(plan.segment_plan(at(10), None, at(8), at(20), 0, 0)[0], 0)
        self.assertEqual(plan.segment_plan(at(10), "not a time", None, None, "x", 1.0)[0], 0)
        self.assertEqual(plan.segment_plan(at(10, 30), None, None, None, 0, 1.0)[0], 60)


if __name__ == "__main__":
    unittest.main()
