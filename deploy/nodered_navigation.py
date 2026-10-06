"""Route Home buttons and shift-end navigation without production side effects."""
import copy
import json
import re


def add_navigation(nodes):
    by_id = {n['id']: n for n in nodes}
    front = next((n for n in nodes if n.get('name') == 'front' and n['type'] == 'ui-template'), None)
    if front is None:
        return nodes
    main = next(n for n in nodes if n.get('name') == 'main' and n['type'] == 'ui-template')
    detail_page = by_id[by_id[front['group']]['page']]
    hourly_page = by_id[by_id[main['group']]['page']]
    detail_path, hourly_path = detail_page['path'], hourly_page['path']
    prefix = front['id'] + '-home-'
    if prefix + 'control' in by_id:
        return nodes
    control = copy.deepcopy(next(n for n in nodes if n['type'] == 'ui-control'))
    control.update(id=prefix + 'control', name='Home and shift-end navigation',
                   z=front['z'], wires=[[]], x=1050, y=1160)
    control.pop('g', None)
    detail_page['name'] = 'Shift Details'
    nodes.append(control)
    for index, template in enumerate([n for n in nodes if n['type'] == 'ui-template'
                                      and n.get('name') in ('front', 'main', 'Condition', 'Reject', 'Downtime Log UI')]):
        destination = detail_path if template['id'] == main['id'] else hourly_path
        hint = 'Shift Details' if template['id'] == main['id'] else 'Hourly Checksheet'
        button = (f'\n    <div class="d-flex justify-end mb-2">'
                  f'<v-btn color="cyan" variant="outlined" '
                  f'@click="send({{topic: \'go_home\', payload: {{page: \'{destination}\'}}}})" '
                  f'title="Open {hint}" aria-label="Home: {hint}">'
                  '<v-icon start>mdi-home</v-icon>Home</v-btn></div>\n')
        template['format'], count = re.subn(r'(<v-container\b[^>]*>)',
                                            lambda match: match[1] + button,
                                            template['format'], count=1)
        if count != 1:
            raise ValueError(f"Cannot place Home button in {template.get('name')}")
        # Delayed client cleanup must land on the same page as the server broadcast.
        template['format'] = re.sub(r"(page:\s*)(['\"])Smart Checksheet\2",
                                    lambda match: match[1] + json.dumps(detail_path), template['format'])
        router_id = prefix + template['id']
        original = [target for output in template['wires'] for target in output]
        router = {'id': router_id, 'type': 'function', 'z': template['z'],
                  'name': f"Home route: {template.get('name')}", 'outputs': 2,
                  'func': "if (msg.topic === 'go_home') {\n"
                          f"    msg.payload = {{page: {json.dumps(destination)}}};\n"
                          "    return [msg, null];\n}\nreturn [null, msg];\n",
                  'noerr': 0, 'initialize': '', 'finalize': '', 'libs': [],
                  'x': 700, 'y': 1160 + index * 60,
                  'wires': [[control['id']], original]}
        # Home messages bypass existing form/API/timer handlers entirely.
        template['wires'] = [[router_id]]
        nodes.append(router)
    ack = next(n for n in nodes if n.get('name') == 'Apply accepted shift only')
    ack['func'] = ack['func'].replace(
        "{topic: 'trigger_auto_end_shift', payload: {value: true}}];",
        "{topic: 'trigger_auto_end_shift', payload: {value: true}}, "
        f"{{payload: {{page: {json.dumps(detail_path)}}}}}];")
    ack['outputs'] = 5
    ack['wires'].append([control['id']])
    # Activation keeps its existing form routes; absent output five means no navigation.
    return nodes
