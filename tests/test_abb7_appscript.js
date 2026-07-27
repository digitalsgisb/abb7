const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const scriptPath = path.join(__dirname, "..", "abb7appscript.js");
const source = fs.readFileSync(scriptPath, "utf8");
const context = {
  console,
  Number,
  Object,
  String,
  Math,
  Date,
  JSON,
  isNaN,
  parseInt,
};

vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

assert.equal(context.parseWorkingTimeStartHour("8:00 AM to 4:15 PM", "Day"), 8);
assert.equal(context.parseWorkingTimeStartHour("4:15 PM to 12:30 AM", "Night"), 16);
assert.equal(context.parseWorkingTimeStartHour("8:00 PM to 8:00 AM", "Night"), 20);

const nightNoOtMap = context.buildShiftRowMap(16);
assert.equal(nightNoOtMap["16"], 10);
assert.equal(nightNoOtMap["23"], 24);
assert.equal(nightNoOtMap["0"], 26);

const hourlyRows = [
  ["Shift_ID", "Date", "Model", "Hour Slot", "Plan", "Actual", "Lot Number", "Rest Time"],
  ["SHIFT-1", "2026-07-24", "D66B", "16.00-17.00", 10, 8, "LOT-A", 2.5],
  ["SHIFT-1", "2026-07-24", "L02D", "16:00-17:00", 5, 4, "LOT-B", 1.5],
  ["SHIFT-1", "2026-07-24", "D66B", "16.00 - 17.00", 2, 1, "LOT-A", 0],
  ["OTHER", "2026-07-24", "D66B", "16.00-17.00", 99, 99, "LOT-X", 99],
];
const hourly = context.aggregateHourlyData(hourlyRows, "SHIFT-1", nightNoOtMap);
assert.equal(hourly.hours["16"].plan, 17);
assert.equal(hourly.hours["16"].actual, 13);
assert.equal(hourly.hours["16"].restMinutes, 4);
assert.equal(hourly.hours["16"].segments["D66B\u001fLOT-A"].plan, 12);
assert.equal(hourly.hours["16"].segments["D66B\u001fLOT-A"].actual, 9);
assert.equal(hourly.modelTotals.D66B, 9);
assert.equal(hourly.modelTotals.L02D, 4);
const printableSegments = context.getHourSegments(hourly.hours["16"]);
assert.equal(printableSegments.length, 2);
assert.equal(printableSegments[0].model, "D66B");
assert.equal(printableSegments[0].plan, 12);
assert.equal(printableSegments[0].actual, 9);
assert.equal(printableSegments[1].model, "L02D");
assert.equal(printableSegments[1].plan, 5);
assert.equal(printableSegments[1].actual, 4);

const rejectRows = [
  ["Shift_ID", "Hour Slot", "Slab", "Slab Code", "Return", "OHT", "NG", "NG Code", "Loft", "Loft Code"],
  ["SHIFT-1", "16.00-17.00", 1, "S1", 0, "", 2, "D2", 0, ""],
  ["SHIFT-1", "16.00-17.00", 3, "S2", 1, "OHT-1", 4, "D3", 2, "L1"],
];
const rejects = context.aggregateRejectData(rejectRows, "SHIFT-1", nightNoOtMap);
assert.equal(rejects["16"].slab, 4);
assert.equal(rejects["16"].returnRoll, 1);
assert.equal(rejects["16"].rejectNg, 6);
assert.deepEqual(Array.from(rejects["16"].slabCodes), ["S1", "S2"]);

const downtimeRows = [
  ["Shift_ID", "Hour Slot", "Category", "Code", "Duration", "Description", "Remarks"],
  ["SHIFT-1", "16.00-17.00", "Machine", "M1", 5, "Jam", "First stop"],
  ["SHIFT-1", "16.00-17.00", "Mechanical", "M2", 7, "Belt", "Second stop"],
  ["SHIFT-1", "16.00-17.00", "Schedule", "Y1", 3, "Meeting", ""],
];
const downtime = context.aggregateDowntimeData(downtimeRows, "SHIFT-1", nightNoOtMap);
assert.equal(downtime["16"].machine.duration, 12);
assert.deepEqual(Array.from(downtime["16"].machine.codes), ["M1", "M2"]);
assert.equal(downtime["16"].schedule.duration, 3);
assert.equal(downtime["16"].remarks.length, 3);

const shiftRows = [
  ["Shift_ID", "Date", "Line", "Shift", "Group", "Working_time"],
  ["SHIFT-1", "old", "Line 1", "Day", "-", "8:00 AM to 4:15 PM"],
  ["SHIFT-1", "new", "Line 1", "Day", "-", "8:00 AM to 8:00 PM"],
];
assert.equal(context.findLatestShiftRow(shiftRows, "SHIFT-1")[1], "new");

console.log("ABB 7 Apps Script aggregation tests passed.");
