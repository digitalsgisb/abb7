"""Wire downtime controls to the shared Python session instead of browser mode commands."""
import copy
from pathlib import Path
import re


STATE = """const p = typeof msg.payload === 'string' ? JSON.parse(msg.payload) : msg.payload;
if (!p || !p.state) return null;
// Removing client targeting broadcasts the same authoritative session to every device.
const state = {topic: 'downtime_state', payload: p};
const key = `${p.event || ''}:${p.state.revision}`;
const seen = flow.get('downtimeEffectKeys') || [];
const duplicate = seen.includes(key);
if (p.event || p.mode_changed) flow.set('downtimeEffectKeys', [...seen, key].slice(-128));
const log = !duplicate && p.log ? {topic: 'downtime_data', payload: p.log} : null;
const mode = !duplicate && p.mode_changed ? {topic: 'mode_update', payload: p.mode} : null;
const navigate = !duplicate && ['logged', 'cancelled'].includes(p.event) ?
    {payload: {page: 'Hourly Checksheet'}, ...(p.client ? {_client: p.client} : {})} : null;
return [state, log, mode, navigate];
"""

TELEMETRY = """const p = typeof msg.payload === 'string' ? JSON.parse(msg.payload) : msg.payload;
if (!p || !p.downtime_session) return null;
return {topic: 'downtime_state', payload: {state: p.downtime_session, server_ms: p.server_ms,
    mode: p.current_mode}};
"""


