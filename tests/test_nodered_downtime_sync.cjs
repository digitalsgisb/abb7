const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const named = name => nodes.find(n => n.name === name && n.type !== 'ui-page');
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
const mainMessages = [];
const mainDevice = {...main.data(), isSetupConfirmed: true, send: msg => mainMessages.push(msg)};
main.methods.navigate.call(mainDevice, 'Downtime Checksheet');
assert.deepEqual(mainMessages.map(m => m.topic), ['navigation']);
assert(!main.watch.machineMode, 'Mode state updates can echo commands');
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
console.log('PASS: two-device timer sync, navigation without downtime, shared stop, broadcast scope, duplicate-log protection');
