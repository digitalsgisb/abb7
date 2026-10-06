"""Add isolated form preview and Python-authoritative shift routing to private flows."""
import copy
import re


GATE = """if (!['form_data', 'resend_active_shift'].includes(msg.topic)) return null;
const data = msg.payload || {};
if (data.testMode === true || String(data.testMode).toLowerCase() === 'true') return null;
return {payload: JSON.stringify(msg.topic === 'resend_active_shift' ? {action: 'resend_active'} : data)};
"""

ACK = """const p = typeof msg.payload === 'string' ? JSON.parse(msg.payload) : msg.payload;
if (!p || !p.status) return null;
const feedback = {topic: 'shift_form_feedback', payload: p};
if (p.status === 'ended') {
    return [null, null, null, {topic: 'trigger_auto_end_shift', payload: {value: true}}];
}
if (p.status !== 'active') return [null, feedback, null, null];
return [{topic: 'form_data', payload: p.source_payload}, feedback,
        p.navigate_to_hourly === false ? null : {topic: 'navigate', payload: {page: 'Hourly Checksheet'}}, null];
"""

TELEMETRY = """const p = typeof msg.payload === 'string' ? JSON.parse(msg.payload) : msg.payload;
if (!p || p.shift_id === undefined) return null;
return {topic: 'shift_runtime', payload: p};
"""


