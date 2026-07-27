import os
import sys
import unittest
from datetime import datetime


ABB7_DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ABB7_DIRECTORY not in sys.path:
    sys.path.insert(0, ABB7_DIRECTORY)

from abb7_shift_schedule import (
    calculate_shift_end_datetime,
    coerce_bool,
    hour_slot_for_shift_end,
)


class ABB7ShiftScheduleTests(unittest.TestCase):
    def test_day_without_overtime(self):
        shift_end = calculate_shift_end_datetime(
            "2026-07-03", "Day", False, "8:00 AM to 4:15 PM"
        )
        self.assertEqual(shift_end, datetime(2026, 7, 3, 16, 15))
        self.assertEqual(hour_slot_for_shift_end(shift_end), "16.00-17.00")

    def test_day_with_overtime(self):
        shift_end = calculate_shift_end_datetime(
            "2026-07-03", "Day", True, "8:00 AM to 8:00 PM"
        )
        self.assertEqual(shift_end, datetime(2026, 7, 3, 20, 0))
        self.assertEqual(hour_slot_for_shift_end(shift_end), "19.00-20.00")

    def test_night_without_overtime_crosses_midnight(self):
        shift_end = calculate_shift_end_datetime(
            "2026-07-03", "Night", False, "4:15 PM to 12:30 AM"
        )
        self.assertEqual(shift_end, datetime(2026, 7, 4, 0, 30))
        self.assertEqual(hour_slot_for_shift_end(shift_end), "0.00-1.00")

    def test_night_with_overtime_crosses_midnight(self):
        shift_end = calculate_shift_end_datetime(
            "2026-07-03", "Night", True, "8:00 PM to 8:00 AM"
        )
        self.assertEqual(shift_end, datetime(2026, 7, 4, 8, 0))
        self.assertEqual(hour_slot_for_shift_end(shift_end), "7.00-8.00")

    def test_fallback_schedule_and_string_boolean(self):
        shift_end = calculate_shift_end_datetime(
            "20260703", "Day", "false", "-"
        )
        self.assertFalse(coerce_bool("false"))
        self.assertEqual(shift_end, datetime(2026, 7, 3, 16, 15))


if __name__ == "__main__":
    unittest.main()

