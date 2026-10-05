#!/usr/bin/python
# Copyright: (c) 2026, Versionlock contributors
# SPDX-License-Identifier: MIT
DOCUMENTATION = r'''
---
module: package_hold_report
short_description: Write one atomic hold CSV report on the controller
version_added: "0.2.0"
description:
  - Run in a separate localhost play after all managed hosts have finished.
options:
  action:
    description: Allocate a unique run identity or write final CSVs.
    type: str
    choices: [init, finalize]
    required: true
  report_root:
    description: Parent directory for unique runs.
    type: path
    default: reports
  run_id:
    description: Run identity from init.
    type: str
  phase:
    description: CSV type.
    type: str
    choices: [hold, prerequisites]
    default: hold
  hosts:
    description: Frozen inventory hosts mapped to result and OS facts.
    type: dict
    default: {}
attributes:
  check_mode:
    support: full
author:
  - Versionlock contributors
'''
EXAMPLES = r'''
- name: Allocate report identity
  package_hold_report:
    action: init
'''
RETURN = r'''
run_id:
  description: Unique identity shared by every host in this run.
  returned: on init
  type: str
success:
  description: Complete host coverage without failed checks.
  returned: on finalize
  type: bool
'''
import os
import re
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.report import aggregate, new_run_id, write_reports


def main():
    module = AnsibleModule(argument_spec={
        'action': {'type': 'str', 'choices': ['init', 'finalize'], 'required': True},
        'report_root': {'type': 'path', 'default': 'reports'},
        'run_id': {'type': 'str'},
        'phase': {'type': 'str', 'choices': ['hold', 'prerequisites'], 'default': 'hold'},
        'hosts': {'type': 'dict', 'default': {}},
    }, supports_check_mode=True)
    parameters = module.params
    if parameters['action'] == 'init':
        module.exit_json(changed=False, run_id=new_run_id())
    run_id = parameters['run_id']
    if not run_id or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9\-]{36}', run_id):
        module.fail_json(changed=False, msg='Invalid run_id')
    directory = os.path.join(os.path.abspath(parameters['report_root']), run_id)
    if module.check_mode:
        _, coverage = aggregate(run_id, parameters['phase'], parameters['hosts'])
        success = not any(not row['success'] for row in coverage)
    else:
        try:
            success = write_reports(directory, run_id, parameters['phase'], parameters['hosts'])
        except OSError as exc:
            module.fail_json(changed=False, msg=str(exc))
    module.exit_json(changed=not module.check_mode, success=success, report_directory=directory)


if __name__ == '__main__':
    main()
