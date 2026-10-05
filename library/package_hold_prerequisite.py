#!/usr/bin/python
# Copyright: (c) 2026, Versionlock contributors
# SPDX-License-Identifier: MIT
DOCUMENTATION = r'''
---
module: package_hold_prerequisite
short_description: Probe native package hold platform and prerequisites
version_added: "0.2.0"
description:
  - Audit capabilities or install missing prerequisite packages and recheck.
options:
  mode:
    description: Read-only audit or explicit prerequisite remediation.
    type: str
    choices: [audit, remediate, platform]
    default: audit
  command_timeout_seconds:
    description: Maximum duration of each capability or install command.
    type: int
    default: 30
  dnf_plugin_package:
    description: RED OS DNF4 plugin prerequisite.
    type: str
    default: python3-dnf-plugin-versionlock
attributes:
  check_mode:
    support: full
author:
  - Versionlock contributors
'''
EXAMPLES = r'''
- name: Audit prerequisites
  package_hold_prerequisite:
    mode: audit
'''
RETURN = r'''
components:
  description: Capabilities before and after remediation, including planned changes.
  returned: when mode is not platform
  type: list
  elements: dict
os:
  description: Normalized os-release identity.
  returned: always
  type: dict
backend:
  description: Native backend selected for this platform.
  returned: always
  type: str
'''
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.hold import (
    BACKENDS, HoldError, NAME, detect_os,
)
from ansible.module_utils.prerequisites import run_prerequisites
import re


def main():
    module = AnsibleModule(argument_spec={
        'mode': {'type': 'str', 'default': 'audit', 'choices': ['audit', 'remediate', 'platform']},
        'command_timeout_seconds': {'type': 'int', 'default': 30},
        'dnf_plugin_package': {'type': 'str', 'default': 'python3-dnf-plugin-versionlock'},
    }, supports_check_mode=True)
    parameters = module.params
    if (parameters['command_timeout_seconds'] < 1
            or not re.fullmatch(NAME, parameters['dnf_plugin_package'])):
        module.fail_json(changed=False, msg='Invalid timeout or prerequisite package')
    if parameters['mode'] == 'platform':
        try:
            platform = detect_os()
            module.exit_json(changed=False, os=platform, backend=BACKENDS[platform['id']],
                             ansible_facts={'package_hold_platform': platform})
        except HoldError as exc:
            module.fail_json(changed=False, errors=[exc.error], msg=str(exc))
    result = run_prerequisites(parameters['mode'], module.check_mode,
                               parameters['command_timeout_seconds'], parameters['dnf_plugin_package'])
    module.exit_json(**result)


if __name__ == '__main__':
    main()
