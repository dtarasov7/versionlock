"""Load both independent implementations and shared synthetic fixtures."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Mirror Ansible's local module_utils search without an installed collection.
import ansible.module_utils
ansible.module_utils.__path__.insert(0, str(ROOT / 'module_utils'))
from ansible.module_utils import hold, report, prerequisites

filter_spec = importlib.util.spec_from_file_location('package_hold_filter', ROOT / 'filter_plugins/package_hold.py')
filter_module = importlib.util.module_from_spec(filter_spec)
filter_spec.loader.exec_module(filter_module)
resolve = filter_module.resolve

spec = importlib.util.spec_from_file_location('hold_exporter', ROOT / 'package-hold-exporter.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)
CASES = json.loads((ROOT / 'tests/fixtures/backend-cases.json').read_text())['cases']


def config_for(system='astra', packages=None, status_path='/tmp/test-status.json'):
    return {'schema_version': 1, 'os': system, 'backend': exporter.METHODS[system],
            'packages': packages if packages is not None else [{'id': 'nginx', 'name': 'nginx'}],
            'scan_interval_seconds': 300, 'scan_timeout_seconds': 60, 'command_timeout_seconds': 30,
            'listen_address': '127.0.0.1', 'listen_port': 9817, 'status_path': status_path}


class FixtureRunner:
    def __init__(self, inputs, consumer):
        self.inputs = inputs
        self.consumer = consumer
        self.calls = []
        self.error_type = hold.HoldError if consumer == 'module' else exporter.ScanError

    def executable(self, name):
        if not self.inputs.get('prerequisite_available', True) and name in ('dnf', 'apt-mark', 'apt-get'):
            raise self.error_type('prerequisite_missing', 'Missing ' + name)
        return '/usr/bin/' + name

    path = executable

    def run(self, argv, query=True):
        self.calls.append(argv)
        self.executable(argv[0])
        if argv[:2] == ['rpm', '-qf']:
            return self.inputs.get('dnf_identity', 'dnf\t4.17.0\n')
        if argv[0] in ('rpm', 'dpkg-query'):
            response = self.inputs['installed_query']
        elif argv[0] == 'apt-config':
            response = self.inputs.get('apt_config_dump', {'rc': 0, 'stdout': '', 'stderr': ''})
        elif argv == ['dnf', '--version']:
            return '4.14.0\n'
        else:
            return ''
        if response.get('exception'):
            raise self.error_type(response['exception'], 'Fixture failure')
        if response['rc'] or response['stderr']:
            code = 'permission_denied' if 'permission denied' in response['stderr'].lower() else 'command_failed'
            raise self.error_type(code, 'Fixture query failure')
        return response['stdout']

    query = run

    def read(self, path):
        files = {'/etc/os-release': 'ID={}\nVERSION_ID="1"\n'.format(self.inputs.get('os_id', 'astra')),
                 '/etc/dnf/dnf.conf': self.inputs.get('dnf_config', '[main]\nplugins=1\n'),
                 '/etc/dnf/plugins/versionlock.conf': self.inputs.get('plugin_config', '[main]\nenabled=1\nlocklist=/etc/dnf/plugins/versionlock.list\n'),
                 '/etc/dnf/plugins/versionlock.list': self.inputs.get('locklist', '')}
        if path in self.inputs.get('files', {}):
            return self.inputs['files'][path]
        if path not in files:
            raise self.error_type('prerequisite_missing', 'Missing fixture file: ' + path)
        return files[path]
