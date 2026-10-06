import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "flow_update", Path(__file__).resolve().parents[1] / "deploy" / "update_nodered_shift_end.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NodeRedUpdateTests(unittest.TestCase):
    def test_removes_manual_reset_and_preserves_private_configuration(self):
        nodes = [
            {"id": "main", "name": "main", "format":
             '          <v-btn color="red"\n            @click="showEndShiftConfirm = true">END SHIFT</v-btn>\n'
             '    <v-dialog v-model="showEndShiftConfirm">Confirm</v-dialog>\n'
             '      showEndShiftConfirm: false,\n'
             "      this.sendUpdate('shift_ended', true);", "wires": [["route"]]},
            {"id": "route", "rules": [{"v": "shift_ended"}, {"v": "other"}],
             "wires": [["cleanup", "disable", "api"], ["private"]], "outputs": 2},
            {"id": "clock", "name": "check if shift end", "func": "old", "wires": [["main", "prep"]]},
            {"id": "schedule", "name": "Set Shift End Time", "func": "old"},
            {"id": "prep", "name": "Prep End Shift Broadcast", "func": "MQTT + all templates",
             "wires": [["mqtt", "main"]]},
            {"id": "disable", "name": "disable auto clock", "wires": [["mqtt"]]},
            {"id": "mqtt", "topic": "nodered/endshift"},
            {"id": "cleanup"}, {"id": "api"},
            {"id": "private", "local_setting": "preserve-this"},
        ]
        result = module.update(copy.deepcopy(nodes))
        by_id = {n["id"]: n for n in result}
        self.assertNotIn("mqtt", by_id)
        self.assertNotIn("disable", by_id)
        self.assertNotIn("showEndShiftConfirm", by_id["main"]["format"])
        self.assertNotIn("sendUpdate", by_id["main"]["format"])
        self.assertEqual(by_id["clock"]["wires"], [["main", "prep", "cleanup", "api"]])
        self.assertEqual(by_id["route"]["wires"], [["private"]])
        self.assertEqual(by_id["route"]["outputs"], 1)
        self.assertEqual(by_id["private"], nodes[-1])
        self.assertEqual(module.update(copy.deepcopy(result)), result)
