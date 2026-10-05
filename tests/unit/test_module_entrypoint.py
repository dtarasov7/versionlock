"""Exercise OS detection and backend selection through the Ansible module entrypoint."""
import copy
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from support import ROOT, hold

specification = importlib.util.spec_from_file_location('package_hold_module', ROOT / 'library/package_hold.py')
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)


class ModuleFailure(Exception):
    pass


class ModuleEntrypointTests(unittest.TestCase):
    def run_module(self, os_id, requested_backend=None, detection_error=None):
        parameters = {'packages': [], 'backend': requested_backend, 'state': 'query',
                      'command_timeout_seconds': 30, 'lock_timeout_seconds': 10}
        ansible_module = SimpleNamespace(params=parameters, check_mode=False,
                                        exit_json=Mock(), fail_json=Mock(side_effect=ModuleFailure))
        result = {'schema_version': 1, 'failed': False, 'changed': False, 'packages': [], 'errors': []}
        with patch.object(module, 'AnsibleModule', return_value=ansible_module) as constructor, \
                patch.object(module, 'detect_os', return_value={'id': os_id}, side_effect=detection_error), \
                patch.object(module, 'Runner') as runner, patch.object(module, 'Backend') as adapter, \
                patch.object(module, 'execute', return_value=copy.deepcopy(result)) as execute:
            try:
                module.main()
            except ModuleFailure:
                pass
        return ansible_module, constructor, runner, adapter, execute

    def test_omitted_backend_selects_each_native_backend(self):
        for os_id, backend in hold.BACKENDS.items():
            with self.subTest(os=os_id):
                ansible_module, constructor, runner, adapter, execute = self.run_module(os_id)
                backend_argument = constructor.call_args.kwargs['argument_spec']['backend']
                self.assertFalse(backend_argument.get('required', False))
                adapter.assert_called_once_with(backend, runner.return_value)
                self.assertEqual(execute.call_args.args[1], backend)
                ansible_module.fail_json.assert_not_called()
                ansible_module.exit_json.assert_called_once()

    def test_explicit_backend_is_an_assertion(self):
        for os_id, backend in hold.BACKENDS.items():
            with self.subTest(os=os_id):
                ansible_module, _, _, adapter, execute = self.run_module(os_id, backend)
                self.assertEqual(adapter.call_args.args[0], backend)
                execute.assert_called_once()
                ansible_module.fail_json.assert_not_called()

    def test_mismatched_backend_never_runs_package_commands(self):
        ansible_module, _, runner, adapter, execute = self.run_module('alt', 'dpkg_selections')
        failure = ansible_module.fail_json.call_args.kwargs
        self.assertEqual(failure['backend'], 'alt_rpm_hold')
        self.assertEqual(failure['errors'][0]['code'], 'unsupported_platform')
        self.assertFalse(failure['changed'])
        runner.assert_not_called()
        adapter.assert_not_called()
        execute.assert_not_called()

    def test_unsupported_os_never_falls_back_to_a_package_manager(self):
        error = hold.HoldError('unsupported_platform', 'Unsupported os-release ID')
        ansible_module, _, runner, adapter, execute = self.run_module('debian', detection_error=error)
        failure = ansible_module.fail_json.call_args.kwargs
        self.assertIsNone(failure['backend'])
        self.assertEqual(failure['errors'][0]['code'], 'unsupported_platform')
        runner.assert_not_called()
        adapter.assert_not_called()
        execute.assert_not_called()
