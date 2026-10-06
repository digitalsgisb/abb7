// Run against the generated private flow: node tests/test_nodered_shift_ui.cjs updated-flow.json
const assert = require('node:assert/strict');
const fs = require('node:fs');

async function main() {
  const nodes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const named = name => nodes.find(node => node.name === name);
  const byId = new Map(nodes.map(node => [node.id, node]));
  assert.equal(byId.size, nodes.length);
  for (const node of nodes) {
    for (const target of (node.wires || []).flat()) assert(byId.has(target), `Missing wire ${target}`);
    if (node.type === 'function') new Function('msg', 'node', 'flow', 'global', node.func);
    if (node.type === 'switch') assert.equal(node.rules.length, node.wires.length);
  }
  const component = name => new Function(named(name).format.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace('export default', 'return'))();
  const front = component('front');
  const dashboard = component('main');
  const messages = [];
  const context = {
    ...front.data(), testMode: true, formData: {shift: 'Night', prodDate: '2026-10-06'},
    calculatedWorkingTime: '4:15 PM to 12:30 AM', send: m => messages.push(m),
  };
  await front.methods.submitForm.call(context);
  assert.equal(messages.length, 0, 'Test preview must not send commands');
  assert(context.formFeedback.includes('TEST PREVIEW'));
  // A test edit must not overwrite the production form cached by the browser.
  global.localStorage = {setItem: () => assert.fail('Test mode wrote production storage')};
  front.watch.formData.handler.call(context, context.formData);

  context.testMode = false;
  context.$refs = {conversionForm: {validate: async () => ({valid: true})}};
  context.msg = {};
  global.localStorage = {setItem: () => {}};
  await front.methods.submitForm.call(context);
  assert.deepEqual(messages.map(m => m.topic), ['form_data']);
  const gate = new Function('msg', named('Submit live form to Python').func);
  assert.equal(gate({topic: 'form_data', payload: {testMode: true}}), null);
  assert.equal(gate({topic: 'form_data', payload: {testMode: 'true'}}), null);
  assert.equal(JSON.parse(gate(messages[0]).payload).shift, 'Night');
  assert.equal(JSON.parse(gate({topic: 'resend_active_shift', payload: {}}).payload).action, 'resend_active');

  const decide = new Function('msg', named('Apply accepted shift only').func);
  const pending = decide({payload: {status: 'pending', message: 'Waiting until 16:15'}});
  assert.equal(pending[0], null, 'Pending form reached production writers');
  assert.equal(pending[2], null, 'Pending form navigated to live controls');
  const rejected = decide({payload: {status: 'rejected'}});
  assert.equal(rejected[0], null);
  const accepted = decide({payload: {status: 'active', source_payload: messages[0].payload}});
  assert.equal(accepted[0].topic, 'form_data');
  assert.equal(accepted[2].topic, 'navigate');
  const ended = decide({payload: {status: 'ended'}});
  assert.equal(ended[3].topic, 'trigger_auto_end_shift');
  // There must be no independent clock capable of resetting the UI or server session.
  assert.equal(new Function('msg', named('check if shift end').func)({}), null);
  const out = nodes.find(n => n.type === 'mqtt out' && n.topic === 'nodered/newshift');
  const senders = nodes.filter(n => (n.wires || []).flat().includes(out.id));
  assert.deepEqual(senders.map(n => n.name), ['Submit live form to Python']);
  const state = {...dashboard.data(), isSetupConfirmed: false};
  dashboard.watch.msg.call(state, {topic: 'shift_runtime', payload: {
    shift_id: '20261006-Day-MF', shift: 'Day', overtime: false, date: '2026-10-06',
    scheduled_end_at: '2026-10-06T16:15:00', pending_shift: messages[0].payload,
    persistence_enabled: false,
  }});
  assert(state.activeShiftLabel.includes('Day'));
  assert(state.shiftResetAt.includes('16:15'));
  assert(state.pendingShiftLabel.includes('Night'));
  assert(state.storageWarning.includes('SQLite'));
  console.log('PASS: preview isolation, accepted-only writes, resend, runtime display, JS syntax and flow wiring');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