def add_downtime_sync(nodes):
    by_id = {n['id']: n for n in nodes}
    downtime = next((n for n in nodes if n.get('type') == 'ui-template' and n.get('name') == 'Downtime Log UI'), None)
    if downtime is None:
        return nodes
    main = next(n for n in nodes if n.get('type') == 'ui-template' and n.get('name') == 'main')
    line = 'abb7' if any(n.get('topic') == 'abb7/shift_form_result' for n in nodes) else 'abb2'
    prefix = line + '-downtime-'
    if prefix + 'result-in' in by_id:
        return nodes
    # Retain the layout and reason lookups; replace all browser-local lifecycle code.
    script = Path(__file__).with_name('downtime_component.js').read_text(encoding='utf-8')
    downtime['format'] = re.sub(r'<script>[\s\S]*?</script>', lambda _: '<script>\n' + script + '\n</script>', downtime['format'], count=1)
    banner = '''
    <v-alert type="info" class="mb-3">Opening this page does not stop production. Start Timer begins downtime; Stop Timer returns to Normal. Log or cancel the stopped session before starting another.</v-alert>
    <v-alert v-if="syncError" type="error" class="mb-3">{{ syncError }}</v-alert>
    <v-alert v-if="!ready" type="warning" class="mb-3">Connecting to the shared timer...</v-alert>
    <v-chip class="mb-3" :color="timerRunning ? 'red' : 'green'">{{ timerRunning ? 'Shared timer running on all devices' : (sessionStatus === 'stopped' ? 'Timer stopped; machine Normal; log or cancel' : 'Machine mode unchanged; timer not started') }}</v-chip>
'''
    downtime['format'] = downtime['format'].replace('    <v-form', banner + '    <v-form', 1)
    downtime['format'] = downtime['format'].replace('@click="toggleTimer" block', ':disabled="controlsDisabled || sessionStatus === \'stopped\'" @click="toggleTimer" block', 1)
    downtime['format'] = downtime['format'].replace('@click="cancelDowntime"', ':disabled="controlsDisabled" @click="cancelDowntime"', 1)
    downtime['format'] = downtime['format'].replace('<v-btn type="submit"', '<v-btn :disabled="controlsDisabled" type="submit"', 1)
    downtime['format'] = downtime['format'].replace('v-model.number="formData.durationMinutes"', ':model-value="isSessionLocked ? Math.max(1, Math.ceil(elapsedMs / 60000)) : formData.durationMinutes" @update:model-value="if (!isSessionLocked) formData.durationMinutes = $event"', 1)
    downtime['format'] = downtime['format'].replace('label="Total Logged Duration (Mins) *"', ':readonly="isSessionLocked" label="Total Logged Duration (Mins) *"', 1)
    downtime['format'] = downtime['format'].replace(':disabled="timerRunning"', ':disabled="isSessionLocked || controlsDisabled"')
    downtime['format'] = downtime['format'].replace(':disabled="!formData.downtimeCategory"', ':disabled="!formData.downtimeCategory || isSessionLocked || controlsDisabled"')
    for category in ('Quality', 'Schedule', 'Planned Stop', 'Production', 'Machine'):
        downtime['format'] = downtime['format'].replace(f'@click="setCategory(\'{category}\')"', f':disabled="isSessionLocked || controlsDisabled" @click="setCategory(\'{category}\')"')
    # Inputs from Home wrappers reach these original route switches on output two.
    down_home = by_id[downtime['wires'][0][0]]
    down_route = by_id[down_home['wires'][1][0]]
    def targets(rule):
        index = next(i for i, item in enumerate(down_route['rules']) if item.get('v') == rule)
        return [t for t in down_route['wires'][index] if by_id[t]['type'] not in {'mqtt out', 'debug'}]
    log_targets, mode_targets = targets('downtime_data'), targets('mode_update')
    # Python already logged/applied the command. Only mirror accepted effects to the API.
    for target in mode_targets:
        n = by_id[target]
        if n['type'] == 'function' and 'modeMap' in n['func']:
            n['func'] = n['func'].replace('"normal": "normal"', '"normal": "normal",\n    "rest": "rest"')
    mqtt = next(n for n in nodes if n['type'] == 'mqtt out' and n.get('topic') == 'nodered/mode')
    command_out = copy.deepcopy(mqtt)
    command_out.update(id=prefix + 'command-out', topic='nodered/downtime_session', name='Shared downtime commands', qos='1', wires=[])
    command_out.pop('g', None)
    sensor = next(n for n in nodes if n['type'] == 'mqtt in' and n.get('topic') == 'sensor2/data')
    result_in = copy.deepcopy(sensor)
    result_in.update(id=prefix + 'result-in', topic=line + '/downtime_result', datatype='json',
                     name='Shared downtime results', wires=[[prefix + 'result']], qos='1')
    result_in.pop('g', None)
    def function(suffix, name, code, wires):
        return {'id': prefix + suffix, 'type': 'function', 'z': downtime['z'], 'name': name,
                'func': code, 'outputs': len(wires), 'noerr': 0, 'initialize': '', 'finalize': '',
                'libs': [], 'x': 600, 'y': 1500, 'wires': wires}
    result = function('result', 'Broadcast shared downtime', STATE,
                      [[downtime['id'], main['id']], log_targets, mode_targets, targets('navigate')])
    telemetry = function('telemetry', 'Restore shared timer on every device', TELEMETRY, [[downtime['id'], main['id']]])
    sensor['wires'][0].append(telemetry['id'])
    additions = [command_out, result_in, result, telemetry]
    for template in (downtime, main):
        home = by_id[template['wires'][0][0]]
        original = list(home['wires'][1])
        gate = function('gate-' + template['id'], 'Route shared commands: ' + template['name'],
                        "if (msg.topic === 'downtime_command') {\n"
                        "    return [{payload: JSON.stringify({...msg.payload, client: msg._client})}, null];\n}\n"
                        "if (['mode_update', 'nodered/mode', 'downtime_data'].includes(msg.topic)) return null;\n"
                        "return [null, msg];\n", [[command_out['id']], original])
        home['wires'][1] = [gate['id']]
        additions.append(gate)
    # The dashboard mode selector reflects shared state without echoing received updates.
    source = main['format']
    source = re.sub(r',\s*machineMode\(newMode\)\s*\{\s*if \(this.isSetupConfirmed\) \{\s*this.modeChanged\(newMode\);\s*}\s*}', '', source, count=1)
    source = source.replace('      storageWarning:', '      downtimeRevision: 0,\n      sharedDowntimeRunning: false,\n      applyingSharedMode: false,\n      storageWarning:', 1)
    source = source.replace('v-model="machineMode" mandatory', 'v-model="machineMode" :disabled="sharedDowntimeRunning" mandatory', 1)
    source = source.replace('    modeChanged(newMode) {\n      this.send({ topic: "machine_mode", payload: newMode });\n    },', '''    modeChanged(newMode) {
      if (this.applyingSharedMode || this.sharedDowntimeRunning) return;
      this.send({topic: 'downtime_command', payload: {action: 'mode', data: {mode: newMode},
        revision: this.downtimeRevision, command_id: `${Date.now()}-${Math.random()}`}});
    },''', 1)
    source = source.replace('        const p = newMsg.payload;', '''        const p = newMsg.payload;
        if (newMsg.topic === 'downtime_state') {
          this.downtimeRevision = p.state.revision;
          this.sharedDowntimeRunning = p.state.status === 'running';
          this.applyingSharedMode = true;
          this.machineMode = p.mode === 'REST' ? 'Rest' : 'Normal';
          this.$nextTick(() => {this.applyingSharedMode = false;});
          return;
        }''', 1)
    source = source.replace('<div class="text-caption text-grey-lighten-1 mb-1">Machine Mode Selector</div>',
        '<div class="text-caption text-grey-lighten-1 mb-1">Machine Mode Selector</div><v-alert v-if="sharedDowntimeRunning" type="warning" class="mb-2">Shared downtime timer running. Open Downtime to stop or log it.</v-alert>')
    main['format'] = source
    # Model/setup broadcasts should reach tablets as well as the originating client.
    sync = next((n for n in nodes if n.get('name') == 'smart dashboard sync function'), None)
    if sync:
        sync['func'] = sync['func'].replace('    flow.set("hourlyDashboardState", msg.payload || {});',
            '    flow.set("hourlyDashboardState", msg.payload || {});\n    delete msg._client;\n    delete msg.socketid;')
    for i, n in enumerate(additions):
        n['x'], n['y'] = 300 + 180 * (i % 4), 1500 + 80 * (i // 4)
    return nodes + additions
