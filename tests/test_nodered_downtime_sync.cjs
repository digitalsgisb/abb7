const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const named = name => nodes.find(n => n.name === name && n.type === 'ui-template') || nodes.find(n => n.name === name && !['ui-page', 'ui-group'].includes(n.type));
const component = name => new Function(named(name).format.match(/<script>([\s\S]*?)<\/script>/)[1].replace('export default', 'return'))();
const down = component('Downtime Log UI');
const makeDevice = () => {
  const messages = [];
  const device = {...down.data(), $nextTick: f => f(), send: msg => messages.push(msg)};
  for (const [name, method] of Object.entries(down.methods)) device[name] = method.bind(device);
  for (const [name, computed] of Object.entries(down.computed)) Object.defineProperty(device, name, {get: () => computed.call(device)});
  return {device, messages};
};
const pc = makeDevice(), tablet = makeDevice();
const state = {revision: 1, id: 'shared-session', status: 'running', started_ms: Date.now() - 120000,
  stopped_ms: null, elapsed_ms: 0, target_ms: null, data: {downtimeCategory: 'Machine', machineIssue: 'Fault'}};
const reply = {state, server_ms: Date.now(), mode: 'DOWN'};
pc.device.applyShared(reply);
tablet.device.applyShared(reply);
assert(pc.device.timerRunning && tablet.device.timerRunning);
assert.equal(pc.device.sessionId, tablet.device.sessionId);
assert(Math.abs(pc.device.elapsedMs - tablet.device.elapsedMs) < 100);
assert.equal(pc.messages.length + tablet.messages.length, 0, 'Receiving state sent mode commands');
tablet.device.toggleTimer();
assert.equal(tablet.messages[0].topic, 'downtime_command');
assert.equal(tablet.messages[0].payload.action, 'stop');
const stopped = {...reply, command_id: tablet.device.commandId,
  state: {...state, revision: 2, status: 'stopped', stopped_ms: Date.now(), elapsed_ms: 120000}};
pc.device.applyShared(stopped);
tablet.device.applyShared(stopped);
assert(!pc.device.timerRunning && !tablet.device.timerRunning);
assert.equal(pc.device.formData.durationMinutes, 2);
assert.equal(tablet.device.formData.durationMinutes, 2);
assert.equal(tablet.device.busy, false);
// Opening the downtime page and selecting Machine must not send a mode change.
const idle = makeDevice();
idle.device.ready = true;
idle.device.lastServerSeen = Date.now();
idle.device.setCategory('Machine');
assert.equal(idle.messages.length, 0);
assert(!idle.device.timerRunning);
const main = component('main');
assert.equal(typeof down.unmounted, 'function', 'Downtime cleanup uses an unsupported hook');
assert.equal(typeof main.unmounted, 'function', 'Clock cleanup uses an unsupported hook');
const mainMessages = [];
const mainDevice = {...main.data(), isSetupConfirmed: false, send: msg => mainMessages.push(msg)};
main.methods.navigate.call(mainDevice, 'Downtime Checksheet');
assert.deepEqual(mainMessages.map(m => m.topic), ['navigation']);
assert(!main.watch.machineMode, 'Mode state updates can echo commands');
mainMessages.length = 0;
main.methods.modeChanged.call(mainDevice, 'Rest');
assert.deepEqual(mainMessages, [{topic: 'machine_mode', payload: 'Rest'}]);
main.methods.modeChanged.call(mainDevice, 'Normal');
assert.equal(mainMessages[1].payload, 'Normal');
mainDevice.sharedDowntimeRunning = true;
main.methods.modeChanged.call(mainDevice, 'Rest');
assert.equal(mainMessages.length, 2, 'Rest bypassed a running downtime session');
for (const mode of ['Normal', 'Rest']) {
  assert(named('main').format.includes(`@click="modeChanged('${mode}')"`), 'Missing explicit mode button handler');
}
const byId = new Map(nodes.map(n => [n.id, n]));
const home = byId.get(named('main').wires[0][0]);
const gate = byId.get(home.wires[1][0]);
const routeCommand = new Function('msg', gate.func);
const routed = routeCommand({topic: 'machine_mode', payload: 'Rest'});
assert.equal(routed[1].payload, 'Rest');
const route = byId.get(gate.wires[1][0]);
const modeIndex = route.rules.findIndex(r => r.v === 'machine_mode');
assert(route.wires[modeIndex].some(id => byId.get(id).topic === 'nodered/mode'), 'Rest did not reach Python mode topic');

// No client-side countdown completion or shift-end handler may log independently.
assert(!named('Downtime Log UI').format.includes('topic: "mode_update"'));
assert(!named('Downtime Log UI').format.includes('handleAutoEndShift'));
assert(!named('Downtime Log UI').format.includes('localStorage.setItem'));
const context = new Map();
const flow = {get: k => context.get(k), set: (k, v) => context.set(k, v)};
const broadcast = new Function('msg', 'flow', named('Broadcast shared downtime').func);
const response = {state: stopped.state, server_ms: Date.now(), mode: 'NORMAL', mode_changed: true,
  event: 'logged', log: {event_id: 'once'}, client: {socketId: 'tablet'}};
const result = broadcast({payload: response, _client: {socketId: 'pc'}}, flow);
assert(!result[0]._client, 'State broadcast was limited to one device');
assert.equal(result[1].topic, 'downtime_data');
assert.equal(result[2].payload, 'NORMAL');
assert.equal(result[3]._client.socketId, 'tablet');
const duplicate = broadcast({payload: response}, flow);
assert.equal(duplicate[1], null);
assert.equal(duplicate[2], null);
assert.equal(duplicate[3], null);
const cancelResult = broadcast({payload: {...response, event: 'cancelled', log: null,
  state: {...response.state, revision: 20}}}, flow);
assert.equal(cancelResult[3].payload.page, 'Smart Checksheet');
assert.equal(cancelResult[3]._client.socketId, 'tablet');
assert.equal(cancelResult[1], null, 'Cancel unexpectedly logged downtime');
const idleCancelResult = broadcast({payload: {...response, event: 'cancelled', log: null,
  mode_changed: false, state: {...response.state, revision: 21, status: 'idle'}}}, flow);
assert.equal(idleCancelResult[3].payload.page, 'Smart Checksheet');
const reject = component('Reject');
const rejectMessages = [];
const rejectDevice = {...reject.data(), send: msg => rejectMessages.push(msg)};
rejectDevice.resetForm = reject.methods.resetForm.bind(rejectDevice);
reject.methods.cancelReject.call(rejectDevice);
assert.deepEqual(rejectMessages, [{topic: 'navigate', payload: {page: 'Smart Checksheet'}}]);
const rejectHome = byId.get(named('Reject').wires[0][0]);
const rejectSwitch = byId.get(rejectHome.wires[1][0]);
const rejectNavIndex = rejectSwitch.rules.findIndex(r => r.v === 'navigate');
assert(rejectSwitch.wires[rejectNavIndex].some(id => byId.get(id).type === 'ui-control'));
console.log('PASS: two-device timer sync, navigation without downtime, shared stop, broadcast scope, duplicate-log protection');
