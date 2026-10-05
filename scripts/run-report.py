#!/usr/bin/env python3
"""Refuse unsafe limits/tags and validate duplicate YAML keys before report playbook."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import yaml


class StrictLoader(yaml.SafeLoader):
    pass


def strict_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise ValueError('Duplicate YAML key: ' + str(key))
        keys.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, strict_mapping)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--inventory', required=True, type=Path)
    parser.add_argument('--phase', choices=['hold', 'prerequisites'], default='hold')
    parser.add_argument('--mode', choices=['audit', 'remediate'], default='audit')
    parser.add_argument('--limit')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--serial', default='100%')
    arguments, extra = parser.parse_known_args()
    blocked = ('--tags', '-t', '--skip-tags', '--start-at-task', '--step', '--list-tasks', '--list-tags', '--list-hosts', '--syntax-check', '--limit', '-l')
    unsafe = False
    for argument in extra:
        option = argument.split('=')[0]
        if (option in blocked or argument.startswith(('-t', '-l'))
                or (option.startswith('--') and any(item.startswith(option) for item in blocked))):
            unsafe = True
    if unsafe:
        parser.error('Report coverage requires all init/managed/finalize tasks; limits must use --limit')
    inventory = arguments.inventory.resolve()
    files = list(inventory.rglob('*.yml')) + list(inventory.rglob('*.yaml')) if inventory.is_dir() else [inventory]
    for directory in ('group_vars', 'host_vars'):
        for path in (inventory.parent / directory).rglob('*'):
            if path.is_file():
                files.append(path)
    for path in set(files):
        if path.suffix in ('.yaml', '.yml', '.json', ''):
            try:
                with path.open(encoding='utf-8') as stream:
                    yaml.load(stream, Loader=StrictLoader)
            except (ValueError, yaml.YAMLError) as exc:
                parser.error(str(path) + ': ' + str(exc))
    root = Path(__file__).resolve().parents[1]
    command = ['ansible-playbook', '-i', str(inventory), str(root / 'playbooks/report.yml')]
    if arguments.limit:
        command += ['--limit', arguments.limit]
    command += extra
    environment = dict(os.environ, ANSIBLE_NOCOLOR='1',
                       ANSIBLE_CONFIG=str(root / 'ansible.cfg'))
    listing = subprocess.run(command + ['--list-hosts'], capture_output=True, text=True,
                             env=environment, timeout=60, check=False)
    if listing.returncode:
        sys.stderr.write(listing.stderr)
        return listing.returncode
    plays = re.split(r'\n\s*play #\d+', listing.stdout)[1:]
    host_lists = []
    for play in plays:
        block = re.search(r'hosts \(\d+\):\n(.*)', play, re.DOTALL)
        host_lists.append([line.strip() for line in block.group(1).splitlines() if line.strip()] if block else [])
    if len(host_lists) != 3 or host_lists[0] != ['localhost'] or host_lists[2] != ['localhost']:
        parser.error('Limit must include localhost so init and final CSV writer both run')
    if not host_lists[1]:
        parser.error('No managed hosts selected')
    options = {'package_hold_report_targets': host_lists[1], 'package_hold_report_phase': arguments.phase,
               'package_hold_report_serial': arguments.serial,
               'package_hold_' + ('control' if arguments.phase == 'hold' else 'prerequisites') + '_mode': arguments.mode}
    command += ['-e', json.dumps(options)]
    if arguments.check:
        command.append('--check')
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
