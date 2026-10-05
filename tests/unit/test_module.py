import copy
import csv
import fnmatch
from contextlib import nullcontext
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from support import CASES, FixtureRunner, ROOT, hold, report, prerequisites


class StatefulRunner(FixtureRunner):
    def __init__(self, system, directory=None):
        fixture = next(case for case in CASES if case['case'] == system + '_unheld')
        super().__init__(copy.deepcopy(fixture['inputs']), 'module')
        self.system = system
        self.directory = directory
        self.mutations = []
        self.external = False
        self.external_patterns = []
        self.fail_verify = False

    def run(self, argv, query=True):
        if not query:
            self.mutations.append(argv)
            self.calls.append(argv)
            if self.fail_verify:
                raise hold.HoldError('command_failed', 'Partial mutation failed')
            if argv[0] == 'apt-mark':
                want = 'hold' if argv[1] == 'hold' else 'install'
                self.inputs['installed_query']['stdout'] = 'nginx\tamd64\t' + want + ' ok installed\n'
            elif argv[0] == 'dnf':
                self.inputs['locklist'] = 'nginx-0:1.0-1.*\n' if argv[-2] == 'add' else ''
            return ''
        if argv[0] == 'apt-config' and self.directory:
            path = Path(self.directory) / '90-package-hold.conf'
            content = path.read_text() if path.exists() else ''
            output = 'RPM::Hold:: "^nginx$";\n' if self.external else ''
            output += ''.join('RPM::Hold:: "' + pattern + '";\n'
                              for pattern in self.external_patterns)
            for line in content.splitlines():
                if line.strip().startswith('"'):
                    output += 'RPM::Hold:: ' + line.strip() + '\n'
            return output
        return super().run(argv, query)


