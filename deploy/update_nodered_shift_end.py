"""Update a private Node-RED export for automatic shift endings.

Usage: python3 deploy/update_nodered_shift_end.py input.json output.json
Existing credentials/configuration stay in the local export, outside Git.
"""
import json
from pathlib import Path
import re
import sys
import importlib.util

_ui_spec = importlib.util.spec_from_file_location("shift_ui", Path(__file__).with_name("nodered_shift_ui.py"))
_ui_module = importlib.util.module_from_spec(_ui_spec)
_ui_spec.loader.exec_module(_ui_module)

FUNCTIONS = {'Set Shift End Time': "// Night shifts finish on the day after the selected production date.\nconst data = msg.payload || {};\nconst shift = String(data.shift || '').trim().toUpperCase();\nconst ot = data.overtime === true || data.overtime === 1 ||\n    ['1', 'true', 'yes', 'on', 'ot'].includes(String(data.overtime).trim().toLowerCase());\nconst match = /^(\\d{4})-?(\\d{2})-?(\\d{2})$/.exec(String(data.prodDate || ''));\nif (!match || !['DAY', 'NIGHT'].includes(shift)) {\n    node.warn('Cannot schedule shift end: invalid production date or shift.');\n    return null;\n}\nconst year = Number(match[1]), month = Number(match[2]) - 1, day = Number(match[3]);\nconst date = new Date(year, month, day);\nif (date.getFullYear() !== year || date.getMonth() !== month || date.getDate() !== day) {\n    node.warn('Cannot schedule shift end: invalid production date.');\n    return null;\n}\nconst endHour = shift === 'DAY' ? (ot ? 20 : 16) : (ot ? 8 : 0);\nconst endMinute = ot ? 0 : (shift === 'DAY' ? 15 : 30);\nconst deadline = new Date(year, month, day + (shift === 'NIGHT' ? 1 : 0), endHour, endMinute);\nglobal.set('shiftEndAt', deadline.getTime());\nglobal.set('shiftActive', true);\nreturn msg;\n", 'check if shift end': '// Python owns machine-counter finalization; this clock ends the UI session.\nconst deadline = global.get(\'shiftEndAt\');\nif (global.get(\'shiftActive\') !== true || !Number.isFinite(deadline)) return null;\nif (Date.now() < deadline) return null;\n// Consume before broadcasting, even with no dashboard connected.\nglobal.set(\'shiftActive\', false);\nmsg.topic = "trigger_auto_end_shift";\nmsg.payload = { value: true };\nreturn msg;\n'}


def update(nodes):
    def named(name):
        matches = [n for n in nodes if n.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"Expected one {name!r} node, found {len(matches)}")
        return matches[0]

    main = named("main")
    template = main["format"]
    template = re.sub(r'          <v-btn[^>]*\n\s*@click="showEndShiftConfirm = true">.*?</v-btn>\n',
                      "", template, count=1, flags=re.S)
    template = re.sub(r'    <v-dialog v-model="showEndShiftConfirm".*?</v-dialog>\n',
                      "", template, count=1, flags=re.S)
    template = template.replace("      showEndShiftConfirm: false,\n", "")
    template = template.replace("      this.sendUpdate('shift_ended', true);",
                                "      // Python finalizes machine counters at the scheduled shift end.")
    template = template.replace("// Trigger the manual endShift animation & clear sequence",
                                "// Clear the dashboard after the scheduled shift end")
    if "showEndShiftConfirm" in template:
        raise ValueError("Unrecognized End Shift UI; no output written")
    main["format"] = template
    removed = {n["id"] for n in nodes if n.get("topic") == "nodered/endshift"
               or n.get("name") == "disable auto clock"}
    route = next(n for n in nodes if n["id"] == main["wires"][0][0])
    checker = named("check if shift end")
    for i in reversed(range(len(route["rules"]))):
        if route["rules"][i].get("v") == "shift_ended":
            for target in route["wires"][i]:
                if target not in removed and target not in checker["wires"][0]:
                    checker["wires"][0].append(target)
            route["rules"].pop(i)
            route["wires"].pop(i)
    route["outputs"] = len(route["wires"])
    for name, func in FUNCTIONS.items():
        named(name)["func"] = func
    prep = named("Prep End Shift Broadcast")
    prep["func"] = prep["func"].replace("MQTT + all templates",
                                      "dashboard templates (Python owns machine resets)")
    nodes = [n for n in nodes if n["id"] not in removed]
    for n in nodes:
        if "wires" in n:
            n["wires"] = [[t for t in ws if t not in removed] for ws in n["wires"]]
    return _ui_module.add_shift_ui(nodes)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: update_nodered_shift_end.py input.json output.json")
    source, output = map(Path, sys.argv[1:])
    if source.resolve() == output.resolve():
        raise SystemExit("Use a different output path to preserve the original export")
    nodes = update(json.loads(source.read_text(encoding="utf-8-sig")))
    output.write_text(json.dumps(nodes, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    print(f"Updated flow written to {output}")