def add_shift_ui(nodes):
    front = next((n for n in nodes if n.get('name') == 'front'), None)
    if front is None:
        return nodes
    front['format'] = re.sub(r'\s*<v-switch\b[^>]*v-model="testMode"[^>]*></v-switch>', '', front['format'])
    front['format'] = front['format'].replace("{{ testMode ? 'PREVIEW TEST' : 'CONFIRM LIVE SHIFT' }}", 'CONFIRM LIVE SHIFT')
    main = next(n for n in nodes if n.get('name') == 'main')
    # Derive the line from the existing API node, never from user-entered form data.
    api = next(n for n in nodes if n.get('name') == 'node.js_endShift')
    line = 'abb7' if 'ABB7' in api['func'] else 'abb2'
    prefix = line + '-shift-'
    if any(n['id'] == prefix + 'gate' for n in nodes):
        ack = next(n for n in nodes if n.get('name') == 'Apply accepted shift only')
        if 'p.navigate_to_hourly' not in ack['func']:
            ack['func'] = ack['func'].replace("{topic: 'navigate', payload: {page: 'Hourly Checksheet'}}, null];", "p.navigate_to_hourly === false ? null : {topic: 'navigate', payload: {page: 'Hourly Checksheet'}}, null];")
        next(n for n in nodes if n.get('name') == 'check if shift end')['func'] = '// Shift end notifications now come from Python.\nreturn null;\n'
        next(n for n in nodes if n.get('name') == 'Set Shift End Time')['func'] = 'return msg;'
        return nodes
    by_id = {n['id']: n for n in nodes}
    route = by_id[front['wires'][0][0]]
    mqtt_out = next(n for n in nodes if n.get('topic') == 'nodered/newshift' and n['type'] == 'mqtt out')
    sensor = next(n for n in nodes if n['type'] == 'mqtt in' and n.get('topic') == 'sensor2/data')
    # Original form side effects run only after Python acknowledges activation.
    for n in nodes:
        if 'wires' in n:
            n['wires'] = [[t for t in targets if t != mqtt_out['id']] for targets in n['wires']]
    front['wires'] = [[prefix + 'gate']]
    sensor['wires'][0].append(prefix + 'telemetry')
    checker = next(n for n in nodes if n.get('name') == 'check if shift end')
    ended_targets = list(checker['wires'][0])
    checker['func'] = '// Shift end notifications now come from Python.\nreturn null;\n'
    next(n for n in nodes if n.get('name') == 'Set Shift End Time')['func'] = 'return msg;'

    def function(suffix, name, func, wires):
        return {'id': prefix + suffix, 'type': 'function', 'z': front['z'], 'name': name,
                'func': func, 'outputs': len(wires), 'noerr': 0, 'initialize': '',
                'finalize': '', 'libs': [], 'x': 560, 'y': 1000 + 60 * len(nodes), 'wires': wires}

    ack_in = copy.deepcopy(sensor)
    ack_in.update(id=prefix + 'ack-in', name='Python shift decisions',
                  topic=line + '/shift_form_result', datatype='json',
                  wires=[[prefix + 'ack']], x=250, y=1040)
    ack_in.pop('g', None)
    nodes.extend([
        function('gate', 'Submit live form to Python', GATE, [[mqtt_out['id']]]),
        ack_in,
        function('ack', 'Apply accepted shift only', ACK,
                 [[route['id']], [front['id'], main['id']], [route['id']], ended_targets]),
        function('telemetry', 'Show authoritative shift times', TELEMETRY, [[main['id']]]),
    ])
    # Give new nodes sensible editor positions.
    for i, node in enumerate(nodes[-4:]):
        node['x'], node['y'] = 300 + i * 200, 1040

    template = front['format']
    banner = '''    <v-alert v-if="testMode" type="warning" class="mb-3">TEST MODE: preview only. No live shift or production records will be changed.</v-alert>
    <v-btn :disabled="testMode" @click="send({topic: 'resend_active_shift', payload: {}})" class="mb-3">Resend active shift details</v-btn>
    <v-alert v-if="formFeedback" type="info" class="mb-3">{{ formFeedback }}</v-alert>
'''
    template = template.replace('    <v-form ref="conversionForm"', banner + '    <v-form ref="conversionForm"', 1)
    template = template.replace('      formData: {', "      testMode: false,\n      formFeedback: '',\n      formData: {", 1)
    template = template.replace('        CONFIRM', "        CONFIRM LIVE SHIFT", 1)
    template = template.replace('      handler(newValue) {', '      handler(newValue) {\n        if (this.testMode) return;', 1)
    template = template.replace('  watch: {', '''  watch: {
    msg(newMsg) {
      if (newMsg?.topic !== 'shift_form_feedback') return;
      const p = newMsg.payload;
      this.formFeedback = p.status === 'active' ? 'Shift activated.' : (p.message || p.status);
    },''', 1)
    template = template.replace('    async submitForm() {', '''    async submitForm() {
      if (this.testMode) {
        this.formFeedback = `TEST PREVIEW: ${this.formData.shift}, ${this.formData.prodDate}, ${this.calculatedWorkingTime}. Nothing submitted.`;
        return;
      }''', 1)
    template = template.replace('        timestamp: Date.now()', '        testMode: false,\n        timestamp: Date.now()', 1)
    navigation = '''      this.send({
        topic: 'navigate',
        payload: {
          page: 'Hourly Checksheet'
        }
      });'''
    if navigation not in template:
        raise ValueError('Unrecognized front navigation; no output written')
    front['format'] = template.replace(navigation, '', 1)

    template = main['format']
    template = template.replace('      showSetupWarning: false,', "      activeShiftLabel: 'Waiting for Python',\n      shiftResetAt: '—',\n      pendingShiftLabel: 'None',\n      storageWarning: '',\n      showSetupWarning: false,", 1)
    panel = '''    <v-alert type="info" class="mb-3">
      Active shift: {{ activeShiftLabel }} · Reset at: {{ shiftResetAt }}<br>
      Next shift: {{ pendingShiftLabel }}
    </v-alert>
    <v-alert v-if="storageWarning" type="error" class="mb-3">{{ storageWarning }}</v-alert>
'''
    template = template.replace('      <v-row dense class="mb-3">', panel + '      <v-row dense class="mb-3">', 1)
    template = template.replace('        const p = newMsg.payload;', '''        const p = newMsg.payload;
        if (newMsg.topic === 'shift_runtime') {
          this.activeShiftLabel = p.shift_id === 'NO PROD' ? 'No active shift' : `${p.shift} (${p.overtime ? 'OT' : 'No OT'}) — ${p.date}`;
          this.shiftResetAt = p.scheduled_end_at ? new Date(p.scheduled_end_at).toLocaleString('en-GB') : '—';
          const next = p.pending_shift;
          this.pendingShiftLabel = next ? `${next.shift} — ${next.prodDate}, ${next.workingTime}` : 'None';
          this.storageWarning = p.persistence_enabled === false ? 'SQLite saving is disabled. Production may be lost on restart. Check service logs.' : '';
          return;
        }''', 1)
    main['format'] = template
    return nodes
