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
    # ui-control resolves page names, not URL paths. Restore the original menu label.
    if detail_page['name'] == 'Shift Details':
        detail_page['name'] = 'Smart Checksheet'
    detail_name, hourly_name = detail_page['name'], hourly_page['name']
    aliases = {detail_path: detail_name, 'Shift Details': detail_name, hourly_path: hourly_name,
               detail_name: detail_name, hourly_name: hourly_name}
    for node in nodes:
        for field in ('func', 'format'):
            if field not in node:
                continue
            def replace_page(match):
                target = aliases.get(match[3])
                if target is None:
                    return match[0]
                if field == 'format':
                    literal = "'" + target.replace("\\", "\\\\").replace("'", "\\'") + "'"
                else:
                    if match[3] == target:
                        return match[0]
                    literal = json.dumps(target)
                return match[1] + literal
            node[field] = re.sub(r"(page:\s*)(['\"])([^'\"]+)\2", replace_page, node[field])
    # Navigation is read-only: a missing browser setup flag must not silently block it.
    main['format'] = main['format'].replace("    navigate(targetPage) {\n      if (!this.isSetupConfirmed) return;", "    navigate(targetPage) {")
    navigation_branch = "if (msg.topic === 'navigation') return [msg, null];\n"
    for existing in nodes:
        if existing.get('name') == 'Home route: main' and navigation_branch not in existing.get('func', ''):
            existing['func'] = existing['func'].replace('return [null, msg];', navigation_branch + 'return [null, msg];')
    reject = next((n for n in nodes if n.get('type') == 'ui-template' and n.get('name') == 'Reject'), None)
    if reject:
        reject['format'] = re.sub(r'cancelReject\(\)\s*\{[\s\S]*?\n\s*},',
            "cancelReject() {\n        this.send({ topic: \"navigate\", payload: { page: 'Smart Checksheet' } });\n        this.resetForm();\n      },", reject['format'], count=1)
    prefix = front['id'] + '-home-'
    if prefix + 'control' in by_id:
        return nodes
    control = copy.deepcopy(next(n for n in nodes if n['type'] == 'ui-control'))
    control.update(id=prefix + 'control', name='Home and shift-end navigation',
                   z=front['z'], wires=[[]], x=1050, y=1160)
    control.pop('g', None)
    nodes.append(control)
    for index, template in enumerate([n for n in nodes if n['type'] == 'ui-template'
                                      and n.get('name') in ('front', 'main', 'Condition', 'Reject', 'Downtime Log UI')]):
        destination = detail_name if template['id'] == main['id'] else hourly_name
        hint = detail_name if template['id'] == main['id'] else hourly_name
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
                                    lambda match: match[1] + "'" + detail_name.replace("'", "\\'") + "'", template['format'])
        router_id = prefix + template['id']
        original = [target for output in template['wires'] for target in output]
        router = {'id': router_id, 'type': 'function', 'z': template['z'],
                  'name': f"Home route: {template.get('name')}", 'outputs': 2,
                  'func': "if (msg.topic === 'go_home') {\n"
                          f"    msg.payload = {{page: {json.dumps(destination)}}};\n"
                          "    return [msg, null];\n}\n" +
                          (navigation_branch if template['id'] == main['id'] else "") +
                          "return [null, msg];\n",
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
        f"{{payload: {{page: {json.dumps(detail_name)}}}}}];")
    ack['outputs'] = 5
    ack['wires'].append([control['id']])
    # Activation keeps its existing form routes; absent output five means no navigation.
    return nodes


def restore_main_ui_control_route(nodes):
    """Upgrade switch-only diagnostic exports back to the shared ui-control route."""
    main = next((n for n in nodes if n.get('type') == 'ui-template' and n.get('name') == 'main'), None)
    front = next((n for n in nodes if n.get('type') == 'ui-template' and n.get('name') == 'front'), None)
    if main is None or front is None:
        return nodes
    by_id = {n['id']: n for n in nodes}
    control_id = front['id'] + '-home-control'
    if control_id not in by_id:
        return nodes
    debug_id = main['id'] + '-navigation-debug'
    nodes = [n for n in nodes if n['id'] != debug_id]
    for n in nodes:
        if 'wires' in n:
            n['wires'] = [[t for t in output if t != debug_id] for output in n['wires']]
    home_id = front['id'] + '-home-' + main['id']
    if home_id in by_id:
        return nodes
    route = next(n for n in nodes if n.get('type') == 'switch' and
                 any(r.get('v') == 'navigation' for r in n.get('rules', [])) and
                 any(r.get('v') == 'machine_mode' for r in n.get('rules', [])))
    for i in reversed(range(len(route['rules']))):
        if route['rules'][i].get('v') == 'downtime_command':
            route['rules'].pop(i)
            route['wires'].pop(i)
    route['outputs'] = len(route['rules'])
    gate = next(n for n in nodes if n.get('name') == 'Route shared commands: main')
    gate['wires'][1] = [route['id']]
    detail = by_id[by_id[front['group']]['page']]['name']
    home = {'id': home_id, 'type': 'function', 'z': main['z'], 'name': 'Home route: main',
            'outputs': 2, 'noerr': 0, 'initialize': '', 'finalize': '', 'libs': [],
            'func': "if (msg.topic === 'go_home') {\n    msg.payload = {page: " + json.dumps(detail) + "};\n    return [msg, null];\n}\nif (msg.topic === 'navigation') return [msg, null];\nreturn [null, msg];\n",
            'x': 700, 'y': 1160, 'wires': [[control_id], [gate['id']]]}
    nodes.append(home)
    main['wires'] = [[home_id]]
    return nodes
