#!/usr/bin/python
# Copyright: (c) 2026, Versiolock contributors
# SPDX-License-Identifier: MIT
DOCUMENTATION = r'''
---
module: package_hold
short_description: Query or manage native holds of installed exact package names
version_added: "0.2.0"
description:
  - Absent packages are omitted. Errors never become unheld.
  - RED OS DNF4, Astra dpkg selections and provisional ALT RPM holds are separate backends.
options:
  packages:
    description: Resolved package identifiers and exact binary names.
    type: list
    elements: dict
    required: true
    suboptions:
      id:
        description: Stable package identifier.
        type: str
        required: true
      name:
        description: Exact binary package name without architecture or version.
        type: str
        required: true
  backend:
    description:
      - Automatically selected from os-release when omitted.
      - An explicit value asserts the detected backend; it cannot override OS detection.
    type: str
    choices: [dnf_versionlock, dpkg_selections, alt_rpm_hold]
  state:
    description: Query, ensure held, or explicitly remove holds.
    type: str
    default: query
    choices: [query, held, unheld]
  command_timeout_seconds:
    description: Per-command timeout.
    type: int
    default: 30
  lock_timeout_seconds:
    description: Mutation lock timeout.
    type: int
    default: 10
attributes:
  check_mode:
    support: full
  diff_mode:
    support: none
author:
  - Versiolock contributors
'''
EXAMPLES = r'''
- name: Audit resolved packages
  package_hold:
    packages: [{id: java17, name: openjdk-17-jre}]
- name: Explicit operator unhold
  package_hold:
    state: unheld
    packages: [{id: java17, name: openjdk-17-jre}]
'''
RETURN = r'''
packages:
  description: Installed package observations with before_state, changed, would_change and error.
  returned: always
  type: list
  elements: dict
errors:
  description: Host-level failures with code and message.
  returned: always
  type: list
  elements: dict
schema_version:
  description: Result contract version.
  returned: always
  type: int
'''
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.hold import (
    BACKENDS, Backend, HoldError, Runner, detect_os, execute,
)


def main():
    module = AnsibleModule(argument_spec={
        'packages': {'type': 'list', 'elements': 'dict', 'required': True,
                     'options': {'id': {'type': 'str', 'required': True},
                                 'name': {'type': 'str', 'required': True}}},
        'backend': {'type': 'str', 'choices': list(BACKENDS.values())},
        'state': {'type': 'str', 'default': 'query', 'choices': ['query', 'held', 'unheld']},
        'command_timeout_seconds': {'type': 'int', 'default': 30},
        'lock_timeout_seconds': {'type': 'int', 'default': 10},
    }, supports_check_mode=True)
    parameters = module.params
    backend = None
    try:
        platform = detect_os()
        backend = BACKENDS[platform['id']]
        if parameters['backend'] is not None and backend != parameters['backend']:
            raise HoldError('unsupported_platform', 'Backend does not match this OS')
        if parameters['command_timeout_seconds'] < 1 or parameters['lock_timeout_seconds'] < 0:
            raise HoldError('configuration_invalid', 'Invalid timeout')
    except HoldError as exc:
        module.fail_json(schema_version=1, changed=False, check_mode=module.check_mode,
                         backend=backend, operation=parameters['state'], packages=[],
                         errors=[exc.error], msg=str(exc))
    adapter = Backend(backend, Runner(parameters['command_timeout_seconds']))
    result = execute(parameters['packages'], backend, parameters['state'],
                     module.check_mode, adapter, parameters['lock_timeout_seconds'])
    failed = result.pop('failed')
    if failed:
        module.fail_json(msg='Hold check or postcondition failed', **result)
    module.exit_json(failed=False, **result)


if __name__ == '__main__':
    main()