class ModuleTests(unittest.TestCase):
    packages = [{'id': 'nginx', 'name': 'nginx'}, {'id': 'missing', 'name': 'missing'}]

    def call(self, runner, state, check=False):
        backend = {'redos': 'dnf_versionlock', 'astra': 'dpkg_selections', 'alt': 'alt_rpm_hold'}[runner.system]
        adapter = hold.Backend(backend, runner, runner.read)
        with patch.object(hold, 'mutation_lock', lambda timeout: nullcontext()):
            return hold.execute(self.packages, backend, state, check, adapter)

    def test_held_and_unheld_idempotent_and_check_mode_each_backend(self):
        for system in ['redos', 'astra', 'alt']:
            with self.subTest(system=system), tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
                runner = StatefulRunner(system, directory)
                # Backend config fixtures use the virtual filesystem; owned fragment
                # uses a real temporary file to exercise atomic read-modify-write.
                fragment = Path(directory) / '90-package-hold.conf'
                original_reader = runner.read
                runner.read = lambda path: fragment.read_text() if path == str(fragment) else original_reader(path)
                with patch.object(hold, 'ALT_FRAGMENT', str(fragment)):
                    queried = self.call(runner, 'query')
                    self.assertFalse(queried['changed'])
                    self.assertEqual(len(queried['packages']), 1)
                    with patch.object(hold, 'mutation_lock', side_effect=AssertionError('check_mode lock write')):
                        checked = hold.execute(self.packages, queried['backend'], 'held', True,
                                               hold.Backend(queried['backend'], runner, runner.read))
                    self.assertTrue(checked['changed'])
                    self.assertTrue(checked['packages'][0]['would_change'])
                    self.assertEqual(checked['packages'][0]['state'], 'unheld')
                    self.assertEqual(runner.mutations, [])
                    self.assertFalse(fragment.exists())
                    held = self.call(runner, 'held')
                    self.assertFalse(held['failed'], held)
                    self.assertTrue(held['changed'])
                    self.assertEqual(held['packages'][0]['state'], 'held')
                    self.assertFalse(self.call(runner, 'held')['changed'])
                    checked_unhold = self.call(runner, 'unheld', True)
                    self.assertTrue(checked_unhold['changed'])
                    self.assertEqual(checked_unhold['packages'][0]['state'], 'held')
                    unheld = self.call(runner, 'unheld')
                    self.assertFalse(unheld['failed'], unheld)
                    self.assertTrue(unheld['changed'])
                    self.assertEqual(unheld['packages'][0]['state'], 'unheld')
                    self.assertFalse(self.call(runner, 'unheld')['changed'])
                    if system == 'redos':
                        self.assertEqual(runner.mutations[0], ['dnf', '-q', '--disablerepo=*', 'versionlock', 'add', 'nginx-0:1.0-1.x86_64'])
                    if system == 'astra':
                        self.assertEqual(runner.mutations[0], ['apt-mark', 'hold', 'nginx:amd64'])

    def test_unknown_never_mutates(self):
        runner = StatefulRunner('redos')
        runner.inputs['locklist'] = 'nginx-*\n'
        result = self.call(runner, 'held')
        self.assertTrue(result['failed'])
        self.assertFalse(result['changed'])
        self.assertEqual(runner.mutations, [])

    def test_unhold_partial_multiarch_and_verify_every_architecture(self):
        for system in ['astra', 'redos']:
            with self.subTest(system=system):
                runner = StatefulRunner(system)
                if system == 'astra':
                    runner.inputs['installed_query']['stdout'] = (
                        'nginx\tamd64\thold ok installed\nnginx\ti386\tinstall ok installed\n')
                else:
                    runner.inputs['installed_query']['stdout'] = (
                        'nginx\t0\t1.0\t1\tx86_64\nnginx\t0\t1.0\t1\ti686\n')
                    runner.inputs['locklist'] = 'nginx-0:1.0-1.x86_64\n'
                planned = self.call(runner, 'unheld', True)
                self.assertTrue(planned['changed'])
                self.assertEqual(runner.mutations, [])
                # A successful command with unchanged partial state is a failure.
                with patch.object(runner, 'run', side_effect=lambda argv, query=True:
                                  StatefulRunner.run(runner, argv) if query else ''):
                    failed = self.call(runner, 'unheld')
                self.assertTrue(failed['failed'])
                self.assertEqual(failed['packages'][0]['error']['code'], 'verification_failed')
                result = self.call(runner, 'unheld')
                self.assertTrue(result['changed'])
                self.assertFalse(result['failed'], result)
                self.assertFalse(self.call(runner, 'unheld')['changed'])

    def test_dnf_delete_uses_literal_records_not_name_version_alias(self):
        runner = StatefulRunner('redos')
        runner.inputs['installed_query']['stdout'] = 'tool-2\t0\t1\t1\tx86_64\n'
        records = ['tool-2-0:1-1.*', 'tool-0:2-1.*', 'tool-2-extra-0:1-1.*']
        runner.inputs['locklist'] = '\n'.join(records) + '\n'
        adapter = hold.Backend('dnf_versionlock', runner, runner.read)
        adapter.query([{'id': 'tool', 'name': 'tool-2'}])
        adapter.mutate('tool-2', [], 'unheld')
        patterns = runner.mutations[-1][5:]
        # DNF _match tests each of these NEVRA aliases using fnmatch.
        aliases = [
            ['tool-2', 'tool-2-1', 'tool-2-0:1-1.*'],
            ['tool', 'tool-2', 'tool-0:2-1.*'],
            ['tool-2-extra', 'tool-2-extra-1', 'tool-2-extra-0:1-1.*'],
        ]
        matched = [any(fnmatch.fnmatch(alias, pattern) for alias in names for pattern in patterns)
                   for names in aliases]
        self.assertEqual(matched, [True, False, False])

    def test_partial_mutation_failure_preserves_changed(self):
        runner = StatefulRunner('astra')
        runner.fail_verify = True
        result = self.call(runner, 'held')
        self.assertTrue(result['failed'])
        self.assertTrue(result['changed'])
        self.assertEqual(result['packages'][0]['state'], 'unknown')

    def test_package_disappears_before_mutation(self):
        runner = StatefulRunner('astra')
        adapter = hold.Backend('dpkg_selections', runner, runner.read)
        before = adapter.query(self.packages)
        with patch.object(adapter, 'query', side_effect=[before, ([], {})]), patch.object(hold, 'mutation_lock', lambda timeout: nullcontext()):
            result = hold.execute(self.packages, 'dpkg_selections', 'held', adapter=adapter)
        self.assertEqual(result['packages'], [])
        self.assertFalse(result['changed'])

    def test_alt_external_hold_conflict_and_preserves_other_owned_entries(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            fragment = Path(directory) / '90-package-hold.conf'
            fragment.write_text('RPM::Hold {\n    "^other$";\n    "^nginx$";\n};\n')
            runner = StatefulRunner('alt', directory)
            runner.external = True
            original_read = runner.read
            runner.read = lambda path: fragment.read_text() if path == str(fragment) else original_read(path)
            with patch.object(hold, 'ALT_FRAGMENT', str(fragment)):
                result = self.call(runner, 'unheld')
            self.assertTrue(result['failed'])
            self.assertEqual(result['packages'][0]['state'], 'held')
            self.assertEqual(result['packages'][0]['error']['code'], 'external_hold_conflict')
            self.assertIn('^other$', fragment.read_text())
            self.assertNotIn('^nginx$', fragment.read_text())

    def test_alt_default_regex_survives_hold_and_unhold(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            fragment = Path(directory) / '90-package-hold.conf'
            runner = StatefulRunner('alt', directory)
            runner.external_patterns = ['^(kernel|alsa)[0-9]+-source']
            original_read = runner.read
            runner.read = lambda path: fragment.read_text() if path == str(fragment) else original_read(path)
            with patch.object(hold, 'ALT_FRAGMENT', str(fragment)):
                self.assertEqual(self.call(runner, 'query')['packages'][0]['state'], 'unheld')
                self.assertFalse(self.call(runner, 'held')['failed'])
                self.assertFalse(self.call(runner, 'held')['changed'])
                self.assertFalse(self.call(runner, 'unheld')['failed'])
                self.assertFalse(self.call(runner, 'unheld')['changed'])
            self.assertEqual(runner.external_patterns, ['^(kernel|alsa)[0-9]+-source'])
            self.assertFalse(fragment.exists())

    def test_alt_broad_hold_conflict_preserves_owned_regex(self):
        for external in [False, True]:
            with self.subTest(external=external), tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
                fragment = Path(directory) / '90-package-hold.conf'
                broad = '^NGIN[[:alpha:]]$'
                patterns = ['^other$', '^nginx$'] + ([] if external else [broad])
                fragment.write_text('RPM::Hold {\n' +
                                    ''.join('    "' + pattern + '";\n' for pattern in patterns) +
                                    '};\n', encoding='utf-8')
                runner = StatefulRunner('alt', directory)
                runner.external_patterns = [broad] if external else []
                original_read = runner.read
                runner.read = lambda path: fragment.read_text() if path == str(fragment) else original_read(path)
                with patch.object(hold, 'ALT_FRAGMENT', str(fragment)):
                    result = self.call(runner, 'unheld')
                    repeated = self.call(runner, 'unheld')
                for attempt in [result, repeated]:
                    self.assertTrue(attempt['failed'])
                    self.assertEqual(attempt['packages'][0]['state'], 'held')
                    self.assertEqual(attempt['packages'][0]['error']['code'], 'external_hold_conflict')
                self.assertTrue(result['changed'])
                self.assertFalse(repeated['changed'])
                self.assertIn('^other$', fragment.read_text())
                self.assertNotIn('^nginx$', fragment.read_text())
                if not external:
                    self.assertIn(broad, fragment.read_text())

    def test_alt_owned_regex_backslashes_and_empty_rule_are_preserved(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            fragment = Path(directory) / '90-package-hold.conf'
            patterns = [r'^other\.name$', '']
            fragment.write_text('RPM::Hold {\n' +
                                ''.join('    "' + pattern + '";\n' for pattern in patterns) +
                                '};\n', encoding='utf-8')
            with patch.object(hold, 'ALT_FRAGMENT', str(fragment)):
                self.assertTrue(hold.change_alt_fragment('nginx', 'held'))
                self.assertTrue(hold.change_alt_fragment('nginx', 'unheld'))
                self.assertFalse(hold.change_alt_fragment('nginx', 'unheld'))
            for pattern in patterns:
                self.assertIn('"' + pattern + '";', fragment.read_text())

    def test_real_mutation_lock_contention_bounded(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            path = str(Path(directory) / 'lock')
            with hold.mutation_lock(1, path):
                with self.assertRaises(hold.HoldError) as caught:
                    with hold.mutation_lock(0, path):
                        self.fail('Acquired conflicting lock')
            self.assertEqual(caught.exception.error['code'], 'concurrent_change')

    def test_module_result_schema(self):
        import json
        import jsonschema
        result = self.call(StatefulRunner('astra'), 'held', True)
        schema = json.loads((ROOT / 'schemas/module-result.schema.json').read_text())
        jsonschema.validate(result, schema)


class ReportTests(unittest.TestCase):
    def test_csv_installed_only_quoting_formula_and_missing_coverage(self):
        runner = StatefulRunner('astra')
        result = hold.execute(ModuleTests.packages, 'dpkg_selections', adapter=hold.Backend('dpkg_selections', runner, runner.read))
        result['run_id'] = 'current'
        hosts = {'=host,"quoted"\nnext': {'result': result, 'os': {'id': 'astra'}},
                 'unreachable': {}, 'old-cache': {'result': dict(result, run_id='previous')}}
        rows, coverage = report.aggregate('current', 'hold', hosts)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['package'], 'nginx')
        content = report.csv_document(report.FIELDS['hold'], rows).decode('utf-8')
        decoded = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(decoded[0]['host'], "'=host,\"quoted\"\nnext")
        self.assertEqual([row['error_code'] for row in coverage if not row['success']], ['missing_result', 'missing_result'])
        self.assertNotIn('missing,', content)

    def test_empty_results_header_and_unique_run_directories(self):
        first, second = report.new_run_id(), report.new_run_id()
        self.assertNotEqual(first, second)
        with tempfile.TemporaryDirectory(dir=ROOT / '.build') as directory:
            result = {'run_id': first, 'packages': [], 'failed': False, 'errors': []}
            self.assertTrue(report.write_reports(directory, first, 'hold', {'host': {'result': result}}))
            self.assertEqual(len(Path(directory, 'hold.csv').read_text().splitlines()), 1)
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(Path(directory, 'hold.csv')).st_mode & 0o777, 0o600)


class PrerequisitesTests(unittest.TestCase):
    def test_audit_and_check_mode_do_not_install(self):
        components = [{'name': 'rpm', 'available': True, 'error': None},
                      {'name': 'dnf', 'available': True, 'error': None},
                      {'name': 'package_database', 'available': True, 'error': None},
                      {'name': 'package:plugin', 'available': False,
                       'error': {'code': 'prerequisite_missing', 'message': 'Missing'}}]
        runner = StatefulRunner('redos')
        platform = {'id': 'redos', 'version_id': '8'}
        with patch.object(prerequisites, 'inspect', return_value=components):
            audit = prerequisites.run_prerequisites(runner=runner, platform=platform)
            checked = prerequisites.run_prerequisites('remediate', True, runner=runner, platform=platform)
        self.assertFalse(audit['changed'])
        self.assertFalse(audit['success'])
        self.assertTrue(checked['changed'])
        self.assertFalse(checked['success'])
        self.assertFalse(checked['components'][-1]['available_after'])
        self.assertEqual(runner.mutations, [])

    def test_install_reprobes_and_repeated_remediation_is_idempotent(self):
        before = [{'name': name, 'available': True, 'error': None} for name in ('rpm', 'dnf', 'package_database')]
        before.append({'name': 'package:plugin', 'available': False, 'error': {'code': 'prerequisite_missing', 'message': 'Missing'}})
        after = copy.deepcopy(before)
        after[-1].update(available=True, error=None)
        runner = StatefulRunner('redos')
        platform = {'id': 'redos', 'version_id': '8'}
        with patch.object(prerequisites, 'inspect', side_effect=[before, after, after]) as probe:
            first = prerequisites.run_prerequisites('remediate', runner=runner, platform=platform)
            second = prerequisites.run_prerequisites('remediate', runner=runner, platform=platform)
        self.assertTrue(first['changed'])
        self.assertTrue(first['success'])
        self.assertFalse(second['changed'])
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(runner.mutations, [['dnf', '-y', 'install', 'plugin']])


if __name__ == '__main__':
    unittest.main()
