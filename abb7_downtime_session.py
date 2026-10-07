"""Shared, restart-safe downtime session transitions; no browser-owned timers."""
import copy
import math
import uuid


def empty_session():
    return {"revision": 0, "id": None, "status": "idle", "data": {},
            "started_ms": None, "stopped_ms": None, "target_ms": None,
            "elapsed_ms": 0, "timer_mode": "stopwatch", "commands": []}


def derived_mode(data):
    category = data.get("downtimeCategory")
    if category == "Planned Stop":
        return "PLANNED STOP"
    if category == "Schedule":
        return "MODEL CHANGE" if "model change" in str(data.get("cleanReasonName", "")).lower() else "PLANNED STOP"
    return "DOWN"


def validate_data(data):
    if data.get("downtimeCategory") not in {"Machine", "Quality", "Production", "Schedule", "Planned Stop"}:
        raise ValueError("Select a downtime category.")
    if data["downtimeCategory"] == "Machine" and not str(data.get("machineIssue", "")).strip():
        raise ValueError("Enter the machine issue before starting.")
    if data["downtimeCategory"] not in {"Machine", "Planned Stop"} and not str(data.get("cleanReasonName", "")).strip():
        raise ValueError("Select a specific reason.")


def log_payload(state, automatic=False):
    data = state["data"]
    category = data["downtimeCategory"]
    description = data.get("machineIssue") if category == "Machine" else data.get("cleanReasonName", category)
    code = "N/A"
    if category != "Machine" and "-" in description:
        code, description = [part.strip() for part in description.split("-", 1)]
    duration = max(1, math.ceil(state["elapsed_ms"] / 60000))
    action = str(data.get("actionTaken", "")).strip()
    if automatic and not action:
        action = "Automatically closed; action not recorded."
    return {"event_id": "downtime-" + state["id"], "category": category, "code": code,
            "description": description, "durationMinutes": duration,
            "actionTaken": action, "remarks": data.get("remarks", ""),
            "session_id": state["id"], "shift_id": state.get("shift_id")}


def transition(previous, command, now_ms, shift_id, automatic=False):
    """Return new state and effects; invalid/stale commands never mutate input."""
    state = copy.deepcopy(previous or empty_session())
    action = command.get("action")
    effects = {"mode": None, "log": None, "event": None}
    if action == "get":
        return state, effects
    command_id = command.get("command_id")
    if command_id and command_id in state.get("commands", []):
        return state, effects
    if not automatic:
        if command.get("revision") != state["revision"]:
            raise ValueError("Another device updated this session. Review the latest state and try again.")
        if action not in {"start", "update", "mode"} and command.get("session_id") != state["id"]:
            raise ValueError("This downtime session is no longer current.")
    if action == "mode":
        if state["status"] == "running":
            raise ValueError("Stop the shared downtime timer before changing machine mode.")
        mode = str(command.get("data", {}).get("mode", "")).upper()
        if mode not in {"NORMAL", "REST"}:
            raise ValueError("Select Normal or Rest.")
        effects["mode"] = mode
    elif action == "update":
        incoming = command.get("data", {})
        if state["status"] in {"running", "stopped"}:
            allowed = {"actionTaken", "remarks"}
            if any(k not in allowed and incoming[k] != state["data"].get(k) for k in incoming):
                raise ValueError("Finish or cancel the current session before changing its reason.")
        state["data"].update(incoming)
    elif action == "start":
        if shift_id == "NO PROD":
            raise ValueError("Start an active production shift first.")
        if state["status"] in {"running", "stopped"}:
            raise ValueError("A shared downtime session already exists. Log or cancel it first.")
        data = dict(command.get("data", {}))
        validate_data(data)
        preset = float(data.get("presetTime") or 0)
        if not math.isfinite(preset) or preset < 0:
            raise ValueError("Enter a valid preset duration.")
        state.update(id=uuid.uuid4().hex, status="running", data=data, shift_id=shift_id,
                     started_ms=now_ms, stopped_ms=None, elapsed_ms=0,
                     target_ms=now_ms + preset * 60000 if preset > 0 else None,
                     timer_mode="countdown" if preset > 0 else "stopwatch")
        effects.update(mode=derived_mode(data), event="started")
    elif action in {"stop", "log", "cancel", "expire", "shift_end"}:
        if action == "cancel" and state["status"] == "idle":
            state["data"] = {}
            state["revision"] += 1
            return state, effects
        if action == "log" and state["status"] == "idle":
            data = dict(command.get("data", {}))
            validate_data(data)
            minutes = float(data.get("durationMinutes") or 0)
            if not math.isfinite(minutes) or minutes <= 0 or shift_id == "NO PROD":
                raise ValueError("Enter a positive duration and use an active production shift.")
            state.update(id=uuid.uuid4().hex, data=data, status="stopped", shift_id=shift_id,
                         elapsed_ms=minutes * 60000, started_ms=None, stopped_ms=now_ms)
        if state["status"] not in {"running", "stopped"}:
            raise ValueError("No current downtime session to stop or log.")
        if state["status"] == "running":
            end = min(now_ms, state["target_ms"]) if action == "expire" and state["target_ms"] else now_ms
            state.update(elapsed_ms=max(0, end - state["started_ms"]), stopped_ms=end)
        if action == "stop":
            if state["status"] != "running":
                return state, effects
            state["status"] = "stopped"
            effects.update(mode="NORMAL", event="stopped")
        elif action == "cancel":
            state.update(status="idle", data={}, id=None, started_ms=None, stopped_ms=None,
                         target_ms=None, elapsed_ms=0)
            effects.update(mode="NORMAL", event="cancelled")
        else:
            if action == "log":
                state["data"].update({k: v for k, v in command.get("data", {}).items() if k in {"actionTaken", "remarks"}})
                if not str(state["data"].get("actionTaken", "")).strip():
                    raise ValueError("Enter the action taken before logging downtime.")
            effects.update(mode="NORMAL", event="logged", log=log_payload(state, automatic))
            state.update(status="idle", data={}, id=None, started_ms=None, stopped_ms=None,
                         target_ms=None, elapsed_ms=0)
    else:
        raise ValueError("Unknown downtime command.")
    state["revision"] += 1
    if command_id:
        state["commands"] = (state.get("commands", []) + [command_id])[-64:]
    return state, effects
