import copy
import unittest
from support import CASES, FixtureRunner, ROOT, config_for, exporter, hold, resolve


class BackendContractTests(unittest.TestCase):
    def compare_case(self, case):
        rows_by_consumer = []
        for consumer in ('module', 'exporter'):
            runner = FixtureRunner(case['inputs'], consumer)
            error = None
            try:
                if consumer == 'module':
                    rows, _ = hold.Backend(case['backend'], runner, runner.read).query(case['resolved_packages'])
                else:
                    config = config_for(case['os'], case['resolved_packages'])
                    rows = exporter.query_packages(config, runner, runner.read)
            except (hold.HoldError, exporter.ScanError) as exc:
                error = (exc.error if consumer == 'module' else exc.detail)['code']
                rows = []
            normalized = [{'id': row['id'], 'package': row['package'], 'state': row['state'],
                           'error_code': row['error']['code'] if row['error'] else None} for row in rows]
            self.assertEqual(error, case['expected_host_error'], (consumer, case['case']))
            self.assertEqual(normalized, case['expected_packages'], (consumer, case['case']))
            rows_by_consumer.append(normalized)
        self.assertEqual(*rows_by_consumer)

    def test_all_golden_fixtures_both_adapters(self):
        for case in CASES:
            with self.subTest(case=case['case']):
                self.compare_case(case)

    def variant(self, name, changes, state=None, code=None, host_error=None):
        case = copy.deepcopy(next(case for case in CASES if case['case'] == name))
        case['inputs'].update(changes)
        if host_error:
            case['expected_host_error'] = host_error
            case['expected_packages'] = []
        elif state:
            case['expected_packages'][0].update(state=state, error_code=code)
        self.compare_case(case)

    def test_redos_imported_rpm_key_metadata_is_not_a_binary_package(self):
        self.variant('redos_held', {'installed_query': {'rc': 0, 'stderr': '',
                     'stdout': 'gpg-pubkey\t0\tf8ac18ee\t591e99a0\t(none)\nnginx\t0\t1.0\t1\tx86_64\n'}})

    def test_dnf_obsolete_exact_lock_is_still_hold(self):
        self.variant('redos_held', {'locklist': '# old build\nnginx-0:0.9-2.*\n'})

    def test_dnf_multiarch_coverage(self):
        self.variant('redos_held', {'installed_query': {'rc': 0, 'stderr': '',
                     'stdout': 'nginx\t0\t1\t1\tx86_64\nnginx\t0\t1\t1\ti686\n'},
                     'locklist': 'nginx-0:1-1.x86_64\n'}, 'unheld')

    def test_dnf_wildcard_negative_and_disabled(self):
        for rule in ['nginx-*', 'nginx-0:*-1.*', '!nginx-0:1-1.*', '*']:
            with self.subTest(rule=rule):
                self.variant('redos_held', {'locklist': rule + '\n'}, 'unknown', 'unsupported_rule')
        for key in ('dnf_config', 'plugin_config'):
            value = '[main]\nplugins=0\n' if key == 'dnf_config' else '[main]\nenabled=0\nlocklist=/etc/dnf/plugins/versionlock.list\n'
            self.variant('redos_held', {key: value}, 'unknown', 'backend_disabled')

    def test_dnf_configuration_errors_and_custom_locklist(self):
        for value in ['[main]\nplugins=maybe\n', '[main]\nplugins=1\nplugins=0\n', '[main]\npluginpath=/custom\n']:
            self.variant('redos_held', {'dnf_config': value}, 'unknown', 'configuration_invalid')
        self.variant('redos_held', {'plugin_config': '[main]\nenabled=1\nlocklist=/custom.list\n',
                                 'files': {'/custom.list': 'nginx-0:1-1.*\n'}})

    def test_installed_query_errors_are_host_failures_all_os(self):
        for system in ('redos', 'astra', 'alt'):
            for response, code in [({'rc': 2, 'stdout': '', 'stderr': 'DB error'}, 'command_failed'),
                                   ({'rc': 0, 'stdout': '', 'stderr': 'permission denied'}, 'permission_denied'),
                                   ({'exception': 'command_timeout'}, 'command_timeout')]:
                with self.subTest(system=system, code=code):
                    self.variant(system + '_held', {'installed_query': response}, host_error=code)

    def test_dpkg_residual_is_absent_and_bad_eflag_is_unknown(self):
        self.variant('astra_absent', {'installed_query': {'rc': 0, 'stderr': '', 'stdout': 'nginx\tamd64\thold ok config-files\n'}})
        self.variant('astra_held', {'installed_query': {'rc': 0, 'stderr': '', 'stdout': 'nginx\tamd64\thold reinstreq installed\n'}}, 'unknown', 'command_failed')
        self.variant('astra_held', {'installed_query': {'rc': 0, 'stderr': '', 'stdout': 'nginx\tamd64\tinstall ok half-installed\n'}}, host_error='command_failed')

    def test_dpkg_multiarch_and_deinstall_selection(self):
        self.variant('astra_held', {'installed_query': {'rc': 0, 'stderr': '', 'stdout': 'nginx\tamd64\thold ok installed\nnginx\ti386\tinstall ok installed\n'}}, 'unheld')
        self.variant('astra_held', {'installed_query': {'rc': 0, 'stderr': '', 'stdout': 'nginx\tamd64\tdeinstall ok installed\n'}}, 'unheld')

    def test_alt_posix_patterns_and_dump_failure(self):
        cases = [('kernel.*', 'unheld'), ('nginx', 'held'), ('^ng.*$', 'held'),
                 ('', 'held'), ('^NGINX$', 'held'), ('gin', 'held'),
                 ('^ngin[[:alpha:]]$', 'held'), ('^ngin[[:digit:]]$', 'unheld'),
                 (r'^ngin\w$', 'held'), (r'^ngin\d$', 'unheld')]
        for value, state in cases:
            with self.subTest(pattern=value):
                output = 'RPM::Hold:: "' + value + '";\n'
                self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '',
                             'stdout': output}}, state)
        for value in ['[', '(', '*', '[[:invalid:]]', '\x00']:
            with self.subTest(invalid_pattern=value):
                output = 'RPM::Hold:: "' + value + '";\n'
                self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '',
                             'stdout': output}}, 'unknown', 'configuration_invalid')
        self.variant('alt_held', {'apt_config_dump': {'rc': 1, 'stderr': 'error', 'stdout': ''}}, 'unknown', 'command_failed')
        self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '', 'stdout': 'Dir::Etc::parts "/custom";\n'}}, 'unknown', 'configuration_invalid')

    def test_alt_escaping_and_unrelated_repeated_list_entries(self):
        for name in ['libstdc++', 'name.with.dots', 'java-17-openjdk']:
            pattern = '^' + name.replace('+', '[+]').replace('.', '[.]') + '$'
            self.assertEqual(hold.canonical_pattern(name), pattern)
            self.assertEqual(exporter.alt_pattern(name), pattern)
            output = 'Other::List:: "x";\nOther::List:: "y";\nRPM::Hold:: "' + pattern + '";\n'
            self.assertEqual(hold.parse_alt(output), [pattern])
            self.assertEqual(exporter.alt_rules(output), [pattern])

    def test_native_alt_dump_preserves_unrelated_backslash_patterns(self):
        output = (ROOT / 'tests/fixtures/alt-p10-apt-config.dump').read_text(encoding='utf-8')
        unrelated = '\n'.join(line for line in output.splitlines() if not line.startswith('RPM::Hold::'))
        self.assertEqual(hold.parse_alt(unrelated), [])
        self.assertEqual(exporter.alt_rules(unrelated), [])
        patterns = ['^(kernel|alsa)[0-9]+-source']
        self.assertEqual(hold.parse_alt(output), patterns)
        self.assertEqual(exporter.alt_rules(output), patterns)
        self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '',
                     'stdout': output}}, 'unheld')
        names = ['bash', 'kernel123-source', 'alsa1-source-extra', 'ALSA2-source',
                 'kernel-source', 'mykernel1-source']
        expected = {'kernel123-source', 'alsa1-source-extra', 'ALSA2-source'}
        self.assertEqual(hold.alt_matches(patterns, names), expected)
        self.assertEqual(exporter.alt_matches(patterns, names), expected)

    def test_alt_all_rules_are_validated_even_after_matching_rule(self):
        output = 'RPM::Hold:: "^nginx$";\nRPM::Hold:: "[";\n'
        self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '',
                     'stdout': output}}, 'unknown', 'configuration_invalid')

    def test_alt_named_and_case_insensitive_configuration_keys(self):
        for key in ['RPM::Hold::manual', 'rpm::hold::', 'RpM::HoLd::Admin']:
            output = key + ' "^nginx$";\n'
            with self.subTest(key=key):
                self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '',
                             'stdout': output}})
        output = 'dir::etc::parts "/custom";\n'
        self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '',
                     'stdout': output}}, 'unknown', 'configuration_invalid')
        output = 'RPM::Hold::manual::nested "^nginx$";\n'
        self.variant('alt_held', {'apt_config_dump': {'rc': 0, 'stderr': '',
                     'stdout': output}}, 'unknown', 'unsupported_rule')

    def test_dnf_incomplete_configuration_returns_unknown_not_exception(self):
        self.variant('redos_held', {'plugin_config': '[main]\nenabled=1\n'},
                     'unknown', 'configuration_invalid')
        self.variant('redos_held', {'plugin_config': '[main]\nenabled=1\nlocklist=/missing\n'},
                     'unknown', 'prerequisite_missing')

    def test_dnf_identity_uses_rpm_owner_instead_of_version_command(self):
        for identity in ['dnf\t5.0.0\n', 'dnf5\t5.0.0\n', 'dnf\t4.17.0\ndnf\t4.17.0\n', '4.17.0\n']:
            self.variant('redos_held', {'dnf_identity': identity}, 'unknown', 'unsupported_platform')
        case = next(case for case in CASES if case['case'] == 'redos_held')
        for consumer in ('module', 'exporter'):
            runner = FixtureRunner(case['inputs'], consumer)
            if consumer == 'module':
                hold.Backend(case['backend'], runner, runner.read).query(case['resolved_packages'])
            else:
                exporter.query_packages(config_for('redos', case['resolved_packages']), runner, runner.read)
            self.assertNotIn(['dnf', '--version'], runner.calls)
            self.assertIn(['rpm', '-qf', '/usr/bin/dnf', '--qf', '%{NAME}\t%{VERSION}\n'], runner.calls)

    def test_alt_dump_does_not_unescape_regex_backslashes(self):
        patterns = [r'^ngin\.x$', r'^literal\\name$']
        output = ''.join('RPM::Hold:: "' + pattern + '";\n' for pattern in patterns)
        self.assertEqual(hold.parse_alt(output), patterns)
        self.assertEqual(exporter.alt_rules(output), patterns)
        for matcher in [hold.alt_matches, exporter.alt_matches]:
            self.assertEqual(matcher(patterns, ['ngin.x', 'literalname']), {'ngin.x'})

    def test_mapping_and_semantic_validation(self):
        packages = [{'id': 'java17', 'names': {'redos': 'java-17-openjdk', 'astra': 'openjdk-17-jre', 'alt': None}}]
        self.assertEqual(resolve(packages, 'redos'), [{'id': 'java17', 'name': 'java-17-openjdk'}])
        self.assertEqual(resolve(packages, 'astra'), [{'id': 'java17', 'name': 'openjdk-17-jre'}])
        self.assertEqual(resolve(packages, 'alt'), [])
        for invalid in [packages * 2, [{'id': 'bad', 'names': {'astra': 'nginx'}}],
                        [{'id': 'bad', 'names': dict.fromkeys(['redos', 'astra', 'alt'])}],
                        [{'id': 'bad', 'names': dict.fromkeys(['redos', 'astra', 'alt'], '-command')}]]:
            with self.assertRaises(Exception):
                resolve(invalid, 'astra')
        self.assertEqual(resolve([], 'astra'), [])


if __name__ == '__main__':
    unittest.main()
