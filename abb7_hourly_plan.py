"""Fixed hourly plan for the Smart Checksheet.

The plan of an hour (or of the part of it one model and lot ran) is fixed from its start:

    minutes of that hour inside the shift and inside this model/lot segment
    - planned minutes (rest, planned stop, schedule / model change)
    = available minutes, divided by the standard cycle (minutes per piece)

Machine, quality and production downtime never lower the plan: it shows as the
reason the actual fell short. Pure functions only; no GPIO, MQTT or files.
"""
from datetime import datetime, timedelta

# Modes whose time comes off the plan. DOWN (machine/quality/production) is not one of them.
PLANNED_MODES = ("REST", "PLANNED STOP", "MODEL CHANGE")


def local_time(value):
    """A naive local datetime from a datetime or ISO text; None when missing or unreadable."""
    if value in (None, "", "-"):
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def floor_hour(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def segment_window(now, segment_started_at=None, shift_start=None, shift_end=None, closing=False, standard=False):
    """Start and end of the segment the plan covers.

    standard: the hourly push at hh:00 closes the hour that just ended.
    closing: a model/lot change or the shift end closes the segment now.
    otherwise: the live plan for the running segment, to the end of this hour or the shift.
    """
    now, shift_start, shift_end = local_time(now), local_time(shift_start), local_time(shift_end)
    if standard:
        end = floor_hour(now)
        hour_start = end - timedelta(hours=1)
    elif closing:
        end = min(now, shift_end) if shift_end else now
        hour_start = floor_hour(end - timedelta(microseconds=1))
    else:
        hour_start = floor_hour(now)
        end = hour_start + timedelta(hours=1)
        if shift_end:
            end = min(end, shift_end)
    start = max(moment for moment in (hour_start, local_time(segment_started_at), shift_start) if moment is not None)
    return start, max(start, end)


def fixed_plan(start, end, planned_lost_seconds, standard_cycle_minutes):
    try:
        cycle = float(standard_cycle_minutes)
        lost = max(0.0, float(planned_lost_seconds or 0.0))
    except (TypeError, ValueError):
        return 0
    if cycle <= 0:
        return 0
    minutes = max(0.0, (end - start).total_seconds() - lost) / 60.0
    return int(minutes / cycle + 1e-9)


def segment_plan(now, segment_started_at, shift_start, shift_end, planned_lost_seconds, standard_cycle_minutes,
                 closing=False, standard=False):
    """(plan, segment end) for the current segment."""
    start, end = segment_window(now, segment_started_at, shift_start, shift_end, closing=closing, standard=standard)
    return fixed_plan(start, end, planned_lost_seconds, standard_cycle_minutes), end
